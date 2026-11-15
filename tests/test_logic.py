"""Offline tests for the pure logic of the fp_tracker integration.

These run without Home Assistant: the api module only needs `requests`,
which is not imported at module level for the pure helpers.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import types

# The api module only needs `requests` and the sibling const module, but
# importing it as part of the package would run __init__.py, which
# imports Home Assistant. So the directory is loaded as a stand alone
# package instead.
PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "fp_tracker"

pkg = types.ModuleType("fpt")
pkg.__path__ = [str(PKG_DIR)]
pkg.__package__ = "fpt"
sys.modules["fpt"] = pkg

const = importlib.import_module("fpt.const")
api = importlib.import_module("fpt.api")

_extract_courier = api._extract_courier
_extract_delivery_address = api._extract_delivery_address
_extract_eta_minutes = api._extract_eta_minutes
courier_distance_m = api.courier_distance_m
derive_state = api.derive_state
estimate_eta_minutes = api.estimate_eta_minutes

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


# ---------------------------------------------------------------- states
section("狀態機")
check("已取消", derive_state({"status_flags": {"is_canceled": True}}), "cancelled")
check("已送達", derive_state({"status_flags": {"is_delivered": True}}), "delivered")
check("已完成", derive_state({"status_flags": {"is_completed": True}}), "delivered")
check("已取餐", derive_state({"status_flags": {"is_picked_up": True}}), "picked_up")

check(
    "備餐中（order history 說法）",
    derive_state(
        {
            "status_flags": {"is_active": True},
            "current_status": {"message": "RESTAURANTS_OTP_OD_PROGRESS_PREPARING"},
        }
    ),
    "preparing",
)
check(
    "備餐中（tracking 說法）",
    derive_state(
        {
            "status_flags": {"is_active": True},
            "current_status": {
                "message": "shop.order.status.message_order_accepted_by_vendor"
            },
        }
    ),
    "preparing",
)
check(
    "已接受（無 PREPARING 字樣）",
    derive_state(
        {
            "status_flags": {"is_active": True},
            "current_status": {"message": "shop.order.status.message_order_accepted"},
        }
    ),
    "accepted",
)
check(
    "已取餐（message 說 PICKED，flags 還沒翻）",
    derive_state(
        {
            "status_flags": {"is_active": True, "is_picked_up": False},
            "current_status": {
                "code": 14,
                "message": "RESTAURANTS_OTP_OD_PROGRESS_PICKED",
            },
        }
    ),
    "picked_up",
)
check(
    "已抵達（message 說 ARRIVED）",
    derive_state(
        {
            "status_flags": {"is_active": True},
            "current_status": {"message": "RESTAURANTS_OTP_OD_PROGRESS_ARRIVED"},
        }
    ),
    "arrived",
)
check(
    "已送達（message 說 DELIVERED，但 flags 還沒翻）",
    derive_state(
        {
            "status_flags": {"is_active": True, "is_delivered": False},
            "current_status": {
                "code": 16,
                "message": "RESTAURANTS_OTP_OD_PROGRESS_DELIVERED",
            },
        }
    ),
    "delivered",
)
check(
    "配送中（message 說 ON_THE_WAY）",
    derive_state(
        {
            "status_flags": {"is_active": True},
            "current_status": {"message": "RESTAURANTS_OTP_OD_PROGRESS_ON_THE_WAY"},
        }
    ),
    "picked_up",
)
check("無旗標", derive_state({}), "unknown")
check(
    "非 dict 旗標",
    derive_state({"status_flags": "nope"}),
    "unknown",
)

# ------------------------------------------------------------------- eta
section("ETA 解析")
check("eta 為 int", _extract_eta_minutes({"eta": 16}), 16)
check("eta 為 str", _extract_eta_minutes({"eta": "16"}), 16)
check("median 有值", _extract_eta_minutes({"delivery_time_range": {"median": "5"}}), 5)
check(
    "median 空字串 → 退回 lower",
    _extract_eta_minutes({"delivery_time_range": {"median": "", "lower": "10"}}),
    10,
)
check(
    "live_activity range 有區間",
    _extract_eta_minutes({"live_activity": {"delivery_time": {"range": "10 — 20"}}}),
    10,
)
check(
    "live_activity range 單值",
    _extract_eta_minutes({"live_activity": {"delivery_time": {"range": "7"}}}),
    7,
)
check("完全沒有 ETA", _extract_eta_minutes({}), None)
check("eta 為 None", _extract_eta_minutes({"eta": None}), None)

# -------------------------------------------------------------- distance
section("距離計算")
# The delivery address of the live test order.
ADDR = (24.65991751, 121.76177161)
VENDOR = (24.67125, 121.753361)

d_at_addr = courier_distance_m({"latitude": ADDR[0], "longitude": ADDR[1]}, [ADDR])
check("送餐者在收件地址 → 0 m", d_at_addr is not None and round(d_at_addr) == 0, True)
d = courier_distance_m({"latitude": VENDOR[0], "longitude": VENDOR[1]}, [ADDR])
check("餐廳到收件地址 約 1.5 km", d is not None and 1400 < d < 1600, True)
check("沒有送餐者位置 → None", courier_distance_m({}, [ADDR]), None)
check("沒有目標點 → None", courier_distance_m({"latitude": 24.0, "longitude": 121.0}, []), None)

# The distance must be measured to the delivery address, not the restaurant.
d_addr = courier_distance_m(
    {"latitude": 24.6650, "longitude": 121.7570}, [ADDR]
)
d_vendor = courier_distance_m(
    {"latitude": 24.6650, "longitude": 121.7570}, [VENDOR]
)
check("多目標時取最近的一個", d_addr is not None and d_vendor is not None and d_addr != d_vendor, True)

section("ETA 估算")
check("1000 m → 2 分鐘", estimate_eta_minutes(1000), 2)
check("2500 m → 6 分鐘", estimate_eta_minutes(2500), 6)
check("沒有距離 → None", estimate_eta_minutes(None), None)
check("距離 0 → 1 分鐘", estimate_eta_minutes(0), 1)

# --------------------------------------------------------------- courier
section("送餐者欄位")
c = _extract_courier(
    {
        "courier": {
            "name": "小明",
            "phone": "0912345678",
            "vehicle_type": "scooter",
            "latitude": "24.66",
            "longitude": "121.76",
        }
    }
)
check("姓名", c["name"], "小明")
check("電話", c["phone"], "0912345678")
check("車輛", c["vehicle"], "scooter")
check("緯度轉 float", c["latitude"], 24.66)
check("經度轉 float", c["longitude"], 121.76)
check("無 courier → 空 dict", _extract_courier({}), {})
check(
    "座標是垃圾 → None",
    _extract_courier({"courier": {"latitude": "abc", "longitude": "xyz"}}),
    {"name": "", "phone": "", "vehicle": "", "latitude": None, "longitude": None},
)

# --------------------------------------------------------------- address
section("收件地址")
a = _extract_delivery_address(
    {"delivery_address": {"latitude": 24.65991751, "longitude": 121.76177161}}
)
check("緯度", a["latitude"], 24.65991751)
check("經度", a["longitude"], 121.76177161)
check(
    "沒有地址 → None",
    _extract_delivery_address({}),
    {"latitude": None, "longitude": None},
)
check(
    "退回 order_address",
    _extract_delivery_address({"order_address": {"latitude": 1.5, "longitude": 2.5}}),
    {"latitude": 1.5, "longitude": 2.5},
)

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} 項失敗：{FAILURES}")
    sys.exit(1)
print("✅ 全部通過")
