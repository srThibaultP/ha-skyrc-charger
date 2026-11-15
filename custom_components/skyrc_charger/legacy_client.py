"""Driver of the older SkyRC chargers (Q200, D200, D100, B6...): monitoring.

The SkyRC app tells these chargers by the model code they give in answer to
the info request, and reads them differently from the MC5000:

- every channel is asked for by its bitmask with two commands: 0x5F, which
  carries the four digit password and answers the channel's type and whether
  the password was right, and 0x55, which answers the channel's state and
  what it is doing;
- the number of channels, and whether the password is enabled, come from the
  list of models the app downloads from SkyRC's server, not from the
  charger. They are copied here from that list.

What is offered is monitoring and stop (0xFE with the channel's bitmask, as
on the MC5000). Starting a channel is not: it needs a complete program and
nothing of it was checked against a charger.
"""

from __future__ import annotations

from bleak.exc import BleakError

from .const import DEFAULT_LEGACY_PASSWORD, LEGACY_BY_KEY
from .mc5000_client import CHANNEL_BITMASK, Mc5000Client
from .models import ChannelData, ChargerState

CMD_CHANNEL_INFO = 0x5F
CMD_CHANNEL_STATUS = 0x55

# Byte 4 of the 0x55 answer.
STATE_IDLE = (0, 2, 6)  # the app treats these as "not working"
STATE_ACTIVE = (1, 3)
STATE_ERROR = (4, 5)
STATE_DC_OUTPUT = 7


def _password_bytes(password: str) -> bytes:
    """One byte per digit, as 0x5F wants them."""
    if len(password) != 4 or not password.isascii() or not password.isdigit():
        raise ValueError("The password is four digits")
    return bytes(int(digit) for digit in password)


class LegacyClient(Mc5000Client):
    """An older charger: read and stop; nothing else."""

    supports_voltage_curves = False
    supports_settings = False
    #: Set by make_legacy_client() for each model.
    _password_enabled = True

    def __init__(self, ble_device: object) -> None:
        super().__init__(ble_device)
        self._passcode = DEFAULT_LEGACY_PASSWORD

    def set_passcode(self, passcode: str) -> None:
        _password_bytes(passcode)  # validate early
        self._passcode = passcode

    async def _async_handshake(self) -> None:
        """Identify the charger, then unlock each channel with the password."""
        device_info = await self._query_device_info()
        if device_info:
            self._device_info = device_info

        accepted = True
        for index in range(self.channel_count):
            resp = await self._request(
                CMD_CHANNEL_INFO,
                bytes([CHANNEL_BITMASK[index]]) + _password_bytes(self._passcode),
            )
            if resp is None or len(resp) < 13:
                continue
            if self._password_enabled and resp[12] != 1:
                accepted = False

        self.unlocked = accepted

    @staticmethod
    def _parse_channel(index: int, packet: bytes) -> ChannelData:
        """Decode a 0x55 answer: state 4, then, while a channel works,
        capacity (mAh) 5-6, elapsed (s) 7-8, voltage (mV) 9-10, current (mA)
        11-12, external and internal temperature 13-14, resistance (mOhm)
        15-16. An error state carries the error codes in 5 and 6."""
        if len(packet) < 5:
            return ChannelData(index=index, status="unavailable")

        state = packet[4]

        if state in STATE_ERROR:
            sys_error = packet[5] if len(packet) > 5 else 0
            charge_error = packet[6] if len(packet) > 6 else 0
            return ChannelData(
                index=index,
                status="error",
                voltage=0.0,
                current=0.0,
                error=f"system_{sys_error}_charge_{charge_error}",
            )

        if state in STATE_ACTIVE and len(packet) >= 17:
            voltage = int.from_bytes(packet[9:11], "big")
            current = int.from_bytes(packet[11:13], "big")
            # The app shows 0xFFFF as "no reading".
            return ChannelData(
                index=index,
                status="active",
                voltage=0.0 if voltage == 0xFFFF else round(voltage / 1000, 3),
                current=0.0 if current == 0xFFFF else round(current / 1000, 3),
                capacity=int.from_bytes(packet[5:7], "big"),
                time=int.from_bytes(packet[7:9], "big"),
                temperature=float(packet[14]),
                resistance=int.from_bytes(packet[15:17], "big"),
            )

        if state == STATE_DC_OUTPUT and len(packet) > 15 and packet[5] == 0:
            return ChannelData(
                index=index,
                status="dc_output",
                voltage=round(int.from_bytes(packet[10:12], "big") / 1000, 3),
                current=round(int.from_bytes(packet[12:14], "big") / 1000, 3),
            )

        status = "idle" if state in STATE_IDLE else f"unknown_{state}"
        return ChannelData(index=index, status=status, voltage=0.0, current=0.0)

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise BleakError("The charger is not connected")

        channels: list[ChannelData] = []
        for index in range(self.channel_count):
            resp = await self._request(CMD_CHANNEL_STATUS, bytes([CHANNEL_BITMASK[index]]))
            if resp is None:
                channels.append(ChannelData(index=index, status="unavailable"))
            else:
                channels.append(self._parse_channel(index, resp))
        return ChargerState(device=self._device_info, channels=channels)

    async def start_channel(self, channel: int, **kwargs: object) -> None:
        raise ValueError(f"Starting a channel is not supported yet on the {self.MODEL_NAME}")

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: object) -> None:
        raise ValueError(f"Starting a channel is not supported yet on the {self.MODEL_NAME}")


def make_legacy_client(model: str) -> type[LegacyClient]:
    """The driver of one model of the list: its name, code and channels."""
    details = LEGACY_BY_KEY[model]
    return type(
        f"Legacy{details.key.title().replace('_', '')}Client",
        (LegacyClient,),
        {
            "MODEL_NAME": details.name,
            "MODEL_CODE": details.code.encode("ascii"),
            "channel_count": details.channels,
            "_password_enabled": details.password,
        },
    )
