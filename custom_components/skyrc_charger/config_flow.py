from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    BLE_SERVICE_UUID,
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_NAME,
    DEFAULT_PASSCODE,
    DOMAIN,
    MODEL_MC5000,
    MODEL_NAMES,
    MODELS,
    OPTION_PASSCODE,
)
from .coordinator import BLE_NAME_PATTERNS_BY_MODEL, _name_matches

_LOGGER = logging.getLogger(__name__)


def _valid_passcode(value: Any) -> bool:
    text = str(value)
    return len(text) == 4 and text.isascii() and text.isdigit()


class SkyrcChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the SkyRC chargers."""

    VERSION = 1

    def __init__(self) -> None:
        self._model: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> SkyrcChargerOptionsFlow:
        return SkyrcChargerOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry) -> bool:
        # Only the MC5000 has anything to configure: its passcode.
        return config_entry.data.get(CONF_MODEL) == MODEL_MC5000

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1: pick the charger model."""
        if user_input is not None:
            self._model = user_input[CONF_MODEL]
            return await self.async_step_device()

        schema = vol.Schema({vol.Required(CONF_MODEL, default=MODEL_MC5000): vol.In(MODELS)})
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 2: pick/enter the BLE device address."""
        errors: dict[str, str] = {}
        model = self._model or MODEL_MC5000

        if user_input is not None:
            passcode = str(user_input.get(OPTION_PASSCODE, DEFAULT_PASSCODE)).strip()
            if not _valid_passcode(passcode):
                errors[OPTION_PASSCODE] = "invalid_passcode"
            else:
                address = str(user_input[CONF_ADDRESS]).strip()
                name = str(user_input.get(CONF_NAME) or MODEL_NAMES[model]).strip()

                await self.async_set_unique_id(f"{model}-{address.upper()}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=name,
                    data={CONF_ADDRESS: address, CONF_NAME: name, CONF_MODEL: model},
                    options={OPTION_PASSCODE: passcode} if model == MODEL_MC5000 else {},
                )

        devices = await self._async_discover_devices(model)
        if not devices and not errors:
            errors["base"] = "no_devices_found"

        return self.async_show_form(
            step_id="device",
            data_schema=self._build_schema(devices, model),
            errors=errors,
        )

    async def _async_discover_devices(self, model: str) -> dict[str, str]:
        """List candidate chargers from Home Assistant's Bluetooth manager.

        Going through HA rather than running our own BleakScanner sweep means
        devices seen by ESPHome/Shelly Bluetooth proxies show up too, and we
        don't take the adapter away from other integrations for 15 seconds
        while the form loads.
        """
        devices: dict[str, str] = {}

        try:
            infos = bluetooth.async_discovered_service_info(self.hass, connectable=True)
        except Exception as err:
            _LOGGER.warning("HA Bluetooth discovery unavailable, using manual setup: %r", err)
            return devices

        patterns = BLE_NAME_PATTERNS_BY_MODEL[model]

        for info in infos:
            address = info.address or ""
            if not address:
                continue

            name = info.name or ""
            service_uuids = [uuid.lower() for uuid in (info.service_uuids or [])]

            if not (_name_matches(name, patterns) or BLE_SERVICE_UUID in service_uuids):
                continue

            label = f"{name or 'SkyRC charger'} ({address})"
            rssi = getattr(info, "rssi", None)
            if rssi is not None:
                label = f"{label} — RSSI {rssi}"

            devices[address] = label

        return devices

    def _build_schema(self, devices: dict[str, str], model: str) -> vol.Schema:
        default_name = MODEL_NAMES[model]
        fields: dict = {
            vol.Required(CONF_ADDRESS): vol.In(devices) if devices else str,
            vol.Optional(CONF_NAME, default=default_name): str,
        }
        if model == MODEL_MC5000:
            fields[vol.Optional(OPTION_PASSCODE, default=DEFAULT_PASSCODE)] = str
        return vol.Schema(fields)


class SkyrcChargerOptionsFlow(config_entries.OptionsFlow):
    """Change the passcode an MC5000 is unlocked with."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current = self.config_entry.options.get(OPTION_PASSCODE, DEFAULT_PASSCODE)

        if user_input is not None:
            passcode = str(user_input[OPTION_PASSCODE]).strip()
            if _valid_passcode(passcode):
                return self.async_create_entry(
                    data={**self.config_entry.options, OPTION_PASSCODE: passcode}
                )
            errors[OPTION_PASSCODE] = "invalid_passcode"

        schema = vol.Schema({vol.Required(OPTION_PASSCODE, default=current): str})
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
