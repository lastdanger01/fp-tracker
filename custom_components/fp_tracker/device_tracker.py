"""Device tracker platform for the fp_tracker integration.

The courier position is exposed as a device tracker so the HA map card
can show the courier moving towards the delivery address. Every order
slot gets its own tracker.
"""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_ORDER_CODE, ATTR_RESTAURANT, DOMAIN, MAX_ORDERS
from .coordinator import TrackerCoordinator
from .entity import SlotEntity, slot_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fp_tracker device trackers."""
    coordinator: TrackerCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [CourierTracker(coordinator, slot) for slot in range(MAX_ORDERS)]
    )


class CourierTracker(SlotEntity, TrackerEntity):
    """Track the courier position of one order."""

    _attr_name = "送餐者"

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_courier_{slot}"
        self._attr_device_info = slot_device_info(slot)

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude of the courier."""
        data = self.slot_data
        return data.get("latitude") if data else None

    @property
    def longitude(self) -> float | None:
        """Return the longitude of the courier."""
        data = self.slot_data
        return data.get("longitude") if data else None

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy in meters."""
        return 100

    @property
    def extra_state_attributes(self) -> dict:
        """Return the courier and order details."""
        data = self.slot_data
        if not data:
            return {}
        courier = data.get("courier") or {}
        return {
            "courier_name": courier.get("name") or None,
            "courier_phone": courier.get("phone") or None,
            "courier_vehicle": courier.get("vehicle") or None,
            ATTR_ORDER_CODE: data.get("order_code"),
            ATTR_RESTAURANT: data.get("restaurant"),
            "distance_meters": data.get("distance_m"),
            "eta_estimate_minutes": data.get("eta_estimate"),
            "eta_minutes": data.get("eta_minutes"),
        }
