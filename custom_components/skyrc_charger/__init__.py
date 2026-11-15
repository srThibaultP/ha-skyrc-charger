from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import (
    CHANNELS_BY_MODEL,
    CHEMISTRY_OPTIONS,
    CONF_ADDRESS,
    CONF_MODEL,
    DEFAULT_PASSCODE,
    DOMAIN,
    MAX_CHANNELS,
    MODE_OPTIONS,
    MODEL_MC5000,
    OPTION_PASSCODE,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.BUTTON, Platform.SWITCH]

SERVICE_REFRESH = "refresh"
SERVICE_START_SLOT = "start_slot"
SERVICE_STOP_SLOT = "stop_slot"
SERVICE_START_ALL = "start_all"
SERVICE_STOP_ALL = "stop_all"
SERVICE_FETCH_VOLTAGE_CURVE = "fetch_voltage_curve"

ATTR_ENTRY_ID = "entry_id"
ATTR_SLOT = "slot"
ATTR_CHEMISTRY = "chemistry"
ATTR_MODE = "mode"
ATTR_CURRENT_MA = "current_ma"
ATTR_CAPACITY_MAH = "capacity_mah"

_SERVICES = (
    SERVICE_REFRESH,
    SERVICE_START_SLOT,
    SERVICE_STOP_SLOT,
    SERVICE_START_ALL,
    SERVICE_STOP_ALL,
    SERVICE_FETCH_VOLTAGE_CURVE,
)


def _entries(hass: HomeAssistant) -> dict:
    return {
        key: value
        for key, value in hass.data.get(DOMAIN, {}).items()
        if isinstance(value, dict) and "coordinator" in value
    }


def _get_coordinator(hass: HomeAssistant, entry_id: str | None):
    """Resolve the coordinator a service call targets.

    entry_id is optional while a single charger is configured, which keeps
    hand-written scripts and voice assistants simple.
    """
    entries = _entries(hass)

    if not entries:
        raise HomeAssistantError("No SkyRC charger is set up")

    if entry_id is None:
        if len(entries) > 1:
            raise HomeAssistantError(
                "Several SkyRC chargers are set up; pass entry_id to say which one"
            )
        entry_id = next(iter(entries))

    if entry_id not in entries:
        raise HomeAssistantError(f"Unknown SkyRC charger config entry {entry_id!r}")

    return entries[entry_id]["coordinator"]


