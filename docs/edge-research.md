# 提高期望值／贏過大盤的方法調研（edge-research）

日期：2026-10-23。目的：延續 D40–D43（型態對隨機進場只有小幅優勢、台股所有組合輸 0050），找「有可靠證據、可用 OHLCV＋公開資料驗證」的方法。

**資料來源與可信度說明**：本調研用網路搜尋取得摘要與二手整理，多數論文**只讀到摘要或可靠二手摘要，未讀全文**；
下表中「效果大小」凡標「二手」者，引用前須回原文核對。沒有讀到全文的數字一律不寫成定論。未註冊任何帳號、未填任何個資。

## 0. 先講結論

1. 學術上**最硬**的證據是「動能／趨勢」（橫斷面動能、時間序列動能、52 週新高）與「波動度管理」（Moreira-Muir、Barroso–Santa-Clara），
   但效果多半出現在「風險調整後／降低回撤」，而不是「單筆勝率或期望值更高」。
2. **台股是動能文獻的壞例子**：多篇研究指出台股傳統價格動能不明顯，且在市場狀態轉換時反轉（見 §2）。因此直接把美股動能搬到台股，預期不要太高。
3. 加密貨幣的趨勢／動能證據較強（Liu-Tsyvinski-Wu；Zarattini 等的 Donchian 集成），但這些多是 SSRN 工作論文或單一研究團隊結果，加上倖存者偏差風險，要自己用無倖存者偏差的資料重驗。
4. 我們過去的結果（移動停損拉高 RR 但不贏大盤）與文獻一致：**「進場型態」不是主要 alpha 來源；趨勢濾網、部位大小（波動度）、與大盤擇時才是**。
5. 沒有找到任何對 Minervini 趨勢模板／VCP、IBD RS rating 的獨立同行審查驗證（見 §2.1 的說明）。

## 1. 證據表

圖例：強度＝我對證據的主觀評級（強／中／弱），依據是來源層級、樣本長度、是否被獨立複製。難度＝在現有回測框架（`backtest/`，OHLCV，0050／BTC 基準）上的實作難度。

