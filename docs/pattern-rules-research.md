# 頭肩底（IHS）型態規則補強調研：還沒用到的量化規則與公式

調研日期：2026-10-18。範圍：只找「本專案尚未實作」的規則（現況見 `ihs_scanner/config.py` 與 charter D4–D19）。
方法：SignalSync、stock-pattern、neurotrader888 都用 `gh api` 下載原始碼逐行讀過；Savin 論文用 PDF 全文轉文字讀過；Bulkowski 官網頁面實際讀過；其餘來源的限制見第 4 節。
符號：LS/H/RS = 左肩/頭/右肩的**低點**（`.low`、index）；N1 = LS..H 間最高 swing high、N2 = H..RS 間最高 swing high；`neck(i)` = 過 N1、N2 的頸線在 bar i 的值；
`head_height = neck(H.idx) − H.low`；`ATR` = 14 根 ATR（SignalSync 用簡單平均 TR，`scan.py` L862）；`W = RS.idx − LS.idx`。

授權縮寫：**MIT** = 可參考並可改寫程式碼（保留版權聲明）；**GPL-3.0 / 無授權 / TradingView House Rules = 只參考概念，不得複製程式碼**；**論文/書/網站 = 規則公式本身是事實，不涉及程式碼授權，但請自己重寫**。

---

## 1. 規則清單表

「重疊」欄：無 = 本專案完全沒有；部分 = 有近似規則但公式不同；有 = 已實作（列出來是為了說明為何不推薦）。

### A. 來自 SignalSync（MIT，可參考程式碼）

來源檔：https://github.com/yanivil/SignalSync/blob/main/scan.py （main，調研時 HEAD = `9ef9b22803`）；常數 L150–160；`detect_inverse_hs` L1395–1535；`evaluate_breakout` L1062–1120。
文件：https://github.com/yanivil/SignalSync/blob/main/docs/wiki/02-Pattern-Catalog.md （L28、L62–80）、https://github.com/yanivil/SignalSync/blob/main/docs/wiki/03-Configuration-and-Tuning.md （L19、L58、L252–266）。

