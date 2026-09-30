"""Tests for the settings transport — API-variant detection and the
endpoint/payload each variant uses.

api/settings.py only depends on models.py, so both are loaded under stub
parent packages; no homeassistant install is needed. The fake client
answers GET/PUT/POST from a route table and records every call.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types


def _load_settings_module():
    here = os.path.dirname(__file__)
    base = os.path.abspath(os.path.join(here, "..", "custom_components", "bytewatt"))
    for name, path in (("bw_stub", base), ("bw_stub.api", os.path.join(base, "api"))):
        package = types.ModuleType(name)
        package.__path__ = [path]
        sys.modules[name] = package
    for name, rel in (("bw_stub.models", "models.py"),
                      ("bw_stub.api.settings", "api/settings.py")):
        spec = importlib.util.spec_from_file_location(name, os.path.join(base, rel))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules["bw_stub.models"], sys.modules["bw_stub.api.settings"]


models, settings_api = _load_settings_module()
BatterySettingsAPI = settings_api.BatterySettingsAPI
GridFeedInSettingsAPI = settings_api.GridFeedInSettingsAPI

HOST = "host-id"
OK = {"code": 200, "msg": "Success"}

CYCLE_DATA = {
    "gridChargeCycle": 1, "ctrDisCycle": 0, "batUseCap": 5,
    "dayChargeTimeList": [{"beginTime": "11:00", "endTime": "14:59",
                           "chargeLimit": 100, "chargePower": 8500, "sort": 1}],
    "dayDischargeTimeList": None,
}
CHARGE_CONFIG_DATA = {
    "id": HOST, "gridCharge": 0, "ctrDis": 0, "batUseCap": 5, "batHighCap": 100,
    "timeChaf1": "00:00", "timeChae1": "23:45", "timeChaf2": "00:00", "timeChae2": "00:00",
    "timeDisf1": "00:00", "timeDise1": "00:00", "timeDisf2": None, "timeDise2": None,
    "upsReserve": 0, "upsReserveEnable": True, "timeExpLimW1": 800,
}


class _FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.host_system_id = HOST
        self.host_sys_sn = "HOST-SN"
        self.settings_api_variant = None
        self.calls = []

    def _answer(self, method, endpoint, payload=None):
        self.calls.append((method, endpoint, payload))
        return self.routes.get(endpoint)

    async def _async_get(self, endpoint):
        return self._answer("GET", endpoint)

    async def _async_put(self, endpoint, payload):
        return self._answer("PUT", endpoint, payload)

    async def _async_post(self, endpoint, payload):
        return self._answer("POST", endpoint, payload)

    async def async_login(self):
        return True


def _routes(new_version, cycle=None, charge_config=CHARGE_CONFIG_DATA):
    return {
        f"api/iterate/sysSet/hasNewVersionCharge?id={HOST}": {**OK, "data": new_version},
        f"api/iterate/sysSet/getCycleStrategy?id={HOST}": cycle or {**OK, "data": CYCLE_DATA},
        f"api/iterate/sysSet/getChargeConfigInfo?id={HOST}": {**OK, "data": charge_config},
        "api/iterate/sysSet/setCycleStrategy": OK,
        "api/iterate/sysSet/updateChargeConfigInfo": {"code": 200},
        "api/iterate/sysSet/v2/saveFeedStrategy": OK,
    }


async def test_new_version_system_uses_cycle_strategy():
    client = _FakeClient(_routes(new_version=True))
    s = await BatterySettingsAPI(client).fetch_current_settings(max_retries=1)
    assert s.api_variant == models.VARIANT_CYCLE_STRATEGY
    assert s.charge_slots[0].begin_time == "11:00"
    assert client.settings_api_variant == models.VARIANT_CYCLE_STRATEGY
    assert not any("getChargeConfigInfo" in c[1] for c in client.calls)


async def test_legacy_system_uses_charge_config():
    client = _FakeClient(_routes(new_version=False))
    api = BatterySettingsAPI(client)
    s = await api.fetch_current_settings(max_retries=1)
    assert s.api_variant == models.VARIANT_CHARGE_CONFIG
    assert s.charge_slots[0].end_time == "23:45"
    assert not any("getCycleStrategy" in c[1] for c in client.calls)

    s.charge_slots[0].end_time = "03:15"
    assert await api.put(s, max_retries=1)
    method, endpoint, payload = client.calls[-1]
    assert (method, endpoint) == ("PUT", "api/iterate/sysSet/updateChargeConfigInfo")
    assert payload["timeChae1"] == "03:15"


async def test_variant_detected_once_per_client():
    client = _FakeClient(_routes(new_version=True))
    await BatterySettingsAPI(client).fetch_current_settings(max_retries=1)
    await BatterySettingsAPI(client).fetch_current_settings(max_retries=1)
    detections = [c for c in client.calls if "hasNewVersionCharge" in c[1]]
    assert len(detections) == 1


async def test_6030_falls_back_to_charge_config_within_one_attempt():
    """hasNewVersionCharge unavailable + 6030 from getCycleStrategy → legacy API.

    Must succeed with max_retries=1 — the submit path fetches single-attempt.
    """
    routes = _routes(new_version=None, cycle={"code": 6030, "msg": "The system does not exist"})
    routes[f"api/iterate/sysSet/hasNewVersionCharge?id={HOST}"] = None
    client = _FakeClient(routes)
    s = await BatterySettingsAPI(client).fetch_current_settings(max_retries=1)
    assert s is not None
    assert s.api_variant == models.VARIANT_CHARGE_CONFIG
    assert client.settings_api_variant == models.VARIANT_CHARGE_CONFIG


async def test_detection_failure_is_not_cached():
    routes = _routes(new_version=True)
    routes[f"api/iterate/sysSet/hasNewVersionCharge?id={HOST}"] = None
    client = _FakeClient(routes)
    s = await BatterySettingsAPI(client).fetch_current_settings(max_retries=1)
    assert s.api_variant == models.VARIANT_CYCLE_STRATEGY
    assert client.settings_api_variant is None


async def test_cycle_strategy_put_unchanged():
    client = _FakeClient(_routes(new_version=True))
    api = BatterySettingsAPI(client)
    s = await api.fetch_current_settings(max_retries=1)
    assert await api.put(s, max_retries=1)
    method, endpoint, payload = client.calls[-1]
    assert (method, endpoint) == ("PUT", "api/iterate/sysSet/setCycleStrategy")
    assert payload["id"] == HOST
    assert "chargeTimeList" in payload


async def test_feedin_posts_v2_and_stamps_host_serial_on_new_slots():
    client = _FakeClient(_routes(new_version=True))
    feedin = models.GridFeedInSettings(system_id=HOST, slots=[
        models.GridFeedInSlot(start="16:00", end="18:00", sys_sn="SLOT-SN"),
        models.GridFeedInSlot(start="19:00", end="20:00"),
    ])
    assert await GridFeedInSettingsAPI(client).post(feedin, max_retries=1)
    method, endpoint, payload = client.calls[-1]
    assert (method, endpoint) == ("POST", "api/iterate/sysSet/v2/saveFeedStrategy")
    assert [d["sysSn"] for d in payload["feedStrategyDTOList"]] == ["SLOT-SN", "HOST-SN"]
