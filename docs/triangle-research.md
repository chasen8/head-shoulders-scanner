# 三角收斂（上升／下降／對稱三角）調研：開源實作、論文、可量化規則

調研日期：2026-10-22。目的：在現有 IHS 掃描器（pivot → 組點 → 規則過濾 → 形狀驗證 → TP → 狀態，見 `docs/current-rules.md`）上新增三種三角形的實作依據。
本文只做調研，**未改任何程式**。格式與深度比照 `github-research.md`、`academic-ihs-research.md`。

讀取方式：GitHub 用 `gh api` 抓原始碼（commit SHA 固定，行號可重現）；Bulkowski 網頁用 curl 抓全文（`thepatternsite.com` 憑證鏈在本機 WebFetch 失敗，改 `curl -k` 讀公開網頁，只讀不送資料）；
論文 PDF 下載後用 pdftotext 抽字，**公式再把頁面轉圖目視核對**（OCR／抽字會丟符號）。沒讀到全文的一律標明。

---

## 0. 結論先講（≤10 行版）

1. **三角形的共同骨架**：用已確認的 pivot 高點擬合阻力線 R、pivot 低點擬合支撐線 S（建議在 log 價格空間），兩線必須收斂、交點（apex）在右側，價格被兩線「包住」。
2. **觸碰點數**：Bulkowski 的識別準則是「一條線至少 3 點、另一條至少 2 點，且是明確的高低點」（總共 ≥5 個轉折）。Lo 2000、stock-pattern 也是 5 個連續極值。建議：高 ≥2、低 ≥2、合計 ≥5。
3. **分類靠斜率佔型態高度的比例**（不要用絕對斜率：zeta-zetra 的 1e-5 是原始價格單位，換個幣就失效）：水平 ＝ 線在型態期間的漂移 ≤ 0.15 × 型態高度 H0；上升／下降線漂移 ≥ 0.30 × H0；對稱＝兩線都 ≥ 0.20 × H0 且斜率比 ≤ 3。
4. **收斂比例**：Bulkowski 說突破中位數發生在「到 apex 的 61%～74%」；Ben Omrane & Van Oppens (2004) 規定型態結束時間 ≤ 起點到 apex 的 75%。建議 gap(最後一根)/gap(起點) 介於 0.20～0.75，apex 進度 p = (t_now − t_start)/(t_apex − t_start) < 1 才有效。
5. **包住容忍度**：沿用現行頸線的 0.5%（收盤）／1%（最高最低）。stock-pattern 是零容忍（任何收盤穿線就整個否決）；trendln 用 0.5%。
6. **突破**：Bulkowski 用「收盤穿越趨勢線」；建議預設收盤＋緩衝（4h 0.5%／1d 1%／1w 1%），可用 `breakout_price` 開關改成影線（和 D36 一致）。成交量只當選配（型態內量縮 78%～86% 是 Bulkowski 統計，不是必要條件）。
7. **目標價**：高度 H0 從突破價投射。Bulkowski 的「達標率」可當保守 TP1（上升三角上破 70%／下破 44%；下降 64%／50%；對稱 58%／36%），完整高度當 TP2。
8. **方向預期要小心**：Bulkowski 的樣本中上升三角上破 63%、對稱 60%，但**下降三角反而 53% 上破**。所以「下降三角偏空」不能當預設訊號；標示類型與偏向，真正的方向以實際突破為準。
9. **現有五個 GitHub 來源沒有一個能直接抄**：zeta-zetra 無授權、stock-pattern 是 GPL-3.0（只參考概念）；MIT 的 neurotrader888（趨勢線最佳化）、trendln、tysoncung 可借概念，三角形偵測本身要自己寫。
10. 計算量很小（見第 7 節）：每條序列約 20 個 pivot，估計全市場 3 週期 ≈ 10⁴ 條序列、整體以秒～十幾秒計（**未實測**）。

---

## 1. 閱讀狀態總表

| # | 來源 | 類型 | 授權 | 是否讀到原始碼／全文 |
|---|---|---|---|---|
| G1 | zeta-zetra/chart_patterns | GitHub | **無授權**（API 無 license、無 LICENSE 檔） | 是（triangles.py 全檔 130 行） |
| G2 | BennyThadikaran/stock-pattern | GitHub | **GPL-3.0** | 是（utils.py 的 is_triangle、find_triangles） |
| G3 | tysoncung/crypto-chart-patterns | GitHub | MIT | 是（enhanced_patterns.py 三角形函式） |
| G4 | neurotrader888/TechnicalAnalysisAutomation | GitHub | MIT | 是（trendline_automation.py 全檔、flags_pennants.py 關鍵段） |
| G5 | GregoryMorse/trendln | GitHub | MIT | 只讀 README（參數說明）；演算法細節未逐行讀 |
| G6 | srdeotarse/Linear-Regression-for-Stock-Chart-Pattern-Recognition | GitHub | MIT | 是（TrianglePattern.ipynb 全部程式格） |
| G7 | TradingView「Triangle Pattern Detection [Dots3Red]」、Trendoscope「Auto Chart Patterns」 | Pine | TradingView 開源腳本（授權**未驗證**） | **只讀到頁面說明，原始碼沒拿到** |
| N1 | BennyThadikaran/precise-patterns | GitHub | GPL-3.0 | 查過 patterns.py：沒有三角形（只有 VCP 等） |
| N2 | pkjmesra/PKScreener | GitHub | MIT | 查檔案樹與程式碼搜尋：沒有三角形偵測 |
| N3 | yanivil/SignalSync | GitHub | MIT | 查 docs/wiki/02-Pattern-Catalog.md：只有 Cup&Handle／IHS／雙底／Wolfe，沒有三角形 |
| A1 | Lo, Mamaysky & Wang (2000) | 論文 | — | **是**，NBER WP 7613 全文，Definition 3 目視核對 |
| A2 | Ben Omrane & Van Oppens (2004) | 論文（CORE DP 2004/35） | — | **是**，全文 PDF，附錄 C.10/C.11 目視核對 |
| B1 | Bulkowski 的 thepatternsite.com：ascending／descending／symmetrical triangles、Triangle Apex | 書／網站 | 版權所有，只引用數字與準則 | 是（三頁＋apex 頁全文）；**《Encyclopedia of Chart Patterns》本書沒讀到** |
| B2 | Bulkowski busted triangles 三頁 | 網站 | 同上 | 只讀到搜尋摘要（標明在第 6 節） |

