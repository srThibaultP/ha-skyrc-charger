from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import SkyrcEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    if coordinator.supports_settings:
        entities.append(SkyrcWarningToneSwitch(coordinator, entry.entry_id))
    if coordinator.supports_voltage_curves:
        entities.append(SkyrcAutoFetchVoltageCurvesSwitch(coordinator, entry.entry_id))

    async_add_entities(entities)


class SkyrcBaseSwitch(SkyrcEntity, SwitchEntity):
    """These switches control the integration, so they stay usable offline."""

    @property
    def available(self) -> bool:
        return True


class SkyrcWarningToneSwitch(SkyrcEntity, SwitchEntity):
    """The charger's warning tone, written to the charger when toggled."""

    _attr_name = "Warning Tone"
    _attr_icon = "mdi:alert-octagon-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_warning_tone"

    @property
    def is_on(self) -> bool | None:
        device = (self.coordinator.data or {}).get("device")
        return getattr(device, "warning_tone", None) if device else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_charger_settings(warning_tone=True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_charger_settings(warning_tone=False)


class SkyrcAutoFetchVoltageCurvesSwitch(SkyrcBaseSwitch):
    """Keep the voltage curves of the working slots refreshed automatically."""

    _attr_name = "Auto Fetch Voltage Curves"
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_auto_fetch_voltage_curves"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.auto_fetch_voltage_curves)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_auto_fetch_voltage_curves(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_auto_fetch_voltage_curves(False)
        self.async_write_ha_state()
