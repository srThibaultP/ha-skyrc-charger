"""MC3000 driver: monitoring, from what the official MC3000 app reads.

The MC3000 has its own, older protocol over the same Bluetooth service:

- every frame is 20 bytes: 0x0F, the command, its data, zeros, and a last
  byte that is the sum of the 19 bytes before it;
- a slot is asked for by its index (0 to 3), not a bitmask;
- there is no password;
- 0x57 is the info request (it carries the Bluetooth address, last byte
  first) and answers the firmware and hardware versions; the charger gives
  no model code, so the model is told by this answer alone;
- 0x55 answers a slot: battery type, mode, number of cycles, state, elapsed
  time, voltage, current, capacity, temperature, internal resistance;
- 0x61 answers the charger's basic settings, of which the temperature unit
  decides how the temperatures are to be read;
- 0x56 answers the logged voltage curve of a slot (up to 245 bytes);
- 0xFE stops a slot.

Starting a slot is not offered: the program is sent with commands that were
not checked against a charger.
"""

from __future__ import annotations

import asyncio
from typing import Any

from bleak.exc import BleakError

from .mc5000_client import CHANNEL_BITMASK, CHAR_UUID, Mc5000Client
from .models import ChannelData, ChargerState, DeviceData, VoltageCurve

FRAME_SIZE = 20
CURVE_FRAME_SIZE = 245
START_BYTE = 0x0F

CMD_STATUS = 0x55
CMD_CURVE = 0x56
CMD_INFO = 0x57
CMD_BASIC_DATA = 0x61
CMD_STOP = 0xFE

# Byte 3 of a 0x55 answer, as the app lists the battery types.
BATTERY_TYPE_NAME = {
    0: "liion",
    1: "lifepo4",
    2: "liion_hv",
    3: "nimh",
    4: "nicd",
    5: "nizn",
    6: "eneloop",
    7: "ram",
    8: "lto",
    9: "naion",
}
_LITHIUM_TYPES = frozenset({"liion", "lifepo4", "liion_hv", "lto", "naion"})
_ZINC_TYPES = frozenset({"nizn", "ram"})

# Byte 4: the mode is an index into the list of the battery type.
_LITHIUM_MODES = ("charge", "refresh", "storage", "discharge", "cycle")
_ZINC_MODES = ("charge", "refresh", "discharge", "cycle")
_NICKEL_MODES = ("charge", "refresh", "break_in", "discharge", "cycle")

# Byte 6: what the slot is doing; 128 and above are errors.
STATUS_BYTE_NAME = {
    0: "standby",
    1: "charging",
    2: "discharging",
    3: "paused",
    4: "done",
}
ERROR_BYTE_NAME = {
    128: "input_voltage_low",
    129: "input_voltage_high",
    130: "adc_1_error",
    131: "adc_2_error",
    132: "battery_disconnected",
    133: "check_failed",
    134: "capacity_limit",
    135: "time_limit",
    136: "internal_temperature_high",
    137: "battery_temperature_limit",
    138: "short_circuit",
    139: "reversed_polarity",
}
_WORKING = (1, 2, 3)

CMD_START = 0x05
CMD_PROGRAM = 0x11
CMD_SET_BASIC = 0x63

# Per battery type, in the order of BATTERY_TYPE_NAME: (min, default, max) in
# mV, as the app lists them.
CHARGE_END_MV = (
    (4000, 4200, 4250), (3400, 3600, 3650), (4100, 4350, 4400), (1470, 1650, 1800),
    (1470, 1650, 1800), (1850, 1900, 1950), (1470, 1650, 1800), (1400, 1650, 1700),
    (2600, 2850, 2900), (3200, 4000, 4150),
)  # fmt: skip
DISCHARGE_END_MV = (
    (2500, 3000, 3650), (2000, 2900, 3150), (2650, 3300, 3750), (500, 1000, 1100),
    (500, 1000, 1100), (1000, 1200, 1300), (500, 900, 1000), (500, 900, 1300),
    (1500, 1800, 2250), (1500, 2000, 3500),
)  # fmt: skip
KEEP_MV = (
    (3980, 4150, 4180), (3380, 3550, 3580), (4080, 4250, 4330), (1300, 1350, 1450),
    (1300, 1350, 1450), (1500, 1600, 1880), (1300, 1350, 1450), (1400, 1450, 1500),
    (2580, 2700, 2830), (3980, 4150, 4180),
)  # fmt: skip
# The storage voltage of the lithium types (liion, lifepo4, liion_hv, lto).
STORAGE_MV = {"liion": (3650, 3800, 4000), "lifepo4": (3150, 3300, 3400),
              "liion_hv": (3750, 3900, 4100), "lto": (2250, 2400, 2600)}  # fmt: skip
