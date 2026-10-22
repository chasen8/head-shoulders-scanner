# 型態勝率文獻與開源調研

日期：2026-10-22。方法：WebSearch／WebFetch。每個數字標明是「讀到原頁」還是「二手摘要」。
本檔是調研，不是規格；不改專案程式。

## 0. 一句話結論

沒有任何來源能證明「某個型態的停利先於停損勝率最高」。最接近「排名」的是 Bulkowski，但他的指標是
「突破後有沒有漲超過 5%」，和停利停損勝率是不同的東西。學術上最嚴格的檢驗（Osler、Savin 等）對單一型態當獨立策略多為「不賺錢」。

## 1. 各型態比較表

| 型態 | 來源（連結） | 市場與期間 | 樣本數 | 指標 | 定義 | 可信度 |
|---|---|---|---|---|---|---|
| 頭肩底（標準） | [Bulkowski hsb](https://www.thepatternsite.com/hsb.html)（原頁） | 美股，Bulkowski 約 30 年資料，多頭市場向上突破 | 3,197 | 平均漲幅 45%；失敗率 11%；達目標價 71%；總表現排名 13/39；回測 65% | 失敗 = 突破後漲幅未超過 5% 就反轉；達目標 = 到頭部到頸線高度 | 中。樣本大，但是「完美」挑選過的交易，作者自算，無成本、無獨立驗證 |
| 複合頭肩底 | [Bulkowski chsb](https://www.thepatternsite.com/chsb.html)（原頁） | 同上 | 933 | 平均漲幅 47%；失敗率 7%；達目標 71%；表現排名 9/39 | 同上 | 同上 |
| 頭肩底 | [Investing.com 轉述 TechCharts](https://uk.investing.com/analysis/chart-patterns-ranked-by-accuracy-200622665)（原頁，但 TechCharts 原文未找到） | 未載明，2017–2021 | 未載明 | 73.0% | 「達到價格目標」 | 低。無樣本數、無方法、無原文；與 Bulkowski 的 71% 達目標接近但不能互證 |
| Big W、雙底（Eve&Eve、Adam&Eve 等）、三重底、杯柄、升三角、下降楔形、高緊旗 | [Bulkowski 排名頁](https://thepatternsite.com/id84.html)（原頁，只有排名無數值） | 美股多頭市場向上突破 | 未列 | 失敗排名（1 最好）：圓底 1、杯柄 2、複合頭肩底 3、Big W 5、頭肩底 10、Eve&Adam 12、Eve&Eve 13、三重底 16、升三角 27、對稱三角 36、下降楔形 37；表現排名：高緊旗 30（差） | 同上 | 中（同上）。注意：同一個型態「失敗排名」和「表現排名」可以差很多 |
| 高緊旗 | [Bulkowski htf](https://thepatternsite.com/htf.html)、[HTFStudy](https://thepatternsite.com/HTFStudy.html)（二手摘要，未直接讀原頁） | 美股 | 1,028 | 平均漲幅 39%；失敗率 15%；達目標 82%；另一研究頁：14% 無向上突破＋19% 漲不到 5% = 33% | 同上 | 中低。舊文常見的 69% 漲幅已被作者下修 |
| 頭肩底／頭肩頂、雙底雙頂、三角形、矩形、擴張等共 10 型 | [Lo, Mamaysky & Wang 2000](https://www.nber.org/papers/w7613) | 美股 1962–1996，核回歸自動辨識 | 數千檔股票 | 條件報酬分布與無條件分布「顯著不同」：NYSE/AMEX 10 型中 7 型、NASDAQ 10 型全部 | 檢定的是報酬分布差異，不是勝率 | 高（頂級期刊），但作者自己說不等於可獲利；無交易成本；NASDAQ 結果受流動性影響 |
| 頭肩 | [Osler 1998 (CXO 摘要)](https://www.cxoadvisory.com/technical-trading/classic-papers-returns-from-pattern-based-technical-analysis/)（二手） | 美股 100 檔隨機股，1962–1993 | 約每股每年 1 次 | 持有約 10 日平均報酬 -0.24%（未扣成本） | 演算法辨識頭肩 | 中高。結論：用型態交易平均虧損 |
| 頭肩 | [Savin, Weller & Zvingelis 2007](https://www.cxoadvisory.com/technical-trading/testing-the-head-and-shoulders-pattern/)（二手摘要；[原文草稿 PDF](https://www.biz.uiowa.edu/faculty/gsavin/papers/HSrevision_paw_10%2019%2006.pdf)） | 美股 S&P500＋Russell2000，1990–1999 | 每季數百個 | 作為獨立策略「可能不獲利」；Russell2000 風險調整後三個月落後市場約 5–7%/年；搭配對沖組合可加約 8%/年 | 做空獲利 vs 市場漂移 | 高。多頭市場期間做空型態被漂移吃掉，要注意是「頭肩頂」方向居多 |
| 頭肩 | [Chang & Osler 1999, Economic Journal](https://ideas.repec.org/a/ecj/econjl/v109y1999i458p636-61.html)（摘要） | 外匯日資料 1973–1994 | 未取得 | 頭肩規則有獲利（bootstrap 檢定），但輸給簡單規則（均線、濾網） | 利潤檢定對 10,000 條隨機漫步 | 中高，但是外匯非股票 |
| 蠟燭線 | [Caginalp & Laurent 1998](https://c.mql5.com/forextsd/forum/210/The%20Predictive%20Power%20of%20Price%20Patterns.pdf) | S&P500 成分股 1992–1996 | 全成分股 | 樣本外二日持有獲利近 1%，統計顯著 | 三日型態（三白兵等） | 中。只在這段期間；未扣成本 |
| 蠟燭線（28 種） | [Marshall, Young & Rose（Massey 論文庫）](https://mro.massey.ac.nz/items/edc6c1cf-63d9-4c94-a13d-92ffe1a53481/full)（二手摘要） | 美股 DJIA 成分股 1992–2002 | 未取得 | 無看多訊號能可靠預測正的異常報酬 | 十日持有 | 中高 |
| 蠟燭線 | [Lu, Shiu & Liu 2012 台股](https://ideas.repec.org/a/wly/revfec/v21y2012i2p63-68.html)、[Lu 2014 台股](https://ideas.repec.org/a/eee/pacfin/v26y2014icp65-78.html)（摘要） | 台灣 50 追蹤基金成分股 2002–2008；台股 1992–2009 | 未取得 | 3 個看多反轉型態獲利（2012）；4 個單線型態扣成本後獲利（2014） | bootstrap、樣本外檢定 | 中。這是少數台股證據，但是蠟燭線不是圖表型態 |
| 多空蠟燭型態，ES 期貨與 AAPL | [TradesViz 部落格](https://www.tradesviz.com/blog/candlestick-pattern-effectiveness-backtesting/)（原頁） | ES 15 分鐘 2025/5–10 等 | 21–98 筆 | 勝率 52%–83% | 平台自己的規則 | 低。樣本極小、廠商自家、退出規則不全 |

## 2. 「勝率」定義差異（不要直接比）

1. Bulkowski 失敗率：突破後價格沒有往突破方向走超過 5% 就反轉。這是「有沒有走出 5%」，不涉及停損。
   一個型態可以「沒失敗」（漲過 5%）但先掃到停損再漲。反過來也可能。
2. Bulkowski 達標率（到目標價）：只看最後有沒有到頸線高度，不管中途回檔多深；且是用「最終高點」（ultimate high），
   天然偏樂觀。
3. 我們的 D40 回測勝率：TP1 先於停損、含成本、同根碰兩者算停損。這是最嚴的，所以台股頭肩底 TP1 勝率 41.9% 與
   Bulkowski「失敗率 11%（等於 89% 漲過 5%）」並不矛盾：兩者問的是不同問題。
4. 學術的「顯著」：是條件報酬 vs 無條件報酬差異的統計檢定（如 Lo 等）或扣成本後平均報酬（如 Osler），
   不是勝率。有顯著可能勝率仍只有 52%。
5. 部落格或 TechCharts 的 73%：多半是「到達價格目標」，定義、樣本都未知。
6. Bulkowski 的樣本有「完美交易」篩選與倖存者偏差，且作者網站自己標示統計已舊（部分資料未更新）。

## 3. GitHub 專案

| 專案 | 授權 | 內容 | 是否公布勝率 | 評估 |
|---|---|---|---|---|
| [BennyThadikaran/stock-pattern](https://github.com/BennyThadikaran/stock-pattern) | GPL-3.0（不得複製程式碼） | 掃描 VCP、諧波、頭肩底、三角形、雙頂；附 backtest CLI | 無 | 只能當做法參考；方法在 wiki，未驗證 |
| [zeta-zetra/chart_patterns](https://github.com/zeta-zetra/chart_patterns) | 未標示（視同保留所有權利，不可複製） | 雙頂底、頭肩（含倒）、三角、旗形 | 無 | 只有偵測範例 |
| [GZ30eee/PatternPulse](https://github.com/GZ30eee/PatternPulse) | MIT | 頭肩、雙底、杯柄、對稱三角；前進式回測固定持有期（如 10 日），輸出勝率／Sharpe | 無數字（只說 LSTM 過濾常常贏不了基準） | 結構合理但沒公布結果、未提成本、README 有占位連結。可參考做法，數字無從引用 |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) 的蠟燭線範例 app | 見 repo（未驗證） | TA-Lib 蠟燭線偵測＋回測 | 未取得 | 搜尋結果間接提到，未直接讀 README |

結論：沒有找到「多型態比較並公布可信勝率」的開源專案。找到的都是偵測器，少數附回測腳本但不附結果。

## 4. 結論

- 多來源都排前面：Bulkowski 的失敗排名裡，複合頭肩底、杯柄、圓底、Big W、頭肩底都靠前（失敗率低），
  但「失敗」只代表漲過 5%。頭肩底在 Bulkowski 失敗率與表現排名皆非最頂（失敗 10、表現 13），並非冠軍。
  杯柄、圓底的失敗排名更好，但它們難以用純數學規則在多時框穩定辨識。
- 學術嚴格檢驗下不成立或很弱：頭肩單獨當交易策略（Osler 1998 負報酬；Savin 2007 多頭期不獲利）；
  蠟燭線在 DJIA（Marshall 等）無效。Chang & Osler 在外匯有利潤但輸給均線。
- 有統計意義但不等於可獲利：Lo 等 2000 的 10 型態報酬分布顯著；Caginalp & Laurent 的二日約 1%（未扣成本）。
- 加密貨幣：未找到對頭肩底／雙底的嚴謹檢定。唯一找到的是 [2025 Mt.Gox 逐筆交易研究](https://ideas.repec.org/a/spr/fininn/v11y2025i1d10.1186_s40854-025-00763-2.html)，
  研究的是使用型態的交易者而非型態本身報酬，不能推論勝率；以及 2004 [歐元兌美元盤中研究](https://research.dial.uclouvain.be/bitstreams/b4472704-16de-4786-8a0d-5a575d0b4e11/download)：
  逾半數型態有可預測性，但只有 2 種獲利顯著且不夠付成本。
- 台股：型態（頭肩、雙底）的嚴謹研究未找到；找到的是蠟燭線有獲利的 2012、2014 兩篇，以及一篇未發表的台指期雙底碩士論文（[NTU](https://tdr.lib.ntu.edu.tw/handle/123456789/17919?mode=full)，只讀到搜尋摘要）。
- 與我們 D40 的關係：我們的負中位數、少數大漲撐平均，與「型態單獨使用邊際薄」的學術結論方向一致。
  Bulkowski 的高排名不應被理解成我們也能拿到 70% 以上勝率。

## 5. 建議（若要新增型態）

依證據強度，且需能用純 OHLCV 數學規則定義：

1. 雙底（Big W／Eve&Eve 變體）：Bulkowski 失敗排名 5（Big W）與表現排名 5（Eve&Eve），Lo 等 2000 也涵蓋雙底。
   規則來源：Bulkowski 的 [Big W 與雙底頁](https://thepatternsite.com/id84.html) 連到各型態頁，含辨識規則；Lo 等的核回歸定義見 [NBER w7613](https://www.nber.org/papers/w7613)。
   與現有頭肩底共用極值與頸線邏輯，實作成本最低。
2. 矩形底（Rectangle bottom）：Bulkowski 上漲榜單前列（3 個月 +14%）；Lo 等有矩形。規則簡單（上下平行邊界）。
   來源同 Bulkowski 網站；注意其失敗排名只有 18，表現排名 8。
3. 下降楔形：Bulkowski 上漲榜有 +10%（2 個月），但失敗排名 37（差），所以只在加篩選時值得做。證據最弱。

不建議做：杯柄、圓底（難以穩定數學化）、高緊旗（Bulkowski 表現排名 30，且近年數據被下修）。
任何新增型態都應先照 D40 流程做回測，再決定是否上線。

## 6. 無法確認的地方

- Bulkowski 的完整失敗率／平均漲幅表在書中（《Encyclopedia of Chart Patterns》），網頁只讀到排名與兩頁頭肩底、高緊旗的數值；
  雙底、升三角、下降楔形的失敗率數值沒有取得。
- 他的樣本期間與「完美交易」篩選條件細節未讀到原文；網站自述統計較舊。
- Savin 等 2007 與 Osler 1998 是讀二手摘要；Chang & Osler 只讀到摘要；Park & Irwin 2007 只確認書目資訊（Journal of Economic Surveys 21(4), 786–826），結論未讀到，不引用其數字。
- 蠟燭線台股兩篇與 Marshall 等 DJIA 的樣本數未取得。
- TechCharts 73% 無原文。
- vectorbt 蠟燭線範例未讀 README；其餘 repo 的 wiki 未讀。
- 未找到加密貨幣型態的嚴謹勝率研究；也未找到台股頭肩底的同儕審查研究，不排除中文資料庫（Airiti）有。
