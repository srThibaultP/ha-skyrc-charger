"""Common data model of the charger drivers.

Both drivers expose the same shape of data to the coordinator/entities so
that sensors, buttons, etc. don't need to know which charger they're
talking to.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChannelData:
    """Normalized state of a single charging slot."""

    index: int  # 0-based
    status: str | None = None  # idle / charging / discharging / done / paused / ...
    chemistry: str | None = None  # liion, nimh, ... or None if unknown/empty
    mode: str | None = None  # charge / storage / discharge / cycle / refresh / break_in
    voltage: float | None = None  # volts
    current: float | None = None  # amps
    capacity: int | None = None  # mAh
    temperature: float | None = None  # celsius
    resistance: float | None = None  # milliohms
    time: int | None = None  # seconds elapsed
    led: str | None = None
    error: str | None = None  # MC5000 fault reported for the slot, if any


@dataclass
class DeviceData:
    """Normalized device-level info."""

    name: str = "SkyRC Charger"
    address: str = ""
    manufacturer: str = "SkyRC"
    model: str = ""
    hw_version: str | None = None
    sw_version: str | None = None
    input_voltage: float | None = None
    # MC5000-only settings.
    beep_volume: str | None = None  # off / low / medium / high
    notification_volume: str | None = None
    end_notification: str | None = None  # once / repeat
    backlight: str | None = None  # low / medium / high
    warning_tone: bool | None = None
    sleep_timer_minutes: int | None = None  # 0 = never


@dataclass
class ChargerState:
    device: DeviceData = field(default_factory=DeviceData)
    channels: list[ChannelData] = field(default_factory=list)


@dataclass
class VoltageCurve:
    """One slot's logged voltage curve, as read back from the charger."""

    samples_mv: list[int] = field(default_factory=list)
    interval_seconds: int | None = None
    checksum_ok: bool | None = None


class ChargerClient:
    """Common interface the drivers implement.

    The coordinator and entity platforms only talk to this interface, so
    supporting another charger is a matter of adding a driver and
    instantiating it in coordinator.py.
    """

    channel_count: int = 0

    #: Whether the charger can replay a logged voltage curve for a slot.
    supports_voltage_curves: bool = False
    #: Whether the charger's own settings (beeps, backlight, ...) can be written.
    supports_settings: bool = False

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError

    def set_ble_device(self, ble_device) -> None:
        """Point the client at a freshly discovered BLEDevice.

        Home Assistant hands out a new BLEDevice whenever the charger is
        re-advertised (and, with Bluetooth proxies, the connection path can
        change between adverts). Reusing a stale one is the usual cause of
        reconnect loops, so the coordinator refreshes it before every
        reconnect.
        """
        raise NotImplementedError

    def set_passcode(self, passcode: str) -> None:
        """Set the passcode to unlock the charger with, where it has one."""

    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def async_update(self) -> ChargerState:
        """Poll the charger and return a fresh ChargerState."""
        raise NotImplementedError

    async def start_channel(self, channel: int, **kwargs) -> None:
        """Start charging/discharging on a channel.

        The charger has no "use the last program" command: a full program
        is written first, which starts the slot. kwargs may carry
        chemistry/mode/current/capacity overrides; the app's defaults for
        the chemistry are used otherwise.
        """
        raise NotImplementedError

    async def stop_channel(self, channel: int) -> None:
        raise NotImplementedError

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs) -> None:
        """Start every channel.

        `per_channel` carries overrides that differ from slot to slot (each
        slot's declared chemistry, typically); `kwargs` applies to all of
        them. Defaults to starting the channels one by one.
        """
        per_channel = per_channel or {}
        for channel in range(self.channel_count):
            await self.start_channel(channel, **{**kwargs, **per_channel.get(channel, {})})

    async def stop_all(self) -> None:
        raise NotImplementedError

    async def async_get_voltage_curve(self, channel: int) -> VoltageCurve:
        """Read back the logged voltage curve for one slot."""
        raise NotImplementedError

    async def async_set_settings(self, **changes) -> None:
        """Change the charger's own settings (see the driver for the keys)."""
        raise NotImplementedError
