# SkyRC Charger – Home Assistant Integration (MC5000)

Custom Home Assistant integration that monitors and controls the SkyRC
MC5000 battery charger (four slots) over BLE.

> **Version 2.0 drops the MC3000.** The SkyRC app does not support it and its
> support depended on a third-party library. If you still have an MC3000
> entry, remove it; its entities will go. Version 1.x keeps working for the
> MC3000.

## Origins / credits

- MC5000 BLE protocol: implemented in pure Python from what the official
  SkyRC app (2.94) sends and reads: the commands, the status and program
  layouts, and the voltage ranges and defaults it offers for each chemistry.
  Status, settings, start and stop were checked against a charger on
  firmware 1.83 with a NiMH cell.
- The structure of the integration (config flow, coordinator, entities) comes
  from [jperquin/ha-skyrc-mc3000](https://github.com/jperquin/ha-skyrc-mc3000),
  see [NOTICE](NOTICE).

## Installation

### Marketplace / HACS (recommended)

1. Marketplace (HACS) → ⋮ → **Custom repositories** → paste
   `https://github.com/srThibaultP/ha-skyrc-charger` → category **Integration**
2. Install **SkyRC Charger**
3. Restart Home Assistant

### Manual

Copy `custom_components/skyrc_charger` into your Home Assistant
`config/custom_components/` folder, then restart Home Assistant.

### Setup

`Settings → Devices & services → Add integration → SkyRC Charger`, choose
your model (MC5000), then pick or enter the BLE address. The
device list comes from Home Assistant's own Bluetooth manager, so chargers
seen through an ESPHome or Shelly Bluetooth proxy show up as well.

The **MC5000** has a four digit passcode, shown on its screen (`1234` from
the factory). Enter it during setup, or later under the integration's
**Configure** button. With the wrong one the charger still reports its
status, and a warning in the log says the passcode was refused.

You can add **multiple chargers** — each becomes its own config entry/device.

## Entities

Entity ids are built from the charger's name, i.e. the title of its config
entry. A charger named "SkyRC MC5000" gives `sensor.skyrc_mc5000_slot_1_voltage`
and so on.

For each of the four slots:
`sensor.*_status`, `*_battery_type`, `*_mode`, `*_voltage`, `*_current`,
`*_capacity`, `*_internal_resistance`, `*_elapsed_time`, `*_temperature`,
`select.*_expected_chemistry`, `button.*_start`, `button.*_stop`.

Device-level: `button.*_refresh`, `button.*_start_all`, `button.*_stop_all`.

Per slot, the logged voltage curve: the `sensor.*_voltage_curve_points` /
`button.*_fetch_voltage_curve` pair and `switch.*_auto_fetch_voltage_curves`.
The charger logs the whole run from the start of the program: one sample
every 8 seconds at first, then the interval doubles as the run grows (16 s
after about 40 minutes), and the curve comes back in pages of 100 samples.

The per-slot `sensor.*_error` (`none`, or the fault the charger
reports: `reversed_polarity`, `wrong_battery_type`, `capacity_limit`, …) and
the charger's own settings, which you can change from Home Assistant:
`select.*_beep_volume`, `*_notification_volume` (off, low, medium, high),
`*_end_notification` (once, repeat), `*_backlight` (low, medium, high),
`*_sleep_timer` (off, 1 to 120 min) and `switch.*_warning_tone`. They are
read when the connection opens, written back all at once and checked by
reading them again. The firmware and hardware versions show on the device
page.

The MC5000 reports a slot's temperature only when an external probe is
plugged in, so `sensor.*_temperature` is created but disabled by default.

The status of a slot is one of `empty`, `idle`, `standby`, `charging`,
`discharging`, `resting`, `done` or `error`. Battery type, mode, current,
capacity, temperature, resistance and elapsed time are only reported while a
program runs; an idle slot gives its voltage alone.

`examples/skyrc-mc5000-dashboard.yaml` is a Lovelace view with the status of
each slot and buttons that start a charge with presets for AA and AAA NiMH
cells.

## Using the SkyRC app alongside

The charger only allows one BLE client at a time. To use the official SkyRC
app, turn off the entry's "Enable polling for changes" system option
(Settings → Devices & services → SkyRC Charger → ⋮ → System options), or
disable the entry. With polling off, Home Assistant only connects when you
press refresh, start or stop a slot.

## Services

All services take an optional `entry_id` naming the charger to act on. You
can leave it out while a single charger is configured.

| Service | What it does |
| --- | --- |
| `skyrc_charger.refresh` | Force an immediate poll. |
| `skyrc_charger.start_slot` | Start one slot. |
| `skyrc_charger.stop_slot` | Stop one slot. |
| `skyrc_charger.start_all` | Start every slot in one go. |
| `skyrc_charger.stop_all` | Stop every slot. |
| `skyrc_charger.fetch_voltage_curve` | Read back a slot's logged voltage curve. |

### Expected chemistry

`select.*_expected_chemistry` says what is in a slot. The MC5000 only reports
a slot's chemistry while a program runs, so the select is the chemistry
written into the program when a slot is started. `start_slot` and
`start_all` also take optional `chemistry`, `mode`, `current_ma` and
`capacity_mah`. Anything you don't specify gets the value the SkyRC app
proposes for that chemistry: for example 3000 mAh at 3000 mA for a lithium
cell, 3000 mAh at 1000 mA for NiMH, 4.20 V / 3.20 V / 3.80 V (target,
cut-off, storage) for Li-ion and 1.65 V / 0.90 V for NiMH, and a 60 °C
temperature cut-off. The app also limits the voltages to a range per
chemistry (for example 4.05 to 4.25 V for a Li-ion target); a value outside
it is refused.

A slot that already runs a program is refused: the charger acknowledges a new
program for a running slot but keeps the old one going, so stop it first.

There is no default chemistry: a start is refused until one is given, either
in the call or through the select. The modes depend on it too, and a mode
the chemistry doesn't have is refused:

| Chemistry | Modes |
| --- | --- |
| `liion`, `liion_hv`, `lifepo4`, `lto`, `naion` | `charge`, `storage`, `discharge`, `cycle` |
| `nimh`, `nicd`, `eneloop` | `charge`, `refresh`, `break_in`, `discharge`, `cycle` |
| `nizn`, `ram` | `charge`, `discharge`, `cycle` |

## Connection handling and logging

The charger drops its BLE link now and then and vanishes entirely while its
power is off. Every drop is recovered on the next poll, so it is not
worth a log line each time.

- Connections go through
  [`bleak_retry_connector.establish_connection()`](https://github.com/Bluetooth-Devices/bleak-retry-connector),
  which retries the transient adapter/proxy errors that a bare
  `BleakClient.connect()` reports as a hard failure, reuses Home Assistant's
  cached GATT services and cooperates with its connection slot allocator.
- The `BLEDevice` is refreshed from Home Assistant's Bluetooth manager before
  every reconnect; reconnecting with a stale one is the usual cause of
  reconnect loops.
- A poll failure is logged once when an outage starts and once an hour while
  it lasts, not once per ten second poll. Recovery is logged at info.

Last known values keep being served during an outage so the dashboard
doesn't flicker on a one-poll dropout; after about five minutes without
contact the entities go unavailable rather than presenting stale readings as
current.

## Icon / logo

The icon and logo are the SkyRC app icon, a trademark of SkyRC used only to
identify the chargers (see `NOTICE`). They ship in
`custom_components/skyrc_charger/brand/`, which Home Assistant 2026.3 and
later read directly. Older versions show the
generic puzzle-piece icon. Entity icons (buttons, sensors, etc.) work on any
version.

## Known limitations

- **No MC5000 program writing without a start**: on the MC5000, writing a
  program is what starts the slot.
- The driver is **not made or endorsed by SkyRC**: it speaks the same
  protocol as their app. Test carefully, keep an eye on
  your charger the first few times, and don't leave it unattended with
  unfamiliar chemistries/currents.
- Full MC5000 profile editing (delta-peak, trickle, keep-voltage, …) is not
  exposed; those use defaults baked into the driver.

## Tests

The unit tests stub out Home Assistant, so they need nothing installed:

```bash
cd tests && python3 -m unittest discover
```

## Safety

This is a convenience layer for monitoring and start/stop, not a replacement
for checking your charger's configured battery type, current, and safety
cut-offs before starting a slot. Home Assistant constructs the charge
profile that gets sent to the device.

## License

MIT for the original work in this repository, see [LICENSE](LICENSE). Code
inherited from the upstream project, which has no license, is not covered:
see [NOTICE](NOTICE).
