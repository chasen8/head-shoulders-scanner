# 頭肩底（Inverse H&S）掃描器：GitHub 開源專案調研

調研日期：2026-10-16。資料來源：`gh api`（star、授權、最後 push 日期取自 GitHub API，準確）＋實際讀過的原始碼與 README。
「授權」欄以 GitHub API 的 SPDX 為準，再人工開 LICENSE 檔核對；API 回 NOASSERTION 或 none 的另外標註。
結論先講：**沒有任何現成專案同時滿足使用者規格**（多 timeframe、左右肩%差、頸線角度、評分）。最接近的是 SignalSync（MIT、僅 1 star、剛建立、品質極高），最「正統」的 pivot＋確認邏輯是 neurotrader888（MIT）。

---

## 一、逐 repo 紀錄（皆已開啟 README 並讀過核心程式）

### 1. neurotrader888/TechnicalAnalysisAutomation（最值得借）
- 網址：https://github.com/neurotrader888/TechnicalAnalysisAutomation
- Star：約 582；最後更新 2023-04（停更）；授權：**MIT**（LICENSE 已核對）
- 偵測方法：rolling window extrema（`rw_top/rw_bottom`，order=N），另有 directional change（zigzag，% 門檻）、perceptually important points。
- 頸線／突破：有。頸線 = 左右 armpit 連線（含斜率）；突破 = 收盤穿越頸線值；`early_find` 選項（價格過右肩中點即提早確認）。
- 成交量：無。評分：無，但算 `pattern_r2`（用 5 段折線擬合模型對價格的 R²，作者自己說「不太有效、會延遲」，註解掉不用）。
- 多 symbol：無（單一 numpy 陣列＋CSV）。程式品質：中上，可讀，dataclass 清楚；但是純 Python 迴圈、無測試框架（有 test 腳本）、README 近乎空白、依賴 mplfinance。
- 可借的點：
  - `rw_extremes` 回傳 `[confirm_idx, extreme_idx, price]`：**把「確認時間」與「極值位置」分開存**，天然避免 look-ahead（確認 index = 極值 index + order）。註解明講 scipy `argrelextrema` 速度快但「小心作弊用到未來資料」。
  - `find_hs_patterns`：用 `deque(maxlen=5)` 收最近 5 個交替 extrema，每新增一個極值才檢查一次；**`hs_lock / ihs_lock` 去重**：偵測到一個型態後上鎖，直到出現新的反向 extremum 才解鎖，避免同一型態每根 K 線重複觸發。
  - 規則寫得簡單：頭比兩肩低；「balance rule」（肩在對方肩與 armpit 中點的同側）；「symmetry rule」（肩到頭的時間比 ≤ 2.5 倍）。可與使用者的「左右肩差異率」並用。
  - 頭部高度用「頸線在頭位置的值」減頭價（`head_height`），而不是用水平線算，對斜頸線正確。
  - 另含 backtest 腳本（`test_hs_patterns.py`，統計 early/非 early 的勝率），可參考回測指標設計。
- 缺點：無 volume、無多 timeframe、sigma/order 要手調。