def _get_expected_chemistry(hass: HomeAssistant, entry_id: str, slot: int) -> str:
    """Read back the 'expected chemistry' select for one slot.

    Resolved through the entity registry by unique_id: guessing the entity_id
    from the entry_id doesn't survive slugification, and the previous
    fallback (scan every select ending in the right suffix) would happily
    pick up another charger's slot.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "select", DOMAIN, f"{entry_id}_slot_{slot}_expected_chemistry"
    )

    if entity_id is None:
        return "any"

    state = hass.states.get(entity_id)
    if state is None:
        return "any"

    value = str(state.state).lower()
    return "any" if value in ("unknown", "unavailable", "") else value


def _start_kwargs(call: ServiceCall, expected_chemistry: str) -> dict:
    kwargs: dict = {}

    if ATTR_CHEMISTRY in call.data:
        kwargs["chemistry"] = call.data[ATTR_CHEMISTRY]
    elif expected_chemistry != "any":
        kwargs["chemistry"] = expected_chemistry
    if ATTR_MODE in call.data:
        kwargs["mode"] = call.data[ATTR_MODE]
    if ATTR_CURRENT_MA in call.data:
        kwargs["current"] = call.data[ATTR_CURRENT_MA]
    if ATTR_CAPACITY_MAH in call.data:
        kwargs["capacity"] = call.data[ATTR_CAPACITY_MAH]

    return kwargs


_ENTRY_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): str})

_SLOT_SCHEMA = _ENTRY_SCHEMA.extend(
    {vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS))}
)

_START_SLOT_SCHEMA = _SLOT_SCHEMA.extend(
    {
        vol.Optional(ATTR_CHEMISTRY): vol.In(CHEMISTRY_OPTIONS),
        vol.Optional(ATTR_MODE): vol.In(MODE_OPTIONS),
        vol.Optional(ATTR_CURRENT_MA): vol.Coerce(int),
        vol.Optional(ATTR_CAPACITY_MAH): vol.Coerce(int),
    }
)

_START_ALL_SCHEMA = _ENTRY_SCHEMA.extend(
    {
        vol.Optional(ATTR_CHEMISTRY): vol.In(CHEMISTRY_OPTIONS),
        vol.Optional(ATTR_MODE): vol.In(MODE_OPTIONS),
        vol.Optional(ATTR_CURRENT_MA): vol.Coerce(int),
        vol.Optional(ATTR_CAPACITY_MAH): vol.Coerce(int),
    }
)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration-wide services, once."""

    def coordinator_for(call: ServiceCall):
        return _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))

    def entry_id_for(call: ServiceCall) -> str:
        entry_id = call.data.get(ATTR_ENTRY_ID)
        if entry_id is not None:
            return entry_id
        return next(iter(_entries(hass)))

    def check_slot(coordinator, slot: int | None) -> None:
        if slot is not None and slot > coordinator.channel_count:
            raise HomeAssistantError(
                f"{coordinator.model.upper()} has {coordinator.channel_count} slots; "
                f"slot {slot} does not exist"
            )

    async def connected(call: ServiceCall):
        """Validate the call, then hand back a connected charger.

        Everything that can be rejected without touching the radio is
        checked first, so a bad call never costs a BLE connection.
        """
        coordinator = coordinator_for(call)
        check_slot(coordinator, call.data.get(ATTR_SLOT))

        return await coordinator.async_connected_charger(), coordinator

    async def async_handle_refresh(call: ServiceCall) -> None:
        await coordinator_for(call).async_request_refresh()

    async def async_handle_start_slot(call: ServiceCall) -> None:
        slot = call.data[ATTR_SLOT]
        charger, coordinator = await connected(call)

        await coordinator.async_request_refresh()
        expected = _get_expected_chemistry(hass, entry_id_for(call), slot)

        kwargs = _start_kwargs(call, expected)
        _LOGGER.info("SkyRC %s: starting slot %s with %s", coordinator.model, slot, kwargs)
        try:
            await charger.start_channel(slot - 1, **kwargs)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def async_handle_stop_slot(call: ServiceCall) -> None:
        slot = call.data[ATTR_SLOT]
        charger, coordinator = await connected(call)

        _LOGGER.info("SkyRC %s: stopping slot %s", coordinator.model, slot)
        await charger.stop_channel(slot - 1)
        await coordinator.async_request_refresh()

    async def async_handle_start_all(call: ServiceCall) -> None:
        charger, coordinator = await connected(call)
        entry_id = entry_id_for(call)

        await coordinator.async_request_refresh()

        # Each slot keeps its own declared chemistry rather than inheriting
        # whatever the last one happened to be.
        per_channel: dict[int, dict] = {}
        for slot in range(1, coordinator.channel_count + 1):
            expected = _get_expected_chemistry(hass, entry_id, slot)
            slot_kwargs = _start_kwargs(call, expected)
            if slot_kwargs:
                per_channel[slot - 1] = slot_kwargs

        _LOGGER.info(
            "SkyRC %s: starting all slots with %s", coordinator.model, per_channel or "defaults"
        )
        try:
            await charger.start_all(per_channel=per_channel)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def async_handle_stop_all(call: ServiceCall) -> None:
        charger, coordinator = await connected(call)
        _LOGGER.info("SkyRC %s: stopping all slots", coordinator.model)
        await charger.stop_all()
        await coordinator.async_request_refresh()

    async def async_handle_fetch_voltage_curve(call: ServiceCall) -> None:
        coordinator = coordinator_for(call)
        slot = call.data[ATTR_SLOT]
        check_slot(coordinator, slot)

        if not coordinator.supports_voltage_curves:
            raise HomeAssistantError(f"{coordinator.model.upper()} does not expose voltage curves")

        await coordinator.async_fetch_voltage_curve(slot - 1)

    handlers = {
        SERVICE_REFRESH: (async_handle_refresh, _ENTRY_SCHEMA),
        SERVICE_START_SLOT: (async_handle_start_slot, _START_SLOT_SCHEMA),
        SERVICE_STOP_SLOT: (async_handle_stop_slot, _SLOT_SCHEMA),
        SERVICE_START_ALL: (async_handle_start_all, _START_ALL_SCHEMA),
        SERVICE_STOP_ALL: (async_handle_stop_all, _ENTRY_SCHEMA),
        SERVICE_FETCH_VOLTAGE_CURVE: (async_handle_fetch_voltage_curve, _SLOT_SCHEMA),
    }

    for service, (handler, schema) in handlers.items():
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a SkyRC charger from a config entry."""
    from .coordinator import SkyrcChargerCoordinator

    domain_data = hass.data.setdefault(DOMAIN, {})

    address = entry.data[CONF_ADDRESS]
    model = entry.data.get(CONF_MODEL, MODEL_MC5000)

    if model not in CHANNELS_BY_MODEL:
        raise ConfigEntryError(
            f"The SkyRC {model.upper()} is no longer supported by this integration; "
            "remove the entry (version 2.0 only supports the models of the SkyRC app)"
        )

    coordinator = SkyrcChargerCoordinator(
        hass, address, model, device_name=entry.title, config_entry=entry
    )
    domain_data[entry.entry_id] = {"coordinator": coordinator, "model": model}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _async_register_services(hass)

    # A polling-disabled charger isn't contacted until someone asks
    # for it (refresh button or service, a start or a stop).
    if not coordinator.polling_disabled:
        entry.async_create_background_task(
            hass, coordinator.async_refresh(), f"{DOMAIN} first refresh"
        )

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hand a changed passcode to the coordinator; it reconnects with it."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data is None:
        return
    await entry_data["coordinator"].async_set_passcode(
        entry.options.get(OPTION_PASSCODE, DEFAULT_PASSCODE)
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if not unload_ok:
        return False

    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.pop(entry.entry_id, None)

    if entry_data is not None:
        coordinator = entry_data["coordinator"]
        charger = coordinator.charger
        if charger is not None:
            try:
                await charger.disconnect()
            except Exception as err:
                _LOGGER.debug("SkyRC %s: disconnect on unload failed: %r", coordinator.model, err)

    # Services are integration-wide; drop them only once the last charger is
    # gone.
    if not _entries(hass):
        for service in _SERVICES:
            hass.services.async_remove(DOMAIN, service)

    return unload_ok
