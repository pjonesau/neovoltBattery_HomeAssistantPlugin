"""Tests for the per-input solar power sensors (PV1/PV2 on the Host inverter).

Most run the real client, coordinator update and sensor together over a fake
HTTP session, because the failure handling only holds across all three: the
coordinator re-serves cached data after a failed poll.
"""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

pytest.importorskip("Crypto.Cipher")
aiohttp = pytest.importorskip("aiohttp")

try:
    from custom_components.bytewatt import sensor
    from custom_components.bytewatt.api import neovolt_client
    from custom_components.bytewatt.coordinator import ByteWattDataUpdateCoordinator
except ModuleNotFoundError as exc:
    pytest.skip(f"Module not installed in this environment: {exc.name}", allow_module_level=True)

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.util import dt as dt_util

from custom_components.bytewatt.const import (
    CONF_HOST_SYS_SN,
    DOMAIN,
    HOST_PV_INPUTS,
    PV_INPUT_SENSORS,
)
from custom_components.bytewatt.stacks import extract_pv_inputs

HOST_SN = "25000SP295W00658"
FOLLOWER_SN = "25000SP285W00998"
LAST_POWER_DATA = "getLastPowerData"

# Portal readings from 2026-11-02 14:44. The merged response carries different
# ppv1/ppv2 values so a test can tell which response a sensor read from.
HOST_DATA = {
    "soc": 99.9,
    "prealL1": -1200,
    "ppv": 2557,
    "ppv1": 685,
    "ppv2": 1872,
    "ppv3": 0,
    "ppv4": 0,
}
ALL_DATA = {"soc": 74.9, "ppv": 2557, "ppv1": 9001, "ppv2": 9002}
FOLLOWER_DATA = {"soc": 0, "prealL1": 0, "ppv": 0, "ppv1": 0, "ppv2": 0, "ppv3": 0, "ppv4": 0}
INVERTERS = [
    {"sysSn": HOST_SN, "parallelGridMode": 1, "cobat": 28.8},
    {"sysSn": FOLLOWER_SN, "parallelGridMode": 2, "cobat": 9.6},
]


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _ok(data: Any) -> _FakeResponse:
    return _FakeResponse({"code": 200, "data": data})


class _RouterSession:
    """Answers getLastPowerData per sysSn; every other endpoint gets empty data."""

    def __init__(self) -> None:
        self.power = {
            "All": _ok(ALL_DATA),
            HOST_SN: _ok(HOST_DATA),
            FOLLOWER_SN: _ok(FOLLOWER_DATA),
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
        self.calls.append((url, params or {}))
        if LAST_POWER_DATA not in url:
            return _ok({})
        response = self.power[params["sysSn"]]
        if isinstance(response, Exception):
            raise response
        return response

    def power_calls_by_sys_sn(self) -> Counter:
        return Counter(
            params["sysSn"] for url, params in self.calls if LAST_POWER_DATA in url
        )


def _client(session: _RouterSession, host_sys_sn: str = HOST_SN) -> neovolt_client.NeovoltClient:
    client = object.__new__(neovolt_client.NeovoltClient)
    client.base_url = "https://example.invalid"
    client.session = session
    client.host_sys_sn = host_sys_sn
    client.token = "token"
    client.user_id = "U1"
    client._stack_soc_cache = {}
    client._inverter_list_cache = (dt_util.now(), INVERTERS)
    client._last_host_inverter_data = None
    return client


def _coordinator(client: neovolt_client.NeovoltClient) -> ByteWattDataUpdateCoordinator:
    coordinator = object.__new__(ByteWattDataUpdateCoordinator)
    coordinator.client = SimpleNamespace(get_battery_data=client.async_get_battery_data)
    coordinator.hass = SimpleNamespace(data={})
    coordinator.entry_id = "entry"
    coordinator.circuit_breaker = MagicMock()
    coordinator.circuit_breaker.can_execute.return_value = True
    coordinator.diagnostic_service = MagicMock(diagnostics_enabled=False)
    coordinator._last_battery_data = None
    coordinator._last_successful_update = None
    coordinator._consecutive_stale_checks = 0
    coordinator._recovery_attempts = 0
    coordinator.last_update_success = True
    coordinator.data = None
    return coordinator


async def _poll(coordinator: ByteWattDataUpdateCoordinator) -> None:
    coordinator.data = await coordinator._async_update_data()


def _sensors(coordinator: Any) -> dict[str, sensor.ByteWattPvInputSensor]:
    entry = MagicMock(entry_id="entry", data={CONF_HOST_SYS_SN: HOST_SN})
    return {
        field: sensor.ByteWattPvInputSensor(coordinator, entry, sensor_type, name, field, HOST_SN)
        for field, sensor_type, name in PV_INPUT_SENSORS
    }


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param({"ppv1": 685, "ppv2": 1872}, {"ppv1": 685.0, "ppv2": 1872.0}, id="both"),
        pytest.param({"ppv1": 0, "ppv2": 0}, {"ppv1": 0.0, "ppv2": 0.0}, id="zero-kept"),
        pytest.param({"ppv1": "685"}, {"ppv1": 685.0}, id="numeric-string"),
        pytest.param({"ppv1": 685}, {"ppv1": 685.0}, id="one-missing"),
        pytest.param({"ppv1": None, "ppv2": 1872}, {"ppv2": 1872.0}, id="null-dropped"),
        pytest.param({"ppv1": "n/a", "ppv2": True}, {}, id="non-numeric-dropped"),
        pytest.param({}, {}, id="empty"),
    ],
)
def test_extract_pv_inputs(data: dict[str, Any], expected: dict[str, float]) -> None:
    assert extract_pv_inputs(data, ["ppv1", "ppv2"]) == expected


