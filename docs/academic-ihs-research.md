# 頭肩底（IHS）學術方法調研：數學定義、公式與可實作方案

調研日期：2026-10-19。目的：使用者回饋「掃出來的頭肩底都不太像」，找學術論文的精確定義與「看整體形狀」的方法，對照本專案現行做法（固定 pivot window → 組五點 → 門檻規則，見 charter D4–D24、`ihs_scanner/config.py` 的 `TEMPLATE_OVERRIDES`）。

讀取方式：PDF 都先下載，用 `pdftotext` 抽文字；**公式全部再把 PDF 頁面轉成圖片逐一目視核對**（文字抽取會把 ≤/≥、上下標弄丟）。Osler & Chang 是掃描檔，先用 Tesseract OCR，再看關鍵頁圖片核對。
頁碼說明：「WP p.x」指工作論文 PDF 上印的頁碼；「JF p.x」指 Journal of Finance 期刊頁碼。

---

## 0. 結論先講

1. **最推薦的做法：方案 (c)，也就是保留現行規則產生候選，再加一個「形狀分數」**。分數有兩部分：(i) PIP 主導性，檢查 LS/N1/H/N2/RS 是不是窗口內最主要的轉折點；(ii) 拿使用者 5 個範例當模板，算距離（log 正規化）。這樣做最直接回應「五點不是視覺主要轉折」與「只看五點、不看整體」這兩個問題；候選產生邏輯不動，5 個 golden 測試不會被破壞；計算量也幾乎是零。第二步才考慮用多尺度 zigzag 或核平滑取代固定 pivot（方案 a）。
2. 學術上的 HS 定義（Lo 1.5%、Savin R9 時間均勻、Osler 2.5 倍時間比、Bulkowski「兩肩離頭差不多遠」）都要求**時間對稱**。使用者的 5 個範例幾乎都**時間極不對稱**（第 7 節的表：頭的位置落在 LS→RS 的 13%～79%；LS→N1 只有 4～7 根，H→N2 卻可長達 655 根）。所以**不能照抄學術的時間規則**。模板距離要以振幅為主，時間權重要低。
3. Fu et al. (2007) 的原文**沒有拿到**（出版社付費牆；作者機構庫已撤下；唯一的公開 PDF 主機連不上；PolyU 論文庫有人機驗證）。第 5 節的 PIP 與模板公式來自**作者 Fu 2011 年綜述（有讀到）＋兩份二手重述**，都已標明來源，要核對時請以原文為準。

---

## 1. 閱讀狀態總表

| # | 文獻 | 是否讀到全文 | 讀的版本 |
|---|---|---|---|
| 1 | Lo, Mamaysky & Wang (2000) | **是** | NBER WP 7613（60 頁全文）＋JF 期刊版 PDF（課程網站轉載，用來對期刊頁碼） |
| 2 | Savin, Weller & Zvingelis (2007) | **是**（工作論文修訂版） | 2006-10-19 revised version，作者網站 PDF（37 頁） |
| 3a | Osler & Chang (1995) NY Fed Staff Report 4 | **是**（掃描檔，OCR＋圖片核對） | NY Fed 官網 PDF（67 頁） |
| 3b | Chang & Osler (1999) Economic Journal | **否** | RePEc 註明無免費下載。演算法以 3a 為準 |
| 4a | Fu, Chung, Luk & Ng (2007) EAAI | **否**（只讀到摘要） | 見第 5 節，公式取自替代來源 |
| 4b | Fu (2011) "A review on time series data mining" EAAI | **是** | 希臘 Ioannina 大學課程網站轉載的期刊版 PDF |
| 5a | Zhang et al. (2010) ADC，PIP＋Spearman 等級相關 | **是** | 作者網站 PDF |
| 5b | Bulkowski, thepatternsite.com/hsb.html | **是**（網頁） | 2026-10-19 讀取 |
| 5c | Dawson & Steeley (2003)、Zapranis & Tsinaslanidis (2012/2014)、Tsinaslanidis & Kugiumtzis (2014, PIP＋DTW)、Leigh et al. (2002)、Wang & Chan (2007) | **否** | 付費牆、academia.edu／ResearchGate 403、AUTH 圖書館封鎖 IP。依指示「只收有明確數學定義的」，**本文不寫它們的公式** |

---

## 2. Lo, Mamaysky & Wang (2000)

**書目**：Andrew W. Lo, Harry Mamaysky, Jiang Wang, "Foundations of Technical Analysis: Computational Algorithms, Statistical Inference, and Empirical Implementation", *Journal of Finance* 55(4), 1705–1765, 2000. DOI [10.1111/0022-1082.00265](https://doi.org/10.1111/0022-1082.00265)。工作論文：NBER WP 7613，<https://www.nber.org/system/files/working_papers/w7613/w7613.pdf>。
**讀到全文**：是。

### 2.1 方法摘要
1. 把價格看成 $P_t = m(X_t) + \epsilon_t$，用 Nadaraya–Watson 核迴歸估計平滑曲線 $\hat m_h$。
2. 在**滾動視窗**（38 個交易日）裡各做一次平滑，找平滑曲線的局部極值。
3. 把每個平滑極值**映射回原始價格**在 $[\tau-1,\tau+1]$ 範圍內的最高或最低點，叫「相關極值」。
4. 用 5 個**連續**極值 $E_1..E_5$ 套型態定義。

### 2.2 原文公式（逐字照錄，符號依原文）

核函數與權重（WP p.6–7，eq.5–8；JF p.1710–1711）：

$$K(x)\ge 0,\quad \int K(u)\,du = 1 \tag{5}$$
$$K_h(u)\equiv \frac{1}{h}K(u/h),\quad \int K_h(u)\,du=1 \tag{6}$$
$$\omega_{t,h}(x)\equiv K_h(x-X_t)/g_h(x) \tag{7}$$
$$g_h(x)\equiv \frac{1}{T}\sum_{t=1}^{T}K_h(x-X_t) \tag{8}$$

Nadaraya–Watson 估計式（WP p.7 eq.9；JF p.1711）：

$$\hat m_h(x)=\frac{1}{T}\sum_{t=1}^{T}\omega_{t,h}(x)Y_t=\frac{\sum_{t=1}^{T}K_h(x-X_t)Y_t}{\sum_{t=1}^{T}K_h(x-X_t)} \tag{9}$$

高斯核（WP p.7 eq.10；JF p.1711）：

$$K_h(x)=\frac{1}{h\sqrt{2\pi}}\,e^{-x^2/2h^2} \tag{10}$$

交叉驗證選頻寬（WP p.8 eq.12–13；JF p.1714）：

$$CV(h)=\frac{1}{T}\sum_{t=1}^{T}\left(P_t-\hat m_{h,t}\right)^2 \tag{12}$$
$$\hat m_{h,t}\equiv \frac{1}{T}\sum_{\tau\ne t}^{T}\omega_{\tau,h}Y_\tau \tag{13}$$

$\hat m_{h,t}$ 是把第 $t$ 筆拿掉後估出來的值（leave-one-out）。
**0.3 倍規則**（WP p.8；JF p.1714，原文）："an acceptable solution to this problem is to use a bandwidth of $0.3\times h^*$, where $h^*$ minimizes $CV(h)$"。理由是 CV 選出的頻寬讓專業技術分析師覺得「太平滑」。註 5（WP p.26；JF p.1714）說明這個 0.3 是在做統計分析**之前**，用目視加上詢問分析師定下來的，之後沒有再調。
原文也承認這是 ad hoc，並指出核估計的缺點包括 **boundary bias**（邊界偏誤），建議改用局部多項式迴歸（WP p.8–9；JF p.1715）。

滾動視窗平滑（WP p.14 eq.14；JF p.1719）：

$$\hat m_h(\tau)=\frac{\sum_{s=t}^{t+l+d-1}K_h(\tau-s)P_s}{\sum_{s=t}^{t+l+d-1}K_h(\tau-s)},\qquad t=1,\dots,T-l-d+1 \tag{14}$$

找極值（WP p.14；JF p.1719–1720）：
- 找使 $\mathrm{Sgn}(\hat m_h'(\tau))=-\mathrm{Sgn}(\hat m_h'(\tau+1))$ 的 $\tau$。符號由 $+1$ 變 $-1$ 是局部高點，由 $-1$ 變 $+1$ 是局部低點。
- 接著在原始價格 $\{P_t\}$ 的 $[\tau-1,\tau+1]$（NBER 版印成 $[t-1,t+1]$，依上下文應為 $\tau$）找最大或最小值，用這個原始極值判斷型態。
- 遇到平盤（$\hat m_h'(\tau)=0$）時，令 $s=\inf\{s>\tau:\hat m_h'(s)\ne 0\}$，改比較 $\mathrm{Sgn}(\hat m_h'(\tau-1))$ 與 $\mathrm{Sgn}(\hat m_h'(s))$。
- 這個做法保證極值序列高低交替。

