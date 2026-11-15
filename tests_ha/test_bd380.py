"""A BD380: one discharger, monitored and stopped."""

from __future__ import annotations

from bleak.backends.device import BLEDevice

from custom_components.skyrc_charger.models import ChannelData
from homeassistant.core import HomeAssistant

from .conftest import FakeClient, make_entry


async def test_it_reports_power_and_energy_and_offers_stop_only(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.channels = [
        ChannelData(
            index=0,
            status="working",
            mode="constant_current",
            voltage=12.34,
            current=2.5,
            power=31.5,
            energy=4.2,
            capacity=1200,
        )
    ]
    entry = make_entry(model="bd380")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.skyrc_bd380_slot_1_power").state == "31.5"
    assert hass.states.get("sensor.skyrc_bd380_slot_1_energy").state == "4.2"
    assert hass.states.get("sensor.skyrc_bd380_slot_1_voltage").state == "12.34"
    assert hass.states.get("sensor.skyrc_bd380_slot_2_voltage") is None
    assert hass.states.get("button.skyrc_bd380_slot_1_stop") is not None
    assert hass.states.get("button.skyrc_bd380_slot_1_start") is None
    assert hass.states.get("sensor.skyrc_bd380_slot_1_battery_type") is None


async def test_the_other_models_have_no_power_sensor(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.skyrc_mc5000_slot_1_power") is None
