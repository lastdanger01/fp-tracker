"""Statistics page for the foodpanda order history.

The integration tracks a single delivery at a time, but the order
history holds months of orders. This module turns that history into a
small self contained page with the spending statistics, so the history
can be looked at without opening the foodpanda app.

The page is written into `/config/www/fp-stats/`, which Home Assistant
serves as `/local/fp-stats/index.html`. It is one HTML file with the
data baked in and no external files, so it also works when the server
has no access to the internet.
"""

from __future__ import annotations

import html
import json
import logging
import os
from datetime import datetime
from typing import Any

from .api import FoodpandaApiError, FoodpandaClient
from .const import CONF_LOCALCODE, DEFAULT_LOCALCODE
from .credentials import async_load_shared_credentials

_LOGGER = logging.getLogger(__name__)

PAGE_DIR = "/config/www/fp-stats"
PAGE_FILE = "index.html"
PAGE_URL = "/local/fp-stats/index.html"

# The payment codes the API uses, with the label that is shown.
PAYMENT_LABELS = {
    "globalpayments_pxpay": "線上付款",
    "globalpayments_card": "信用卡",
    "cybersource_creditcard": "信用卡",
    "cybersource_paypal": "PayPal",
    "paypal": "PayPal",
    "cash": "現金",
    "apple_pay": "Apple Pay",
    "google_pay": "Google Pay",
    "line_pay": "LINE Pay",
}

# The meal a order belongs to, by the hour it was ordered.
MEAL_PERIODS = [
    (5, 10, "早餐", "#f6a623"),
    (11, 14, "午餐", "#4caf50"),
    (15, 17, "下午茶", "#ab47bc"),
    (18, 21, "晚餐", "#ef5350"),
    (22, 28, "宵夜", "#42a5f5"),
    (0, 4, "宵夜", "#42a5f5"),
]


def _payment_label(code: str) -> str:
    """Return the label of a payment code."""
    if not code:
        return "其他"
    return PAYMENT_LABELS.get(code, code)


def _meal_period(hour: int) -> tuple[str, str]:
    """Return the name and the color of the meal period of an hour."""
    for start, end, name, color in MEAL_PERIODS:
        if start <= hour <= end:
            return name, color
    return "其他", "#90a4ae"


