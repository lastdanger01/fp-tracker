"""Offline tests for tracking more than one order at the same time."""

from __future__ import annotations

import asyncio
import importlib
import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "fp_tracker"

pkg = types.ModuleType("fpt")
pkg.__path__ = [str(PKG_DIR)]
pkg.__package__ = "fpt"
sys.modules["fpt"] = pkg

api = importlib.import_module("fpt.api")

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


def order(code, flags, ordered_at, vendor="八方雲集", vendor_lat=24.67125, vendor_lon=121.753361):
    """Build one order like the API returns it."""
    return {
        "order_code": code,
        "status_flags": flags,
        "ordered_at": {"date": ordered_at, "timezone": "Asia/Taipei"},
        "vendor": {"name": vendor, "latitude": vendor_lat, "longitude": vendor_lon},
    }


ACTIVE = {"is_active": True, "is_delivered": False, "is_canceled": False, "is_completed": False}


class FakeClient:
    """A client that returns a fixed order history.

    The real `async_get_active_orders` is called unbound, so the real
    selection logic is what gets tested.
    """

    def __init__(self, orders):
        self._orders = orders

    async def async_get_orders(self):
        """Return the order history."""
        return self._orders


def _run(orders):
    """Call the real selection logic against a fake client."""
    client = FakeClient(orders)
    return asyncio.run(api.FoodpandaClient.async_get_active_orders(client))


# --------------------------------------------------------- multiple orders
section("多張進行中訂單")
history = [
    order("r8qi-2646-c6nd", ACTIVE, "2026-11-15 13:23:53", "八方雲集 (冬山廣興店)"),
    order("bd0q-2646-09ug", ACTIVE, "2026-11-15 13:21:54", "捲吉海苔飯捲",
          24.6784151, 121.7589056),
    order("kfve-2646-m481", {"is_active": False, "is_delivered": True}, "2026-11-15 08:18:45",
          "貳玖巷早午餐"),
]
active = _run(history)
check("找到 2 張進行中", len(active), 2)
check("按時間排序，最舊的在前", active[0]["order_code"], "bd0q-2646-09ug")
check("第二張是較新的", active[1]["order_code"], "r8qi-2646-c6nd")
check("第一張是捲吉海苔飯捲", active[0]["vendor"]["name"], "捲吉海苔飯捲")
check("第二張是八方雲集", active[1]["vendor"]["name"], "八方雲集 (冬山廣興店)")

section("只有一張進行中")
one = _run([history[0], history[2]])
check("找到 1 張", len(one), 1)
check("是那一張", one[0]["order_code"], "r8qi-2646-c6nd")

section("沒有進行中")
none = _run([history[2]])
check("找到 0 張", len(none), 0)

section("已取消的不算")
cancelled = _run(
    [
        order("a", {"is_active": True, "is_canceled": True}, "2026-11-15 13:00:00"),
        history[0],
    ]
)
check("只算 1 張", len(cancelled), 1)

section("沒有 ordered_at 也不會炸")
no_time = _run(
    [
        {"order_code": "x", "status_flags": ACTIVE, "vendor": {"name": "無時間"}},
        history[0],
    ]
)
check("找到 2 張", len(no_time), 2)

section("單張的舊方法仍然可用")
single = _run([history[2], history[0]])
check("async_get_active_orders 正常", len(single), 1)


# ------------------------------------------------------- slot assignment
section("槽位對應")
section("同一張訂單不會佔兩個槽位")
check("兩張各佔一槽", [o["order_code"] for o in active], ["bd0q-2646-09ug", "r8qi-2646-c6nd"])

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} 項失敗：{FAILURES}")
    sys.exit(1)
print("✅ 全部通過")
