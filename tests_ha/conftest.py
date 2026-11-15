"""Fixtures: a fake charger on a fake Bluetooth stack, inside a real Home Assistant."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

from bleak.backends.device import BLEDevice
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skyrc_charger.const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_NAME,
    DOMAIN,
    MODEL_MC5000,
    OPTION_PASSCODE,
)
from custom_components.skyrc_charger.models import (
    ChannelData,
    ChargerClient,
    ChargerState,
    DeviceData,
    VoltageCurve,
)
from homeassistant.core import HomeAssistant

pytest_plugins = "pytest_homeassistant_custom_component"

ADDRESS = "DC:23:52:88:36:E2"


@pytest.fixture(autouse=True)
def _auto_enable(enable_custom_integrations: None, mock_bluetooth: None) -> None:
    """Load the custom integration, and a Bluetooth stack it can depend on."""


class FakeClient(ChargerClient):
    """A charger that answers, and remembers what it was asked."""

    channel_count = 4
    supports_voltage_curves = True
    supports_settings = True

    def __init__(self, device: Any = None) -> None:
        self.connected = False
        self.unlocked: bool | None = True
        self.model_code: str | None = "100214"
        self.passcode = "1234"
        self.fail: Exception | None = None
        self.calls: list[tuple[str, Any]] = []
        self.channels = [
            ChannelData(index=i, status="empty", voltage=0.0, current=0.0) for i in range(4)
        ]
        self.device = DeviceData(
            name="SkyRC MC5000",
            model="MC5000",
            sw_version="1.83",
            hw_version="1",
            beep_volume="off",
            notification_volume="low",
            end_notification="once",
            backlight="low",
            warning_tone=True,
            sleep_timer_minutes=0,
        )

    @property
    def is_connected(self) -> bool:
        return self.connected

    def set_ble_device(self, ble_device: Any) -> None:
        self.calls.append(("set_ble_device", ble_device))

    def set_passcode(self, passcode: str) -> None:
        self.passcode = passcode

    async def connect(self) -> None:
        if self.fail:
            raise self.fail
        self.connected = True
        self.calls.append(("connect", None))

    async def disconnect(self) -> None:
        self.connected = False
        self.calls.append(("disconnect", None))

    async def async_update(self) -> ChargerState:
        if self.fail:
            raise self.fail
        return ChargerState(device=self.device, channels=list(self.channels))

    async def start_channel(self, channel: int, **kwargs: Any) -> None:
        self.calls.append(("start_channel", (channel, kwargs)))

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs: Any) -> None:
        self.calls.append(("start_all", per_channel))

    async def stop_channel(self, channel: int) -> None:
        self.calls.append(("stop_channel", channel))

    async def stop_all(self) -> None:
        self.calls.append(("stop_all", None))

    async def async_set_settings(self, **changes: Any) -> None:
        self.calls.append(("set_settings", changes))
        for key, value in changes.items():
            setattr(self.device, key, value)

    async def async_get_voltage_curve(self, channel: int) -> VoltageCurve:
        self.calls.append(("curve", channel))
        return VoltageCurve(samples_mv=[1240, 1241, 1250, 0], interval_seconds=8, checksum_ok=True)

    def called(self, name: str) -> list[Any]:
        return [arg for call, arg in self.calls if call == name]


@pytest.fixture
def client() -> Generator[FakeClient]:
    fake = FakeClient()
    with (
        patch.dict(
            "custom_components.skyrc_charger.coordinator.CLIENTS",
            {MODEL_MC5000: lambda _: fake, "nc3000": lambda _: fake, "nc2200": lambda _: fake},
        ),
        patch("custom_components.skyrc_charger.config_flow.Mc5000Client", lambda _: fake),
    ):
        yield fake


@pytest.fixture
def ble_device() -> Generator[BLEDevice]:
    device = BLEDevice(ADDRESS, "#Charger-E236", {})
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=device
        ),
        patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None),
    ):
        yield device


def make_entry(
    *, pref_disable_polling: bool = False, model: str = MODEL_MC5000, **options: Any
) -> MockConfigEntry:
    title = f"SkyRC {model.upper()}"
    return MockConfigEntry(
        pref_disable_polling=pref_disable_polling,
        domain=DOMAIN,
        title=title,
        unique_id=f"{model}-{ADDRESS}",
        data={CONF_ADDRESS: ADDRESS, CONF_MODEL: model, CONF_NAME: title},
        options={OPTION_PASSCODE: "1234", **options},
    )


@pytest.fixture
async def entry(hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice) -> MockConfigEntry:
    """A charger that is set up and answering."""
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
