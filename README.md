# foodpanda 即時追蹤 (fp_tracker)

追蹤 foodpanda 訂單的**即時**狀態：送餐者在哪、訂單到哪個階段、還要多久到。

上游的 [foodpanda](https://github.com/tsunglung/foodpanda) 整合只查訂單歷史，
`courier` 欄位對已完成的訂單永遠是空的，而且它讀錯了 API 的欄位名。
這個整合用對的欄位，並且在你訂單進行中時持續更新。

## 會產生哪些實體

**同時可追蹤最多 3 張進行中的訂單。** 每一張訂單佔一個「槽位」，
槽位編號從 1 開始，實體名稱如下：

| 實體 | 型別 | 說明 |
|------|------|------|
| `sensor.foodpanda_訂單_N_配送狀態` | sensor | 餐廳備餐中／已取餐配送中／已抵達／已送達 |
| `sensor.foodpanda_訂單_N_預計送達` | sensor | foodpanda App 顯示的分鐘數 |
| `sensor.foodpanda_訂單_N_預估剩餘時間` | sensor | 用送餐者 GPS 位置推估的分鐘數 |
| `sensor.foodpanda_訂單_N_距離送達地點` | sensor | 送餐者到送達地點的距離 |
| `binary_sensor.foodpanda_訂單_N_配送中` | binary_sensor | 這個槽位有訂單進行中 |
| `binary_sensor.foodpanda_訂單_N_送餐者已接近` | binary_sensor | 送餐者已到附近 |
| `device_tracker.foodpanda_訂單_N_送餐者` | device_tracker | 送餐者位置，可放進地圖卡片 |

`N` 是槽位編號（1、2、3）。槽位是**固定的**：不管當下有幾張訂單，
三組實體都會存在，沒有對應訂單的槽位顯示 `unavailable`。

這樣做的好處是 entity id 不會隨訂單增減而改變，放在 Lovelace 裡的卡片
不會突然壞掉，entity registry 也不會愈積愈多。

### 一張訂單的完整資料

`配送狀態` 感測器的屬性包含：

| 屬性 | 說明 |
|------|------|
| `order_code` | foodpanda 訂單代碼 |
| `restaurant` | 餐廳名稱 |
| `status` | 狀態代碼（`preparing` / `picked_up` / `arrived` / `delivered` …） |
| `status_code` | foodpanda API 的數字狀態碼 |
| `courier_name` / `courier_phone` / `courier_vehicle` | 送餐者資訊 |
| `vendor_latitude` / `vendor_longitude` | 餐廳座標 |
| `delivery_latitude` / `delivery_longitude` | 你的收件地址座標 |
| `changed_at` | 狀態最後變動的時間 |

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
| `token` | 瀏覽器登入 foodpanda → F12 → Application → Cookies → `www.foodpanda.com` → `token` |
| `refresh_token` | 同上，`refresh_token` |
| `device_token` | 同上，`device_token` |
| `user_source` | 同上，`userSource`（台灣是 `volo`） |
| `localcode` | 台灣填 `tw`（香港 `hk`、新加坡 `sg`） |

`client_id` 與 `session_id` 是選填的，填了會讓 API 回應更完整；
第一次 refresh 之後整合會自己從 cookie 補上。

### user_source 一定要填

少了 `user_source`，foodpanda API 會回 **HTTP 403**，設定流程會直接失敗。
它送出去的是 `X-Fp-Api-Key` 這個 header，而 **header 的值不重要，有這個 header 就行** ——
填 `volo` 就對了。

### 怎麼拿這些 token

四個值都在瀏覽器的 cookie 裡：

1. 用 Chrome 或 Edge 登入 foodpanda
2. `F12` → **Application** 分頁
3. 左邊 **Storage → Cookies → `https://www.foodpanda.com`**
4. 上方 Filter 輸入 `token`（`userSource` 要另外搜一次）

會看到這幾筆：

| Cookie 名稱 | 填到哪個欄位 |
|---|---|
| `token` | token |
| `device_token` | device_token |
| `refresh_token` | refresh_token |
| `userSource` | user_source |

> ⚠️ `token` 和 `device_token` 長得很像，都是 JWT，貼錯欄位 API 只會回 401。
> 一個快速檢查法：把值貼到終端機跑
> `echo "<貼的那串>" | cut -d. -f1 | base64 -d`
> 看到 `keymaker-volo-fp-tw` 是 access token，
> 看到 `keymaker-volo-fp-device-tw` 是 device token（多一個 `-device-`）。

> Cookie 值在 Chrome 的面板裡可能是 percent-encoded 的，
> 值的內容裡不該出現 `%` 或 `+`；有的話要先 decode 再貼。

## 用法

### 地圖上看送餐者

```yaml
type: map
entities:
  - entity: device_tracker.foodpanda_訂單_1_送餐者
  - entity: zone.家
```

兩張訂單同時進行時，把兩個槽位都放進去：

```yaml
type: map
entities:
  - entity: device_tracker.foodpanda_訂單_1_送餐者
  - entity: device_tracker.foodpanda_訂單_2_送餐者
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

### 狀態判斷

`status_flags` 是最可靠的，但**旗標不一定跟得上狀態訊息**：
實測有訂單已經 `RESTAURANTS_OTP_OD_PROGRESS_PICKED`（已取餐），
`is_picked_up` 卻還是 `false`。

所以判斷順序是：

1. `is_canceled` / `is_delivered` / `is_completed` → 取消或送達
2. `is_picked_up` → 已取餐
3. 狀態訊息含 `PICKED` / `ON_THE_WAY` → 已取餐
4. 狀態訊息含 `ARRIVED` / `DELIVERED` → 已抵達／已送達
5. `is_active` + 狀態訊息含 `PREPARING` / `COOKING` → 備餐中
6. 其餘 → 未知

### Token 會自動更新

foodpanda 的 access token 只有幾十分鐘的有效期。整合在 token 過期時
會自動用 `refresh_token` 換新的，並且**存回自己的儲存空間**
（`/config/.storage/fp_tracker/credentials`），所以重啟 HA 之後
不會又拿到過期的 token。

存儲空間是獨立於 config entry 的，因為寫回 config entry 會觸發
update listener，把正在跑的 coordinator 一起重載掉。

換 token 的端點是 `https://www.foodpanda.com.tw/login/new/api/refresh-token`，
**少了 `/login/new/api` 這段會得到 HTTP 404**。

### 剩餘時間怎麼估

foodpanda 不一定給 ETA。整合用送餐者 GPS 與送達地點的直線距離，
除以市區平均車速 25 km/h 推估。直線距離一定小於實際路徑，
所以是**下限**。API 有給 ETA 時以 API 的為準。

距離小於 80 公尺時會直接判定為「已抵達」。

## 已知限制

- foodpanda 沒有公開 API，這是逆向其 App 的 Web API。App 改版可能會失效。
- 已完成的訂單不會保留送餐者位置，只有進行中的訂單能追蹤。
- 沒有進行中的訂單時，槽位顯示 `unavailable`，這是正常行為。
- 同時最多追蹤 3 張訂單（`MAX_ORDERS`），超過的就不追了。
- 改到整合的 Python 檔之後，**要重啟 Home Assistant** 才會生效：
  自訂整合的「重新載入」不會清掉 Python 的模組快取。

## 開發與驗證

```bash
# 語法檢查
python3 -c "import ast,glob;[ast.parse(open(f).read()) for f in glob.glob('custom_components/fp_tracker/*.py')]"

# 邏輯測試（狀態機、ETA 解析、距離計算、多訂單排序）
python3 tests/test_logic.py
python3 tests/test_multi_order.py

# 驗證 API 欄位（需要有效的 token）
curl -sk -H "Authorization: Bearer <token>" \
  "https://tw.fd-api.com/api/v5/orders/order_history?include=order_products,order_details" | head -c 500
```

## 授權

MIT
