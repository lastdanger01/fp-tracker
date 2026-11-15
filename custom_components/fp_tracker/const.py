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
CONF_X_DEVICE = "x_device"
CONF_TOKEN_TIMEOUT = "token_timeout"
CONF_LOCALCODE = "localcode"
CONF_USERNAME = "username"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_LOCALCODE = "tw"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

# The API answers HTTP 403 when this header is missing, so the field is
# not optional in practice: the value itself does not matter, it only
# has to be there. The value is picked up from the cookies on the first
# refresh anyway.
DEFAULT_USER_SOURCE = "volo"

# How many orders are tracked at the same time. Every order gets its
# own set of entities in its own slot, so the entity ids stay stable
# and do not pile up in the entity registry.
MAX_ORDERS = 3

# The name of the device that holds the entities of one order slot.
SLOT_DEVICE_NAME = "foodpanda 訂單"

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
# The login and refresh endpoints live under `/login/new/api`. Without
# that path the API answers HTTP 404 ("Cannot POST /refresh-token").
LOGIN_HOSTS = {
    "tw": "https://www.foodpanda.com.tw/login/new/api",
    "hk": "https://www.foodpanda.hk/login/new/api",
    "sg": "https://www.foodpanda.com.sg/login/new/api",
}

REQUEST_TIMEOUT = 25

# The x-device header identifies the app installation. The foodpanda
# login API expects it, and the same device token belongs to it.
DEFAULT_X_DEVICE = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6ImtleW1ha2VyLXZvbG8tZGV2aWNlLWZwLXR3IiwidHlw"
    "IjoiSldUIn0.eyJpZCI6IjEzNTI4MjhjLWM0MTAtNDgyZi05ZjZkLTk2MGE4NDdiNzRkMCIs"
    "ImNsaWVudF9pZCI6InZvbG8iLCJ1c2VyX2lkIjoidHdrMGNzcjMiLCJleHBpcmVzIjo0ODYw"
    "NDUyODYzLCJ0b2tlbl90eXBlIjoiYmVhcmVyIiwic2NvcGUiOiJERVZJQ0VfVE9LRU4ifQ.bNa"
    "-xls2e7LQcX9HkBNHQcwrc9m5JhV-34qXAaBvCT1yOV8fPT9udzXsRTXa1nt7Wx4l-oe58SKx"
    "-BGH5j75bJxgQRoZNl6oktaV_3M_GrjPLp4v1aqTQQCLVBhHbVfSn2Tm115M6WrfkG-paKgaB"
    "vwjqxKD2u3P7FniP5SnW8bchaph8t4hwlJOMbSC8vgIlyN0nCFUdjgWVEcil8MTkAndXE4OClx"
    "5_ebUo4mt5EiLeR8qiKTWgH0_aHmzu_kc9KX_lrHtQbyzDgsMjZiqSx8XdL4bOgNbKgUPqh3"
    "uaP6hvMtHdOepf3aCfrW7rMMQOLydXI5Kw2_dfasgoX-GDww"
)

# The credential file of the older `foodpanda` integration. Both
# integrations talk to foodpanda with the same device token, and a
# refresh token can only be used once, so two integrations that keep
# their own copy of it end up invalidating each other. The older
# integration owns that file, so this integration reads the fresh
# credentials from there instead of keeping a second copy.
SHARED_STORAGE_KEY = "foodpanda/tokens.json"

# Below this distance the courier counts as arrived.
ARRIVED_DISTANCE_M = 80.0

# Above this distance the courier is on the way to the customer
# instead of waiting at the restaurant.
EN_ROUTE_DISTANCE_M = 200.0

# How long a finished order stays in its slot, so the "delivered"
# state stays visible instead of flashing by for a single poll.
FINISHED_RETENTION_SECONDS = 300.0

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
