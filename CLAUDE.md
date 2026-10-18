# 頭肩底V2（IHS Scanner）

純 OHLCV 數學規則的頭肩底（Inverse Head and Shoulders）多 symbol／多 timeframe（4h/1d/1w）掃描器，
之後接網站與 Discord 通知。
目前狀態：見 `docs/project-charter.md`「目前狀態」。

## 本專案特有約定
- **全新獨立專案**：與 `桌面\頭肩底`（V1.1）、`桌面\頭肩底-V2-Hybrid` 無關，
  不 import、不修改它們；可唯讀參考做法。
- 規格權威：`docs/spec.md`（使用者原文，不改）；詮釋與補充：charter 決策記錄。
- venv：`C:\trading-data\ihs-scanner-v2\venv`（`py -3.13` 建立）；K 線快取與輸出：
  `C:\trading-data\ihs-scanner-v2\{data,output}`（不進 OneDrive）。
- 資料：幣安**公開** K 線端點，無金鑰、無交易權限。
- 不 repaint：只用已收盤 K 線；as-of t 的結果只能由 `df[:t+1]` 算出（截斷測試鎖住）。
- 開源參考調查：`docs/github-research.md`（GPL／無授權的 repo 不得複製程式碼）。

## 路由
- 全域準則（環境坑、派工、判斷、維護）：`C:\Users\chris\.claude\CLAUDE.md`
  會自動載入，衝突時本檔優先。
- 決策與進度：`docs/project-charter.md`（格式「日期｜決定｜原因｜何時可推翻」）。