# Eneloop models: capacity (mAh), charge, discharge and break-in current (A).
ENELOOP_MAH = (2000, 800, 2500, 900, 1000, 600, 2200, 3200, 6000)
ENELOOP_CHARGE_A = (1.0, 0.4, 1.25, 0.45, 0.5, 0.3, 1.1, 1.6, 3.0)
ENELOOP_DISCHARGE_A = (0.5, 0.2, 0.62, 0.22, 0.25, 0.15, 0.55, 0.8, 1.5)
_NICKEL_TYPES = frozenset({"nimh", "nicd", "eneloop"})

CHARGE_CURRENT_MA = (50, 1000, 3000)
DISCHARGE_CURRENT_MA = (50, 500, 2000)
CAPACITY_MAH = (0, 2000, 50000)
BREAK_IN_CAPACITY_MAH = (100, 2000, 50000)
REST_MINUTES = (0, 0, 240)
CYCLES = (1, 1, 99)
DELTA_PEAK_MV = (0, 3, 20)
TRICKLE_MA = (0, 100, 300)
TEMPERATURE_C = (20, 45, 70)
TIMER_MINUTES = (0, 0, 1440)

# The charger's basic settings (0x61 read, 0x63 write).
DISPLAY_OPTIONS = ("off", "auto", "1_min", "3_min", "5_min", "always_on")
FAN_OPTIONS = ("auto", "off", "on", "20", "25", "30", "35", "40", "45", "50")
INPUT_MV = (10000, 11000, 12000)


def _frame20(command: int, data: bytes = b"") -> bytes:
    body = bytes([START_BYTE, command]) + data
    body = body.ljust(FRAME_SIZE - 1, b"\x00")
    return body + bytes([sum(body) & 0xFF])


def modes_of(battery_type: str) -> tuple[str, ...]:
    """The modes of a battery type, in the order the mode byte indexes them."""
    if battery_type in _LITHIUM_TYPES:
        return _LITHIUM_MODES
    if battery_type in _ZINC_TYPES:
        return _ZINC_MODES
    return _NICKEL_MODES


