"""An NC2600: four slots, monitored only."""

from __future__ import annotations

from unittest.mock import patch

from bleak.backends.device import BLEDevice

from custom_components.skyrc_charger.const import CONF_ADDRESS, DOMAIN
from custom_components.skyrc_charger.models import ChannelData
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant

from .conftest import ADDRESS, FakeClient, make_entry


async def test_it_reports_its_slots_and_offers_nothing_to_stop(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.channels = [
        ChannelData(
            index=0, status="charging", mode="charge", voltage=1.31, current=0.5, capacity=400
        ),
        *[ChannelData(index=i, status="empty", voltage=0.0, current=0.0) for i in range(1, 4)],
    ]
    entry = make_entry(model="nc2600")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.skyrc_nc2600_slot_1_status").state == "charging"
    assert hass.states.get("sensor.skyrc_nc2600_slot_1_voltage").state == "1.31"
    assert hass.states.get("sensor.skyrc_nc2600_slot_4_status").state == "empty"
    assert hass.states.get("button.skyrc_nc2600_slot_1_stop") is None
    assert hass.states.get("button.skyrc_nc2600_slot_1_start") is None
    assert hass.states.get("sensor.skyrc_nc2600_slot_1_battery_type") is None


async def test_it_is_told_from_the_mc3000_by_its_model_code(
    hass: HomeAssistant, client: FakeClient
) -> None:
    named = BLEDevice(ADDRESS, "Charger", {})
    client.model_code = "100145"
    client.identified = True
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=named
        ),
        patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]),
        patch("custom_components.skyrc_charger.async_setup_entry", return_value=True),
        patch("custom_components.skyrc_charger.config_flow.MODEL_BY_CODE", {}),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["data"]["model"] == "nc2600"
