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
- D16 2026-10-18｜GitHub repo `chasen8/head-shoulders-scanner` 設為**私有**，暫不部署 Pages；workflow 只留 `workflow_dispatch`：pytest → build_site.py → upload-artifact（site/）；schedule 與 Pages 部署段落保留為註解｜使用者選擇；免費方案私有 repo 不能開 Pages，升級 Pro 要花錢｜使用者改公開 repo、升級 Pro，或改用其他託管
- D17 2026-10-18｜網站＝靜態頁讀 `site/data/scan.json`；所有數值（分數、頸線端點、`neckline_side`、`next_scan_at`）由 `build_site.py` 算，前端只格式化與繪圖；K 線圖用 Lightweight Charts 5.2.1 釘版；設計準則照 `docs/site-design.md`（frontend-design skill）｜偵測邏輯只有一份（Python），前端不會與掃描器分岔｜需要互動式重算時
- D18 2026-10-18｜build_site.py 任一 symbol/timeframe 抓取失敗 → 非零退出且不覆寫既有 scan.json；`next_scan_at` 只有 `--scheduled` 或 `GITHUB_EVENT_NAME=schedule` 才填｜寧可顯示舊資料也不發布缺幣種的半套結果；排程停用時不謊報下次掃描｜幣種清單變大、單一下市幣常讓整批失敗時改成部分成功
