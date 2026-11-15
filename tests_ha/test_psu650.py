"""A PSU650: a DC output and three USB-C ports, read only."""

from __future__ import annotations

from unittest.mock import patch

from bleak.backends.device import BLEDevice
import pytest

from custom_components.skyrc_charger.const import CONF_ADDRESS, DOMAIN
from custom_components.skyrc_charger.models import ChannelData
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import ADDRESS, FakeClient, make_entry


async def test_it_reports_each_port_and_the_totals(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.device.total_power = 65.5
    client.device.total_current = 3.2
    client.device.error = None
    client.channels = [
        ChannelData(
            index=0, status="on", voltage=20.0, current=1.5, power=30.0, mode="constant_voltage"
        ),
        ChannelData(index=1, status="on", voltage=9.0, current=5.0, power=45.0, mode="pd_5v"),
        ChannelData(index=2, status="off", voltage=0.0, current=0.0, power=0.0),
        ChannelData(index=3, status="on", voltage=5.0, current=3.0, power=15.0, mode="pd_pps"),
    ]
    entry = make_entry(model="psu650")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.skyrc_psu650_total_power").state == "65.5"
    assert hass.states.get("sensor.skyrc_psu650_total_current").state == "3.2"
    assert hass.states.get("sensor.skyrc_psu650_error").state == "none"
    assert hass.states.get("sensor.skyrc_psu650_slot_1_power").state == "30.0"
    assert hass.states.get("sensor.skyrc_psu650_slot_2_mode").state == "pd_5v"
    assert hass.states.get("sensor.skyrc_psu650_slot_3_status").state == "off"
    # Nothing a power supply does not report, and nothing to stop or start.
    assert hass.states.get("sensor.skyrc_psu650_slot_1_capacity") is None
    assert hass.states.get("sensor.skyrc_psu650_slot_1_elapsed_time") is None
    assert hass.states.get("button.skyrc_psu650_slot_1_stop") is None
    assert hass.states.get("button.skyrc_psu650_stop_all") is None
    assert hass.states.get("button.skyrc_psu650_slot_1_start") is None


async def test_stopping_says_there_is_nothing_to_stop(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    entry = make_entry(model="psu650")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="nothing to stop"):
        await hass.services.async_call("skyrc_charger", "stop_all", {}, blocking=True)


async def test_it_is_recognised_by_its_bluetooth_name(
    hass: HomeAssistant, client: FakeClient
) -> None:
    named = BLEDevice(ADDRESS, "PSU650-1A2B", {})
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=named
        ),
        patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        with patch("custom_components.skyrc_charger.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_ADDRESS: ADDRESS}
            )

    assert result["data"]["model"] == "psu650"
    assert result["title"] == "SkyRC PSU650"
    assert result["options"] == {"passcode": "5793"}
    # No model code was asked for: the name is enough.
    assert ("connect", None) in client.calls
