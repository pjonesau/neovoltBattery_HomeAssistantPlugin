"""Tests for the bank SOC when the Host getLastPowerData call fails.

On a parallel system sysSn=All reports the capacity-weighted average of the
Host (whole bank) and the followers (0), so it reads 25% low for 30 + 10
kWh. A failed Host call used to fall back to that value for one poll, which
showed up as one-minute dips to 75% of the real SOC (seen 2026-11-09/10).
"""
from __future__ import annotations

import asyncio
import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("Crypto.Cipher")
aiohttp = pytest.importorskip("aiohttp")

try:
    from custom_components.bytewatt import sensor
    from custom_components.bytewatt.api import neovolt_client
except ModuleNotFoundError as exc:
    pytest.skip(f"Module not installed in this environment: {exc.name}", allow_module_level=True)

from custom_components.bytewatt.const import HOST_SOC_MAX_AGE, SENSOR_SOC

from tests.test_pv_inputs import (
    ALL_DATA,
    HOST_DATA,
    HOST_SN,
    INVERTERS,
    _client,
    _coordinator,
    _FakeResponse,
    _ok,
    _poll,
    _RouterSession,
)

HOST_FAILURES = [
    pytest.param(_FakeResponse("<html>404 Not Found</html>", status=404), id="nginx-404"),
    pytest.param(_FakeResponse({"code": 6053, "msg": "too fast"}), id="api-error-code"),
    pytest.param(aiohttp.ClientError("boom"), id="transport-error"),
    pytest.param(asyncio.TimeoutError(), id="timeout"),
    pytest.param(_ok({"soc": None}), id="soc-null"),
]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime.datetime]:
    """Controllable dt_util.now() for the client."""
    state = {"now": datetime.datetime(2026, 11, 10, 7, 46, tzinfo=datetime.timezone.utc)}
    monkeypatch.setattr(neovolt_client.dt_util, "now", lambda: state["now"])
    return state


def _soc_sensor(coordinator: Any) -> sensor.ByteWattSensor:
    entry = MagicMock(entry_id="entry", data={})
    return sensor.ByteWattSensor(
        coordinator, entry, SENSOR_SOC, "Battery Percentage", "battery", "soc", "%",
        "mdi:battery",
    )


async def test_host_soc_is_reported_over_all_average(clock: dict) -> None:
    data = await _client(_RouterSession()).async_get_battery_data()

    assert data["soc"] == HOST_DATA["soc"]


@pytest.mark.parametrize("host_failure", HOST_FAILURES)
async def test_failed_host_read_keeps_last_host_soc_not_all(clock: dict, host_failure: Any) -> None:
    """The regression: one failed Host call must not dip the sensor to All."""
    session = _RouterSession()
    coordinator = _coordinator(_client(session))
    soc_sensor = _soc_sensor(coordinator)
    await _poll(coordinator)
    assert soc_sensor.native_value == HOST_DATA["soc"]

    session.power[HOST_SN] = host_failure
    clock["now"] += datetime.timedelta(minutes=1)
    await _poll(coordinator)

    assert coordinator.data["connection_status"] == "connected"
    assert soc_sensor.native_value == HOST_DATA["soc"]
    assert soc_sensor.native_value != ALL_DATA["soc"]


async def test_new_host_reading_replaces_the_kept_value(clock: dict) -> None:
    session = _RouterSession()
    client = _client(session)
    await client.async_get_battery_data()

    session.power[HOST_SN] = _FakeResponse({}, status=404)
    clock["now"] += datetime.timedelta(minutes=1)
    await client.async_get_battery_data()

    session.power[HOST_SN] = _ok({**HOST_DATA, "soc": 98.7})
    clock["now"] += datetime.timedelta(minutes=1)
    data = await client.async_get_battery_data()

    assert data["soc"] == 98.7


async def test_kept_value_expires_after_max_age(clock: dict) -> None:
    session = _RouterSession()
    client = _client(session)
    await client.async_get_battery_data()
    session.power[HOST_SN] = _FakeResponse({}, status=404)

    clock["now"] += datetime.timedelta(seconds=HOST_SOC_MAX_AGE)
    assert (await client.async_get_battery_data())["soc"] == HOST_DATA["soc"]

    clock["now"] += datetime.timedelta(seconds=1)
    assert (await client.async_get_battery_data())["soc"] is None


async def test_failed_first_read_on_parallel_system_is_unknown(clock: dict) -> None:
    session = _RouterSession()
    session.power[HOST_SN] = _FakeResponse({}, status=404)
    coordinator = _coordinator(_client(session))
    soc_sensor = _soc_sensor(coordinator)

    await _poll(coordinator)

    assert soc_sensor.native_value is None


async def test_unknown_inverter_count_is_treated_as_parallel(clock: dict) -> None:
    session = _RouterSession()
    session.power[HOST_SN] = _FakeResponse({}, status=404)
    client = _client(session)
    client._inverter_list_cache = None
    client.fetch_inverter_list = MagicMock(side_effect=AssertionError("not reached"))

    async def _no_stacks(headers: dict) -> dict:
        return {}

    client._async_build_stack_data = _no_stacks

    assert (await client.async_get_battery_data())["soc"] is None


async def test_single_inverter_falls_back_to_all(clock: dict) -> None:
    """With one inverter, All is that inverter's own SOC, so it is correct."""
    session = _RouterSession()
    session.power[HOST_SN] = _FakeResponse({}, status=404)
    client = _client(session)
    client._inverter_list_cache = (clock["now"], INVERTERS[:1])

    assert (await client.async_get_battery_data())["soc"] == ALL_DATA["soc"]
