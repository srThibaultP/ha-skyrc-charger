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
from homeassistant.data_entry_flow import AbortFlow

from .const import (
    BLE_SERVICE_UUID,
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_NAME,
    DOMAIN,
    MC5000_BLE_NAME_PATTERNS,
    MODEL_BY_CODE,
    MODEL_MC3000,
    MODEL_NAMES,
    MODEL_PSU650,
    OPTION_PASSCODE,
    PSU650_NAME_FRAGMENT,
    default_passcode,
)
from .coordinator import CLIENTS
from .mc3000_client import Mc3000Client
from .mc5000_client import Mc5000Client

_LOGGER = logging.getLogger(__name__)

_BASE_ERRORS = ("cannot_connect", "unsupported_model")


def _passcode_ok(value: str) -> bool:
    return len(value) == 4 and value.isascii() and value.isdigit()


class SkyrcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a charger, found by Bluetooth or picked from the nearby devices."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None

    # -- helpers ---------------------------------------------------------

    async def _async_check(self, address: str, passcode: str) -> tuple[str | None, str | None, str]:
        """Try the charger: (error key, model, passcode used).

        The charger is first asked which model it is, then connected with the
        driver of that model, which is the one that knows how to use the
        passcode. A blank passcode means the factory one of the model.
        """
        if passcode and not _passcode_ok(passcode):
            return "invalid_passcode_format", None, passcode

        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is None:
            return "cannot_connect", None, passcode

        if PSU650_NAME_FRAGMENT in (device.name or "").upper():
            # The app tells the PSU650 by its Bluetooth name, not a model code.
            model: str | None = MODEL_PSU650
        else:
            probe = Mc5000Client(device)
            try:
                await probe.connect(probe=True)
                code = probe.model_code
                await probe.disconnect()
            except (BleakError, TimeoutError, OSError) as err:
                _LOGGER.debug("Could not connect to %s: %r", address, err)
                return "cannot_connect", None, passcode

            model = MODEL_BY_CODE.get(code or "")
            if model is None:
                # The MC3000 gives no model code: it is the one that answers
                # the info request in its own frame.
                old = Mc3000Client(device)
                try:
                    await old.connect(probe=True)
                    if old.identified:
                        model = MODEL_MC3000
                    await old.disconnect()
                except (BleakError, TimeoutError, OSError) as err:
                    _LOGGER.debug("Could not connect to %s: %r", address, err)
                    return "cannot_connect", None, passcode
        if model is None:
            _LOGGER.warning("Unsupported SkyRC model at %s", address)
            return "unsupported_model", None, passcode

        passcode = passcode or default_passcode(model)
        if not _passcode_ok(passcode):
            return "invalid_passcode_format", None, passcode

        client = CLIENTS[model](device)
        client.set_passcode(passcode)
        try:
            await client.connect()
        except (BleakError, TimeoutError, OSError) as err:
            _LOGGER.debug("Could not connect to %s: %r", address, err)
            return "cannot_connect", None, passcode

        unlocked = client.unlocked
        await client.disconnect()

        if unlocked is False:
            return "invalid_auth", None, passcode
        return None, model, passcode

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

    def _create(self, address: str, model: str, name: str, passcode: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=name,
            data={CONF_ADDRESS: address, CONF_MODEL: model, CONF_NAME: name},
            options={OPTION_PASSCODE: passcode},
        )

    def _abort_if_address_configured(self, address: str) -> None:
        """One entry per charger, whichever model it turns out to be."""
        for entry in self._async_current_entries():
            if str(entry.data.get(CONF_ADDRESS, "")).upper() == address.upper():
                raise AbortFlow("already_configured")

    async def _async_claim(self, address: str, model: str) -> None:
        await self.async_set_unique_id(f"{model}-{address.upper()}")
        self._abort_if_unique_id_configured()

    # -- steps -----------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        self._abort_if_address_configured(discovery_info.address)
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
            address = self._discovery.address.upper()
            error, model, passcode = await self._async_check(address, passcode)
            if error is None and model is not None:
                await self._async_claim(address, model)
                return self._create(address, model, MODEL_NAMES[model], passcode)
            errors["base" if error in _BASE_ERRORS else OPTION_PASSCODE] = error or "cannot_connect"

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Optional(OPTION_PASSCODE, default=""): str}),
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
            self._abort_if_address_configured(address)
            error, model, passcode = await self._async_check(address, passcode)
            if error is None and model is not None:
                await self._async_claim(address, model)
                name = str(user_input.get(CONF_NAME) or MODEL_NAMES[model]).strip()
                return self._create(address, model, name, passcode)
            errors["base" if error in _BASE_ERRORS else OPTION_PASSCODE] = error or "cannot_connect"

        address_field = vol.In(nearby) if nearby else str
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): address_field,
                    vol.Optional(CONF_NAME): str,
                    vol.Optional(OPTION_PASSCODE, default=""): str,
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
            error, _model, passcode = await self._async_check(entry.data[CONF_ADDRESS], passcode)
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
        current = self.config_entry.options.get(
            OPTION_PASSCODE, default_passcode(self.config_entry.data.get(CONF_MODEL, ""))
        )

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
