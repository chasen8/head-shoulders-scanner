# 頭肩底掃描器規格（使用者原文，2026-10-16）

> 本檔是使用者提供的原始規格，**權威來源**。規格沒寫到、或需要詮釋的地方，
> 一律記在 `docs/project-charter.md` 決策記錄，不改本檔原文。

目標：
從多個 symbol、多個 timeframe 的 OHLCV 資料中，偵測「頭肩底 Inverse Head and Shoulders」候選型態，並輸出符合條件的 symbol、timeframe、型態關鍵點、分數與相關指標。

資料來源：
每個 symbol 都有 OHLCV K 線資料，欄位至少包含：timestamp、open、high、low、close、volume

需要支援 timeframe：4h、1d、1w

策略核心：
尋找頭肩底結構：

- Left Shoulder Low  = 左肩低點，記為 LS
- Neckline High 1    = 左頸線高點，記為 N1
- Head Low           = 頭部低點，記為 H
- Neckline High 2    = 右頸線高點，記為 N2
- Right Shoulder Low = 右肩低點，記為 RS

時間順序必須滿足：LS.time < N1.time < H.time < N2.time < RS.time

價格結構必須滿足：H.low < LS.low、H.low < RS.low（頭部必須低於左右肩）。

## 第一步：找 swing high / swing low

pivot window 方法：
- swing low：某根 K 線的 low 必須小於前後 N 根 K 線的 low
- swing high：某根 K 線的 high 必須大於前後 N 根 K 線的 high

建議 pivot window：4h：N = 3 到 5；1d：N = 3 到 5；1w：N = 2 到 3。做成可調參數。

## 第二步：組合候選頭肩底結構

swing low LS → swing high N1 → swing low H → swing high N2 → swing low RS

1. LS、H、RS 必須是 swing low
2. N1、N2 必須是 swing high
3. H.low 必須低於 LS.low 與 RS.low
4. RS.low 必須在 H.low 之後
5. N1 必須在 LS 與 H 之間
6. N2 必須在 H 與 RS 之間

## 第三步：左右肩差異率

shoulder_diff = abs(LS.low - RS.low) / ((LS.low + RS.low) / 2)

- 4h：shoulder_diff <= 0.05
- 1d：shoulder_diff <= 0.10
- 1w：shoulder_diff <= 0.10

## 第四步：頭部深度

shoulder_avg = (LS.low + RS.low) / 2
head_depth = (shoulder_avg - H.low) / shoulder_avg

條件 head_depth > 0；最小頭部深度（可調）：4h >= 0.03、1d >= 0.05、1w >= 0.05

## 第五步：頸線角度 / 頸線斜率

bars_between = N2.index - N1.index
neckline_slope_pct_per_bar = ((N2.high - N1.high) / N1.high) / bars_between
neckline_angle = atan(neckline_slope_pct_per_bar * angle_scale) * 180 / pi

angle_scale 預設 100。條件 abs(neckline_angle) <= 30。angle_scale 與 max_neckline_angle 可調。

## 第六步：頸線價格

neckline_price_at(i) = N1.high + (N2.high - N1.high) * ((i - N1.index) / (N2.index - N1.index))

## 第七步：突破條件（可選）

- Mode A candidate：結構成立＋肩差＋頭深＋頸線角度符合 → 輸出候選
- Mode B breakout：candidate 條件之外，還要在 RS 之後 close 突破頸線：
  close > neckline_price_at(current_index) * (1 + breakout_buffer)

breakout_buffer 預設：4h 0.005、1d 0.01、1w 0.01

## 第八步：成交量條件（可選）

volume_ratio = breakout_volume / average_volume_20，啟用 volume_filter 時 volume_ratio >= 1.2。
candidate 模式尚未突破時 volume_ratio 可為 null。

## 第九步：Pattern Score（0–100）

1. shoulder_score（30）= max(0, 1 - shoulder_diff / max_allowed_shoulder_diff) * 30
2. head_depth_score（25）= min(head_depth / ideal_head_depth, 1) * 25，ideal_head_depth = 0.10
3. neckline_score（25）= max(0, 1 - abs(neckline_angle) / max_neckline_angle) * 25
4. breakout_score（20）：breakout 模式且已突破給 20；candidate 模式依目前 close 距離頸線：
   distance_to_neckline = abs(current_close - current_neckline) / current_neckline
   breakout_score = max(0, 1 - distance_to_neckline / 0.10) * 20

pattern_score = 四項加總

## 第十步：輸出欄位

symbol、timeframe、pattern_type = "inverse_head_and_shoulders"、mode、pattern_score、
LS_time、LS_price、N1_time、N1_price、H_time、H_price、N2_time、N2_price、RS_time、RS_price、
shoulder_diff、head_depth、neckline_angle、neckline_slope_pct_per_bar、current_close、
current_neckline_price、distance_to_neckline、breakout_detected、breakout_time、breakout_price、volume_ratio

## 第十一步：排序

pattern_score 由高到低；同分時依序：breakout_detected = True 優先 → distance_to_neckline 較小 →
volume_ratio 較高 → timeframe 1w > 1d > 4h

## 第十二步：程式結構

find_swing_points(df, pivot_window)、detect_inverse_head_shoulders(df, timeframe, config)、
calculate_neckline(N1, N2)、calculate_pattern_score(...)、scan_symbols(symbol_data, config)、
export_results(results, output_path)

config 包含：pivot_window_by_timeframe、shoulder_diff_limit_by_timeframe、
min_head_depth_by_timeframe、max_neckline_angle、angle_scale、breakout_buffer_by_timeframe、
enable_breakout_filter、enable_volume_filter、min_volume_ratio、min_pattern_score

## 第十三步：重要限制

不用 LLM API、不用圖片辨識、不用主觀文字判斷；所有篩選來自 OHLCV 數學規則；
結果可重現、可回測、可調參。

## 第十四步：驗收條件

1. 能從 OHLCV DataFrame 找出 swing high / swing low
2. 能找出符合 LS → N1 → H → N2 → RS 的頭肩底候選
3. 4h 的左右肩差異率限制為 5%
4. 1d / 1w 的左右肩差異率限制為 10%
5. 頸線角度限制為正負 30 度
6. 可以輸出 candidate 模式結果
7. 可以選擇是否啟用 breakout 模式
8. 可以選擇是否啟用 volume filter
9. 結果可以依 pattern_score 排序
10. 程式碼清楚、可維護、可調參
