"""NC3000 driver: monitoring, from what the official SkyRC app reads.

The NC3000 speaks the same protocol as the MC5000 (same framing, passcode,
info request, 0x91 slot status, 0xFE stop, 0x96/0x95 settings, 0x98 curve),
with differences in what the slot status means:

- it has no chemistry byte: the charger takes NiMH and NiCd cells, and the
  program does not carry the chemistry;
- the status byte has its own numbering (7 charging, 8 discharging,
  9 cooling, 10 done) and so has the mode byte;
- the error byte is a flag, not a code.

Starting a slot is not offered yet: the NC3000 program (its modes, defaults
and ranges) was not checked against a charger, and a wrong program could
harm a cell. Stopping a slot, reading it and its curve are the same
commands as on the MC5000.
"""

from __future__ import annotations

from .mc5000_client import Mc5000Client
from .models import ChannelData

NC3000_MODEL_CODE = b"100209"

# Byte 18 of the 0x91 answer, as the app labels it.
STATUS_BYTE_NAME = {
    7: "charging",
    8: "discharging",
    9: "cooling",
    10: "done",
}
# Byte 19.
MODE_BYTE_NAME = {
    1: "charge",
    2: "discharge",
    3: "refresh",
    4: "break_in",
    5: "turbo",
}


class Nc3000Client(Mc5000Client):
    """An NC3000: read, stop, curve and settings; no start yet."""

    MODEL_NAME = "NC3000"
    MODEL_CODE = NC3000_MODEL_CODE

    @staticmethod
    def _parse_channel(index: int, packet: bytes) -> ChannelData:
        """Decode one 0x91 answer, laid out as on the MC5000 without the chemistry."""
        if len(packet) < 18:
            return ChannelData(index=index, status="unavailable")

        current_ma = int.from_bytes(packet[4:6], "big")
        voltage_mv = int.from_bytes(packet[6:8], "big")
        temperature = int.from_bytes(packet[8:10], "big")
        capacity = int.from_bytes(packet[10:12], "big")

        error_flag = 0
        if len(packet) >= 22:
            elapsed = int.from_bytes(packet[12:16], "big")
            resistance = int.from_bytes(packet[16:18], "big")
            status_byte = packet[18]
            mode_value = packet[19]
            error_flag = packet[20]
        else:
            elapsed = int.from_bytes(packet[12:14], "big")
            resistance = int.from_bytes(packet[14:16], "big")
            status_byte = packet[16]
            mode_value = packet[17]
            if len(packet) == 20:
                error_flag = packet[18]

        # Below 5 mV the app considers the slot empty.
        has_cell = voltage_mv >= 5
        running = status_byte >= 7

        if error_flag == 1:
            status = "error"
        elif not running and not has_cell:
            status = "empty"
        elif running:
            status = STATUS_BYTE_NAME.get(status_byte, f"unknown_{status_byte:02x}")
        else:
            # 0 is idle, 1 to 6 are the charger getting ready.
            status = "idle" if status_byte == 0 else "standby"

        error = "error" if error_flag == 1 else None

        if not has_cell and not running:
            return ChannelData(index=index, status=status, voltage=0.0, current=0.0, error=error)

        return ChannelData(
            index=index,
            status=status,
            mode=MODE_BYTE_NAME.get(mode_value, f"unknown_{mode_value}") if running else None,
            voltage=round(voltage_mv / 1000, 3),
            # The NC3000 reports the current in mA, like the MC5000.
            current=round(current_ma / 1000, 3) if running else 0.0,
            capacity=capacity if running else None,
            temperature=temperature if running and temperature else None,
            resistance=resistance if running else None,
            time=elapsed if running else None,
            error=error,
        )

    async def start_channel(self, channel: int, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the NC3000")

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: object) -> None:
        raise ValueError("Starting a slot is not supported yet on the NC3000")
