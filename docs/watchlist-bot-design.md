# 關注清單機器人設計（方案 C，D34；每人一份清單＋Discord 登入，D35）

> **D35（2026-10-22）取代了本文 D34 的「共用一份清單」「網站同步金鑰」「/api/watchlist 的 items 結構」**，見文末〈D35〉一節；下面 D34 各節中標「（已被 D35 取代）」的內容只留作歷史。表格訊息、下拉選單、按鈕 custom_id、sha1 規則、降級規則仍有效。

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
  儲存：KV namespace WATCHLIST，key "items" = JSON 陣列（已被 D35 取代：改為每人一個 key）
網站（site/）
  └─ 設定填入「同步金鑰」後，星號加／取消時呼叫 Worker API；載入時合併伺服器名單（已被 D35 取代：改用 Discord 登入）
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

## Worker API（已被 D35 取代）

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
所有密鑰不進 repo、不進對話。（D34 網站同步金鑰已被 D35 取代：網站不再持有 SYNC_KEY。）

## 未設定時的降級
- 沒有機器人 token：表格改用 webhook 發（無選單）；關注通知略過並在 log 說明。
- 沒有 Worker 網址／金鑰：關注通知略過；網站星號維持只存本機。

## D35：每人一份清單、通知方式每人可選、網站用 Discord 登入（2026-10-22）

使用者決定讓 Discord 伺服器的朋友也能用：(1) 每人一份清單，以 Discord 使用者 ID 區分；(2) 通知方式每人可選——在 #關注 頻道被 @，
或機器人私訊；(3) 網站改成「用 Discord 登入」同步星號，取代同步金鑰。

### 儲存（KV `WATCHLIST`）

| key | 內容 |
|---|---|
| `user:<discord_user_id>` | `{"user_id","username","notify":"channel"\|"dm","items":[item…]}`；notify 預設 `channel`；每人上限 **200** 筆 |
| `users` | user_id 陣列（索引）。KV 無鎖，新使用者同時寫入可能掉一筆索引 → 伺服器 GET 另外以 `list({prefix:"user:"})` 補齊 |
| `items` | D34 共用清單：**忽略**（不知道是誰加的，不遷移）；可用 `POST /api/watchlist {"delete_legacy":true}` 刪除 |

item 格式同 D34。使用者第一次加入／改通知方式／網站登入時建立紀錄。

### Discord 互動（全部只動點選者自己的清單；回覆都是 ephemeral）

- 使用者 ID：伺服器內 `interaction.member.user.id`，私訊內 `interaction.user.id`；取不到就拒絕。
- `watch_select`／`watch_select_N`：加到點選者的清單（表格訊息不分人，誰點就加給誰）；第一次會附一句「要改私訊請用 /notify」。
- `unwatch:<pattern_key|sha1前16>`：只移除點選者自己的；不在自己清單 →「這筆不在你的關注清單（可能已取消，或是別人關注的）」。
  私訊裡的按鈕同樣有效。
- `/watchlist`：列出自己的清單（最多 40 行）＋目前通知方式。
- `/notify mode:<channel|dm>`（必填 choice）：設定通知方式；選 dm 時提醒「若收不到請到伺服器隱私設定允許成員私訊」。
- 指令註冊：`bot/register-commands.mjs`（PUT 全部指令）；`.github/workflows/discord-channels.yml` 最後一步會用 `DISCORD_BOT_TOKEN`
  查 application id 後執行它（input `guild_id` 可只註冊到單一伺服器、立即生效；空白＝全域，最久約 1 小時）。

### Worker API

| 方法 | 路徑 | 認證 | 內容 |
|---|---|---|---|
| GET | `/api/watchlist` | `Bearer <SYNC_KEY>`（GitHub Actions） | `{"users":[{"user_id","username","notify","items":[…]}]}` |
| POST | `/api/watchlist` | 同上 | `{"update":[{"user_id","pattern_key","last_status"?,"last_notified_at"?,"name"?}], "delete_legacy"?:true}` → `{"ok":true,"updated":n}` |
| GET | `/auth/login?return=<url>` | — | 302 到 Discord 授權頁（見下） |
| GET | `/auth/callback?code&state` | state 簽章＋cookie | 302 回 return URL `#ihs_session=…` |
| GET | `/api/me` | `Bearer <session>`（網站） | `{"user_id","username","notify"}` |
| GET | `/api/me/watchlist` | 同上 | `{"items":[…]}` |
| POST | `/api/me/watchlist` | 同上 | `{"add":[item…],"remove":[pattern_key…]}` → `{"items":[…]}`（先 remove 再 add，去重、上限 200） |
| POST | `/api/me/notify` | 同上 | `{"mode":"channel"\|"dm"}` → `{"notify":…}` |
| OPTIONS | `/api/*` | — | CORS 預檢（同 D34：`https://chasen8.github.io` 與本機 `http://127.0.0.1:*`／`localhost:*`） |