| # | 規則名稱 | 精確公式 | 預設門檻 | 來源行號 | 授權 | 解決的假型態 | 與現有重疊 |
|---|---|---|---|---|---|---|---|
| R1 | 頭深 ATR 正規化 | `H.low < LS.low − k·ATR[H]` 且 `H.low < RS.low − k·ATR[H]` | k = 1.0 | scan.py L151、L1436 | MIT | 頭只比肩低一點點（雜訊級的「頭」）；低波動時 % 門檻太鬆、高波動時太緊 | 部分（現有 `min_head_depth` 是 3%/5% 固定 %） |
| R2 | 肩差以頭高為分母 | `abs(LS.low − RS.low) ≤ r·head_height` | r = 0.30（spec profile） | scan.py L153、L1457–1459；catalog L72 | MIT | 同樣 5% 肩差，在頭很淺的型態裡肩比頭還重要；頭很深時 5% 又太嚴 | 部分（現有以肩價 % 為分母） |
| R3 | 頸線總傾斜（與 bar 數無關） | `abs(slope·W) / close[H] ≤ T`，`slope = (N2.high − N1.high)/(N2.idx − N1.idx)` | T = 0.15 | scan.py L156、L1450–1456 | MIT | 寬型態用「每根 % 斜率」角度規則會放行總傾斜很大的斜線 | 部分（現有 ±30° 用 atan(每根 %斜率×100)；30° ≈ 0.577%/bar，W=60 根時頸線總傾斜可達 ~35%，W=100 時 ~58%，實際上對寬型態幾乎不設限；兩者可並用） |
| R4 | 兩側「肩→頸線點」時間比 | `abs((N1.idx−LS.idx) − (RS.idx−N2.idx)) / max(兩者) ≤ 0.40` | 0.40（spec）；**tuned profile 關閉** | scan.py L155、L1461–1465；cfg.md L19 | MIT | LS→N1 與 N2→RS 反彈段極不對稱（一邊緩升一邊暴升） | 部分（現有只有 LS→H / H→RS 時間比 ≤2.5） |
| R5 | 前置下跌（SMA 或 head-height 擇一） | `SMA50 < SMA200` 或 `max(high[LS−60..LS]) − LS.low ≥ 1.0·head_height` | 60 根回看；1.0 倍頭高 | scan.py L159、L1468–1475；cfg.md L296 | MIT | 沒有東西可反轉（盤整底部或上升趨勢中的假頭肩底） | 部分（現有選配 `require_prior_downtrend` 已含 head-height 那一支，但**沒有 SMA50<SMA200 這一支**，且用 close 而非 high） |
| R6 | 突破年齡從第一次穿越算、不重置 | `age = n−1−first_close_above_neck`；`age ≤ MAX_AGE` | 日線 3 根（SignalSync）；本專案 D14/D15 已採 | scan.py L1062–1120 | MIT | 洗盤穿越被當成新鮮突破 | **有**（D14），不列入推薦 |
| R7 | MAX_RUNAWAY：追高上限 | 最後 close `> trigger·(1+0.05)` → 視為 STALE | 5% | scan.py L77、L1112 | MIT | 突破後已漲一段，現在才看到＝追高 | 無（現有只有突破年齡，沒有「離頸線多遠」上限；僅 `breakout_distance_range` 用於計分） |
| R8 | 觀察名單接近度 + 地板 | 未突破時：`close ≥ neck·(1−0.05)` 且 `close > RS.low` 才列 WATCHLIST，否則 STALE | 5%；地板 = RS.low | scan.py L78、L1099–1105 | MIT | 離頸線很遠的「候選」佔滿清單；跌破 RS 低點＝型態已失效 | 部分（現有 `max_bars_after_rs` 是時間窗，不是價格距離；「跌破 RS.low 即失效」不確定是否已有，見第 4 節） |
| R9 | 最小報酬風險比 | `RR = (target − entry)/(entry − stop) ≥ 1.0` | 1.0（tuned）；回測 1.0→158 筆/+0.29R，1.5→113/+0.30R，2.0→77/+0.29R | cfg.md L19、`MIN_REWARD_RISK` scan.py L206 區段 | MIT | 目標價低於一個風險單位的「不划算」型態 | 無（顯示/篩選用，不是形狀規則） |
| R10 | 風險上限 | `(entry − stop)/entry ≤ 15%` | 15% | scan.py L193–199 | MIT | 止損離進場太遠（頸線距 RS 很遠） | 無 |

SignalSync 自己的回測結論（日線美股 S&P500、11 年 point-in-time，自述未經外部驗證）：
- IHS 全期 +0.23R／1096 筆；**±40% 兩側對稱（R4）會砍掉最好的訊號（程式註解：被移除的訊號平均 +0.74R）→ tuned profile 關掉**（cfg.md L19）。
- 成交量確認（≥1.3×）在 tuned 也關掉、改成加分。
- 評分 0–100「只能當門檻不能排序」（cfg.md L58、L93）。
- 結論可信度：樣本是美股日線，不等於加密貨幣 4h/1w，僅作方向參考。

### B. 來自 neurotrader888/TechnicalAnalysisAutomation（MIT）

來源：https://github.com/neurotrader888/TechnicalAnalysisAutomation/blob/main/head_shoulders.py （HEAD = `da99c20bf3`）；`check_ihs_pattern` L178–276；`get_pattern_return` L361–392。授權 MIT。
本專案已採 balance／symmetry（D19）；下面是**還沒採**的部分。

