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
