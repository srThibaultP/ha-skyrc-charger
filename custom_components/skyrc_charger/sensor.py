"""Sensors of a SkyRC charger: what each slot reports, and its logged curve."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import MODEL_BD380, MODEL_MC5000, MODEL_PSU650
from .coordinator import SkyrcConfigEntry, SkyrcCoordinator
from .entity import SkyrcEntity
from .models import ChannelData, DeviceData

PARALLEL_UPDATES = 0

# A power supply reports a port's power, voltage and current, and no more.
NO_SLOT_READINGS = frozenset({MODEL_PSU650})


def _format_seconds(value: int | None) -> str | None:
    if value is None or value < 0:
        return None
    return f"{value // 3600}:{value % 3600 // 60:02d}:{value % 60:02d}"


@dataclass(frozen=True, kw_only=True)
class SlotSensorDescription(SensorEntityDescription):
    """A sensor reading one value of a slot."""

    value_fn: Callable[[ChannelData], StateType]
    #: Models that report it; every model when empty.
    models: frozenset[str] = frozenset()
    #: Models that do not.
    exclude: frozenset[str] = frozenset()


SLOT_SENSORS: tuple[SlotSensorDescription, ...] = (
    SlotSensorDescription(
        key="status",
        translation_key="status",
        icon="mdi:state-machine",
        value_fn=lambda slot: slot.status,
    ),
    SlotSensorDescription(
        key="battery_type",
        translation_key="battery_type",
        icon="mdi:battery",
        models=frozenset({MODEL_MC5000}),
        value_fn=lambda slot: slot.chemistry,
    ),
    SlotSensorDescription(
        key="mode",
        translation_key="mode",
        icon="mdi:battery-sync",
        value_fn=lambda slot: slot.mode,
    ),
    SlotSensorDescription(
        key="voltage",
        translation_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda slot: slot.voltage,
    ),
    SlotSensorDescription(
        key="current",
        translation_key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda slot: slot.current,
    ),
    SlotSensorDescription(
        # MEASUREMENT, not TOTAL_INCREASING: the charger resets this counter at
        # the start of every run, which a total-increasing sensor would read as
        # a meter rollover and add to the long-term statistic.
        key="capacity",
        translation_key="capacity",
        exclude=NO_SLOT_READINGS,
        native_unit_of_measurement="mAh",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-plus",
        value_fn=lambda slot: slot.capacity,
    ),
    SlotSensorDescription(
        # Only known with an external probe plugged in; otherwise 0.
        key="temperature",
        translation_key="temperature",
        exclude=NO_SLOT_READINGS,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda slot: slot.temperature,
    ),
    SlotSensorDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        models=frozenset({MODEL_BD380, MODEL_PSU650}),
        value_fn=lambda slot: slot.power,
    ),
    SlotSensorDescription(
        key="energy",
        translation_key="energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        models=frozenset({MODEL_BD380}),
        value_fn=lambda slot: slot.energy,
    ),
    SlotSensorDescription(
        key="resistance",
        translation_key="resistance",
        exclude=NO_SLOT_READINGS,
        native_unit_of_measurement="mΩ",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:omega",
        value_fn=lambda slot: slot.resistance,
    ),
    SlotSensorDescription(
        key="time",
        translation_key="time",
        exclude=NO_SLOT_READINGS,
        icon="mdi:timer-outline",
        value_fn=lambda slot: _format_seconds(slot.time),
    ),
    SlotSensorDescription(
        key="error",
        translation_key="error",
        exclude=NO_SLOT_READINGS,
        icon="mdi:alert-circle-outline",
        value_fn=lambda slot: slot.error or "none",
    ),
)


@dataclass(frozen=True, kw_only=True)
class DeviceSensorDescription(SensorEntityDescription):
    """A sensor reading a value of the whole device."""

    value_fn: Callable[[DeviceData], StateType]
    models: frozenset[str]


DEVICE_SENSORS: tuple[DeviceSensorDescription, ...] = (
    DeviceSensorDescription(
        key="total_power",
        translation_key="total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        models=frozenset({MODEL_PSU650}),
        value_fn=lambda device: device.total_power,
    ),
    DeviceSensorDescription(
        key="total_current",
        translation_key="total_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        models=frozenset({MODEL_PSU650}),
        value_fn=lambda device: device.total_current,
    ),
    DeviceSensorDescription(
        key="device_error",
        translation_key="device_error",
        icon="mdi:alert-circle-outline",
        models=frozenset({MODEL_PSU650}),
        value_fn=lambda device: device.error or "none",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SkyrcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        DeviceSensor(coordinator, description)
        for description in DEVICE_SENSORS
        if coordinator.model in description.models
    ]
    for index in range(coordinator.channel_count):
        entities.extend(
            SlotSensor(coordinator, index, description)
            for description in SLOT_SENSORS
            if (not description.models or coordinator.model in description.models)
            and coordinator.model not in description.exclude
        )
        if coordinator.supports_curves:
            entities.append(CurveSensor(coordinator, index))
    async_add_entities(entities)


class DeviceSensor(SkyrcEntity, SensorEntity):
    entity_description: DeviceSensorDescription

    def __init__(self, coordinator: SkyrcCoordinator, description: DeviceSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        data = self.coordinator.data
        return self.entity_description.value_fn(data.device) if data else None


class SlotSensor(SkyrcEntity, SensorEntity):
    entity_description: SlotSensorDescription

    def __init__(
        self,
        coordinator: SkyrcCoordinator,
        index: int,
        description: SlotSensorDescription,
    ) -> None:
        super().__init__(coordinator, f"slot_{index + 1}_{description.key}")
        self.entity_description = description
        self._index = index
        self._attr_translation_placeholders = {"slot": str(index + 1)}

    @property
    def native_value(self) -> StateType:
        data = self.coordinator.data
        if data is None or self._index >= len(data.channels):
            return None
        return self.entity_description.value_fn(data.channels[self._index])


class CurveSensor(SkyrcEntity, SensorEntity):
    """The voltage curve the charger logged for a slot.

    The state is the number of samples; the curve rides along as attributes,
    which the recorder leaves out, so a dashboard chart can plot it.
    """

    _attr_translation_key = "voltage_curve_points"
    _attr_icon = "mdi:chart-line"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"samples_mv", "samples_v"})

    def __init__(self, coordinator: SkyrcCoordinator, index: int) -> None:
        super().__init__(coordinator, f"slot_{index + 1}_voltage_curve_points")
        self._index = index
        self._attr_translation_placeholders = {"slot": str(index + 1)}

    @property
    def native_value(self) -> int | None:
        curve = self.coordinator.curves.get(self._index)
        if curve is None:
            return None
        return sum(1 for sample in curve.samples_mv if sample > 0)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        curve = self.coordinator.curves.get(self._index)
        if curve is None:
            return {}
        return {
            "sample_count": len(curve.samples_mv),
            "interval_seconds": curve.interval_seconds,
            "checksum_ok": curve.checksum_ok,
            "samples_mv": curve.samples_mv,
            "samples_v": [round(sample / 1000, 3) for sample in curve.samples_mv],
            "last_fetched": curve.fetched_at.isoformat(timespec="seconds"),
        }