**原文也直接點出 raw pivot 的問題**（WP p.14；JF p.1720）："a simpler alternative is to identify local extrema from the raw price data directly … The problem with this approach is that it identifies too many extrema, and also yields patterns that are not visually consistent with the kind of patterns that technical analysts find compelling."

**Definition 1**（WP p.11；JF p.1716），$E_1..E_5$ 是 5 個**連續**局部極值：

$$
\text{IHS}\equiv\begin{cases}
E_1 \text{ a minimum}\\
E_3<E_1,\; E_3<E_5\\
E_1 \text{ and } E_5 \text{ within 1.5 percent of their average}\\
E_2 \text{ and } E_4 \text{ within 1.5 percent of their average}
\end{cases}
$$

HS 是鏡像：$E_1$ 為 maximum，且 $E_3>E_1,\ E_3>E_5$。「within 1.5 percent of their average」在 Savin et al. 被寫成 $\max_i|E_i-\bar E|\le 0.015\,\bar E$（見第 3 節）。

**視窗與完成條件**（WP p.12–13；JF p.1718–1719）：
- $l=35$、$d=3$，所以每個視窗是 $l+d=38$ 個交易日。
- 型態必須在視窗內完成，而且**最後一個極值必須落在第 $t+l-1$ 天**；也就是型態完成後還要再過 $d=3$ 天才算偵測到。
- 報酬從 $t+l+d$ 日收盤起算：$Z_1\equiv Y_{t+l+d+1}/Y_{t+l+d}$。原文說這樣「without any look-ahead bias」。

### 2.3 參數值
| 參數 | 值 | 出處 |
|---|---|---|
| 核 | 高斯 | eq.10 |
| 頻寬 | $0.3\times h^*$（$h^*$ = 每個視窗的 CV 最小值） | WP p.8 / JF p.1714 |
| 視窗 | $l=35,\ d=3$（38 日） | WP p.13 / JF p.1718 |
| 肩、頸容差 | 1.5% | Def.1 |
| 原始極值搜尋範圍 | 平滑極值 ±1 日 | WP p.14 / JF p.1720 |

### 2.4 結果（WP p.20–23，Table I、V–VIII）
- 樣本：NYSE/AMEX 與 Nasdaq，1962–1996，7 個五年期，每期 50 檔，日資料。
- NYSE/AMEX 找到 HS 1,611 個、**IHS 1,654 個**。用 GBM 模擬的對照只有 577／578 個。Nasdaq 的 HS 是 919 個。
- 檢定的是「型態完成後 3 天的單日報酬」分布有沒有改變，**不是交易獲利**。
  - NYSE/AMEX 的十分位卡方檢定 Q：10 種型態中 7 種顯著（不顯著的是 BBOT、TTOP、DBOT，所以 IHS 顯著）。
  - Kolmogorov–Smirnov 檢定：NYSE/AMEX 的 IHS p = 0.104，不顯著；Nasdaq 十種型態全部在 5% 顯著。

### 2.5 無法確認的地方
- 原文**沒有公布 $h^*$ 的典型數值**（例如幾天），只有「0.3 倍」。
- NBER 版的 $[t-1,t+1]$ 與期刊版文字抽取都不清楚，依定義推為 $[\tau-1,\tau+1]$。
- 期刊版表格頁的頁碼我沒有逐一對。

---

## 3. Savin, Weller & Zvingelis (2007)

