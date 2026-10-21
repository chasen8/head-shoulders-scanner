# 頭肩底V2 Project Charter

## 目前狀態
- 2026-10-16：規格落檔（`docs/spec.md`）、GitHub 開源調查完成（`docs/github-research.md`）。
  第 1 輪：偵測引擎＋幣安公開 K 線抓取＋CLI 掃描＋測試。
- 2026-10-18：第 1 輪完成。`ihs_scanner/` + `scan.py`，35 個 pytest 全綠（含截斷／不 repaint 測試）；
  實跑 BTC/ETH/SOL × 4h/1d/1w 找到 2 筆，BTC 1w 一筆已用原始 K 線手算核對（肩差、頭深、角度、頸線、四項分數）。
  單一 symbol/timeframe 1000 根偵測約 10 ms。
- 2026-10-18：Q1–Q3 定案（使用者：「GitHub 有前例就參考，沒有就照你的」），見 D13–D15；36 個測試全綠，實跑結果不變。
- 2026-10-18：網站第 1 版完成：`build_site.py` → `site/data/scan.json`，`site/`（靜態、無 build），
  設計說明 `docs/site-design.md`；`.github/workflows/scan-and-deploy.yml` 目前只手動觸發、上傳 artifact（D17）。
  44 個 pytest 全綠。正式資料（不含 stale）4 筆。
- 下一步：決定部署方式（公開 repo＋Pages 或其他）後啟用排程；Discord webhook 通知。
- 2026-10-18：推上私有 repo `chasen8/head-shoulders-scanner`（commit 198e05e）；手動觸發 workflow 在 GitHub 上跑通（pytest＋掃描＋上傳 site artifact 全綠）。下一步：Discord 通知。

- 2026-10-18：使用者回饋第 2 輪：(1) 掃描改為幣安全部現貨 USDT 對（約 497 個，排除穩定幣與槓桿代幣，weight 節流）；(2) 網站改 TradingView 淺色乾淨風格（`docs/site-design.md`），scan.json 與單筆 bars 拆檔；(3) 加 balance／symmetry 規則與 pivot 5/5/3（D19）。82 個測試全綠。
- 2026-10-18：定時掃描＋Discord 通知上線（D16、D20），GitHub 上手動執行 3 次全綠；等使用者自己設定 `DISCORD_WEBHOOK_URL` secret。型態規則研究完成（`docs/pattern-rules-research.md`），下一步實作前 5 條推薦規則（D21）。
- 2026-10-18：D21 五條規則上線，127 個測試全綠；全幣種快照結果 42→12 筆。
- 2026-10-19：多交易所（1378 幣，D22）＋ template 規則（D23）完成，189 個測試全綠、5 個範例全過；下一步整合網站與 Discord（forming 狀態、TP、tv_symbol）。
- 2026-10-19：網站＋Discord 整合 forming／TP1／TP2／交易所標籤（D24），208 個測試全綠，GitHub dry-run 成功；全來源 1378 幣本機掃描 195 筆（forming 113／watchlist 76／confirmed 6）。待辦：使用者重建 webhook 並自行 `gh secret set`；已知 1MBABYDOGE（幣安）與 MBABYDOGE（Gate）未被去重。
- 2026-10-18：網站公開（D25）與 Discord webhook 設定完成。
- 2026-10-19：D26 形狀驗證上線（241 測試全綠）。結論：只有 5 個正樣本時公式驗證區分力有限，下一步需要使用者標記好／壞樣本。
- 2026-10-19：觀察名單、☰ 選單、頸線斜率篩選上線（D27），243 測試全綠，公開網站已更新。
- 2026-10-19：TP1 改對數斐波那契 1.414（D28）；台股上線（D29），271 測試全綠，公開網站同時有加密貨幣 151 筆與台股 665 筆。
- 2026-10-21：夜間模式（D31）與 Discord 附圖、全部列出（D32）上線，289 測試全綠。已知：台股 4h 每天只有 2 根，1000 根上限≈2 年，型態可能過長，待使用者決定是否縮短台股 4h 上限。
- 2026-10-22：D34 程式完成（pytest 324、Worker node 測試 11 全綠）；待使用者：註冊 Cloudflare、建 Discord 機器人並 `gh secret set DISCORD_BOT_TOKEN`、提供 Application ID／Public Key／兩個頻道 ID，之後主對話部署 Worker 與設定金鑰。
- 2026-10-22：D35 部署完成（Worker 測試 23、pytest 335 全綠；斜線指令已註冊到伺服器）。待使用者：Discord 後台 OAuth2 Redirect 加 `<worker>/auth/callback`、`wrangler secret put DISCORD_CLIENT_SECRET`，之後網站登入才會啟用。
- 2026-10-22：D36 上線（352 測試全綠，含 BTC/AT/LDO 三個新黃金案例）。
## 決策記錄
格式：日期｜決定｜原因｜何時可推翻

