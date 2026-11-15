"""Data update coordinator for the fp_tracker integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    FoodpandaApiError,
    FoodpandaClient,
    courier_distance_m,
    derive_state,
    estimate_eta_minutes,
    _extract_courier,
    _parse_api_datetime,
)
from .const import (
    ATTR_ETA,
    ATTR_ETA_ESTIMATE,
    ATTR_LAST_UPDATED,
    ATTR_RESTAURANT,
    ATTR_STATUS,
    ATTR_STATUS_CODE,
    ATTR_VENDOR_LAT,
    ATTR_VENDOR_LON,
    CONF_CLIENT_ID,
    CONF_DEVICE_TOKEN,
    CONF_LOCALCODE,
    CONF_REFRESH_TOKEN,
    CONF_SESSION_ID,
    CONF_TOKEN,
    CONF_USER_SOURCE,
    DEFAULT_LOCALCODE,
    DOMAIN,
    STATE_ARRIVED,
    STATE_LABELS,
)

_LOGGER = logging.getLogger(__name__)


async def async_get_access_token(hass: HomeAssistant) -> str:
    """Return a usable access token for the HA API.

    The long lived token in MEMORY.md is often expired. A fresh one is
    fetched from the refresh token in the auth storage.
    """
    from homeassistant.helpers import storage

    store = storage.Store(hass, 1, "auth")
    data = await store.async_load()
    refresh_tokens = (data or {}).get("data", {}).get("refresh_tokens", {})

    if not refresh_tokens:
        raise UpdateFailed("找不到可用的 refresh token")

    # The owner token is the one that is not tied to a long lived
    # access token; try the shortest lived one first because those are
    # the normal session tokens.
    for token_info in refresh_tokens.values():
        refresh_token = token_info.get("refresh_token")
        if not refresh_token:
            continue
        try:
            resp = await hass.auth.async_refresh_access_token(refresh_token)
        except Exception:  # noqa: BLE001
            continue
        if resp and resp.get("access_token"):
            return resp["access_token"]

    raise UpdateFailed("無法取得有效的 access token")


class TrackerCoordinator(DataUpdateCoordinator):
    """Poll the foodpanda API and keep the current delivery state."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: FoodpandaClient,
        home_lat: float | None = None,
        home_lon: float | None = None,
        scan_interval: int = 30,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.home_lat = home_lat
        self.home_lon = home_lon
        self.last_state: str | None = None
        self.last_order_code: str | None = None

    async def _async_update_data(self) -> dict:
        """Fetch the current delivery state."""
        try:
            order = await self.client.async_get_active_order()
        except FoodpandaApiError as err:
            # A dead token is worth one refresh attempt before giving up.
            if "過期" in str(err):
                if await self.client.async_refresh_token():
                    try:
                        order = await self.client.async_get_active_order()
                    except FoodpandaApiError as err2:
                        raise UpdateFailed(str(err2)) from err2
                else:
                    raise UpdateFailed(str(err)) from err
            else:
                raise UpdateFailed(str(err)) from err

        if not order:
            self.last_state = None
            self.last_order_code = None
            return {
                "active": False,
                "state": None,
                "label": None,
                "order_code": None,
                "courier": {},
                "vendor": {},
                "eta_minutes": None,
                "eta_estimate": None,
                "distance_m": None,
                "latitude": None,
                "longitude": None,
                "vendor_latitude": None,
                "vendor_longitude": None,
                "status_code": None,
                "changed_at": None,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

        order_code = order.get("order_code") or ""
        state = derive_state(order)

        # The tracking endpoint carries the live courier position, the
        # order history does not.
        tracking: dict = {}
        if order_code:
            try:
                tracking = await self.client.async_get_tracking(order_code)
            except FoodpandaApiError as err:
                _LOGGER.debug("取 tracking 失敗：%s", err)

        merged = {**order, **tracking}
        courier = _extract_courier(merged)
        vendor = merged.get("vendor") or {}
        if not isinstance(vendor, dict):
            vendor = {}

        status = merged.get("current_status") or {}
        status_code = status.get("code") if isinstance(status, dict) else None

        vendor_lat = vendor.get("latitude")
        vendor_lon = vendor.get("longitude")

        home = None
        if self.home_lat is not None and self.home_lon is not None:
            home = (self.home_lat, self.home_lon)

        distance = courier_distance_m(courier, vendor, home)
        eta_estimate = estimate_eta_minutes(distance)

        eta_minutes = None
        eta_range = merged.get("delivery_time_range") or {}
        if isinstance(eta_range, dict):
            median = eta_range.get("median")
            try:
                if median not in (None, ""):
                    eta_minutes = int(median)
            except (TypeError, ValueError):
                eta_minutes = None
        if eta_minutes is None:
            live = merged.get("live_activity") or {}
            if isinstance(live, dict):
                delivery_time = live.get("delivery_time") or {}
                if isinstance(delivery_time, dict):
                    try:
                        if delivery_time.get("range") not in (None, ""):
                            eta_minutes = int(delivery_time["range"])
                    except (TypeError, ValueError):
                        eta_minutes = None

        # A courier that is close enough counts as arrived even when the
        # API has not flipped the flag yet.
        if distance is not None and distance <= ARRIVED_DISTANCE_M and state not in (
            "delivered",
            "cancelled",
        ):
            state = STATE_ARRIVED

        changed_at = _parse_api_datetime(status.get("changedAt")) if isinstance(status, dict) else None

        return {
            "active": True,
            "state": state,
            "label": STATE_LABELS.get(state, state),
            "order_code": order_code,
            "courier": courier,
            "vendor": {
                "name": vendor.get("name") or "",
                "latitude": vendor_lat,
                "longitude": vendor_lon,
            },
            "restaurant": vendor.get("name") or "",
            "eta_minutes": eta_minutes,
            "eta_estimate": eta_estimate,
            "distance_m": distance,
            "latitude": courier.get("latitude"),
            "longitude": courier.get("longitude"),
            "vendor_latitude": vendor_lat,
            "vendor_longitude": vendor_lon,
            "status_code": status_code,
            "changed_at": changed_at.isoformat() if changed_at else None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