---

## 2. GitHub 來源逐一整理

SHA：G1 `44f8baa`、G2 `85c710d`、G3 `1601b0c`、G4 `da99c20`、G5 `82dcb11`、G6 `b533ce6`（全碼見各連結）。

### G1 zeta-zetra/chart_patterns — 無授權，**只參考概念**
- 網址：<https://github.com/zeta-zetra/chart_patterns/blob/44f8baa3b15dfa6b9d54b51d2307c85a255d3a40/chart_patterns/triangles.py#L16-L128>
- 授權：無（repo 沒有 license）。依預設是保留所有權利 → 不得複製程式碼。
- 偵測方法：`pivot_points.py` 的 `find_pivot_point` 用左右各 3 根的嚴格極值（`left_count=3,right_count=3`）。對每根 K 線 i，取 [i−25, i] 內的 pivot 高點與低點，各做一次 `scipy.stats.linregress`（x＝K 線序號、y＝原始價格）。
- 門檻（L16-L18、L90-L126）：

| 項目 | 值 |
|---|---|
| 回看 lookback | 25 根 |
| 觸碰點數 min_points | 3（但 L84 的條件寫成 `size<3 and size<3` 再加 `==0`，實際等於只要求兩邊各 ≥1 個，是個 bug） |
| 擬合 R² 下限 rlimit | 絕對相關係數 \|r\| ≥ 0.9（高、低兩邊都要） |
| 水平判斷 | 斜率 ±1e-5（**原始價格每根的絕對值**，不可跨價位使用） |
| 上升三角 | 低點斜率 ≥ 1e-5，高點斜率在 ±1e-5 內 |
| 下降三角 | 高點斜率 ≤ −1e-5，低點斜率在 ±1e-5 內 |
| 對稱三角 | 低點斜率 ≥ 1e-5 且高點斜率 ≤ −1e-5 |
| 突破、量、目標價 | **無**（只標型態，沒有突破與目標價邏輯） |
- 可借概念：「R² 下限」當擬合品質門檻（但 2 個點的線 R²≡1，要配合觸碰點數下限）。

### G2 BennyThadikaran/stock-pattern — **GPL-3.0，只參考概念，不得複製**
- 網址：<https://github.com/BennyThadikaran/stock-pattern/blob/85c710daa5809f8f85c9e6f3f98ceea200317566/src/utils.py#L112-L158>（`is_triangle`）、<…/src/utils.py#L970-L1130>（`find_triangles`）、<…/src/utils.py#L337-L371>（`get_max_min`，pivot 預設左右各 6 根）、<…/src/utils.py#L397-L441>（`generate_trend_line`，兩點連線）。
- 偵測方法：用 pivot 取 A（區間最高）→ B（A 後最低）→ C（B 後最高）→ D → E，再加目前收盤 F，共 5 個轉折＋現價。
- 分類（`is_triangle`，L151-L158）。令 avgBarLength＝A 到 D 之間每根 (High−Low) 的**中位數**（L1047）：
  - 上升：\|A−C\| ≤ avgBarLength 且 \|C−E\| ≤ avgBarLength（上方三個高點「幾乎一樣高」），且 B < D < F < E。
  - 下降：\|B−D\| ≤ avgBarLength，且 A > C > E > F ≥ D。
  - 對稱：A > C > E，B < D < F，E > F。
- 其他門檻（L1063-L1100）：
  - 上、下兩條線起點的時間跨度比 max/min ≤ 1.8（「2:1 起點時間比」）。
  - 兩線已交叉（上線終點 < 下線終點）→ 型態已走完，放棄。
  - 區間內**任何一根收盤**穿出任一條趨勢線（`df.Close > upper_slope` 或 `< lower_slope`）→ 整個否決（零容忍）。
  - 另有以絕對斜率（價格／根）排除某些組合的條件（L1082-L1090），原作者的意圖看不太出來，**不採用**。
- 突破、量、目標價：這個函式只輸出「形成中」的三角形，沒有突破與目標價。
- 可借概念：用「中位數 K 線區間」當水平容忍（概念上等同 ATR 正規化）；用收盤零穿越當包住條件；上下線起點時間比限制。

### G3 tysoncung/crypto-chart-patterns — MIT
- 網址：<https://github.com/tysoncung/crypto-chart-patterns/blob/1601b0c816065cd756ae01a82bd1d02000dba140/enhanced_patterns.py#L21-L60>
- 偵測方法：對極值序列（max_min）取滑動 4 個極值的視窗，視窗在 K 線序號上的跨度 ≤ 50 就處理（L30）；只比較第一個與最後一個高點／低點。
- 門檻（L50、L54、L58）：上升＝\|高₁−高ₙ\| < 2%·高₁ 且 低ₙ > 低₁×1.02；下降＝高ₙ < 高₁×0.98 且 \|低₁−低ₙ\|<2%·低₁；對稱＝高ₙ<高₁×0.98 且 低ₙ>低₁×1.02。最少 2 高 2 低。
- 沒有突破、量、目標價。品質很粗（只看兩端、不檢查中間）；可借的只有「水平＝首尾差 ≤2%」這個量級。