### 2. yanivil/SignalSync（規格最接近；只借設計，不依賴成熟度）
- 網址：https://github.com/yanivil/SignalSync
- Star：1（2026-09-04 才建立，2026-10-16 仍在更新）；授權：**MIT**
- 偵測方法：自寫 fractal pivot `find_pivots(order)`（視窗內最大／最小且為第一個極值，**最後 order 根永遠不是 pivot＝明確不 repaint**）＋ATR。
- 頸線／突破／成交量／評分：**全有**。S&P500 日線多 symbol，單檔 `scan.py`（約 12 萬字元）、有 pytest、GitHub Actions 每日跑、輸出 `signals.json`/`report.md`/GitHub Pages。
- 逐條內容（`detect_inverse_hs`）：
  - 三個連續 swing low = LS/H/RS；頭須比兩肩低至少 `IHS_MIN_HEAD_ATR` 倍 ATR（**頭部深度用 ATR 正規化**）。
  - 肩差異限制：`|S2−S1| ≤ 30% × head_height`（與使用者「肩差≤5%/10%」精神相同，但分母是頭深，不是肩價；使用者可考慮兩種都算）。
  - 頸線錨點 = LS-H 之間最高 high（N1）、H-RS 之間最高 high（N2），**排除肩／頭那根 K 線本身**（避免上影線被當頸線點）。
  - 頸線傾斜 = 斜率×寬度 / 頭價，超過 15% 就不算頸線（對應使用者的 ±30° 角度；它用「總漲跌幅比例」而非 atan，更不受 bar 數影響）。
  - 左右兩半時間比（2.5 倍）、兩側時間比 ±40%、型態寬度 20–200 根、前置下跌（SMA50<SMA200 或跌幅≥一個頭高）。
  - 狀態機 `evaluate_breakout`：CONFIRMED（收盤>頸線且量比≥1.3×）／WATCHLIST（接近頸線、仍在右肩低點上）／STALE；**突破年齡 age 從「第一次突破」起算，回踩再突破不重置**（作者實測重置會讓假陽性從 1% 升到 5%）。
  - 評分 0–100：基底 50＋肩價對稱 15＋時間對稱 10＋頸線平坦 10＋站上 SMA200 5＋風險小 5＋放量 5；`MIN_SCORE=60`。
  - `_dedupe`：同 (ticker, pattern, status) 只留最高分。
  - 附帶：資料處理（split/dividend 調整、`align_last_bar` 對齊多數 symbol 最新完整 K 線、填補 Yahoo 缺收盤價）、hash-pinned requirements、每日 Actions 排程、`--profile spec/tuned/legacy` 規則集並存（用一年回放挑參數）。
- 缺點：只做日線（沒有 4h/1w）、邏輯全擠在單一巨檔；作者是新專案、歷史績效自述未經外部驗證；find_pivots 用 Python for 迴圈（O(n·order)）。
- 授權 MIT，**可直接引用片段**（保留版權聲明）；但因成熟度低，建議「讀懂後自己重寫」而非複製整檔。

### 3. BennyThadikaran/stock-pattern
- 網址：https://github.com/BennyThadikaran/stock-pattern
- Star：約 415；最後更新 2025-12；授權：**GPL-3.0（特別標注：不可複製進非 GPL 專案／若要做成對外網站服務並散布需注意）**
- 偵測：`get_max_min(barsLeft, barsRight)` rolling window pivot；`is_reverse_hns(a..f, avgBarLength)` 以「**K 線中位數 high-low 範圍 (avgBarLength)**」當容差（`|b−d| < avgBarLength`、肩深 > 0.6×avgBarLength），**用價格尺度自適應而非固定 %**。
- 頸線：連 B、D 兩點（線性迴歸 `generate_trend_line`）；**只偵測「突破前」的型態（最後一根收盤仍在頸線下）**，定位為 watchlist。已突破的「頸線被穿越後超過 avgBarLength」就丟棄。
- 成交量：只在 VCP 等其他型態用；無 pattern_score。多 symbol：有（讀 CSV 目錄、CLI 掃全市場）、有 mplfinance 繪圖與 `backtest.py`、有 loader 抽象層（可接不同資料源）。
- 品質：好（有測試、CLI、wiki 說明演算法），但 `utils.py` 單檔 10 萬字元、H&S 取「全域最低 pivot」當頭再向兩側找，只抓最近的一組。
- 可借：ATR/K 棒範圍當容差、Loader 抽象、回測腳本結構、「只報突破前」的 watchlist 概念。**只參考，不複製程式碼（GPL）**。
- 衍生：tan-yang/pt-lib（GPL-3.0，2 star，2026-07；README 自稱基於 stock-pattern 加 28 種型態；只讀 README，未審程式）→ 不建議。