def _number(value: Any) -> float:
    """Return a value as a float, whatever the API sent."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _brand(vendor: str) -> str:
    """Return the brand of a restaurant, without the branch name."""
    return vendor.split(" (")[0].strip() or vendor


def build_data(orders: list[dict]) -> dict:
    """Turn the order history into the numbers the page shows."""
    rows: list[dict] = []
    item_counter: dict[str, int] = {}
    item_spend: dict[str, float] = {}
    by_day: dict[str, dict] = {}
    by_vendor: dict[str, dict] = {}
    by_brand: dict[str, dict] = {}
    by_payment: dict[str, dict] = {}
    by_period: dict[str, dict] = {}

    for order in orders:
        vendor = (order.get("vendor") or {}).get("name") or "未知店家"
        ordered_at = (order.get("ordered_at") or {}).get("date") or ""
        try:
            moment = datetime.strptime(ordered_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            moment = None

        total = _number(order.get("total_value"))
        subtotal = _number(order.get("subtotal"))
        fee = _number(order.get("service_fee_total"))
        delivery = _number(order.get("delivery_fee"))
        tip = _number(order.get("rider_tip"))
        payment = _payment_label(order.get("payment_type_code") or "")
        period = _meal_period(moment.hour) if moment else ("其他", "#90a4ae")

        products = []
        for product in order.get("order_products") or []:
            if not isinstance(product, dict):
                continue
            name = product.get("name") or ""
            quantity = int(_number(product.get("quantity")) or 0)
            price = _number(product.get("total_price"))
            if not name:
                continue
            products.append(
                {"name": name, "quantity": quantity, "price": price}
            )
            item_counter[name] = item_counter.get(name, 0) + quantity
            item_spend[name] = item_spend.get(name, 0.0) + price

        row = {
            "code": order.get("order_code") or "",
            "date": moment.strftime("%Y-%m-%d") if moment else "",
            "time": moment.strftime("%H:%M") if moment else "",
            "weekday": "一二三四五六日"[moment.weekday()] if moment else "",
            "vendor": vendor,
            "brand": _brand(vendor),
            "total": total,
            "subtotal": subtotal,
            "fee": fee,
            "delivery": delivery,
            "tip": tip,
            "payment": payment,
            "period": period[0],
            "period_color": period[1],
            "products": products,
            "summary": "、".join(
                f"{p['name']}×{p['quantity']}" for p in products[:3]
            ),
            "count": len(products),
        }
        rows.append(row)

        day = row["date"]
        if day:
            entry = by_day.setdefault(day, {"date": day, "total": 0.0, "count": 0})
            entry["total"] += total
            entry["count"] += 1

        for bucket, key in (
            (by_vendor, vendor),
            (by_brand, row["brand"]),
        ):
            entry = bucket.setdefault(
                key, {"name": key, "total": 0.0, "count": 0, "items": []}
            )
            entry["total"] += total
            entry["count"] += 1
            for product in products:
                if product["name"] not in entry["items"]:
                    entry["items"].append(product["name"])

        entry = by_payment.setdefault(
            payment, {"name": payment, "total": 0.0, "count": 0}
        )
        entry["total"] += total
        entry["count"] += 1

        entry = by_period.setdefault(
            period[0], {"name": period[0], "total": 0.0, "count": 0, "color": period[1]}
        )
        entry["total"] += total
        entry["count"] += 1

    rows.sort(key=lambda r: (r["date"], r["time"]), reverse=True)

    total_all = sum(r["total"] for r in rows)
    count_all = len(rows)

    def _sorted_bucket(bucket: dict) -> list[dict]:
        return sorted(bucket.values(), key=lambda e: e["total"], reverse=True)

    top_items = sorted(
        (
            {"name": name, "count": count, "total": round(item_spend.get(name, 0.0))}
            for name, count in item_counter.items()
        ),
        key=lambda i: (-i["count"], -i["total"]),
    )

    return {
        "summary": {
            "count": count_all,
            "total": round(total_all),
            "average": round(total_all / count_all) if count_all else 0,
            "highest": round(max((r["total"] for r in rows), default=0)),
            "cheapest": round(min((r["total"] for r in rows), default=0)),
            "days": len(by_day),
            "vendors": len(by_vendor),
            "brands": len(by_brand),
            "items": len(item_counter),
            "from": rows[-1]["date"] if rows else "",
            "to": rows[0]["date"] if rows else "",
        },
        "rows": rows,
        "by_day": sorted(by_day.values(), key=lambda e: e["date"]),
        "by_vendor": _sorted_bucket(by_vendor),
        "by_brand": _sorted_bucket(by_brand),
        "by_payment": _sorted_bucket(by_payment),
        "by_period": _sorted_bucket(by_period),
        "top_items": top_items,
    }


def _bar_rows(entries: list[dict], limit: int = 12) -> str:
    """Render a list of bars for the page."""
    if not entries:
        return '<div class="empty">沒有資料</div>'
    top = entries[:limit]
    biggest = max((e["total"] for e in top), default=1) or 1
    out = []
    for entry in top:
        width = entry["total"] / biggest * 100
        out.append(
            '<div class="bar-row">'
            f'<div class="bar-label" title="{html.escape(entry["name"])}">'
            f'{html.escape(entry["name"])}</div>'
            '<div class="bar-track">'
            f'<div class="bar-fill" style="width:{width:.1f}%"></div>'
            "</div>"
            f'<div class="bar-value">{entry["total"]:,}<span class="bar-count">'
            f'×{entry["count"]}</span></div>'
            "</div>"
        )
    return "".join(out)


def _donut(entries: list[dict]) -> str:
    """Render a pie chart with a legend."""
    if not entries:
        return '<div class="empty">沒有資料</div>'
    colors = ["#ff7043", "#42a5f5", "#66bb6a", "#ab47bc", "#ffa726", "#26a69a"]
    total = sum(e["total"] for e in entries) or 1
    parts = []
    legend = []
    offset = 0.0
    for index, entry in enumerate(entries):
        share = entry["total"] / total * 100
        color = colors[index % len(colors)]
        parts.append(
            f'<circle class="donut-part" r="15.9155" cx="21" cy="21" '
            f'fill="none" stroke="{color}" stroke-width="6" '
            f'stroke-dasharray="{share:.2f} {100 - share:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}"></circle>'
        )
        offset += share
        legend.append(
            f'<li><span class="dot" style="background:{color}"></span>'
            f'<span class="legend-name">{html.escape(entry["name"])}</span>'
            f'<span class="legend-value">{entry["total"]:,}'
            f' <em>({share:.0f}%)</em></span></li>'
        )
    return (
        '<div class="donut-wrap">'
        '<svg viewBox="0 0 42 42" class="donut">'
        + "".join(parts)
        + "</svg>"
        '<ul class="legend">' + "".join(legend) + "</ul></div>"
    )


def _columns(entries: list[dict]) -> str:
    """Render a column chart of the daily spending."""
    if not entries:
        return '<div class="empty">沒有資料</div>'
    biggest = max((e["total"] for e in entries), default=1) or 1
    out = []
    for entry in entries:
        height = max(entry["total"] / biggest * 100, 2)
        out.append(
            '<div class="col-wrap">'
            f'<div class="col-value">{entry["total"]:,}</div>'
            f'<div class="col" style="height:{height:.1f}%"></div>'
            f'<div class="col-label">{html.escape(entry["date"][5:])}</div>'
            "</div>"
        )
    return '<div class="columns">' + "".join(out) + "</div>"


def _table(rows: list[dict]) -> str:
    """Render the table of all orders."""
    if not rows:
        return '<div class="empty">沒有資料</div>'
    out = []
    for row in rows:
        products = "、".join(
            f'{html.escape(p["name"])}×{p["quantity"]}' for p in row["products"]
        )
        out.append(
            "<tr>"
            f'<td class="c-date">{html.escape(row["date"])}'
            f'<span class="c-time">{html.escape(row["time"])}</span></td>'
            f'<td>{html.escape(row["vendor"])}</td>'
            f'<td class="c-items">{html.escape(products)}</td>'
            f'<td><span class="pill">{html.escape(row["payment"])}</span></td>'
            f'<td class="c-total">{row["total"]:,}</td>'
            "</tr>"
        )
    return (
        '<table class="table"><thead><tr>'
        "<th>日期</th><th>餐廳</th><th>品項</th><th>付款</th><th>金額</th>"
        "</tr></thead><tbody>" + "".join(out) + "</tbody></table>"
    )


def _item_list(items: list[dict], limit: int = 15) -> str:
    """Render the list of the most ordered products."""
    if not items:
        return '<div class="empty">沒有資料</div>'
    out = []
    for index, item in enumerate(items[:limit], start=1):
        out.append(
            "<li>"
            f'<span class="rank">{index}</span>'
            f'<span class="item-name">{html.escape(item["name"])}</span>'
            f'<span class="item-count">×{item["count"]}</span>'
            f'<span class="item-total">{item["total"]:,}</span>'
            "</li>"
        )
    return '<ul class="items">' + "".join(out) + "</ul>"


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>foodpanda 餐費分析</title>
<style>
:root {
  --bg: #f4f5f7;
  --card: #ffffff;
  --text: #1f2328;
  --muted: #6b7280;
  --line: #e5e7eb;
  --accent: #ff7043;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC",
    "PingFang TC", "Microsoft JhengHei", sans-serif;
  background: var(--bg); color: var(--text);
}
.wrap { max-width: 1100px; margin: 0 auto; }
header { margin-bottom: 16px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 13px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin-bottom: 16px; }
.kpi { background: var(--card); border-radius: 14px; padding: 14px 16px;
  border: 1px solid var(--line); }
.kpi .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.kpi .value { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.kpi .note { font-size: 11px; color: var(--muted); margin-top: 4px; }
.card { background: var(--card); border-radius: 14px; padding: 16px;
  border: 1px solid var(--line); margin-bottom: 16px; }
.card h2 { font-size: 15px; margin: 0 0 14px; display: flex;
  align-items: center; gap: 8px; }
.card h2 .tag { font-size: 11px; font-weight: 500; color: var(--muted);
  background: #f3f4f6; padding: 2px 8px; border-radius: 999px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 760px) { .grid2 { grid-template-columns: 1fr; } }

.bar-row { display: grid; grid-template-columns: minmax(90px, 150px) 1fr auto;
  align-items: center; gap: 10px; margin-bottom: 9px; }
.bar-label { font-size: 13px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.bar-track { background: #f1f3f5; border-radius: 999px; height: 10px;
  overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 999px; }
.bar-value { font-size: 13px; font-weight: 600; white-space: nowrap; }
.bar-count { font-size: 11px; color: var(--muted); font-weight: 400;
  margin-left: 6px; }

.donut-wrap { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.donut { width: 150px; height: 150px; transform: rotate(-90deg); flex: none; }
.donut-part { transition: stroke-dasharray 0.3s; }
.legend { list-style: none; margin: 0; padding: 0; flex: 1; min-width: 160px; }
.legend li { display: flex; align-items: center; gap: 8px; font-size: 13px;
  margin-bottom: 7px; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.legend-name { flex: 1; }
.legend-value { font-weight: 600; }
.legend-value em { font-style: normal; color: var(--muted); font-weight: 400;
  font-size: 11px; }

.columns { display: flex; align-items: flex-end; gap: 4px; height: 160px;
  overflow-x: auto; padding-top: 18px; }
.col-wrap { flex: 1 0 34px; display: flex; flex-direction: column;
  align-items: center; height: 100%; justify-content: flex-end; }
.col { width: 100%; background: linear-gradient(180deg, #ff8a65, #ff7043);
  border-radius: 4px 4px 0 0; min-height: 3px; }
.col-value { font-size: 10px; color: var(--muted); margin-bottom: 3px; }
.col-label { font-size: 10px; color: var(--muted); margin-top: 5px;
  white-space: nowrap; }

.items { list-style: none; margin: 0; padding: 0; }
.items li { display: grid; grid-template-columns: 22px 1fr auto auto;
  gap: 10px; align-items: center; font-size: 13px; padding: 7px 0;
  border-bottom: 1px solid var(--line); }
.items li:last-child { border-bottom: none; }
.rank { width: 22px; height: 22px; border-radius: 50%; background: #f1f3f5;
  color: var(--muted); font-size: 11px; display: flex; align-items: center;
  justify-content: center; font-weight: 600; }
.item-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item-count { color: var(--muted); font-size: 12px; }
.item-total { font-weight: 600; min-width: 52px; text-align: right; }

.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th { text-align: left; color: var(--muted); font-weight: 500;
  font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--line); }
.table td { padding: 9px 10px; border-bottom: 1px solid #f3f4f6;
  vertical-align: top; }
.table tr:last-child td { border-bottom: none; }
.c-date { white-space: nowrap; }
.c-time { color: var(--muted); font-size: 11px; margin-left: 6px; }
.c-items { color: var(--muted); font-size: 12px; max-width: 260px; }
.c-total { text-align: right; font-weight: 600; white-space: nowrap; }
.pill { background: #f1f3f5; border-radius: 999px; padding: 2px 9px;
  font-size: 11px; white-space: nowrap; }
.empty { color: var(--muted); font-size: 13px; padding: 20px 0;
  text-align: center; }
footer { text-align: center; color: var(--muted); font-size: 11px;
  padding: 8px 0 24px; }
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>foodpanda 餐費分析</h1>
  <div class="sub">__SUBTITLE__</div>
</header>

<div class="kpis">
  <div class="kpi"><div class="label">總花費</div>
    <div class="value">NT$__TOTAL__</div>
    <div class="note">__COUNT__ 筆訂單</div></div>
  <div class="kpi"><div class="label">平均客單</div>
    <div class="value">NT$__AVERAGE__</div>
    <div class="note">最高 NT$__HIGHEST__</div></div>
  <div class="kpi"><div class="label">消費天數</div>
    <div class="value">__DAYS__ 天</div>
    <div class="note">共 __VENDORS__ 家店</div></div>
  <div class="kpi"><div class="label">點過的品項</div>
    <div class="value">__ITEMS__ 種</div>
    <div class="note">__BRANDS__ 個品牌</div></div>
</div>

<div class="card">
  <h2>每日消費 <span class="tag">依日期</span></h2>
  __BY_DAY__
</div>

<div class="grid2">
  <div class="card">
    <h2>餐廳排行 <span class="tag">依花費</span></h2>
    __BY_VENDOR__
  </div>
  <div class="card">
    <h2>品牌排行 <span class="tag">依花費</span></h2>
    __BY_BRAND__
  </div>
</div>

<div class="grid2">
  <div class="card">
    <h2>付款方式</h2>
    __BY_PAYMENT__
  </div>
  <div class="card">
    <h2>餐別分布</h2>
    __BY_PERIOD__
  </div>
</div>

<div class="card">
  <h2>最常點的品項 <span class="tag">依次數</span></h2>
  __TOP_ITEMS__
</div>

<div class="card">
  <h2>全部訂單 <span class="tag">__COUNT__ 筆</span></h2>
  __TABLE__
</div>

<footer>由 fp_tracker 產生 · __GENERATED__</footer>
</div>
</body>
</html>
"""


