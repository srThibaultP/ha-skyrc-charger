"""A type 228: eight slots, monitored and stopped, not started yet."""

from __future__ import annotations

from bleak.backends.device import BLEDevice
import pytest

from custom_components.skyrc_charger.models import ChannelData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeClient, make_entry


class Fake228(FakeClient):
    channel_count = 8

    def __init__(self, device=None) -> None:
        super().__init__(device)
        self.model_code = "100228"
        self.channels = [
            ChannelData(index=i, status="empty", voltage=0.0, current=0.0) for i in range(8)
        ]


async def _setup(hass: HomeAssistant, client: Fake228) -> None:
    from unittest.mock import patch

    from custom_components.skyrc_charger.coordinator import CLIENTS

    entry = make_entry(model="type228")
    entry.add_to_hass(hass)
    with patch.dict(CLIENTS, {"type228": lambda _: client}):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_it_has_eight_slots_and_no_start(hass: HomeAssistant, ble_device: BLEDevice) -> None:
    await _setup(hass, Fake228())

    for slot in (1, 8):
        assert hass.states.get(f"sensor.skyrc_type228_slot_{slot}_voltage") is not None
        assert hass.states.get(f"button.skyrc_type228_slot_{slot}_stop") is not None
    assert hass.states.get("sensor.skyrc_type228_slot_9_voltage") is None
    assert hass.states.get("sensor.skyrc_type228_slot_1_battery_type") is None
    assert hass.states.get("button.skyrc_type228_slot_1_start") is None


async def test_the_eighth_slot_can_be_stopped(hass: HomeAssistant, ble_device: BLEDevice) -> None:
    client = Fake228()
    await _setup(hass, client)

    await hass.services.async_call("skyrc_charger", "stop_slot", {"slot": 8}, blocking=True)

    assert client.called("stop_channel") == [7]


async def test_starting_says_it_is_not_supported(
    hass: HomeAssistant, ble_device: BLEDevice
) -> None:
    client = Fake228()
    await _setup(hass, client)

    with pytest.raises(HomeAssistantError, match="not supported yet on the TYPE228"):
        await hass.services.async_call(
            "skyrc_charger", "start_slot", {"slot": 1, "chemistry": "nimh"}, blocking=True
        )