### G4 neurotrader888/TechnicalAnalysisAutomation — MIT（trendline 與 flag/pennant）
- 網址：<https://github.com/neurotrader888/TechnicalAnalysisAutomation/blob/da99c20bf3d977b639451258cd6cfca9baa1dcc3/trendline_automation.py#L6-L117>、<…/flags_pennants.py#L118-L195>、<…/flags_pennants.py#L232-L282>
- **趨勢線擬合（trendline_automation.py）**：先對收盤做線性迴歸 → 取離迴歸線最遠的高點／低點當錨點 → 把線的斜率最佳化：線通過錨點，且**所有價格都在線的同一側**（支撐：`diffs.max() ≤ 1e-5`；阻力：`diffs.min() ≥ −1e-5`，L17-L20），在此限制下最小化平方誤差（L23）。資料先取 **自然對數**（L127-L128），lookback=30 的滾動視窗（L130）。
  - 這是「包絡線」（envelope）型趨勢線：線不穿過任何 K 線，緊貼外緣。
  - 可借：log 空間、「線不得被價格穿過」當硬約束。授權 MIT，可用但要保留授權聲明。
- **Pennant（flags_pennants.py）**：三角形的近親（旗形後的收斂）。bull 版（L118-L195）：
  - 先有旗桿；旗形寬度 ≤ 旗桿寬度的 0.5（L128）、旗形高度 ≤ 旗桿高度的 0.5（L134）。
  - 用 PIP 取 5 個點，上下兩條線求交點 x；交點若落在旗形內（0 ≤ x ≤ 最後一點）→ 否決（L162-L163），**但這是 pennant 的要求：兩線要「在右側」才收斂**；交點太靠左的「發散」也否決（L166-L167）。
  - 突破＝現價高於阻力線外推值（L170-L172）；`support_slope > 0` 判為 pennant，否則為 flag（L175-L178）。
  - 沒有目標價（旗桿高度當目標是原 YouTube 影片的作法，程式碼裡沒有）。

### G5 GregoryMorse/trendln — MIT
- 網址：<https://github.com/GregoryMorse/trendln/blob/82dcb119a667f1a1ef5dd8e5a55624236e56973d/README.md#L59-L87>
- 方法：先找局部極值（`METHOD_NUMDIFF` 數值微分、或 NAIVE），再找「通過 ≥2 個極值（NCUBED 為 3 個）且不被價格穿過」的線（`METHOD_NSQUREDLOGN` 兩點排序斜率搜尋、`METHOD_HOUGHPOINTS` Hough 轉換）。
- 預設參數：`window=125`（先分窗找線再合併）、**`errpct=0.005`**（0.5% 容錯，線可被價格穿過的幅度）、`accuracy=2`（數值微分精度）。
- **沒有三角形分類、突破、目標價**；只提供趨勢線。可借：0.5% 容錯量級（與現行頸線 V3a 的 0.5% 一致）。

### G6 srdeotarse/Linear-Regression-for-Stock-Chart-Pattern-Recognition — MIT
- 網址：<https://github.com/srdeotarse/Linear-Regression-for-Stock-Chart-Pattern-Recognition/blob/b533ce6ecd16aa7cf15336ea733dceb09655b0e0/TrianglePattern.ipynb>
- 方法：pivot（前後各 3 根）→ 取最近的一個高／低 pivot＋視窗內最高高點／最低低點＋其後的次高／次低，**每側只用 3 點**做 `linregress`。僅示範，**沒有斜率分類、容忍度、突破、目標價**。
- 結論：品質低，只是 G1 的教學版本；不採用。

### G7 TradingView 開源 Pine（只讀到說明，原始碼未取得）
- 「Triangle Pattern Detection [Dots3Red]」：<https://kr.tradingview.com/script/k1uUbyXz-Triangle-Pattern-Detection-Dots3Red/>。授權頁面只標「OPEN-SOURCE SCRIPT」，具體授權（TradingView 預設為 MPL-2.0）**未驗證**。說明列出的驗證項目：兩線收斂、**兩個 pivot 之間沒有 K 線穿越趨勢線**、上下線起點對齊容差、最小／最大寬度、max apex distance、breakout threshold %（收盤超出線多少才算突破）。參數預設值頁面沒有列。
- 「Auto Chart Patterns [Market Adapted]」（Trendoscope）：<https://in.tradingview.com/script/fTropr3t-Auto-Chart-Patterns-Market-Adapted>。以 zigzag pivot 找通道、楔形、三角形；有 30% 誤差門檻等設定。同樣只讀到說明。
- 因為沒讀到原始碼，**不引用具體公式**，只把它們當作「業界同樣用：pivot＋兩線收斂＋不穿越＋apex 距離＋收盤突破緩衝」的旁證。

### 負面結果（查過但沒有三角形）
- BennyThadikaran/precise-patterns（GPL-3.0）、pkjmesra/PKScreener（MIT）、yanivil/SignalSync（MIT）：見第 1 節表。使用者列的 PKScreener、SignalSync 都沒有三角形偵測，不要再花時間找。

---

## 3. 論文與書