### 4. BennyThadikaran/precise-patterns（stock-pattern 作者的重寫版）
- 網址：https://github.com/BennyThadikaran/precise-patterns
- Star：約 26；2026-01；授權 **GPL-3.0**；README 自標 Work in Progress。
- 重點：**事件驅動（`candle.close` 事件 → `pivot.confirm` 事件）**、支援任意分鐘 K 合成（3/5/15/75/240）、單調 deque 的 O(1) 滑動視窗極值（`MinMax`，pivot 在 buffer 的 `pivot_pos` 位置，右側 `right_bars` 根到齊才發出 confirm）。H&S 本體尚未實作（`patterns.py` 只有 1.2KB）。
- 可借：把「pivot 確認」做成串流事件，未來若改成 WebSocket 即時／Discord 推播，天然無 look-ahead；4h 由 1h/分鐘線合成的作法。GPL → 只參考概念。

### 5. zeta-zetra/chart_patterns
- 網址：https://github.com/zeta-zetra/chart_patterns
- Star：約 126；最後更新 2024-07；授權：**無授權（GitHub 顯示 none，無 LICENSE 檔）→ 法律上不可複製使用**
- 偵測：兩層 pivot（`pivot_interval`=10 與 `short_pivot_interval`=5，左右各 N 根），在 lookback 視窗內取 short pivot；`scipy.stats.linregress` 對上方高點的斜率（頸線）限制 `|slope|<=1e-4`；頭 vs 兩肩比例 `head_ratio_before/after=0.98`。
- 有 inverse_head_and_shoulders.py、doubles、flag、triangles、pennant，附 plotly 繪圖與 pytest。
- 品質：中偏弱。用 `df.loc[i,...]` 逐格迴圈，效能差；頸線斜率閾值是原始價格單位（1e-4）而非 % 或角度，不能跨 symbol／價位通用；會直接修改輸入 DataFrame（加欄位）；import 路徑 `chart_patterns.chart_patterns...` 有重複，不好用；只輸出單一 bar 標記，無突破／成交量／評分。
- 可借（只看概念）：雙層 pivot（大 pivot 抓頭、小 pivot 抓肩與頸線點）的想法；結果以 `ihs_idx / ihs_point` 欄位存回 DataFrame，方便繪圖。

### 6. keithorange/PatternPy（以及 fork：white07S/TradingPatternScanner）
- 網址：https://github.com/keithorange/PatternPy ／ https://github.com/white07S/TradingPatternScanner
- Star：約 479／316；最後更新 2024-02／2023-08；授權：PatternPy 為 **CC BY-NC-SA 4.0（非商業＋相同方式分享，LICENSE.md 已核對；GitHub 顯示 NOASSERTION）→ 要做網站／收費就不能用**；TradingPatternScanner 的 LICENSE 為版權聲明（作者 Preetam Sharma 2017）+ GitHub NOASSERTION，**授權不明，視為不可用**。
- 偵測：`head_and_shoulders()` 只是「單根 K 線比前一根與後一根都低／高」的 rolling 比較——**本質是 1 根 K 線的局部極值標記，根本不是頭肩型態**；且使用 `shift(-1)`，即用到未來資料（look-ahead）。無頸線、無突破、無評分。其餘 wedge／triangle 同樣是 3 根 K 線的粗糙規則。
- 結論：star 多但名不符實，**不建議參考演算法、不引用程式碼**。只能當作「為什麼不能用 rolling max/shift 偷懶」的反面教材。

### 7. Rishit-Raj/Inverse-Head-and-Shoulders-Technical-Pattern-Identification
- 網址：https://github.com/Rishit-Raj/Inverse-Head-and-Shoulders-Technical-Pattern-Identification
- Star：0；2025-08；授權：**無**
- 內容：幾乎是 neurotrader888 `head_shoulders.py` 的複製加上 yfinance 下載與 mplfinance 繪圖（資料類、函式命名完全一致），README 有「~65–75% 成功率」等無出處宣稱。無新增價值，且無授權。**不參考**（直接看原作者 neurotrader888 即可）。

