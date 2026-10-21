# 關注清單機器人設計（方案 C，D34）

使用者 2026-10-22 決定：
1. 「右肩確認」（status watchlist）改成**每次掃描一張表格**（加密貨幣每 4 小時、台股每日），不再逐筆；
   右肩形成中、突破維持逐筆附圖（D24/D32）。
2. 關注清單用 **Discord 機器人＋Cloudflare Worker**：表格訊息下方有下拉選單，點選立即加入關注；
   網站星號同步到同一份名單。
3. 關注頻道只通知**狀態改變**（forming → watchlist → confirmed，以及 stale／消失）。

## 元件

```
GitHub Actions（掃描，Python）
  ├─ 右肩確認表格 → 機器人發到「主頻道」（附表格圖＋下拉選單 watch_select）
  │                  機器人未設定時退回 webhook（只有表格圖，沒有選單）
  └─ 關注通知     → GET Worker /api/watchlist → 比對狀態 → 機器人發到「關注頻道」（附型態圖＋「取消關注」按鈕）
                     → POST Worker /api/watchlist 更新 last_status
Cloudflare Worker（bot/，JS，無 build）
  ├─ POST /interactions   Discord 互動端點（Ed25519 驗簽）
  └─ /api/watchlist       GET / POST，Bearer SYNC_KEY；CORS 允許 https://chasen8.github.io
  儲存：KV namespace WATCHLIST，key "items" = JSON 陣列
網站（site/）
  └─ 設定填入「同步金鑰」後，星號加／取消時呼叫 Worker API；載入時合併伺服器名單
```

## 關注項目格式（三方共用）

```json
{
  "pattern_key": "binance|spot|STARUSDT|4h|2026-09-01T00:00:00Z|2026-09-20T08:00:00Z",
  "market": "crypto",            // "crypto" | "tw"
  "symbol": "STARUSDT",
  "name": null,                  // 台股中文名，例如 "台積電"
  "timeframe": "4h",
  "added_at": "2026-10-22T08:00:00Z",
  "added_via": "discord",        // "discord" | "site" | "api"
  "last_status": "watchlist",    // 加入當下或上次通知時的狀態；null = 未知
  "last_notified_at": null
}
```
`pattern_key` 定義見 `ihs_scanner/keys.py`（不含 RS，D24）。同一 pattern_key 只存一筆。

## Worker API

| 方法 | 路徑 | 認證 | 內容 |
|---|---|---|---|
| POST | /interactions | Discord 簽章（X-Signature-Ed25519、X-Signature-Timestamp，公鑰 DISCORD_PUBLIC_KEY） | 見下 |
| GET | /api/watchlist | `Authorization: Bearer <SYNC_KEY>` | `{"items":[...]}` |
| POST | /api/watchlist | 同上 | `{"add":[item...], "remove":[pattern_key...], "update":[{"pattern_key","last_status","last_notified_at"}]}` → 回傳更新後 `{"items":[...]}` |
| OPTIONS | /api/watchlist | — | CORS 預檢 |

錯誤：401 未授權、400 格式錯誤（不回傳任何密鑰內容）。

## Discord 互動

- PING（type 1）→ `{"type":1}`。
- 元件 `custom_id = "watch_select"`（string select，可多選）：每個選項的 value＝pattern_key（≤100 字元；
  超過時用下方 sha1 規則）。Worker 從 pattern_key 解析 exchange|market|symbol|tf（keys.py 格式），
  market 由 exchange 推得（twse/tpex → tw，其餘 → crypto）；台股中文名從選項 label 取不到，name 可為 null，
  之後由 Python 端在下次掃描時補上（POST update）。last_status 先記 "watchlist"（表格只列右肩確認）。
  回覆 ephemeral（flags 64）：「已加入關注：STARUSDT 4h、…」。
- 元件 `custom_id = "unwatch:<pattern_key>"`（按鈕，關注頻道每則通知附一個）：移除後 ephemeral 回覆「已取消關注」。
- 斜線指令（選配）`/watchlist`：ephemeral 列出目前關注。

custom_id 上限 100 字元：`unwatch:` + pattern_key 若超過，改用 pattern_key 的 sha1 前 16 碼，
Worker 以同樣雜湊比對（兩邊共用 `sha1(pattern_key)[:16]` 規則）。

## 機器人發訊（Python，GitHub Actions）

- `POST https://discord.com/api/v10/channels/{channel_id}/messages`，`Authorization: Bot <token>`，multipart（payload_json＋files）。
- 主頻道表格訊息：表格圖（PNG，中文用圖內英數＋代號；台股加代號，中文名放選單 label）＋最多 5 個 action row，
  每個 string select 最多 25 個選項；超過 125 筆就分多則訊息。選項 label 例：「#3 STARUSDT 4h 83分」。
- 關注頻道：每個狀態改變一則 embed＋型態圖（chart_image.py）＋「取消關注」按鈕。

## GitHub Secrets／變數

| 名稱 | 誰設定 | 用途 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | 已設定 | 逐筆通知（不變） |
| `DISCORD_BOT_TOKEN` | 使用者自己 `gh secret set` | 機器人發訊 |
| `DISCORD_MAIN_CHANNEL_ID`、`DISCORD_WATCH_CHANNEL_ID` | 頻道 ID（非機密） | 發訊目標 |
| `WATCHLIST_API_URL` | Worker 網址 | 讀寫關注清單 |
| `WATCHLIST_SYNC_KEY` | 主對話隨機產生後直接寫入 secret，不顯示 | API 認證 |

Worker secrets：`DISCORD_PUBLIC_KEY`（非機密）、`SYNC_KEY`（同上，與 GitHub 共用）。
所有密鑰不進 repo、不進對話；網站的同步金鑰只存在使用者瀏覽器 localStorage。

## 未設定時的降級
- 沒有機器人 token：表格改用 webhook 發（無選單）；關注通知略過並在 log 說明。
- 沒有 Worker 網址／金鑰：關注通知略過；網站星號維持只存本機。
