"""PSU650 driver: monitoring, from what the official SkyRC app reads.

The PSU650 is a power supply with a DC output and three USB-C ports. It has
its own protocol, and the app does not tell it by the model code but by its
Bluetooth name, which contains "PSU650".

A frame is the usual one with one more byte after the command, an id that is
always 0, ahead of the data: 0x0F, length, command, 0, data, checksum, with
the checksum over the command and what follows. So a command's data is sent
as `b"\x00" + data`.

- 0xB0: the realtime readings: total power, error code, total current, then
  the DC output and each USB-C port (enabled, power, voltage, current);
- 0xB1: what each USB-C port negotiated (the fast-charge protocol);
- 0x74: the password, as on the other models.

Switching a port or the DC output on and off (0xB4, 0xB3) is not offered
yet: nothing of it was checked against a device.
"""

from __future__ import annotations

from bleak.exc import BleakError

from .mc5000_client import Mc5000Client
from .models import ChannelData, ChargerState

CMD_REALTIME = 0xB0
CMD_PD_DETAIL = 0xB1
PSU650_MODEL_CODE = b"200031"
PSU650_NAME = "PSU650"

# The fast-charge protocols a USB-C port reports, by index.
PD_PROTOCOLS = (
    "none", "qc2", "qc3", "qc3_plus", "sfcp", "afc", "fcp", "scp", "vooc1", "vooc4",
    "svooc", "tfcp", "ufcs", "pe1", "pe2", "pd_5v", "pd_hv", "pd_avs", "pd_pps",
    "pd_epr", "pd_epr_avs",
)  # fmt: skip
DC_MODES = {0: "constant_current", 1: "constant_voltage"}

# Where each port starts in the realtime data, and how long its block is.
PORT_OFFSETS = (6, 14, 21, 28)  # DC, C1, C2, C3
DATA_OFFSET = 4
REALTIME_LENGTH = DATA_OFFSET + 35


def _u16(frame: bytes, offset: int) -> int:
    return int.from_bytes(frame[offset : offset + 2], "big")


class Psu650Client(Mc5000Client):
    """A PSU650: read only."""

    channel_count = 4  # the DC output, then USB-C 1, 2 and 3
    supports_voltage_curves = False
    supports_settings = False
    MODEL_NAME = "PSU650"
    MODEL_CODE = PSU650_MODEL_CODE

    async def _async_handshake(self) -> None:
        """Ask who it is (three variants of the request, as the app does),
        then send the password."""
        for data in (bytes(17), b"\x00"):
            if await self._request(0x57, data, timeout=2.0) is not None:
                break
        await self._unlock()

    @staticmethod
    def _parse_realtime(frame: bytes) -> tuple[list[ChannelData], float, float, int]:
        """Decode a 0xB0 answer into the four ports, the total power (W),
        the total current (A) and the error code."""
        if len(frame) < REALTIME_LENGTH:
            return (
                [ChannelData(index=i, status="unavailable") for i in range(4)],
                0.0,
                0.0,
                0,
            )

        total_power = _u16(frame, DATA_OFFSET) / 10
        error_code = frame[DATA_OFFSET + 2]
        total_current = _u16(frame, DATA_OFFSET + 4) / 10

        ports = []
        for index, start in enumerate(PORT_OFFSETS):
            base = DATA_OFFSET + start
            enabled = frame[base] == 1
            ports.append(
                ChannelData(
                    index=index,
                    status="on" if enabled else "off",
                    power=_u16(frame, base + 1) / 10,
                    voltage=_u16(frame, base + 3) / 10,
                    current=_u16(frame, base + 5) / 10,
                    # The DC output also says whether it holds the current or
                    # the voltage constant.
                    mode=DC_MODES.get(frame[base + 7], f"unknown_{frame[base + 7]}")
                    if index == 0
                    else None,
                )
            )
        return ports, total_power, total_current, error_code

    @staticmethod
    def _apply_pd_detail(ports: list[ChannelData], frame: bytes | None) -> None:
        """Add the protocol each USB-C port negotiated, from a 0xB1 answer.

        Per port, ten bytes: enabled, protocol, maximum power (W), power,
        voltage and current in tenths.
        """
        if frame is None or len(frame) < DATA_OFFSET + 30:
            return
        for index in (1, 2, 3):
            base = DATA_OFFSET + (index - 1) * 10
            protocol = frame[base + 1]
            ports[index].mode = (
                PD_PROTOCOLS[protocol] if protocol < len(PD_PROTOCOLS) else f"unknown_{protocol}"
            )

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise BleakError("The power supply is not connected")

        realtime = await self._request(CMD_REALTIME, b"\x00\x00")
        if realtime is None:
            channels = [ChannelData(index=i, status="unavailable") for i in range(4)]
            return ChargerState(device=self._device_info, channels=channels)

        ports, total_power, total_current, error_code = self._parse_realtime(realtime)
        self._apply_pd_detail(ports, await self._request(CMD_PD_DETAIL, b"\x00\x00"))

        device = self._device_info
        device.total_power = total_power
        device.total_current = total_current
        device.error = f"error_{error_code}" if error_code else None
        return ChargerState(device=device, channels=ports)

    async def start_channel(self, channel: int, **kwargs: object) -> None:
        raise ValueError("A PSU650 has nothing to start")

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: object) -> None:
        raise ValueError("A PSU650 has nothing to start")

    async def stop_channel(self, channel: int) -> None:
        raise ValueError("A PSU650 has nothing to stop")

    async def stop_all(self) -> None:
        raise ValueError("A PSU650 has nothing to stop")
