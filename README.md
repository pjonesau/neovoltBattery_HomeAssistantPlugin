# Byte-Watt Battery Monitor for Home Assistant (fork)

A fork of
[candreacchio/neovoltBattery_HomeAssistantPlugin](https://github.com/candreacchio/neovoltBattery_HomeAssistantPlugin).
See upstream for the integration's features, setup, entities and services. This page only covers what the fork changes.

The fork includes everything in upstream up to `80a30e8` (20 July 2026). The
integration domain is still `bytewatt`, and upstream's existing entities keep their
IDs.

## How this fork differs from upstream

### Battery SOC on parallel systems

On some parallel systems, the portal reports the whole bank's SOC on the host
inverter and 0% on each follower. Upstream uses the combined `sysSn=All` reading,
which averages those by capacity and so reads low.

- **Battery Percentage** comes from the host inverter's own reading. Power and
  energy figures still come from the combined reading.
- If the host request fails, the sensor keeps the last good host value for up to
  10 minutes, then shows *unknown*. It never falls back to the combined value.
  Each fallback is logged as a warning.

### Per-stack battery sensors (new)

In a parallel install, each inverter can only charge or discharge its own battery
stack. Each inverter gets three sensors:

- **Host / Follower `<serial>` Stack Battery** (%): that stack's own SOC. It comes
  from the portal's 5-minute history, so it can lag by up to 5 minutes.
- **… Stack Power** (W): that inverter's own AC power. Negative means charging.
- **… Stack State**: full, charging, discharging or empty.

Stack capacities come from the portal's inverter list.

### PV1 / PV2 Power sensors (new)

These show the solar power on each input of the host inverter (`ppv1`, `ppv2`).
They read from the host request the integration already makes, so there are no
extra API calls. When there's no reading they show *unavailable*, not 0 W.

### Battery State sensor (new)

Shows Charging, Discharging or Idle, based on battery power (`pbat`).

### Battery settings: the right API for each system

The portal uses one of two settings APIs, depending on the system: **cycle
strategy** on newer firmware, or the legacy **charge config**. The fork asks the
portal which one each system uses (`hasNewVersionCharge`), as the portal's own web
page does. Legacy systems only have Time 1 and no per-slot power, so the Battery
Charge Power and Battery Discharge Power entities show as unavailable there.

### Grid Feed-in saved through the portal's v2 endpoint

Feed-in settings are saved through `v2/saveFeedStrategy`, which stores a cutoff
SOC for each time slot.

- The **Grid Feed-in Cutoff SOC** entity and the `set_grid_feedin_cutoff_soc`
  service apply to every slot.
- A cutoff below the minimum SOC is rejected, as the portal does.
- An unset cutoff is shown, saved and copied to new slots as 30%, the portal's
  default.

### Fewer outage notifications

Upstream raises a persistent notification for every reconnect attempt, transient
error and recovery. The fork raises one "extended outage" notification once the
portal has been unreachable for 30 minutes, and dismisses it on recovery.

### Tests and developer docs

The fork adds tests for the settings API, the settings manager, per-stack data,
the PV inputs and the bank SOC. `RECOVERY_SYSTEM.md` describes the current
recovery behaviour.
