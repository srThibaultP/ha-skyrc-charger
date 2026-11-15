"""Switches of a SkyRC charger."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import SkyrcConfigEntry, SkyrcCoordinator
from .entity import SkyrcAlwaysAvailableEntity, SkyrcEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkyrcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = []
    if coordinator.supports_curves:
        entities.append(AutoFetchCurvesSwitch(coordinator))
    if coordinator.supports_settings:
        entities.append(WarningToneSwitch(coordinator))
    if coordinator.has_basic_settings:
        entities.extend(BasicSettingSwitch(coordinator, key) for key in ("beep", "screensaver"))
    async_add_entities(entities)


class BasicSettingSwitch(SkyrcEntity, SwitchEntity):
    """A switch among the MC3000's own settings."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SkyrcCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._key = key
        self._attr_translation_key = key

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return getattr(data.device, self._key) if data else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_settings(**{self._key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_settings(**{self._key: False})


class WarningToneSwitch(SkyrcEntity, SwitchEntity):
    """The charger's warning tone, written to the charger when toggled."""

    _attr_translation_key = "warning_tone"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SkyrcCoordinator) -> None:
        super().__init__(coordinator, "warning_tone")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.device.warning_tone if data else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_settings(warning_tone=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_settings(warning_tone=False)


class AutoFetchCurvesSwitch(SkyrcAlwaysAvailableEntity, SwitchEntity, RestoreEntity):
    """Keep the voltage curves of the working slots refreshed."""

    _attr_translation_key = "auto_fetch_voltage_curves"

    def __init__(self, coordinator: SkyrcCoordinator) -> None:
        super().__init__(coordinator, "auto_fetch_voltage_curves")

    @property
    def is_on(self) -> bool:
        return self.coordinator.auto_fetch_curves

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self.coordinator.auto_fetch_curves = last.state == STATE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_fetch_curves = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_fetch_curves = False
        self.async_write_ha_state()