| # | 方法 | 來源（連結） | 市場與期間 | 效果大小（出處說明） | 台股／加密證據 | 資料需求 | 難度 | 與掃描器結合 | 強度 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 橫斷面價格動能（買贏家／賣輸家） | Jegadeesh & Titman 1993 [SSRN/JF](https://floridapsc.com/library/filings/2013/07457-2013/Support/Gulf's%20response%20to%20OPC's%201st%20POD,%20No.%2057/Jegadeesh%20and%20Titman%201993%20(140-167).pdf)；整理 [Wharton](https://knowledge.wharton.upenn.edu/article/recognizing-an-investing-signal-that-defied-wisdom-and-endured/) | 美股 1965–1989 | 摘要：3–12 個月持有期有顯著正報酬，部分在之後兩年消退；二手整理約 12% 年化（6 個月持有）——**確切數字未回原文核對** | 台股：不明顯（見 #1b） | 日收盤 | 低 | 以 RS 排名當訊號濾網（只做近 6–12 月強勢股的型態） | 美股強／台股弱 |
| 1b | 台股動能 | [Du, Huang, Liao 2009 J. Econ. & Bus.](https://ideas.repec.org/a/eee/jebusi/v61y2009i2p140-152.html)；[Lin et al. 2016 Pacific-Basin Fin. J.](https://ideas.repec.org/a/eee/pacfin/v38y2016icp59-75.html)；[Why is there no momentum in Taiwan](https://experts.nau.edu/en/publications/why-is-there-no-momentum-in-the-taiwan-stock-market/) | 台股（各研究期間不同，未逐一核對） | 摘要：台股下跌市場較常見，下跌後動能報酬更負；市場狀態不變時動能顯著為正，狀態轉換時反轉 | 台股：**動能弱且依市場狀態而定** | 日收盤＋大盤狀態 | 低 | 動能濾網必須搭配大盤趨勢狀態（如 0050 > 200 日線） | 中（機制）／弱（可獲利性） |
| 2 | 52 週新高（nearness to 52-week high） | George & Hwang 2004 [JF 59(5)](https://www.cxoadvisory.com/1284/technical-trading/the-52-week-high-as-a-momentum-indicator-for-individual-stocks/)（只讀摘要）；[新興市場指數不獲利：Malin & Bornholt 見上頁二手整理] | 美股；摘要稱預測力優於過去報酬且長期不反轉 | 摘要層級，無數字 | 台股有一篇 IJBESAR 研究（連結 [PDF](https://ijbesar.ihu.gr/docs/volume16_issue1/16_01_07.pdf)，**憑證過期讀不到**，未核對） | 日收盤，無額外資料 | 低 | **與頭肩底天然互補**：頭肩底多在低檔，52 週新高在高檔；可作為「突破型態」的分類（頸線突破同時接近 52 週高）。D42 的「前置高點在 LS 前 12 個月以上較好」其實在測相反方向 | 中 |
| 3 | 時間序列動能／趨勢跟隨 | Moskowitz, Ooi, Pedersen 2012 [JFE 104(2)](https://papers.ssrn.com/abstract=2089463)；[Hurst, Ooi, Pedersen 2017 JPM 百年證據](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing?aqrPDF=1) | 58 種期貨；67 資產、1880–2016 | 摘要：1–12 個月顯著、分散組合異常報酬；百年版二手摘要：扣 2+20 費用後平均夏普約 0.4（[CXO 二手](https://www.cxoadvisory.com/momentum-investing/trend-following-with-intrinsic-momentum-over-the-very-long-run)，未核對） | 期貨為主，**非台股個股**；批評：[Quantpedia 整理 Kim-Tse-Wald](https://quantpedia.com/deconstructing-the-time-series-momentum-strategy/) 稱 alpha 大部分來自波動度縮放，2009–2013 優勢消失 | 日收盤 | 低 | 當「大盤／個股趨勢濾網」 | 強（跨資產）／台股個股弱 |
| 4 | 10 個月均線擇時 | Faber 2007 JWM（[二手討論](https://ritholtz.com/2012/01/revisiting-quant-approach-to-tactical-asset-allocation)，**未找到 SSRN 原文頁**） | 多資產類別，二手稱 1901 起 | 二手：降低波動與回撤、報酬與買進持有相近；**不是提高報酬** | 無台股專屬證據（未找到） | 月收盤 | 很低 | 用於 0050 / BTC 的「大盤開關」，只在多頭才放行訊號 | 中 |
| 5 | 加密動能／趨勢 | Liu, Tsyvinski, Wu 2022 [JF（SSRN）](https://papers.ssrn.com/abstract=3379131)；Zarattini, Pagani, Barbon 2025 [Catching Crypto Trends（SFI 25-80）](https://abarbon.com/papers/catching-crypto-trends) | 加密 2014/2015 起 | LTW：市場、規模、動能三因子解釋橫斷面報酬。Zarattini：Donchian 集成＋波動度部位，前 20 大流動幣輪動，扣費夏普 >1.5、對 BTC 年化 alpha 10.8%（**作者自報、工作論文，第三方 [QuantifiedStrategies](https://www.quantifiedstrategies.com/?p=1192563) 稱未驗證**） | 加密：有（但單一團隊、倖存者偏差待驗） | 日 K，需含下市幣才能驗證 | 中 | 對 BTC 做 Donchian 集成當開關；與我們 D43 加密 ATR×2 結果方向一致但同樣有倖存者疑慮 | 中 |
| 6 | 波動度管理組合 | Moreira & Muir 2017 [JF 72(4)](https://www.nber.org/papers/w22208)；Barroso & Santa-Clara 2015 [JFE](https://ciencia.ucp.pt/en/publications/momentum-has-its-moments/) | 美股因子，1926 起 | MM：大 alpha、夏普提升（二手稱 50%–100% 倍數，NBER 頁面無數字，**未核對**）。BSC 二手（[CXO](https://www.cxoadvisory.com/momentum-investing/avoiding-momentum-strategy-crashes/)）：動能夏普幾乎加倍、崩盤幾乎消失；126 日波動估計 | 台股／加密無直接證據（未找到）；但原理（波動度比預期報酬變動更大）是通則 | 日報酬 | 低 | **直接改我們的部位大小**：每筆風險按近期波動縮放、整體曝險隨大盤波動下調 | 強（美股）／台股待驗 |
| 7 | 固定風險、Kelly 分數 | 無一手實證可引（見 §4 無法確認）；Kelly 為數學結論，實務用 1/4–1/2 Kelly | — | — | — | 逐筆 R 分佈 | 低 | 用 D43 的逐筆 R 分佈估算；因期望值接近 0，Kelly 比例也近 0 | 理論 |
| 8 | 台股三大法人買賣超 | [NTU 論文：外資買賣超（Jegadeesh-Titman 法）](https://tdr.lib.ntu.edu.tw/handle/123456789/74582)（只讀摘要） | 台股，期間未核對 | 摘要：外資買賣超可賺超額報酬，**僅在小型股有效**，多空市況皆正 | 台股：有但僅小型股；**三大法人逐一檢驗的學術文獻未找到** | TWSE OpenAPI 免註冊（見 §3） | 中 | 型態訊號 ＋ 外資連買 N 日當濾網 | 弱–中 |
| 9 | 台股月營收（營收動能） | [NTU：標準化未預期營收多空](https://tdr.lib.ntu.edu.tw/handle/123456789/80182)；[NCU：MoM＋YoY 雙因子 2013–2024](https://etd.lib.ncu.edu.tw/detail/2a61234058e39839846030de3c0973e6)；[vocus 整理](https://vocus.cc/article/635e31e9fd89780001ca8e44) | 台股 | NTU 摘要：超額報酬不被 Q 因子／五因子完全解釋，但**近年正報酬期間縮短**；數字未讀 | 台股：有（碩論層級），另有新興板零結果 | 月營收（TWSE/MOPS 免費；對齊公告日避免前視） | 中 | 型態訊號 ＋ 營收 YoY 創高濾網 | 弱–中 |
| 10 | 多重檢定門檻 | Harvey, Liu, Zhu 2016 [RFS 29(1)](https://papers.ssrn.com/abstract=2513152) | — | 新因子 t 值應 >3.0；[Chen 2021 反駁](https://business.unl.edu/academic-programs/departments/finance/about/seminar-series/documents/AndrewChen2.pdf)認為多數發現為真 | — | — | — | 驗證紀律 | 方法論 |
| 11 | Deflated Sharpe Ratio | Bailey & López de Prado 2014 [JPM 40(5)](https://papers.ssrn.com/abstract=2460551) | — | 校正選擇偏誤（多重測試）與非常態 | — | 試驗次數 N、報酬序列 | 低 | 我們 D43 測了 38 種出場，直接適用 | 方法論 |

### 對 Minervini／IBD RS 的說明
搜尋**沒有找到**同行審查或可重現的獨立回測驗證 Minervini 趨勢模板／VCP，只有交易論壇貼文（[TradingQnA](https://tradingqna.com/t/a-25-year-empirical-study-on-minervinis-volatility-contraction-pattern-vcp-what-2-35m-data-points-reveal-about-market-crashes/198732)，匿名、無方法、無程式碼，**視為不可信**）。
這些規則在概念上是「RS 動能＋趨勢濾網＋收斂型態」，可拆成上表 #1、#2、#3 各自驗證。IBD RS rating／CAN SLIM 本次未找到可讀的一手驗證，不下結論。

## 2. 開源專案與台股／加密資料來源

（star／授權／最後更新為 2026-10-23 以 GitHub API 查詢）

| 專案 | 用途 | 規模與授權 | 可信度評估 |
|---|---|---|---|
| [microsoft/qlib](https://github.com/microsoft/qlib) | AI 量化研究平台、因子與回測 | 49.6k star，MIT，2026-10 仍在更新 | 框架可靠，但**不附台股策略績效保證**；預設資料為美股／陸股，台股需自備資料；ML 模型易過擬合，須自做樣本外 |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 向量化回測、大量參數掃描 | 9.5k star，授權 NOASSERTION（Apache 加 Commons Clause，**使用前讀授權**） | 適合參數掃描；注意掃描量越大越需要 DSR 校正 |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | 輕量事件回測 | 9.1k star，**AGPL-3.0**（不可複製進專案，僅可參考） | 簡單易懂；成本模型簡化 |
| [FinMind/FinMind](https://github.com/FinMind/FinMind) | 台股資料 API＋社群 | 2.9k star，Apache-2.0，2026-10 更新 | 資料集含三大法人、融資融券、外資持股、月營收；**免註冊可呼叫但限 300 次/小時，註冊驗證信箱後 600**（官方 README／[PyPI](https://pypi.org/project/FinMind/)）；資料僅供教育非商業；2024 一篇部落格稱免登入拉全市場被拒（單一軼事）。**我們沒有註冊** |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 事件回測老牌 | 23.5k star，GPL-3.0，2024-08 後無更新 | 不維護；GPL 不可複製 |
| [stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading) | 書附程式（含因子評估、回測方法） | 21.5k star，MIT | 教學品質高，可參考防前視與樣本外流程 |
| FinLab（[PyPI](https://pypi.org/project/finlab)） | 台股回測＋資料 | 資料需登入 token（網頁稱免費額度 500MB/日，未核對）；是否開源未確認 | 公開文章多為行銷性質、績效數字未見樣本外與成本細節，**可信度低於學術文獻**；TEJ（[zipline-tej](https://pypi.org/project/zipline-tej/0.0.35)）為商業資料，需付費 |

公開資料端點：[TWSE OpenAPI](https://openapi.twse.com.tw/)（本次實測 `STOCK_DAY_ALL` 回 200、免金鑰）、[TPEx OpenAPI](https://www.tpex.org.tw/openapi/)（實測 200）。
三大法人／融資融券／月營收是否都有對應的**歷史**端點與可回溯長度，本次未逐一驗證（見 §6）。

## 3. 推薦清單（證據強度 × 現有資料可快速驗證）

共同規則（沿用 D40–D43 框架）：已收盤 K、as-of t 只用 `df[:t+1]`、台股含手續費證交稅、加密 0.1%/邊、基準為還原權息 0050／BTC 持有、期間以 **2016–2021 訓練／選參數、2022–2026 凍結參數驗證**（單次，不回頭調）。
登記試驗次數 N，所有績效報 DSR（§5）。

### 推薦 1：大盤趨勢開關（Faber 10 月均線／200 日線）＋既有訊號
- 證據：#3、#4，跨資產、百年尺度；效果是降回撤而非提高報酬。D42 的「0050 均線濾網」在單筆層級無效，但**單筆勝率不是這類方法的作用點，應看資金曲線**，與先前測法不同。
- 規則：A 組＝D43 資金曲線（頭肩底 A 日週、ATR×3 收盤移動停損、每筆風險 1%、最多 10 倉）；B 組＝同上但僅在 0050 月收盤 > 10 月均線（再測 200 日線）時允許新進場，轉空時**不**強平，僅停止新倉；C 組＝0050 本身用同開關（對照，看開關本身價值）。加密同理用 BTC。
- 參數：只允許 10 月／200 日兩種（N=2 以免挖掘）。
- 比較：年化、最大回撤、夏普、Calmar，對 0050 持有與 C 組；B 相對 A 的差異用逐年配對 bootstrap。
- 預期失敗條件：若 B 仍輸 0050 持有（很可能，因為 0050 本身在多頭），結論即「型態策略無法以擇時補救」。

### 推薦 2：波動度目標部位（Moreira-Muir／Barroso–Santa-Clara 風格）
- 證據：#6，因子層級美股強；台股無直接證據，這正是要驗證的點。
- 規則：每筆風險 r_t = 1% × (σ_target / σ_hat_t)，σ_hat 為 0050（或策略日報酬）近 126 日已實現波動，r 上下限 0.25%–1.5%；σ_target 取訓練期策略報酬波動中位數（不用未來資訊）；其餘同 D43。
- 比較：固定 1% 版本、0050 持有、0050 自身波動度目標版（對照）。看夏普與回撤是否改善，而不是年化絕對值。
- 樣本外：σ_target 在 2016–2021 定，2022–2026 凍結。
- 補充：同時算 Kelly：用訓練期逐筆 R 估算 f*，看是否 ≈ 0，作為「別加槓桿」的佐證。

### 推薦 3：相對強度（RS）排序＋52 週新高鄰近度當型態濾網／同日排序
- 證據：#1、#2、#1b；台股動能偏弱所以是「可能失敗」的檢驗，價值在於同時解決 D43 發現的「同日排序影響很大（可差 15 個百分點）」問題——目前排序是隨機，改成有理論依據的固定排序。
- 規則：訊號日計算 (a) 12–1 月報酬百分位（排除最近 1 個月）、(b) 收盤/52 週高。四組：不分、(a) 前 1/3、(b) ≥0.8、(a)∩(b)。同日多訊號時依 (a) 或 (b) 排序進場（取代隨機排序）。
- 比較：同標的隨機進場（D42 做法）、各組合 vs 0050；報逐筆超額與資金曲線；t 值以標的與月份聚類。
- 預期：台股可能無效或僅在多頭狀態有效（Lin et al. 2016）；因此另切「0050 在 200 日線上／下」兩個狀態各報一次。
- 不依賴新資料，兩小時內可跑。

### 推薦 4：加密 Donchian 集成開關（含無倖存者偏差資料的取得）
- 證據：#5（Zarattini 等），但為自報工作論文。
- 規則：對 BTC：多個 Donchian 週期（例 20/50/100 日）的突破訊號平均成 0–1 曝險，波動度縮放；對照 BTC 持有。對山寨幣：先**只在現有宇宙測**並明確標「倖存者偏差、不可信」。
- 比較：BTC 持有、頭肩底 A ATR×2 收盤（D43）、Donchian 集成；2018–2021 選、2022–2026 驗。
- 這項最誠實的產出是「BTC 層級的開關是否有價值」，山寨幣層級待取得含下市幣資料（見 §6）。

### 推薦 5（資料外加，驗證成本較高）：外資買賣超或月營收 YoY 濾網
- 證據：#8、#9，僅碩論／摘要層級，且「小型股才有效」「近年期間縮短」。
- 規則：先從 TWSE／TPEx OpenAPI 取得歷史（若可回溯長度不足 5 年，直接放棄這項）；濾網為訊號日前 5 日外資累計買超 > 0，或月營收 YoY 創 12 個月新高（以**公告日隔日**才可用，避免前視）。
- 比較：同標的隨機進場、同期未濾網訊號；分市值五分位報告，因為文獻指向小型股。
- 注意：小型股成本與漲跌停、流動性在我們的回測中沒有建模，結果會偏樂觀，須加大滑價敏感度（例如單邊 0.3%/0.5%）。

## 4. 誠實的預期

- **扣成本後仍有效的機率較高**：趨勢開關與波動度目標（本質是降低曝險，換手低，成本小）。但它們的好處多是**回撤與夏普**，年化報酬未必勝過 0050 持有；0050 在 2016–2026 的強勢多頭會讓任何擇時看起來吃虧。
- **可能被套利掉或期間特定**：橫斷面動能（發表後多篇文獻顯示美股減弱，Quantpedia 整理的 TSMOM 批評同向；本次未逐篇核對）、台股動能（本來就弱）、營收動能（NTU 摘要明說近年正報酬期間縮短）。
- **加密**：趨勢證據看似好，但 2018–2022 的大行情與倖存者偏差使任何回測都偏樂觀；D43 已有同樣警訊。
- **整體**：預期結果是「某些做法能改善風險調整後指標，但仍難以在牛市年份贏過 0050 持有」，與 D42/D43 一致。若要贏大盤，較可能靠「大盤的槓桿／擇時」而非型態選股；這屬於使用者決策，且涉及實際資金，需另行討論。
- 型態掃描器的合理定位仍是「候選清單與風險定義工具」（停損、部位大小），不是 alpha 來源。

## 5. 防過擬合的做法（給後續驗證用）

1. **預先登記**：每個實驗先寫下規則、參數集、N（試驗總數）、通過標準，再跑。寫進 charter 決策記錄。
2. **樣本切分**：時間順序切成訓練／驗證；驗證期參數凍結、只跑一次。若要滾動，用 walk-forward（固定視窗向前滾，只報合併後的樣本外段）。
3. **多重比較**：
   - Deflated Sharpe Ratio（[Bailey & López de Prado 2014](https://papers.ssrn.com/abstract=2460551)）：輸入試驗次數、夏普、偏態峰態、樣本長度，得到「在 N 次試驗下仍顯著」的機率。D43 的 38 種出場可作為實例。
   - t 值門檻 >3.0（[Harvey-Liu-Zhu 2016](https://papers.ssrn.com/abstract=2513152)）；但 [Chen 2021](https://business.unl.edu/academic-programs/departments/finance/about/seminar-series/documents/AndrewChen2.pdf) 認為此門檻可能過嚴，取其為保守參考。
4. **穩健性**：剔除最好 N 筆、不同同日排序（D43 已做）、成本加倍、參數鄰域平滑度（最佳參數周圍若表現崩落即過擬合）。
5. **交易不獨立**：t 檢定以標的／月份聚類，或用區塊 bootstrap。
6. **前視與倖存者偏差**：月營收／財報用公告日；宇宙用「當時」成分；加密補含下市幣。
7. 與已有的「截斷測試鎖住 as-of」並用。

## 6. 無法確認的地方

- 所有論文效果大小（JT 約 12%、MM 夏普 +50–100%、Hurst 夏普 0.4、Zarattini 夏普 >1.5／alpha 10.8%）**皆為摘要或二手整理**，未讀全文表格；引用前須核對。
- Faber 2007 原文未找到（SSRN 頁面未出現），只有二手描述。
- George & Hwang 台股研究（IJBESAR）因憑證過期未讀取；台股 52 週新高的證據等於**未確認**。
- 台股三大法人（外資、投信、自營商）逐一檢驗報酬預測力的學術文獻未找到；融資融券與外資持股比例的研究本次未搜尋到可讀來源。
- 營收動能：僅有碩論與摘要，各自期間與數字未核對，且互有矛盾（含一篇零結果）。
- 波動度管理在台股／加密的直接證據未找到。Kelly 分數沒有引用一手實證，只作為數學工具。
- TWSE／TPEx OpenAPI 是否提供三大法人、融資融券、月營收的**足夠長歷史**未驗證（只確認端點可連、免金鑰）；歷史資料可能需要用 FinMind（需註冊提高額度）或 TWSE 網頁歷史下載。需要使用者決定是否註冊。
- FinLab 的開源範圍與免費額度、公開策略績效未取得可查證數字。
- IBD RS rating／CAN SLIM 一手驗證未找到。
- 加密含下市幣的歷史資料來源尚未確認（幣安公開端點只有現存交易對）。