| # | 規則名稱 | 精確公式 | 預設門檻 | 來源行號 | 授權 | 解決的假型態 | 與現有重疊 |
|---|---|---|---|---|---|---|---|
| R11 | 型態起點（LS 之前價格必須先高於頸線延伸） | 令 `slope = (N2−N1)/(N2.idx−N1.idx)`；對 `j = 1..(N2.idx−N1.idx)`：`neck_ext(LS.idx−j) = N1 + (LS.idx−N1.idx−j)·slope`，找第一個 `high[LS.idx−j] > neck_ext`；找不到（在 `head_width` 根內）→ 拒絕 | 回看長度 = 頸線寬 `N2.idx−N1.idx` | head_shoulders.py L227–244（IHS）、L123–139（HS） | MIT | LS 之前價格一直在頸線**下方**＝頸線延伸線在 LS 前沒被穿過，肩不是「從高處跌下來」的肩（沒有前置下跌、肩只是盤整底部的一格）；同時產生 `neck_start` 供畫圖 | 無（`require_prior_downtrend` 看的是「LS 前 N 根最高 close 與 LS.low 差距」，不是「是否站上頸線延伸」） |
| R12 | RS 與頸線的「半途確認」（early_find） | `close[i] ≥ (RS.low + N2.high)/2`（RS 與 armpit 中點） | — | L196–205（HS 版 L84–99 對稱） | MIT | 反向：不是過濾，是**提早**一些型態；會增加 look-ahead 風險 | 無；**不推薦**（與 D13 signal_time 精神衝突） |
| R13 | 止損／目標的對照公式 | 止損 = `RS.low`；目標 = `neck(break) + head_height`，`head_height = neck(H.idx) − H.low`；持有上限 = `head_width` 根 | — | L361–392；`head_height` L271 | MIT | （顯示資訊）見第 3 節 | — |
| R14 | pattern_r2：五段折線擬合 R² | 以 LS→N1→H→N2→RS→break 之間的線性折線當模型，`R² = 1 − SS_res/SS_tot`（對 `data[start_i:break_i]`） | 作者註解掉不用 | L43–71、L273–275 | MIT | 「型態清晰度」度量 | 無；作者自己說效果差且延遲，**不推薦**（但可當顯示用清晰度分數） |

### C. 來自 BennyThadikaran/stock-pattern（GPL-3.0，**只參考概念，不得複製程式碼**）

來源：https://github.com/BennyThadikaran/stock-pattern/blob/main/src/utils.py （HEAD = `85c710daa5`）；`is_reverse_hns` L192–226；`find_reverse_hns` L1267–1345。
它的點位命名：A、C、E = 三個低點（LS、H、RS）；B、D = 兩個頸線高點（N1、N2）；F = 最後收盤。`avgBarLength = median(High − Low)`，取 B 到 D 之間的 K 棒（L1325 附近，`df.loc[b_idx:d_idx]`）。

| # | 規則名稱 | 精確公式（改寫成本專案符號） | 預設門檻 | 來源行號 | 授權 | 解決的假型態 | 與現有重疊 |
|---|---|---|---|---|---|---|---|
| R15 | 頸線平坦度（以 K 棒範圍為尺） | `abs(N1.high − N2.high) < M`，`M = median(high−low over N1..N2)` | 1.0×M | utils.py L218 | **GPL-3.0，只參考概念** | 頸線兩端高度差遠大於一根典型 K 棒＝斜頸線，與「平坦頸線」型態不符；以 K 棒範圍當尺，自動適應波動度 | 部分（現有是角度；本質是「用波動度當尺的平坦度」，可改寫成 ATR 版 `abs(N1−N2) ≤ k·ATR`） |
| R16 | 頭相對肩的最小落差（K 棒範圍） | `abs(H.low − RS.low) > 0.6·M`（作者式子寫 `abs(c−e) > round(avgBarLength*0.6,2)`；只檢查頭與右肩，不檢查左肩） | 0.6×M | utils.py L212、L219 | GPL-3.0，只參考概念 | 與 R1 同類（頭夠不夠低），用 median bar range 而非 ATR | 與 R1 重複，擇一即可 |
| R17 | 頸線點必須高於雙肩 | `min(N1.high, N2.high) > max(LS.low, RS.low)` | — | utils.py L217 | GPL-3.0，只參考概念 | 防止退化型態（armpit 低於肩）；由「N=區間最高 high」的定義幾乎自動成立 | 有（實質已成立），不推薦 |
| R18 | 突破前才算型態、已突破太遠者丟棄 | 最後 close 必須 `≤ neck(now)`（只報突破前）；若 E 之後的 `highest_high − min(B,D) > M` → 型態已突破過頭，丟棄 | 超出頸線 > 1×M 即丟 | utils.py L1329–1345 | GPL-3.0，只參考概念 | 已經走完的型態被當候選 | 部分（本專案 D9/D15 用時間窗；這是「價格超出頸線多少 ATR」的版本，等同 R7 的 ATR 版） |
| R19 | 「型態點位須為真實 pivot」檢查 | 若 `A == High[a_idx]` 或 `B == Low[b_idx]` 等（點位取到了錯誤的 OHLC 欄）→ 判定型態構成不良並重試下一個候選 | — | utils.py L1319–1327 | GPL-3.0，只參考概念 | 實作層級的資料一致性檢查 | 不適用（本專案 LS/H/RS 取 `.low`、N 取 `.high` 已分開） |

