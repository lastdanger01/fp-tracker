"""Offline test for the foodpanda statistics page.

The page is built from the order history, so the numbers are checked
against a small fixture instead of the live API.
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

# The modules that need Home Assistant are stubbed here, because the
# offline tests run without it.
ha = types.ModuleType("homeassistant")
ha.__path__ = []
core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object
helpers = types.ModuleType("homeassistant.helpers")
storage = types.ModuleType("homeassistant.helpers.storage")


class _Store:  # noqa: D101 - stub
    def __init__(self, *args, **kwargs):
        pass


storage.Store = _Store
helpers.storage = storage
ha.core = core
ha.helpers = helpers
sys.modules.update(
    {
        "homeassistant": ha,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.storage": storage,
    }
)

const = importlib.import_module("fpt.const")
stats = importlib.import_module("fpt.stats")

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


def order(
    code,
    vendor,
    date,
    total,
    payment="globalpayments_pxpay",
    products=None,
    tip=0,
):
    """Build one order like the API returns it."""
    return {
        "order_code": code,
        "vendor": {"name": vendor},
        "ordered_at": {"date": date, "timezone": "Asia/Taipei"},
        "total_value": total,
        "subtotal": total,
        "service_fee_total": 0,
        "delivery_fee": 0,
        "rider_tip": tip,
        "payment_type_code": payment,
        "order_products": products or [],
    }


def product(name, quantity=1, price=100):
    """Build one order product."""
    return {
        "name": name,
        "quantity": quantity,
        "total_price": price,
        "toppings": [],
    }


ORDERS = [
    order("a1", "八方雲集 (冬山廣興店)", "2026-11-15 13:23:53", 197,
          products=[product("紹辣乾麵【辣】", 1, 80), product("冬瓜茶", 1, 60)]),
    order("a2", "捲吉海苔飯捲", "2026-11-15 08:18:45", 250,
          payment="cybersource_creditcard",
          products=[product("海苔飯捲", 2, 120)]),
    order("a3", "初光早午餐", "2026-11-14 07:38:52", 191,
          products=[product("早餐A", 1, 191)]),
    order("a4", "初光早午餐 (羅東店)", "2026-11-13 09:00:00", 120,
          products=[product("早餐B", 1, 120)]),
]

data = stats.build_data(ORDERS)

section("摘要")
check("訂單數", data["summary"]["count"], 4)
check("總花費", data["summary"]["total"], 758)
check("平均客單", data["summary"]["average"], 190)
check("最高金額", data["summary"]["highest"], 250)
check("最低金額", data["summary"]["cheapest"], 120)
check("消費天數", data["summary"]["days"], 3)
check("餐廳數（含分店）", data["summary"]["vendors"], 4)
check("品牌數（合併分店）", data["summary"]["brands"], 3)
check("品項種類", data["summary"]["items"], 5)
check("起始日", data["summary"]["from"], "2026-11-13")
check("結束日", data["summary"]["to"], "2026-11-15")

section("付款方式")
payments = {e["name"]: e for e in data["by_payment"]}
check("線上付款 3 筆", payments["線上付款"]["count"], 3)
check("信用卡 1 筆", payments["信用卡"]["count"], 1)
check(
    "未知付款碼有標籤",
    stats._payment_label("some_new_gateway") == "some_new_gateway",
    True,
)

section("品牌合併")
brands = {e["name"]: e for e in data["by_brand"]}
check("初光早午餐合併成一個品牌", "初光早午餐" in brands, True)
check("初光早午餐 2 筆", brands["初光早午餐"]["count"], 2)
check("初光早午餐合計", brands["初光早午餐"]["total"], 311)

section("餐別分布")
periods = {e["name"]: e for e in data["by_period"]}
check("午餐 1 筆", periods["午餐"]["count"], 1)
check("早餐 3 筆", periods["早餐"]["count"], 3)
check("早餐合計", periods["早餐"]["total"], 561)

section("品項排行")
top = data["top_items"]
check("第一名的次數", top[0]["count"], 2)
check("第一名是海苔飯捲×2", top[0]["name"], "海苔飯捲")
check(
    "品項花費有累計（total_price 是行總額）",
    next(i for i in top if i["name"] == "海苔飯捲")["total"],
    120,
)

section("每日消費")
days = {e["date"]: e for e in data["by_day"]}
check("11/15 有 2 筆", days["2026-11-15"]["count"], 2)
check("11/15 合計", days["2026-11-15"]["total"], 447)

section("頁面")
page = stats.render_html(data)
check("有總花費", "758" in page, True)
check("沒有未替換的 placeholder", "__" in page.replace("__", "@@"), False)
check(
    "所有 placeholder 都換掉了",
    [t for t in ("__SUBTITLE__", "__TOTAL__", "__BY_DAY__", "__TABLE__", "__GENERATED__") if t in page],
    [],
)
check("有 <html>", page.strip().startswith("<!DOCTYPE html>"), True)
check("沒有外部資源", "http://" not in page and "https://" not in page, True)

# A name with a quote must not break the markup.
tricky = stats.build_data(
    [order("t1", '店"<script>alert(1)</script>', "2026-11-15 12:00:00", 100)]
)
tricky_page = stats.render_html(tricky)
check("惡意店名被轉義", "<script>alert(1)</script>" in tricky_page, False)
check("轉義後仍看得見", "&lt;script&gt;" in tricky_page, True)

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} 項失敗：{FAILURES}")
    sys.exit(1)
print("✅ 全部通過")