錯誤只回 `{"error":"unauthorized"|"bad_request"|"not_found"|"method_not_allowed"|"internal_error"}`；登入頁錯誤是簡短中文 HTML。
code、access token、session、secret 不寫 log、不出現在任何回應或錯誤裡。SYNC_KEY 不能當 session 用，session 也不能打伺服器 API。

### 網站登入（Discord OAuth2 authorization code，scope `identify`）

1. 網站連結 `GET /auth/login?return=<目前頁面網址>`。return 白名單：`https://chasen8.github.io/` 開頭、`http://127.0.0.1[:port]/`、
   `http://localhost[:port]/`（本機預覽）；其他一律 400，不轉址。return 裡的 `#…` 會去掉。
2. Worker 產生 nonce，state = `base64url(JSON{r:return,n:nonce,exp:+10 分鐘})` + `.` + HMAC-SHA256（`SESSION_SECRET`，簽的是 `"o1."+payload`）；
   同時設 cookie `ihs_oauth=<nonce>`（HttpOnly、Secure、SameSite=Lax、Path=/auth、10 分鐘），302 到
   `https://discord.com/oauth2/authorize?client_id=<DISCORD_CLIENT_ID>&response_type=code&scope=identify&redirect_uri=<worker>/auth/callback&state=…&prompt=none`
   （`prompt=none`：授權過的人不再看到同意頁）。
3. `GET /auth/callback`：驗 state 簽章＋未過期＋return 仍在白名單，且 cookie nonce 相符（防 login CSRF：別人做的 callback 連結在你瀏覽器會失敗）；
   否則 400。使用者取消（`error=`）→ 302 回 `return#ihs_login_error=cancelled`。
4. 用 `DISCORD_CLIENT_ID`（wrangler.toml `[vars]`，非機密）＋`DISCORD_CLIENT_SECRET`（Worker secret）POST `https://discord.com/api/v10/oauth2/token`
   換 access token → `GET /api/v10/users/@me` 取 `id`、`username`。任一步失敗 → `#ihs_login_error=discord`（不帶細節）。
   登入功能未設定（缺 client id／secret／SESSION_SECRET）→ `#ihs_login_error=unavailable`。
5. 建立／更新使用者紀錄的 username，簽 session = `base64url(JSON{u:user_id,n:username,exp:+30 天})` + `.` + HMAC（簽 `"s1."+payload`），
   302 回 `return#ihs_session=<session>`（fragment 不會送到任何伺服器，不進 access log），清掉 `ihs_oauth` cookie，`Referrer-Policy: no-referrer`。
6. 網站存 localStorage `ihs.session.v1` 並清 fragment；之後 `/api/me/*` 帶 `Authorization: Bearer <session>`；401 → 提示重新登入，本機星號保留。
   沒有伺服器端 session 撤銷：輪替 `SESSION_SECRET` 會讓所有人登出。

### 通知（`notify_watchlist.py`，GitHub Actions）

- GET `/api/watchlist` → 每位使用者、此市場（crypto／tw）的項目逐一比對（規則同 D34：狀態改變才通知、消失記 `gone` 一次、`last_status` null＝首次只記錄、
  台股缺名補名）。**每人各自的 `last_status`**。
- 分組：同一 `pattern_key` 的同一變化（previous → current）合併。
  - `channel` 的人：**一則** #關注 訊息，content `<@id1> <@id2>`，`allowed_mentions: {"parse":[],"users":[…]}`（只會 @ 這些人，
    不會 @everyone／身分組）；超過 50 人分多則。
  - `dm` 的人：`POST /api/v10/users/@me/channels {"recipient_id"}` 取得私訊頻道再發同一則（無 content，`allowed_mentions.parse=[]`）。
    **403**（對方關閉伺服器成員私訊）→ 改在 #關注 @ 他並註明「私訊失敗…」（和同組的頻道使用者合成一則）；其他失敗 → 不記錄，下次重試。
  - 每則都附型態圖＋「取消關注」按鈕（Worker 只移除按的人自己的）。
- 送達的人才 POST update（帶 user_id）；沒送達的下次再發。沒有機器人 token → 全部略過不記錄；沒有關注頻道 → 頻道對象略過不記錄，私訊照發。
- API／Discord 失敗不讓 workflow 失敗；token、SYNC_KEY、API 網址都不印。

### Secrets（D35 新增）

| 名稱 | 位置 | 說明 |
|---|---|---|
| `DISCORD_CLIENT_ID` | wrangler.toml `[vars]` | = Application ID，公開 |
| `DISCORD_CLIENT_SECRET` | Worker secret | Discord Developer Portal → OAuth2 → Client Secret |
| `SESSION_SECRET` | Worker secret | 隨機長字串（≥32 bytes），簽 state 與 session；輪替＝全部登出 |

Discord Developer Portal → OAuth2 → Redirects 要加 `<Worker 網址>/auth/callback`。GitHub secrets 不變（`WATCHLIST_SYNC_KEY` 只剩 Actions 用）。