### D. 學術與 Bulkowski（公式是事實，可自行實作）

| # | 規則名稱 | 精確公式 | 預設門檻 | 來源 | 授權 | 解決的假型態 | 與現有重疊 |
|---|---|---|---|---|---|---|---|
| R20 | Lo–Mamaysky–Wang 肩部/谷部 1.5% 緊容差 | 5 個相對極值 E1..E5（IHS 為 E1 = 低點的鏡像）：`max_i abs(E_i − Ē) ≤ 0.015·Ē`，肩：`Ē=(E1+E5)/2`（i=1,5）；頸線點：`Ē=(E2+E4)/2`（i=2,4）；另 E3 為頭（HS 為 E3>E1 且 E3>E5，IHS 為鏡像） | 1.5%（Lo 原版）；Savin 重校成 4%（R4a/R5a） | Savin, Weller, Zvingelis, "The Predictive Power of Head-and-Shoulders Price Patterns in the U.S. Stock Market", §A.4 與 §B，https://www.biz.uiowa.edu/faculty/gsavin/papers/HSrevision_paw_10%2019%2006.pdf （重述 Lo et al. 2000 的 R1–R5；Lo 原文 DOI 10.1111/0022-1082.00265 未直接讀到） | 論文（公式可用） | 兩側過度不對稱；**同時限制頸線點 E2、E4 彼此接近＝頸線近水平** | 部分（現有肩差 5%/10% 較寬；沒有「頸線點彼此在 x% 內」的規則，只有角度） |
| R21 | Savin R6/R7：肩深／頭深比例帶 | IHS 版：`ratio = mean(N1.high − LS.low, N2.high − RS.low) / head_height`，要求 `ratio` 落在帶內。**論文原文的不等號在 PDF 轉文字時遺失**；文字說明為「排除肩部高度占頭部高度比例過大或過小的情形」，故推定 R6 為 `ratio ≤ 0.7`、R7 為 `ratio ≥ 0.25`（見第 4 節） | 0.25 ≤ ratio ≤ 0.7（推定） | 同上 PDF §B，R6/R7 | 論文 | 肩幾乎和頭一樣深（頭不突出）或肩深度趨近 0（肩只是頸線附近的小凹） | 無 |
| R22 | Savin R8：頭高占價格最小比例 | `head_height / price ≥ 0.03`（原式分母為 E3 = 頭價） | 3% | 同上 PDF §B R8 | 論文 | 頭高太小、與雜訊無法區分 | 有（`min_head_depth` 3%/5%） |
| R23 | Savin R9：相鄰極值時間間隔均勻 | 令 5 個極值日期間隔 `ΔX_i`（i=1..4），`X̄ = mean(ΔX)`；要求 `max_i abs(ΔX_i − X̄) ≤ 1.2·X̄` | 1.2 | 同上 PDF §B R9 | 論文 | 極端水平不對稱（某一段時間特別長／短） | 部分（現有 LS→H / H→RS 比 ≤2.5；R23 涵蓋 4 段、即 LS→N1→H→N2→RS） |
| R24 | Savin 頸線穿越條件 | 要求價格已在頸線上方收盤，且以 E6 為最後確認極值 | — | 同上 PDF §B 第 4 點 | 論文 | 只有形狀、沒有突破的型態 | 有（breakout 模式） |
| R25 | Bulkowski 確認價位定義 | 突破價 = 價格收盤**越過向下傾斜頸線**；若頸線**向上傾斜**，則以**右 armpit（N2.high）**為確認價，不是頸線延伸值 | — | https://www.thepatternsite.com/hsb.html （Identification Guidelines 表「Confirmation」列；注意該站對伺服器要求 User-Agent，meta 有 `noai`，只引用事實） | 書/網站 | 向上斜頸線時，延伸線值高於 N2，條件過嚴＋ 確認晚；定義不同會讓突破日期差很多 | 無（現有一律用延伸頸線） |
| R26 | Bulkowski：左肩谷高於右肩谷較差 | 偏好 `LS.low ≤ RS.low`（右肩較高）；`LS.low > RS.low` 的後突破表現較差 | 無定量門檻 | 同上，Trading Tips 表「Shoulder」列 | 書/網站 | 不對稱的肩（左肩抬高）→ 動能較弱 | 部分（balance rule 管的是 LS 與 RS+N2 中點的關係） |
| R27 | Bulkowski：量能 | 量最高在 LS 或 H，右肩縮量；「65% 的案例成交量趨勢向下」 | 無硬門檻 | 同上，Identification Guidelines「Volume」列 | 書/網站 | 右肩放量（賣壓仍在）→ 反彈力量弱 | 無（現有只有突破量比 `min_volume_ratio` 1.2） |
| R28 | Bulkowski：前置趨勢長度 | 「短期（0–3 個月）的下跌領先型態」突破後表現最好；前置下跌很小→預期漲幅也小 | 0–3 個月 | 同上 Trading Tips「Trends」「Price reversal」列 | 書/網站 | 長期緩慢下跌後的底部 | 部分（R5） |
| R29 | Bulkowski：避免多重谷 | 進場前避免「multi-peak」型態：型態前出現過多反覆的高低點 | 無定量門檻 | 同上 Trading Lessons slide 1 | 書/網站 | LS..RS 之間有多餘的 swing low（複雜底） | 不確定現有是否已排除（H 是 LS..RS 最低，但若允許非連續 swing low 取 LS/H/RS，中間可有其他 swing low），見第 4 節 |
| R30 | Bulkowski：長大突破 K 棒警示 | 突破日 K 棒範圍異常大（tall one-day breakout）後常回測；以 `high−low ≥ k·ATR` 定義（k 為本報告建議；Bulkowski 無數值） | 無數值 | 同上 Trading Lessons slide 4 | 書/網站 | 突破日過度延伸、隔日回落 | 無 |

