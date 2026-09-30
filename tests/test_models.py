"""Tests for the dataclass models — especially the HAR-verified round-trip
between getCycleStrategy / setCycleStrategy field names.

These tests only need ``models.py`` itself (stdlib-only at the module
level), so they load it directly via importlib instead of going through
``custom_components.bytewatt`` — the package's ``__init__.py`` would
otherwise pull in homeassistant/voluptuous, which we don't want for
pure-model tests.
"""
from __future__ import annotations

import importlib.util
import os

import pytest


def _load_models_module():
    """Load models.py directly without triggering the package's __init__."""
    here = os.path.dirname(__file__)
    path = os.path.abspath(os.path.join(
        here, "..", "custom_components", "bytewatt", "models.py",
    ))
    spec = importlib.util.spec_from_file_location("bytewatt_models", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


models = _load_models_module()
ChargeSlot = models.ChargeSlot
DischargeSlot = models.DischargeSlot
CycleStrategy = models.CycleStrategy
GridFeedInSettings = models.GridFeedInSettings
GridFeedInSlot = models.GridFeedInSlot


# ---------------------------------------------------------------------------
# CycleStrategy: GET → model → PUT round-trip
# ---------------------------------------------------------------------------

GET_RESPONSE_SAMPLE = {
    "gridChargeCycle": 0,
    "ctrDisCycle": 0,
    "batUseCap": 5,
    "executeCycleType": 0,
    "upsReserve": 1,
    "loadcutoutEn": 0,
    "cutoffSoc": 0,
    "wakeupSoc": 0,
    "isSupportDischargeSoc": True,
    "isSupportChargerPower": True,
    "poinv": 10000,
    "dayChargeTimeList": [
        {"beginTime": "01:00", "endTime": "05:00", "chargeLimit": 100,
         "chargePower": 8000, "sort": 1},
    ],
    "dayDischargeTimeList": [
        {"beginTime": "17:00", "endTime": "23:00", "chargeLimit": 10,
         "chargePower": 10000, "sort": 1},
    ],
    # Server returns extra keys we don't model — they should round-trip via raw_data.
    "extraServerField": "preserve_me",
}


def test_from_api_response_parses_top_level_fields():
    s = CycleStrategy.from_api_response(GET_RESPONSE_SAMPLE)
    assert s.bat_use_cap == 5
    assert s.grid_charge_cycle == 0
    assert s.ctr_dis_cycle == 0
    assert s.ups_reserve == 1
    assert s.is_support_discharge_soc is True


def test_from_api_response_parses_slot_lists():
    s = CycleStrategy.from_api_response(GET_RESPONSE_SAMPLE)
    assert len(s.charge_slots) == 1
    assert s.charge_slots[0].begin_time == "01:00"
    assert s.charge_slots[0].end_time == "05:00"
    assert len(s.discharge_slots) == 1
    assert s.discharge_slots[0].end_time == "23:00"


def test_to_dict_renames_slot_keys_for_put():
    """GET uses dayChargeTimeList/dayDischargeTimeList; PUT uses chargeTimeList/dischargeTimeList.

    Confirmed against a live HAR capture from the Byte-Watt portal — this
    asymmetry is intentional and must NOT regress.
    """
    s = CycleStrategy.from_api_response(GET_RESPONSE_SAMPLE)
    s.host_system_id = "test-host-id"
    payload = s.to_dict()
    assert "chargeTimeList" in payload
    assert "dischargeTimeList" in payload
    # GET-side names must NOT appear in the PUT payload — they'd carry the
    # original (stale, unmodified) slots alongside our edits.
    assert "dayChargeTimeList" not in payload
    assert "dayDischargeTimeList" not in payload


def test_to_dict_includes_host_system_id_under_id_key():
    s = CycleStrategy.from_api_response(GET_RESPONSE_SAMPLE)
    s.host_system_id = "host-xyz"
    payload = s.to_dict()
    assert payload["id"] == "host-xyz"


def test_to_dict_preserves_unknown_server_fields():
    s = CycleStrategy.from_api_response(GET_RESPONSE_SAMPLE)
    payload = s.to_dict()
    # raw_data echo lets the server's new fields round-trip without us
    # needing to model them — except for the GET-side slot keys we deliberately strip.
    assert payload.get("extraServerField") == "preserve_me"


def test_to_dict_field_set_matches_har_capture():
    """Every field the Byte-Watt portal sends in its setCycleStrategy PUT.

    Captured from a live save on the portal — these are the keys the
    server requires (or at least always sends).
    """
    s = CycleStrategy.from_api_response(GET_RESPONSE_SAMPLE)
    s.host_system_id = "test"
    payload = s.to_dict()
    required_keys = {
        "id", "batUseCap", "upsReserve", "executeCycleType",
        "loadcutoutEn", "wakeupSoc", "cutoffSoc",
        "gridChargeCycle", "ctrDisCycle",
        "chargeTimeList", "dischargeTimeList",
        "isSupportDischargeSoc", "isSupportChargerPower", "poinv",
    }
    missing = required_keys - payload.keys()
    assert not missing, f"to_dict() missing required PUT fields: {missing}"


# ---------------------------------------------------------------------------
# GridFeedInSettings: round-trip
# ---------------------------------------------------------------------------

FEEDIN_GET_SAMPLE = {
    "batteryEn": 0,
    "batteryFeedCutoffSoc": 30.0,
    "poinv": 5000.0,
    "timePeriodLimit": 6,
    "batUseCap": 5.0,
    "feedStrategyVOList": [
        {"start": "00:00", "end": "00:15", "feedPower": "0",
         "sysSn": "TEST-SN", "sort": 1},
    ],
    "prechargeEn": 0,
    "prechargeSoc": None,
}


def test_feedin_from_api_response():
    s = GridFeedInSettings.from_api_response(FEEDIN_GET_SAMPLE, "test-system-id")
    assert s.system_id == "test-system-id"
    assert s.battery_en == 0
    assert s.battery_feed_cutoff_soc == 30.0
    assert len(s.slots) == 1
    assert s.slots[0].start == "00:00"
    assert s.slots[0].feed_power == 0


def test_feedin_to_dict_matches_portal_v2_post():
    """The portal's v2/saveFeedStrategy POST sends exactly these keys.

    Read from the portal's submit handler (2026-09): the cutoff SOC moved
    into each slot, so there is no top-level batteryFeedCutoffSoc.
    """
    s = GridFeedInSettings.from_api_response(FEEDIN_GET_SAMPLE, "test-id")
    payload = s.to_dict()
    assert set(payload.keys()) == {
        "id", "batteryEn", "feedStrategyDTOList", "prechargeEn", "prechargeSoc",
    }
    assert payload["id"] == "test-id"
    assert set(payload["feedStrategyDTOList"][0].keys()) == {
        "start", "end", "feedPower", "sort", "weeks", "feedCutoffSoc", "sysSn",
    }


def test_feedin_applies_portal_defaults_for_unset_socs():
    """null/0 feedCutoffSoc and prechargeSoc are sent as the portal's 30 / 80."""
    s = GridFeedInSettings.from_api_response(FEEDIN_GET_SAMPLE, "test-id")
    assert s.slots[0].feed_cutoff_soc is None
    payload = s.to_dict()
    assert payload["prechargeSoc"] == 80
    assert payload["feedStrategyDTOList"][0]["feedCutoffSoc"] == 30


def test_feedin_per_slot_cutoff_round_trips():
    data = dict(FEEDIN_GET_SAMPLE, feedStrategyVOList=[
        {"id": 5318885, "start": "16:00", "end": "18:00", "feedPower": 30,
         "feedCutoffSoc": 40, "sysSn": "SN", "sort": 1, "weekday": 127,
         "weeks": [7, 1, 2, 3, 4, 5, 6]},
    ])
    s = GridFeedInSettings.from_api_response(data, "test-id")
    assert s.effective_cutoff_soc == 40
    dto = s.to_dict()["feedStrategyDTOList"][0]
    assert dto["feedCutoffSoc"] == 40
    assert dto["weeks"] == [7, 1, 2, 3, 4, 5, 6]
    assert "id" not in dto  # the portal doesn't send the row id either


def test_feedin_effective_cutoff_falls_back_to_top_level():
    s = GridFeedInSettings.from_api_response(FEEDIN_GET_SAMPLE, "test-id")
    assert s.effective_cutoff_soc == 30.0


def test_feedin_renumbers_sort():
    s = GridFeedInSettings(slots=[GridFeedInSlot(sort=5), GridFeedInSlot(sort=9)])
    assert [d["sort"] for d in s.to_dict()["feedStrategyDTOList"]] == [1, 2]


# ---------------------------------------------------------------------------
# Legacy charge-config API (getChargeConfigInfo / updateChargeConfigInfo)
# ---------------------------------------------------------------------------

CHARGE_CONFIG_SAMPLE = {
    "id": "server-id", "gridCharge": 1, "ctrDis": 0,
    "timeChaf1": "14:30", "timeChae1": "16:00", "timeChaf2": "20:00", "timeChae2": "21:00",
    "timeDisf1": "16:00", "timeDise1": "06:00", "timeDisf2": None, "timeDise2": None,
    "batHighCap": 95, "batUseCap": 6, "batCapRange": [0, 100],
    "upsReserveEnable": True, "upsReserve": 1, "cutoffSoc": None,
    "timeExpLimW1": 800, "extraServerField": "preserve_me",
}


def test_charge_config_parses_time1_and_socs():
    s = CycleStrategy.from_charge_config(CHARGE_CONFIG_SAMPLE)
    assert s.api_variant == models.VARIANT_CHARGE_CONFIG
    assert s.supports_slot_power is False
    assert s.grid_charge_cycle == 1
    assert s.bat_use_cap == 6
    assert (s.charge_slots[0].begin_time, s.charge_slots[0].end_time) == ("14:30", "16:00")
    assert s.charge_slots[0].charge_limit == 95
    assert (s.discharge_slots[0].begin_time, s.discharge_slots[0].end_time) == ("16:00", "06:00")


def test_charge_config_put_overlays_edits_on_get_response():
    s = CycleStrategy.from_charge_config(CHARGE_CONFIG_SAMPLE)
    s.host_system_id = "host-id"
    s.bat_use_cap = 10
    s.grid_charge_cycle = 0
    s.charge_slots[0].end_time = "17:00"
    s.charge_slots[0].charge_limit = 90
    payload = s.to_charge_config_dict()
    assert payload["id"] == "host-id"
    assert payload["batUseCap"] == 10
    assert payload["gridCharge"] == 0
    assert payload["timeChae1"] == "17:00"
    assert payload["batHighCap"] == 90
    # Time 2 and unmodelled fields pass through untouched
    assert (payload["timeChaf2"], payload["timeChae2"]) == ("20:00", "21:00")
    assert payload["upsReserve"] == 1
    assert payload["timeExpLimW1"] == 800
    assert payload["extraServerField"] == "preserve_me"
    # Nothing from the cycle-strategy shape leaks in
    assert not {"chargeTimeList", "dischargeTimeList", "gridChargeCycle"} & payload.keys()


def test_charge_config_put_defaults_null_times():
    payload = CycleStrategy.from_charge_config(CHARGE_CONFIG_SAMPLE).to_charge_config_dict()
    assert (payload["timeDisf2"], payload["timeDise2"]) == ("00:00", "00:00")


def test_charge_config_put_keeps_server_id_without_host():
    payload = CycleStrategy.from_charge_config(CHARGE_CONFIG_SAMPLE).to_charge_config_dict()
    assert payload["id"] == "server-id"


def test_cycle_strategy_supports_slot_power():
    assert CycleStrategy.from_api_response(GET_RESPONSE_SAMPLE).supports_slot_power is True


# ---------------------------------------------------------------------------
# ChargeSlot / DischargeSlot
# ---------------------------------------------------------------------------

def test_chargeslot_roundtrip():
    raw = {"beginTime": "02:00", "endTime": "06:00", "chargeLimit": 90,
           "chargePower": 5000, "sort": 1}
    slot = ChargeSlot.from_api_response(raw)
    out = slot.to_dict()
    for key in ("beginTime", "endTime", "chargeLimit", "chargePower", "sort"):
        assert out[key] == raw[key]


def test_dischargeslot_roundtrip():
    raw = {"beginTime": "17:00", "endTime": "22:00", "chargeLimit": 20,
           "chargePower": 8000, "sort": 1}
    slot = DischargeSlot.from_api_response(raw)
    out = slot.to_dict()
    for key in ("beginTime", "endTime", "chargeLimit", "chargePower", "sort"):
        assert out[key] == raw[key]


def test_gridfeedinslot_omits_optional_keys_when_unset():
    """sysSn is optional and id is never sent — omit both when unset."""
    slot = GridFeedInSlot(start="00:00", end="01:00", feed_power=100, sort=1)
    out = slot.to_dict()
    assert "id" not in out
    assert "sysSn" not in out
    assert out["start"] == "00:00"
    assert out["feedPower"] == 100


# ---------------------------------------------------------------------------
# Safe coercion helpers — defensive parsing for API fields that might be
# missing, null, empty, or wrong-typed.
# ---------------------------------------------------------------------------

_safe_int = models._safe_int
_safe_float = models._safe_float
_safe_bool = models._safe_bool
_safe_str = models._safe_str


def test_safe_int_handles_missing_key():
    assert _safe_int({}, "x", 42) == 42


def test_safe_int_handles_explicit_none():
    """API sending `{"x": null}` used to crash `int(None)`."""
    assert _safe_int({"x": None}, "x", 42) == 42


def test_safe_int_handles_empty_string():
    assert _safe_int({"x": ""}, "x", 42) == 42


def test_safe_int_handles_non_numeric_string():
    assert _safe_int({"x": "not a number"}, "x", 42) == 42


def test_safe_int_passes_through_valid():
    assert _safe_int({"x": 100}, "x", 0) == 100
    assert _safe_int({"x": "100"}, "x", 0) == 100  # numeric string is fine


def test_safe_float_handles_none_and_strings():
    assert _safe_float({"x": None}, "x", 1.0) == 1.0
    assert _safe_float({"x": ""}, "x", 1.0) == 1.0
    assert _safe_float({"x": "abc"}, "x", 1.0) == 1.0
    assert _safe_float({"x": "2.5"}, "x", 0.0) == 2.5
    assert _safe_float({"x": 3}, "x", 0.0) == 3.0


def test_safe_bool_handles_string_false_correctly():
    """bool('false') is True in Python — must not regress."""
    assert _safe_bool({"x": "false"}, "x", True) is False
    assert _safe_bool({"x": "FALSE"}, "x", True) is False
    assert _safe_bool({"x": "0"}, "x", True) is False
    assert _safe_bool({"x": "no"}, "x", True) is False


def test_safe_bool_handles_string_true():
    assert _safe_bool({"x": "true"}, "x", False) is True
    assert _safe_bool({"x": "TRUE"}, "x", False) is True
    assert _safe_bool({"x": "1"}, "x", False) is True
    assert _safe_bool({"x": "yes"}, "x", False) is True


def test_safe_bool_handles_native_bool():
    assert _safe_bool({"x": True}, "x", False) is True
    assert _safe_bool({"x": False}, "x", True) is False


def test_safe_bool_handles_int():
    assert _safe_bool({"x": 1}, "x", False) is True
    assert _safe_bool({"x": 0}, "x", True) is False


def test_safe_bool_unknown_string_falls_back_to_default():
    """Unknown string shouldn't silently coerce — fall back to default."""
    assert _safe_bool({"x": "maybe"}, "x", True) is True
    assert _safe_bool({"x": "maybe"}, "x", False) is False


def test_safe_bool_handles_none():
    assert _safe_bool({"x": None}, "x", True) is True
    assert _safe_bool({"x": None}, "x", False) is False


def test_safe_str_handles_none():
    assert _safe_str({"x": None}, "x", "default") == "default"
    assert _safe_str({}, "x", "default") == "default"
    assert _safe_str({"x": "hello"}, "x", "") == "hello"
