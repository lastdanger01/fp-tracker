"""Data update coordinator for the fp_tracker integration.

The coordinator keeps the state of every order that is currently in
progress. There can be more than one of those at a time, so the data
is a list of orders, oldest first.
"""

from __future__ import annotations

import logging
import time
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
    _extract_products,
    _parse_api_datetime,
    courier_distance_m,
    derive_state,
    estimate_eta_minutes,
    summarize_products,
)
from .const import (
    ARRIVED_DISTANCE_M,
    DOMAIN,
    FINISHED_RETENTION_SECONDS,
    STATE_ARRIVED,
    STATE_DELIVERED,
    STATE_LABELS,
    STATE_PICKED_UP,
    STATE_PREPARING,
)
from .credentials import (
    async_load_shared_credentials,
    async_save_credentials,
    async_save_shared_credentials,
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


def _is_active(order: dict) -> bool:
    """Return True when the order is still in progress."""
    flags = order.get("status_flags") or {}
    if not isinstance(flags, dict):
        return False
    if flags.get("is_canceled") or flags.get("is_delivered"):
        return False
    if flags.get("is_completed"):
        return False
    if flags.get("is_active"):
        return True

    # Some orders are marked active only by their status code, so fall
    # back to the state derived from the flags.
    return derive_state(order) in (
        STATE_PREPARING,
        "accepted",
        STATE_PICKED_UP,
        STATE_ARRIVED,
    )


def _is_finished(order: dict) -> bool:
    """Return True when the order is done, cancelled or not."""
    flags = order.get("status_flags") or {}
    if not isinstance(flags, dict):
        return False
    if flags.get("is_canceled"):
        return True
    return bool(flags.get("is_delivered") or flags.get("is_completed"))


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
        account: str | None = None,
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
        self.account = account

        # The order codes that were seen in progress. An order that
        # leaves this set has just been finished, and it is reported
        # as "delivered" for a while so the state sensor and the
        # notification get the last state before the slot is released.
        self._seen_active: set[str] = set()
        self._reported_finished: dict[str, float] = {}

    async def _async_fetch_orders(self) -> list[dict]:
        """Return the active orders, refreshing the token when needed."""
        # The credentials are shared with the older foodpanda
        # integration. A refresh token can only be used once, so both
        # integrations have to use the same copy, otherwise they
        # invalidate each other.
        await self._async_adopt_shared_credentials()

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

    async def _async_adopt_shared_credentials(self) -> None:
        """Take over the credentials of the shared credential file.

        The older foodpanda integration refreshes the access token
        every cycle and writes it back to `foodpanda/tokens.json`.
        Reading that file instead of keeping a second copy means only
        one refresh token exists in the whole system.
        """
        try:
            shared = await async_load_shared_credentials(self.hass, self.account)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("讀取共用憑證失敗：%s", err)
            return

        if not shared:
            return

        # Only take over credentials that are actually usable.
        if not shared.get("token"):
            return

        if shared.get("token") == self.client.token:
            return

        self.client.token = shared.get("token") or self.client.token
        self.client.device_token = (
            shared.get("device_token") or self.client.device_token
        )
        self.client.refresh_token = (
            shared.get("refresh_token") or self.client.refresh_token
        )
        self.client.client_id = shared.get("client_id") or self.client.client_id
        self.client.session_id = shared.get("session_id") or self.client.session_id
        self.client.user_source = shared.get("user_source") or self.client.user_source
        self.client.x_device = shared.get("x_device") or self.client.x_device
        self.client.token_timeout = shared.get("token_timeout") or 0

    async def _async_persist_credentials(self) -> None:
        """Write the refreshed credentials back.

        The refreshed access token is short lived, so without this the
        integration would come back with an expired token after every
        restart of Home Assistant. The store is used instead of the
        config entry because writing to the config entry would trigger
        the update listener and tear down the running coordinator.

        The credentials are written into the shared file as well, so
        the older integration picks them up instead of refreshing the
        same device token a second time.
        """
        credentials = self.client.async_get_credentials()
        try:
            await async_save_shared_credentials(
                self.hass, credentials, self.account
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("寫回共用憑證失敗：%s", err)

        if self.entry is None:
            return
        try:
            await async_save_credentials(
                self.hass, self.entry.entry_id, credentials
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("寫回憑證失敗：%s", err)

    async def _async_update_data(self) -> dict:
        """Fetch the current delivery states of every active order."""
        orders = await self._async_fetch_orders()

        # An order that just finished is still in the order history.
        # It is reported once as "delivered" so the state sensor and
        # the notification get the last state before the slot is
        # released.
        finished = await self._async_collect_finished(orders)

        slots: list[dict] = []
        for order in list(orders) + finished:
            if len(slots) >= self.max_orders:
                break
            slots.append(await self._async_build_slot(order))

        # Keep the slot order stable: the oldest order comes first.
        slots.sort(
            key=lambda s: _parse_api_datetime(s.get("ordered_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )

        return {
            "active": bool(orders),
            "count": len(orders),
            "orders": slots,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    async def _async_collect_finished(self, active: list[dict]) -> list[dict]:
        """Return the orders that just finished, once each.

        An order that was seen in progress and is now marked as
        delivered is reported one last time, so the "delivered"
        notification is not lost when the order leaves the active
        list.
        """
        try:
            history = getattr(self.client, "last_orders", None)
            if not history:
                history = await self.client.async_get_orders()
        except FoodpandaApiError as err:
            _LOGGER.debug("取訂單歷史失敗：%s", err)
            return []

        active_codes = {
            o.get("order_code") for o in active if o.get("order_code")
        }
        self._seen_active |= active_codes

        finished: list[dict] = []
        for order in history:
            code = order.get("order_code")
            if not code or code in active_codes:
                continue
            if code not in self._seen_active:
                continue
            if not _is_finished(order):
                continue
            if code not in self._reported_finished:
                self._reported_finished[code] = time.time()
            finished.append(order)

        # An order that has been reported is kept in its slot for a
        # while, so the "delivered" state stays visible instead of
        # flashing by for a single poll.
        now = time.time()
        for code, reported_at in list(self._reported_finished.items()):
            if now - reported_at > FINISHED_RETENTION_SECONDS:
                self._reported_finished.pop(code, None)
                self._seen_active.discard(code)

        return finished

    async def _async_build_slot(self, order: dict) -> dict:
        """Turn one API order into the data an entity needs."""
        order_code = order.get("order_code") or ""

        # The tracking endpoint carries the live courier position, the
        # order history does not.
        tracking: dict = {}
        if order_code:
            try:
                tracking = await self.client.async_get_tracking(order_code)
            except FoodpandaApiError as err:
                _LOGGER.debug("取 tracking 失敗：%s", err)

        merged = {**order, **tracking}

        # The state is derived from the merged data: the tracking
        # endpoint knows about the pickup, the order history does not.
        state = derive_state(merged)

        courier = _extract_courier(merged)
        vendor = merged.get("vendor") or {}
        if not isinstance(vendor, dict):
            vendor = {}

        # The order content: what was ordered and how much of it.
        products = _extract_products(merged)
        summary, item_count = summarize_products(products)

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
            STATE_DELIVERED,
            "cancelled",
        ):
            state = STATE_ARRIVED
        elif state in (STATE_PREPARING, "accepted"):
            # The flags lag behind the position: a courier that is on
            # the road has a position already, while the order still
            # says it is being prepared. A courier that is closer to
            # the customer than to the restaurant has picked the order
            # up.
            state = self._state_from_position(state, courier, address, vendor)

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
            "products": products,
            "products_summary": summary,
            "item_count": item_count,
            "ordered_at": (
                _parse_api_datetime(merged.get("ordered_at")).isoformat()
                if _parse_api_datetime(merged.get("ordered_at"))
                else None
            ),
        }

    def _state_from_position(
        self,
        state: str,
        courier: dict,
        address: dict,
        vendor: dict,
    ) -> str:
        """Return the state that the courier position implies.

        The API flags can lag behind the courier position by a poll or
        two. A courier that is on the road towards the customer, while
        the order still says it is being prepared, has already picked
        the order up.
        """
        lat = courier.get("latitude")
        lon = courier.get("longitude")
        if lat is None or lon is None:
            return state

        to_customer = None
        if address["latitude"] is not None and address["longitude"] is not None:
            to_customer = courier_distance_m(
                courier, [(address["latitude"], address["longitude"])]
            )

        to_restaurant = None
        vlat = vendor.get("latitude")
        vlon = vendor.get("longitude")
        if vlat is not None and vlon is not None:
            to_restaurant = courier_distance_m(courier, [(float(vlat), float(vlon))])

        if to_customer is None:
            return state

        if to_restaurant is not None and to_customer < to_restaurant:
            # The courier is much closer to the customer than to the
            # restaurant, so the order is on its way.
            return STATE_PICKED_UP

        if to_customer <= ARRIVED_DISTANCE_M:
            return STATE_ARRIVED

        return state