### A1 Lo, Mamaysky & Wang (2000)，*Journal of Finance* 55(4)
- 全文：<https://www.nber.org/system/files/working_papers/w7613/w7613.pdf>（WP p.11，Definition 3；與 HS 同一章，極值取法見 `academic-ihs-research.md` 第 2 節）。
- **原文定義（目視核對）**：Triangle tops (TTOP) 與 bottoms (TBOT) 是 5 個連續局部極值 E₁…E₅，
  - TTOP ≡ { E₁ 是極大值；E₁ > E₃ > E₅；E₂ < E₄ }
  - TBOT ≡ { E₁ 是極小值；E₁ < E₃ < E₅；E₂ > E₄ }
  對照：Broadening 是 E₁<E₃<E₅ 且 E₂>E₄（相反）；Rectangle（WP p.12）是高點彼此、低點彼此各在平均的 0.75% 內，且 lowest top > highest bottom。
- 含義：Lo 的 triangle 只定義「高點遞降、低點遞升」的**收斂**（等於對稱三角形的方向性條件），**沒有水平邊的變體**，也**沒有斜率門檻、觸碰次數、突破或目標價**。TTOP 與 TBOT 的差別只是起點是高點或低點，不是上升／下降三角。
- 對本專案：可當「對稱三角至少 5 個交替極值、高點遞降、低點遞升」的最低標準；上升／下降三角要把 Lo 的 Rectangle（水平）與 Triangle（收斂）各取一邊混合。

### A2 Ben Omrane & Van Oppens (2004)，"The Predictive Success and Profitability of Chart Patterns in the Euro/Dollar Foreign Exchange Market"
- 全文：<https://research.dial.uclouvain.be/bitstreams/b4472704-16de-4786-8a0d-5a575d0b4e11/download>（CORE Discussion Paper 2004/35；定義在 PDF p.21 附錄 C.10、C.11，符號定義在 p.5-6）。
- 符號：p(Eᵢ,Eⱼ)＝兩極值連線斜率；V_t(Eᵢ,Eⱼ)＝該線在 t 的價格；t_int ＝ 兩線交點時間。
- **Triangle Top (TRIT)**：4 個極值 E₁…E₄，
  - E₁ > E₂；
  - p(E₁,E₃) ≤ tg(−30°)（阻力線向下，斜率不得比 −30° 緩，原文就這樣寫）；
  - 0.9 ≤ \|p(E₁,E₃)\| / p(E₂,E₄) ≤ 1.1（兩線斜率大小在 ±10% 內，也就是**對稱**三角形）；
  - 型態結束時間 t_f ≤ t_E₁ ＋ 0.75 ×（t_int − t_E₁）（**必須在到 apex 的 75% 之前完成／突破**）；
  - (P_{t_E₁} − P_{t_min}) ≥ (2/3) × h（前置走勢高度要 ≥ 2/3 h）。
  TRIB 為鏡像（E₁ < E₂、p(E₂,E₄) ≤ tg(−30°)、比值 0.9–1.1、同樣 75% 條件）。
- **未確認**：論文沒有在三角形的條件旁明確定義 h（HS 的 h 是頭部高度＝E₃ 到 E₂E₄ 線的距離）；推測三角形的 h 是型態最寬高度，但**原文沒寫**，未採用其 2/3 條件。
- 目標價（p.7，式 3.3 的概念）：預測「至少走完一個 h」，且要在 [t_f, t_f + (t_f − t_d)] 時間內達成。這與 Bulkowski 的 measure rule 同源。
- 對本專案的價值：唯一給出「收斂比例上限」（75% 前）的學術來源，也是唯一把對稱度（斜率比 0.9–1.1）寫成公式的來源。**但它處理的是 EUR/USD 日內資料，斜率比 ±10% 太嚴**，Dots3Red 之類的實作改成可調的最大斜率比。

### B1 Bulkowski（thepatternsite.com 三頁 ＋ apex 頁；數字為網站所載，樣本是美股日線、牛市）
來源：<https://thepatternsite.com/at.html>（上升）、<https://thepatternsite.com/dt.html>（下降）、<https://thepatternsite.com/st.html>（對稱）、<https://thepatternsite.com/Apex.html>（apex）。**書本全文未讀**，書中可能有網站沒寫的細節（如寬度、量的細部統計）。

| 項目 | 上升三角 | 下降三角 | 對稱三角 |
|---|---|---|---|
| 形狀 | 上線水平、下線向上 | 下線水平、上線向下 | 下線向上、上線向下 |
| 觸碰 | 一條線 ≥3 點，另一條 ≥2 點，須是明確的波峰／波谷 | 同左 | 同左 |
| 價格填滿（crossing） | 必須從一邊穿到另一邊，「填滿三角形，不是留白」 | 同左，「避免大量留白」 | 同左 |
| 成交量 | 78% 以上呈下降 | 78% 下降，突破前很低 | 84%–86% 下降 |
| 突破方向 | 上破 63% | 上破 **53%** | 上破 60% |
| 突破位置 | 平均走到 apex 的 **64%**（兩個方向相同） | 中位數 **61%–65%** | **74%** |
| 確認 | 價格收盤穿出趨勢線 | 價格收盤穿出趨勢線 | 價格收盤穿出趨勢線 |
| 失敗率（損益兩平，上破／下破） | 17%／38% | 22%／23% | 25%／37% |
| 平均漲／跌幅 | 43%／13% | 38%／15% | 34%／12% |
| 回測（throwback／pullback）率 | 64%／63% | 60%／58% | 62%／65% |
| 達到目標價比例（上破／下破） | **70%／44%** | **64%／50%** | **58%／36%** |
| 成績排名（上破／下破，1 最佳） | 16/39；30/36 | 33/39；15/36 | 36/39；34/36 |
| 樣本數 | >1,400 筆完美交易 | >1,300 | >3,000 |

