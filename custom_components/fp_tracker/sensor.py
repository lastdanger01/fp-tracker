"""Sensor platform for the fp_tracker integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ORDER_CODE,
    ATTR_RESTAURANT,
    ATTR_STATUS,
    ATTR_STATUS_CODE,
    DOMAIN,
    MAX_ORDERS,
)
from .coordinator import TrackerCoordinator
from .entity import SlotEntity, slot_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fp_tracker sensors."""
    coordinator: TrackerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for slot in range(MAX_ORDERS):
        entities.extend(
            [
                TrackerStateSensor(coordinator, slot),
                TrackerProductsSensor(coordinator, slot),
                TrackerEtaSensor(coordinator, slot),
                TrackerEtaEstimateSensor(coordinator, slot),
                TrackerDistanceSensor(coordinator, slot),
            ]
        )
    async_add_entities(entities)


class _BaseTrackerSensor(SlotEntity, SensorEntity):
    """Common base for the tracker sensors."""

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_attribution = "Powered by foodpanda"
        self._attr_device_info = slot_device_info(slot)

    @property
    def _data(self) -> dict | None:
        """Return the data of this slot."""
        return self.slot_data


class TrackerStateSensor(_BaseTrackerSensor):
    """The delivery state of the current order."""

    _attr_name = "配送狀態"
    _attr_icon = "mdi:food-delivery"

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_state_{slot}"

    @property
    def native_value(self) -> str | None:
        """Return the state label."""
        data = self._data
        return data.get("label") if data else None

    @property
    def extra_state_attributes(self) -> dict:
        """Return the order details."""
        data = self._data
        if not data:
            return {}
        courier = data.get("courier") or {}
        return {
            ATTR_STATUS: data.get("state"),
            ATTR_STATUS_CODE: data.get("status_code"),
            ATTR_ORDER_CODE: data.get("order_code"),
            ATTR_RESTAURANT: data.get("restaurant"),
            "courier_name": courier.get("name") or None,
            "courier_phone": courier.get("phone") or None,
            "courier_vehicle": courier.get("vehicle") or None,
            "vendor_latitude": data.get("vendor_latitude"),
            "vendor_longitude": data.get("vendor_longitude"),
            "delivery_latitude": data.get("delivery_latitude"),
            "delivery_longitude": data.get("delivery_longitude"),
            "changed_at": data.get("changed_at"),
        }


class TrackerProductsSensor(_BaseTrackerSensor):
    """What was ordered in this order slot."""

    _attr_name = "訂單內容"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_products_{slot}"

    @property
    def native_value(self) -> str | None:
        """Return the products of the order as one line."""
        data = self._data
        if not data:
            return None
        return data.get("products_summary") or None

    @property
    def extra_state_attributes(self) -> dict:
        """Return the products one by one."""
        data = self._data
        if not data:
            return {}
        return {
            "products": data.get("products") or [],
            "item_count": data.get("item_count", 0),
            "products_summary": data.get("products_summary"),
        }


class TrackerEtaSensor(_BaseTrackerSensor):
    """The ETA reported by the foodpanda app."""

    _attr_name = "預計送達"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_eta_{slot}"

    @property
    def native_value(self) -> int | None:
        """Return the ETA in minutes."""
        data = self._data
        return data.get("eta_minutes") if data else None


class TrackerEtaEstimateSensor(_BaseTrackerSensor):
    """The ETA estimated from the courier position."""

    _attr_name = "預估剩餘時間"
    _attr_icon = "mdi:timer-sand"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_eta_estimate_{slot}"

    @property
    def native_value(self) -> int | None:
        """Return the estimated minutes."""
        data = self._data
        return data.get("eta_estimate") if data else None


class TrackerDistanceSensor(_BaseTrackerSensor):
    """The distance of the courier to the delivery address."""

    _attr_name = "距離送達地點"
    _attr_icon = "mdi:map-marker-distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = "m"

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_distance_{slot}"

    @property
    def native_value(self) -> float | None:
        """Return the distance in meters."""
        data = self._data
        return data.get("distance_m") if data else None