**書目**：Gene Savin, Paul Weller, Jānis Zvingelis, "The Predictive Power of 'Head-and-Shoulders' Price Patterns in the U.S. Stock Market", *Journal of Financial Econometrics* 5(2), 243–265, 2007. DOI [10.1093/jjfinec/nbl012](https://doi.org/10.1093/jjfinec/nbl012)（卷期頁經 Crossref 核對）。
讀的版本：Revised version, October 19, 2006，<https://www.biz.uiowa.edu/faculty/gsavin/papers/HSrevision_paw_10%2019%2006.pdf>。
**讀到全文**：是（工作論文修訂版。期刊版與它的差異未核對）。

> 注意：本文**只研究 HS 頂部（放空）**，沒有 IHS。下面 IHS 版是鏡像改寫，不是原文。

### 3.1 原文公式（WP p.6–10，目視核對頁面圖片）

核迴歸（WP p.6 eq.2），第 $i$ 個視窗、跨度 $n$：

$$m_{i,n}(x)=\frac{\sum_{j=i}^{i+n-1}P_j\,K\!\left(\frac{x-X_j}{h_{i,n}}\right)}{\sum_{j=i}^{i+n-1}K\!\left(\frac{x-X_j}{h_{i,n}}\right)}$$

$K$ 為標準常態密度。$h_{i,n}$ 在每個視窗用 leave-one-out 交叉驗證選，所以每個視窗的頻寬不同（WP p.6–7）。

極值（WP p.7 §A.4）：若 $m_{i,n}(X_{i-1})<m_{i,n}(X_i)$ 且 $m_{i,n}(X_i)\ge m_{i,n}(X_{i+1})$，則 $X_i$ 是局部高點；低點把不等號反過來。對應的**相關極值**取原始 $P$ 在 $P_{i-1}$ 到 $P_{i+1}$ 區間內的極值。

Lo 的條件（WP p.7）：
- (R1) $E_1$ is a maximum.
- (R2) $E_3>E_1$.
- (R3) $E_3>E_5$.
- (R4) $\max_i|E_i-\bar E|\le 0.015\cdot\bar E,\ i=1,5,\ \bar E=(E_1+E_5)/2$
- (R5) $\max_i|E_i-\bar E|\le 0.015\cdot\bar E,\ i=2,4,\ \bar E=(E_2+E_4)/2$

修改後的條件（WP p.8–9，"Bulkowski restrictions"）：
- (R4a)(R5a)：把 0.015 改成 **0.04**。原文說這樣「allows the neckline to be more steeply sloped」。
- 另外四條：

$$\text{(R6)}\quad \frac{[(E_1-E_2)+(E_5-E_4)]/2}{E_3-(E_2+E_4)/2}\le 0.7$$
$$\text{(R7)}\quad \frac{[(E_1-E_2)+(E_5-E_4)]/2}{E_3-(E_2+E_4)/2}\ge 0.25$$
$$\text{(R8)}\quad \frac{E_3-(E_2+E_4)/2}{E_3}\ge 0.03$$
$$\text{(R9)}\quad \max_i\left|(X^*_{i+1}-X^*_i)-\bar X^*\right|\le 1.2\cdot\bar X^*,\ i=1,\dots,4,\quad \bar X^*=\sum_{i=1}^{4}(X^*_{i+1}-X^*_i)/4$$

$X^*_i$ 是第 $i$ 個極值的日期。
> **這裡更正 `pattern-rules-research.md` 第 4 節第 1 點的不確定**：圖片核對後確認 R6 是 **≤ 0.7**、R7 是 **≥ 0.25**，和先前的推定一致。

- **頸線穿越條件**（WP p.9–10）：價格在走向 $E_6$ 的途中必須跌破 $E_2$–$E_4$ 連線，而且 $E_6$ 必須出現在第 $n-3$ 日。
- **校準方式**（WP p.9）：R6–R9 是用 Bulkowski (1997, 2000) 書中「11 個 63 日內完成的 HS 範例」校準的。**這跟本專案拿使用者 5 個範例校準 T 規則是同一種做法**。

IHS 鏡像改寫（本專案符號；$E_1=LS,\ E_2=N1,\ E_3=H,\ E_4=N2,\ E_5=RS$）：

$$\text{R6/R7:}\quad 0.25\le\frac{[(N1-LS)+(N2-RS)]/2}{(N1+N2)/2-H}\le 0.7,\qquad \text{R8:}\ \frac{(N1+N2)/2-H}{H}\ge 0.03$$

R8 的分母在 IHS 該用 $H$ 還是頸線，原文沒有 IHS 版，屬於本報告的選擇。

### 3.2 參數值
| 參數 | 值 | 出處 |
|---|---|---|
| 視窗 $n$ | 63 交易日（依 Bulkowski 的平均完成時間） | WP p.8 |
| 頻寬 | CV 頻寬 $h_{i,n}$ 的 **1、1.5、2、2.5 倍**（注意：比 Lo 的 0.3 倍**更平滑**） | WP p.8 |
| 肩、頸容差 | 4% | R4a/R5a |
| 肩深/頭深 | 0.25～0.7 | R6/R7 |
| 頭高/價格 | ≥ 3% | R8 |
| 時間均勻 | 1.2 | R9 |

### 3.3 結果（WP p.12–20、Table I p.24）
- S&P 500 在 1990–1999 找到的 HS 數量：

  | 頻寬倍數 | 不加 Bulkowski 限制 | 加 Bulkowski 限制 |
  |---|---|---|
  | 1 | 14,244 | 8,676 |
  | 1.5 | 9,160 | 6,165 |
  | 2 | 5,972 | 4,086 |
  | 2.5 | 3,550 | 2,463 |

  **頻寬越大，型態越少**。
- Russell 2000（60 日、倍數 2.5）：加上 Bulkowski 限制後由 6,406 個降到 4,452 個（−30%），但「**has no impact on the excess returns**」（WP p.13）。也就是加嚴形狀規則不改變預測力。
- 風險調整後超額報酬約年化 5–7%（Fama–French 三因子 intercept 顯著為負，代表放空有利）。其中一部分來自負動能。

### 3.4 無法確認的地方
- 期刊版與這份工作論文的差異未核對。
- CV 頻寬的典型數值沒有公布。

---

## 4. Osler & Chang (1995)（及 Chang & Osler 1999）

**書目**：Carol L. Osler, P. H. Kevin Chang, "Head and Shoulders: Not Just a Flaky Pattern", Federal Reserve Bank of New York Staff Reports No. 4, August 1995，<https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr4.pdf>。**讀到全文**：是（掃描檔；OCR 後再看頁面圖片核對）。
期刊版：P. H. Kevin Chang, Carol L. Osler, "Methodical Madness: Technical Analysis and the Irrationality of Exchange-Rate Forecasts", *Economic Journal* 109(458), 636–661, 1999，DOI [10.1111/1468-0297.00466](https://doi.org/10.1111/1468-0297.00466)。**未讀到**（RePEc 標示無免費下載）。下面全部以 1995 版為準。

### 4.1 方法（SR4 p.10–15）
**Zigzag 定義**（p.10，原文）："We define a peak as a local maximum at least x percent higher than the preceding trough and a trough as a local minimum at least x percent lower than the preceding peak, where x is referred to as the 'cutoff.'"

**多尺度**（p.10–11）：
- 每個幣別掃 10 次，cutoff = 日報酬標準差 σ 的 **1, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00, 3.50, 4.00, 4.50 倍**。各幣別的 σ 為 0.27%～0.79%（註 7）。
- 不同 cutoff 找到的重複型態要去重；新進場日落在既有進場日 2 天內的不算（註 8）。

**HS 頂部判定**：用 4 個連續 peak，後 3 個是 LS、Head、RS（p.11–12，Chart 1 在 p.49；符號：$L_p$ 前一個 peak、$L_t$（$T_{LL}$）前一個 trough、$T_L$/$T_R$ 左右頸線谷）：
1. 頭高於左右肩。
2. 前置上升趨勢：(a) LS > 前一個 peak $L_p$；(b) $T_L$ > 前一個 trough $T_{LL}$。
3. 不可有極端**垂直**不對稱：$RS > (LS+T_L)/2$ 且 $T_R \le (LS+T_L)/2$；同時 $LS > (RS+T_R)/2$ 且 $T_L \le (RS+T_R)/2$。
4. 不可有極端**水平**不對稱：$(t_H-t_{LS})\le 2.5\,(t_{RS}-t_H)$ 且 $(t_{RS}-t_H)\le 2.5\,(t_H-t_{LS})$。
5. 頸線突破時限：RS 之後到穿越頸線的時間 ≤ $t_{RS}-t_{LS}$。

IHS 鏡像（本專案符號，非原文）：
1. $H<LS,\ H<RS$。
2. $LS<$ 前一個 trough，$N1<$ 前一個 peak。
3. $RS<(LS+N1)/2$ 且 $N2\ge(LS+N1)/2$；$LS<(RS+N2)/2$ 且 $N1\ge(RS+N2)/2$。
4. 同上的 2.5 倍時間比。
5. 同上的突破時限。

**第 3、4 條正是 neurotrader888 的 balance／2.5 symmetry（本專案 D19）的原始出處**；D23 template profile 已因為擋掉 TRUST、AZTEC、GRAM 而把它們關閉。

**進出場**（p.13–15）：
- 進場：以穿越頸線當日**收盤價**進場。若穿越發生在 RS 還沒被 cutoff 確認之前，要等 RS 確認才進場（原文："we ensure that our positions do not benefit from future information"）。這和本專案 D13 的 signal_time 同理。
- 出場（內生）：出現新的 trough 或 peak 就出場。
  - 停損：進場價的 1%。
  - bounce：回彈但還沒走到頭到頸線距離的 25% 前，不出場。

### 4.2 結果（p.18–23）
- 樣本：6 種匯率，1973-03-19～1994-06-13，日收盤價。
- 每種幣每年 1～2 個部位，持倉平均幾週。
- 25–40% 的型態最後有穿越頸線（註 9）。
- **馬克與日圓**每筆平均約 +1%，顯著優於隨機漫步自助法；其他 4 種幣不顯著。
- 穩健性（p.23）：時間比改成 2.0 或 3.0、放寬垂直對稱，結果沒有顯著差異。

### 4.3 無法確認的地方
- 1999 期刊版是否改了參數，未核對。
- OCR 文字只拿來定位，引用的條件都對照過頁面圖片（p.11–13、p.49）。

---

## 5. Fu, Chung, Luk & Ng (2007) 與 PIP（Perceptually Important Points）

**書目**：Tak-chung Fu, Fu-lai Chung, Robert Luk, Chak-man Ng, "Stock time series pattern matching: Template-based vs. rule-based approaches", *Engineering Applications of Artificial Intelligence* 20(3), 347–364, 2007. DOI [10.1016/j.engappai.2006.07.003](https://doi.org/10.1016/j.engappai.2006.07.003)。

**讀到全文**：**否**。試過的管道：
- ScienceDirect：403。
- NSYSU 公開 PDF（<https://par.cse.nsysu.edu.tw/resource/paper/2011/111219/Stock%20time%20series%20pattern%20matching%20Template-based%20vs.%20rule-based%20approaches.pdf>）：主機連線逾時，Wayback 沒有存檔。
- PolyU IRA：顯示 withdrawn。
- Fu 2007 博士論文（PolyU theses 200/1064，標示 open access）：被互動式人機驗證擋住。
- ResearchGate：403。

**只讀到摘要**（VTC 機構庫 <https://repository.vtc.edu.hk/ive-it-sp/4/>）。摘要確認的事實：
- 比較 3 種 PIP 距離：PIP-ED、PIP-PD、PIP-VD。
- 有 template-based 與 rule-based 兩種比對方法。
- 「By identifying the PIPs directly from the time domain, time series and templates of different lengths can be compared」。
- 結論只說兩種方法「provide different directions」。**哪個較好、準確率數字，摘要沒寫，我沒有讀到**。

### 5.1 PIP 選點：作者本人的描述（讀到）
來源：Tak-chung Fu, "A review on time series data mining", *EAAI* 24(1), 164–181, 2011，DOI [10.1016/j.engappai.2010.09.007](https://doi.org/10.1016/j.engappai.2010.09.007)。讀的是轉載 PDF <https://www.cse.uoi.gr/~pvassil/supervision/diplomatikes/extraMaterial/Timeseries/segmentation/2011_TimeSeriesMiningSurvey.pdf>，§2 p.165，原文：

> "The first data point $P_1$ and the last data point $P_n$ in the time series are the first and two PIPs, respectively. The next PIP that is found will be the point in P with maximum distance to the first two PIPs. The fourth PIP that is found will be the point in P with maximum vertical distance to the line joining its two adjacent PIPs … The PIP location process continues until all the points in P are attached to a reordered list L or the required number of PIPs is reached … Seven PIPs are identified …"

同頁也說這個想法跟 Douglas–Peucker 折線化簡相近。

### 5.2 PIP 距離公式（二手來源，未對原文）

設相鄰兩個 PIP 為 $p_1(x_1,y_1)$、$p_2(x_2,y_2)$，測試點 $p_3(x_3,y_3)$：

**VD（vertical distance）**：

$$VD(p_3,p_c)=|y_c-y_3|=\left|\left(y_1+(y_2-y_1)\frac{x_c-x_1}{x_2-x_1}\right)-y_3\right|,\quad x_c=x_3$$

- 來源 A：Phetchanchai et al., "Index Financial Time Series Based on Zigzag-Perceptually Important Points", *J. Computer Science* 6(12):1389–1395, 2010, p.1390（<https://thescipub.com/pdf/jcssp.2010.1389.1395.pdf>，讀到全文）。該文把絕對值拿掉，用正負號判斷轉折方向。
- 來源 B：silicon-beach/stock-analysis 的報告 `report.pdf` p.2（<https://github.com/silicon-beach/stock-analysis>，MIT，學生專題）。

**PD（perpendicular distance）**，來源 B p.2：

$$s=\frac{y_2-y_1}{x_2-x_1},\quad x_c=\frac{x_3+s\,y_3+s^2x_2-s\,y_2}{1+s^2},\quad y_c=s\,x_c-s\,x_2+y_2,\quad PD=\sqrt{(x_c-x_3)^2+(y_c-y_3)^2}$$

**ED**：測試點到兩個相鄰 PIP 的歐氏距離之和（來源 A p.1390 文字描述）。

二手來源說 Fu 等人認為 **PIP-VD 表現最好**（WebSearch 摘要引述，我沒有讀到原文表格）。Zhang et al. 2010 則改用 PD。

### 5.3 模板比對（Template-based）：二手重述

來源 B（report.pdf p.3，公式為圖片，已目視核對）。它在參考文獻中引用 Fu et al. 2007，Fig.3 也像是重繪原文的圖：

$$AD(SP,Q)=\sqrt{\frac{1}{n}\sum_{k=1}^{n}(SP_k-Q_k)^2}$$
$$TD(SP,Q)=\sqrt{\frac{1}{n-1}\sum_{k=2}^{n}(SP_k^t-Q_k^t)^2}$$
$$DM(SP,Q)=w_1\times AD(SP,Q)+(1-w_1)\times TD(SP,Q)$$

- $SP_k$ 是第 $k$ 個 PIP 的振幅（正規化到 0–1），$Q_k$ 是模板點振幅；上標 $t$ 代表時間座標。
- 來源 B 自己取 $w_1=0.5$。**Fu 原文建議的 $w_1$、正規化方式、相似度門檻，我都無法確認**。
- 來源 B 的程式碼 `recognizer.py` 實際上用平方、沒開根號（`AD = tmp.dot(tmp)/N`），$w=0.4$。
- 程式還有一個「temporal control penalty」：$tc=1-\exp(-(d/\theta)^2)$，其中 $d=\text{slen}-\text{dlen}$、$\theta=\text{dlen}/\text{dlc}$。依該報告參考文獻，應出自 Chung et al. 2004（IEEE TEC 8(5):471–489，演化式分段），**未核對**。

來源 B 的 IHS 模板（7 點，時間等距 $x=0,1/6,\dots,1$）：$y=[1,\ 0.25,\ 0.5,\ 0,\ 0.5,\ 0.25,\ 1]$（`patterns.json`）。**這是該專題自訂的，不保證等於 Fu 原文的模板值**。

### 5.4 規則比對（Rule-based）：二手重述

來源 B（report.pdf p.3–4，明說是依 Lo et al. 2000 的定義寫成 7 個 PIP 的規則）。IHS，$sp_1..sp_7$ 由左到右：

```
sp4 < sp2 and sp6        （頭最低）
sp2 < sp1 and sp3        （左肩是局部低）
sp6 < sp5 and sp7        （右肩是局部低）
sp3 < sp1                （頸線點低於起點：前置下跌）
sp5 < sp7                （頸線點低於終點：已上漲）
diff(sp2, sp6) < 15%     （兩肩）
diff(sp3, sp5) < 15%     （兩頸線點）
```

程式裡 `diff(a,b) = |a−b|/min(a,b)`。報告中「Patterns with similar shapes … but violating certain rules may still be identified」那段文字很像轉述 Fu 原文對模板法缺點的說明。
注意：Lo 原文是 **1.5%**，這裡是 **15%**。是 Fu 刻意放寬還是轉述筆誤，**無法確認**。Zhang et al. 2010 也寫 15%，並說「according the definition of Lo et al. (2000)」。

### 5.5 Fu 2007 的實驗結論
**無法確認**（沒讀到原文）。不要引用任何「模板法 vs 規則法準確率」的數字。

---

## 6. 其他有明確數學定義、且讀到全文的

### 6.1 Zhang, Jiang, Liu, Lau, Wang, Zhang (2010)：PIP＋Spearman 等級相關＋規則
**書目**："A Real Time Hybrid Pattern Matching Scheme for Stock Time Series", Proc. 21st Australasian Database Conference (ADC 2010), CRPIT Vol.104。PDF：<https://ruizhang.info/publications/Adc2010_HybridPatternMatching.pdf>（讀到全文，9 頁；頁碼依 PDF 頁序）。

方法：
- 在滑動窗 $W$ 內用 PIP-PD 抽 $N=7$ 點（Fig.4）。
- 把 7 點換成**等級**，和模板的等級算 Spearman 相關（eq.2）：
  $$\rho=1-\frac{6\sum_{i=1}^{n}d_i^2}{n(n^2-1)}$$
  $d_i$ 是兩組等級差。
- $\rho$ 超過該模板的門檻 THRE 後，再過規則集。
- 窗口移動：找到型態就跳 $W$，否則移 1 根（Fig.3、Fig.5）。

模板（Table 1，「Position in descending order」與「Rank」，rank 1 = 最高）：
- HS：Position $[6\,2\,4\,1\,5\,3\,7]$，Rank $[6.5\ 2.5\ 4.5\ 1\ 4.5\ 2.5\ 6.5]$。
- **Reversed HS（＝IHS）**：Position $[1\,5\,3\,7\,4\,6\,2]$，Rank $[1.5\ 5.5\ 3.5\ 7\ 3.5\ 5.5\ 1.5]$。
- 兩肩同等級（都是 5.5），所以**不規定左肩或右肩誰高**。等級法天生容忍點位上下移動。

IHS 規則（Fig.7，Down 版）：
- Rule 1：$|SP_2-SP_6|<15\%$。
- Rule 2：$|SP_3-SP_5|<15\%$。
- Rule 3'：$SP_4$ 等級最後（最低）。
- Rule 4'：$sp_2$、$sp_6$ 等級為 5 與 6。
- Rule 5：$sp_1$、$sp_7$ 等級 5、6 或 7（原文只寫 Up 版，Down 版未明寫）。

合成資料產生（Fig.8）：
- time scaling：7 點模板拉長到 $m=25, 43, 61$。
- time warping：把每個關鍵點在相鄰點間距的 1/3、2/3 範圍內隨機移動。
- 雜訊：振幅 0–0.1／0.1–0.2／0.2–0.3，機率 0.5。
- 共 216 條序列。原文說這是「improving more details of the parameters than Fu et al (2007) proposed」。

結果：合成資料與恆生指數 21 年（5,087 點）上，準確率都優於歐氏距離法和斜率法。**只有長條圖（Fig.10），內文沒有數字；THRE 門檻值也沒寫**。

### 6.2 Bulkowski，Head-and-Shoulders Bottom（網站）
<https://www.thepatternsite.com/hsb.html>（2026-10-19 讀取；頁面 meta 有 `noai`，只引用事實）。
- Identification Guidelines：
  - Shape：「3-valley pattern … proportional, and not lopsided」。
  - Symmetry：「two shoulders should bottom near the same price, be **nearly the same distance from the head**」。
  - Confirmation：收盤**突破向下傾斜的頸線**；若頸線向上傾斜，改以突破**右 armpit**為準。
- 統計：3,197 筆、失敗率 11%、平均漲幅 45%、throwback 65%、達標 71%。
- Trading lessons：「Avoid multi-peaks」（有多個谷的複雜底要避開）。
- 沒有數學公式，只有文字準則。

### 6.3 DTW（動態時間校正）：Fu (2011) 的定義（讀到）
Fu 2011 §3，p.168 eq.2–4：
- 距離矩陣 $m_{ij}=d(q_i,p_j)=(q_i-p_j)^2$。
- 校正路徑 $W=w_1,\dots,w_K$，$\max(m,n)\le K<m+n-1$。限制條件：
  - 邊界：$w_1=(1,1)$，$w_K=(m,n)$。
  - 連續：$a-a'\le1,\ b-b'\le1$。
  - 單調：$a-a'\ge0,\ b-b'\ge0$。
- 遞迴式與距離：
  $$\gamma(i,j)=d(q_i,p_j)+\min\{\gamma(i-1,j-1),\gamma(i-1,j),\gamma(i,j-1)\}$$
  $$DTW(Q,P)=\min_W\Big[\sum_{k=1}^{K}d(w_k)\Big]$$
- 同頁引 Keogh & Pazzani (2001) 的批評：DTW 可能把一個點對到另一條序列的一大段，產生不直覺的對齊（"unintuitive alignments"）。

**沒有讀到「用 DTW 判定 HS」的論文全文**：Tsinaslanidis & Kugiumtzis 2014 (ESWA) 被 403 擋住。另外找到 2026 年 Pacific-Basin Finance Journal 一篇台股 HS 研究（DOI 10.1016/j.pacfin.2026.103390），**未讀**。

---

## 7. 用學術規則檢驗使用者的 5 個範例（實算）

點位取自 `tests/golden_examples.py`。AZTEC 的 RS 還在形成中，時間暫定為 2026-10-09。

| 範例 | LS→N1 / N1→H / H→N2 / N2→RS（根） | 頭在 LS→RS 的位置 | Lo 肩 1.5% | Savin 肩/頸 4% | Savin R6/R7 比 | Savin R9 時間 1.2 | Osler 時間比 2.5 |
|---|---|---|---|---|---|---|---|
| HMSTR 4h | 7 / 116 / 57 / 22 | 0.61 | 2.4% ✗ | 2.4%／2.3% ✓ | 0.78 ✗（>0.7） | ✗ | 1.56 ✓ |
| TRUST 4h | 7 / 104 / 655 / 60 | **0.13** | 8.8% ✗ | 8.8%／6.6% ✗ | 0.59 ✓ | ✗ | **6.4 ✗** |
| GRAM 1d | 4 / 74 / 11 / 10 | **0.79** | 3.4% ✗ | ✓／✓ | 0.70 邊界 | ✗ | **3.7 ✗** |
| AZTEC 1d | 6 / 84 / 41 / ≥11 | 0.63 | 14.5% ✗ | ✗／✗ | 0.61 ✓ | ✗ | 1.7 ✓ |
| PENDLE 1d | 7 / 128 / 167 / 19 | 0.42 | 0.4% ✓（頸 1.7% ✗） | ✓／✓ | 0.44 ✓ | ✓ | 1.4 ✓ |

（肩、頸欄位是 $|E_1-\bar E|/\bar E$。R6/R7 比 = $\frac{[(N1-LS)+(N2-RS)]/2}{(N1+N2)/2-H}$。R9 例：HMSTR 四段平均 50.5 根，最大偏差 65.5 根 > 1.2×50.5。）

用 log 價、以「頭＝0、頭正上方的頸線＝1」正規化後的振幅 $y'$（第 9 節方案 c 會用到）：

| 範例 | LS | N1 | H | N2 | RS |
|---|---|---|---|---|---|
| HMSTR | 0.32 | 1.08 | 0 | 0.96 | 0.19 |
| TRUST | 0.59 | 1.03 | 0 | 0.80 | 0.27 |
| GRAM | 0.48 | 1.19 | 0 | 0.97 | 0.25 |
| AZTEC | 0.72 | 1.27 | 0 | 0.87 | 0.31 |
| PENDLE | 0.68 | 1.01 | 0 | 0.98 | 0.68 |
| 平均（標準差） | 0.56 (0.16) | 1.12 (0.11) | 0 | 0.92 (0.08) | 0.34 (0.19) |

**解讀**：
- 使用者心中的頭肩底是「**振幅結構清楚**（兩肩各自從頸線的跌幅相近、頭明顯更深、頸線乾淨），**時間可以很不對稱**（左肩很窄，常常只是 4～7 根的急跌急彈）」。
- 學術定義幾乎都以時間對稱為核心（Lo 用 38 日視窗、Savin R9、Osler 2.5 倍、Bulkowski），**照搬會把使用者的範例擋掉**。D23 關掉 D19 balance／symmetry 的決定是對的。
- 學術方法真正能借的是三件事：**怎麼選點**（平滑後的極值、zigzag、PIP）、**連續極值的要求**（五點之間不能夾著更顯著的轉折），以及**用範例校準**（Savin 用 11 個範例校準 R6–R9，本專案用 5 個）。

---

## 8. 為什麼目前的型態可能看起來不像（對照論文）

| # | 現行做法的問題 | 論文怎麼處理 | 能解決什麼 |
|---|---|---|---|
| 1 | **固定 pivot（左右各 5 根）不管型態尺度**。型態寬 60～1000 根，5 根 pivot 對一個 600 根的型態來說等於雜訊級的小波動，可選的 LS/RS 太多 | Lo 先平滑再找極值，並明說 raw extrema「identifies too many extrema … not visually consistent」（JF p.1720）。Osler 用 cutoff = k·σ 的 zigzag，而且**掃 10 個尺度**。Savin 用 4 種頻寬倍數 | 極值數量隨型態尺度調整，選到的點比較接近肉眼看到的主要轉折 |
| 2 | **只檢查五點，五點之間的路徑沒檢查**。T4 只管頸線上方，下方沒管：例如 N1→H 之間另有一個接近頭部深度的谷（Bulkowski 的 multi-peak）、或 H→N2 中途一個大反彈，規則仍然會放行 | Lo 要求 $E_1..E_5$ 是平滑曲線上**連續**的極值，中間不准夾別的顯著轉折。PIP 選點天生就挑「離骨架最遠」的點，主要轉折一定先被選中 | 檢查「五點是不是這段走勢最重要的 5 個轉折」（第 9 節方案 c 的 PIP 主導性） |
| 3 | **組合式選點**：每個 H 對應很多個 LS、RS，規則一一過濾後，留下的可能是「數值剛好過門檻」的小谷。T7、T8 是補丁 | Fu／Zhang 用模板距離或等級相關**排序**，取最像的；Osler 對不同 cutoff 的結果去重 | 用「離範例模板多遠」排序或過濾，取代「剛好過門檻」 |
| 4 | **門檻規則是獨立的是非題，沒有整體形狀分數**。多條都只是勉強通過的型態，和每條都漂亮通過的型態得到一樣的結果 | 模板法（Fu）是整體距離；Savin 發現加 4 條形狀限制只砍掉 30% 的數量，**報酬沒變**，代表規則越多不等於越像 | 一個連續的形狀距離，可以排序、可以跟使用者範例比 |
| 5 | **線性價格百分比**：小幣漲跌幅很大（PENDLE 頭到頸線 +191%），百分比門檻在不同振幅下的意義不一樣 | 論文多半直接用價格（股票振幅小）。PIP 和模板都先把振幅正規化到 0–1 | 改用 log 價，再以頭高正規化（第 7 節的 $y'$），尺度一致 |
| 6 | **時間對稱學術規則不適用** | 見第 7 節 | 模板距離的時間權重要低，或只檢查順序 |

---

## 9. 建議方案

共同約定：
- 「as-of $t$」指最後一根已收盤 K 線，所有計算只用 ≤ $t$ 的資料。
- 回測時每個 $t$ 都要重算，**不可以先對整條序列做一次平滑或 PIP 再回測**。
- 計算量的基準：1378 幣 × 3 週期 ≈ 4,134 條序列，每條 ≤ 1000 根。現在全掃偵測約 10 秒，抓資料約 5 分鐘。
- 下面的毫秒數是本機 numpy 粗測：1000 根上 NW 稠密矩陣平滑 ≈ 29 ms；PIP 抽 7 點 ≈ 0.2–2 ms；CV 掃 20 個 h ≈ 0.5 s。

### 方案 (c)【優先 1】現行規則產生候選＋形狀分數（PIP 主導性＋範例模板距離）

**步驟**：
1. 現行 detector 照舊輸出候選（LS, N1, H, N2, RS, status）。**不改候選邏輯**，D23 的 5 個 golden 測試不受影響。
2. 取窗口 $[s,e]$：$s=\max(0,\ LS.idx-\lceil\beta\,(RS.idx-LS.idx)\rceil)$，$\beta=0.25$（含 LS 之前的下跌段）；$e=t$。
3. 序列：$z_i=\log\big((high_i+low_i)/2\big)$（或 log close）。用 **PIP-VD** 在 $z[s..e]$ 抽 $K=9$ 個點（含兩端）。每個 PIP 再吸附到原始 K 線：谷點取 $\pm r$ 根內最低的 low，峰點取最高的 high；$r=\max(2,\lceil0.02W\rceil)$，$W=RS.idx-LS.idx$。
4. **PIP 主導性**：
   - 候選的 5 點各自在 $\pm\tau$ 根內找同類型的 PIP（谷對谷、峰對峰），$\tau=\max(N_{pivot},\lceil0.05W\rceil)$。$S_{dom}$ = 配對到的點數 / 5。
   - 另外，$(LS,RS)$ 之間若有沒被配對、而且 $VD>\gamma\cdot$ head_height 的 PIP（$\gamma\approx0.35$，待校準），就是「多出來的主要轉折」（multi-peak），要扣分。
   - 起始建議：H 必須配對到，且 $S_{dom}\ge 0.8$。
5. **模板距離**（使用者 5 個範例當模板，公式沿用 Fu 式，見 5.3 節）：
   - 振幅：$y'_k=(z_k-z_H)/(z_{neck}(H)-z_H)$，$k\in\{LS,N1,N2,RS\}$。
   - 時間：$x'_k=(t_k-t_{LS})/(t_{RS}-t_{LS})$，$k\in\{N1,H,N2\}$。
   - 對每個範例 $j$：
     $$AD_j=\sqrt{\tfrac14\sum_k (y'_k-q^{j}_k)^2},\quad TD_j=\sqrt{\tfrac13\sum_k (x'_k-q^{j,t}_k)^2},\quad D_j=w\,AD_j+(1-w)\,TD_j$$
     $w=0.8$（使用者範例時間差很大，時間權重要低）。
   - 分數 $D=\min_j D_j$（最近範例）。也可以給網站顯示的 0–100 分：$100\cdot\max(0,1-D/\theta)$。
6. **門檻校準**：
   - 先做 5 個範例的 leave-one-out（實算）：
     - $w=1$ 時，每個範例到最近另一範例的距離是 0.09（HMSTR、GRAM）、0.12（TRUST）、0.13（AZTEC）、0.20（PENDLE）。
     - $w=0.8$ 時是 0.09～0.19。
   - 所以起始 $\theta\approx0.25$，略高於最大的 LOO 最近距離，讓每個範例在拿掉自己後仍能被其他範例接受。
   - **只有 5 個正樣本，先只拿來排序，不要過濾**。等使用者標了負樣本，再用「正樣本 LOO 全部通過、負樣本被排除的比例」選 $\theta$、$\gamma$、$w$。
   - 對照：傳統對稱模板 $[0.5,1,0,1,0.5]$（時間等分）到 5 個範例的 AD 是 0.11～0.19、TD 是 0.16～0.27。
7. 最後排序 = 現行分數 × $f(S_{dom},D)$；或先在網站上顯示「形狀分」，給使用者驗證。

**計算量**：每個候選做一次 PIP，約 1–2 ms。即使全幣種候選在過濾前有數千個，也只多幾秒；過濾後約 200 個，可以忽略。

**look-ahead**：窗口只到 $t$，沒有前視。但 PIP 的最後一點就是 $t$，所以 forming 或 watchlist 型態的分數會隨每根新 K 線變動（這是「形成中」的本質，不是偏誤）。回測要逐根重算。

**與範例相容**：模板就是範例本身，候選邏輯也不變；要確保的是**拿掉自己之後**仍高於門檻（LOO），可以寫成 golden 測試。注意 AZTEC 的 RS 時間是暫定值。

**為什麼排第一**：改動最小、風險最低，而且直接處理第 8 節的 #2、#3、#4、#5 四個問題。

### 方案 (a)【優先 2】改進 swing 的來源：多尺度 zigzag 或核迴歸平滑，取代固定 pivot

兩個子方案，擇一或並行，用 profile 旗標切換，和現行方法對照。

**(a1) Osler 式多尺度 zigzag（建議先做）**：
1. 波動率：$\sigma_t$ = $t$ 之前 200 根 log 報酬的標準差（只用 ≤ $t$）。
2. cutoff $x=k\cdot\sigma_t$。原文 $k=1\ldots4.5$ 是針對日匯率，型態深度約 1–5%；本專案型態深度 20–40%，所以 $k$ 要校準，目標是讓 $x$ 大約落在 3%–25%。也可以直接用頭高的比例當尺度。取 6～10 個 $k$。
3. 每個 $k$ 產生高低交替的峰谷：谷要比前一個峰低 x% 才成立，**確認時間 = 價格從谷反彈 x% 的那根**。這是因果的，確認延遲依幅度而定，不是固定 N 根。
4. 把每組**連續**的 5 個極值（谷、峰、谷、峰、谷）當 LS..RS，套現行 T 規則（對應 Lo 的「five consecutive extrema」）。不同 $k$ 的重複結果去重（D11）。
5. forming RS 照 T6：N2 確認之後，以至今最低點暫定 RS。

**(a2) Lo 式核平滑**：
1. 對每個尺度 $L\in\{4h: 150, 300, 600, 1000\}$，取窗口 $[t-L+1,t]$，在 $z$ 上用 eq.14 的高斯核平滑，頻寬 $h=c\cdot L$。
2. 在平滑曲線上找導數變號點，映射回原始 low/high，搜尋範圍 $\pm\lceil h\rceil$ 根（Lo 用 ±1 日，是因為他的視窗只有 38 日）。
3. **頻寬不要用 CV**：本機用隨機漫步測試，CV 選到的 $h^*\approx1$ 根，幾乎不平滑；Lo 自己也說 CV 不適合，0.3 倍是 ad hoc。改成 $c$ 用 5 個範例校準：在範例截圖時點，找讓平滑極值恰好落在使用者 5 點附近的 $c$。
4. **右端邊界效應（重要）**：在 $\tau$ 靠近 $t$ 的位置，高斯核只剩左半邊，估計值會往內部偏（Lo 自己說的 boundary bias），而且新 K 線進來後，近端的平滑值和極值會移動或消失（repaint）。
   - 這不是前視，因為只用了 ≤ $t$ 的資料；但最後幾根的極值不穩定。
   - 規則：只承認 $\tau\le t-d$ 的平滑極值，$d=\lceil\kappa h\rceil$、$\kappa\approx1.5$（同 Lo 的 $d=3$ 和 pivot 的確認延遲）。
   - 近端的 RS 用原始價格的 T6 邏輯。
   - 可以改用局部線性迴歸（Lo 建議的 local polynomial）降低邊界偏誤。
5. 計算量：用截斷卷積實作，$\hat m=\frac{(P*K_h)}{(1*K_h)}$，核截在 $\pm4h$，成本 $O(L\cdot h)$。估算每條序列每個尺度不到 1 ms，4 尺度 × 4,134 條約數秒。**不要用稠密矩陣**：約 29 ms × 4 × 4,134 ≈ 8 分鐘。**也不要每次跑 CV**：約 0.5 s × 4 × 4,134 ≈ 35 分鐘。

**與範例相容**：未知，要實測。風險是 TRUST、PENDLE 這種寬型態裡，窄窄的左肩（4–7 根）在大尺度下可能被平滑掉（zigzag 大 cutoff 也一樣）。多尺度可以緩解；如果真的被抹掉，就只能用小 cutoff 抓 LS，用大 cutoff 抓 H（「大 pivot 抓頭、小 pivot 抓肩」的雙層想法，見 `pattern-rules-research.md` 的 R35）。

**look-ahead**：(a1) 用確認時間就沒有前視；(a2) 見第 4 點。回測都要逐根重算。

### 方案 (b)【優先 3，研究性質】純 PIP 窗口掃描＋範例模板（Fu 式）

1. 對 as-of $t$，窗長 $L$ 取幾何級數：4h 60→1000、1d 40→600、1w 16→200，各約 12～14 個值。窗口為 $[t-L+1,t]$。
2. 在 $z$ 上抽 PIP-VD 9～11 點。從中挑「谷、峰、谷、峰、谷」的 5 點子序列，加上兩端點，共 7 點 $[sp_1..sp_7]$。組合數很小，可以窮舉。
3. 規則先篩：5.4 節的 IHS 規則，但把 15% 換成本專案 T2 的跌幅對稱；$sp_1>sp_3,sp_5$（前置下跌）；forming 時 $sp_7>sp_6$。
4. 模板距離：同方案 (c) 第 5 步。對每個 $L$ 取最小的 $D$，跨 $L$ 去重。
5. 計算量：每條序列約 14 窗 × 2 ms ≈ 30 ms，4,134 條約 2 分鐘。可以向量化或只對有候選的序列做。
6. look-ahead：無（窗口只到 $t$）。
7. 與範例相容：**風險最高**。7 個 PIP 選的是「垂直距離最大」的點，在 TRUST（826 根）這種寬型態中，7 根寬的左肩不一定排得進前 9 個 PIP。這也是為什麼 (b) 適合當「第二路候選來源」補漏，而不是取代現行偵測。

### 優先序與預估
1. **(c)**：先只做「顯示形狀分」，不過濾，用來收集使用者回饋；等有負樣本再開過濾。約 1～2 天，包含測試與 LOO golden 測試。
2. **(a1) zigzag 多尺度**：設成新 profile，與 template profile 在同一批樣本上對照筆數與使用者評分。
3. **(a2) 核平滑**、**(b) 純 PIP**：等 (c) 和 (a1) 的結果出來再決定是否需要。

**技術決策提醒**：採用哪一個方案時，請把決策寫進 charter 決策記錄（D26），格式為「日期｜決定｜原因｜何時可推翻」。

---

## 10. 需要使用者提供的東西

1. **「不像」的負樣本 20～50 個**：直接從網站或 Discord 現有結果挑，給 pattern_id 或「交易所＋幣＋週期＋LS 日期」即可，每個附一句「哪裡不像」。例如：左肩太小、頭不明顯、中間太亂、頸線歪、已經漲完。這是校準 $\theta$、$\gamma$、$w$ 唯一可靠的依據。
2. **更多「像」的正樣本 10～20 個**，最好包含：1w（目前 0 個）、已確認突破的、時間比較對稱的（看模板是否太偏向「窄左肩」）。標記方式同 `tests/golden_examples.py`：LS/N1/H/N2/RS 的時間＋價格（UTC+8 也可以，我們會轉換）。若能再標**型態起點**（LS 之前開始下跌的高點）更好，可以當模板的第 1 點。
3. 確認一個偏好：像 TRUST 那樣**頭在很左邊**（位置 0.13）、右半段拖很長的形狀，是否也算你要的頭肩底？這決定模板的時間權重 $w$ 要多低。
4. 想要「少而漂亮」還是「多而寬鬆」：決定方案 (c) 是當過濾還是只當排序。
5. （選擇性）如果你有學校或公司的圖書館帳號，可以下載 Fu et al. 2007 原文 PDF 給我核對第 5 節的公式（DOI 10.1016/j.engappai.2006.07.003）。

---

## 11. 無法確認或待核對的清單

1. **Fu et al. 2007 原文沒有讀到**。第 5.2–5.4 節的 VD／PD 公式、AD／TD／$w_1$ 的形式、IHS 規則集都來自二手來源（Phetchanchai et al. 2010、silicon-beach 學生報告與 MIT 程式碼）。Fu 原文的 $w_1$ 建議值、正規化方式、HS 模板點值、相似度門檻、實驗準確率**一概未知**。
2. Fu 規則中的「15%」與 Lo 的「1.5%」不一致：是放寬還是誤植，無法確認。
3. Lo 的 $h^*$ 典型數值、Savin 的 CV 頻寬數值，原文都沒有公布。
4. Savin 只研究 HS 頂部，第 3 節的 IHS 版是鏡像改寫；R8 分母在 IHS 該用 $H$ 還是頸線，屬於本報告的選擇。
5. Chang & Osler (1999) 期刊版沒讀到，是否和 1995 版參數不同未知。
6. 第 5 節「PIP-VD 表現最好」只是 WebSearch 摘要轉述的二手說法，未見原文表格。
7. Dawson & Steeley (2003)、Zapranis & Tsinaslanidis (2012/2014)、Tsinaslanidis & Kugiumtzis (2014, PIP＋DTW)、Leigh et al. (2002) 模板網格、Wang & Chan (2007)、2026 年 Pacific-Basin Finance Journal 台股 HS 研究：**都沒讀到全文，本文不採用它們的任何數字或公式**。
8. 第 9 節的計算量是本機用合成資料粗測，加上外推；「LOO 距離 0.09～0.20」是用使用者標記的點位算的，不是用偵測器實際選到的點。AZTEC 的 RS 時間是暫定值。
9. 方案 (a)(b) 與使用者範例的相容性**沒有實測**，只是推論。
