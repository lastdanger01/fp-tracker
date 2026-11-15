"""Shared base classes for the fp_tracker entities.

Every order gets its own slot. The slots are fixed in number, so the
entities always exist and their entity ids stay stable. A slot without
an order in it is simply unavailable.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SLOT_DEVICE_NAME
from .coordinator import TrackerCoordinator


class SlotEntity(CoordinatorEntity[TrackerCoordinator]):
    """An entity that belongs to one order slot."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TrackerCoordinator, slot: int) -> None:
        super().__init__(coordinator)
        self._slot = slot

    @property
    def slot_data(self) -> dict | None:
        """Return the data of this slot, or None when the slot is empty."""
        data = self.coordinator.data or {}
        orders = data.get("orders") or []
        if self._slot < len(orders):
            return orders[self._slot]
        return None

    @property
    def available(self) -> bool:
        """Return True when this slot holds an order."""
        return bool(self.coordinator.last_update_success) and self.slot_data is not None


def slot_device_info(slot: int) -> DeviceInfo:
    """Return the device info for one order slot."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{DOMAIN}_slot_{slot}")},
        name=f"{SLOT_DEVICE_NAME} {slot + 1}",
        manufacturer="foodpanda",
    )
