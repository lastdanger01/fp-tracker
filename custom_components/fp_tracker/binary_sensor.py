"""Binary sensor platform for the fp_tracker integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_ORDERS, STATE_ARRIVED, STATE_CANCELLED, STATE_DELIVERED
from .coordinator import TrackerCoordinator
from .entity import SlotEntity, slot_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fp_tracker binary sensors."""
    coordinator: TrackerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []
    for slot in range(MAX_ORDERS):
        entities.extend(
            [
                ActiveOrderSensor(coordinator, slot),
                CourierNearbySensor(coordinator, slot),
            ]
        )
    async_add_entities(entities)


class _BaseBinary(SlotEntity, BinarySensorEntity):
    """Common base for the binary sensors."""

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_device_info = slot_device_info(slot)


class ActiveOrderSensor(_BaseBinary):
    """True while an order is in progress."""

    _attr_name = "配送中"
    _attr_icon = "mdi:food-delivery"

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_active_{slot}"

    @property
    def is_on(self) -> bool:
        """Return True while this slot holds an order in progress."""
        data = self.slot_data
        if not data:
            return False
        # The slot keeps the order for one more poll after it was
        # delivered, so that the "delivered" state is visible. That
        # order is not in progress any more.
        return data.get("state") not in (STATE_DELIVERED, STATE_CANCELLED)


class CourierNearbySensor(_BaseBinary):
    """True when the courier is close to the delivery address."""

    _attr_name = "送餐者已接近"
    _attr_icon = "mdi:map-marker-radius"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot)
        self._attr_unique_id = f"{DOMAIN}_nearby_{slot}"

    @property
    def is_on(self) -> bool:
        """Return True when the courier is near."""
        data = self.slot_data
        return bool(data) and data.get("state") == STATE_ARRIVED