### E. 其他來源（TradingView、二手資料、未取得原始碼）

| # | 規則名稱 | 內容 | 來源 | 授權 | 備註 |
|---|---|---|---|---|---|
| R31 | ATR 化的型態尺寸/對稱容差（TradingView「Adaptive Head and Shoulders Detector」） | 說明頁寫：頭須比兩肩低至少 X·ATR（"Min. head deviation"）；兩肩差 ≤ Y·ATR（"Shoulder symmetry tolerance"）；LS→H 與 H→RS 的時間比有上限；前置下跌衝擊 ≥ Z·ATR（"Min. trend impulse"）；突破用**收盤**確認、逾時未突破則作廢（"Max. wait time for breakout"）。**頁面無 Pine 原始碼可讀，預設值未取得** | https://id.tradingview.com/script/Gkrogzkp/ （Uncle_the_shooter） | TradingView House Rules，**只參考概念** | 只有概念層級：全部用 ATR 當單位。與 R1、R2 的 ATR 版同一思路 |
| R32 | 「Structured Head and Shoulder Pattern (MastersinMarkets)」 | 以 5 個 swing 點；最小型態尺寸隨 ATR 縮放；對稱度評分（價格對稱、時間對稱、頸線斜率）＞門檻（如 60）才畫；允許 Max Slope 限制。**無數值、無原始碼** | https://in.tradingview.com/script/xEMi8od2-Structured-Head-and-Shoulder-Pattern-MastersinMarkets/ | House Rules，只參考概念 | 與 SignalSync 評分架構相同，無新規則 |
| R33 | 右肩深度（占價格 %）作為品質分數 | 自家資料：肩深（相對頸線峰值）`depth_RS = (N.high − RS.low)/N.high` 分桶勝率（MFE ≥3%、兩週視窗）：1.6–5.8% → 32.8%；5.8–7.2% → 44.9%；7.2–8.9% → 53.8%；8.9–11.1% → 59.8%；11.1–25.8% → 66.5%（n≈2,050/桶，樣本內，2025-02~12，美股 6,000+ 檔） | https://www.stockdataanalytics.com/blog/inverse-head-and-shoulders-pattern-67000-trade-analysis/ | 自家行銷文章，無程式碼；樣本內未控制其他變數 | 方向與 Savin R7（肩不能太淺）一致；可信度中低。同文：肩差越大勝率反而越高（0–3% → 45.7%；10–15% → 58.5%，部分被分數混淆），**即「肩差越嚴越好」未被支持**；頸線回測（throwback）→ 勝率 43% vs 乾淨突破 85%；「型態效率 = 頭深/√持續天數」3.0–5.0 桶勝率 65.1% |
| R34 | PKScreener | **沒有 IHS**：該 repo 僅有 `pkscreener/classes/CandlePatterns.py`（K 線型態），在樹中搜 head/shoulder 無結果 | https://github.com/pkjmesra/PKScreener （MIT） | MIT | 無可借 |
| R35 | zeta-zetra/chart_patterns | 頭相對兩肩比例 `head/shoulder_left ∈ [0.98, 1)` 與 `head/shoulder_right ∈ [0.98, 1)`（價格比，非 %）；頸線高點線性迴歸斜率 `abs(slope) ≤ 1e-4`（原始價格單位） | https://github.com/zeta-zetra/chart_patterns/blob/main/chart_patterns/inverse_head_and_shoulders.py （條件式在檔案後段，約 L85–97，行號未逐行核對） | **無授權（GitHub license = none）→ 只參考概念** | 兩條都是固定比例/絕對斜率，不可跨幣種；唯一概念亮點：「大 pivot 抓頭、小 pivot 抓肩與頸線點」雙層 pivot（已在 github-research.md 記錄） |
| R36 | scipy 峰顯著度（prominence） | `scipy.signal.peak_prominences` / `find_peaks(prominence=k·ATR)` 可作為「每個 swing low 要比周圍至少凸出 k 倍 ATR」的標準定義 | https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.peak_prominences.html | BSD（函式庫） | **我沒有實際開啟該頁**，依既有知識列入；實作前請核對文件 |

