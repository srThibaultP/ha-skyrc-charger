"""Diagnostics of a SkyRC charger."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, OPTION_PASSCODE
from .coordinator import SkyrcConfigEntry

TO_REDACT = {CONF_ADDRESS, OPTION_PASSCODE, "address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SkyrcConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "polling_disabled": entry.pref_disable_polling,
        "last_update_success": coordinator.last_update_success,
        "state": async_redact_data(asdict(coordinator.data), TO_REDACT)
        if coordinator.data
        else None,
        "curves": {
            slot + 1: {
                "samples": len(curve.samples_mv),
                "interval_seconds": curve.interval_seconds,
                "checksum_ok": curve.checksum_ok,
            }
            for slot, curve in coordinator.curves.items()
        },
    }
