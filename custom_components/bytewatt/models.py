"""Data models for the Byte-Watt integration."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Safe field-coercion helpers used by every from_api_response().
#
# dict.get(key, default) returns `default` only when the KEY is missing —
# if the API sends `"foo": null` or `"foo": ""`, dict.get returns the
# null/empty string and the subsequent int()/float() call raises TypeError
# or ValueError. The whole settings refresh then fails and entities go
# unavailable. These helpers coerce missing / null / empty / non-numeric
# values to the supplied default.
# ---------------------------------------------------------------------------

def _safe_int(data: Dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(data: Dict[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(data: Dict[str, Any], key: str, default: bool) -> bool:
    """Boolean-coerce an API field, with string-aware semantics.

    bool("false") is True in Python because any non-empty string is
    truthy — that would silently flip the wrong way for an API that ever
    returns string booleans. Handle the common string forms explicitly
    so this helper does the right thing regardless of whether the
    server sends true/1/"true"/"1"/"yes"/"on".
    """
    value = data.get(key, default)
    if value is None:
        return default
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("true", "1", "yes", "on", "y", "t"):
            return True
        if lower in ("false", "0", "no", "off", "n", "f", ""):
            return False
        # Unknown string — log + fall back to default rather than guess.
        return default
    return bool(value)


def _safe_str(data: Dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if value is None:
        return default
    return str(value)


@dataclass
class SoCData:
    """Represents battery State of Charge data."""
    soc: float = 0
    grid_consumption: float = 0
    battery: float = 0
    house_consumption: float = 0
    create_time: str = ""
    pv: float = 0

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "SoCData":
        return cls(
            soc=data.get("soc", 0),
            grid_consumption=data.get("gridConsumption", 0),
            battery=data.get("battery", 0),
            house_consumption=data.get("houseConsumption", 0),
            create_time=data.get("createTime", ""),
            pv=data.get("pv", 0),
        )


@dataclass
class GridData:
    """Represents grid energy data."""
    total_solar_generation: float = 0
    total_feed_in: float = 0
    total_battery_charge: float = 0
    total_battery_discharge: float = 0
    pv_power_house: float = 0
    pv_charging_battery: float = 0
    total_house_consumption: float = 0
    grid_based_battery_charge: float = 0
    grid_power_consumption: float = 0

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "GridData":
        return cls(
            total_solar_generation=data.get("Total_Solar_Generation", 0),
            total_feed_in=data.get("Total_Feed_In", 0),
            total_battery_charge=data.get("Total_Battery_Charge", 0),
            total_battery_discharge=data.get("Total_Battery_Discharge", 0),
            pv_power_house=data.get("PV_Power_House", 0),
            pv_charging_battery=data.get("PV_Charging_Battery", 0),
            total_house_consumption=data.get("Total_House_Consumption", 0),
            grid_based_battery_charge=data.get("Grid_Based_Battery_Charge", 0),
            grid_power_consumption=data.get("Grid_Power_Consumption", 0),
        )


# ---------------------------------------------------------------------------
# Battery settings API variants.
#
# The portal asks ``hasNewVersionCharge?id=<host>`` and shows the Cyclic
# Strategy form (getCycleStrategy / setCycleStrategy) when it returns true,
# or the legacy charge-config form (getChargeConfigInfo /
# updateChargeConfigInfo) when it returns false. Both endpoints may answer
# 200 on a new-version system, but only the one the portal uses holds the
# live schedule — so the variant must be chosen, not guessed from which
# endpoint responds.
# ---------------------------------------------------------------------------

VARIANT_CYCLE_STRATEGY = "cycle_strategy"
VARIANT_CHARGE_CONFIG = "charge_config"


# ---------------------------------------------------------------------------
# New Cycle Strategy models — matches getCycleStrategy / setCycleStrategy
# ---------------------------------------------------------------------------

@dataclass
class ChargeSlot:
    """One charge time slot."""
    begin_time: str = "00:00"
    end_time: str = "00:00"
    charge_limit: float = 100.0    # Charging cutoff SOC %
    charge_power: int = 8000       # W
    sort: int = 1
    weeks: List[int] = field(default_factory=lambda: [7, 1, 2, 3, 4, 5, 6])
    feed_mode: int = 0
    equip_group_id: int = 0
    feed_power: int = 0

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "ChargeSlot":
        return cls(
            begin_time=_safe_str(data, "beginTime", "00:00"),
            end_time=_safe_str(data, "endTime", "00:00"),
            charge_limit=_safe_float(data, "chargeLimit", 100.0),
            charge_power=_safe_int(data, "chargePower", 8000),
            sort=_safe_int(data, "sort", 1),
            weeks=data.get("weeks") or [7, 1, 2, 3, 4, 5, 6],
            feed_mode=_safe_int(data, "feedMode", 0),
            equip_group_id=_safe_int(data, "equipGroupId", 0),
            feed_power=_safe_int(data, "feedPower", 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "beginTime": self.begin_time,
            "endTime": self.end_time,
            "chargeLimit": self.charge_limit,
            "chargePower": self.charge_power,
            "sort": self.sort,
            "weeks": self.weeks,
            "feedMode": self.feed_mode,
            "equipGroupId": self.equip_group_id,
            "feedPower": self.feed_power,
        }


@dataclass
class DischargeSlot:
    """One discharge time slot."""
    begin_time: str = "00:00"
    end_time: str = "00:00"
    charge_limit: float = 10.0     # Discharging cutoff SOC %
    charge_power: int = 10000      # Battery discharge power W
    sort: int = 1
    weeks: List[int] = field(default_factory=lambda: [7, 1, 2, 3, 4, 5, 6])
    feed_mode: int = 0
    equip_group_id: int = 0
    feed_power: int = 0

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "DischargeSlot":
        return cls(
            begin_time=_safe_str(data, "beginTime", "00:00"),
            end_time=_safe_str(data, "endTime", "00:00"),
            charge_limit=_safe_float(data, "chargeLimit", 10.0),
            charge_power=_safe_int(data, "chargePower", 10000),
            sort=_safe_int(data, "sort", 1),
            weeks=data.get("weeks") or [7, 1, 2, 3, 4, 5, 6],
            feed_mode=_safe_int(data, "feedMode", 0),
            equip_group_id=_safe_int(data, "equipGroupId", 0),
            feed_power=_safe_int(data, "feedPower", 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "beginTime": self.begin_time,
            "endTime": self.end_time,
            "chargeLimit": self.charge_limit,
            "chargePower": self.charge_power,
            "sort": self.sort,
            "weeks": self.weeks,
            "feedMode": self.feed_mode,
            "equipGroupId": self.equip_group_id,
            "feedPower": self.feed_power,
        }


@dataclass
class CycleStrategy:
    """Battery cycle strategy — maps to getCycleStrategy / setCycleStrategy.

    Field names mirror the server-side JSON keys verbatim (snake_case).
    Entity code does NOT access these fields directly — it goes through
    SettingsManager, which translates logical names like ``minimum_soc``
    to the underlying slot/field. That keeps the model layer free of
    presentation concerns.
    """
    # Top-level flags
    grid_charge_cycle: int = 1      # gridChargeCycle  (grid charging enabled)
    ctr_dis_cycle: int = 1          # ctrDisCycle      (discharge time control)
    bat_use_cap: float = 10.0       # batUseCap        (global discharging cutoff SOC)
    execute_cycle_type: int = 0     # 0=every day, 1=every week
    ups_reserve: int = 0
    loadcutout_en: int = 0
    cutoff_soc: int = 0
    wakeup_soc: int = 0
    is_support_discharge_soc: bool = True
    is_support_charger_power: bool = True
    poinv: int = 10000

    # Time slot lists
    charge_slots: List[ChargeSlot] = field(default_factory=list)
    discharge_slots: List[DischargeSlot] = field(default_factory=list)

    # Echo of unknown GET fields so they round-trip through PUT
    raw_data: Dict[str, Any] = field(default_factory=dict)
    # Set by BatterySettingsAPI after fetch; used in to_dict() for the "id" field
    host_system_id: str = ""
    # Which settings API this was read from, and must be written back to
    api_variant: str = VARIANT_CYCLE_STRATEGY

    @property
    def supports_slot_power(self) -> bool:
        """Charge-config systems have no per-slot charge/discharge power."""
        return self.api_variant == VARIANT_CYCLE_STRATEGY

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "CycleStrategy":
        charge_slots = [
            ChargeSlot.from_api_response(s)
            for s in (data.get("dayChargeTimeList") or [])
        ]
        discharge_slots = [
            DischargeSlot.from_api_response(s)
            for s in (data.get("dayDischargeTimeList") or [])
        ]
        return cls(
            grid_charge_cycle=_safe_int(data, "gridChargeCycle", 1),
            ctr_dis_cycle=_safe_int(data, "ctrDisCycle", 1),
            bat_use_cap=_safe_float(data, "batUseCap", 10.0),
            execute_cycle_type=_safe_int(data, "executeCycleType", 0),
            ups_reserve=_safe_int(data, "upsReserve", 0),
            loadcutout_en=_safe_int(data, "loadcutoutEn", 0),
            cutoff_soc=_safe_int(data, "cutoffSoc", 0),
            wakeup_soc=_safe_int(data, "wakeupSoc", 0),
            is_support_discharge_soc=_safe_bool(data, "isSupportDischargeSoc", True),
            is_support_charger_power=_safe_bool(data, "isSupportChargerPower", True),
            poinv=_safe_int(data, "poinv", 10000),
            charge_slots=charge_slots,
            discharge_slots=discharge_slots,
            raw_data=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Build the PUT payload for setCycleStrategy.

        The server's GET returns ``dayChargeTimeList``/``dayDischargeTimeList``
        but the PUT expects ``chargeTimeList``/``dischargeTimeList``
        (verified against a live HAR capture of the Byte-Watt portal).
        Pop the GET-side keys when echoing raw_data so they don't leak
        stale slot data into the PUT alongside our edits.
        """
        result = dict(self.raw_data)
        result.pop("dayChargeTimeList", None)
        result.pop("dayDischargeTimeList", None)
        result.update({
            "id": self.host_system_id,
            "batUseCap": self.bat_use_cap,
            "upsReserve": self.ups_reserve,
            "executeCycleType": self.execute_cycle_type,
            "loadcutoutEn": self.loadcutout_en,
            "wakeupSoc": self.wakeup_soc,
            "cutoffSoc": self.cutoff_soc,
            "gridChargeCycle": self.grid_charge_cycle,
            "ctrDisCycle": self.ctr_dis_cycle,
            "chargeTimeList": [s.to_dict() for s in self.charge_slots],
            "dischargeTimeList": [s.to_dict() for s in self.discharge_slots],
            "isSupportDischargeSoc": self.is_support_discharge_soc,
            "isSupportChargerPower": self.is_support_charger_power,
            "poinv": self.poinv,
        })
        return result

    @classmethod
    def from_charge_config(cls, data: Dict[str, Any]) -> "CycleStrategy":
        """Parse a getChargeConfigInfo response (legacy charge-config systems).

        Only Time 1 of each schedule is modelled, because that is all
        SettingsManager exposes; Time 2 and every other field round-trip
        untouched via raw_data. There is no per-slot power on this API.
        """
        return cls(
            grid_charge_cycle=_safe_int(data, "gridCharge", 0),
            ctr_dis_cycle=_safe_int(data, "ctrDis", 0),
            bat_use_cap=_safe_float(data, "batUseCap", 10.0),
            ups_reserve=_safe_int(data, "upsReserve", 0),
            loadcutout_en=_safe_int(data, "loadcutoutEn", 0),
            cutoff_soc=_safe_int(data, "cutoffSoc", 0),
            wakeup_soc=_safe_int(data, "wakeupSoc", 0),
            is_support_discharge_soc=_safe_bool(data, "isSupportOffGridSocControl", True),
            is_support_charger_power=False,
            charge_slots=[ChargeSlot(
                begin_time=_safe_str(data, "timeChaf1", "00:00"),
                end_time=_safe_str(data, "timeChae1", "00:00"),
                charge_limit=_safe_float(data, "batHighCap", 100.0),
            )],
            discharge_slots=[DischargeSlot(
                begin_time=_safe_str(data, "timeDisf1", "00:00"),
                end_time=_safe_str(data, "timeDise1", "00:00"),
                charge_limit=_safe_float(data, "batUseCap", 10.0),
            )],
            raw_data=dict(data),
            api_variant=VARIANT_CHARGE_CONFIG,
        )

    def to_charge_config_dict(self) -> Dict[str, Any]:
        """Build the PUT payload for updateChargeConfigInfo.

        Mirrors the portal: the full GET response with the edited fields
        overlaid. Only fields the integration manages are written, so the
        server's own values (Time 2, UPS reserve, off-grid SOC control,
        export limits, ...) pass through unchanged.
        """
        result = dict(self.raw_data)
        # The portal defaults null times before submitting; do the same so
        # the PUT never carries a null schedule.
        for key in ("timeChaf1", "timeChae1", "timeChaf2", "timeChae2",
                    "timeDisf1", "timeDise1", "timeDisf2", "timeDise2"):
            if result.get(key) is None:
                result[key] = "00:00"
        result.update({
            "id": self.host_system_id or result.get("id") or "",
            "gridCharge": self.grid_charge_cycle,
            "ctrDis": self.ctr_dis_cycle,
            "batUseCap": self.bat_use_cap,
        })
        if self.charge_slots:
            slot = self.charge_slots[0]
            result["timeChaf1"] = slot.begin_time
            result["timeChae1"] = slot.end_time
            result["batHighCap"] = slot.charge_limit
        if self.discharge_slots:
            slot = self.discharge_slots[0]
            result["timeDisf1"] = slot.begin_time
            result["timeDise1"] = slot.end_time
        return result


