"""The fp_tracker integration.

Tracks the live state of a foodpanda delivery: where the courier is,
what stage the order is in, and how long until it arrives.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
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
    CONF_TOKEN_TIMEOUT,
    CONF_USER_SOURCE,
    CONF_USERNAME,
    CONF_X_DEVICE,
    DEFAULT_LOCALCODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_ORDERS,
    PLATFORMS,
    STATE_LABELS,
)
from .coordinator import TrackerCoordinator
from .credentials import (
    async_load_credentials,
    async_load_shared_credentials,
    shared_credentials_are_newer,
    stored_credentials_are_newer,
)
from .notify import SERVICE_GROUP, async_setup_notifications, escape_html

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Optional(CONF_USERNAME): cv.string}, extra=vol.ALLOW_EXTRA)},
    extra=vol.ALLOW_EXTRA,
)

SERVICE_REFRESH = "refresh"
SERVICE_NOTIFY = "notify"
SERVICE_STATS = "generate_stats"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration from YAML. Nothing to do here."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up fp_tracker from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    data = {**entry.data, **entry.options}

    # The refreshed credentials live in a store of their own, so they
    # survive a restart without touching the config entry.
    #
    # The store is only adopted when it holds the newer token. A token
    # the user just pasted into the options flow is newer than the
    # store, so the store must not throw it away.
    stored = await async_load_credentials(hass, entry.entry_id)
    if stored and stored_credentials_are_newer(stored, data):
        data.update({k: v for k, v in stored.items() if v})
    elif stored:
        _LOGGER.debug("憑證檔比較舊，保留目前的憑證")

    # The credentials are shared with the older foodpanda integration.
    # A refresh token can only be used once, so two integrations that
    # keep their own copy of it end up invalidating each other. The
    # shared file holds the one copy that both of them use.
    #
    # The shared file is only adopted when it holds the newer token.
    # A leftover file from an older installation can hold a token that
    # expired long ago, and taking it would throw away a good token
    # that the user just pasted into the config flow.
    account = str(data.get(CONF_USERNAME, "") or "") or None
    shared = await async_load_shared_credentials(hass, account)
    if shared and shared_credentials_are_newer(shared, data):
        data.update({k: v for k, v in shared.items() if v})
    elif shared:
        _LOGGER.debug("共用憑證檔比較舊，保留目前的憑證")

    client = FoodpandaClient(
        hass,
        localcode=data.get(CONF_LOCALCODE, DEFAULT_LOCALCODE),
        token=data.get(CONF_TOKEN, ""),
        device_token=data.get(CONF_DEVICE_TOKEN, ""),
        refresh_token=data.get(CONF_REFRESH_TOKEN, ""),
        client_id=data.get(CONF_CLIENT_ID, ""),
        session_id=data.get(CONF_SESSION_ID, ""),
        user_source=data.get(CONF_USER_SOURCE, ""),
        x_device=data.get(CONF_X_DEVICE, ""),
        token_timeout=int(data.get(CONF_TOKEN_TIMEOUT, 0) or 0),
    )

    scan_interval = int(data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds()))

    coordinator = TrackerCoordinator(
        hass,
        client,
        entry=entry,
        home_lat=data.get("home_latitude"),
        home_lon=data.get("home_longitude"),
        scan_interval=scan_interval,
        max_orders=MAX_ORDERS,
        account=account,
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
            for order in data.get("orders") or []:
                if message is None:
                    text = _build_message(order)
                else:
                    text = message
                await hass.services.async_call(
                    "notify",
                    SERVICE_GROUP,
                    {
                        "message": escape_html(text),
                        "title": escape_html(title),
                    },
                    blocking=False,
                )

    async def _generate_stats(call: ServiceCall) -> None:
        """Rebuild the spending statistics page from the order history."""
        from .stats import async_generate_stats

        account = call.data.get("account")
        try:
            result = await async_generate_stats(hass, account)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("產生 foodpanda 統計頁失敗：%s", err)
            raise

        message = (
            f"foodpanda 統計頁已更新\n"
            f"{result['count']} 筆訂單，合計 NT${result['total']:,}\n"
            f"{result['path']}"
        )
        # The page is already written at this point, so a notification
        # that fails must not turn the whole service call into an error.
        try:
            await hass.services.async_call(
                "notify",
                SERVICE_GROUP,
                {
                    "message": escape_html(message),
                    "title": escape_html("foodpanda 餐費分析"),
                },
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("統計頁已產生，但通知送出失敗：%s", err)

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    hass.services.async_register(DOMAIN, SERVICE_NOTIFY, _notify)
    hass.services.async_register(DOMAIN, SERVICE_STATS, _generate_stats)


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