> 驗收點：不重複且本專案尚未實作的規則 ≥ 8 條 —— R1、R2（部分）、R3、R5 的 SMA 分支、R7、R11、R15、R20（頸線點緊容差）、R21、R23、R25、R27、R30、R33（共 14 條）。

---

## 2. 推薦順序（品質改善潛力 × 實作難度）

筆數減少為**直覺估計**，沒有跑過你的資料；要上線前請用 `scan.py` 對 5–10 個幣各 timeframe 做一次 on/off 比較再定預設。
所有「筆數減少」是相對於「已套用 D19 的現況」。

| 優先 | 規則 | 建議公式（本專案符號） | 副作用／筆數減少（直覺） | 預設 |
|---|---|---|---|---|
| 1 | **R1 頭深 ATR 化**（與現有 % 門檻並用、取較嚴者） | `H.low ≤ min(LS.low, RS.low) − 1.0·ATR[H.idx]`（ATR 用型態 H 附近 14 根，已含 LS 前資料以避免 look-ahead） | −5% ~ −15%。主要砍掉低波動時段（4h 盤整）的「微頭」；高波動幣種不受影響。風險：新上市幣 ATR 不穩（前 14 根部分均值，SignalSync 用 `min_periods=1`），建議 bar < 30 時略過本規則。 | **開**，k=1.0（可先 0.75 再調） |
| 2 | **R11 型態起點／LS 前高於頸線延伸**（neurotrader888，MIT） | 以 N1、N2 求頸線，向左延伸到 LS 之前最多 `N2.idx−N1.idx` 根，要求某根 `high` 超過延伸頸線 | −10% ~ −25%（砍掉「盤整底部、LS 之前根本沒有從頸線高度跌下來」的型態）。副作用：LS 在資料開頭（回看不足）的型態會被誤殺，需在資料不足時略過規則；與選配 `require_prior_downtrend` 有部分重疊，可取代之。 | **開**（它是形狀幾何條件，不是績效假設） |
| 3 | **R3 頸線總傾斜**（SignalSync，MIT；與角度並用） | `abs(slope·W)/close[H.idx] ≤ 0.15`（slope 單位：價格／bar；換成頸線 N1→N2 的斜率，W = RS.idx−LS.idx） | −5% ~ −15%（主要影響 1d/1w 的寬型態與斜率大的 4h）。補強現有 ±30° 對寬型態過鬆的缺陷。 | **開**，0.15 |
| 4 | **R2 肩差以頭高為分母**（SignalSync 0.30；建議本專案先用較鬆的 0.5 並與現有 % 同時成立） | `abs(LS.low − RS.low) ≤ 0.5·head_height`，`head_height = neck(H.idx) − H.low` | −5% ~ −20%（影響「頭很淺但肩差在 10% 內」的型態；對深頭型態幾乎不影響）。注意 Haase 資料顯示肩差大不等於差，不要設太緊（SignalSync 0.30 為日線美股，不是 4h 加密）。 | **開**，0.5；0.30 先放選配 |
| 5 | **R21 肩深／頭深比例帶（Savin R6/R7）** | `ratio = mean(N1.high−LS.low, N2.high−RS.low)/head_height`；先只用下限 `ratio ≥ 0.25`（肩不能太淺，與 Haase 肩深資料同方向），上限 0.7 待驗證不等號方向後再開 | 下限：−5% ~ −15%；上限（0.7）：−10% ~ −30%（會砍掉肩與頭幾乎一樣深的「三底」）。不等號方向是推定，**先只開下限**。 | 下限**開**（0.25）；上限**關**（選配 0.7） |
| 6 | **R7 追高上限 MAX_RUNAWAY** | 最新 close `> neck(first_break)·(1+0.05)` 時標記 `stale`（4h 可改 3%、1w 10%，因波動不同；建議用 ATR 倍數：`close − neck(first_break) > 2·ATR`） | 只影響已突破清單：−10% ~ −30% 的已突破項目（D15 時間窗已擋掉不少）。屬「是否值得追」資訊，不改型態判定。 | **開**，但先只顯示警示標籤，不剔除 |
| 7 | **R9 最小報酬風險比** | `target = neck(H.idx 或 break) + head_height`；`stop = RS.low − 0.25·ATR[RS.idx]`；`RR = (target − entry)/(entry − stop) ≥ 1.0` | 約 −5% ~ −15%（頸線離 RS 很遠、頭淺的型態會被擋）。SignalSync 回測：1.0 門檻與無門檻的期望值差不多，僅排除「目標低於一個風險單位」的明顯不划算者。 | 顯示 RR；**篩選預設關**，選配 1.0 |
| 8 | **R25 向上傾斜頸線的確認價定義**（Bulkowski） | 頸線斜率 > 0 時，突破價 = N2.high；否則 = 頸線延伸值 | 不是過濾，是**定義改變**：向上斜頸線的突破時間會提前，「已突破」數量增加、突破年齡計算受影響，與 D13/D14/D15 需一併回測。 | **關**（選配 `confirm_level="bulkowski"`），避免打破現有回測基準 |

