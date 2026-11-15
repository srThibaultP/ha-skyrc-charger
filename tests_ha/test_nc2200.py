"""An NC2200: four slots, monitored and stopped; nothing else yet."""

from __future__ import annotations

from bleak.backends.device import BLEDevice
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeClient, make_entry


async def _setup(hass: HomeAssistant) -> None:
    entry = make_entry(model="nc2200")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_it_offers_what_the_app_reads(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    await _setup(hass)

    assert hass.states.get("sensor.skyrc_nc2200_slot_1_voltage") is not None
    assert hass.states.get("button.skyrc_nc2200_slot_4_stop") is not None
    # No curve, no start, no settings, no chemistry.
    assert hass.states.get("sensor.skyrc_nc2200_slot_1_voltage_curve_points") is None
    assert hass.states.get("button.skyrc_nc2200_slot_1_fetch_voltage_curve") is None
    assert hass.states.get("switch.skyrc_nc2200_auto_fetch_voltage_curves") is None
    assert hass.states.get("button.skyrc_nc2200_slot_1_start") is None
    assert hass.states.get("select.skyrc_nc2200_backlight") is None
    assert hass.states.get("switch.skyrc_nc2200_warning_tone") is None


async def test_the_curve_and_the_start_are_refused(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    await _setup(hass)

    with pytest.raises(HomeAssistantError, match="does not give a voltage curve"):
        await hass.services.async_call(
            "skyrc_charger", "fetch_voltage_curve", {"slot": 1}, blocking=True
        )
    with pytest.raises(HomeAssistantError, match="not supported yet on the NC2200"):
        await hass.services.async_call(
            "skyrc_charger", "start_slot", {"slot": 1, "chemistry": "nimh"}, blocking=True
        )
