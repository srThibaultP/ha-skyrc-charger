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

A slot is started by sending it a program (0x11): slot, mode, then, as the
mode needs them, the capacity (break-in), the charge current, the discharge
current (both in mA) and the number of cycles. The app has no stop command,
and its settings (0x0A/0x0B) are not offered here: one of them, the end of
charge voltage, is clamped with values that depend on the model's variant.
"""

from __future__ import annotations

from typing import Any

from bleak.exc import BleakError

from .mc3000_client import CMD_PROGRAM, Mc3000Client, _in_range
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

# (min, default, max), as the app's program screen has them.
CAPACITY_MAH = (500, 500, 3500)
CHARGE_CURRENT_MA = (200, 250, 2600)
CYCLES = (1, 1, 12)
# The "145" variant (model code 100145) discharges up to 2 A, the other 1 A.
DISCHARGE_CURRENT_MA_113 = (100, 100, 1000)
DISCHARGE_CURRENT_MA_145 = (100, 100, 2000)
VARIANT_145 = "100145"


class Nc2600Client(Mc3000Client):
    """An NC2600: read only."""

    MODEL_NAME = "NC2600"
    supports_voltage_curves = False
    supports_settings = False

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

    def _build_program(self, channel: int, **kwargs: Any) -> bytes:
        """The 17 data bytes of a 0x11 program, laid out as the app does."""
        mode = str(kwargs.get("mode") or "charge")
        if mode not in MODE_BYTE_NAME:
            raise ValueError(f"The NC2600 has the modes {', '.join(MODE_BYTE_NAME)}, not {mode}")
        mode_index = MODE_BYTE_NAME.index(mode)

        discharge_limits = (
            DISCHARGE_CURRENT_MA_145
            if self._device_info.model_code == VARIANT_145
            else DISCHARGE_CURRENT_MA_113
        )
        data = bytearray(17)
        data[0], data[1] = channel, mode_index
        if mode == "break_in":
            capacity = _in_range(
                "capacity", int(kwargs.get("capacity", CAPACITY_MAH[1])), CAPACITY_MAH, "mAh"
            )
            data[2:4] = capacity.to_bytes(2, "big")
        if mode not in ("break_in", "discharge"):
            charge = _in_range(
                "current", int(kwargs.get("current", CHARGE_CURRENT_MA[1])), CHARGE_CURRENT_MA, "mA"
            )
            data[4:6] = charge.to_bytes(2, "big")
        if mode not in ("break_in", "charge"):
            discharge = _in_range(
                "discharge_current",
                int(kwargs.get("discharge_current", discharge_limits[1])),
                discharge_limits,
                "mA",
            )
            data[6:8] = discharge.to_bytes(2, "big")
        if mode == "cycle":
            data[8] = _in_range(
                "cycle_count", int(kwargs.get("cycle_count", CYCLES[1])), CYCLES, ""
            )
        return bytes(data)

    async def _send_program(self, channel: int, payload: bytes) -> None:
        resp = await self._request(CMD_PROGRAM, payload)
        if resp is None:
            raise BleakError(f"NC2600 gave no answer to the program for slot {channel + 1}")

    async def async_get_voltage_curve(self, channel: int):
        raise NotImplementedError("The NC2600 curve is not read yet")

    async def stop_channel(self, channel: int) -> None:
        raise ValueError("Stopping a slot is not supported on the NC2600")

    async def stop_all(self) -> None:
        raise ValueError("Stopping a slot is not supported on the NC2600")
