"""Config flow of the SkyRC Charger integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from bleak.exc import BleakError
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    BLE_SERVICE_UUID,
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_NAME,
    DEFAULT_PASSCODE,
    DOMAIN,
    MC5000_BLE_NAME_PATTERNS,
    MODEL_MC5000,
    MODEL_NAMES,
    OPTION_PASSCODE,
)
from .mc5000_client import Mc5000Client

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY = "manual"


def _passcode_ok(value: str) -> bool:
    return len(value) == 4 and value.isascii() and value.isdigit()


class SkyrcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a charger, found by Bluetooth or picked from the nearby devices."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None

    # -- helpers ---------------------------------------------------------

    async def _async_check(self, address: str, passcode: str) -> str | None:
        """Try the charger; return an error key, or None when it answers."""
        if not _passcode_ok(passcode):
            return "invalid_passcode_format"

        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is None:
            return "cannot_connect"

        client = Mc5000Client(device)
        client.set_passcode(passcode)
        try:
            await client.connect()
        except (BleakError, TimeoutError, OSError) as err:
            _LOGGER.debug("Could not connect to %s: %r", address, err)
            return "cannot_connect"

        unlocked = client.unlocked
        await client.disconnect()
        return "invalid_auth" if unlocked is False else None

    def _nearby(self) -> dict[str, str]:
        """The chargers Home Assistant's Bluetooth can connect to."""
        found: dict[str, str] = {}
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            name = info.name or ""
            if BLE_SERVICE_UUID in (uuid.lower() for uuid in info.service_uuids) or any(
                pattern in name.lower() for pattern in MC5000_BLE_NAME_PATTERNS
            ):
                found[info.address.upper()] = f"{name or 'SkyRC charger'} ({info.address})"
        return found

    def _create(self, address: str, name: str, passcode: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=name,
            data={CONF_ADDRESS: address, CONF_MODEL: MODEL_MC5000, CONF_NAME: name},
            options={OPTION_PASSCODE: passcode},
        )

    async def _async_claim(self, address: str) -> None:
        await self.async_set_unique_id(f"{MODEL_MC5000}-{address.upper()}")
        self._abort_if_unique_id_configured()

    # -- steps -----------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self._async_claim(discovery_info.address)
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A charger found by Bluetooth: ask for its passcode."""
        assert self._discovery is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            passcode = str(user_input[OPTION_PASSCODE]).strip()
            error = await self._async_check(self._discovery.address, passcode)
            if error is None:
                return self._create(
                    self._discovery.address.upper(), MODEL_NAMES[MODEL_MC5000], passcode
                )
            errors[OPTION_PASSCODE] = error

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(OPTION_PASSCODE, default=DEFAULT_PASSCODE): str}),
            description_placeholders={"name": self._discovery.name},
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a nearby charger, or type its address."""
        errors: dict[str, str] = {}
        nearby = self._nearby()

        if user_input is not None:
            address = str(user_input[CONF_ADDRESS]).strip().upper()
            passcode = str(user_input[OPTION_PASSCODE]).strip()
            await self._async_claim(address)
            error = await self._async_check(address, passcode)
            if error is None:
                name = str(user_input.get(CONF_NAME) or MODEL_NAMES[MODEL_MC5000]).strip()
                return self._create(address, name, passcode)
            errors["base" if error == "cannot_connect" else OPTION_PASSCODE] = error

        address_field = vol.In(nearby) if nearby else str
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): address_field,
                    vol.Optional(CONF_NAME, default=MODEL_NAMES[MODEL_MC5000]): str,
                    vol.Required(OPTION_PASSCODE, default=DEFAULT_PASSCODE): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The charger refused the passcode: ask for the right one."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            passcode = str(user_input[OPTION_PASSCODE]).strip()
            error = await self._async_check(entry.data[CONF_ADDRESS], passcode)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, options={**entry.options, OPTION_PASSCODE: passcode}
                )
            errors[OPTION_PASSCODE] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(OPTION_PASSCODE): str}),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SkyrcOptionsFlow:
        return SkyrcOptionsFlow()


class SkyrcOptionsFlow(OptionsFlow):
    """Change the passcode the charger is unlocked with."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self.config_entry.options.get(OPTION_PASSCODE, DEFAULT_PASSCODE)

        if user_input is not None:
            passcode = str(user_input[OPTION_PASSCODE]).strip()
            if _passcode_ok(passcode):
                return self.async_create_entry(
                    data={**self.config_entry.options, OPTION_PASSCODE: passcode}
                )
            errors[OPTION_PASSCODE] = "invalid_passcode_format"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(OPTION_PASSCODE, default=current): str}),
            errors=errors,
        )
