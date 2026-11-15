# foodpanda 即時追蹤 (fp_tracker)

追蹤 foodpanda 訂單的**即時**狀態：送餐者在哪、訂單到哪個階段、還要多久到。

上游的 [foodpanda](https://github.com/tsunglung/foodpanda) 整合只查訂單歷史，
`courier` 欄位對已完成的訂單永遠是空的，而且它讀錯了 API 的欄位名。
這個整合用對的欄位，並且在你訂單進行中時持續更新。

## 會產生哪些實體

| 實體 | 型別 | 說明 |
|------|------|------|
| `sensor.fp_tracker_配送狀態` | sensor | 餐廳備餐中／已取餐配送中／已抵達／已送達 |
| `sensor.fp_tracker_預計送達` | sensor | foodpanda App 顯示的分鐘數 |
| `sensor.fp_tracker_預估剩餘時間` | sensor | 用送餐者 GPS 位置推估的分鐘數 |
| `sensor.fp_tracker_距離送達地點` | sensor | 送餐者到送達地點的距離（公尺） |
| `binary_sensor.fp_tracker_配送中` | binary_sensor | 有訂單進行中 |
| `binary_sensor.fp_tracker_送餐者已接近` | binary_sensor | 送餐者已到附近 |
| `device_tracker.fp_tracker_送餐者` | device_tracker | 送餐者位置，可放進地圖卡片 |

## 安裝

### HACS

1. HACS → 右上角三點選單 → **自訂存放庫**
2. 網址填 `lastdanger01/fp-tracker`（**不是**完整 URL）
3. 類別選 **整合 / Integration**（不能留空）
4. 儲存 → 到 HACS 的「整合」分頁搜尋 **foodpanda 即時追蹤** → 下載
5. **重啟 Home Assistant**

### 手動安裝

把 `custom_components/fp_tracker` 複製到 HA 的 `config/custom_components/` 底下，然後重啟。

## 設定

**設定 → 裝置與服務 → 整合 → 新增整合 → foodpanda 即時追蹤**

需要四個 token 欄位：

| 欄位 | 來源 |
|------|------|
| `token` | 瀏覽器登入 foodpanda → F12 → Application → Cookies → `www.foodpanda.com` → `access_token` |
| `refresh_token` | 同上，`refresh_token` |
| `device_token` | access token 的 JWT payload 裡的 `device_token` 欄位 |
| `localcode` | 台灣填 `tw`（香港 `hk`、新加坡 `sg`） |

另外三個 Perseus 欄位（`client_id`、`session_id`、`user_source`）是選填的，
但填了會讓 API 回應更完整。

### 怎麼拿 device_token

access token 是 JWT，把中間那段用 base64 解開就能看到：

```bash
echo "<token 的第二段>" | base64 -d | python3 -m json.tool
```

裡面的 `device_token` 就是需要的值。

## 用法

### 地圖上看送餐者

```yaml
type: map
entities:
  - entity: device_tracker.fp_tracker_送餐者
  - entity: zone.家
```

### 狀態變化自動通知

整合預設會在訂單狀態改變時自動發通知（用 HA 的 `notify` 服務）。
要接到 Telegram，確認你的 `notify` 服務有綁 `telegram` 即可。

不想自動通知就先在 `notify` 服務上關掉，或手動叫：

```yaml
action: fp_tracker.notify
data:
  title: foodpanda 配送狀態
```

### 手動立即更新

```yaml
action: fp_tracker.refresh
```

## 技術細節

### 為什麼用 requests 而不是 aiohttp

foodpanda 前面的 PerimeterX bot protection 會檢查 **TLS 指紋**：

- `aiohttp` → HTTP 403 + captcha
- `requests` → 正常通過

`requests` 是同步的，所以呼叫都丟到 executor
（`hass.async_add_executor_job`），不會阻塞 event loop。

### API 欄位

實測確認過的資料形狀：

- 訂單列表在 `data["items"]`（**不是** `data["orders"]`）
- 配送狀態在 `current_status`，但**可讀訊息會隨 App 版本改變**
  （`shop.order.status.message_order_delivered` 變成
  `RESTAURANTS_OTP_OD_PROGRESS_DELIVERED`），所以**狀態判斷以 `status_flags` 為準**
- 送餐者位置在 `courier`，只在**進行中**的訂單有值
- 即時追蹤端點是 `/api/v5/tracking/orders/<order_code>`，
  會回傳 `courier`、`tracking`、`map`、`eta`、`delay`、`delivery_time_range`

### 剩餘時間怎麼估

foodpanda 不一定給 ETA。整合用送餐者 GPS 與送達地點的直線距離，
除以市區平均車速 25 km/h 推估。直線距離一定小於實際路徑，
所以是**下限**。API 有給 ETA 時以 API 的為準。

距離小於 80 公尺時會直接判定為「已抵達」。

## 已知限制

- foodpanda 沒有公開 API，這是逆向其 App 的 Web API。App 改版可能會失效。
- Token 過期需要重新設定整合（整合會自動嘗試 refresh 一次）。
- 已完成的訂單不會保留送餐者位置，只有進行中的訂單能追蹤。

## 授權

MIT
