"""Data update coordinator for the fp_tracker integration.

The coordinator keeps the state of every order that is currently in
progress. There can be more than one of those at a time, so the data
is a list of orders, oldest first.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    FoodpandaApiError,
    FoodpandaClient,
    _extract_courier,
    _extract_delivery_address,
    _extract_eta_minutes,
    _parse_api_datetime,
    courier_distance_m,
    derive_state,
    estimate_eta_minutes,
)
from .const import (
    ARRIVED_DISTANCE_M,
    DOMAIN,
    STATE_ARRIVED,
    STATE_LABELS,
)
from .credentials import async_save_credentials

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


def _empty_order() -> dict:
    """Return the shape of an order slot that has no order in it."""
    return {
        "order_code": None,
        "state": None,
        "label": None,
        "status_code": None,
        "restaurant": "",
        "courier": {},
        "eta_minutes": None,
        "eta_estimate": None,
        "distance_m": None,
        "latitude": None,
        "longitude": None,
        "vendor_latitude": None,
        "vendor_longitude": None,
        "delivery_latitude": None,
        "delivery_longitude": None,
        "changed_at": None,
    }


class TrackerCoordinator(DataUpdateCoordinator):
    """Poll the foodpanda API and keep the current delivery states."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: FoodpandaClient,
        entry: ConfigEntry | None = None,
        home_lat: float | None = None,
        home_lon: float | None = None,
        scan_interval: int = 30,
        max_orders: int = 3,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry = entry
        self.home_lat = home_lat
        self.home_lon = home_lon
        self.max_orders = max_orders

    async def _async_fetch_orders(self) -> list[dict]:
        """Return the active orders, refreshing the token when needed."""
        try:
            return await self.client.async_get_active_orders()
        except FoodpandaApiError as err:
            # A dead token is worth one refresh attempt before giving up.
            if "過期" in str(err):
                if await self.client.async_refresh_token():
                    await self._async_persist_credentials()
                    try:
                        return await self.client.async_get_active_orders()
                    except FoodpandaApiError as err2:
                        raise UpdateFailed(str(err2)) from err2
                raise UpdateFailed(str(err)) from err
            raise UpdateFailed(str(err)) from err

    async def _async_persist_credentials(self) -> None:
        """Write the refreshed credentials to the credential store.

        The access token is short lived, so without this the
        integration would come back with an expired token after every
        restart of Home Assistant. The store is used instead of the
        config entry because writing to the config entry would trigger
        the update listener and tear down the running coordinator.
        """
        if self.entry is None:
            return
        try:
            await async_save_credentials(
                self.hass, self.entry.entry_id, self.client.async_get_credentials()
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("寫回憑證失敗：%s", err)

    async def _async_update_data(self) -> dict:
        """Fetch the current delivery states of every active order."""
        orders = await self._async_fetch_orders()

        slots: list[dict] = []
        for order in orders[: self.max_orders]:
            slots.append(await self._async_build_slot(order))

        return {
            "active": bool(slots),
            "count": len(slots),
            "orders": slots,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    async def _async_build_slot(self, order: dict) -> dict:
        """Turn one API order into the data an entity needs."""
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

        # The courier is heading to the delivery address, so that is the
        # point the distance is measured to. The home coordinates from
        # the config entry are only used when the API has no address.
        address = _extract_delivery_address(merged)
        targets: list[tuple[float, float]] = []
        if address["latitude"] is not None and address["longitude"] is not None:
            targets.append((address["latitude"], address["longitude"]))
        if self.home_lat is not None and self.home_lon is not None:
            targets.append((self.home_lat, self.home_lon))
        if not targets and vendor_lat is not None and vendor_lon is not None:
            targets.append((float(vendor_lat), float(vendor_lon)))

        distance = courier_distance_m(courier, targets)
        eta_estimate = estimate_eta_minutes(distance)
        eta_minutes = _extract_eta_minutes(merged)

        # A courier that is close enough counts as arrived even when the
        # API has not flipped the flag yet.
        if distance is not None and distance <= ARRIVED_DISTANCE_M and state not in (
            "delivered",
            "cancelled",
        ):
            state = STATE_ARRIVED

        changed_at = (
            _parse_api_datetime(status.get("changedAt")) if isinstance(status, dict) else None
        )

        return {
            "order_code": order_code,
            "state": state,
            "label": STATE_LABELS.get(state, state),
            "status_code": status_code,
            "restaurant": vendor.get("name") or "",
            "courier": courier,
            "eta_minutes": eta_minutes,
            "eta_estimate": eta_estimate,
            "distance_m": distance,
            "latitude": courier.get("latitude"),
            "longitude": courier.get("longitude"),
            "vendor_latitude": vendor_lat,
            "vendor_longitude": vendor_lon,
            "delivery_latitude": address["latitude"],
            "delivery_longitude": address["longitude"],
            "changed_at": changed_at.isoformat() if changed_at else None,
        }
