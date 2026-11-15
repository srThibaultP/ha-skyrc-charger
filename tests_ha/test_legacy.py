"""An older charger (Q200): four channels, monitored and stopped."""

from __future__ import annotations

from unittest.mock import patch

from bleak.backends.device import BLEDevice
import pytest

from custom_components.skyrc_charger.const import CONF_ADDRESS, DOMAIN, OPTION_PASSCODE
from custom_components.skyrc_charger.models import ChannelData
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import ADDRESS, FakeClient, make_entry


async def _setup(hass: HomeAssistant) -> None:
    entry = make_entry(model="q200")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_it_offers_what_the_app_reads(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    await _setup(hass)

    assert hass.states.get("sensor.skyrc_q200_slot_4_voltage") is not None
    assert hass.states.get("sensor.skyrc_q200_slot_5_voltage") is None
    assert hass.states.get("button.skyrc_q200_slot_1_stop") is not None
    assert hass.states.get("button.skyrc_q200_slot_1_start") is None
    assert hass.states.get("sensor.skyrc_q200_slot_1_voltage_curve_points") is None
    assert hass.states.get("select.skyrc_q200_backlight") is None


async def test_starting_says_it_is_not_supported(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    await _setup(hass)

    with pytest.raises(HomeAssistantError, match="not supported yet on the Q200"):
        await hass.services.async_call(
            "skyrc_charger", "start_slot", {"slot": 1, "chemistry": "nimh"}, blocking=True
        )


async def test_a_q200_is_recognised_and_gets_its_factory_password(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.model_code = "100104"
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch("custom_components.skyrc_charger.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["data"]["model"] == "q200"
    assert result["title"] == "SkyRC Q200"
    assert result["options"] == {OPTION_PASSCODE: "5793"}
    assert client.passcode == "5793"


async def test_status_of_a_working_channel(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.channels = [
        ChannelData(index=0, status="active", voltage=4.1, current=1.5, capacity=300)
    ] + [ChannelData(index=i, status="idle", voltage=0.0, current=0.0) for i in range(1, 4)]

    await _setup(hass)

    assert hass.states.get("sensor.skyrc_q200_slot_1_status").state == "active"
    assert hass.states.get("sensor.skyrc_q200_slot_1_voltage").state == "4.1"
    assert hass.states.get("sensor.skyrc_q200_slot_2_status").state == "idle"
