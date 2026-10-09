# ByteWatt Home Assistant Integration Development Guide

## Repository Overview

A Home Assistant custom integration (domain `bytewatt`) for monitoring and controlling ByteWatt/Neovolt battery systems through the Byte-Watt cloud portal API: real-time solar, battery and grid power flows, daily and cumulative energy, battery charge/discharge scheduling and Grid Feed-in Control, with automatic recovery when the API stalls.

- **Version**: 1.1.0 (`manifest.json`), HACS custom repository, requires Home Assistant 2024.11.0+.
- This checkout is `github.com/pjonesau/neovoltBattery_HomeAssistantPlugin`, a fork of `candreacchio/neovoltBattery_HomeAssistantPlugin`; `manifest.json`, `info.md` and the README still point documentation and issues at the original repo.

## Project Structure

```
custom_components/bytewatt/
├── __init__.py            # Entry setup/unload, service registration and handlers
├── config_flow.py         # Setup (credentials → host inverter), reconfigure, options (scan interval)
├── const.py               # Constants, defaults and config keys
├── coordinator.py         # DataUpdateCoordinator: polling, heartbeat, stale-data recovery
├── bytewatt_client.py     # Thin high-level wrapper over api/neovolt_client.py
├── models.py              # Data models (CycleStrategy, GridFeedInSlot, …)
├── settings_manager.py    # SettingsManager: server cache + pending diff + submit (single source of truth)
├── sensor.py              # ~30 sensors: real-time power, today's energy, cumulative totals
├── switch.py              # Grid charging, discharge time control (+ feed-in switch from grid_feedin.py)
├── number.py              # Min SOC, charge cap, slot charge/discharge power (+ feed-in numbers)
├── time.py                # Charge/discharge windows (+ feed-in Time 1 start/end)
├── grid_feedin.py         # Grid Feed-in entities, set up from switch/number/time
├── button.py              # Re-exports pending.async_setup_entry
├── pending.py             # Submit Settings / Discard Pending Settings buttons
├── validation.py          # Minimal data validation
├── services.yaml          # Service schemas
├── strings.json           # UI strings (translations/en.json is a copy — keep them identical)
├── translations/en.json
├── brand/                 # Icon and logo images
├── api/
│   ├── neovolt_auth.py    # Password encryption and authentication
│   ├── neovolt_client.py  # Low-level async HTTP client (power data, stats, host SOC)
│   └── settings.py        # Stateless settings transport: endpoints, payloads, retries, 6069 re-login
└── utilities/
    ├── circuit_breaker.py
    ├── connection_stats.py
    ├── diagnostic_service.py  # health_check service implementation
    └── time_utils.py

tests/                     # pytest suite (models, client, settings API, settings manager)
scripts/                   # manual_auth_check.py, manual_battery_data_check.py — live API probes, run by hand
info.md                    # HACS info page
RECOVERY_SYSTEM.md         # Recovery system description
```

## Commands
- **Tests**: `pip install -r requirements_test.txt`, then `pytest` from the repo root (`pytest.ini` sets `testpaths = tests` and `asyncio_mode = auto`, so async tests need no marker)
- **Manual Install**: Copy `custom_components/bytewatt` to Home Assistant's `custom_components` directory
- **Lint**: `flake8 custom_components/bytewatt --max-line-length=100`
- **Type Check**: `mypy custom_components/bytewatt --ignore-missing-imports`
- **Syntax Check**: `find custom_components/bytewatt -name '*.py' -exec python3 -m py_compile {} +`
- **Debug**: in HA `configuration.yaml`, `logger: logs: custom_components.bytewatt: debug`

## Code Style
- **Python**: whatever Home Assistant 2024.11+ runs on (3.12+)
- **Formatting**: 4 spaces (not tabs), <100 char lines
- **Imports**: Standard lib → Third party → Home Assistant, grouped with blank lines
- **Naming**: CamelCase (classes), UPPER_CASE (constants), snake_case (variables/functions)
- **Error handling**: Try/except with appropriate logging levels
- **Comments**: Docstrings with triple double quotes
- **Type hints**: Required for all new functions/methods
- **Magic Numbers**: Use named constants from `const.py`

## Architecture

