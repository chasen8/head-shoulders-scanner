# 關注清單機器人（Cloudflare Worker）

Discord 互動端點（下拉選單加入關注、按鈕取消關注、`/watchlist`）＋關注清單 API（網站星號與 Python 通知共用）。
介面規格：`../docs/watchlist-bot-design.md`。純 ES module，無 build、無 npm 執行期依賴。

## 檔案
- `src/worker.js`：Worker 本體（`POST /interactions`、`GET|POST /api/watchlist`）。
- `wrangler.toml`：Worker 名稱、KV binding（`id` 部署前要填）。不含任何 secret。
- `register-commands.mjs`：選配，註冊 `/watchlist` 斜線指令。
- `test/worker.test.mjs`：`node:test` 測試（記憶體假 KV、測試用 Ed25519 金鑰對）。

## 部署步驟（需要 Cloudflare 帳號；在 `bot/` 資料夾內執行）
1. `npx wrangler login`
2. `npx wrangler kv namespace create WATCHLIST`，把輸出的 `id` 貼進 `wrangler.toml` 的 `REPLACE_WITH_KV_NAMESPACE_ID`。
3. `npx wrangler secret put DISCORD_PUBLIC_KEY`（Discord Developer Portal → General Information → Public Key）
4. `npx wrangler secret put SYNC_KEY`（隨機長字串；GitHub secret `WATCHLIST_SYNC_KEY` 與網站「同步金鑰」用同一組）
5. `npx wrangler deploy`，記下 Worker 網址（`https://ihs-watchlist-bot.<子網域>.workers.dev`）。
6. Discord Developer Portal → 你的應用程式 → General Information → **Interactions Endpoint URL**
   填 `<Worker 網址>/interactions`，按儲存（Discord 會送 PING 驗證，通過才存得進去）。
7. （選配）註冊斜線指令：
   `DISCORD_APPLICATION_ID=... DISCORD_BOT_TOKEN=... node register-commands.mjs`
   （可加 `DISCORD_GUILD_ID` 只註冊到單一伺服器，立即生效）。
8. 網站：設定 → 雲端同步，填 Worker 網址與同步金鑰，按「測試連線」。

## 本機測試
`node --test "bot/test/*.test.mjs"`（專案根目錄；Node 22 在 Windows 上不接受目錄參數，要用萬用字元，
或進 `bot/` 後直接 `node --test`）。

## API 摘要
- `GET /api/watchlist`：`Authorization: Bearer <SYNC_KEY>` → `{"items":[...]}`
- `POST /api/watchlist`：`{"add":[item...], "remove":[pattern_key...], "update":[{"pattern_key","last_status","last_notified_at"}]}`
  → 回傳更新後的 `{"items":[...]}`。以 `pattern_key` 去重，上限 500 筆。
- CORS 只允許 `https://chasen8.github.io` 與本機 `http://127.0.0.1:*`、`http://localhost:*`。
- 錯誤回應只有 `{"error": "unauthorized" | "bad_request" | "not_found" | "internal_error"}`，不含任何密鑰或內部訊息。

## 注意
- KV 是最終一致且讀改寫沒有鎖：同一秒內兩個寫入可能互相覆蓋其中一個。個人用量可接受；網站下次載入會把本機有、伺服器沒有的補上傳。
- 輪替 `SYNC_KEY`：重跑第 4 步，並同步更新 GitHub secret 與網站設定。