def render_html(data: dict) -> str:
    """Render the statistics page."""
    summary = data["summary"]
    subtitle = (
        f"{summary['from']} ~ {summary['to']} · "
        f"共 {summary['count']} 筆訂單 · 資料來源 Foodpanda 訂單歷史"
    )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    replacements = {
        "__SUBTITLE__": html.escape(subtitle),
        "__TOTAL__": f"{summary['total']:,}",
        "__COUNT__": str(summary["count"]),
        "__AVERAGE__": f"{summary['average']:,}",
        "__HIGHEST__": f"{summary['highest']:,}",
        "__DAYS__": str(summary["days"]),
        "__VENDORS__": str(summary["vendors"]),
        "__ITEMS__": str(summary["items"]),
        "__BRANDS__": str(summary["brands"]),
        "__BY_DAY__": _columns(data["by_day"]),
        "__BY_VENDOR__": _bar_rows(data["by_vendor"]),
        "__BY_BRAND__": _bar_rows(data["by_brand"]),
        "__BY_PAYMENT__": _donut(data["by_payment"]),
        "__BY_PERIOD__": _bar_rows(data["by_period"]),
        "__TOP_ITEMS__": _item_list(data["top_items"]),
        "__TABLE__": _table(data["rows"]),
        "__GENERATED__": html.escape(generated),
    }

    page = TEMPLATE
    for key, value in replacements.items():
        page = page.replace(key, value)
    return page