- **目標價（measure rule）**：
  - 上升：高度＝水平線 B 的價格 − 型態內**最低的谷底** A；從突破價加（上破）或減（下破）。
  - 下降：高度＝**最高的峰** A − 水平線 B。
  - 對稱：高度＝最高峰 − 最低谷。
  - 上面表格的「達標率」，Bulkowski 的 tip 說是「把高度乘上它再加到突破價」（保守目標）。突破價＝價格穿過趨勢線的那一點。
- **停損**：上破用型態內某個小低點（A 側）；下破用水平線價格 B（上升三角頁）。
- **Apex 與轉折**（apex 頁）：三角形頂點附近 75% 的樣本在幾天內出現小高或小低；60% 在 apex 附近改變方向；apex 與最近極值平均距離 3.6 天（隨機基準 6.55 天）。含義：**過了 apex 還沒突破的三角形，失效機率高**，支持「apex 過了就 stale」。
- 其他（摘要來源，**未讀到全文，僅供參考**）：Bulkowski「busted」頁把「價格最多走 10% 就反轉，並收盤穿過**三角形另一側的頂或底（不是趨勢線）**」定義為假突破；對稱三角下破有 48% 被反轉（牛市）；上升三角下破被反轉 46%、平均再漲 36%。來源：<https://www.thepatternsite.com/BustAscTriangles.html>、<https://thepatternsite.com/BustSymTriangles.html>（只看到搜尋摘要）。另有二手網站把「突破需超過 5%」歸給 Bulkowski，**本調研沒找到一手出處，不採用**。
- 突破量：對稱三角頁說「突破量高於 30 日均量的型態表現較好」；沒有給必要門檻。

### 其他有讀到的二手
- Bulkowski 對趨勢線的詳細擬合法（如何畫線）網站沒寫；書裡有圖解，**未讀**。
- Zapranis & Tsinaslanidis、Lucas、Osler 的三角形定義：之前調研（`academic-ihs-research.md` 第 1 節）已記錄 Zapranis 付費牆讀不到；Osler & Chang (1995) 只做 HS，無三角形。本次搜尋未找到其他有明確三角形數學定義且可讀全文的論文，**不採用**。

---

## 4. 各來源規則對照（一頁總表）

| 規則面向 | G1 zeta | G2 stock-pattern(GPL) | G3 tysoncung | G4/G5 | A1 Lo | A2 Ben Omrane | B1 Bulkowski |
|---|---|---|---|---|---|---|---|
| 轉折點取法 | 左右 3 根 | 左右 6 根 | 外部極值序列 | 外部／數值微分 | 核迴歸極值 | 外部極值 | 目視 |
| 最少點數 | 2+2（實際，bug） | 5 轉折＋現價 | 2+2 | ≥2（NCUBED 3） | 5 極值 | 4 極值 | 3+2 |
| 擬合 | 迴歸、\|r\|≥0.9 | 兩點連線 | 無（只比首尾） | 包絡線（不被穿越） | 無 | 兩點連線 | 目視 |
| 水平定義 | 斜率 ±1e-5（絕對） | 高點差 ≤ 中位數 K 線區間 | 首尾差 <2% | — | Rect：0.75% | — | 水平 |
| 對稱／斜率門檻 | 正負號 | 只比高低大小順序 | 首尾 ±2% | — | E1>E3>E5, E2<E4 | 斜率比 0.9–1.1；≤tg(−30°) | 上斜＋下斜 |
| 包住（不穿線） | 無 | 任何收盤穿線即否決 | 無 | 價格不得穿線（容錯 0.5%） | — | — | 填滿 |
| 收斂比例 | — | 兩線已交叉＝失效 | — | — | — | t_f ≤ 起點到 apex 的 75% | 突破在 61%–74% |
| 突破定義 | 無 | 無 | 無 | 收盤>線外推（pennant） | 無 | 無 | 收盤穿趨勢線 |
| 目標價 | 無 | 無 | 無 | 無 | 無 | 高度 h 投射 | 高度（×達標率）從突破價投射 |
| 量 | 無 | 無 | 無 | 無 | 無 | 無 | 量縮 78%–86%；突破量>30 日均量較佳 |
| 授權 | 無（參考概念） | GPL-3.0（參考概念） | MIT | MIT | — | — | 引用數字 |

---

## 5. 建議的量化定義

> 以下數值中，**標「文獻」的是上面來源給的**，標「建議」的是我的工程假設（需用使用者標的範例與回測校準，與 IHS 一樣先出初值再調）。所有價格計算建議在 **log 價格**空間（IHS 的 V1/V2 已用 log，百分比容忍度自然一致；neurotrader888 也用 log）。

### 5.1 符號與前置
- pivot 沿用現行：低點／高點「嚴格大於（小於）左右各 N 根」，N＝4h 5／1d 5／1w 3；最後 N 根沒有 pivot（確認延遲）。
- ATR＝ATR(14)，在最後一根已收盤 K 線取值；ε_touch ＝ max(0.3 × ATR/價格, 0.5%)（建議）。以此換算：1d 約 1.2%、1w 約 3%、4h 約 0.6%–1%。
- R(t)＝阻力線（用視窗內 pivot 高點的最小平方法，log 價）；S(t)＝支撐線（pivot 低點）。gap(t)＝R(t)−S(t)（log）。
- 型態起點 t_s＝所有用到的 pivot 中最早者；t_now＝最後一根已收盤 K 線；W＝t_now−t_s（根數）。
- H0＝gap(t_s)（型態起點的寬度，Bulkowski/Ben Omrane 的「型態最寬高度」）。