async def test_sensors_read_the_host_response_not_the_merged_one() -> None:
    coordinator = _coordinator(_client(_RouterSession()))
    await _poll(coordinator)
    sensors = _sensors(coordinator)

    assert sensors["ppv1"].native_value == 685.0
    assert sensors["ppv2"].native_value == 1872.0
    assert sensors["ppv1"].available is True
    assert sensors["ppv2"].available is True


def test_sensor_metadata_supports_long_term_statistics() -> None:
    pv1 = _sensors(MagicMock())["ppv1"]

    assert pv1.device_class == SensorDeviceClass.POWER
    assert pv1.state_class == SensorStateClass.MEASUREMENT
    assert pv1.native_unit_of_measurement == "W"
    assert pv1.unique_id == "entry_pv1_power"
    assert pv1.extra_state_attributes == {"sysSn": HOST_SN}


async def test_genuine_zero_stays_zero_and_available() -> None:
    session = _RouterSession()
    session.power[HOST_SN] = _ok({**HOST_DATA, "ppv1": 0, "ppv2": 0})
    coordinator = _coordinator(_client(session))
    await _poll(coordinator)
    sensors = _sensors(coordinator)

    assert sensors["ppv1"].native_value == 0
    assert sensors["ppv2"].native_value == 0
    assert sensors["ppv1"].available is True
    assert sensors["ppv2"].available is True


@pytest.mark.parametrize(
    "host_response",
    [
        pytest.param(_FakeResponse({}, status=429), id="http-429"),
        pytest.param(_FakeResponse({"code": 6053, "msg": "too fast"}), id="api-error-code"),
        pytest.param(aiohttp.ClientError("boom"), id="transport-error"),
        pytest.param(_FakeResponse(["not", "an", "object"]), id="non-object-body"),
        pytest.param(_ok(None), id="null-data"),
        pytest.param(_ok({"soc": 99.9, "prealL1": -1200}), id="fields-missing"),
        pytest.param(_ok({"soc": 99.9, "ppv1": None, "ppv2": None}), id="fields-null"),
    ],
)
async def test_no_host_reading_is_unavailable_despite_merged_values(host_response: Any) -> None:
    """The merged response still carries ppv1/ppv2 here and must not fill in."""
    session = _RouterSession()
    session.power[HOST_SN] = host_response
    coordinator = _coordinator(_client(session))
    await _poll(coordinator)
    sensors = _sensors(coordinator)

    assert coordinator.data["battery"]["ppv1"] == ALL_DATA["ppv1"]
    assert sensors["ppv1"].available is False
    assert sensors["ppv2"].available is False
    assert sensors["ppv1"].native_value is None


