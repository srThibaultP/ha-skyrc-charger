from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CHEMISTRY_OPTIONS_BY_MODEL, DOMAIN
from .entity import SkyrcEntity, build_device_info
from .mc5000_client import SETTING_OPTIONS, SLEEP_TIMER_MINUTES

SLEEP_TIMER_OPTIONS = ["off"] + [f"{minutes} min" for minutes in SLEEP_TIMER_MINUTES[1:]]

# key (also the DeviceData attribute), name, options, icon
SETTING_SELECTS = (
    ("beep_volume", "Beep Volume", list(SETTING_OPTIONS["beep_volume"]), "mdi:volume-high"),
    (
        "notification_volume",
        "Notification Volume",
        list(SETTING_OPTIONS["notification_volume"]),
        "mdi:bell-ring-outline",
    ),
    (
        "end_notification",
        "End Notification",
        list(SETTING_OPTIONS["end_notification"]),
        "mdi:bell-check-outline",
    ),
    ("backlight", "Backlight", list(SETTING_OPTIONS["backlight"]), "mdi:brightness-6"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list = [
        SkyrcChemistrySelect(coordinator, entry.entry_id, slot_index)
        for slot_index in range(coordinator.channel_count)
    ]
    if coordinator.supports_settings:
        entities.extend(
            SkyrcSettingSelect(coordinator, entry.entry_id, key, name, options, icon)
            for key, name, options, icon in SETTING_SELECTS
        )
        entities.append(SkyrcSleepTimerSelect(coordinator, entry.entry_id))

    async_add_entities(entities)


class SkyrcChemistrySelect(SelectEntity, RestoreEntity):
    """Expected battery chemistry for a slot.

    The chemistry written into the program when a slot is started: the
    MC5000 only reports a slot's chemistry while a program runs, so what is
    in the slot has to be declared.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:flask-outline"
    _attr_should_poll = False

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        self.coordinator = coordinator
        self.entry_id = entry_id
        self.slot_index = slot_index
        self.slot = slot_index + 1

        self._attr_options = CHEMISTRY_OPTIONS_BY_MODEL[coordinator.model]
        self._attr_name = f"Slot {self.slot} Expected Chemistry"
        self._attr_unique_id = f"{entry_id}_slot_{self.slot}_expected_chemistry"
        self._attr_current_option = "any"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            return
        self._attr_current_option = option
        self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        return self._attr_current_option

    @property
    def device_info(self):
        return build_device_info(self.coordinator, self.entry_id)


class SkyrcSettingSelect(SkyrcEntity, SelectEntity):
    """One of the charger's own settings, written to the charger when changed."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry_id: str, key: str, name: str, options, icon) -> None:
        super().__init__(coordinator, entry_id)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_options = list(options)
        self._attr_unique_id = f"{entry_id}_{key}"

    def _device(self):
        return (self.coordinator.data or {}).get("device")

    @property
    def current_option(self) -> str | None:
        device = self._device()
        value = getattr(device, self._key, None) if device else None
        return value if value in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_charger_settings(**{self._key: option})


class SkyrcSleepTimerSelect(SkyrcSettingSelect):
    """The idle time after which the charger switches its screen off."""

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(
            coordinator, entry_id, "sleep_timer", "Sleep Timer", SLEEP_TIMER_OPTIONS, "mdi:sleep"
        )

    @property
    def current_option(self) -> str | None:
        device = self._device()
        minutes = getattr(device, "sleep_timer_minutes", None) if device else None
        if minutes is None:
            return None
        option = "off" if minutes == 0 else f"{minutes} min"
        return option if option in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        minutes = 0 if option == "off" else int(option.split()[0])
        await self.coordinator.async_set_charger_settings(sleep_timer_minutes=minutes)
