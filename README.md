# SkyRC Charger – Home Assistant Integration

Custom Home Assistant integration that monitors and controls SkyRC battery
chargers over BLE, from what the official SkyRC app does.

| Model | Status |
| --- | --- |
| **MC5000** | Full support, tested on a charger (firmware 1.83): status, start, stop, curves, settings. |
| **NC2200** | Experimental, **monitoring and stop only**: no settings and no curve either (the app reads them from it with other commands). Written from the app, not tested on a charger. |
| **NC3000** | Experimental, **monitoring and stop only**, written from the app and not tested on a charger. Starting a slot is refused until its program has been checked. |
| **NC3000 Octa** (8 slots) | Experimental, **monitoring and stop only**, same limits as the NC3000. The app knows it by its model code `100228`. |
| **MC3000** | Experimental, written from the MC3000 app and not tested on a charger. Another, older protocol (20-byte frames, no password). Per slot: status, battery type, mode, voltage, current, capacity, time, temperature, resistance, errors, the voltage curve, **start** (a full program with the app's ranges and defaults; the chemistry is required) and **stop**. Its own settings: keypress beep, screensaver, display timeout, cooling fan and input voltage limit. It is recognised by the answer it gives, not by a model code. The factory and calibration resets are not offered. |
| **NC2600** | Experimental, written from the NC2600 app (1.98) and not tested on a charger. The MC3000's protocol with its own layout: per slot the status, mode, voltage, current, capacity, time, temperature and resistance, and **start** (charge, refresh, break-in, discharge or cycle, with the app's ranges). The app has no stop command, so none is offered; neither are the curve and the settings (one of them is clamped with values that depend on the model's variant). Told from the MC3000 by the model code it gives (`100113`, `100145`). |
| **BD380** (discharger) | Experimental, **monitoring and stop only**, written from the app and not tested on a device. One "slot": its state, the mode it holds constant (current, voltage, power or resistance), voltage, current, power, energy, capacity, elapsed time and temperature. Its password is the app's default, `5793`. |
| **PSU650** (power supply) | Experimental, **monitoring only**, written from the app and not tested on a device. Recognised by its Bluetooth name. Slot 1 is the DC output, slots 2 to 4 the three USB-C ports: status, voltage, current, power and the negotiated protocol, plus total power, total current and the error code. Nothing to start or stop; switching a port is not offered yet. Its password is `5793`. |
| **Q200, Q200V2, Q200 neo, Q500 neo, D100, D200s, D200 Nex, D200 neo (metal), D260, D750 Ultra, D8neo, B6nano, B6evo, B6 nax** | Experimental, **monitoring and stop only**. These older chargers use another protocol: each channel is unlocked with a password (5793 unless you changed it) and reports whether it works, its voltage, current, capacity, time, temperature and resistance. The number of channels and the password flag are the ones of the list the app downloads from SkyRC. Not tested on a charger. |

The model is recognised when the charger is added: it tells it itself.

> **The MC3000 is back (experimental).** Version 2.0 dropped it, as the SkyRC app 2.94 does not
> support it; it is now written from SkyRC's own MC3000 app (4.1.2), like the other models.

## Origins / credits

- MC5000 BLE protocol: implemented in pure Python from what the official
  SkyRC app (2.94) sends and reads: the commands, the status and program
  layouts, and the voltage ranges and defaults it offers for each chemistry.
  Status, settings, start and stop were checked against a charger on
  firmware 1.83 with a NiMH cell.
- The rest of the integration (config flow, coordinator, entities, services) is
  written for this project, to Home Assistant's own conventions.

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

A charger in range of Home Assistant's Bluetooth is offered automatically
(Settings → Devices & services). Otherwise: `Add integration → SkyRC Charger`,
pick it in the list or type its Bluetooth address. The list comes from Home
Assistant's own Bluetooth manager, so chargers seen through an ESPHome or
Shelly Bluetooth proxy show up as well. Home Assistant connects to the charger
to check it: it must be on, and reachable by an adapter or an *active*
Bluetooth proxy (a passive scanner hears it but cannot connect).

The charger has a four digit passcode: the one shown on its screen on the
MC5000 (`1234` from the factory), `5793` on the BD380 and the older chargers. Leave it empty
during setup to use the factory one. If the charger later refuses it, Home
Assistant asks for the right one (the "Repairs"-style reauthentication
notice). It can also be changed under the integration's **Configure** button.

You can add **multiple chargers**: each becomes its own config entry/device.

## Entities

Entity ids are built from the charger's name, i.e. the title of its config
entry. A charger named "SkyRC MC5000" gives `sensor.skyrc_mc5000_slot_1_voltage`
and so on.

For each of the four slots:
`sensor.*_status`, `*_battery_type`, `*_mode`, `*_voltage`, `*_current`,
`*_capacity`, `*_internal_resistance`, `*_elapsed_time`, `*_temperature`,
`select.*_expected_chemistry`, `button.*_start`, `button.*_stop`.

Only the entities a model reports are created. The BD380 adds a `*_power` (W) and an `*_energy` (Wh) sensor to its slot; the PSU650 adds
`*_power` to each port, and total power, total current and an error sensor to the device. The NC3000, the NC3000 Octa
and the NC2200 report no battery type; the NC2200 has no curve and no
settings; no model other than the MC5000 has a start button, a chemistry
select or `start_all` yet. The older chargers call their channels slots in the
entity names (`sensor.skyrc_q200_slot_1_voltage`), and their status is
`idle`, `active` (charging or discharging: the app does not say which in the
status), `dc_output` or `error`, with the error given as
`system_<code>_charge_<code>`.

Device-level: `button.*_refresh`, `button.*_start_all`, `button.*_stop_all`.

Per slot, the logged voltage curve: the `sensor.*_voltage_curve_points` /
`button.*_fetch_voltage_curve` pair and `switch.*_auto_fetch_voltage_curves`.
The charger logs the whole run from the start of the program: one sample
every 8 seconds at first, then the interval doubles as the run grows (16 s
after about 40 minutes), and the curve comes back in pages of 100 samples.

The per-slot `sensor.*_error` (`none`, or the fault the charger
reports: `reversed_polarity`, `wrong_battery_type`, `capacity_limit`, …; its `description` attribute says what it means and what usually
causes it) and
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
press refresh, start or stop a slot, and lets go of the charger again a
minute later.

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

## Troubleshooting

- **Cannot connect**: the charger accepts one Bluetooth link at a time. Close the SkyRC app,
  check that it is on and within reach of an adapter or a Bluetooth proxy.
- **The passcode is refused**: enter the one shown on the charger's screen (the factory ones are
  `1234` on the MC5000, `5793` on the older chargers); Home Assistant asks again by itself.
- **The charger changed its address** (a new adapter, a replaced charger of the same model):
  use *Reconfigure* on the entry.
- **A slot shows an error**: the error sensor has a `description` attribute with its usual cause.

## Removing the integration

Delete the entry in *Settings > Devices & services*; its device and entities go with it. To
remove the code too, uninstall it from the Marketplace and restart Home Assistant.

## Tests

The unit tests stub out Home Assistant, so they need nothing installed:

```bash
cd tests && python3 -m unittest discover
```

The integration tests run inside a real Home Assistant (`pytest tests_ha`). Both suites together
cover about 97 % of the integration; the CI fails below 95 %.

## Safety

This is a convenience layer for monitoring and start/stop, not a replacement
for checking your charger's configured battery type, current, and safety
cut-offs before starting a slot. Home Assistant constructs the charge
profile that gets sent to the device.

## License

MIT, see [LICENSE](LICENSE). The SkyRC name and icon are SkyRC's: see
[NOTICE](NOTICE).
