"""Setting up and unloading a charger."""

from __future__ import annotations

from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skyrc_charger.const import CONF_ADDRESS, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import ADDRESS, FakeClient, make_entry


async def test_a_charger_sets_up_with_its_entities(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.skyrc_mc5000_slot_1_status").state == "empty"
    assert hass.states.get("select.skyrc_mc5000_backlight").state == "low"
    assert hass.states.get("switch.skyrc_mc5000_warning_tone").state == "on"
    assert hass.states.get("button.skyrc_mc5000_slot_1_start") is not None
    # Off by default: only known with a probe plugged in.
    assert hass.states.get("sensor.skyrc_mc5000_slot_1_temperature") is None


async def test_the_unique_ids_of_the_entities_are_stable(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Dashboards and history hang on these; they must not change."""
    registry = er.async_get(hass)
    expected = {
        "sensor.skyrc_mc5000_slot_1_voltage": f"{entry.entry_id}_slot_1_voltage",
        "sensor.skyrc_mc5000_slot_3_internal_resistance": f"{entry.entry_id}_slot_3_resistance",
        "sensor.skyrc_mc5000_slot_2_elapsed_time": f"{entry.entry_id}_slot_2_time",
        "sensor.skyrc_mc5000_slot_1_voltage_curve_points": (
            f"{entry.entry_id}_slot_1_voltage_curve_points"
        ),
        "button.skyrc_mc5000_slot_4_stop": f"{entry.entry_id}_slot_4_stop",
        "button.skyrc_mc5000_stop_all": f"{entry.entry_id}_stop_all",
        "select.skyrc_mc5000_slot_1_expected_chemistry": (
            f"{entry.entry_id}_slot_1_expected_chemistry"
        ),
        "select.skyrc_mc5000_sleep_timer": f"{entry.entry_id}_sleep_timer",
        "switch.skyrc_mc5000_warning_tone": f"{entry.entry_id}_warning_tone",
        "switch.skyrc_mc5000_auto_fetch_voltage_curves": (
            f"{entry.entry_id}_auto_fetch_voltage_curves"
        ),
    }
    for entity_id, unique_id in expected.items():
        found = registry.async_get(entity_id)
        assert found is not None, entity_id
        assert found.unique_id == unique_id, entity_id


async def test_the_device_carries_the_versions_the_charger_reported(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}-{ADDRESS}")}
    )

    assert device is not None
    assert device.manufacturer == "SkyRC"
    assert device.model == "MC5000"
    assert device.sw_version == "1.83"
    assert device.hw_version == "1"


async def test_unloading_closes_the_link(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    assert client.connected

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not client.connected


async def test_an_unreachable_charger_is_retried(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    client.fail = BleakError("no route")

    await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_no_connectable_scanner_is_explained(hass: HomeAssistant, client: FakeClient) -> None:
    config_entry = make_entry()
    config_entry.add_to_hass(hass)

    class Heard:
        source = "90:15:06:F9:C2:72"
        rssi = -61

    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=None
        ),
        patch("homeassistant.components.bluetooth.async_last_service_info", return_value=Heard()),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "passive Bluetooth scanner 90:15:06:F9:C2:72" in caplog_text(hass)


def caplog_text(hass: HomeAssistant) -> str:
    # The reason is kept on the entry, which is what the user sees.
    entries = hass.config_entries.async_entries(DOMAIN)
    return str(entries[0].reason)


async def test_a_refused_passcode_starts_the_reauth_flow(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    client.unlocked = False

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_an_entry_of_a_dropped_model_says_why(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="SkyRC MC3000",
        unique_id="mc3000-AA:BB:CC:DD:EE:FF",
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF", "model": "mc3000"},
    )
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert "no longer supported" in str(config_entry.reason)


async def test_with_polling_off_nothing_connects_at_setup(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry(pref_disable_polling=True)
    config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert client.called("connect") == []
    # The buttons still work: they connect when pressed.
    assert hass.states.get("button.skyrc_mc5000_refresh").state != "unavailable"


async def test_the_services_are_described(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from homeassistant.helpers.service import async_get_all_descriptions

    descriptions = (await async_get_all_descriptions(hass))[DOMAIN]

    assert set(descriptions) == {
        "refresh",
        "start_slot",
        "stop_slot",
        "start_all",
        "stop_all",
        "fetch_voltage_curve",
    }
    assert set(descriptions["start_slot"]["fields"]) == {
        "entry_id",
        "slot",
        "chemistry",
        "mode",
        "current_ma",
        "capacity_mah",
    }
