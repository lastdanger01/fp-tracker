"""Watches the delivery states and notifies on every change.

This is what makes the integration useful without opening the HA UI: the
moment the foodpanda app changes the order state, a notification goes
out. Every order is watched separately, so two orders in parallel do
not hide each other.
"""

from __future__ import annotations

import html
import logging

from homeassistant.core import HomeAssistant

from .coordinator import TrackerCoordinator

_LOGGER = logging.getLogger(__name__)

# The states worth notifying about. Going from "preparing" to "picked up"
# matters, but a poll that returns the same state twice does not.
NOTIFY_STATES = {"preparing", "accepted", "picked_up", "arrived", "delivered", "cancelled"}

# "notify.send_message" is an entity service: without a target it fails
# with "must contain at least one of entity_id, device_id, ...". The
# group service "notify.notify" reaches every notify service instead.
SERVICE_GROUP = "notify"


def escape_html(text: str) -> str:
    """Escape a text for telegram, which parses the message as HTML.

    Without this a restaurant called "湯包 & 豆漿" or a message that
    contains "<" makes telegram reject the whole message with
    "Can't parse entities".
    """
    return html.escape(str(text), quote=False)


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

    # The last known state of every order, so a real change can be told
    # apart from a poll that returned the same thing again.
    last_states: dict[str, str] = {}

    async def _notify(data: dict) -> None:
        """Send the notification."""
        state = data.get("state")
        if state not in NOTIFY_STATES:
            return
        try:
            await hass.services.async_call(
                "notify",
                SERVICE_GROUP,
                {
                    "message": escape_html(_build_message(data)),
                    "title": escape_html("foodpanda 配送狀態"),
                },
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("送出通知失敗：%s", err)

    async def _on_update() -> None:
        """Called after every coordinator refresh."""
        data = coordinator.data or {}
        orders = data.get("orders") or []

        seen: dict[str, str] = {}
        for order in orders:
            code = order.get("order_code")
            if not code:
                continue
            seen[code] = order.get("state")

        # Orders that are gone are forgotten, so a new order with the
        # same code later still counts as a change.
        for code in list(last_states):
            if code not in seen:
                last_states.pop(code, None)

        for code, state in seen.items():
            if last_states.get(code) == state:
                continue
            last_states[code] = state
            order = next((o for o in orders if o.get("order_code") == code), None)
            if order:
                await _notify(order)
    coordinator.async_add_listener(_on_update)