### 5.2 觸碰（touch）與擬合品質
1. 高點線 R：視窗內 pivot 高點 ≥2 個；低點線 S：pivot 低點 ≥2 個；**合計 ≥5 個轉折**（文獻：Bulkowski 3+2；Lo、stock-pattern 5 個極值）。不要求高低完全交替，但要有「分別落在兩條線上」的明確峰與谷。
2. 每個被採用的 pivot 到自己那條線的殘差 ≤ ε_touch（建議；等效於 trendln 的 0.5% errpct 概念、G1 的 \|r\|≥0.9 概念）。殘差超過者不算觸碰（可剔除重擬合一次）。
3. 兩點以上共線：拿 pivot 高點先用最小平方法；若最大殘差 > ε_touch 就從最遠的 pivot 剔除重算，最多重算 2 次，仍不過則放棄該起點。
4. （選配，品質分）Bulkowski 的「價格必須填滿（crossing）」：型態內收盤穿越中線 (R+S)/2 的次數 ≥ 觸碰數−1；用來擋掉「兩條線中間大片空白」的假三角。

### 5.3 斜率分類（用「漂移占高度」比例，不是絕對斜率）
定義：drift_R ＝ \|R(t_last_R) − R(t_first_R)\| ／ H0，S 同理，其中 t_first/last 是該線第一個與最後一個 pivot。符號：R 上升 drift>0、下降 drift<0（用有號值判方向）。

| 型態 | 阻力線 R | 支撐線 S | 備註 |
|---|---|---|---|
| 上升三角 | 水平：\|drift_R\| ≤ 0.15 | 上升：drift_S ≥ +0.30 | S 的 drift 設得比 R 大，是因為 gap 要縮到 0.25–0.75（見 5.4） |
| 下降三角 | 下降：drift_R ≤ −0.30 | 水平：\|drift_S\| ≤ 0.15 | 鏡像 |
| 對稱三角 | 下降：drift_R ≤ −0.20 | 上升：drift_S ≥ +0.20 | 兩線都明顯傾斜；斜率比 max(\|sR\|,\|sS\|)/min(…) ≤ 3（Dots3Red 也有可調最大斜率比；Ben Omrane 嚴格到 0.9–1.1） |
| 都不符合 | 兩線同向（楔形、旗形、通道）、或一條線介於水平與傾斜之間 | | **不歸類為三角形**，丟棄（楔形另案） |

- 水平容忍用 0.15·H0 是建議值。對照：G3 用首尾差 2%、Lo Rectangle 用 0.75%、G2 用「一根 K 線區間的中位數」；0.15·H0（H0 常為 8%–30%）約落在 1.2%–4.5%，量級相當且與價位無關。
- 若想更保守可再加一道 ATR 地板：水平線漂移 ≤ max(0.15·H0, 1.0×ATR)；傾斜線漂移 ≥ 0.5×ATR（避免 H0 很小時把雜訊當傾斜）。

### 5.4 收斂與 apex
1. 兩線在右側相交：t_apex ＞ t_now。以 log 價線求交點：t_apex ＝ t_ref ＋ (S(t_ref) − R(t_ref)) ／ (sR − sS)（sR、sS 為每根斜率；兩線平行或發散則否決）。
2. apex 進度 p ＝ (t_now − t_s) ／ (t_apex − t_s)。**p ＜ 1**（尚未走到頂點）才是有效三角；過了 apex 仍沒突破＝stale（Bulkowski apex 頁：apex 附近多半轉折）。
3. 收斂比例 ρ ＝ gap(t_now) ／ H0。**建議 0.20 ≤ ρ ≤ 0.75**：
   - ρ ≤ 0.75 ＝ 至少已收斂 25%（太早的「類矩形」不算；Ben Omrane 的上限 75% 對應 ρ≥0.25，Bulkowski 突破中位數 61%–74% 對應 ρ≈0.26–0.39）。
   - ρ ≥ 0.20 ＝ 還沒擠到頂點；ρ 太小就接近 apex，預期突破後走勢也最弱（Bulkowski 的 apex 轉折）。ρ < 0.20 可標「接近 apex，late」而非直接丟棄。
4. 型態寬度下限：H0 ≥ 2 × ATR%（不是雜訊）；且 H0 ≤ 80%（避免跨越巨幅行情的異常大三角）。（建議值）
5. 兩線起點時間對齊（G2 的 1.8 倍、Dots3Red 的 start alignment tolerance）：第一個高點與第一個低點的時間差 ≤ 0.5 × W（建議）。

### 5.5 型態長度（根數，t_s 到 t_now）
| | 4h | 1d | 1w | 依據 |
|---|---|---|---|---|
| 最短 | 24 | 20 | 10 | 建議；5 個轉折在 pivot N=5/5/3 下至少要約 4N 根 |
| 最長 | 200（台股 4h 120） | 120 | 60 | 建議；對照 G3 ≤50 根、G1 25 根、Lo 38 根的視窗皆偏短，IHS 用 1000/600/200，三角形較小故取折衷 |

這些是「**沒有文獻給根數**」的工程取值（Bulkowski 只描述 apex 比例，沒給根數）。上市時間短的新幣（1w <60 根）自然不會有 1w 三角形。

### 5.6 包住（enclosure）
- 型態期間（t_s 到 t_now，**突破那根之前**）：
  - 收盤：close ≤ R(t)×(1+0.5%) 且 close ≥ S(t)×(1−0.5%)（沿用 V3a 的 0.5%；trendln 也是 0.5%；G2 零容忍）。
  - 最高／最低：high ≤ R(t)×(1+1%)、low ≥ S(t)×(1−1%)（沿用 T4 的 1%，容許影線）。
- 違反者視為「突破」處理（見 5.7）而不是「包住失敗」：包住失敗只用於「突破前」的歷史檢查；一旦有收盤穿出就進入已突破分支。

