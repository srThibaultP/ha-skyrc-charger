"""The charger's settings, the declared chemistry, the curves."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.skyrc_charger.coordinator import POLL_INTERVAL
from custom_components.skyrc_charger.models import ChannelData
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .conftest import FakeClient, make_entry


async def test_a_select_writes_the_setting_to_the_charger(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.skyrc_mc5000_backlight", "option": "high"},
        blocking=True,
    )

    assert client.called("set_settings") == [{"backlight": "high"}]
    assert hass.states.get("select.skyrc_mc5000_backlight").state == "high"


async def test_the_sleep_timer_is_offered_in_minutes(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    state = hass.states.get("select.skyrc_mc5000_sleep_timer")
    assert state.state == "off"
    assert state.attributes["options"] == [
        "off",
        "1 min",
        "3 min",
        "5 min",
        "10 min",
        "15 min",
        "30 min",
        "60 min",
        "120 min",
    ]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.skyrc_mc5000_sleep_timer", "option": "15 min"},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.skyrc_mc5000_sleep_timer", "option": "off"},
        blocking=True,
    )

    assert client.called("set_settings") == [
        {"sleep_timer_minutes": 15},
        {"sleep_timer_minutes": 0},
    ]


async def test_the_warning_tone_switch(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.skyrc_mc5000_warning_tone"}, blocking=True
    )

    assert client.called("set_settings") == [{"warning_tone": False}]
    assert hass.states.get("switch.skyrc_mc5000_warning_tone").state == "off"


async def test_the_declared_chemistry_survives_a_restart(
    hass: HomeAssistant, client: FakeClient, ble_device: object
) -> None:
    mock_restore_cache(hass, [State("select.skyrc_mc5000_slot_4_expected_chemistry", "lifepo4")])
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("select.skyrc_mc5000_slot_4_expected_chemistry").state == "lifepo4"
    assert config_entry.runtime_data.expected_chemistry[3] == "lifepo4"


async def test_working_slots_get_their_curve_refreshed_when_asked(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.skyrc_mc5000_auto_fetch_voltage_curves"},
        blocking=True,
    )
    client.channels[1] = ChannelData(index=1, status="charging", voltage=1.3, current=0.5)

    async_fire_time_changed(hass, dt_util.utcnow() + POLL_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done()

    assert client.called("curve") == [1]
    assert hass.states.get("sensor.skyrc_mc5000_slot_2_voltage_curve_points").state == "3"
    assert hass.states.get("sensor.skyrc_mc5000_slot_1_voltage_curve_points").state == "unknown"


async def test_the_status_sensors_read_the_slots(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    client.channels[0] = ChannelData(
        index=0,
        status="charging",
        chemistry="nimh",
        mode="charge",
        voltage=1.387,
        current=0.999,
        capacity=380,
        resistance=55,
        time=3725,
        error=None,
    )

    async_fire_time_changed(hass, dt_util.utcnow() + POLL_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done()

    def state(name: str) -> str:
        return hass.states.get(f"sensor.skyrc_mc5000_slot_1_{name}").state

    assert state("status") == "charging"
    assert state("battery_type") == "nimh"
    assert state("voltage") == "1.387"
    assert state("current") == "0.999"
    assert state("capacity") == "380"
    assert state("elapsed_time") == "1:02:05"
    assert state("error") == "none"