class Mc3000Client(Mc5000Client):
    """An MC3000: read, stop and curve; no start."""

    MODEL_NAME = "MC3000"
    MODEL_CODE = b""
    supports_settings = True

    def __init__(self, ble_device: object) -> None:
        super().__init__(ble_device)
        self._fahrenheit = False

    @property
    def identified(self) -> bool:
        """Whether the charger answered the info request in the MC3000's frame."""
        return self._device_info.sw_version is not None

    def set_passcode(self, passcode: str) -> None:
        """The MC3000 has no password."""

    @staticmethod
    def _frame(command: int, data: bytes) -> bytes:
        return _frame20(command, data)

    @staticmethod
    def _answers(resp: bytes, command: int) -> bool:
        return len(resp) > 1 and resp[1] == command

    def _notify_handler(self, _characteristic: object, data: bytearray) -> None:
        """Cut the stream into frames: 20 bytes, or 245 for a curve."""
        buffer = self._rx_buffer
        buffer.extend(data)
        while buffer:
            if buffer[0] != START_BYTE:
                start = buffer.find(bytes([START_BYTE]))
                if start < 0:
                    buffer.clear()
                    return
                del buffer[:start]
            size = CURVE_FRAME_SIZE if len(buffer) > 1 and buffer[1] == CMD_CURVE else FRAME_SIZE
            if len(buffer) < size:
                return
            self._notify_queue.put_nowait(bytes(buffer[:size]))
            del buffer[:size]

    # -- connection ----------------------------------------------------

    async def _async_handshake(self) -> None:
        device_info = await self._query_device_info()
        if device_info:
            self._device_info = device_info
        await self._read_basic()

    async def _query_device_info(self) -> DeviceData | None:
        address = getattr(self._device, "address", "") or ""
        try:
            mac = bytes.fromhex(address.replace(":", ""))
        except ValueError:
            mac = b""
        if len(mac) != 6:
            mac = bytes(6)

        # The charger can drop the first command after a connection.
        resp = None
        for _ in range(3):
            resp = await self._request(CMD_INFO, b"\x00" + mac[::-1], timeout=2.0)
            if resp is not None:
                break
        if resp is None or len(resp) < FRAME_SIZE:
            return None

        code = resp[3:9]
        firmware = (resp[14] * 100 + resp[15]) / 100
        hardware = resp[16] * 10 / 100
        return DeviceData(
            name=f"SkyRC {self.MODEL_NAME}",
            address=address,
            manufacturer="SkyRC",
            model=self.MODEL_NAME,
            # The NC2600 gives its model code here; the MC3000 does not.
            model_code=code.decode("ascii") if code.isdigit() else None,
            sw_version=f"{firmware:.2f}",
            hw_version=f"{hardware:.2f}",
        )

    # -- readings ------------------------------------------------------

    def _parse_slot(self, index: int, packet: bytes) -> ChannelData:
        """Decode a 0x55 answer: battery type 3, mode 4, cycles 5, state 6,
        elapsed seconds 7-8, voltage (mV) 9-10, current (mA) 11-12, capacity
        (mAh) 13-14, temperature 15, internal resistance (mOhm) 16-17."""
        if len(packet) < 18:
            return ChannelData(index=index, status="unavailable")

        battery_type = BATTERY_TYPE_NAME.get(packet[3])
        modes = modes_of(battery_type or "")
        mode = modes[packet[4]] if packet[4] < len(modes) else f"unknown_{packet[4]}"
        state = packet[6]
        voltage = int.from_bytes(packet[9:11], "big")
        current = int.from_bytes(packet[11:13], "big")
        resistance = int.from_bytes(packet[16:18], "big")
        temperature = float(packet[15])
        if self._fahrenheit:
            temperature = round((temperature - 32) * 5 / 9, 1)

        if state >= 128:
            return ChannelData(
                index=index,
                status="error",
                chemistry=battery_type,
                voltage=round(voltage / 1000, 3),
                current=0.0,
                error=ERROR_BYTE_NAME.get(state, f"unknown_{state}"),
            )

        working = state in _WORKING
        return ChannelData(
            index=index,
            status=STATUS_BYTE_NAME.get(state, f"unknown_{state}"),
            chemistry=battery_type,
            mode=mode if working else None,
            voltage=round(voltage / 1000, 3),
            current=round(current / 1000, 3) if working else 0.0,
            capacity=int.from_bytes(packet[13:15], "big") if working else None,
            temperature=temperature if working else None,
            # The app shows 0, 1 and 65535 as "not available".
            resistance=None if resistance in (0, 1, 0xFFFF) else float(resistance),
            time=int.from_bytes(packet[7:9], "big") if working else None,
        )

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise BleakError("The charger is not connected")

        channels: list[ChannelData] = []
        for index in range(self.channel_count):
            resp = await self._request(CMD_STATUS, bytes([index]))
            if resp is None:
                channels.append(ChannelData(index=index, status="unavailable"))
            else:
                channels.append(self._parse_slot(index, resp))
        return ChargerState(device=self._device_info, channels=channels)

    async def async_get_voltage_curve(self, channel: int) -> VoltageCurve:
        """Read a slot's logged curve: 120 samples (mV), one every `interval` seconds."""
        resp = await self._request(CMD_CURVE, bytes([channel]))
        if resp is None or len(resp) < CURVE_FRAME_SIZE or resp[2] != channel:
            return VoltageCurve()
        samples = [
            int.from_bytes(resp[i - 1 : i + 1], "big") for i in range(6, CURVE_FRAME_SIZE, 2)
        ]
        return VoltageCurve(
            samples_mv=samples,
            interval_seconds=int.from_bytes(resp[3:5], "big"),
            checksum_ok=None,
        )

    # -- commands ------------------------------------------------------

    def _port_byte(self, channel: int) -> int:
        """A slot is read by its index but stopped by its bitmask."""
        return CHANNEL_BITMASK[channel]

    async def _write_frame(self, frame: bytes) -> None:
        client = self._client
        if client is None:
            raise BleakError("The charger is not connected")
        while not self._notify_queue.empty():
            self._notify_queue.get_nowait()
        await client.write_gatt_char(CHAR_UUID, frame, response=False)

    def _build_program(self, channel: int, **kwargs: Any) -> bytes:
        """The 40 bytes of a program, laid out as the MC3000 app saves them.

        The app's ranges and defaults apply; a program for a type the caller
        did not name is refused, as a lithium default on a nickel cell would
        charge it to 4.2 V.
        """
        chemistry = kwargs.get("chemistry")
        if chemistry not in BATTERY_TYPE_NAME.values():
            raise ValueError(
                f"Slot {channel + 1}: the MC3000 needs a chemistry to start "
                f"({', '.join(BATTERY_TYPE_NAME.values())})"
            )
        chemistry = str(chemistry)
        type_index = next(i for i, name in BATTERY_TYPE_NAME.items() if name == chemistry)
        modes = modes_of(chemistry)
        mode = str(kwargs.get("mode") or "charge")
        if mode not in modes:
            raise ValueError(f"A {chemistry} cell has the modes {', '.join(modes)}, not {mode}")
        mode_index = modes.index(mode)
        break_in = mode == "break_in"
        nickel = chemistry in _NICKEL_TYPES

        eneloop_type = int(kwargs.get("eneloop_type", 0))
        if chemistry == "eneloop" and not 0 <= eneloop_type < len(ENELOOP_MAH):
            raise ValueError(f"eneloop_type must be between 0 and {len(ENELOOP_MAH) - 1}")

        if chemistry == "eneloop":
            capacity = _in_range("capacity", ENELOOP_MAH[eneloop_type], CAPACITY_MAH, "mAh")
        else:
            limits = BREAK_IN_CAPACITY_MAH if break_in else CAPACITY_MAH
            capacity = _in_range("capacity", int(kwargs.get("capacity", limits[1])), limits, "mAh")

        default_charge, default_discharge = CHARGE_CURRENT_MA[1], DISCHARGE_CURRENT_MA[1]
        if chemistry == "eneloop":
            default_charge = int(ENELOOP_CHARGE_A[eneloop_type] * 1000)
            default_discharge = int(ENELOOP_DISCHARGE_A[eneloop_type] * 1000)
        if break_in:
            # The app computes the break-in currents from the capacity.
            if capacity == 0:
                default_charge = default_discharge = 10
            elif capacity <= 10000:
                default_charge, default_discharge = capacity // 10, capacity // 5
            else:
                default_charge = min(capacity // 10, 3000)
                default_discharge = 2000
        charge_current = _in_range(
            "current", int(kwargs.get("current", default_charge)), CHARGE_CURRENT_MA, "mA"
        )
        discharge_current = _in_range(
            "discharge_current",
            int(kwargs.get("discharge_current", default_discharge)),
            DISCHARGE_CURRENT_MA,
            "mA",
        )

        end_limits = CHARGE_END_MV[type_index]
        if mode == "storage" and chemistry in STORAGE_MV:
            end_limits = STORAGE_MV[chemistry]
        target_mv = _in_range(
            "target_mv", int(kwargs.get("target_mv", end_limits[1])), end_limits, "mV"
        )
        cutoff_mv = _in_range(
            "cutoff_mv",
            int(kwargs.get("cutoff_mv", DISCHARGE_END_MV[type_index][1])),
            DISCHARGE_END_MV[type_index],
            "mV",
        )
        min_stop = 10 if chemistry == "naion" else 0
        charge_cutoff_ma = _in_range(
            "charge_cutoff_ma",
            int(kwargs.get("charge_cutoff_ma", min(100, charge_current))),
            (min_stop, 0, charge_current),
            "mA",
        )
        discharge_cutoff_ma = _in_range(
            "discharge_cutoff_ma",
            int(kwargs.get("discharge_cutoff_ma", min(500, discharge_current))),
            (min_stop, 0, discharge_current),
            "mA",
        )
        charge_rest = _in_range(
            "charge_rest_min",
            int(kwargs.get("charge_rest_min", REST_MINUTES[1])),
            REST_MINUTES,
            "min",
        )
        discharge_rest = _in_range(
            "discharge_rest_min",
            int(kwargs.get("discharge_rest_min", REST_MINUTES[1])),
            REST_MINUTES,
            "min",
        )
        cycles = _in_range("cycle_count", int(kwargs.get("cycle_count", CYCLES[1])), CYCLES, "")
        cycle_direction = int(kwargs.get("cycle_direction", 0))
        if not 0 <= cycle_direction <= (1 if break_in else 3):
            raise ValueError("cycle_direction is 0 or 1 for a break-in, 0 to 3 otherwise")
        delta = trickle = 0
        if nickel:
            delta = _in_range(
                "delta_peak_mv",
                int(kwargs.get("delta_peak_mv", DELTA_PEAK_MV[1])),
                DELTA_PEAK_MV,
                "mV",
            )
            trickle = _in_range(
                "trickle_ma", int(kwargs.get("trickle_ma", TRICKLE_MA[1])), TRICKLE_MA, "mA"
            )
            if trickle % 10:
                raise ValueError("trickle_ma goes by 10 mA")
        keep_limits = KEEP_MV[type_index]
        keep_mv = int(kwargs.get("keep_mv", keep_limits[1]))
        if keep_mv:
            keep_mv = _in_range("keep_mv", keep_mv, keep_limits, "mV")
        temperature = int(kwargs.get("temperature_cutoff_c", TEMPERATURE_C[1]))
        if temperature:
            temperature = _in_range("temperature_cutoff_c", temperature, TEMPERATURE_C, "°C")
        timer = 0
        if not break_in:
            timer = _in_range(
                "cutoff_timer_min",
                int(kwargs.get("cutoff_timer_min", TIMER_MINUTES[1])),
                TIMER_MINUTES,
                "min",
            )

        data = bytearray(40)
        data[0], data[1] = START_BYTE, CMD_PROGRAM
        data[2] = CHANNEL_BITMASK[channel]
        data[3], data[4] = type_index, mode_index
        data[5:7] = capacity.to_bytes(2, "big")
        data[7:9] = charge_current.to_bytes(2, "big")
        data[9:11] = discharge_current.to_bytes(2, "big")
        data[11:13] = target_mv.to_bytes(2, "big")
        data[13:15] = cutoff_mv.to_bytes(2, "big")
        data[15:17] = charge_cutoff_ma.to_bytes(2, "big")
        data[17:19] = discharge_cutoff_ma.to_bytes(2, "big")
        data[19] = charge_rest
        data[20] = cycles
        data[21] = cycle_direction
        data[22] = delta
        data[23] = trickle // 10
        data[24:26] = keep_mv.to_bytes(2, "big")
        data[26] = temperature
        data[27:29] = timer.to_bytes(2, "big")
        data[29] = discharge_rest
        data[39] = sum(data[:39]) & 0xFF
        return bytes(data)

    async def _send_program(self, channel: int, payload: bytes) -> None:
        """What the app does to start a slot: stop it, send the two halves of
        the program, then, once the charger answers, start it."""
        mask = CHANNEL_BITMASK[channel]
        await self._request(CMD_STOP, bytes([mask]))
        await asyncio.sleep(0.5)
        await self._write_frame(payload[:20])
        await asyncio.sleep(0.05)
        await self._write_frame(payload[20:])
        try:
            reply = await asyncio.wait_for(self._notify_queue.get(), timeout=5.0)
        except TimeoutError:
            reply = None
        if reply is None or len(reply) < 2 or reply[1] != CMD_PROGRAM:
            raise BleakError(f"MC3000 gave no answer to the program for slot {channel + 1}")
        await self._request(CMD_START, bytes([mask]))

    async def start_channel(self, channel: int, **kwargs: Any) -> None:
        config = {**self._last_config.get(channel, {}), **kwargs}
        payload = self._build_program(channel, **config)
        self._last_config[channel] = config
        await self._send_program(channel, payload)

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: Any) -> None:
        per_channel = per_channel or {}
        programs = []
        for channel in range(self.channel_count):
            config = {
                **self._last_config.get(channel, {}),
                **kwargs,
                **per_channel.get(channel, {}),
            }
            programs.append((channel, config, self._build_program(channel, **config)))
        for channel, config, payload in programs:
            self._last_config[channel] = config
            await self._send_program(channel, payload)

    # -- basic settings ------------------------------------------------

    async def _read_basic(self) -> None:
        resp = await self._request(CMD_BASIC_DATA, timeout=2.0)
        if resp is None or len(resp) < 9:
            return
        self._fahrenheit = resp[2] != 0
        device = self._device_info
        device.beep = bool(resp[3])
        device.display_timeout = (
            DISPLAY_OPTIONS[resp[4]] if resp[4] < len(DISPLAY_OPTIONS) else None
        )
        device.screensaver = bool(resp[5])
        device.cooling_fan = FAN_OPTIONS[resp[6]] if resp[6] < len(FAN_OPTIONS) else None
        device.input_voltage = round(int.from_bytes(resp[7:9], "big") / 1000, 1)

    async def async_set_settings(self, **changes: Any) -> None:
        """Write the basic settings: beep, display, screensaver, cooling_fan,
        input_voltage. The temperature unit is left as it is."""
        device = self._device_info
        values: dict[str, Any] = {
            "beep": device.beep,
            "display_timeout": device.display_timeout,
            "screensaver": device.screensaver,
            "cooling_fan": device.cooling_fan,
            "input_voltage": device.input_voltage,
        }
        unknown = set(changes) - set(values)
        if unknown:
            raise ValueError(f"Unknown setting: {', '.join(sorted(unknown))}")
        values.update(changes)
        if None in values.values():
            raise BleakError("The settings of the charger were not read yet")

        if values["display_timeout"] not in DISPLAY_OPTIONS:
            raise ValueError(f"display_timeout is one of {', '.join(DISPLAY_OPTIONS)}")
        if values["cooling_fan"] not in FAN_OPTIONS:
            raise ValueError(f"cooling_fan is one of {', '.join(FAN_OPTIONS)}")
        input_mv = _in_range(
            "input_voltage", round(float(values["input_voltage"]) * 1000), INPUT_MV, "mV"
        )

        body = bytearray(20)
        body[0], body[1] = START_BYTE, CMD_SET_BASIC
        body[2] = 1 if self._fahrenheit else 0
        body[3] = 1 if values["beep"] else 0
        body[4] = DISPLAY_OPTIONS.index(str(values["display_timeout"]))
        body[5] = 1 if values["screensaver"] else 0
        body[6] = FAN_OPTIONS.index(str(values["cooling_fan"]))
        body[7:9] = input_mv.to_bytes(2, "big")
        body[19] = sum(body[:19]) & 0xFF

        # The charger sometimes drops the command that follows a write:
        # read the settings back and write again if they did not take.
        for _ in range(3):
            await self._request(CMD_SET_BASIC, bytes(body[2:19]))
            await asyncio.sleep(0.5)
            await self._read_basic()
            if (
                self._device_info.beep == bool(body[3])
                and self._device_info.input_voltage == input_mv / 1000
            ):
                return
        raise BleakError("The MC3000 did not take the new settings")


def _in_range(name: str, value: int, limits: tuple[int, int, int], unit: str) -> int:
    low, _default, high = limits
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high} {unit}".rstrip())
    return value
