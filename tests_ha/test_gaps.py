"""Failure paths of the flows, the coordinator, the switches and the services."""

from __future__ import annotations

from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skyrc_charger.const import CONF_ADDRESS, DOMAIN, OPTION_PASSCODE
from custom_components.skyrc_charger.models import ChannelData
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from .conftest import ADDRESS, FakeClient, make_entry
from .test_config_flow import _service_info

CONFIG_FLOW = "custom_components.skyrc_charger.config_flow"


async def _start_user(hass: HomeAssistant) -> dict:
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_the_mc3000_probe_failing_is_a_connection_error(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.model_code = None
    broken = FakeClient()
    broken.fail = BleakError("no")
    result = await _start_user(hass)
    with patch(f"{CONFIG_FLOW}.Mc3000Client", lambda _: broken):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: ""}
        )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_charger_that_answers_the_probe_but_not_the_connection(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    probe = FakeClient()
    client.fail = BleakError("dropped")
    result = await _start_user(hass)
    with patch(f"{CONFIG_FLOW}.Mc5000Client", lambda _: probe):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "1234"}
        )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_refused_passcode_in_the_bluetooth_flow(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.unlocked = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {OPTION_PASSCODE: "9999"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {OPTION_PASSCODE: "invalid_auth"}


async def test_reauth_with_a_passcode_still_refused(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    client.unlocked = False
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {OPTION_PASSCODE: "4321"}
    )

    assert result["errors"] == {OPTION_PASSCODE: "invalid_auth"}


async def test_no_route_to_the_charger_is_explained(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    coordinator = entry.runtime_data
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=None
        ),
        patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None),
        pytest.raises(HomeAssistantError, match="No Bluetooth adapter or proxy"),
    ):
        await coordinator.async_stop_slot(1)


async def test_every_kind_of_command_failure_is_reported(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    coordinator = entry.runtime_data

    client.unlocked = False
    with pytest.raises(HomeAssistantError, match="refused the passcode"):
        await coordinator.async_stop_slot(1)
    client.unlocked = True

    async def raising(error: Exception) -> None:
        raise error

    for error, text in (
        (ValueError("bad value"), "bad value"),
        (BleakError("gone"), "Cannot talk"),
    ):
        with pytest.raises(HomeAssistantError, match=text):
            await coordinator._async_command(lambda _c, e=error: raising(e))


async def test_a_failing_disconnect_is_only_logged(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    async def broken() -> None:
        raise BleakError("already gone")

    client.disconnect = broken  # type: ignore[method-assign]
    await entry.runtime_data.async_disconnect()


async def test_a_run_with_a_failing_curve_read_carries_on(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    coordinator = entry.runtime_data
    client.channels[0] = ChannelData(index=0, status="charging", voltage=3.9, current=1.0)

    async def broken(_channel: int):
        raise BleakError("no curve")

    client.async_get_voltage_curve = broken  # type: ignore[method-assign]
    coordinator.auto_fetch_curves = True
    coordinator._last_curve_fetch = float("-inf")
    await coordinator.async_refresh()
    # Right after, the 30 second rule skips the read; with nothing working too.
    await coordinator.async_refresh()
    client.channels[0] = ChannelData(index=0, status="empty", voltage=0.0, current=0.0)
    coordinator._last_curve_fetch = float("-inf")
    await coordinator.async_refresh()

    assert coordinator.last_update_success


async def test_the_switches_and_selects_that_turn_things_on(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.skyrc_mc5000_warning_tone"}, blocking=True
    )
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.skyrc_mc5000_auto_fetch_voltage_curves"},
        blocking=True,
    )
    assert ("set_settings", {"warning_tone": True}) in client.calls

    client.device.sleep_timer_minutes = None
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("select.skyrc_mc5000_sleep_timer").state == "unknown"


async def test_the_mc3000_switches_turn_on(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.device.beep = False
    client.device.screensaver = False
    client.device.display_timeout = "auto"
    client.device.cooling_fan = "auto"
    client.device.input_voltage = 11.0
    mc3000 = make_entry(model="mc3000")
    mc3000.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mc3000.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.skyrc_mc3000_screensaver"}, blocking=True
    )

    assert ("set_settings", {"screensaver": True}) in client.calls


async def test_with_two_chargers_an_entry_id_picks_one(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient, ble_device: BLEDevice
) -> None:
    second = MockConfigEntry(
        domain=DOMAIN,
        title="SkyRC MC5000 B",
        unique_id="mc5000-AA:BB:CC:DD:EE:02",
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:02", "model": "mc5000"},
        options={OPTION_PASSCODE: "1234"},
    )
    second.add_to_hass(hass)
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="Several"):
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.services.async_call(DOMAIN, "refresh", {"entry_id": second.entry_id}, blocking=True)


async def test_the_curve_button_fetches_the_curve(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.skyrc_mc5000_slot_1_fetch_voltage_curve"},
        blocking=True,
    )
    assert client.called("curve") == [0]

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.skyrc_mc5000_refresh"}, blocking=True
    )
    assert any(call == "start_all" or call == "curve" for call, _ in client.calls)
