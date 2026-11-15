"""Selects of a SkyRC charger: what is in each slot, and the charger's settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import MC5000_CHEMISTRY_OPTIONS, MODELS_WITHOUT_CHEMISTRY
from .coordinator import SkyrcConfigEntry, SkyrcCoordinator
from .entity import SkyrcAlwaysAvailableEntity, SkyrcEntity
from .mc3000_client import DISPLAY_OPTIONS, FAN_OPTIONS
from .mc5000_client import SETTING_OPTIONS, SLEEP_TIMER_MINUTES
from .models import DeviceData

PARALLEL_UPDATES = 1

SLEEP_TIMER_OPTIONS = ["off", *(f"{minutes} min" for minutes in SLEEP_TIMER_MINUTES[1:])]


def _sleep_option(device: DeviceData) -> str | None:
    minutes = device.sleep_timer_minutes
    if minutes is None:
        return None
    return "off" if minutes == 0 else f"{minutes} min"


@dataclass(frozen=True, kw_only=True)
class SettingSelectDescription(SelectEntityDescription):
    """One of the charger's own settings."""

    value_fn: Callable[[DeviceData], str | None]
    #: The keyword of async_set_settings, and how an option maps to its value.
    setting: str
    to_setting: Callable[[str], object] = str


SETTING_SELECTS = (
    SettingSelectDescription(
        key="beep_volume",
        translation_key="beep_volume",
        options=list(SETTING_OPTIONS["beep_volume"]),
        value_fn=lambda device: device.beep_volume,
        setting="beep_volume",
    ),
    SettingSelectDescription(
        key="notification_volume",
        translation_key="notification_volume",
        options=list(SETTING_OPTIONS["notification_volume"]),
        value_fn=lambda device: device.notification_volume,
        setting="notification_volume",
    ),
    SettingSelectDescription(
        key="end_notification",
        translation_key="end_notification",
        options=list(SETTING_OPTIONS["end_notification"]),
        value_fn=lambda device: device.end_notification,
        setting="end_notification",
    ),
    SettingSelectDescription(
        key="backlight",
        translation_key="backlight",
        options=list(SETTING_OPTIONS["backlight"]),
        value_fn=lambda device: device.backlight,
        setting="backlight",
    ),
    SettingSelectDescription(
        key="sleep_timer",
        translation_key="sleep_timer",
        options=SLEEP_TIMER_OPTIONS,
        value_fn=_sleep_option,
        setting="sleep_timer_minutes",
        to_setting=lambda option: 0 if option == "off" else int(option.split()[0]),
    ),
)


BASIC_SELECTS = (
    SettingSelectDescription(
        key="display_timeout",
        translation_key="display_timeout",
        options=list(DISPLAY_OPTIONS),
        value_fn=lambda device: device.display_timeout,
        setting="display_timeout",
    ),
    SettingSelectDescription(
        key="cooling_fan",
        translation_key="cooling_fan",
        options=list(FAN_OPTIONS),
        value_fn=lambda device: device.cooling_fan,
        setting="cooling_fan",
    ),
    SettingSelectDescription(
        key="input_voltage",
        translation_key="input_voltage",
        options=[f"{volts / 10:.1f}" for volts in range(100, 121)],
        value_fn=lambda device: (
            None if device.input_voltage is None else f"{device.input_voltage:.1f}"
        ),
        setting="input_voltage",
        to_setting=float,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkyrcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    if coordinator.supports_start and coordinator.model not in MODELS_WITHOUT_CHEMISTRY:
        entities.extend(
            ExpectedChemistrySelect(coordinator, index)
            for index in range(coordinator.channel_count)
        )
    if coordinator.supports_settings:
        entities.extend(SettingSelect(coordinator, description) for description in SETTING_SELECTS)
    if coordinator.has_basic_settings:
        entities.extend(SettingSelect(coordinator, description) for description in BASIC_SELECTS)
    async_add_entities(entities)


class ExpectedChemistrySelect(SkyrcAlwaysAvailableEntity, SelectEntity, RestoreEntity):
    """What the user says is in a slot.

    The MC5000 only reports a slot's chemistry while a program runs, so this
    is the chemistry written into the program when the slot is started.
    """

    _attr_translation_key = "expected_chemistry"
    _attr_options = MC5000_CHEMISTRY_OPTIONS

    def __init__(self, coordinator: SkyrcCoordinator, index: int) -> None:
        super().__init__(coordinator, f"slot_{index + 1}_expected_chemistry")
        self._index = index
        self._attr_translation_placeholders = {"slot": str(index + 1)}
        self._attr_current_option = "any"
        coordinator.expected_chemistry[index] = "any"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.options:
            await self.async_select_option(last.state)

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.coordinator.expected_chemistry[self._index] = option
        self.async_write_ha_state()


class SettingSelect(SkyrcEntity, SelectEntity):
    """A setting of the charger itself, written to it when changed."""

    entity_description: SettingSelectDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: SkyrcCoordinator, description: SettingSelectDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        return self.entity_description.value_fn(data.device) if data else None

    async def async_select_option(self, option: str) -> None:
        description = self.entity_description
        await self.coordinator.async_set_settings(
            **{description.setting: description.to_setting(option)}
        )
