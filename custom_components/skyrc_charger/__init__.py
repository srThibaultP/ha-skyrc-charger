"""SkyRC Charger: monitor and control SkyRC chargers over Bluetooth."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CHANNELS_BY_MODEL, CONF_MODEL, DOMAIN, MODEL_MC5000
from .coordinator import SkyrcConfigEntry, SkyrcCoordinator
from .services import async_setup_services

PLATFORMS = [Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SkyrcConfigEntry) -> bool:
    model = entry.data.get(CONF_MODEL, MODEL_MC5000)
    if model not in CHANNELS_BY_MODEL:
        raise ConfigEntryError(
            f"The SkyRC {model.upper()} is no longer supported by this integration; "
            "remove the entry (version 2.0 only supports the models of the SkyRC app)"
        )

    coordinator = SkyrcCoordinator(hass, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_shutdown)

    # With polling off the charger is left to the SkyRC app: nothing connects
    # until a command or a refresh asks for it.
    if not entry.pref_disable_polling:
        await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: SkyrcConfigEntry) -> None:
    """A changed passcode or polling setting starts over with a new link."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SkyrcConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