# ---------------------------------------------------------------------------
# Grid feed-in models — matches getFeedStrategyList / v2/saveFeedStrategy
# ---------------------------------------------------------------------------

# Portal defaults applied when the server returns null/0 for these fields.
DEFAULT_FEED_CUTOFF_SOC = 30.0
DEFAULT_PRECHARGE_SOC = 80.0
ALL_WEEKDAYS = [7, 1, 2, 3, 4, 5, 6]


def _positive_or_none(data: Dict[str, Any], key: str) -> Optional[float]:
    """Parse a SOC the portal treats as unset when null, empty, or 0."""
    value = _safe_float(data, key, 0.0)
    return value if value > 0 else None

@dataclass
class GridFeedInSlot:
    """One grid feed-in time slot."""
    id: Optional[int] = None
    sys_sn: str = ""
    start: str = "00:00"
    end: str = "00:00"
    feed_power: int = 0
    sort: int = 1
    # Per-slot discharge cutoff (v2); None = server returned null/0
    feed_cutoff_soc: Optional[float] = None
    weeks: List[int] = field(default_factory=lambda: list(ALL_WEEKDAYS))

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "GridFeedInSlot":
        return cls(
            id=data.get("id"),  # may legitimately be None for new slots
            sys_sn=_safe_str(data, "sysSn", ""),
            start=_safe_str(data, "start", "00:00"),
            end=_safe_str(data, "end", "00:00"),
            feed_power=_safe_int(data, "feedPower", 0),
            sort=_safe_int(data, "sort", 1),
            feed_cutoff_soc=_positive_or_none(data, "feedCutoffSoc"),
            weeks=data.get("weeks") or list(ALL_WEEKDAYS),
        )

    @property
    def cutoff_soc(self) -> float:
        """The cutoff this slot is shown and saved with.

        An unset cutoff means the portal default, as on the portal itself.
        """
        return self.feed_cutoff_soc or DEFAULT_FEED_CUTOFF_SOC

    def to_dict(self) -> Dict[str, Any]:
        """Build one feedStrategyDTOList entry for v2/saveFeedStrategy.

        Matches what the portal submits: the form fields plus sysSn/sort.
        The server-side row ``id`` is not sent — the portal doesn't either.
        """
        d: Dict[str, Any] = {
            "start": self.start,
            "end": self.end,
            "feedPower": self.feed_power,
            "sort": self.sort,
            "weeks": self.weeks,
            "feedCutoffSoc": self.cutoff_soc,
        }
        if self.sys_sn:
            d["sysSn"] = self.sys_sn
        return d