### 5.7 突破定義
- **預設**：收盤價 close > R(t)×(1+buf) 為上破；close < S(t)×(1−buf) 為下破。buf＝4h 0.5%／1d 1%／1w 1%（與現行 `breakout_buffer` 同值，建議）。依據：Bulkowski 用「收盤穿越趨勢線」；Dots3Red 用「收盤超出線的百分比門檻」。
- **選配**：`breakout_price="high"/"low"`（影線穿越）與 D36 同一個開關，預設仍建議 close；原因是三角形的線本身就是估計值，影線假突破多（Bulkowski：上升三角下破 46% 會被反轉）。
- 突破點：第一根符合的 K 線（不重新計時，同現行 confirmed 規則）。突破時的 p 必須 ≤ 1.0；若第一次穿越發生在 apex 之後（p>1）＝無效（過了 apex 的穿越不算該三角形的突破）。
- **成交量（選配，預設關）**：Bulkowski 統計「型態內量縮 78%–86%」「突破量 > 30 日均量表現較好」皆為統計描述、非必要條件。建議同現行 volume filter：突破量 ≥ 前 20 根均量×1.2 才標 `vol_ok`，不過濾，只加分。
- 假突破／失敗：突破後收盤回到**三角形另一側的頂或底之外**，或回到被穿越線內側超過 0.25×H0（對應 V3c），標 `failed`。Bulkowski 的 bust 定義是「價格最多走 10% 就反轉，並收盤穿過型態另一側的頂／底（不是趨勢線）」。

### 5.8 目標價與失效價
| 項目 | 公式（建議） | 依據 |
|---|---|---|
| 目標價高度 H | 上升：R水平 − 型態內最低 pivot 低點；下降：型態內最高 pivot 高點 − S水平；對稱：最高 pivot 高點 − 最低 pivot 低點 | Bulkowski measure rule；Ben Omrane h |
| TP2（完整） | 上破：突破價 + H；下破：突破價 − H（突破價＝突破那根的邊線價；未突破時＝目前邊線價作預估，與現行 TP2 做法一致） | Bulkowski、Ben Omrane（至少走一個 h） |
| TP1（保守） | 突破價 ± H × 達標率：上升三角 0.70(上)／0.44(下)；下降 0.64／0.50；對稱 0.58／0.36 | Bulkowski tip（高度乘「達標率」）；注意達標率來自美股日線牛市，加密／台股只是近似 |
| 失效價（上破） | 型態內最近一個 pivot 低點（A 側）；更保守＝S 水平（上升）／apex 前最後低點 | Bulkowski 停損建議 |
| 失效價（下破） | 上升三角用水平線價 R；其他用型態內最近 pivot 高點 | Bulkowski 停損建議 |

### 5.9 其他失效與去除
- 突破前：若 close 穿出某條線且 buf 內未確認 → 仍標形成中；若同一根 K 線同時穿出兩邊 → 不成立。
- 突破後 N 根（沿用 confirmed 的 4h 6／1d 5／1w 3）後標 stale。
- 突破後 close 回到型態另一側 → `failed` 後 stale。
- p ≥ 1 未突破 → stale（apex 過了）。

---

## 6. 狀態設計與方向標示

| 狀態 | 條件 | 說明 |
|---|---|---|
| forming（形成中） | 滿足 5.2–5.6；尚無收盤穿線（含 buf）；0.20 ≤ ρ ≤ 0.75；p<1 | 對應 IHS 的 forming／watchlist。可再加 `near` 子標籤：現價離任一邊界 ≤ 0.25×gap |
| breakout_up / breakout_down（已突破） | 第一根收盤穿出 R×(1+buf) 或 S×(1−buf)，且該根 p ≤ 1 | 保留突破方向、突破 K 線、突破價；窗口 4h 6／1d 5／1w 3 根（同 IHS） |
| failed（假突破） | 見 5.7 | 可不顯示，或在網站「失敗」分頁列出（Bulkowski：反轉後常有強走勢，值得標） |
| stale（過期） | apex 已過／窗口已過／被取代 | 不顯示，與 IHS 一致 |

**方向預期與偏向（`bias` 欄位只是標籤，不是訊號）**
| 型態 | 傳統預期 | Bulkowski 實測 | 建議標示 |
|---|---|---|---|
| 上升三角 | 偏多 | 上破 63%；上破成績排名 16/39（不錯），下破 30/36（差）；上破失敗率 17% | bias＝多；上破才進「已突破」，下破要標「反向突破，勝率低（失敗率 38%）」 |
| 下降三角 | 偏空 | **上破 53%**；下破排名 15/36、上破 33/39；兩邊損益兩平失敗率 22%／23% | bias＝空（傳統），但 UI 註明「實測 53% 上破」；方向以實際突破為準 |
| 對稱三角 | 中性，跟隨突破方向 | 上破 60%；整體成績最差（36/39、34/36）；下破失敗率 37% | bias＝中性；只在已突破後給方向；整體分數權重降低 |

- 掃描器的「動作」只由實際突破觸發（Discord 通知、confirmed 清單），不要用形成中三角形的偏向發訊號。
- 通知策略（建議）：預設通知「已突破」且 `failed` 前；上破／下破都通知但訊息註明方向與型態類型；是否只通知多方由使用者決定（本掃描器以頭肩底多頭為主）。

---

## 7. 實作建議（與現有架構的銜接）

