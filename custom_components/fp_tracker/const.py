"""Constants for the fp_tracker integration."""

from datetime import timedelta

DOMAIN = "fp_tracker"
PLATFORMS = ["binary_sensor", "device_tracker", "sensor"]

ATTR_ETA = "eta_minutes"
ATTR_STATUS = "status"
ATTR_STATUS_CODE = "status_code"
ATTR_RESTAURANT = "restaurant"
ATTR_COURIER_NAME = "courier_name"
ATTR_COURIER_PHONE = "courier_phone"
ATTR_COURIER_VEHICLE = "courier_vehicle"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_DISTANCE_M = "distance_meters"
ATTR_ETA_ESTIMATE = "eta_estimate_minutes"
ATTR_ORDER_CODE = "order_code"
ATTR_VENDOR_LAT = "vendor_latitude"
ATTR_VENDOR_LON = "vendor_longitude"
ATTR_HOME_LAT = "home_latitude"
ATTR_HOME_LON = "home_longitude"
ATTR_LAST_UPDATED = "last_updated"
ATTR_ACTIVE_ORDER = "active_order"

CONF_TOKEN = "token"
CONF_DEVICE_TOKEN = "device_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_CLIENT_ID = "client_id"
CONF_SESSION_ID = "session_id"
CONF_USER_SOURCE = "user_source"
CONF_LOCALCODE = "localcode"
CONF_USERNAME = "username"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_LOCALCODE = "tw"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

# The bot protection in front of the foodpanda API looks at the TLS
# fingerprint of the client. aiohttp is rejected with HTTP 403 and a
# captcha challenge, requests gets through. Both user agents are kept:
# the mobile one gets through where the desktop one is asked for a
# captcha, so the refresh tries the mobile one first.
USER_AGENT_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
USER_AGENT_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

API_HOSTS = {
    "tw": "https://tw.fd-api.com",
    "hk": "https://hk.fd-api.com",
    "sg": "https://sg.fd-api.com",
}
LOGIN_HOSTS = {
    "tw": "https://www.foodpanda.com.tw",
    "hk": "https://www.foodpanda.hk",
    "sg": "https://www.foodpanda.sg",
}

REQUEST_TIMEOUT = 25

# The API reports the delivery state through these flags. The order of the
# checks matters: an order that is already picked up is not "preparing"
# any more, and a delivered order is not active any more.
#
# The human readable state is derived from these flags because the
# `current_status.message` string changes between app versions
# ("shop.order.status.message_order_delivered" became
# "RESTAURANTS_OTP_OD_PROGRESS_DELIVERED"), while the flags are stable.
STATE_PREPARING = "preparing"
STATE_ACCEPTED = "accepted"
STATE_PICKED_UP = "picked_up"
STATE_ARRIVED = "arrived"
STATE_DELIVERED = "delivered"
STATE_CANCELLED = "cancelled"
STATE_UNKNOWN = "unknown"

STATE_LABELS = {
    STATE_PREPARING: "餐廳備餐中",
    STATE_ACCEPTED: "餐廳已接受訂單",
    STATE_PICKED_UP: "已取餐，配送中",
    STATE_ARRIVED: "送餐者已抵達",
    STATE_DELIVERED: "已送達",
    STATE_CANCELLED: "已取消",
    STATE_UNKNOWN: "狀態未知",
}

# Distance per hour used to estimate the remaining travel time when the
# API does not give an ETA. A foodpanda courier on a scooter covers
# roughly this distance in city traffic.
AVERAGE_SPEED_KMH = 25.0
# Below this distance the courier is considered to have arrived.
ARRIVED_DISTANCE_M = 80.0
