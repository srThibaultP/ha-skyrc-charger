"""BD380 driver: monitoring, from what the official SkyRC app reads.

The BD380 is a discharger: it draws a set current, voltage, power or
resistance from a battery or a source until a cut-off. The app tells it by
the model code it gives to the info request (600153) and reads it with:

- 0x74, the four digit password, answered 1 when it was right (the same
  command as on the MC5000);
- 0x71, which answers the state, the mode and its set value, the cut-offs
  and, while it works, the readings: voltage, current, power, elapsed time,
  temperatures, energy and capacity.

Starting a discharge (0x70) is not offered: nothing of its program was
checked against a device. Stopping is 0xFE, as on the other models.
"""

from __future__ import annotations

from bleak.exc import BleakError

from .mc5000_client import Mc5000Client
from .models import ChannelData, ChargerState

CMD_STATUS_BD380 = 0x71
BD380_MODEL_CODE = b"600153"

# Byte 4 of the 0x71 answer.
STATUS_BYTE_NAME = {
    1: "working",
    2: "standby",
    3: "done",
    4: "error",
    5: "ready",
}
# Byte 5: what the discharge holds constant.
MODE_BYTE_NAME = {
    0: "constant_current",
    1: "constant_voltage",
    2: "constant_power",
    3: "constant_resistance",
}


class Bd380Client(Mc5000Client):
    """A BD380: read and stop; nothing else."""

    channel_count = 1
    supports_voltage_curves = False
    supports_settings = False
    MODEL_NAME = "BD380"
    MODEL_CODE = BD380_MODEL_CODE

    async def _async_handshake(self) -> None:
        """Identify the device and send it the password; nothing else."""
        device_info = await self._query_device_info()
        if device_info:
            self._device_info = device_info
        await self._unlock()

    @staticmethod
    def _parse_status(packet: bytes) -> ChannelData:
        """Decode a 0x71 answer: state 4, mode 5, readings from byte 15.

        Voltage 15-16 and current 17-18 are in hundredths, power 19-20 in
        tenths of a watt, elapsed time 21-24 in seconds, external and
        internal temperature 25-26, energy 27-28 in tenths of a watt-hour,
        capacity 29-32 in mAh. Byte 15 and 16 carry the error codes in the
        error state.
        """
        if len(packet) < 17:
            return ChannelData(index=0, status="unavailable")

        status_byte = packet[4]
        mode = MODE_BYTE_NAME.get(packet[5], f"unknown_{packet[5]}")

        if status_byte == 4:
            return ChannelData(
                index=0,
                status="error",
                mode=mode,
                voltage=0.0,
                current=0.0,
                error=f"system_{packet[15]}_charge_{packet[16]}",
            )

        status = STATUS_BYTE_NAME.get(status_byte, f"unknown_{status_byte}")
        if status_byte == 5 or len(packet) < 33:
            return ChannelData(index=0, status=status, mode=mode, voltage=0.0, current=0.0)

        return ChannelData(
            index=0,
            status=status,
            mode=mode,
            voltage=round(int.from_bytes(packet[15:17], "big") / 100, 2),
            current=round(int.from_bytes(packet[17:19], "big") / 100, 2),
            power=round(int.from_bytes(packet[19:21], "big") / 10, 1),
            time=int.from_bytes(packet[21:25], "big"),
            temperature=float(packet[26]),
            energy=round(int.from_bytes(packet[27:29], "big") / 10, 1),
            capacity=int.from_bytes(packet[29:33], "big"),
        )

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise BleakError("The device is not connected")

        resp = await self._request(CMD_STATUS_BD380, b"\x00\x00")
        channel = (
            ChannelData(index=0, status="unavailable") if resp is None else self._parse_status(resp)
        )
        return ChargerState(device=self._device_info, channels=[channel])

    async def start_channel(self, channel: int, **kwargs: object) -> None:
        raise ValueError("Starting a discharge is not supported yet on the BD380")

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: object) -> None:
        raise ValueError("Starting a discharge is not supported yet on the BD380")
