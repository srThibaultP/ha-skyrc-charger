"""An NC3000: monitored and stopped, not started yet."""

from __future__ import annotations

from bleak.backends.device import BLEDevice
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skyrc_charger.models import ChannelData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeClient, make_entry


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = make_entry(model="nc3000")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_it_has_the_sensors_it_reports_and_no_start(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    await _setup(hass)

    assert hass.states.get("sensor.skyrc_nc3000_slot_1_voltage") is not None
    assert hass.states.get("button.skyrc_nc3000_slot_1_stop") is not None
    # No chemistry on the NC3000, and nothing to start it with yet.
    assert hass.states.get("sensor.skyrc_nc3000_slot_1_battery_type") is None
    assert hass.states.get("select.skyrc_nc3000_slot_1_expected_chemistry") is None
    assert hass.states.get("button.skyrc_nc3000_slot_1_start") is None
    assert hass.states.get("button.skyrc_nc3000_start_all") is None


async def test_starting_says_it_is_not_supported(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    await _setup(hass)

    with pytest.raises(HomeAssistantError, match="not supported yet on the NC3000"):
        await hass.services.async_call(
            "skyrc_charger", "start_slot", {"slot": 1, "chemistry": "nimh"}, blocking=True
        )

    assert client.called("start_channel") == []


async def test_stopping_still_works(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    await _setup(hass)
    client.channels[0] = ChannelData(index=0, status="charging", voltage=1.3)

    await hass.services.async_call("skyrc_charger", "stop_slot", {"slot": 1}, blocking=True)

    assert client.called("stop_channel") == [0]