@dataclass
class GridFeedInSettings:
    """Grid feed-in control settings."""
    system_id: str = ""
    battery_en: int = 1
    battery_feed_cutoff_soc: float = 20.0
    precharge_en: int = 0
    precharge_soc: Optional[float] = None
    # Minimum SOC; the portal rejects a feed-in cutoff below it
    bat_use_cap: float = 0.0
    slots: List[GridFeedInSlot] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.battery_en)

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self.battery_en = 1 if value else 0

    @classmethod
    def from_api_response(cls, data: Dict[str, Any], system_id: str = "") -> "GridFeedInSettings":
        slots = [GridFeedInSlot.from_api_response(s) for s in (data.get("feedStrategyVOList") or [])]
        return cls(
            system_id=system_id,
            battery_en=_safe_int(data, "batteryEn", 1),
            battery_feed_cutoff_soc=_safe_float(data, "batteryFeedCutoffSoc", 20.0),
            precharge_en=_safe_int(data, "prechargeEn", 0),
            precharge_soc=_positive_or_none(data, "prechargeSoc"),
            bat_use_cap=_safe_float(data, "batUseCap", 0.0),
            slots=slots,
        )

    @property
    def effective_cutoff_soc(self) -> float:
        """The cutoff SOC in force: v2 stores it per slot, so Time 1 wins.

        The top-level batteryFeedCutoffSoc is a v1 field that v2 never saves,
        so it is not a fallback: an unset cutoff is the portal default.
        """
        if self.slots:
            return self.slots[0].cutoff_soc
        return DEFAULT_FEED_CUTOFF_SOC

    def to_dict(self) -> Dict[str, Any]:
        """Build the v2/saveFeedStrategy POST payload, as the portal sends it.

        v2 carries the cutoff SOC per slot, so there is no top-level
        batteryFeedCutoffSoc. Slots are renumbered 1..n like the portal does.
        """
        dto_list = []
        for index, slot in enumerate(self.slots):
            dto = slot.to_dict()
            dto["sort"] = index + 1
            dto_list.append(dto)
        return {
            "id": self.system_id,
            "batteryEn": self.battery_en,
            "feedStrategyDTOList": dto_list,
            "prechargeEn": self.precharge_en,
            "prechargeSoc": self.precharge_soc or DEFAULT_PRECHARGE_SOC,
        }