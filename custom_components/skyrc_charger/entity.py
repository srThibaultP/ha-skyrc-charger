"""Base class of the SkyRC Charger entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SkyrcCoordinator


class SkyrcEntity(CoordinatorEntity[SkyrcCoordinator]):
    """An entity of one charger.

    Entity ids are built from the charger's name (the title of its config
    entry) and the entity's own name: `sensor.skyrc_mc5000_slot_1_voltage`.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: SkyrcCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the charger, adding the versions it reported."""
        coordinator = self.coordinator
        entry = coordinator.config_entry
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}-{coordinator.address}")},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=entry.title,
            manufacturer="SkyRC",
            model=coordinator.model.upper(),
        )
        if coordinator.data:
            device = coordinator.data.device
            if device.sw_version:
                info["sw_version"] = device.sw_version
            if device.hw_version:
                info["hw_version"] = device.hw_version
        return info


class SkyrcAlwaysAvailableEntity(SkyrcEntity):
    """Buttons and switches that act on the integration stay usable offline."""

    @property
    def available(self) -> bool:
        return True