### Data path
`ByteWattDataUpdateCoordinator` (`coordinator.py`) polls through `bytewatt_client.py` → `api/neovolt_client.py` every `scan_interval` seconds (default 60, minimum 30). Sensors are `CoordinatorEntity`s reading its data.

### Settings path (staged edits)
All writable entities — switches, numbers, times, and the Grid Feed-in entities — go through `SettingsManager` (`settings_manager.py`), never the API directly:
- Entities read `effective_*()` (server value overlaid with anything pending) and write `stage_*()` (validated, held locally).
- The **Submit Settings** button (`pending.py`) calls `submit()`, which pushes the battery batch and the feed-in batch through `api/settings.py` in one go. On a per-batch failure, that batch's pending changes are restored so the UI does not lie. **Discard Pending Settings** drops them.
- Services stage and submit immediately, on a path kept separate from the UI's pending dict.
- After a successful submit, refreshes trust the local cache for a short window, because the portal is not read-after-write consistent.
- Unloading the entry with changes pending raises a persistent notification that they were lost.

Read the docstring at the top of `settings_manager.py` before changing any of this; it describes the locking and snapshot-restore model.

### Host inverter
Multi-inverter accounts pick a **host** inverter during setup (`select_inverter` step), stored as `host_system_id` / `host_sys_sn`. It can be changed later through **Reconfigure** (`async_step_reconfigure`), not the options flow. Settings and SOC reads target the host; see the API notes below for why an empty `id=` is not safe. A repair issue is raised when a multi-inverter account has no host configured.

### Recovery
The coordinator runs a heartbeat (every 120 s), marks data stale after 300 s, and after 3 consecutive stale checks resets the client and re-authenticates. A failed recovery is retried *sooner*, not later — the next check comes at the heartbeat interval divided by the attempt count (capped at 5, floor 30 s) — and API calls go through a circuit breaker (`utilities/circuit_breaker.py`); it also reconnects daily at 03:30. These values are read from `entry.options` (`heartbeat_interval`, `max_data_age`, `stale_checks_threshold`, `notify_on_recovery`, `diagnostics_mode`, `auto_reconnect_time`), but **the options flow only exposes `scan_interval`**, so in practice they are always the `DEFAULT_*` values in `const.py`. `diagnostics_mode` is toggled at runtime by the `toggle_diagnostics` service. See `RECOVERY_SYSTEM.md`.

### Services
Defined in `services.yaml`, registered in `__init__.py`. All accept an optional `entry_id` (required with more than one account).
- Battery: `set_minimum_soc`, `set_charge_cap`, `set_charge_start_time`, `set_charge_end_time`, `set_discharge_start_time`, `set_discharge_time` (end), `update_battery_settings`
- Grid feed-in: `set_grid_feedin_enabled`, `set_grid_feedin_cutoff_soc`, `update_grid_feedin_slot`
- Maintenance: `force_reconnect`, `health_check`, `toggle_diagnostics`

## Portal API notes
### Battery Settings API Variants (verified against the portal, 2026-09)
The portal picks one of two battery-settings APIs per system, and so does
`api/settings.py` (`BatterySettingsAPI`):

- **Detection**: `GET api/iterate/sysSet/hasNewVersionCharge?id=<host>` → `data: true`
  means cycle strategy, `false` means charge config. The result is cached on the
  client as `settings_api_variant`. If detection fails, cycle strategy is assumed,
  and a `6030` ("system does not exist") from `getCycleStrategy` switches to charge config.
- **Cycle strategy** (newer firmware): `GET getCycleStrategy?id=` / `PUT setCycleStrategy`.
  GET returns `dayChargeTimeList`; PUT expects `chargeTimeList` (see `CycleStrategy.to_dict`).
- **Charge config** (legacy): `GET getChargeConfigInfo?id=` / `PUT updateChargeConfigInfo`.
  Mapped into `CycleStrategy` via `from_charge_config` / `to_charge_config_dict`
  (Time 1 only; everything else round-trips via `raw_data`). It has no per-slot
  power, so the power entities are unavailable (`CycleStrategy.supports_slot_power`).
- On a new-version system **both** GETs answer 200, but only the cycle strategy
  holds the live schedule. Never pick the variant by "whichever endpoint responds".
- An empty `id=` resolves to the account's first system, which may be a follower
  inverter. Multi-inverter accounts must configure the host id.