async def async_generate_stats(
    hass: Any, account: str | None = None
) -> dict:
    """Fetch the order history and write the statistics page.

    Returns a small dict with the numbers and the path of the page, so
    a service call can report back what happened.
    """
    credentials = await async_load_shared_credentials(hass, account)
    if not credentials:
        raise FoodpandaApiError("找不到可用的 foodpanda 憑證")

    client = FoodpandaClient(
        hass,
        localcode=credentials.get(CONF_LOCALCODE, DEFAULT_LOCALCODE),
        token=credentials.get("token", ""),
        device_token=credentials.get("device_token", ""),
        refresh_token=credentials.get("refresh_token", ""),
        client_id=credentials.get("client_id", ""),
        session_id=credentials.get("session_id", ""),
        user_source=credentials.get("user_source", ""),
        x_device=credentials.get("x_device", ""),
        token_timeout=int(credentials.get("token_timeout", 0) or 0),
    )

    orders = await client.async_get_orders()
    data = build_data(orders)
    page = render_html(data)

    def _write() -> None:
        os.makedirs(PAGE_DIR, exist_ok=True)
        with open(os.path.join(PAGE_DIR, PAGE_FILE), "w", encoding="utf-8") as file:
            file.write(page)

    await hass.async_add_executor_job(_write)

    _LOGGER.info(
        "已產生 foodpanda 統計頁：%s 筆訂單，合計 %s",
        data["summary"]["count"],
        data["summary"]["total"],
    )
    return {
        "path": PAGE_URL,
        "count": data["summary"]["count"],
        "total": data["summary"]["total"],
    }