### 8. jbn/ZigZag
- 網址：https://github.com/jbn/ZigZag
- Star：約 476；最後更新 2024-03；授權：**BSD-3-Clause**（寬鬆，可用）
- 方法：`peak_valley_pivots(X, up_thresh, down_thresh)`：% 門檻 zigzag（反轉幅度超過 up/down 比例才確認新 pivot）；Cython 實作、`limit_to_finalized_segments` 參數可只回傳已確定的段，**避免最後一段 repaint**。
- 只吃單一序列（close），沒有 high/low 版；註解指出「首尾元素一定被標成 pivot」是準確度取捨，用於即時掃描時要排除最後一個 pivot（它尚未確認）。
- 可借：% 門檻 zigzag 的現成可靠實作；與 neurotrader888 的 `directional_change`（用 high/low、以 close 確認）對照著選。

### 9. GregoryMorse/trendln（只看 README，未審核心）
- https://github.com/GregoryMorse/trendln ；約 732 star；2026-04 仍更新；**MIT**。
- 用於支撐／壓力趨勢線（Hough / 線性迴歸），不是頭肩型態。可借用「頸線是否為多點共線」的驗證想法，優先級低。

### 10. TradingView Pine 腳本（只看到說明頁，原始碼未能讀取）
- OBO-TOBO Head & Shoulders Pattern Hunter（作者 karacalit4）https://www.tradingview.com/script/S5b8ruRi-OBO-TOBO-Head-Shoulders-H-S-Pattern-Hunter ：頁面只說明用 ZigZag、畫完整 W/M 形、頸線延伸、對稱過濾，Length 參數控制靈敏度；**閾值與突破規則頁面沒寫，我無法確認**。TradingView 規定重發佈原始碼受 House Rules 限制。
- Advanced Pattern Detector https://www.tradingview.com/script/QMVCsrxh-advanced-pattern-detector ：僅由搜尋摘要得知有 iH&S＋ZigZag，**未開啟，內容未驗證**。
- 結論：Pine 只當 UI 參考（畫法、參數命名），不引用。
- Kernel regression：搜尋沒找到「Nadaraya-Watson 平滑＋pivot 找型態」的 Python 開源實作（只有 TradingView 的 NW 指標與 mcmodels 套件）。所以「先平滑再找極值」這一點只能自己做（建議用 one-sided 因果核，避免未來資料）。

---

## 二、通知／網站 dashboard 類（參考）

| 專案 | 授權／star | 形式 | 可借之處 |
|---|---|---|---|
| yanivil/SignalSync https://github.com/yanivil/SignalSync | MIT／1 | GitHub Actions 每日排程 → commit `signals.json`+`report.md` → pages workflow 發佈靜態網站（`site/`、`app.js` 只有篩選按鈕 JS） | **零伺服器的網站作法**：Actions 跑掃描、輸出 JSON、靜態頁渲染；符合使用者「定時任務上 GitHub Actions」原則。cron 故意避開整點（GitHub 整點延遲最嚴重，作者觀察過一次延遲 4.5 小時）。報告含「自上次報告後已消失的訊號」表（diff 通知很實用）。 |
| OoreFasawe/Stock-Screener（ScreenerDBot）https://github.com/OoreFasawe/Stock-Screener | 無授權／0 | nextcord Discord bot＋FastAPI，把篩選結果用 matplotlib 畫成**表格圖片**貼到指定頻道，Dockerfile 部署 Cloud Run；token 走環境變數 | 「結果表格轉圖片」的 Discord 呈現方式；頻道白名單。**無授權，只參考概念**。程式小（約 9KB），品質普通。 |
| metteyyaX/resonance.ai-scannerv13 https://github.com/metteyyaX/resonance.ai-scannerv13 | **授權矛盾**：README 徽章寫 Apache 2.0，但 GitHub 顯示無 LICENSE／23 star | Coinbase WebSocket 即時突破掃描 → Discord webhook 通知，設定檔放 json | 只讀了 README，核心程式碼未審。「webhook URL 放設定檔／環境變數」的作法；授權不明，只參考。 |
| pkjmesra/PKScreener https://github.com/pkjmesra/PKScreener | MIT／約 401／2026-10 仍更新 | 印度股票大型篩選器，含 `TelegramNotifier.py`、`NotificationService.py`，有專屬測試檔 | 只看到檔案結構，**未讀程式碼**；其型態偏 K 線形態（`CandlePatterns.py`）而非頭肩。若要看「通知服務抽象層」可日後再開。 |
| shner-elmo/TradingView-Screener https://github.com/shner-elmo/TradingView-Screener | MIT／約 1322 | 以 Python 查詢 TradingView screener 資料 | 僅供「資料源」參考，未審；使用者已有自己的 OHLCV 來源則不需要。 |

