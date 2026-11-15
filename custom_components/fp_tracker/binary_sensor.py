"""Binary sensor platform for the fp_tracker integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TrackerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fp_tracker binary sensors."""
    coordinator: TrackerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ActiveOrderSensor(coordinator),
            CourierNearbySensor(coordinator),
        ]
    )


class _BaseBinary(CoordinatorEntity[TrackerCoordinator], BinarySensorEntity):
    """Common base for the binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TrackerCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def _data(self) -> dict:
        """Return the coordinator data."""
        return self.coordinator.data or {}


class ActiveOrderSensor(_BaseBinary):
    """True while an order is in progress."""

    _attr_name = "配送中"
    _attr_icon = "mdi:food-delivery"

    @property
    def is_on(self) -> bool:
        """Return True when an order is in progress."""
        return bool(self._data.get("active"))


class CourierNearbySensor(_BaseBinary):
    """True when the courier is close to the delivery address."""

    _attr_name = "送餐者已接近"
    _attr_icon = "mdi:map-marker-radius"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Return True when the courier is near."""
        return self._data.get("state") == "arrived"
