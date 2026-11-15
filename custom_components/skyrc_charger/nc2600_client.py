"""NC2600 driver: monitoring, from what the official NC2600 app reads.

The NC2600 is a four slot nickel charger. It speaks the MC3000's protocol
(fixed 20-byte frames, a slot asked for by its index, no password), with its
own layout of the 0x55 answer:

- slot 2, mode and cell type in byte 3 (the mode is the low six bits),
  state 4, capacity (mAh) 5-6, voltage (mV) 7-8, current (mA) 9-10, elapsed
  time (s) 11-12, temperature 13, number of cycles 14, internal resistance
  (mOhm) 15-16;
- the info request (0x57) is sent without the Bluetooth address and answers
  a 16-byte identifier, whose first six characters are the model code.

The app has no stop command: it stops a slot by sending a program. Neither
that, nor starting, nor the settings (0x0A/0x0B) is offered here.
"""

from __future__ import annotations

from .mc3000_client import Mc3000Client
from .models import ChannelData

# Byte 4: what the slot is doing.
STATUS_BYTE_NAME = {
    0: "standby",
    1: "charging",
    2: "discharging",
    3: "resting",
    4: "done",
    5: "error",
}
# The mode (byte 3, low six bits), as the app lists them.
MODE_BYTE_NAME = ("charge", "refresh", "break_in", "discharge", "cycle")
_WORKING = (1, 2, 3)


class Nc2600Client(Mc3000Client):
    """An NC2600: read only."""

    MODEL_NAME = "NC2600"
    supports_voltage_curves = False

    async def _async_handshake(self) -> None:
        device_info = await self._query_device_info()
        if device_info:
            self._device_info = device_info

    def _parse_slot(self, index: int, packet: bytes) -> ChannelData:
        if len(packet) < 17:
            return ChannelData(index=index, status="unavailable")

        mode_value = packet[3] & 0x3F
        state = packet[4]
        voltage = int.from_bytes(packet[7:9], "big")
        status = STATUS_BYTE_NAME.get(state, f"unknown_{state}")

        # The app shows a slot with no voltage as empty.
        if voltage == 0 and state not in _WORKING:
            return ChannelData(
                index=index, status="empty" if state == 0 else status, voltage=0.0, current=0.0
            )

        working = state in _WORKING
        resistance = int.from_bytes(packet[15:17], "big")
        return ChannelData(
            index=index,
            status=status,
            mode=(
                (
                    MODE_BYTE_NAME[mode_value]
                    if mode_value < len(MODE_BYTE_NAME)
                    else f"unknown_{mode_value}"
                )
                if working
                else None
            ),
            voltage=round(voltage / 1000, 3),
            current=round(int.from_bytes(packet[9:11], "big") / 1000, 3) if working else 0.0,
            capacity=int.from_bytes(packet[5:7], "big") if working else None,
            temperature=float(packet[13]) if working else None,
            resistance=None if resistance in (0, 1, 0xFFFF) else float(resistance),
            time=int.from_bytes(packet[11:13], "big") if working else None,
            error="device_error" if state == 5 else None,
        )

    async def async_get_voltage_curve(self, channel: int):
        raise NotImplementedError("The NC2600 curve is not read yet")

    async def stop_channel(self, channel: int) -> None:
        raise ValueError("Stopping a slot is not supported on the NC2600")

    async def stop_all(self) -> None:
        raise ValueError("Stopping a slot is not supported on the NC2600")
