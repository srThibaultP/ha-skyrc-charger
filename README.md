# SkyRC Charger – Home Assistant Integration (MC3000 + MC5000)

Custom Home Assistant integration that monitors and controls SkyRC battery
chargers over BLE — supports **both** the MC3000 and the MC5000 (four slots
each) from a single codebase. You pick the model when adding the
integration; each model uses its own protocol "driver" under a shared
interface, so sensors/buttons/services look and behave the same way
regardless of which charger you own.

## Origins / credits

- MC3000 BLE handling: built on the [`skyrc-ble`](https://github.com/jperquin/skyrc-ble)
  library, same as used in [jperquin/ha-skyrc-mc3000](https://github.com/jperquin/ha-skyrc-mc3000),
  whose integration structure (config flow, coordinator, entities,
  voltage curves, program writing) this project was based on and
  keeps tracking.
- MC5000 BLE protocol: implemented in pure Python. It started from the
  reverse engineering documented in [rssdev10/skyrc-mc-rs](https://github.com/rssdev10/skyrc-mc-rs)
  (`docs/PROTOCOL.md`) and now follows what the official SkyRC app (2.94)
  sends and reads. Status, settings, start and stop were checked against a
  charger on firmware 1.83 with a NiMH cell.

## Installation

### Marketplace / HACS (recommended)

1. Marketplace (HACS) → ⋮ → **Custom repositories** → paste
   `https://github.com/srThibaultP/ha-skyrc-charger` → category **Integration**
2. Install **SkyRC Charger (MC3000 / MC5000)**
3. Restart Home Assistant

### Manual

Copy `custom_components/skyrc_charger` into your Home Assistant
`config/custom_components/` folder, then restart Home Assistant.

### Setup

`Settings → Devices & services → Add integration → SkyRC Charger`, choose
your model (MC3000 or MC5000), then pick or enter the BLE address. The
device list comes from Home Assistant's own Bluetooth manager, so chargers
seen through an ESPHome or Shelly Bluetooth proxy show up as well.

The **MC5000** has a four digit passcode, shown on its screen (`1234` from
the factory). Enter it during setup, or later under the integration's
**Configure** button. With the wrong one the charger still reports its
status, and a warning in the log says the passcode was refused.

You can add **multiple chargers** (e.g. one MC3000 and one MC5000, or
several of either) — each becomes its own config entry/device.

## Entities

Entity ids are built from the charger's name, i.e. the title of its config
entry. A charger named "SkyRC MC3000" gives `sensor.skyrc_mc3000_slot_1_voltage`
and so on.

For each of the four slots:
`sensor.*_status`, `*_battery_type`, `*_mode`, `*_voltage`, `*_current`,
`*_capacity`, `*_internal_resistance`, `*_elapsed_time`, `*_temperature`,
`select.*_expected_chemistry`, `button.*_start`, `button.*_stop`.

Only the sensors a model actually reports are created, so an MC5000 gets
none of the MC3000-only ones below.

Device-level: `button.*_refresh`,
`button.*_start_all`, `button.*_stop_all`.

MC3000 only: the per-slot `sensor.*_cycle_count`, `sensor.*_input_voltage`,
plus the diagnostic
`*_temperature_unit`, `*_display_mode`, `*_cooling_fan_mode`,
`*_system_beep` and `*_screensaver` sensors.

Both models: the per-slot `sensor.*_voltage_curve_points` /
`button.*_fetch_voltage_curve` pair and `switch.*_auto_fetch_voltage_curves`.
The MC5000 logs the whole run from the start of the program: one sample
every 8 seconds at first, then the interval doubles as the run grows (16 s
after about 40 minutes), and the curve comes back in pages of 100 samples.

MC5000 only: the per-slot `sensor.*_error` (`none`, or the fault the charger
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

On the MC5000 the status is one of `empty`, `idle`, `standby`, `charging`,
`discharging`, `resting`, `done` or `error`. Battery type, mode, current,
capacity, temperature, resistance and elapsed time are only reported while a
program runs; an idle slot gives its voltage alone.

`examples/skyrc-mc3000-dashboard.yaml` is a ready-made Lovelace dashboard
covering all of it, including charts of the logged voltage curves.

## Using the SkyRC app alongside

Both chargers only allow one BLE client at a time. To use the official SkyRC
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
| `skyrc_charger.start_slot` | Start one slot, after the chemistry interlock. |
| `skyrc_charger.stop_slot` | Stop one slot. |
| `skyrc_charger.start_all` | Start every slot in one go. |
| `skyrc_charger.stop_all` | Stop every slot. |
| `skyrc_charger.fetch_voltage_curve` | Read back a slot's logged voltage curve. |
| `skyrc_charger.write_program` | MC3000: write a complete program to a slot (without starting it). |

### Chemistry interlock

`select.*_expected_chemistry` says what you believe is in a slot, and its
options follow the chemistry names of the model you have — the MC3000 and
MC5000 name them differently, so a single shared list could never match.

On the **MC3000**, `start_slot` and `start_all` refuse to start a slot whose
reported chemistry disagrees with that select, unless it is set to `any`.

On the **MC5000** the select is used as the chemistry to write into the
program instead: unlike the MC3000, the MC5000 BLE protocol has no "start
the currently configured program" command — a full configuration packet must
be sent before every start. `start_slot` and `start_all` therefore also take
optional `chemistry`, `mode`, `current_ma` and `capacity_mah`. Sensible
per-chemistry defaults are used for anything you don't specify (current
defaults to 1000 mA, capacity to 3000 mAh, target/cutoff voltage from the
chemistry's standard values).

There is no default chemistry: a start is refused until one is given, either
in the call or through the select. The modes depend on it too, and a mode
the chemistry doesn't have is refused:

| Chemistry | Modes |
| --- | --- |
| `liion`, `liion_hv`, `lifepo4`, `lto`, `naion` | `charge`, `storage`, `discharge`, `cycle` |
| `nimh`, `nicd`, `eneloop` | `charge`, `refresh`, `break_in`, `discharge`, `cycle` |
| `nizn`, `ram` | `charge`, `discharge`, `cycle` |

### Writing a complete MC3000 program

The MC3000 cannot change one field of a program: the protocol only accepts a
whole one, so `write_program` takes every current, voltage and protection
value together. It writes the program without starting it — use
`start_slot` afterwards.

## Connection handling and logging

These chargers drop their BLE link constantly: the MC3000 firmware closes it
after a few seconds of idling, and both models vanish entirely while the
mains side is off. Every drop is recovered on the next poll, so it is not
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
- The `skyrc_ble` library's own per-drop warnings are demoted to debug. Set
  `logger: logs: skyrc_ble: debug` to see them again.

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
- The MC5000 driver is **not officially supported by SkyRC**; it's a
  community reverse-engineering effort. Test carefully, keep an eye on
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

Same philosophy as the original MC3000 integration: this is a convenience
layer for monitoring and start/stop, not a replacement for checking your
charger's configured battery type, current, and safety cut-offs before
starting a slot — especially on the MC5000, where Home Assistant is now
the one constructing the charge profile that gets sent to the device.

## License

MIT for the original work in this repository, see [LICENSE](LICENSE). Code
inherited from the upstream project, which has no license, is not covered:
see [NOTICE](NOTICE).
