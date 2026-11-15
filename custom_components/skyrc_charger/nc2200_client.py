"""NC2200 driver: monitoring, from what the official SkyRC app reads.

The NC2200 is read with the same 0x91 request as the MC5000, with these
differences in the app:

- a slot is asked for by its index (0 to 3), not a bitmask;
- the answer is 20 bytes: 16-bit elapsed time, resistance in bytes 14-15,
  then the status (byte 16), the mode (17) and an error flag (18);
- the status is numbered 7 charging, 8 discharging, 9 cooling, 10 done, and
  the mode 1 charge, 2 discharge, 3 refresh, 4 break-in, 5 turbo;
- it takes NiMH cells only and has no chemistry byte.

The app sets its system settings and its programs with other, older
commands, and does not read a curve from it, so neither is offered here, nor
is starting a slot: nothing of it was checked against a charger. Stopping a
slot is the same command as on the MC5000.
"""

from __future__ import annotations

from bleak.exc import BleakError

from .mc5000_client import CMD_STATUS, Mc5000Client
from .models import ChannelData, ChargerState

NC2200_MODEL_CODE = b"100181"
SLOT_COUNT = 4

# Byte 16 of the 0x91 answer.
STATUS_BYTE_NAME = {
    7: "charging",
    8: "discharging",
    9: "cooling",
    10: "done",
}
# Byte 17.
MODE_BYTE_NAME = {
    1: "charge",
    2: "discharge",
    3: "refresh",
    4: "break_in",
    5: "turbo",
}


class Nc2200Client(Mc5000Client):
    """An NC2200: read and stop; no start, settings or curve."""

    supports_voltage_curves = False
    supports_settings = False
    MODEL_NAME = "NC2200"
    MODEL_CODE = NC2200_MODEL_CODE

    @staticmethod
    def _parse_channel(index: int, packet: bytes) -> ChannelData:
        """Decode one 20-byte 0x91 answer: current (mA) 4-5, voltage (mV) 6-7,
        temperature 8-9, capacity (mAh) 10-11, elapsed (s) 12-13, resistance
        (mOhm) 14-15, status 16, mode 17, error flag 18."""
        if len(packet) < 18:
            return ChannelData(index=index, status="unavailable")

        current_ma = int.from_bytes(packet[4:6], "big")
        voltage_mv = int.from_bytes(packet[6:8], "big")
        temperature = int.from_bytes(packet[8:10], "big")
        capacity = int.from_bytes(packet[10:12], "big")
        elapsed = int.from_bytes(packet[12:14], "big")
        resistance = int.from_bytes(packet[14:16], "big")
        status_byte = packet[16]
        mode_value = packet[17]
        error_flag = packet[18] if len(packet) == 20 else 0

        # Below 5 mV the app considers the slot empty.
        has_cell = voltage_mv >= 5
        running = status_byte >= 7
        error = "error" if error_flag == 1 else None

        if error is not None:
            status = "error"
        elif not running and not has_cell:
            status = "empty"
        elif running:
            status = STATUS_BYTE_NAME.get(status_byte, f"unknown_{status_byte:02x}")
        else:
            # 0 is idle, 1 to 6 are the charger getting ready.
            status = "idle" if status_byte == 0 else "standby"

        if not has_cell and not running:
            return ChannelData(index=index, status=status, voltage=0.0, current=0.0, error=error)

        return ChannelData(
            index=index,
            status=status,
            mode=MODE_BYTE_NAME.get(mode_value, f"unknown_{mode_value}") if running else None,
            voltage=round(voltage_mv / 1000, 3),
            current=round(current_ma / 1000, 3) if running else 0.0,
            capacity=capacity if running else None,
            temperature=temperature if running and temperature else None,
            resistance=resistance if running else None,
            time=elapsed if running else None,
            error=error,
        )

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise BleakError("The charger is not connected")

        channels: list[ChannelData] = []
        for index in range(SLOT_COUNT):
            resp = await self._request(CMD_STATUS, bytes([index]))
            if resp is None or len(resp) < 4:
                channels.append(ChannelData(index=index, status="unavailable"))
            else:
                channels.append(self._parse_channel(index, resp))
        return ChargerState(device=self._device_info, channels=channels)

    async def start_channel(self, channel: int, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the NC2200")

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the NC2200")