---

## 三、對使用者規格「最值得借」的 8 個點子

1. **pivot 資料結構存三元組 `(confirm_idx, extreme_idx, price)`**（neurotrader888）。型態只能在 `confirm_idx` 之後才「存在」；回測／掃描時以 `confirm_idx` 排序、決定可見性，從結構上杜絕 look-ahead。不要用 `scipy.argrelextrema` 對整段序列直接算後就使用（它會把尾端未確認點也標出來／或在回測中洩漏）；若用，也要丟棄最後 `order` 根內的結果。
2. **即時掃描的延遲處理**：pivot 需要右側 N 根才確認，所以右肩要到「右肩 low 之後再過 N 根」才算 pivot。三種策略可並存：(a) 嚴格模式只用已確認 pivot（SignalSync，最後 order 根永遠不是 pivot，不 repaint）；(b) neurotrader888 的 `early_find`：右肩用「armpit 之後的最低價」暫代，價格過右肩中點即預警；(c) 輸出標記 `status = WATCHLIST（右肩未確認／未突破）/ CONFIRMED（收盤突破頸線）`，並顯示 pivot lag（bars）。這三者直接對應使用者「可選頸線突破＋成交量放大」的設計。
3. **去重**：兩層。(a) 事件層：neurotrader888 的 `hs_lock`，同一組 extrema 觸發後上鎖，直到出現新的反向極值才解鎖；(b) 結果層：SignalSync 的 `_dedupe`，同 symbol＋timeframe＋型態只保留最高分；建議再加「以 (symbol, tf, head 的 bar 時間戳) 當唯一鍵」，讓 Discord 通知只推一次、之後只推狀態變化（WATCHLIST→CONFIRMED）。
4. **頸線錨點取法**：LS-H 與 H-RS 之間區間的最高 high（排除肩／頭那根 K 線本身），以兩點連線算斜率（SignalSync）；頭部深度用「頸線在頭 bar 的值 − 頭價」（兩者都做對斜頸線正確）。頸線角度你規格已定義為 atan(斜率%/bar × angle_scale)；可再加 SignalSync 的「頸線總傾斜／頭價 ≤ 15%」作輔助門檻，能擋掉變形成趨勢線的假頸線。
5. **用 ATR（或 K 棒中位數範圍）當第二層容差**（SignalSync `IHS_MIN_HEAD_ATR`、stock-pattern `avgBarLength`）：肩差 % 規則在高波動幣與低波動股票上意義不同；建議「肩差%（你的規格）」＋「頭深 ≥ k×ATR」雙門檻，4h/1d/1w 可共用同一套 ATR 倍數。
6. **突破狀態機＋age 不重置**（SignalSync `evaluate_breakout`）：突破年齡從第一次收盤站上頸線起算，回踩再突破不重置；超過上限 → STALE。再加 `MAX_RUNAWAY`（突破時離頸線過遠不追）與 WATCH_PROXIMITY（未突破但離頸線在 x% 內才進 watchlist，且收盤必須高於右肩低點，否則型態失效）。成交量：突破那根的量 ÷ N 日均量 ≥ 1.3 才算確認，未達降級為 WATCHLIST 並附註。
7. **評分設計**：用「基底＋各項線性遞減加分」，每一項在門檻處剛好為 0（SignalSync：肩價對稱 15、時間對稱 10 用 log 比值、頸線平坦 10、趨勢 5、風險 5、放量 5）。使用者的 0–100 pattern_score 可直接沿用這個結構，優點是每個子分數都可解釋、可在 Discord／網站展開顯示。另外該專案用「一年回放」比較規則集（spec / tuned / legacy），建議也保留多組 profile 方便調參。
8. **部署形態**：先做 Actions 排程 → 輸出 `signals.json`（含 meta：最後 K 線時間、symbol 數、錯誤數）→ 靜態網站頁＋Discord webhook（可用「表格圖片」或 embed）。多 symbol 資料先做 `align_last_bar`（所有 symbol 對齊到多數 symbol 的最新已收盤 K 線，丟掉未收完的 K 線，4h/1d/1w 都適用），並且只用已收盤 K 線。cron 避開整點。Webhook／token 只放環境變數或 GitHub Secrets。