- D1 2026-10-16｜全新獨立專案，只照 `docs/spec.md`，不沿用 V1.1／V2-Hybrid 程式碼｜使用者選擇｜使用者改變主意
- D2 2026-10-16｜venv 與資料放 `C:\trading-data\ihs-scanner-v2\`｜大檔不進 OneDrive、與其他專案 venv 隔離｜—
- D3 2026-10-16｜資料源：幣安公開 klines，主端點 `https://data-api.binance.vision/api/v3/klines`，備援 `https://api.binance.com`；只保留已收盤 K 線（close_time < 現在）｜使用者選擇；`api.binance.com` 對美國 IP（含 GitHub Actions runner）回 451，`data-api.binance.vision` 是官方公開行情端點｜改用其他交易所或付費資料
- D4 2026-10-16｜Pivot 嚴格不等式（low 必須 **嚴格小於** 前後各 N 根）；pivot 在 index i、確認於 i+N；最後 N 根不產生 pivot｜規格第一步原文為「小於」；確認延遲避免 look-ahead（參考 neurotrader888）｜平盤 K 線過多導致漏抓時，可改左嚴格右寬鬆
- D5 2026-10-16｜N1 取 LS 與 H 之間**最高**的 swing high，N2 取 H 與 RS 之間最高的 swing high；沒有 swing high 則該組合不成立｜規格要求 N1/N2 為 swing high，取最高者最貼近「頸線」語意且讓組合確定性｜回測顯示取其他高點更好
- D6 2026-10-16｜額外結構條件：H 必須是 LS..RS 區間內最低的 low；型態總長 LS→RS ≤ `max_pattern_bars`（預設 4h 150、1d 120、1w 80）｜避免「假頭」與跨太久的無意義組合，也限制組合數｜回測後調整
- D7 2026-10-16｜失效：RS 之後、突破之前，任一根 low < RS.low → 該型態作廢｜右肩被跌破代表 RS 不是右肩，新右肩會以新組合出現｜—
- D8 2026-10-16｜突破＝RS 之後第一根 close > neckline(i)×(1+buffer)（且啟用 volume filter 時 volume_ratio ≥ min）；average_volume_20＝突破 K 線**之前** 20 根平均（不含自身）｜避免自身成交量拉高平均｜—
- D9 2026-10-16｜狀態欄 `status`：watchlist（未突破，RS 後 ≤ `max_bars_after_rs`）／confirmed（突破後 ≤ `max_breakout_age` 根）／stale（超過）；預設不輸出 stale｜通知只需要新鮮訊號（參考 SignalSync）｜—
- D10 2026-10-16｜candidate 模式下若已突破，breakout_score 也給 20｜規格只說 breakout 模式給 20，但已突破者距頸線分數會失真｜使用者另有規定
- D11 2026-10-16｜去重：同 symbol+timeframe 內，[LS,RS] 區間重疊的型態只留最高分（同分留較新）｜同一段走勢不重複列出（參考 neurotrader888 + SignalSync）｜—
- D12 2026-10-16｜current_close／current_neckline_price 取最後一根已收盤 K 線｜規格第九步｜—
- D13 2026-10-18｜新增 `signal_time` = max(RS_confirm_time, breakout_time)；回測進場與通知一律用 signal_time，不用 breakout_time；保留 RS 確認前發生的突破｜突破可能早於 RS 確認（例：BTC 1w 9/21 突破、9/28 才確認 RS），用 breakout_time 回測等於偷看未來；與 neurotrader888「型態在 confirm_idx 才存在」同理｜回測顯示 RS 確認前突破者表現明顯較差
- D14 2026-10-18｜啟用 volume filter 時，突破 = RS 後「第一根同時滿足價格與量比」的 K 線；低量穿越不作廢、維持 watchlist（新欄位 `first_cross_time`）；過期年齡一律從第一次價格穿越起算，之後放量再突破也不重置｜參考 SignalSync `evaluate_breakout`（作者實測：重置年齡讓假陽性從 1% 升到 5%）｜回測不支持
- D15 2026-10-18｜D9 依 timeframe 分開：`max_bars_after_rs` 4h 30／1d 20／1w 8；`max_breakout_age` 4h 6／1d 5／1w 3；去重在狀態篩選之後；分數四捨五入 2 位後排序｜GitHub 無多 timeframe 前例，以「4h 約 5 天、1d 約 4 週、1w 約 2 個月」換算的自訂值｜回測或通知頻率需要調整時
- D16 2026-10-18（同日修訂）｜repo `chasen8/head-shoulders-scanner` 私有、不部署 Pages；workflow `scan-and-notify.yml`：cron `7 */4 * * *`＋手動（dry_run/notify_all）→ pytest → build_site --scheduled → notify_discord → 推 orphan 分支 `scan-state`（state.json＋site/data，force push 單一 commit）→ artifact｜使用者要定時掃描＋Discord；規定排程走 GitHub Actions；免費私有 repo 不能開 Pages；實測每次約 2 分鐘，每月約 360 分鐘（額度 2000）｜改公開 repo、升級 Pro 或改託管
- D17 2026-10-18｜網站＝靜態頁讀 `site/data/scan.json`；所有數值（分數、頸線端點、`neckline_side`、`next_scan_at`）由 `build_site.py` 算，前端只格式化與繪圖；K 線圖用 Lightweight Charts 5.2.1 釘版；設計準則照 `docs/site-design.md`（frontend-design skill）｜偵測邏輯只有一份（Python），前端不會與掃描器分岔｜需要互動式重算時
- D19 2026-10-18｜加兩條形狀規則（neurotrader888 `check_ihs_pattern`，MIT，原始碼已核對）：balance（LS.low ≤ (RS.low+N2.high)/2 且 RS.low ≤ (LS.low+N1.high)/2）、symmetry（LS→H 與 H→RS 根數比 ≤ 2.5）；預設 pivot 改為規格區間上限 4h 5／1d 5／1w 3；選配（預設關）`min_pattern_bars`、`require_prior_downtrend`（SignalSync 概念）｜使用者指出 BTC 4h（LS→H 112 根、H→RS 7 根）看不出頭肩底，原規格只管價格不管時間形狀；5 個幣實測舊規則 4 筆→新規則 1 筆，BTC 1w（比值 2.91、LS 高於右中點）也被排除｜2.5 門檻未針對 4h/1d/1w 回測；回測顯示太嚴時放寬到 3 或關 balance
- D18 2026-10-18（同日修訂）｜build_site.py 部分成功：個別 symbol/timeframe 失敗記在 scan.json `failures`，剛上架無已收盤 K 線記在 `skipped`；失敗率 >20% 才非零退出且不覆寫舊 scan.json／bars（bars 先寫 `bars.tmp/` 再整個換上）；`next_scan_at` 只有排程觸發才填｜全幣種（約 500 個 USDT 對）時單一下市幣不該讓整批失敗；舊網站在中止時保持完整｜失敗率門檻需要調整時
- D20 2026-10-18｜Discord 通知：只發「新型態」與「watchlist→confirmed」，分數 ≥60（`NOTIFY_MIN_SCORE`）；首次只發摘要；沒設 webhook 時事件仍記為已通知（之後設定不補發）；送失敗的不記入 state、下次重試；消失型態保留 14 天｜避免首次與日後轟炸、避免重複通知｜使用者想要補發或不同門檻
- D21 2026-10-18｜再加 5 條形狀規則（預設全開，可個別關，`scan.py --rules-off`）：R1 頭比兩肩低 ≥1 ATR(14, Wilder)；R11 型態起點（LS 前價格曾高於頸線左延伸，比較用 **high**）；R3 頸線總傾斜 ≤15%；R2 肩差 ≤0.5×頭高；R21 肩深/頭高 ≥0.25｜使用者說型態還要修；來源 SignalSync、neurotrader888（MIT）、Savin et al.，見 `docs/pattern-rules-research.md`；全幣種快照 42→12 筆，主要由 R1/R11/R3 刷掉；R11 原始碼用 close，但本專案頸線用 high（D5），用 close 會誤擋 NEAR 1w 這種標準型態｜回測後；R2/R21 目前幾乎沒作用，可考慮收緊或移除
- D22 2026-10-19｜資料來源擴大為多交易所，一個 base 幣只掃一個來源，優先序 binance-spot → binance-perp → okx-spot → okx-perp → gate-perp；預設不含 binance-perp（fapi 對 GitHub runner 美國 IP 回 451，本機可用 `--sources all`）；結果加 exchange/market/tv_symbol，pattern_id 前綴來源；不設成交量門檻；workflow timeout 30 分｜使用者要全幣種含小幣，範例含幣安永續與 OKX；runner probe：OKX、Gate、MEXC、Bitget 可達，Bybit 403｜換執行環境（非美國 IP）時把 binance-perp 加回預設
- D23 2026-10-19｜偵測預設改為 `profile="template"`（依使用者 5 個 TradingView 範例校準，`tests/test_golden_examples.py` 鎖住）：肩膀比「各自頸線高點往下的跌幅%」對稱（取代規格第三步的低點差異率，後者改選配）、頭到頸線漲幅與突出度門檻、寬型態（最大 4h 1000／1d 600／1w 200 根）、新狀態 `forming`（右肩形成中，N2 確認後以至今最低點暫定 RS）、T1–T10 形狀規則；關閉 D19 時間對稱／balance 與 D21 R3／R11，R1 降為 0.5 ATR；`classic` 保留舊行為；輸出 tp1＝突破價＋頭高、tp2＝突破價×(1＋頭到頸線%)（使用者算法）、invalidation_price＝H.low｜使用者範例優先於原始規格文字；原規則把 5 個範例全擋掉｜T7–T10、寬度、突出度等門檻是校準值不是原理推導，1w 沒有範例；回測或使用者回饋後調整
- D24 2026-10-19｜Discord 通知修訂（取代 D20 的事件與門檻）：通知「新 forming」「新 watchlist」「任何→confirmed」，forming→watchlist 不通知；去重鍵不含 RS（`exchange|market|symbol|tf|LS_time|H_time`），state `version: 2`（舊版視為首次只發摘要）；門檻 50（`NOTIFY_MIN_SCORE`）；低於門檻者之後過門檻會補發一次｜使用者要在右肩區就收到提醒；forming 的 RS 會移動，含 RS 的鍵會重複通知；template 分數普遍 50–70｜每日通知量過多時提高門檻；已知限制：forming 期間即使收盤突破仍標 forming，突破通知最多延後 pivot N 根（4h/1d 5 根）
- D25 2026-10-18｜網站公開：程式 repo 保持私有，另建公開 repo `chasen8/head-shoulders-site`（Pages：main 根目錄，https://chasen8.github.io/head-shoulders-site/）；workflow 每次非 dry-run 執行後用 deploy key（只對該 repo 有寫入權，私鑰存 secret `SITE_DEPLOY_KEY`，本機不留副本）force push site/ 單一 commit；`DISCORD_WEBHOOK_URL` secret 已設定｜使用者要公開網站、不想開終端機、程式不公開｜改為整個 repo 公開或換託管時
- D26 2026-10-19｜template 加形狀驗證 V1–V4（全部通過才輸出）：V1 段內反向波動 ≤0.65 頭高（Lo 2000 連續極值概念）、V2 骨架偏差 MAD/頭高 ≤0.20（neurotrader888 pattern_r2 概念，R² 只顯示）、V3 頸線收盤完整性＋追高上限 0.5 頭高（SignalSync）、V4 對使用者 5 範例的模板距離 ≤0.25（Fu 2007，leave-one-out）；另列 shape_score，pattern_score 不變；網站顯示各驗證值｜使用者要「有公式佐證」｜論文原始門檻會擋掉全部 5 個範例，現值是校準到範例剛好通過（GRAM 餘裕僅 0.02–0.03）；快照 170→136 筆，抽圖仍約 3/8 不像 → 需要使用者標負樣本才能再收緊
- D27 2026-10-19｜網站加星號觀察名單＋☰ 左側抽屜（全部型態／觀察名單／設定／關於）＋「頸線斜率」篩選（neckline_change_pct = N2/N1−1，±3% 分下降／水平／上升，build_site 計算）；觀察名單與設定存 localStorage（`ihs.watchlist.v1`／`ihs.settings.v1`，讀寫包 try/catch，提供匯出／匯入）；以 `pattern_key`（ihs_scanner/keys.py，與 Discord 共用）識別；狀態 watchlist 的顯示文字改為「右肩已確認」（網站與 Discord），避免與觀察名單混淆｜使用者要求｜要跨裝置同步或要 Discord 只通知觀察名單時，改存 repo（伺服器端）
- D28 2026-10-19｜TP1 改為對數斐波那契：以 H.low 為 0、頸線中點（N1 與 N2 時間中點的頸線價）為 1，取 1.414 → `TP1 = H × (mid/H)^1.414`（`tp1_fib_level`、`tp1_fib_log` 可調）；TP1 不再依賴突破價，因此不標「預估」；TP2 不變｜使用者指定（TradingView 斐波那契開啟對數）｜使用者改比例或改用線性
- D29 2026-10-19｜新增台股市場：上市＋上櫃普通股 1973 檔（不含 ETF，`--tw-include-etf` 可開），清單／名稱用 TWSE、TPEx OpenAPI，K 線用 Yahoo chart API（runner 需 cookie＋crumb，否則 429）；1d 直接取、1w 由日 K 重採樣、4h 由 60m 切成 09:00–13:00／13:00–13:30（同 TradingView），4h 量為近似值；價格未還原權息；同一套 template 偵測；輸出獨立 `site/data/tw/`；workflow `scan-tw.yml`（平日 UTC 06:10）＋共用 `publish.yml`（發布時取回另一市場資料，concurrency `publish-site`）；Discord 同一 webhook，標題加「台股」，state 分開 `state_tw.json`；網站頁首與 ☰ 抽屜都可切換市場，設定兩市場共用｜使用者要求加密貨幣＋台股｜Yahoo 非官方介面失效時改用 TWSE/TPEx 逐日資料（只有 1d/1w）；台股門檻與通知門檻待使用者決定（首次 665 筆、分數中位數約 72）
- D30 2026-10-19｜網站不顯示「像使用者範例」類文字：形狀驗證表隱藏 V4 列，說明文字移除範例相關句子，V4/V2r 的 JSON 說明改中性；V4 過濾照常運作｜使用者要求｜—
- D31 2026-10-21｜網站加夜間模式（TradingView 深色）：頁首 ☾／☀ 一鍵切換＋設定「外觀」淺色／夜間／跟隨系統，存 `ihs.settings.v1.theme`，預設跟隨系統（使用者指定）；head 內嵌 script 先套用避免白閃；圖表即時改色（`app.js` `PALETTE`）。取代 site-design.md「暫不做暗色模式」｜使用者要求｜使用者想把預設改為跟隨系統或拿掉
- D32 2026-10-21｜Discord 每張卡片附型態圖（`ihs_scanner/chart_image.py`，matplotlib 1200×675、圖內文字英數、只畫 bars 檔的值）；multipart 上傳，每則 ≤10 embed、附件 <7.5MB；取消 20 筆上限與「未列出」摘要，全部逐筆送（保險上限 `NOTIFY_MAX_EVENTS` 預設 200）；圖產生失敗則該筆不附圖照送；workflow 加 `test_message`｜使用者要求附圖並全部列出（台股首日 55 筆中 35 筆被收進摘要）｜通知太多時改回上限或提高分數門檻
- D33 2026-10-21｜台股 4h 型態長度上限改 300 根（約半年），用 `config.MARKET_OVERRIDES["tw"]`＋`market_config()` 只對台股生效；加密貨幣與台股 1d/1w 不變｜台股 4h 每天只有 2 根，1000 根≈2 年，型態過長且不像（例：1101 4h 跨一年多）；使用者同意｜台股 4h 結果太少或使用者要更長時
- D34 2026-10-22｜右肩確認改為每次掃描一則表格（圖＋下拉選單 watch_select/_2…，台北時間標題）；右肩形成中與突破維持逐筆附圖；關注清單＝Discord 機器人＋Cloudflare Worker（`bot/`，KV 儲存，/interactions 驗簽、/api/watchlist Bearer SYNC_KEY），網站星號可選擇雲端同步；關注頻道只通知狀態改變（含消失），每則附圖＋取消關注按鈕；未設定機器人／Worker 時自動降級｜使用者選方案 C（即時點選、網站同步）｜設計見 `docs/watchlist-bot-design.md`；KV 無鎖，同秒寫入可能覆蓋（個人使用可接受）
- D35 2026-10-22｜關注清單改成每人一份（KV `user:<discord_id>`），以 Discord 使用者 ID 區分；選單加入／取消關注／`/watchlist`／`/notify` 只動自己的；通知方式每人可選 #關注 頻道 @（同型態合併一則、allowed_mentions 限定）或機器人私訊（403 時退回頻道 @）；網站改「用 Discord 登入」（OAuth2 code、HMAC state＋nonce cookie、session 30 天放 URL fragment），取代同步金鑰；舊共用清單已刪除（不遷移）；Worker secrets：DISCORD_PUBLIC_KEY、SYNC_KEY、SESSION_SECRET、DISCORD_CLIENT_SECRET（使用者自設）｜使用者要讓伺服器裡的朋友各自關注｜設計見 `docs/watchlist-bot-design.md` D35 節
- D36 2026-10-22｜依使用者三個對照案例修偵測：(A) 突破改用 high（影線穿過頸線即突破，`breakout_price`，template 預設 high）；(B) 突破優先：同一 H 已有有效候選突破後，之後 RS 的候選不成立（例外：前者突破失敗、或後者頸線排名較好），避免把突破後高點當 N2；(C) 碰頸線不算 V1 違規（在碰頸線處切段）並輸出 `neckline_touches`；去重順序：已突破→碰頸線多→下斜→分數；1w 頸線角度上限 45°；T8 改用實體低點；1w 有效下斜型態取代重疊的 1d 上斜型態（`superseded`）｜BTC 1d 誤判未突破、AT 4h 插針未算突破、LDO 週線下斜頸線沒抓到｜黃金測試 `tests/test_golden_d36.py` 鎖住；規則 B 兩個例外由 TRUST／PENDLE 反推；T8 改實體會放進 MINA 類尖峰型態；快照 84→64 筆