async def test_one_missing_field_only_affects_its_own_sensor() -> None:
    session = _RouterSession()
    session.power[HOST_SN] = _ok({"ppv1": 685})
    coordinator = _coordinator(_client(session))
    await _poll(coordinator)
    sensors = _sensors(coordinator)

    assert sensors["ppv1"].native_value == 685.0
    assert sensors["ppv2"].available is False


async def test_failed_poll_goes_unavailable_rather_than_replaying_cache() -> None:
    session = _RouterSession()
    coordinator = _coordinator(_client(session))
    await _poll(coordinator)
    sensors = _sensors(coordinator)
    assert sensors["ppv1"].available is True

    session.power["All"] = _FakeResponse({}, status=429)
    await _poll(coordinator)

    # The coordinator still serves the earlier reading to the other sensors.
    assert coordinator.data["connection_status"] == "cached"
    assert coordinator.data["battery"][HOST_PV_INPUTS]["ppv1"] == 685.0
    assert sensors["ppv1"].available is False
    assert sensors["ppv1"].native_value is None

    session.power["All"] = _ok(ALL_DATA)
    await _poll(coordinator)

    assert sensors["ppv1"].available is True
    assert sensors["ppv1"].native_value == 685.0


async def test_open_circuit_breaker_goes_unavailable() -> None:
    session = _RouterSession()
    coordinator = _coordinator(_client(session))
    await _poll(coordinator)
    sensors = _sensors(coordinator)

    coordinator.circuit_breaker.can_execute.return_value = False
    coordinator.circuit_breaker.state = MagicMock(value="open")
    await _poll(coordinator)

    assert coordinator.data["connection_status"] == "limited"
    assert sensors["ppv1"].available is False


def test_failed_coordinator_update_is_unavailable() -> None:
    coordinator = MagicMock(
        last_update_success=False,
        data={"connection_status": "connected", "battery": {HOST_PV_INPUTS: {"ppv1": 685.0}}},
    )
    assert _sensors(coordinator)["ppv1"].available is False


async def test_no_additional_request_per_poll() -> None:
    session = _RouterSession()
    client = _client(session)

    await client.async_get_battery_data()
    assert session.power_calls_by_sys_sn() == {"All": 1, HOST_SN: 1, FOLLOWER_SN: 1}

    await client.async_get_battery_data()
    assert session.power_calls_by_sys_sn() == {"All": 2, HOST_SN: 2, FOLLOWER_SN: 2}


async def test_existing_readings_are_unchanged() -> None:
    data = await _client(_RouterSession()).async_get_battery_data()

    assert data["ppv"] == ALL_DATA["ppv"]
    assert data["ppv1"] == ALL_DATA["ppv1"]
    assert data["soc"] == HOST_DATA["soc"]
    assert data["stacks"][HOST_SN]["power_w"] == HOST_DATA["prealL1"]
    assert data["stacks"][FOLLOWER_SN]["power_w"] == FOLLOWER_DATA["prealL1"]


async def test_no_configured_host_means_no_host_request_or_readings() -> None:
    session = _RouterSession()

    data = await _client(session, host_sys_sn="").async_get_battery_data()

    assert session.power_calls_by_sys_sn() == {"All": 1}
    assert HOST_PV_INPUTS not in data


@pytest.mark.parametrize(
    ("entry_data", "expected_count"),
    [
        pytest.param({CONF_HOST_SYS_SN: HOST_SN}, 2, id="host-configured"),
        pytest.param({CONF_HOST_SYS_SN: ""}, 0, id="host-blank"),
        pytest.param({}, 0, id="host-absent"),
    ],
)
async def test_setup_depends_on_config_not_first_poll(
    entry_data: dict[str, str], expected_count: int
) -> None:
    """coordinator.data is None here: a failed first poll must not lose the sensors."""
    coordinator = MagicMock(data=None)
    entry = MagicMock(entry_id="entry", data=entry_data)
    hass = SimpleNamespace(data={DOMAIN: {"entry": {"coordinator": coordinator}}})
    added: list[Any] = []

    await sensor.async_setup_entry(hass, entry, added.extend)

    assert (
        sum(isinstance(entity, sensor.ByteWattPvInputSensor) for entity in added)
        == expected_count
    )
