"""Offline tests for the shared credentials and the state fixes.

These cover the three bugs found on 2026-11-15:

- the state skipped "picked up" because the tracking endpoint reports
  the pickup with its own flag
- a finished order was dropped from its slot immediately, so the
  "delivered" state and its notification were never seen
- the credentials were kept in a second copy, which the older
  foodpanda integration invalidated on every refresh
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "fp_tracker"

pkg = types.ModuleType("fpt")
pkg.__path__ = [str(PKG_DIR)]
pkg.__package__ = "fpt"
sys.modules["fpt"] = pkg

const = importlib.import_module("fpt.const")
api = importlib.import_module("fpt.api")
derive_state = api.derive_state

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    """Compare one value and record the result."""
    if got == want:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------- the pickup flag
section("取餐旗標")
check(
    "is_picked_up_by_rider → picked_up",
    derive_state({"status_flags": {"is_picked_up_by_rider": True}}),
    "picked_up",
)
check(
    "is_picked_up_by_rider 優先於 is_active",
    derive_state(
        {
            "status_flags": {
                "is_active": True,
                "is_picked_up": False,
                "is_picked_up_by_rider": True,
            }
        }
    ),
    "picked_up",
)
check(
    "兩個旗標都 false → 不是 picked_up",
    derive_state(
        {
            "status_flags": {
                "is_active": True,
                "is_picked_up": False,
                "is_picked_up_by_rider": False,
            },
            "current_status": {"message": "RESTAURANTS_OTP_OD_PROGRESS_PREPARING"},
        }
    ),
    "preparing",
)
check(
    "已送達旗標優先於取餐旗標",
    derive_state(
        {
            "status_flags": {
                "is_delivered": True,
                "is_picked_up_by_rider": True,
            }
        }
    ),
    "delivered",
)

# ------------------------------------------------ the shared file
section("共用憑證檔")
check(
    "SHARED_STORAGE_KEY 指向舊整合的檔案",
    const.SHARED_STORAGE_KEY,
    "foodpanda/tokens.json",
)
check(
    "x_device 有預設值",
    const.DEFAULT_X_DEVICE.startswith("eyJhbGciOiJSUzI1NiIs"),
    True,
)
check(
    "x_device 不是空字串",
    len(const.DEFAULT_X_DEVICE) > 100,
    True,
)

# The credential normaliser of the credentials module accepts the
# field names of the older integration. The module imports Home
# Assistant, which is not available offline, so it is stubbed here.
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_storage_mod = types.ModuleType("homeassistant.helpers.storage")


class _Store:  # noqa: D101 - stub
    def __init__(self, *args, **kwargs):
        pass


ha_storage_mod.Store = _Store
ha_helpers.storage = ha_storage_mod
ha_root = types.ModuleType("homeassistant")
ha_root.__path__ = []
ha_root.core = ha_core
ha_root.helpers = ha_helpers
ha_config_entries = types.ModuleType("homeassistant.config_entries")
ha_config_entries.ConfigEntry = object
ha_update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")


class _DataUpdateCoordinator:  # noqa: D101 - stub
    def __init__(self, *args, **kwargs):
        pass


class _UpdateFailed(Exception):  # noqa: D101 - stub
    pass


ha_update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
ha_update_coordinator.UpdateFailed = _UpdateFailed
ha_root.config_entries = ha_config_entries
ha_helpers.update_coordinator = ha_update_coordinator
sys.modules.setdefault("homeassistant", ha_root)
sys.modules.setdefault("homeassistant.core", ha_core)
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
sys.modules.setdefault("homeassistant.helpers.storage", ha_storage_mod)
sys.modules.setdefault("homeassistant.config_entries", ha_config_entries)
sys.modules.setdefault("homeassistant.helpers.update_coordinator", ha_update_coordinator)

credentials = importlib.import_module("fpt.credentials")
normalized = credentials._normalize(
    {
        "token": "T",
        "device_token": "D",
        "refresh_token": "R",
        "clientid": "CI",
        "sessionid": "SI",
        "usersouce": "US",
    }
)
check("token", normalized["token"], "T")
check("device_token", normalized["device_token"], "D")
check("refresh_token", normalized["refresh_token"], "R")
check("clientid → client_id", normalized["client_id"], "CI")
check("sessionid → session_id", normalized["session_id"], "SI")
check("usersouce → user_source", normalized["user_source"], "US")
check("缺 x_device 時用預設值", normalized["x_device"], const.DEFAULT_X_DEVICE)

normalized2 = credentials._normalize(
    {"token": "T", "client_id": "CI2", "session_id": "SI2", "user_source": "US2"}
)
check("新欄位名稱也支援", normalized2["client_id"], "CI2")
check("新欄位 session_id", normalized2["session_id"], "SI2")
check("新欄位 user_source", normalized2["user_source"], "US2")

# ------------------------------------------------ the finished slot
section("已完成訂單保留在槽位")
coordinator = importlib.import_module("fpt.coordinator")
check(
    "_is_finished: is_delivered",
    coordinator._is_finished({"status_flags": {"is_delivered": True}}),
    True,
)
check(
    "_is_finished: is_completed",
    coordinator._is_finished({"status_flags": {"is_completed": True}}),
    True,
)
check(
    "_is_finished: is_canceled",
    coordinator._is_finished({"status_flags": {"is_canceled": True}}),
    True,
)
check(
    "_is_finished: 進行中不算",
    coordinator._is_finished({"status_flags": {"is_active": True}}),
    False,
)
check(
    "_is_active: 進行中",
    coordinator._is_active({"status_flags": {"is_active": True}}),
    True,
)
check(
    "_is_active: 已送達不算",
    coordinator._is_active({"status_flags": {"is_delivered": True}}),
    False,
)
check(
    "保留時間是正數",
    coordinator.FINISHED_RETENTION_SECONDS > 0,
    True,
)

# ------------------------------------------------ the shared file guard
section("共用憑證檔不得覆蓋掉新 token")
# The bug of 2026-11-15: a leftover foodpanda/tokens.json held a token
# that expired in 2024, and it replaced the token the user had just
# pasted into the config flow. The entry then failed with HTTP 401.
#
# A JWT whose payload carries the expiry, so the comparison has
# something to look at.
def make_token(expires: int) -> str:
    """Return a fake access token with the given expiry."""
    import base64 as _b64
    import json as _json

    def seg(data):
        raw = _json.dumps(data).encode()
        return _b64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = seg({"alg": "RS256", "kid": "keymaker-volo-fp-tw"})
    payload = seg({"id": "x", "expires": expires})
    return f"{header}.{payload}.sig"


OLD_TOKEN = make_token(1728788770)   # expired in 2024
NEW_TOKEN = make_token(1794748191)   # valid in 2026

check(
    "過期的共用憑證不會覆蓋新的 token",
    credentials.shared_credentials_are_newer(
        {"token": OLD_TOKEN}, {"token": NEW_TOKEN}
    ),
    False,
)
check(
    "比較新的共用憑證會覆蓋舊的",
    credentials.shared_credentials_are_newer(
        {"token": NEW_TOKEN}, {"token": OLD_TOKEN}
    ),
    True,
)
check(
    "目前沒有 token 時會採用共用憑證",
    credentials.shared_credentials_are_newer({"token": NEW_TOKEN}, {"token": ""}),
    True,
)
check(
    "共用憑證沒有 token 時不採用",
    credentials.shared_credentials_are_newer({"token": ""}, {"token": NEW_TOKEN}),
    False,
)
check(
    "同一組 token 不重複覆蓋",
    credentials.shared_credentials_are_newer(
        {"token": NEW_TOKEN}, {"token": NEW_TOKEN}
    ),
    False,
)
check(
    "讀不到到期時間時以最舊處理",
    credentials.shared_credentials_are_newer(
        {"token": "not-a-jwt"}, {"token": NEW_TOKEN}
    ),
    False,
)
check(
    "JWT 的 expires 有被讀到",
    credentials._jwt_expiry(NEW_TOKEN),
    1794748191,
)
check(
    "沒有 token 時到期時間是 0",
    credentials._jwt_expiry(""),
    0,
)

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} 項失敗：{FAILURES}")
    sys.exit(1)
print("✅ 全部通過")
