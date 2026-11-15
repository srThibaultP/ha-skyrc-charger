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

from bleak.exc import BleakError

from .mc5000_client import Mc5000Client
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
    supports_settings = False

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
        resp = await self._request(CMD_BASIC_DATA, timeout=2.0)
        if resp is not None and len(resp) > 2:
            self._fahrenheit = resp[2] != 0

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
        """A slot is named by its index."""
        return channel

    async def start_channel(self, channel: int, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the MC3000")

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the MC3000")
