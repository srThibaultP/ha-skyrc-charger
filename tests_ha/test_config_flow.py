"""Adding a charger, answering a refused passcode, changing the passcode."""

from __future__ import annotations

from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from habluetooth import BluetoothServiceInfoBleak
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skyrc_charger.const import (
    CONF_ADDRESS,
    CONF_MODEL,
    DOMAIN,
    OPTION_PASSCODE,
)
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import ADDRESS, FakeClient, make_entry


def _service_info(address: str = ADDRESS, name: str = "#Charger-E236") -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=["0000ffe0-0000-1000-8000-00805f9b34fb"],
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=None,  # type: ignore[arg-type]
        connectable=True,
        time=0,
        tx_power=None,
    )


async def test_picking_a_nearby_charger(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[_service_info()],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch("custom_components.skyrc_charger.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ADDRESS: ADDRESS, "name": "Chargeur", OPTION_PASSCODE: "4321"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Chargeur"
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["data"][CONF_MODEL] == "mc5000"
    assert result["options"] == {OPTION_PASSCODE: "4321"}
    assert result["result"].unique_id == f"mc5000-{ADDRESS}"
    assert client.passcode == "4321"
    assert not client.connected  # the check lets go of the charger again


async def test_the_address_can_be_typed(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch("custom_components.skyrc_charger.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ADDRESS: ADDRESS.lower(), OPTION_PASSCODE: "1234"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["title"] == "SkyRC MC5000"


async def test_a_charger_that_cannot_be_reached(hass: HomeAssistant, client: FakeClient) -> None:
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=None
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "1234"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_connection_that_fails(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.fail = BleakError("boom")
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "1234"}
    )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_refused_passcode_is_reported(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.unlocked = False
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "0000"}
    )

    assert result["errors"] == {OPTION_PASSCODE: "invalid_auth"}


async def test_a_passcode_must_be_four_digits(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "12a"}
    )

    assert result["errors"] == {OPTION_PASSCODE: "invalid_passcode_format"}
    assert client.calls == []


async def test_a_charger_found_by_bluetooth(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    with patch("custom_components.skyrc_charger.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {OPTION_PASSCODE: "1234"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SkyRC MC5000"
    assert result["data"][CONF_ADDRESS] == ADDRESS


async def test_a_charger_already_set_up_is_not_offered_again(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_with_the_right_passcode(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    client.unlocked = False
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR

    client.unlocked = True
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {OPTION_PASSCODE: "4321"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.options[OPTION_PASSCODE] == "4321"
    assert config_entry.state is ConfigEntryState.LOADED


async def test_the_passcode_can_be_changed_in_the_options(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPTION_PASSCODE: "9081"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPTION_PASSCODE] == "9081"
    # The entry reloaded and unlocks the charger with the new passcode.
    assert client.passcode == "9081"


async def test_a_bad_passcode_in_the_options_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPTION_PASSCODE: "99"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {OPTION_PASSCODE: "invalid_passcode_format"}


async def test_an_nc3000_is_recognised_by_its_model_code(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.model_code = "100209"
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch("custom_components.skyrc_charger.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "1234"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SkyRC NC3000"
    assert result["data"][CONF_MODEL] == "nc3000"
    assert result["result"].unique_id == f"nc3000-{ADDRESS}"


async def test_a_model_the_integration_does_not_know_is_refused(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.model_code = "100999"  # a model nobody knows
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "1234"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_model"}


async def test_a_charger_is_one_entry_whatever_its_model(
    hass: HomeAssistant, entry: MockConfigEntry, client: FakeClient
) -> None:
    client.model_code = "100209"
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "1234"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_type_228_is_recognised_by_its_model_code(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    client.model_code = "100228"
    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch("custom_components.skyrc_charger.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, OPTION_PASSCODE: "1234"}
        )

    assert result["data"][CONF_MODEL] == "type228"
    assert result["title"] == "SkyRC NC3000 Octa"


async def test_reconfigure_moves_the_entry_to_another_address(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    other = "AA:BB:CC:DD:EE:01"

    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await config_entry.start_reconfigure_flow(hass)
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: other}
        )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_ADDRESS] == other
    assert config_entry.unique_id == f"mc5000-{other}"


async def test_reconfigure_refuses_another_model(
    hass: HomeAssistant, client: FakeClient, ble_device: BLEDevice
) -> None:
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client.model_code = "100209"  # an NC3000 answers at the new address

    with patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]):
        result = await config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: "AA:BB:CC:DD:EE:01"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_model"}
    assert config_entry.data[CONF_ADDRESS] == ADDRESS
