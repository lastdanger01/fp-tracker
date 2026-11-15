"""foodpanda API client for the fp_tracker integration.

Everything runs through `requests` in an executor: the bot protection in
front of the foodpanda API rejects the TLS fingerprint of aiohttp with
HTTP 403 and a captcha challenge.

The response shapes that matter were verified against the live API:

- the order list lives in `data["items"]`, not `data["orders"]`
- the delivery state lives in `current_status["message"]`, not `status`
- the machine readable state lives in `status_flags`
- the courier position lives in `courier` and is only present while the
  order is in progress
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

import requests

from .const import (
    API_HOSTS,
    AVERAGE_SPEED_KMH,
    CONF_DEVICE_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN,
    CONF_TOKEN_TIMEOUT,
    CONF_X_DEVICE,
    DEFAULT_X_DEVICE,
    LOGIN_HOSTS,
    REQUEST_TIMEOUT,
    STATE_ACCEPTED,
    STATE_ARRIVED,
    STATE_CANCELLED,
    STATE_DELIVERED,
    STATE_PICKED_UP,
    STATE_PREPARING,
    STATE_UNKNOWN,
    USER_AGENT_DESKTOP,
    USER_AGENT_MOBILE,
)

_LOGGER = logging.getLogger(__name__)

# Distance between two points on the earth surface, in meters.
EARTH_RADIUS_M = 6_371_000.0

# Used as the sort key for orders without a usable timestamp.
_MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great circle distance between two points in meters."""
    from math import asin, cos, radians, sin, sqrt

    p1, p2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(p1) * cos(p2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def _parse_api_datetime(value: Any) -> datetime | None:
    """Parse the API's datetime shape into an aware datetime.

    The API returns `{"date": "2026-11-15 08:39:14", "timezone": "Asia/Taipei"}`.
    """
    if isinstance(value, dict):
        raw = value.get("date")
        tz_name = value.get("timezone")
        offset = value.get("date_timezone_offset")
    else:
        raw = value
        tz_name = None
        offset = None

    if not raw:
        return None

    if offset:
        try:
            return datetime.fromisoformat(offset)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None

    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return parsed.replace(tzinfo=ZoneInfo(tz_name))
        except Exception:  # noqa: BLE001 - zoneinfo may be missing on the host
            pass

    return parsed.replace(tzinfo=timezone.utc)


def derive_state(order: dict) -> str:
    """Derive the delivery state from the API's status flags.

    The flags are stable across app versions, the human readable
    `current_status.message` is not.
    """
    flags = order.get("status_flags") or {}
    if not isinstance(flags, dict):
        return STATE_UNKNOWN

    if flags.get("is_canceled"):
        return STATE_CANCELLED
    if flags.get("is_delivered") or flags.get("is_completed"):
        return STATE_DELIVERED
    # The tracking endpoint reports the pickup with its own flag. It is
    # the only place where it shows up, so it has to be checked too.
    if flags.get("is_picked_up") or flags.get("is_picked_up_by_rider"):
        return STATE_PICKED_UP

    blob = _status_blob(order)

    # The flags do not always keep up with the status message. A courier
    # that already picked the order up can still have
    # `is_picked_up: false` for a while, so the message is checked too.
    if "PICKED" in blob or "ON_THE_WAY" in blob or "ON_WAY" in blob:
        return STATE_PICKED_UP
    if "ARRIVED" in blob:
        return STATE_ARRIVED
    if "DELIVERED" in blob:
        return STATE_DELIVERED
    if flags.get("is_active"):
        # An active order that was not picked up yet is still at the
        # restaurant. The API does not tell apart "accepted" from
        # "preparing" through the flags, so the status strings are
        # checked for that distinction.
        #
        # Two different vocabularies are in play at the same time:
        #
        # - the order history says "RESTAURANTS_OTP_OD_PROGRESS_PREPARING"
        # - the tracking endpoint says
        #   "shop.order.status.message_order_accepted_by_vendor"
        #
        # Both describe the same phase, and the history uses the
        # "PREPARING" wording, so it wins.
        if "PREPARING" in blob or "COOKING" in blob:
            return STATE_PREPARING
        if "ACCEPTED_BY_VENDOR" in blob:
            # The tracking endpoint labels the very same phase as
            # "accepted by vendor" while the order history calls it
            # "preparing". They share the status code, so this is the
            # same moment, not an earlier one.
            return STATE_PREPARING
        if "ACCEPTED" in blob:
            return STATE_ACCEPTED
        return STATE_PREPARING
    return STATE_UNKNOWN


def _status_blob(order: dict) -> str:
    """Return the upper cased status strings of an order.

    The order history and the tracking endpoint describe the same
    phase with different wording, so both are collected and searched
    together.
    """
    parts: list[str] = []
    for key in ("current_status", "status"):
        value = order.get(key)
        if isinstance(value, dict):
            for field in ("message", "internal_status_code"):
                text = value.get(field)
                if isinstance(text, str):
                    parts.append(text)
        elif isinstance(value, str):
            parts.append(value)

    for entry in order.get("status_history") or []:
        if isinstance(entry, dict):
            for field in ("message", "internal_status_code"):
                text = entry.get(field)
                if isinstance(text, str):
                    parts.append(text)

    return " ".join(parts).upper()


def _extract_eta_minutes(order: dict) -> int | None:
    """Return the ETA in minutes reported by the API.

    The API exposes the ETA in several places and not all of them are
    filled in:

    - `eta` is an integer number of minutes, the most reliable one
    - `delivery_time_range.median` is a string, empty while the order is
      still being prepared
    - `delivery_time_range.lower` / `upper` are the bounds of the range
    - `live_activity.delivery_time.range` looks like "10 — 20"

    They are tried in that order.
    """
    eta = order.get("eta")
    if isinstance(eta, (int, float)) and not isinstance(eta, bool):
        return int(eta)
    if isinstance(eta, str) and eta.strip():
        try:
            return int(eta.strip())
        except ValueError:
            pass

    eta_range = order.get("delivery_time_range")
    if isinstance(eta_range, dict):
        for field in ("median", "lower", "upper"):
            value = eta_range.get(field)
            try:
                if value not in (None, ""):
                    return int(value)
            except (TypeError, ValueError):
                continue

    live = order.get("live_activity")
    if isinstance(live, dict):
        for key in ("delivery_time", "compact_delivery_time"):
            block = live.get(key)
            if not isinstance(block, dict):
                continue
            value = block.get("range")
            if not isinstance(value, str):
                continue
            # "10 — 20" or "10 - 20" -> take the lower bound.
            head = value.split("—")[0].split("-")[0].strip()
            try:
                if head:
                    return int(head)
            except ValueError:
                continue

    return None


def _extract_delivery_address(order: dict) -> dict:
    """Return the latitude and longitude of the delivery address."""
    for key in ("delivery_address", "order_address", "last_delivery_address"):
        address = order.get(key)
        if not isinstance(address, dict):
            continue
        lat = address.get("latitude")
        lon = address.get("longitude")
        try:
            if lat is not None and lon is not None:
                return {"latitude": float(lat), "longitude": float(lon)}
        except (TypeError, ValueError):
            continue
    return {"latitude": None, "longitude": None}


def _extract_courier(order: dict) -> dict:
    """Pull the courier details out of an order, whatever shape they have."""
    courier = order.get("courier")
    if not isinstance(courier, dict):
        return {}

    lat = courier.get("latitude")
    lon = courier.get("longitude")
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat = lon = None

    # A courier without a real position is reported as (0, 0), which is
    # a point in the ocean off the coast of Africa. Taking it for a
    # position would put the courier half a world away and produce a
    # distance of millions of meters, so it is treated as no position.
    if lat == 0 and lon == 0:
        lat = lon = None

    vehicle = courier.get("vehicle_type") or courier.get("vehicle") or ""

    return {
        "name": courier.get("name") or "",
        "phone": courier.get("phone") or courier.get("phone_number") or "",
        "vehicle": vehicle if isinstance(vehicle, str) else "",
        "latitude": lat,
        "longitude": lon,
    }


def _extract_products(order: dict) -> list[dict]:
    """Return the products of an order with their quantity and price.

    The same product can appear more than once in the list, once per
    line of the order, so the lines are kept as they are and the
    summary merges them.
    """
    products: list[dict] = []
    for item in order.get("order_products") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        if not name:
            continue
        try:
            quantity = int(item.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1
        try:
            price = float(item.get("total_price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        products.append(
            {"name": name, "quantity": quantity, "price": round(price)}
        )
    return products


def summarize_products(products: list[dict]) -> tuple[str, int]:
    """Return a one line summary of the products and the item count.

    The same product on several lines of the order is merged, so three
    lines of the same dessert read as "dessert x3".
    """
    merged: dict[str, dict] = {}
    total = 0
    for product in products:
        name = product.get("name") or ""
        if not name:
            continue
        quantity = int(product.get("quantity") or 0)
        total += quantity
        if name in merged:
            merged[name]["quantity"] += quantity
        else:
            merged[name] = {
                "name": name,
                "quantity": quantity,
                "price": product.get("price", 0),
            }
    summary = "、".join(
        f"{item['name']}×{item['quantity']}" for item in merged.values()
    )
    return summary, total


class FoodpandaApiError(Exception):
    """Raised when the foodpanda API cannot be used.

    The HTTP status code is kept so the caller can tell the failure
    modes apart. They used to collapse into a single "token expired"
    message, which sent people looking in the wrong place: a 401 means
    the token really is dead, a 403 means the request was rejected
    (usually a missing user source or the bot protection), and a
    connection error means neither of those.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        """Store the message and the HTTP status code."""
        super().__init__(message)
        self.status = status


class FoodpandaClient:
    """Small synchronous client for the foodpanda order API."""

    def __init__(
        self,
        hass: Any,
        localcode: str = "tw",
        token: str = "",
        device_token: str = "",
        refresh_token: str = "",
        client_id: str = "",
        session_id: str = "",
        user_source: str = "",
        x_device: str = "",
        token_timeout: int = 0,
    ) -> None:
        self.hass = hass
        self.localcode = localcode if localcode in API_HOSTS else "tw"
        self.token = token
        self.device_token = device_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.session_id = session_id
        self.user_source = user_source
        self.x_device = x_device or DEFAULT_X_DEVICE
        self.token_timeout = token_timeout

        # The order history of the last call, so the caller can pick
        # the finished orders out of it without asking again.
        self.last_orders: list[dict] = []

    @property
    def api_base(self) -> str:
        """Return the API base URL for the configured country."""
        return API_HOSTS[self.localcode]

    @property
    def login_base(self) -> str:
        """Return the login base URL for the configured country."""
        return LOGIN_HOSTS.get(self.localcode, LOGIN_HOSTS["tw"])

    def _headers(self, user_agent: str = USER_AGENT_MOBILE) -> dict[str, str]:
        """Build the request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.user_source:
            headers["X-Fp-Api-Key"] = self.user_source
        if self.client_id:
            headers["Perseus-Client-Id"] = self.client_id
        if self.session_id:
            headers["Perseus-Session-Id"] = self.session_id
        return headers

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """Perform a GET request in an executor and return the parsed body."""

        def _call() -> requests.Response:
            return requests.get(
                f"{self.api_base}{path}",
                headers=self._headers(),
                params=params,
                verify=False,
                timeout=REQUEST_TIMEOUT,
            )

        response = await self.hass.async_add_executor_job(_call)

        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise FoodpandaApiError(
                "token 已過期，請重新登入", status=response.status_code
            )
        if response.status_code == HTTPStatus.FORBIDDEN:
            # 403 is not an expired token: the request was rejected. It
            # happens when the user source header is missing, and when
            # the bot protection in front of the API answers.
            raise FoodpandaApiError(
                "API 拒絕了這個請求（HTTP 403）", status=response.status_code
            )
        if response.status_code != HTTPStatus.OK:
            raise FoodpandaApiError(
                f"HTTP {response.status_code}", status=response.status_code
            )

        try:
            return response.json()
        except ValueError as err:
            raise FoodpandaApiError("回應不是合法的 JSON") from err

    async def async_get_orders(self) -> list[dict]:
        """Return the order history, newest first.

        The list is in `data["items"]`. The history is kept in
        `last_orders`, so a caller that needs both the active and the
        finished orders does not have to fetch it twice.
        """
        body = await self._get(
            "/api/v5/orders/order_history?include=order_products,order_details"
        )
        data = body.get("data") or {}
        items = data.get("items")
        if not isinstance(items, list):
            self.last_orders = []
            return []
        self.last_orders = items
        return items

    async def async_get_active_orders(self) -> list[dict]:
        """Return every order that is currently in progress.

        An order is in progress while it is active and neither delivered
        nor cancelled. There can be more than one of those at a time,
        for example when two orders are placed in a row.
        """
        orders = await self.async_get_orders()

        active: list[dict] = []
        for order in orders:
            flags = order.get("status_flags") or {}
            if not isinstance(flags, dict):
                continue
            if flags.get("is_canceled") or flags.get("is_delivered"):
                continue
            if flags.get("is_completed"):
                continue
            if flags.get("is_active"):
                active.append(order)
                continue

            # Some orders are marked active only by their status code, so
            # fall back to the state derived from the flags.
            if derive_state(order) in (STATE_PREPARING, STATE_ACCEPTED, STATE_PICKED_UP, STATE_ARRIVED):
                active.append(order)

        # Oldest first: the first order is the one that arrives first.
        # `ordered_at` is a dict, so it has to be parsed before it can
        # be compared.
        active.sort(key=lambda o: _parse_api_datetime(o.get("ordered_at")) or _MIN_TIME)
        return active

    async def async_get_active_order(self) -> dict | None:
        """Return the order that is currently in progress, if any.

        This is the oldest active order. Use `async_get_active_orders`
        to track every order at once.
        """
        active = await self.async_get_active_orders()
        return active[0] if active else None

    async def async_get_tracking(self, order_code: str) -> dict:
        """Return the live tracking data for one order."""
        body = await self._get(f"/api/v5/tracking/orders/{order_code}")
        data = body.get("data")
        if not isinstance(data, dict):
            return {}
        return data

    async def async_refresh_token(self) -> bool:
        """Refresh the access token. Returns True on success.

        The refresh rotates the refresh token, so the caller has to
        persist the new value. `async_get_credentials` returns the
        current credentials for that.
        """
        payload = {
            "country": self.localcode,
            "platform": "b2c",
            CONF_DEVICE_TOKEN: self.device_token,
            CONF_REFRESH_TOKEN: self.refresh_token,
        }
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "x-otp-method": "EMAIL",
            "x-device": self.x_device,
        }

        def _call(user_agent: str) -> requests.Response:
            return requests.post(
                f"{self.login_base}/refresh-token",
                data=json.dumps(payload),
                headers={**headers, "User-Agent": user_agent},
                verify=False,
                timeout=REQUEST_TIMEOUT,
            )

        last_status = 0
        for agent in (USER_AGENT_MOBILE, USER_AGENT_DESKTOP):
            try:
                response = await self.hass.async_add_executor_job(
                    functools.partial(_call, agent)
                )
            except requests.exceptions.RequestException as err:
                _LOGGER.warning("refresh token 失敗：%s", err)
                return False

            last_status = response.status_code

            if response.status_code == HTTPStatus.OK:
                cookies = _parse_set_cookies(response)
                self.token = cookies.get(CONF_TOKEN, {}).get(CONF_TOKEN, self.token)
                self.device_token = cookies.get(CONF_DEVICE_TOKEN, {}).get(
                    CONF_DEVICE_TOKEN, self.device_token
                )
                self.refresh_token = cookies.get(CONF_REFRESH_TOKEN, {}).get(
                    CONF_REFRESH_TOKEN, self.refresh_token
                )
                self.client_id = cookies.get("dhhPerseusGuestId", {}).get(
                    "dhhPerseusGuestId", self.client_id
                )
                self.session_id = cookies.get("dhhPerseusSessionId", {}).get(
                    "dhhPerseusSessionId", self.session_id
                )
                self.user_source = cookies.get("userSource", {}).get(
                    "userSource", self.user_source
                )
                expires = cookies.get(CONF_TOKEN, {}).get("expires", "")
                if expires:
                    self.token_timeout = _parse_cookie_expiry(expires)
                return True

        _LOGGER.warning("refresh token 失敗，HTTP %s", last_status)
        return False

    def async_get_credentials(self) -> dict:
        """Return the credentials, so they can be persisted.

        The access token is short lived, so a refreshed one has to be
        written back to the config entry. Without that the integration
        would run on an expired token after every restart.
        """
        return {
            CONF_TOKEN: self.token,
            CONF_DEVICE_TOKEN: self.device_token,
            CONF_REFRESH_TOKEN: self.refresh_token,
            CONF_CLIENT_ID: self.client_id,
            CONF_SESSION_ID: self.session_id,
            CONF_USER_SOURCE: self.user_source,
            CONF_LOCALCODE: self.localcode,
            CONF_X_DEVICE: self.x_device,
            CONF_TOKEN_TIMEOUT: self.token_timeout,
        }


def _parse_cookie_expiry(value: str) -> int:
    """Parse the expiry of a cookie into a unix timestamp.

    The foodpanda API uses the RFC 1123 format, for example
    "Sun, 15 Nov 2026 16:24:33 GMT".
    """
    from datetime import datetime, timezone

    try:
        parsed = datetime.strptime(value.strip(), "%a, %d %b %Y %H:%M:%S %Z")
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip(), "%a, %d %b %Y %H:%M:%S")
        except ValueError:
            return 0
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


def _parse_set_cookies(response: requests.Response) -> dict:
    """Parse the raw set-cookie headers into the shape the API uses.

    `requests`' cookie jar cannot be used here because the original
    integration expects a nested structure.
    """
    raw: list[str] = []
    try:
        raw = response.raw.headers.getlist("set-cookie")
    except Exception:  # noqa: BLE001
        raw = []

    if not raw:
        header = response.headers.get("set-cookie", "")
        raw = [header] if header else []

    cookies: dict[str, dict[str, str]] = {}
    for line in raw:
        parts = line.split(";")
        if not parts or "=" not in parts[0]:
            continue
        name, _, value = parts[0].partition("=")
        name = name.strip()
        if not name:
            continue
        expires = ""
        for part in parts[1:]:
            key, _, val = part.partition("=")
            if key.strip().lower() == "expires":
                expires = val.strip()
                break
        cookies[name] = {"name": name, name: value.strip(), "expires": expires}
    return cookies


def estimate_eta_minutes(distance_m: float | None) -> int | None:
    """Estimate the remaining minutes from the straight line distance."""
    if distance_m is None:
        return None
    minutes = (distance_m / 1000.0) / AVERAGE_SPEED_KMH * 60.0
    return max(1, int(round(minutes)))


def courier_distance_m(
    courier: dict, targets: list[tuple[float, float]]
) -> float | None:
    """Return the distance of the courier to the closest target point.

    The targets are the delivery address and, as a fallback, the
    restaurant. The courier is heading to the delivery address, so that
    is the distance that matters.
    """
    lat = courier.get("latitude")
    lon = courier.get("longitude")
    if lat is None or lon is None:
        return None

    best = None
    for tlat, tlon in targets:
        dist = _haversine_m(lat, lon, tlat, tlon)
        if best is None or dist < best:
            best = dist
    return best


async def async_sleep(seconds: float) -> None:
    """Sleep without blocking the event loop."""
    await asyncio.sleep(seconds)
