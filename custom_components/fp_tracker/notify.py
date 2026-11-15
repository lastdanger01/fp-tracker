"""Watches the delivery state and notifies on every change.

This is what makes the integration useful without opening the HA UI: the
moment the foodpanda app changes the order state, a notification goes
out.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .coordinator import TrackerCoordinator

_LOGGER = logging.getLogger(__name__)

# The states worth notifying about. Going from "preparing" to "picked up"
# matters, but a poll that returns the same state twice does not.
NOTIFY_STATES = {"preparing", "accepted", "picked_up", "arrived", "delivered", "cancelled"}


def _build_message(data: dict) -> str:
    """Build the notification text."""
    label = data.get("label") or "狀態更新"
    lines = [label]

    if data.get("restaurant"):
        lines.append(f"餐廳：{data['restaurant']}")

    eta = data.get("eta_minutes")
    estimate = data.get("eta_estimate")
    if eta is not None:
        lines.append(f"預計 {eta} 分鐘送達")
    elif estimate is not None:
        lines.append(f"約 {estimate} 分鐘後送達")

    distance = data.get("distance_m")
    if distance is not None:
        lines.append(f"距離 {distance:.0f} 公尺")

    courier = (data.get("courier") or {}).get("name")
    if courier:
        lines.append(f"送餐者：{courier}")

    return "\n".join(lines)


async def async_setup_notifications(hass: HomeAssistant, coordinator: TrackerCoordinator) -> None:
    """Set up the state change notifications for one coordinator."""

    async def _notify(data: dict) -> None:
        """Send the notification."""
        if not data.get("active"):
            return
        state = data.get("state")
        if state not in NOTIFY_STATES:
            return
        try:
            await hass.services.async_call(
                "notify",
                "send_message",
                {
                    "message": _build_message(data),
                    "title": "foodpanda 配送狀態",
                },
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("送出通知失敗：%s", err)

    async def _on_update() -> None:
        """Called after every coordinator refresh."""
        data = coordinator.data or {}
        state = data.get("state")
        order_code = data.get("order_code")

        # Notify when the state changed, or when a new order started.
        changed = state != coordinator.last_state or order_code != coordinator.last_order_code

        coordinator.last_state = state
        coordinator.last_order_code = order_code

        if changed:
            await _notify(data)

    coordinator.async_add_listener(_on_update)
