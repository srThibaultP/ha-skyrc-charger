"""Buttons of a SkyRC charger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SkyrcConfigEntry, SkyrcCoordinator
from .entity import SkyrcAlwaysAvailableEntity

# Commands share one BLE link, which the coordinator serialises.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class DeviceButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[SkyrcCoordinator], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class SlotButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[SkyrcCoordinator, int], Awaitable[None]]


async def _fetch_curve(coordinator: SkyrcCoordinator, slot: int) -> None:
    await coordinator.async_fetch_curve(slot)


DEVICE_BUTTONS = (
    DeviceButtonDescription(
        key="refresh",
        translation_key="refresh",
        icon="mdi:refresh",
        press_fn=lambda coordinator: coordinator.async_request_refresh(),
    ),
    DeviceButtonDescription(
        key="start_all",
        translation_key="start_all",
        icon="mdi:play-circle-outline",
        press_fn=lambda coordinator: coordinator.async_start_all(),
    ),
    DeviceButtonDescription(
        key="stop_all",
        translation_key="stop_all",
        icon="mdi:stop-circle-outline",
        press_fn=lambda coordinator: coordinator.async_stop_all(),
    ),
)

SLOT_BUTTONS = (
    SlotButtonDescription(
        key="start",
        translation_key="slot_start",
        icon="mdi:play-circle-outline",
        press_fn=lambda coordinator, slot: coordinator.async_start_slot(slot),
    ),
    SlotButtonDescription(
        key="stop",
        translation_key="slot_stop",
        icon="mdi:stop-circle-outline",
        press_fn=lambda coordinator, slot: coordinator.async_stop_slot(slot),
    ),
    SlotButtonDescription(
        key="fetch_voltage_curve",
        translation_key="fetch_voltage_curve",
        icon="mdi:chart-line",
        press_fn=_fetch_curve,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkyrcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        DeviceButton(coordinator, description)
        for description in DEVICE_BUTTONS
        if coordinator.supports_start or description.key != "start_all"
    ]
    for slot in range(1, coordinator.channel_count + 1):
        entities.extend(
            SlotButton(coordinator, slot, description)
            for description in SLOT_BUTTONS
            if coordinator.supports_start or description.key != "start"
        )
    async_add_entities(entities)


class DeviceButton(SkyrcAlwaysAvailableEntity, ButtonEntity):
    entity_description: DeviceButtonDescription

    def __init__(self, coordinator: SkyrcCoordinator, description: DeviceButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)


class SlotButton(SkyrcAlwaysAvailableEntity, ButtonEntity):
    entity_description: SlotButtonDescription

    def __init__(
        self, coordinator: SkyrcCoordinator, slot: int, description: SlotButtonDescription
    ) -> None:
        super().__init__(coordinator, f"slot_{slot}_{description.key}")
        self.entity_description = description
        self._slot = slot
        self._attr_translation_placeholders = {"slot": str(slot)}

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator, self._slot)
