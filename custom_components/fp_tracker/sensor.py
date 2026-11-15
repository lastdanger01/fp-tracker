"""Sensor platform for the fp_tracker integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ETA_ESTIMATE,
    ATTR_ORDER_CODE,
    ATTR_RESTAURANT,
    ATTR_STATUS,
    ATTR_STATUS_CODE,
    DOMAIN,
    STATE_LABELS,
)
from .coordinator import TrackerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fp_tracker sensors."""
    coordinator: TrackerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        TrackerStateSensor(coordinator),
        TrackerEtaSensor(coordinator),
        TrackerEtaEstimateSensor(coordinator),
        TrackerDistanceSensor(coordinator),
    ]
    async_add_entities(entities)


class _BaseTrackerSensor(CoordinatorEntity[TrackerCoordinator], SensorEntity):
    """Common base for the tracker sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TrackerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_attribution = "Powered by foodpanda"

    @property
    def _data(self) -> dict:
        """Return the coordinator data."""
        return self.coordinator.data or {}

    @property
    def available(self) -> bool:
        """Return True when the coordinator has data."""
        return self.coordinator.last_update_success and self._data.get("active", False)


class TrackerStateSensor(_BaseTrackerSensor):
    """The delivery state of the current order."""

    _attr_name = "配送狀態"
    _attr_icon = "mdi:food-delivery"

    @property
    def native_value(self) -> str | None:
        """Return the state label."""
        return self._data.get("label")

    @property
    def extra_state_attributes(self) -> dict:
        """Return the order details."""
        data = self._data
        courier = data.get("courier") or {}
        vendor = data.get("vendor") or {}
        return {
            ATTR_STATUS: data.get("state"),
            ATTR_STATUS_CODE: data.get("status_code"),
            ATTR_ORDER_CODE: data.get("order_code"),
            ATTR_RESTAURANT: data.get("restaurant"),
            "courier_name": courier.get("name") or None,
            "courier_phone": courier.get("phone") or None,
            "courier_vehicle": courier.get("vehicle") or None,
            "vendor_latitude": vendor.get("latitude"),
            "vendor_longitude": vendor.get("longitude"),
            "changed_at": data.get("changed_at"),
        }


class TrackerEtaSensor(_BaseTrackerSensor):
    """The ETA reported by the foodpanda app."""

    _attr_name = "預計送達"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> int | None:
        """Return the ETA in minutes."""
        return self._data.get("eta_minutes")


class TrackerEtaEstimateSensor(_BaseTrackerSensor):
    """The ETA estimated from the courier position."""

    _attr_name = "預估剩餘時間"
    _attr_icon = "mdi:timer-sand"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> int | None:
        """Return the estimated minutes."""
        return self._data.get("eta_estimate")


class TrackerDistanceSensor(_BaseTrackerSensor):
    """The distance of the courier to the delivery address."""

    _attr_name = "距離送達地點"
    _attr_icon = "mdi:map-marker-distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = "m"

    @property
    def native_value(self) -> float | None:
        """Return the distance in meters."""
        return self._data.get("distance_m")
