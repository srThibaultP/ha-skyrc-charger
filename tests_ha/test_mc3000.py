"""An MC3000: four slots, monitored and stopped, no password."""

from __future__ import annotations

from unittest.mock import patch

from bleak.backends.device import BLEDevice

from custom_components.skyrc_charger.const import CONF_ADDRESS, DOMAIN
from custom_components.skyrc_charger.models import ChannelData
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant

from .conftest import ADDRESS, FakeClient, make_entry


async def test_it_reports_each_slot_with_its_battery_type(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.channels = [
        ChannelData(
            index=0,
            status="charging",
            chemistry="nimh",
            mode="charge",
            voltage=1.325,
            current=1.0,
            capacity=250,
            time=600,
        ),
        *[ChannelData(index=i, status="standby", voltage=0.0, current=0.0) for i in range(1, 4)],
    ]
    entry = make_entry(model="mc3000")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.skyrc_mc3000_slot_1_status").state == "charging"
    assert hass.states.get("sensor.skyrc_mc3000_slot_1_battery_type").state == "nimh"
    assert hass.states.get("sensor.skyrc_mc3000_slot_1_voltage").state == "1.325"
    assert hass.states.get("sensor.skyrc_mc3000_slot_4_status").state == "standby"
    assert hass.states.get("sensor.skyrc_mc3000_slot_1_voltage_curve_points") is not None
    assert hass.states.get("button.skyrc_mc3000_slot_1_stop") is not None
    assert hass.states.get("button.skyrc_mc3000_slot_1_start") is None
    assert hass.states.get("select.skyrc_mc3000_beep_volume") is None


async def test_it_is_set_up_when_its_info_answer_is_the_only_clue(
    hass: HomeAssistant, client: FakeClient
) -> None:
    named = BLEDevice(ADDRESS, "Charger", {})
    client.model_code = None  # the MC5000 driver found no model code
    client.identified = True  # but the MC3000 driver got an answer
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=named
        ),
        patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]),
        patch("custom_components.skyrc_charger.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["data"]["model"] == "mc3000"
    assert result["title"] == "SkyRC MC3000"
