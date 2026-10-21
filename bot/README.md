# 關注清單機器人（Cloudflare Worker）

Discord 互動端點（下拉選單加入關注、按鈕取消關注、`/watchlist`、`/notify`）＋**每人一份**關注清單（D35，以 Discord 使用者 ID 區分）
＋網站「用 Discord 登入」（OAuth2，scope `identify`）＋給 GitHub Actions 的伺服器 API。
介面規格：`../docs/watchlist-bot-design.md`（D35 一節）。純 ES module，無 build、無 npm 執行期依賴。

## 檔案
- `src/worker.js`：Worker 本體（`POST /interactions`、`GET|POST /api/watchlist`、`GET /auth/login`、`GET /auth/callback`、`/api/me*`）。
- `wrangler.toml`：Worker 名稱、KV binding、`[vars] DISCORD_CLIENT_ID`（公開的 Application ID）。不含任何 secret。
- `register-commands.mjs`：註冊 `/watchlist`、`/notify` 斜線指令。
- `test/worker.test.mjs`：`node:test` 測試（記憶體假 KV、測試用 Ed25519 金鑰對、假 Discord OAuth 回應，不連網）。

## Worker secrets（`npx wrangler secret put <名稱>`，在 `bot/` 內執行）
| 名稱 | 內容 |
|---|---|
| `DISCORD_PUBLIC_KEY` | Developer Portal → General Information → Public Key |
| `SYNC_KEY` | 隨機長字串；與 GitHub secret `WATCHLIST_SYNC_KEY` 同一組（只給 GitHub Actions 用，網站不再需要） |
| `DISCORD_CLIENT_SECRET` | Developer Portal → OAuth2 → Client Secret（D35，網站登入用） |
| `SESSION_SECRET` | 隨機長字串（≥32 bytes），簽 OAuth state 與網站 session（D35）；輪替＝所有人登出 |

缺 `DISCORD_CLIENT_SECRET` 或 `SESSION_SECRET` 時其他功能照常，網站按登入會回到原頁並顯示「登入暫時無法使用」。

## 部署步驟（需要 Cloudflare 帳號；在 `bot/` 資料夾內執行）
1. `npx wrangler login`
2. `npx wrangler kv namespace create WATCHLIST`，把輸出的 `id` 貼進 `wrangler.toml`（已填）。
3. 依上表設定四個 secret。
4. `npx wrangler deploy`，記下 Worker 網址（`https://ihs-watchlist-bot.<子網域>.workers.dev`）。
5. Discord Developer Portal → 你的應用程式：
   - General Information → **Interactions Endpoint URL** 填 `<Worker 網址>/interactions`（Discord 會送 PING 驗證）。
   - **OAuth2 → Redirects** 加一筆 `<Worker 網址>/auth/callback`（必須完全相同，否則 Discord 會拒絕登入）。
6. 註冊斜線指令：GitHub → Actions → **Discord channel IDs (setup)** → Run workflow（`guild_id` 填伺服器 ID 可立即生效；空白＝全域，最久約 1 小時）。
   或本機：`DISCORD_APPLICATION_ID=... DISCORD_BOT_TOKEN=... node register-commands.mjs`（可加 `DISCORD_GUILD_ID`）。
7. 網站：設定 → 雲端同步 →「用 Discord 登入」。

## 本機測試
`node --test "bot/test/*.test.mjs"`（專案根目錄；Node 22 在 Windows 上不接受目錄參數，要用萬用字元，
或進 `bot/` 後直接 `node --test`）。

## API 摘要
- 伺服器（`Authorization: Bearer <SYNC_KEY>`）：
  - `GET /api/watchlist` → `{"users":[{"user_id","username","notify","items":[...]}]}`
  - `POST /api/watchlist` `{"update":[{"user_id","pattern_key","last_status","last_notified_at","name"?}]}` → `{"ok":true,"updated":n}`；
    `{"delete_legacy":true}` 刪除 D34 遺留的共用 key `items`。
- 網站（`Authorization: Bearer <session>`，session 由 `/auth/callback` 放在回跳網址的 `#ihs_session=`）：
  `GET /api/me`、`GET|POST /api/me/watchlist`（`{"add":[...],"remove":[...]}`，每人上限 200 筆）、`POST /api/me/notify`（`{"mode":"channel"|"dm"}`）。
- 登入：`GET /auth/login?return=<網址>`（return 只接受 `https://chasen8.github.io/…` 與本機 `http://127.0.0.1|localhost`）→ Discord → `GET /auth/callback`。
- CORS 只允許 `https://chasen8.github.io` 與本機 `http://127.0.0.1:*`、`http://localhost:*`。
- 錯誤回應只有 `{"error": "unauthorized" | "bad_request" | "not_found" | "method_not_allowed" | "internal_error"}`，不含任何密鑰、code、token 或內部訊息。

## 注意
- KV 是最終一致且讀改寫沒有鎖：同一位使用者同一秒內兩個寫入可能互相覆蓋其中一個（每人分開存之後機率更低）；網站下次載入會把本機有、伺服器沒有的補上傳。
  新使用者的索引 `users` 若被覆蓋，伺服器 GET 會用 KV list 補回。
- 輪替 `SYNC_KEY`：重設 Worker secret 並同步更新 GitHub secret `WATCHLIST_SYNC_KEY`。