額外小點子：% 門檻 zigzag（jbn/ZigZag 或 neurotrader888 `directional_change`）可當第二種 pivot 來源，與 fractal pivot 結果比較，哪個在 4h 雜訊較少；stock-pattern 的 Loader 抽象與 precise-patterns 的 K 線聚合事件，可作 4h 由 1h 合成的參考。

---

## 四、只適合參考、不適合直接引用程式碼

| Repo | 原因 |
|---|---|
| BennyThadikaran/stock-pattern、precise-patterns、tan-yang/pt-lib | GPL-3.0（衍生作品需同授權開源；使用者若要做成可散布的網站／產品會受限）。概念可學，程式碼不要貼。 |
| keithorange/PatternPy | CC BY-NC-SA 4.0 非商業；且演算法名不符實（單根 K 線極值）並含 `shift(-1)` 未來函數。 |
| white07S/TradingPatternScanner | 授權不明（NOASSERTION），與 PatternPy 同一份程式。 |
| zeta-zetra/chart_patterns | 無授權；斜率門檻用絕對價格單位；效能與 API 設計差。 |
| Rishit-Raj/Inverse-Head-and-Shoulders… | 無授權，neurotrader888 的複製品。 |
| OoreFasawe/Stock-Screener、resonance.ai-scannerv13 | 無授權（後者 README 與 GitHub 授權欄矛盾）。 |
| TradingView 的 Pine 腳本 | 原始碼受 TradingView House Rules，且未取得原始碼。 |
| yanivil/SignalSync | MIT 可用，但 1 star、剛建立、單檔巨型；建議當設計藍本自行重寫，不要整檔複製。 |

可直接引用（授權允許）：neurotrader888（MIT）、SignalSync（MIT）、jbn/ZigZag（BSD-3）、trendln（MIT）。引用時保留原版權聲明並在 charter 決策記錄註明來源。

---

## 五、我不確定／未能驗證的地方
- Star 數為 2026-10-16 查詢的概略值，會變動。
- PatternPy 與 TradingPatternScanner 的 H&S 程式碼檔案大小相同（10359 bytes），判斷為同一份；沒有逐字比對。
- pt-lib、PKScreener、resonance.ai-scanner、trendln 只讀 README／檔案結構，未審核心程式碼；resonance 授權矛盾無法確認。
- 兩個 Pine 腳本的原始碼未取得，閾值與突破規則未知；kernel regression 平滑找 pivot 的開源 Python 專案沒找到。
- SignalSync 與 neurotrader888 的勝率／績效數字都是作者自述，我未重現。
- 「GitHub Pages 靜態站」的成本與限制（私有 repo 是否可用 Pages）未查證，使用者若要私有請另查。
