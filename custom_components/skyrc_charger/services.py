"""Services of the SkyRC Charger integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import CHEMISTRY_OPTIONS, DOMAIN, MAX_CHANNELS, MODE_OPTIONS
from .coordinator import SkyrcConfigEntry, SkyrcCoordinator

ATTR_ENTRY_ID = "entry_id"
ATTR_SLOT = "slot"
ATTR_CHEMISTRY = "chemistry"
ATTR_MODE = "mode"
ATTR_CURRENT_MA = "current_ma"
ATTR_CAPACITY_MAH = "capacity_mah"

SERVICE_REFRESH = "refresh"
SERVICE_START_SLOT = "start_slot"
SERVICE_STOP_SLOT = "stop_slot"
SERVICE_START_ALL = "start_all"
SERVICE_STOP_ALL = "stop_all"
SERVICE_FETCH_VOLTAGE_CURVE = "fetch_voltage_curve"

_ENTRY: dict[Any, Any] = {vol.Optional(ATTR_ENTRY_ID): cv.string}
_SLOT: dict[Any, Any] = {
    vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS))
}
_PROGRAM: dict[Any, Any] = {
    vol.Optional(ATTR_CHEMISTRY): vol.In(CHEMISTRY_OPTIONS),
    vol.Optional(ATTR_MODE): vol.In(MODE_OPTIONS),
    vol.Optional(ATTR_CURRENT_MA): vol.Coerce(int),
    vol.Optional(ATTR_CAPACITY_MAH): vol.Coerce(int),
}


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> SkyrcCoordinator:
    """The charger a call targets; entry_id is optional with a single charger."""
    entries: list[SkyrcConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No SkyRC charger is set up")

    entry_id = call.data.get(ATTR_ENTRY_ID)
    if entry_id is None:
        if len(entries) > 1:
            raise HomeAssistantError(
                "Several SkyRC chargers are set up; pass entry_id to say which one"
            )
        return entries[0].runtime_data

    for entry in entries:
        if entry.entry_id == entry_id:
            return entry.runtime_data
    raise HomeAssistantError(f"Unknown SkyRC charger config entry {entry_id!r}")


def _slot(coordinator: SkyrcCoordinator, call: ServiceCall) -> int:
    slot: int = call.data[ATTR_SLOT]
    if slot > coordinator.channel_count:
        raise HomeAssistantError(
            f"{coordinator.model.upper()} has {coordinator.channel_count} slots; "
            f"slot {slot} does not exist"
        )
    return slot


def _program(call: ServiceCall) -> dict[str, Any]:
    return {
        "chemistry": call.data.get(ATTR_CHEMISTRY),
        "mode": call.data.get(ATTR_MODE),
        "current": call.data.get(ATTR_CURRENT_MA),
        "capacity": call.data.get(ATTR_CAPACITY_MAH),
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration-wide services."""

    async def refresh(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_request_refresh()

    async def start_slot(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_start_slot(_slot(coordinator, call), **_program(call))

    async def stop_slot(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_stop_slot(_slot(coordinator, call))

    async def start_all(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_start_all(**_program(call))

    async def stop_all(call: ServiceCall) -> None:
        await _coordinator(hass, call).async_stop_all()

    async def fetch_voltage_curve(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_fetch_curve(_slot(coordinator, call))

    for name, handler, fields in (
        (SERVICE_REFRESH, refresh, _ENTRY),
        (SERVICE_START_SLOT, start_slot, {**_ENTRY, **_SLOT, **_PROGRAM}),
        (SERVICE_STOP_SLOT, stop_slot, {**_ENTRY, **_SLOT}),
        (SERVICE_START_ALL, start_all, {**_ENTRY, **_PROGRAM}),
        (SERVICE_STOP_ALL, stop_all, _ENTRY),
        (SERVICE_FETCH_VOLTAGE_CURVE, fetch_voltage_curve, {**_ENTRY, **_SLOT}),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=vol.Schema(fields))