### Grid Feed-in API
- `GET api/iterate/sysSet/getFeedStrategyList?id=<host>`
- `POST api/iterate/sysSet/v2/saveFeedStrategy`: the portal only uses v2. Payload:
  `{id, batteryEn, feedStrategyDTOList, prechargeEn, prechargeSoc}` where each slot is
  `{start, end, feedPower, feedCutoffSoc, weeks, sysSn, sort}`. The cutoff SOC is
  per slot; the portal defaults null values to cutoff 30 and precharge 80, and
  requires cutoff >= `batUseCap`.
- An unset slot cutoff is shown, saved and inherited by new slots as 30
  (`GridFeedInSlot.cutoff_soc`). The GET's top-level `batteryFeedCutoffSoc` is a v1
  field that v2 never saves, so it is not a fallback.

### Battery SOC on Parallel Systems (verified 2026-10)
- `getLastPowerData?sysSn=<host>` reports the whole bank's SOC; followers report `soc: 0`
  and zeroed power fields.
- `sysSn=All` returns the capacity-weighted average SOC, so it reads low
  (host 99.9%, follower 0%, All 74.93% for 28.8 + 9.6 kWh). Power fields under `All` match the host.
- The client keeps `All` for power and stats but takes `soc` from the host
  (`NeovoltClient._async_get_host_soc`). If that call fails, it falls back to `All`.

### Legacy Charge Config Format (reference)

**GET Settings** (`getChargeConfigInfo?id=`):
```json
{
  "code": 200,
  "msg": "Success", 
  "data": {
    "gridCharge": 0,
    "timeChaf1": "14:30",
    "timeChae1": "16:00",
    "ctrDis": 0,
    "timeDisf1": "16:00", 
    "timeDise1": "06:00",
    "batUseCap": 6,
    "batHighCap": 100
  }
}
```

**PUT Settings** (`updateChargeConfigInfo`):
```json
{
  "id": "",
  "gridCharge": 0,
  "timeChaf1": "14:30",
  "timeChae1": "16:00",
  "ctrDis": 0,
  "timeDisf1": "16:00",
  "timeDise1": "06:00",
  "batUseCap": 6,
  "batHighCap": 100,
  "batCapRange": [5, 100],
  "isJapaneseDevice": false,
  "upsReserveEnable": true,
  "chargeModeSetting": 0
}
```

## Testing & Validation

`tests/` is a pytest suite (`pytest-asyncio` in auto mode) covering `models.py`, `api/neovolt_client.py`, `api/settings.py` and `settings_manager.py` with mocked HTTP. There are no entity, config-flow or coordinator tests; those can only be exercised in a running Home Assistant. The `scripts/` probes hit the live portal with real credentials and are run by hand.

Mocked responses only prove the code agrees with the fixtures. The API notes above were verified against the live portal; when changing how a payload is built or parsed, check it against the portal's own requests rather than the fixtures.

### Validation Strategy
- **Basic Validation**: SOC range checks, required field validation
- **API Response Validation**: Server-side data is trusted as accurate
- **Settings validation**: `SettingsManager.stage_*()` rejects invalid values (`SettingsValidationError`) before anything is staged — e.g. a feed-in cutoff SOC below the minimum SOC
- **Retry Logic**: Exponential backoff for transient failures
- **Circuit Breaker**: Prevents cascading failures during outages

## Development Notes

### Adding New Sensors
1. Define sensor type constant in `const.py`
2. Add sensor configuration in `sensor.py`
3. Update `coordinator.py` if new data extraction needed
4. Add translations in `translations/en.json`

### Adding New Services
1. Define service constant in `const.py`
2. Add service handler in `__init__.py`
3. Define service schema in `services.yaml`
4. Update client methods if API changes needed

### Error Handling Best Practices
- Use specific exception types from API layers
- Log at appropriate levels (debug/info/warning/error)
- Implement retry logic with exponential backoff
- Use circuit breaker for external API calls
- Provide meaningful error messages to users

### Performance Considerations
- Minimum scan interval: 30 seconds (to prevent API abuse)
- Connection pooling for HTTP requests
- Efficient data structures for historical tracking
- Automatic cleanup of diagnostic logs (max 100 entries)