### 7.1 資料流（無 look-ahead）
1. **輸入**：與 IHS 相同的已收盤 K 線；用同一份 `swings`（pivot 高低，N 與現行相同），**只使用 index ≤ t_now−N 的已確認 pivot**。as-of t 的結果只依賴 `df[:t+1]`，截斷測試鎖住（apex 是對右側的外推，不是 look-ahead）。
2. **候選產生**（每條序列）：取最近 K＝12 個 pivot 高、12 個 pivot 低；起點 s 取最近 8 個 pivot 之一；對每個 s：用 s 之後的 pivot 高／低各做最小平方法 → 5.2 觸碰 → 5.3 分類 → 5.4 收斂 → 5.6 包住 → 5.5 長度。
3. **突破與狀態**：t_now 之前最後一根的包住檢查；從 pivot 最後一點之後掃第一根穿越＝突破。
4. **去重**：同幣同週期、時間重疊的三角形：已突破優先 → 觸碰點數多 → 型態長（t_s 早）→ 分數。三角形與 IHS 是不同型態類別，**互不去重**（可標 `overlaps_ihs`）。
5. **跨週期**：沿用 D36 的「1w 涵蓋 1d 且方向相反則 1d 不輸出」的想法，三角形先不做，避免複雜化，之後再看是否需要。

### 7.2 輸出欄位（建議 scan.json 新增 `triangles` 陣列，不動現有 IHS 欄位）
`type`（asc/desc/sym）、`status`、`bias`、`resist_line`(t1,p1,t2,p2,slope)、`support_line`、`touches_R`、`touches_S`、`apex_t`、`apex_progress`(p)、`converge_ratio`(ρ)、`height_H0`、`breakout_idx`、`breakout_dir`、`breakout_price`、`tp1`、`tp2`、`invalid_price`、`score`、`vol_ok`。網站只需畫兩條線（起點與終點座標）、型態類型與狀態標籤；線的延伸到 apex 可由前端算。

### 7.3 分數（建議，0–100，輸出排序用）
- 觸碰品質 30：每條線殘差平均 / ε_touch、點數（5→3.5 分，≥6 滿）。
- 收斂與對稱 20：ρ 越接近 0.4（Bulkowski 突破中位數附近）越高；類型符合度（斜率比）。
- 填滿 15：中線穿越次數、K 線區間／gap 比。
- 突破／接近 20：已突破 20，否則依離邊界距離。
- 成交量 15：量縮趨勢（選配）＋突破量（選配）。
（建議值，與 IHS 的分數構成類似；需實測調整。）

### 7.4 計算量估計（**未實測**，僅估）
- 序列數：幣約 1,400 ＋ 台股約 2,000 ＝ 3,400，×3 週期 ≈ **10,200 條**，每條 ≤1000 根。
- 每條：pivot 已由 IHS 算好可重用；候選 ≈ 8 個起點 × 3 種分類（分類是門檻比較，不用重算擬合）→ 擬合 ≈ 16 次小迴歸（每次 ≤10 點），包住檢查是 numpy 向量化（≤300 根）。估計 ≈ 0.5–2 ms／條。
- 總計 ≈ **5–20 秒**（單執行緒），遠小於下載 K 線與 IHS 組合搜尋。若 pivot 重算會多一點，但可共用。
- 加速：先用「≥5 個 pivot、且 R/S 都有 ≥2 個」預篩，約可擋掉 70%+ 序列（多數序列趨勢明確，沒有 5 個交替轉折）。

### 7.5 測試
- 合成資料單元測試：理想上升／下降／對稱三角（含雜訊）→ 三種分類各通過；矩形、楔形、旗形 → 都被拒絕。
- 截斷測試（沿用現行）：同一序列在 t、t+1…逐根截斷，結果只依賴過去。
- 使用者範例：請使用者各給 3–5 個三種三角形的真實例子（幣／週期／日期），當 golden test（IHS 的做法）。

### 7.6 授權處理
- 不複製 zeta-zetra（無授權）、stock-pattern、precise-patterns（GPL）。
- 可參考 MIT 的 neurotrader888（包絡趨勢線）、trendln（容錯 0.5%）、tysoncung 的概念，若直接移植程式碼要保留 MIT 聲明；本文建議自行實作（演算法簡單）。

---

## 8. 無法確認的地方

1. **Bulkowski 的數字**來自網站（美股日線、牛市、「perfect trades」定義見其 glossary）；**書本全文沒讀到**。加密貨幣與台股、4h／1w 週期未必適用（尤其突破方向機率與達標率）。
2. **Ben Omrane & Van Oppens 的 h**：三角形條件旁沒有明確定義；2/3·h 條件未採用。其「斜率比 0.9–1.1」是 EUR/USD 日內資料的嚴格版，本文放寬為 ≤3。
3. **Lo 2000** 的 triangle 只有收斂型（E₁>E₃>E₅、E₂<E₄），沒有水平邊的上升／下降三角，也沒有門檻；上升／下降三角在學術文獻裡沒有找到可讀全文的明確定義（Zapranis 等付費牆）。
4. **所有數值門檻**（水平 0.15·H0、傾斜 0.30·H0／0.20·H0、ρ 0.20–0.75、ε_touch 0.3·ATR、長度上下限、H0 ≥ 2 ATR）是我的**初值建議**，文獻沒有直接給；需要用使用者範例＋回測校準。
5. **Dots3Red／Trendoscope**（Pine）原始碼沒拿到，授權未驗證；只當旁證。
6. **trendln** 只讀了 README 的參數說明，沒有逐行讀演算法。
7. **Bulkowski busted triangles** 三頁只讀到搜尋摘要，未讀全文；「二手網站說的 5% 突破濾網」沒找到一手出處，不採用。
8. 「下降三角偏空」的傳統說法與 Bulkowski 實測（53% 上破）衝突，網站標示需向使用者說明；是否要預設只通知「偏向方向」的突破由使用者決定。
9. stock-pattern 中以絕對斜率排除組合的幾行（L1082-L1090）意圖不明，未採用。
10. 計算量為估算，**沒有在實際資料上跑過**。