另列「做成顯示/計分、不當過濾」的規則（證據弱或會誤殺）：
- R4 兩側 ±40% 對稱：SignalSync 自己的消融實驗顯示會砍掉最好的訊號 → **不推薦當過濾**，最多當計分。
- R27 右肩縮量：Bulkowski 說只有 65% 的案例成立；Haase 分桶非單調；→ 計分用 `volume_trend = mean(vol[RS 區間]) / mean(vol[LS 區間])`，不要硬過濾。
- R14 pattern_r2：作者自己說無效且延遲 → 不採。
- R12 early_find：增加 look-ahead 風險，與 D13 衝突 → 不採。
- R20（Lo 的 1.5%）與 R15（頸線兩端高度差 ≤ 1×中位數 K 棒範圍）：太嚴，4h 加密幣會把幾乎所有型態砍光（估 −50% 以上）；Lo 1.5% 對應股票平滑後的極值，不是 raw pivot。可改成 ATR 版 `abs(N1.high − N2.high) ≤ 2·ATR` 當「頸線近水平」加分項，不過濾。

---

## 3. 目標價與止損參考（只當顯示資訊，不是交易建議）

| 項目 | 公式 | 來源 |
|---|---|---|
| 量測目標價（measured move） | `target = breakout_price + head_height`，`head_height = neck(H.idx) − H.low`（頸線在頭 bar 的值減頭低點；斜頸線用此值才正確） | neurotrader888 `head_shoulders.py` L271、L368；SignalSync scan.py L1457、L1504（`IHS_TARGET_AT_HEAD=True`）；cat.md L78 |
| 目標價（舊版變體） | `target = entry + (neck(break) − H.low)`（頸線在突破 bar 的值），平坦頸線時與上式相同 | SignalSync legacy profile，catalog L78 |
| Bulkowski 保守目標 | 量測高度 × 「達標比例」（站上標示 71%，3,197 筆 perfect trades）再加在突破價；即 `breakout + 0.71·head_height` 當保守參考。Bulkowski 原文寫 "multiply it by the percentage meeting price target" | https://www.thepatternsite.com/hsb.html （Trading Tips「Measure rule」） |
| Bulkowski 整體統計 | 失敗率（break-even failure）11%、平均漲幅 45%、throwback 65%、達標 71%（牛市、3,197 筆）；站上同頁說明 throwback 會拖累後續表現 | 同上，Important Bull Market Results |
| 止損（A） | `stop = RS.low`（頸線突破失敗＝回到右肩下方） | neurotrader888 L365 |
| 止損（B，加緩衝） | `stop = RS.low − 0.25·ATR[RS.idx]` | SignalSync scan.py L1493 |
| 風險 % | `risk = (entry − stop)/entry`，> 15% 不報 | SignalSync scan.py L193–199 |
| 追價上限 | `max_buy = min(neck·1.05, 風險達計畫風險 1.5 倍的價位)` | SignalSync scan.py L77、L206 區段、L307 |
| 報酬風險比 | `RR = (target − entry)/(entry − stop)` | SignalSync `reward_risk`（L340 `_reward_ok`） |

