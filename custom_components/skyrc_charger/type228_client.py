"""Type 228 driver: monitoring, from what the official SkyRC app reads.

The app knows this charger only by its model code (100228) and drives it
with the same protocol as the MC5000 and NC3000 (framing, passcode, info,
stop, settings, curve), except that:

- it has eight slots, named by their number (1 to 8) in a command, not by a
  bitmask;
- one request (0x9A) returns all eight slots: five bytes (the fifth is the
  charger's own mode), then 17 bytes per slot, then the checksum, 142 bytes
  in all;
- a slot reports a status, a mode and an error code the way the MC5000 does,
  with a mode numbering of its own and no chemistry.

Starting a slot is not offered yet: its program was not checked against a
charger, and a wrong program could harm a cell.
"""

from __future__ import annotations

from bleak.exc import BleakError

from .mc5000_client import CMD_STATUS_228, ERROR_BYTE_NAME, STATUS_BYTE_NAME, Mc5000Client
from .models import ChannelData, ChargerState

TYPE228_MODEL_CODE = b"100228"
SLOT_COUNT = 8
SLOT_BLOCK = 17
FRAME_LENGTH = 5 + SLOT_COUNT * SLOT_BLOCK + 1

# Byte 15 of a slot block.
MODE_BYTE_NAME = {
    0: "charge",
    1: "discharge",
    2: "break_in",
    3: "cycle",
    4: "max_boost",
    5: "turbo",
    6: "three_stage_charge",
}


class Type228Client(Mc5000Client):
    """The eight-slot type 228: read, stop, curve and settings; no start yet."""

    channel_count = SLOT_COUNT
    MODEL_NAME = "Type 228"
    MODEL_CODE = TYPE228_MODEL_CODE

    def _port_byte(self, channel: int) -> int:
        return channel + 1

    @staticmethod
    def _parse_slot(index: int, block: bytes, device_mode: int = 0) -> ChannelData:
        """Decode the 17 bytes of one slot.

        0-1 current (mA)  2-3 voltage (mV)  4-5 temperature  6-7 capacity (mAh)
        8-11 elapsed (s)  12-13 resistance (mOhm)  14 status  15 mode  16 error

        The charger's own mode (byte 4 of the answer) is 1 when it does not
        measure the internal resistance; the app shows "--" then.
        """
        current_ma = int.from_bytes(block[0:2], "big")
        voltage_mv = int.from_bytes(block[2:4], "big")
        temperature = int.from_bytes(block[4:6], "big")
        capacity = int.from_bytes(block[6:8], "big")
        elapsed = int.from_bytes(block[8:12], "big")
        resistance = int.from_bytes(block[12:14], "big")
        status_byte = block[14]
        mode_value = block[15]
        error_byte = block[16]

        # Below 5 mV the app considers the slot empty.
        has_cell = voltage_mv >= 5
        running = status_byte >= 2
        error = ERROR_BYTE_NAME.get(error_byte, f"unknown_{error_byte}") if error_byte else None

        if error is not None:
            status = "error"
        elif not running and not has_cell:
            status = "empty"
        else:
            status = STATUS_BYTE_NAME.get(status_byte, f"unknown_{status_byte:02x}")

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
            resistance=resistance if running and device_mode != 1 else None,
            time=elapsed if running else None,
            error=error,
        )

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise BleakError("The charger is not connected")

        resp = await self._request(CMD_STATUS_228, b"\x00")
        if resp is None or len(resp) != FRAME_LENGTH:
            channels = [
                ChannelData(index=index, status="unavailable") for index in range(SLOT_COUNT)
            ]
        else:
            channels = [
                self._parse_slot(
                    index, resp[5 + index * SLOT_BLOCK : 5 + (index + 1) * SLOT_BLOCK], resp[4]
                )
                for index in range(SLOT_COUNT)
            ]
        return ChargerState(device=self._device_info, channels=channels)

    async def start_channel(self, channel: int, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the type 228")

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the type 228")
