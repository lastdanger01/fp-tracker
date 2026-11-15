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
    ARRIVED_DISTANCE_M,
    AVERAGE_SPEED_KMH,
    CONF_CLIENT_ID,
    CONF_DEVICE_TOKEN,
    CONF_LOCALCODE,
    CONF_REFRESH_TOKEN,
    CONF_SESSION_ID,
    CONF_TOKEN,
    CONF_USER_SOURCE,
    LOGIN_HOSTS,
    REQUEST_TIMEOUT,
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
    if flags.get("is_picked_up"):
        return STATE_PICKED_UP
    if flags.get("is_active"):
        # An active order that was not picked up yet is still at the
        # restaurant. The API does not tell apart "accepted" from
        # "preparing" through the flags, so the status code is checked
        # for that distinction.
        code = (order.get("current_status") or {}).get("code")
        if code in (7, 8, 9, 10, 11):
            return STATE_PREPARING
        return STATE_PREPARING
    return STATE_UNKNOWN


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

    vehicle = courier.get("vehicle_type") or courier.get("vehicle") or ""

    return {
        "name": courier.get("name") or "",
        "phone": courier.get("phone") or "",
        "vehicle": vehicle if isinstance(vehicle, str) else "",
        "latitude": lat,
        "longitude": lon,
    }


class FoodpandaApiError(Exception):
    """Raised when the foodpanda API cannot be used."""


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
    ) -> None:
        self.hass = hass
        self.localcode = localcode if localcode in API_HOSTS else "tw"
        self.token = token
        self.device_token = device_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.session_id = session_id
        self.user_source = user_source

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
            raise FoodpandaApiError("token 已過期，請重新登入")
        if response.status_code != HTTPStatus.OK:
            raise FoodpandaApiError(f"HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as err:
            raise FoodpandaApiError("回應不是合法的 JSON") from err

    async def async_get_orders(self) -> list[dict]:
        """Return the order history, newest first.

        The list is in `data["items"]`.
        """
        body = await self._get(
            "/api/v5/orders/order_history?include=order_products,order_details"
        )
        data = body.get("data") or {}
        items = data.get("items")
        if not isinstance(items, list):
            return []
        return items

    async def async_get_active_order(self) -> dict | None:
        """Return the order that is currently in progress, if any.

        An order is in progress while it is active and neither delivered
        nor cancelled.
        """
        orders = await self.async_get_orders()

        for order in orders:
            flags = order.get("status_flags") or {}
            if not isinstance(flags, dict):
                continue
            if flags.get("is_canceled") or flags.get("is_delivered"):
                continue
            if flags.get("is_completed"):
                continue
            if flags.get("is_active"):
                return order

        # Some orders are marked active only by their status code, so
        # fall back to the state derived from the flags.
        for order in orders:
            state = derive_state(order)
            if state in (STATE_PREPARING, STATE_PICKED_UP, STATE_ARRIVED):
                return order

        return None

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
        persist the new value.
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
        }

        def _call(user_agent: str) -> requests.Response:
            return requests.post(
                f"{self.login_base}/refresh-token",
                data=json.dumps(payload),
                headers={**headers, "User-Agent": user_agent},
                verify=False,
                timeout=REQUEST_TIMEOUT,
            )

        for agent in (USER_AGENT_MOBILE, USER_AGENT_DESKTOP):
            try:
                response = await self.hass.async_add_executor_job(
                    functools.partial(_call, agent)
                )
            except requests.exceptions.RequestException as err:
                _LOGGER.warning("refresh token 失敗：%s", err)
                return False

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
                return True

        _LOGGER.warning("refresh token 失敗，HTTP %s", response.status_code)
        return False


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
    courier: dict, vendor: dict, home: tuple[float, float] | None
) -> float | None:
    """Return the distance of the courier to the delivery address."""
    lat = courier.get("latitude")
    lon = courier.get("longitude")
    if lat is None or lon is None:
        return None

    targets = []
    if home:
        targets.append(home)
    else:
        vlat = vendor.get("latitude")
        vlon = vendor.get("longitude")
        if vlat is not None and vlon is not None:
            targets.append((float(vlat), float(vlon)))

    if not targets:
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