---

## 4. 無法確認的地方

1. **Savin R6/R7 的不等號方向**：PDF 轉文字時 `≤/≥` 遺失，只剩 0.7 與 0.25 兩個數字。我依文字說明（「排除肩高占頭高比例過大或過小的情形」）推定 R6 = `≤ 0.7`、R7 = `≥ 0.25`；這是 HS（頂）版本，IHS 是鏡像，我已改寫為肩深/頭深。需人工開原 PDF 核對（`HSrevision_paw_10 19 06.pdf` §B）。
2. **Lo, Mamaysky & Wang (2000) 原文沒有讀到**。E1..E5 的 R1–R5（1.5% 容差）是從 Savin et al. 的重述讀到的，內容與你給的描述一致；但 Lo 原文 IHS 的 E1..E5 鏡像定義、核回歸（kernel regression）平滑細節、頁碼我沒有直接核對。DOI：10.1111/0022-1082.00265。
3. **Bulkowski《Encyclopedia of Chart Patterns》書本頁碼無法取得**；我讀的是作者官網 thepatternsite.com 的 hsb.html（取得日 2026-10-18，頁面顯示 "As of 10/16/2026"）。該站的統計隨時更新，搜尋結果中另有 2025-07 的舊版數字（throwback 45% vs 現在 65%），所以數字要標日期。官網 meta 含 `noai`，我只引用事實性統計與公式，沒有搬運文字。
4. **TradingView 兩支腳本（R31、R32）沒有 Pine 原始碼**：只拿到說明頁的文字，沒有預設值與精確公式。TradingView 的 House Rules 亦限制重發佈，故只作概念。LonesomeTheBlue、Trendoscope 的 Auto Chart Patterns 我**沒有找到可讀的 IHS 規則來源**（搜尋只回傳上述兩支），故不列。
5. **Haase／StockDataAnalytics（R33）**：自家行銷文章，樣本內、MFE 3% 當「勝」、未控制其他變數（作者自己承認），且 Medium 原文被 Cloudflare 擋、我是讀 stockdataanalytics.com 的完整版；只當方向參考。
6. **SignalSync 只有 1 star、2026-09 才建立**，回測為作者自述。其標的是美股日線，結論不能直接套到加密貨幣 4h/1w。
7. **ATR 在本專案的算法**：SignalSync 用簡單滾動平均（前 13 根為部分均值）；你的專案 ATR 現行算法／是否已有 `atr` 函式我沒有讀 `ihs_scanner` 其他檔案，R1/R6 實作前請確認，並避免 look-ahead（只用 H 之前或當下的 ATR）。
8. **現有規則是否已涵蓋「跌破 RS.low 即失效」與「LS..RS 之間無其他更低 swing low／多重谷」**：我只讀了 `config.py` 與 charter，沒有讀偵測主程式，所以 R8 的地板條件與 R29 標為「不確定」。
9. **scipy peak_prominences（R36）未實際開啟文件頁**。
10. **PKScreener**：確認 repo 內只有 K 線型態檔；若在其他 repo 或分支（如私有）有 IHS，未涵蓋。
11. 所有「筆數減少」估計純屬直覺，未以你的資料驗證。
