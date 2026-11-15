"""The fp_tracker integration.

Tracks the live state of a foodpanda delivery: where the courier is,
what stage the order is in, and how long until it arrives.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .api import FoodpandaClient
from .const import (
    CONF_CLIENT_ID,
    CONF_DEVICE_TOKEN,
    CONF_LOCALCODE,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_ID,
    CONF_TOKEN,
    CONF_USER_SOURCE,
    DEFAULT_LOCALCODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    STATE_LABELS,
)
from .coordinator import TrackerCoordinator
from .notify import async_setup_notifications

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Optional(CONF_USERNAME): cv.string}, extra=vol.ALLOW_EXTRA)},
    extra=vol.ALLOW_EXTRA,
)

SERVICE_REFRESH = "refresh"
SERVICE_NOTIFY = "notify"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration from YAML. Nothing to do here."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up fp_tracker from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    data = {**entry.data, **entry.options}

    client = FoodpandaClient(
        hass,
        localcode=data.get(CONF_LOCALCODE, DEFAULT_LOCALCODE),
        token=data.get(CONF_TOKEN, ""),
        device_token=data.get(CONF_DEVICE_TOKEN, ""),
        refresh_token=data.get(CONF_REFRESH_TOKEN, ""),
        client_id=data.get(CONF_CLIENT_ID, ""),
        session_id=data.get(CONF_SESSION_ID, ""),
        user_source=data.get(CONF_USER_SOURCE, ""),
    )

    scan_interval = int(data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds()))

    coordinator = TrackerCoordinator(
        hass,
        client,
        home_lat=data.get("home_latitude"),
        home_lon=data.get("home_longitude"),
        scan_interval=scan_interval,
    )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    _register_services(hass)

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await async_setup_notifications(hass, coordinator)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


def _register_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _refresh(call: ServiceCall) -> None:
        """Force an immediate refresh of all trackers."""
        for coordinator in hass.data.get(DOMAIN, {}).values():
            await coordinator.async_request_refresh()

    async def _notify(call: ServiceCall) -> None:
        """Send the current delivery state as a notification."""
        message = call.data.get("message")
        title = call.data.get("title", "foodpanda 配送狀態")
        for coordinator in hass.data.get(DOMAIN, {}).values():
            data = coordinator.data or {}
            if not data.get("active"):
                continue
            if message is None:
                message = _build_message(data)
            await hass.services.async_call(
                "notify",
                "send_message",
                {"message": message, "title": title},
                blocking=False,
            )

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    hass.services.async_register(DOMAIN, SERVICE_NOTIFY, _notify)


def _build_message(data: dict) -> str:
    """Build a human readable message from the tracker data."""
    parts = [data.get("label") or STATE_LABELS.get(data.get("state"), "狀態未知")]

    if data.get("restaurant"):
        parts.append(f"餐廳：{data['restaurant']}")

    eta = data.get("eta_minutes")
    estimate = data.get("eta_estimate")
    if eta is not None:
        parts.append(f"預計 {eta} 分鐘送達")
    elif estimate is not None:
        parts.append(f"約 {estimate} 分鐘後送達")

    distance = data.get("distance_m")
    if distance is not None:
        parts.append(f"距離 {distance:.0f} 公尺")

    courier = (data.get("courier") or {}).get("name")
    if courier:
        parts.append(f"送餐者：{courier}")

    return "\n".join(parts)
