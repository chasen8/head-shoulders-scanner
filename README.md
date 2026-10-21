# 頭肩底V2（IHS Scanner）

純 OHLCV 數學規則的頭肩底（Inverse Head and Shoulders）掃描器，支援多 symbol × 4h/1d/1w；
兩個市場：加密貨幣（多交易所，D22）與台股（上市＋上櫃普通股，D29，見「台股」一節）。
另有收斂三角（上升／下降／對稱三角，D38，見「收斂三角」一節），在同一次掃描中產生，網站從 ☰ 選單進入。
規格：`docs/spec.md`；規格詮釋與補充：`docs/project-charter.md` 決策 D3–D12。
資料來源是各交易所**公開**行情端點（不需金鑰；見下方「資料來源」），只用已收盤 K 線，不 repaint。

## 怎麼跑

```bash
# Git Bash，在專案根目錄
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe -m pytest -q
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe scan.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --timeframes 4h,1d,1w --mode candidate
```

CLI 參數：

| 參數 | 預設 | 說明 |
|---|---|---|
| `--symbols` | 10 個主流 USDT 對 | 逗號分隔 |
| `--timeframes` | `4h,1d,1w` | |
| `--profile` | `template` | 規則組（D23）：`template`＝依使用者 5 個範例校準（含右肩形成中、TP1/TP2），`classic`＝D19/D21 規則 |
| `--mode` | `candidate` | `candidate`＝watchlist＋confirmed；`breakout`＝只要已突破 |
| `--volume-filter` | 關 | 突破需 volume_ratio ≥ `min_volume_ratio` |
| `--limit` | 1000 | 每個 symbol/timeframe 的已收盤 K 線數（>1000 自動分頁） |
| `--min-score` | 0 | 覆寫 `min_pattern_score` |
| `--include-stale` | 關 | 也輸出過期型態（回測用） |
| `--output` | `C:\trading-data\ihs-scanner-v2\output\results_<UTC>.csv` | `.csv` 或 `.json` |
| `--no-cache` | 關 | 不寫 K 線快取（預設寫到 `C:\trading-data\ihs-scanner-v2\data`） |
| `--rules-off` | 無 | 關掉規則做比較，例如 `R1,R3` 或 `V1,V4`；可選 D21 的 R1、R11、R3、R2、R21 與 D26 形狀驗證 V1、V2、V3（含 a/b/c）、V4；`all`＝全關 |

程式內使用：

```python
from ihs_scanner import detect_inverse_head_shoulders, scan_symbols, export_results
res = detect_inverse_head_shoulders(df, "4h", {"enable_breakout_filter": True}, symbol="BTCUSDT")
```
`df` 欄位：`timestamp`（UTC）、`open`、`high`、`low`、`close`、`volume`，只放已收盤 K 線。
結果是「截至 df 最後一根」的狀態；`df[:t+1]` 就是 t 時刻的即時答案（可直接拿來回測）。

## 網站

```bash
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py            # 預設來源：幣安現貨＋OKX 現貨／永續＋Gate 永續
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --sources binance-spot,okx-perp
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --symbols BTCUSDT,ETHUSDT --include-stale
cd site && /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe -m http.server 8000
```

| 參數 | 預設 | 說明 |
|---|---|---|
| `--sources` | `default` | 逗號清單，可選 `binance-spot,binance-perp,okx-spot,okx-perp,gate-perp`；`default`＝除 `binance-perp` 外全部，`all`＝全部 |
| `--symbols` | `all` | `all`＝所選來源的全部 USDT 商品，排除穩定幣／法幣基底（`data_binance.EXCLUDED_BASE_ASSETS`）與槓桿代幣；給逗號清單＝只掃這些幣安現貨代碼（除錯用，忽略 `--sources`） |
| `--timeframes` | `4h,1d,1w` | |
| `--limit` | 1000 | 每組已收盤 K 線數；`1w` 上限 500（`LIMIT_CAP_BY_TIMEFRAME`） |
| `--min-quote-volume` | 0 | >0 時用 24h ticker（weight 80）過濾幣安現貨 24h 成交額（USDT）低於此值的幣；其他來源不過濾（小幣要掃） |
| `--workers` | 4 | 抓取執行緒（最多 4）；各來源各有自己的節流器（幣安 weight、OKX 12 次/秒、Gate 10 次/秒） |
| `--weight-threshold` | 4500 | 本分鐘已用 weight（讀 `X-MBX-USED-WEIGHT-1M`）再加下一個請求會超過此值就等到下一分鐘；IP 上限 6000 |
| `--include-stale` | 關 | 也輸出過期型態（本機預覽用） |
| `--out` | `site/data/scan.json` | bars 檔寫在同目錄的 `bars/` |
| `--scheduled` | 關 | 寫入 `next_scan_at`（`GITHUB_EVENT_NAME=schedule` 時自動） |

輸出：`site/data/scan.json`（metadata＋每筆摘要，含 `scanned_symbols`、`failures`、`skipped`）與
`site/data/bars/<檔名>.json`（每筆型態的 K 線、五點、頸線端點、突破點；網頁點選時才載入）。
bars 先寫到 `bars.tmp/` 再整個換上，舊檔不會殘留。

部分成功：個別 symbol/timeframe 抓取或偵測失敗只記在 `failures`（symbol、timeframe、stage、error）；
失敗比例超過 20% 才非零退出且不覆寫舊的 scan.json／bars。剛上架、還沒有已收盤 K 線的組合記在 `skipped`，不算失敗。
速率：klines 每次 weight 2（任何 limit；2026-10 官方文件與回應 header 實測）、exchangeInfo 20；
遇 429/418 依 `Retry-After` 退避（超過 300 秒視為失敗）。

網站功能（`schema_version` 6）：
- 每筆結果多兩類欄位：`pattern_key`（與 Discord 去重同一個函式 `ihs_scanner/keys.py`，不含 RS）、
  `neckline_change_pct`（N2/N1 − 1）與 `neckline_trend`（`down`／`flat`／`up`，門檻 `NECKLINE_TREND_THRESHOLD`＝0.03，也寫在 `settings.neckline_trend_threshold`）。
- 清單可再依「頸線斜率」篩選（網址 `?neck=down|flat|up`）；每列顯示「頸線 −6.2%」。
- 星號加入「觀察名單」、左上角 ☰ 抽屜（全部型態／觀察名單／設定／關於）。觀察名單與設定存在瀏覽器 localStorage
  （`ihs.watchlist.v1`、`ihs.settings.v1`），不上傳；換裝置用設定裡的匯出／匯入。儲存被停用時網站照常運作（只是不會記住）。
- 網站上狀態 `watchlist` 顯示為「右肩已確認」（JSON 值不變），避免和「觀察名單」混淆；Discord 文字同步。

- 兩個市場（D29）：頁首「加密貨幣／台股」切換與 ☰ 抽屜裡的兩個入口；網址 `?market=tw`。各讀各的 `data/scan.json`、`data/tw/scan.json`，
  頁首顯示該市場的更新時間與範圍；觀察名單同時收兩個市場、每筆標市場；預設篩選兩個市場共用。

設計與維護規則：`docs/site-design.md`。GitHub Actions：`.github/workflows/scan-and-notify.yml`（定時掃描＋Discord 通知，見下節）。

## 收斂三角（D38）

`ihs_scanner/triangles.py`（偵測）＋`config.TRIANGLE_CONFIG`（與頭肩底設定分開，`triangle_config(overrides, market)` 合併；
台股 4h 長度上限 120）。規則與數值：`docs/current-rules.md`「收斂三角」；設計依據：`docs/triangle-research.md`。

- `build_site.py`（含 `--market tw`）在頭肩底寫完後，用**同一批 K 線**跑三角形（`run_triangles`），輸出
  `site/data/triangles.json`＋`site/data/tri_bars/`（台股 `site/data/tw/triangles.json`＋`tri_bars/`）。三角形這一步
  **永遠不會讓頭肩底失敗**：例外只印 `[triangles FAIL]`；個別序列失敗記在 triangles.json `failures`（stage `triangle`）；
  失敗超過 20% 或整步出錯就不覆寫舊的 triangles.json／tri_bars。`detect_seconds`、`detect_ms_per_series`（mean／p95／max）記錄耗時。
- 每筆欄位：`pattern_type`="triangle"、`triangle_type`（ascending／descending／symmetrical）、`bias`（up／down／neutral，只是標籤）、
  `status`（forming／breakout_up／breakout_down／stale）、`breakout_direction`、起訖時間、阻力／支撐線兩端（time, price）、
  `touches`（time, price, side）、`apex_time`／`apex_price`／`apex_progress`、`height_start(_pct)`、目前價與距兩線 %、
  `breakout_time`／`breakout_price`、`target`、`target_conservative`（Bulkowski 達標比例）、`invalidation_price`、`score` 與六個分項、
  `pattern_key`＝`tri|交易所|市場|代號|週期|起點時間`。
- tri_bars 檔：K 線、`resistance_line`／`support_line`（型態內）與 `*_ext`（延伸到頂點，最多一個型態長度）、`future_times`（延伸用的未來 K 線時間）、
  觸碰點、突破、`levels`（目標／保守目標／失效）。
- 發布：`scripts/merge_published.py` 把 `data/triangles.json`、`data/tri_bars` 歸加密貨幣（台股的在 `data/tw/` 內），兩個市場互不覆蓋。
- 網站：☰ →「收斂三角」（依目前市場），網址 `?view=triangles[&market=tw]`，篩選類型／狀態／週期、排序、搜尋；
  詳細頁畫阻力線（紅）、支撐線（綠）延伸到頂點、觸碰點、突破、目標與失效價。**暫不提供**觀察名單星號與 Discord 通知。

## config 參數（`ihs_scanner/config.py`，可部分覆寫）

兩個規則組（D23）共用同一組 key：`profile="template"`（預設，`TEMPLATE_CONFIG`）與 `profile="classic"`
（D19/D21 行為，`CLASSIC_CONFIG`）。`merge_config({"profile": "classic", ...})` 先選規則組再套覆寫；
template 與 classic 不同的值全列在 `TEMPLATE_OVERRIDES`。下表「預設」欄是 classic 值，template 不同時寫在「template」括號裡。

| key | 預設 | 說明 |
|---|---|---|
| `profile` | `template` | `template` 或 `classic` |
| `pivot_window_by_timeframe` | 4h 5、1d 5、1w 3 | swing 左右各 N 根，嚴格不等式；確認延遲 N 根 |
| `shoulder_diff_limit_by_timeframe` | 4h 0.05、1d/1w 0.10（template：None＝關） | 左右肩低點價格差異率上限（規格第三步）；None＝關 |
| `min_head_depth_by_timeframe` | 4h 0.03、1d/1w 0.05 | 最小頭部深度 |
| `max_neckline_angle` / `angle_scale` | 30 / 100（template：4h 30、1d 30、1w 45） | `atan(slope_pct_per_bar × scale)` 度數上限；可分 timeframe（D36：週 K 一根 7 天，同樣 %/根 在時間上比日線陡 7 倍） |
| `breakout_buffer_by_timeframe` | 4h 0.005、1d/1w 0.01 | 突破 = `breakout_price` 指定的價格 > 頸線 × (1+buffer) |
| `breakout_price` | `close`（template：`high`） | D36：`high`＝RS 之後某根最高價（含影線）穿過頸線就算突破；`close`＝收盤（D8）。突破時間、第一次穿越（D14）、signal_time、狀態年齡、V3c、TP2 的 breakout_level 都用它；T4／V3a（型態成立前）不變 |
| `enable_breakout_filter` | False | True＝breakout 模式 |
| `enable_volume_filter` / `min_volume_ratio` | False / 1.2 | 突破量比門檻 |
| `volume_avg_window` | 20 | 均量＝突破 K **之前** 20 根（不含自身） |
| `min_pattern_score` | 0 | 低於此分數不輸出（scan_symbols 套用） |
| `max_pattern_bars_by_timeframe` | 4h 150、1d 120、1w 80（template：1000／600／200） | LS→RS 最大根數 |
| `min_pattern_bars` | None（template：4h 60、1d 40、1w 16） | LS→RS 最小根數；int 或分 timeframe dict |
| `enable_balance_rule` / `max_time_symmetry_ratio` | True / 2.5（template：False / None） | D19 balance、時間對稱 |
| `max_bars_after_rs` | 30 | 未突破時 RS 後超過此根數 → stale |
| `max_breakout_age` | 5 | 突破後超過此根數 → stale |
| `include_stale` | False | 是否輸出 stale |
| `ideal_head_depth` | 0.10 | 頭深分數滿分點 |
| `breakout_distance_range` | 0.10（template：0.30） | 距頸線分數歸零距離 |
| `min_head_atr` / `atr_length` | 1.0 / 14（template：0.5） | D21 R1：`H.low ≤ min(LS.low, RS.low) − k·ATR[H]`，Wilder ATR，只用 ≤ H 的 K 線；ATR 尚無值時略過；None＝關 |
| `enable_pattern_start_rule` | True（template：False） | D21 R11：LS 前 L 根內須有一根價格 > 頸線向左延伸值（neurotrader888），找不到或資料不足 → 拒絕 |
| `pattern_start_lookback` | None | R11 回看根數 L；None＝`N2.index − N1.index − 1`（原始碼 `range(1, head_width)`） |
| `pattern_start_price` | `high` | R11 比較用的價格：`close`（同原始碼）或 `high` |
| `max_neckline_total_slope` | 0.15（template：None） | D21 R3：`\|頸線每根斜率 × (RS.index − LS.index)\| / close[H] ≤ 值`；與角度規則並存；None＝關 |
| `max_shoulder_diff_of_head` | 0.5 | D21 R2：`\|LS.low − RS.low\| ≤ 值 × head_height`（SignalSync 原值 0.30）；與 % 肩差並存；None＝關 |
| `min_shoulder_depth_ratio` / `max_shoulder_depth_ratio` | 0.25 / None | D21 R21：`mean(N1.high − LS.low, N2.high − RS.low) / head_height` 的下限／上限；None＝關 |

D23 template 規則（classic 全關；符號：`drop_L=(N1.high−LS.low)/N1.high`、`drop_R=(N2.high−RS.low)/N2.high`、
`head_rise=neck(H)/H.low−1`、`rise_L=N1.high/LS.low−1`、`rise_R=N2.high/RS.low−1`、`head_height=neck(H)−H.low`）：

| key | template | 說明 |
|---|---|---|
| `min_shoulder_drop_by_timeframe` | 4h 0.06、1d 0.08、1w 0.10 | T1 兩肩從各自頸線高點的跌幅下限 |
| `max_shoulder_drop_diff` / `max_shoulder_drop_rel_diff` | 0.05 / 0.25 | T2 肩膀對稱：`\|drop_L−drop_R\|` ≤ 0.05，且 ≤ 0.25×max(drop_L, drop_R) |
| `min_head_rise_by_timeframe` | 4h 0.12、1d 0.15、1w 0.20 | T3 頭到頸線漲幅下限 |
| `min_head_prominence` | 1.1 | T3 `head_rise / max(rise_L, rise_R)` 下限（頭要比肩明顯深） |
| `max_neckline_violation` | 0.01 | T4 N1 與 RS 之間（N2 除外）每根 high ≤ 頸線×1.01 |
| `max_neckline_tilt_of_head` | 0.75 | T5 `\|N2.high−N1.high\|` ≤ 0.75×head_height（取代 R3） |
| `enable_forming_rs` | True | T6 右肩形成中：N2 已確認後，N2 之後至今最低 low 若仍在 pivot 確認窗內（index > last−N），以 `status="forming"` 輸出 |
| `require_n1_first_high_after_ls` | True | T7 N1 是 LS 之後第一個 swing high，且 LS 是 [LS, N1] 最低 low（LS＝衝向 N1 那段的起漲底） |
| `head_lead_in` | 0.5 | T8 LS 前 ceil(0.5×(H−LS)) 根內不得有低於 H 的價格（資料不足不算失敗） |
| `head_lead_in_price` | `body`（classic：`low`） | D36：T8 比較用 `low` 或實體低點 `min(open, close)`（單根閃崩長下影不算「價格曾低於頭部」，例 LDO 1w 2025-10-06） |
| `reject_post_breakout_rs` | True（classic：False） | D36 突破優先：同一個 H 的另一個有效候選 A（形狀規則＋D7 都通過，狀態不論）在 c 根第一次穿越頸線，RS 在 c 之後的候選 B 不成立（B 是 A 突破後的回測、N2 常是突破後高點）；例外：A 的突破失敗（c～RS_B 之間 low < A.RS）或 B 的頸線排名較好（下斜優於上斜，再比碰頸線次數多） |
| `neckline_touch_tolerance` | 0.01 | D36 碰頸線：N1 與 RS 之間（N2 除外）high 落在頸線 ±1% 內的 K 線；連續的算一次、緊貼 N1/N2 的不算 → `neckline_touches` |
| `v1_ignore_neckline_touches` | True（classic：False） | D36：V1 把碰頸線當成頸線上的額外頂點，在那裡切開該段（漲到頸線再回落不算反向波動；切開後各小段內的反向波動照算） |
| `dedup_priority` | `breakout_touches_slope`（classic：`score`） | D36 去重順序：已突破 → 碰頸線次數多 → 下斜（N2<N1）優於水平／上斜 → 分數（D11） |
| `supersede_by_higher_timeframe` | True（classic：False） | D36 跨週期（scanner／build_site 彙整階段）：同幣同來源，1w 有下斜頸線型態且 [LS, RS] 涵蓋 1d 型態的 H，而 1d 頸線上斜 → 1d 不輸出，記在 scan.json `superseded`（`SUPERSEDE_PAIRS` 目前只有 1w→1d） |
| `max_neckline_rise` | 0.10 | T9 上升頸線上限 `N2.high/N1.high−1` |
| `max_neckline_wick_of_head` | 0.32 | T10 N1/N2 不可是孤立長上影：(high − ±N 根內實體最高) / head_height |
| `score_method` | `template` | `spec`（規格第九步）或 `template`（見下） |
| `ideal_head_prominence` / `ideal_head_rise` | 1.5 / 0.40 | template 分數滿分點 |

template 分數（0–100，分項欄位名同規格）：`shoulder_score = 30×max(0, 1−|drop_L−drop_R|/0.05)`；
`head_depth_score = 12.5×clip((prominence−1)/(1.5−1)) + 12.5×clip(head_rise/0.40)`；
`neckline_score = 25×max(0, 1−tilt_of_head/0.75)`；`breakout_score` 已突破 20，否則 `20×max(0, 1−距頸線/0.30)`。
右肩形成中時價格多在頸線下 20–30%，breakout_score 通常很低，所以 template 分數普遍比 classic 低（範例 53–71 分）。

`max_bars_after_rs`、`max_breakout_age` 也可給 `{"4h": .., "1d": .., "1w": ..}`。

D26 形狀驗證（template 開、classic 全關；數值兩種 profile 都照算並輸出；公式與出處完整寫在 `config.py`）。
符號：z＝自然對數價，`head_height_log = log(neck(H)) − log(H.low)`。V1、V2、V3a、V4 是**過濾**（全部通過才輸出，`shape_valid`）；
V3b、V3c 不判形狀，只把狀態改成 `stale`（`stale_reason`）：

| key | template | 說明（出處） |
|---|---|---|
| `max_counter_swing_of_head` | 0.65 | V1 主要轉折（Lo, Mamaysky & Wang 2000「E1..E5 為連續極值」；Bulkowski 避免 multi-peak）：LS→N1、N1→H、H→N2、N2→RS 每段內部（不含兩端 K 線）最大反向波動，下跌段＝`max_{i<j} log high[j] − log low[i]`、上漲段＝`max_{i<j} log high[i] − log low[j]`，除以 head_height_log。範例 0.44–0.62 |
| `max_skeleton_mad_of_head` | 0.20 | V2 骨架貼合（neurotrader888 `compute_pattern_r2`，MIT）主指標：`log((high+low)/2)` 對五點折線（forming 再接到最後一根）的平均絕對偏差 / head_height_log。範例 0.137–0.178 |
| `min_skeleton_r2` | None | V2 輔助：同一折線的 R²＝1−SS_res/SS_tot，只顯示；範例只有 −0.20～0.37，不能當過濾 |
| `max_neckline_close_violation` | 0.005 | V3a N1 與 RS 之間（N2 除外）每根 `close/neck − 1` 的最大值上限（T4 管 high 1%，V3a 管收盤 0.5%） |
| `max_runaway_of_head` | 0.5 | V3b 追高上限（SignalSync MAX_RUNAWAY 概念）：`(close − neck(now)) / head_height` 超過 → `stale`（`runaway`） |
| `max_fallback_of_head` | 0.25 | V3c 突破失敗：曾穿越頸線（`first_cross_time`，依 `breakout_price`）後 `(neck(now) − close) / head_height` 超過 → `stale`（`failed_breakout`）；小幅回測頸線不算 |
| `max_template_distance` | 0.25 | V4 範例模板距離（Fu et al. 2007 振幅／時間距離）：`y'_k=(z_k−z_H)/(z_neck(H)−z_H)`、`x'_k=(t_k−t_LS)/(t_RS−t_LS)`，`D_j = 0.8·AD_j + 0.2·TD_j`，`D = min_j D_j`；範例 leave-one-out 0.096–0.191 |
| `template_amplitude_weight` | 0.8 | V4 的 w |
| `template_exclude` | `[]` | V4 不比對的範例名稱（leave-one-out 測試用） |
| `shape_score_weight` | 0.0 | `pattern_score = (1−w)·base_score + w·shape_score`；預設 0＝分數不變（理由見 `config.py`） |

範例模板：`ihs_scanner/templates.py`，由 `scripts/build_templates.py` 從 `tests/golden_examples.py` 的點位＋fixture 產生
（`--check` 只比對）。`shape_score = 100×(0.5·clip(1 − V1/0.65) + 0.5·clip(1 − V4/0.25))`：0＝卡在門檻，
100＝中途無反向波動且和範例相同；使用者 5 個範例本身約 15–40 分，所以它是「離門檻多遠」，不混進 `pattern_score`。

## 輸出欄位

規格第十步全部欄位，外加：

- `profile`：`template` 或 `classic`
- `status`：`forming`（template：右肩形成中，RS＝N2 之後至今最低點，尚未被 pivot 確認，**每根新 K 線都可能改變或消失**；
  任何 low 跌破 H 即作廢）／`watchlist`（未突破）／`confirmed`（已突破且夠新）／`stale`
- D23 欄位（比例皆為小數，0.244＝24.4%）：`drop_left_pct`、`drop_right_pct`、`shoulder_drop_diff`、`head_rise_pct`、
  `head_prominence`、`neckline_tilt_of_head`、`neckline_max_violation`、`neckline_change`、`neckline_wick_of_head`、`head_height`
- D36：`neckline_touches`（N1、N2 之外碰頸線次數）、`breakout_basis`（`high`／`close`）；`breakout_price` 是突破那根被拿來比的價格（`high` 模式為最高價）
- 目標價（只是顯示資訊）：`breakout_level`＝已突破時突破那根的頸線價，否則目前這根的頸線價（預估）；
  `tp1 = H.low × (neckline_mid / H.low) ^ 1.414`（D28：頭部到頸線中點的對數斐波那契 1.414，`tp1_fib_level`／`tp1_fib_log` 可調；`neckline_mid`＝N1、N2 時間中點的頸線價，不隨突破移動）；`tp2 = breakout_level × (1 + head_rise_pct)`（使用者圖上算法）；
  `invalidation_price = H.low`
- `shoulder_score`、`head_depth_score`、`neckline_score`、`breakout_score`：四項分數；`base_score`＝四項總和（D26 前的 pattern_score）
- `stale_reason`：`age`（D9 時間窗）／`runaway`（V3b）／`failed_breakout`（V3c）；非 stale 為空
- D26 形狀驗證（比例的單位是「頭高」）：`v1_max_counter_swing`、`v1_leg`（最大反向波動所在段，如 `N1-H`）、`v2_mad`、`v2_r2`、
  `v3_close_violation`（小數，0.005＝高出頸線 0.5%）、`v3_runaway`（負＝在頸線下）、`v3_fallback`（未曾穿越頸線為空）、
  `v4_template_distance`、`v4_nearest_template`、`shape_valid`（classic 沒有啟用任何檢查時為空）、`shape_score`
- `RS_confirm_time`：右肩 pivot 被確認的 K 線時間（= RS_time + N 根）；`forming` 為空
- D21 規則數值（規則關掉時也照算）：`atr_at_head`、`head_atr_multiple`（(min(LS,RS) − H)/ATR）、
  `neckline_total_slope`、`shoulder_diff_of_head`、`shoulder_depth_ratio`、
  `pattern_start_bars_before_ls`（R11 型態起點在 LS 前幾根；找不到為空）
- `signal_time`：此訊號最早可被「即時」知道的時間＝max(RS_confirm_time, breakout_time)（`forming`：max(RS_time, N2 確認時間)）；
  回測進場請用這個，不要用 breakout_time（突破可能發生在 RS 確認前）
- `average_volume_20`、`bars_since_rs`、`bars_since_breakout`、`as_of_time`（計算基準 K 線）
- `pattern_id`：`exchange|market|symbol|tf|LS_time|H_time|RS_time`，給通知去重用（前綴避免跨來源撞號）
- `exchange`（binance/okx/gate）、`market`（spot/perp）、`tv_symbol`（TradingView 代碼，如 `BINANCE:HMSTRUSDT`、`OKX:TRUSTUSDT.P`、`GATEIO:AZTECUSDT.P`）

時間一律 ISO 8601 UTC（`2026-01-01T00:00:00Z`）；未突破時 `breakout_*`、`volume_ratio` 為空（JSON 為 `null`）。
排序：pattern_score ↓ → 已突破優先 → distance_to_neckline ↑ → volume_ratio ↓ → 1w > 1d > 4h。

## 結構

```
ihs_scanner/  config.py  swings.py  neckline.py  scoring.py  detector.py  scanner.py  data_binance.py
              sources.py  data_binance_futures.py  data_okx.py  data_gate.py   （多交易所來源）
              validation.py  templates.py   （D26 形狀驗證 V1–V4；templates.py 由腳本產生）
              triangles.py                   （D38 收斂三角，設定在 config.TRIANGLE_CONFIG）
scan.py       CLI
scripts/fetch_golden_fixtures.py   下載黃金範例 K 線到 tests/fixtures/（公開端點：幣安現貨／USDT-M、OKX）
scripts/build_templates.py         從黃金範例產生 ihs_scanner/templates.py（V4 模板）；--check 只比對
tests/        pytest（不碰網路；截斷／不 repaint 測試在 test_truncation.py、test_template.py、test_golden_examples.py、test_golden_d36.py）
```

黃金測試（D23）：`tests/golden_examples.py` 是使用者 5 個範例（時間已換成 UTC），`tests/test_golden_examples.py`
把 K 線截到截圖當下最後一根已收盤 K 線，用預設 template 偵測並核對點位；`pytest -s tests/test_golden_examples.py`
會印出偵測點位、drop／head_rise、TP 與使用者標註對照。`test_golden_shape_validation_leave_one_out`（D26）
在排除該範例自己的模板後再偵測，要求 V1–V4 全過，`-s` 會印出每條驗證的值、門檻與餘裕。
D36 三個案例（`D36_CASES`：BTCUSDT 1d 突破優先、ATUSDT 4h 影線突破、LDOUSDT 1w／1d 下斜週線頸線）在
`tests/test_golden_d36.py`（含截斷／不 repaint）；不加進 `GOLDEN_EXAMPLES`，因為那 5 個範例同時是 V4 模板。

## 資料來源（D22）

全部是公開行情端點、無金鑰、只用已收盤 K 線。2026-10-18 在 GitHub Actions（美國 IP）實測：
幣安現貨 `data-api.binance.vision` 200；幣安永續 `fapi.binance.com` **451**（被擋）；OKX 200；Gate 永續 200；
Bybit 403；MEXC、Bitget 200。

| `--sources` 名稱 | 端點 | 節流 | 備註 |
|---|---|---|---|
| `binance-spot` | `data-api.binance.vision/api/v3` | 讀 `X-MBX-USED-WEIGHT-1M`，4500/6000 | 主來源，列表失敗＝整批中止 |
| `binance-perp` | `fapi.binance.com/fapi/v1`（PERPETUAL＋USDT＋TRADING） | weight 2400/min，門檻 1800 | **預設關閉**：美國 IP 被擋，只能在非美國網路用 |
| `okx-spot` | `/api/v5/market/candles`（`4H`、`1Dutc`、`1Wutc`，每次 ≤300 筆，`after` 往回翻頁） | 12 次/秒；429／錯誤碼 50011 指數退避 | quoteCcy=USDT、state=live |
| `okx-perp` | 同上，`<BASE>-USDT-SWAP`、linear、live | 同 OKX（共用節流器） | |
| `gate-perp` | `api.gateio.ws/api/v4/futures/usdt/candlesticks`（一次最多 2000 筆） | 10 次/秒；429 退避 | 補「只有合約」的小幣；成交量單位是合約張數 |

同一個 base 幣只掃一個來源，優先序：`binance-spot` → `binance-perp` → `okx-spot` → `okx-perp` → `gate-perp`
（`1000PEPE` 與 `PEPE` 視為同一幣）。不用成交量門檻過濾小幣。非主來源列表失敗記在 `scan.json` 的
`source_errors`、不中止；單一 symbol 抓取失敗記在 `failures`（含 `exchange`／`market`）。
`scan.json` 的 `source_counts` 列每個來源的 `listed`（去重前）、`symbols`（實際掃描）、`scanned_symbols`、
`series_scanned`、`failed_series`。

## 台股（D29）

加密貨幣之外的第二個「市場」：台灣**上市＋上櫃普通股**，偵測器、template profile、形狀驗證、TP1/TP2、forming 狀態全部共用，
門檻沒有另外調（見 charter D29）。程式：`ihs_scanner/data_tw.py`（清單、Yahoo、重採樣）、`build_site_tw.py`（建置），
入口一樣是 `build_site.py`：

```bash
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --market tw                    # 全部普通股
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --market tw --symbols 2330,6488 --include-stale
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --market tw --tw-include-etf    # 加 ETF
```

| 參數 | 預設 | 說明 |
|---|---|---|
| `--market` | `crypto` | `tw`＝台股，輸出到 `site/data/tw/scan.json`＋`site/data/tw/bars/`（不碰加密貨幣的 `site/data/scan.json`） |
| `--tw-include-etf` | 關 | 也掃 ETF（`00` 開頭代號）；預設只掃普通股 |
| `--tw-rps` | 5 | Yahoo 每秒請求數（所有執行緒合計） |
| `--symbols` | `all` | 台股時是股票代號清單，例如 `2330,6488`（除錯用） |
| `--timeframes`、`--limit`、`--workers`、`--include-stale`、`--scheduled`、`--out` | 同加密貨幣 | `1w` 上限 500 根 |

**資料來源**（全部公開、不需金鑰、不需註冊）：

| 用途 | 端點 | 備註 |
|---|---|---|
| 上市清單與中文簡稱 | `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | `Code`、`Name` |
| 上櫃清單與中文簡稱 | `www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes` | `SecuritiesCompanyCode`、`CompanyName`；回應較慢（runner 上約 15 秒） |
| 日 K（→ 日線、週線） | Yahoo `query1.finance.yahoo.com/v8/finance/chart/<代號>.TW（上櫃 .TWO）?interval=1d&range=10y` | |
| 60 分 K（→ 4 小時線） | 同上 `interval=60m&range=730d` | Yahoo 60 分 K 最長約 730 天 → 4h 約 980 根 |

- **範圍**：4 位數、非 0 開頭的代號，去掉名稱以 `-DR` 結尾的存託憑證。這條規則同時排除 ETF（`00xx`）、ETN（`02xxxx`）、
  權證（6 位數）、特別股（`2881A`）、受益證券（`01xxxT`）。2026-10-19 實測上市 1082＋上櫃 891＝1973 檔。
- **Yahoo 在 GitHub runner（美國 IP）上沒有 cookie 會回 429**；先開 `finance.yahoo.com` 拿 cookie、再拿
  `/v1/test/getcrumb` 的 crumb 帶在每個請求上就是 200（2026-10-19 probe）。`YahooClient` 自動做這件事，遇 429／401／403
  會重拿 crumb 並指數退避（最多 5 次）；每檔 2 次請求（日 K、60 分 K），節流 5 次/秒、4 個執行緒。
- **價格**：Yahoo 原始報價（已還原分割、**未還原權息**），與 TradingView 台股預設相同；所以除權息當天會有缺口。網站與 Discord
  一律顯示台幣兩位小數。
- **只用已收盤 K 線**：台北 13:30 收盤＋30 分緩衝（`CLOSE_BUFFER`）之後才算收盤。時間一律存 UTC：日 K 時間＝當天 09:00 台北
  （01:00 UTC），週 K＝該週週一 09:00 台北，週 K 要等週五 13:30＋緩衝之後才算收盤（週五放假也一樣）。
- **週線**＝由日 K 重採樣（週一為一週開始，開＝第一個交易日開盤、收＝最後一個交易日收盤），不多打一次 API。
- **4 小時線的特性**：台股一天只交易 4.5 小時，所以照 TradingView 台股的切法，一天兩根：**09:00–13:00（4 小時）與
  13:00–13:30（半小時）**，長短不一；一週約 10 根、730 天約 980 根。Yahoo 60 分 K 有兩個毛病，用同一天的日 K 修正：
  (1) 09:00 那小時的量常常是 0、13:25–13:30 收盤集合競價的量也不在小時資料裡 → 日量 − 小時量合計的差額，
  第一小時量為 0 時補到 09:00 那根，否則補到 13:00 那根；(2) 最後一小時的收盤是集合競價前的價格 → 13:00 那根的收盤改成日 K 收盤
  （高低點必要時放寬）。因為午盤那根只有半小時，4h 的 pivot 窗、`max_bars_after_rs`（30 根≈3 週）等根數門檻在台股代表的時間比加密貨幣長。
- **部分成功**同 D18：個別股票失敗記 `failures`，失敗率 >20% 才非零退出且不覆寫舊的 `site/data/tw/`；上市或上櫃其中一邊清單抓不到
  記在 `source_errors`，另一邊照掃。
- **輸出**：`scan.json` 頂層 `"market": "tw"`、`settings.universe`（`common` 或 `common+etf`）、`settings.currency` `TWD`；
  每筆多 `name`（中文簡稱），`exchange` 為 `twse`／`tpex`、`market` 為 `stock`、`tv_symbol` 為 `TWSE:2330`／`TPEX:6488`，
  `pattern_key`／`pattern_id` 以 `twse|stock|…`、`tpex|stock|…` 開頭，不會和加密貨幣撞號。
- **排程**：`.github/workflows/scan-tw.yml`，平日 UTC 06:10（台北 14:10，收盤後 40 分），也可手動（`dry_run`、`notify_all`）。
  國定假日照跑，只是沒有新 K 線。
- **實測**（2026-10-19，本機）：1973 檔、5863／5919 組 K 線、3946 次 Yahoo 請求、0 次 429；抓取 793 秒（5 次/秒節流決定）＋
  偵測約 110 秒＝約 15 分鐘。失敗的 56 組都是 4h：上市未滿約 2.4 年的股票對 `range=730d` 回 HTTP 422，已改成自動退回 `2y`／`1y`。
  結果 663 個型態（4h 299、1d 258、1w 106；形成中 380、右肩已確認 228、已突破 55），scan.json 約 3 MB、bars 約 10 MB。
- **Discord**：同一個 webhook；標題「台股 2330 台積電 日線 右肩形成中」、欄位「市場：台股上市／台股上櫃」、價格 `NT$1,005.00`；
  狀態檔獨立 `state_tw.json`（scan-state 分支），第一次執行只發一則「台股頭肩底掃描」摘要。

## 定時掃描與 Discord 通知

`.github/workflows/scan-and-notify.yml`：每 4 小時（UTC，`7 */4 * * *`，4h 收盤後 7 分鐘；也涵蓋 1d、1w 收盤）
跑 pytest → `build_site.py --scheduled` → `notify_discord.py` → 上傳 `site/` 與 `state.json` artifact（保留 7 天）
→ publish job 把 `state.json` 與 `site/data/` 推到 `scan-state` 分支、把 `site/` 推到公開網站 repo。build_site 失敗率 >20% 時整個流程中止，
不通知、不改 state。台股是另一個 workflow `scan-tw.yml`（平日 UTC 06:10，見「台股」一節）。

**兩個市場共用發布（D29）**：公開網站 repo 與 `scan-state` 都是 force push 單一 commit，所以兩個 workflow 都改用共用的
`.github/workflows/publish.yml`：先 clone 目前公開的網站（或抓目前的 `scan-state`），用 `scripts/merge_published.py`
只換掉**自己市場**的檔案、保留另一個市場的（網站：加密貨幣＝`data/scan.json`＋`data/bars/`，台股＝`data/tw/`；
scan-state：加密貨幣＝`state.json`＋`site/data/scan.json`＋`site/data/bars/`，台股＝`state_tw.json`＋`site/data/tw/`），再推上去。
讀不到目前公開的內容時寧可失敗也不推（避免另一邊資料消失）。publish job 用共用的 concurrency group `publish-site`，
兩邊的發布不會同時進行（掃描本身可以同時跑）。

通知規則（`notify_discord.py`）：分數 ≥ 50（`--min-score` 或環境變數 `NOTIFY_MIN_SCORE`；template 形成中範例 53–71 分）時通知
(1) 新出現的「右肩形成中」(2) 任何狀態變成「已突破」——這兩種逐筆附圖；(3) **新變成「右肩確認」**（新出現，或形成中 → 右肩確認）
不再逐筆，改成每次掃描一則**表格**訊息（D34，見下節）。去重鍵是**不含 RS** 的 `exchange|market|symbol|tf|LS_time|H_time`
（形成中的 RS 會隨新低移動，用含 RS 的 pattern_id 會一直重發）；state（`version: 2`）存每個鍵的最後狀態、
最後通知時間與 `notified_status`（最後通知過的狀態）。低於門檻時只記錄不通知，之後分數過門檻會補發一次。
首次執行（沒有 state，或讀到舊版／沒有 version 的 state）只發一則「開始追蹤，目前 N 個型態（各狀態筆數）」摘要並建立基準
（`--notify-all` 才逐筆發）。所有事件**逐筆列出**（D32，不再有「另有 N 個未列出」摘要）：每則訊息最多 10 個 embed，超過就分多則依序送出（訊息間隔 ≥ 1 秒）；
遇 429 依 `retry_after` 等待，重試有上限；送失敗的下次重試。保險上限：環境變數 `NOTIFY_MAX_EVENTS`（`--max-events`，預設 200，0 = 不限），
超過的事件既不送也不記入 state，下一次執行接著送。

**圖片（D32）**：每個 embed 附一張該型態的 PNG（1200×675，約 60–80 KB；`ihs_scanner/chart_image.py`，matplotlib Agg），
只畫 bars 檔（`site/data/bars/`、`site/data/tw/bars/`，用結果的 `chart_file` 找）裡已有的值，不重算偵測：白底黑白 K 線、
LS/N1/H/N2/RS 藍點與標籤、頸線藍虛線、暫定 RS 空心標「RS?」、突破紅點、TP1/TP2 綠虛線、失效價灰虛線、底部成交量。
圖內文字一律英數（runner 沒有中文字型），中文說明在 embed。以 multipart/form-data 上傳（`payload_json` + `files[n]`，
`embed.image.url = attachment://<ascii 檔名>`，每則附件 < 7.5 MB）。bars 檔缺或畫圖失敗只是該筆不附圖（log 記錄），照常通知；
若 Discord 因附件回 4xx，該則改以不含圖的 embed 重送一次。

- `--dry-run`：不送，把會附的圖存到 `--preview-dir`（預設 `output/notify-preview/`），印每則的 embed 標題與附件檔名；workflow dry-run 時上傳成 artifact `notify-preview`（保留 3 天）。
- `--test-message`（或環境變數 `TEST_MESSAGE=true`；workflow_dispatch 輸入 `test_message`）：只送一則「測試：圖片通知」，取目前結果分數最高 1 筆、附圖，不讀不寫 state、不發布。

embed：標題「GRAMUSDT 日線 右肩形成中／已突破」、交易所與市場、分數、目前價、頸線價（距頸線%）、TP1、TP2
（未突破標「預估」）、失效價（頭部低點）、左右肩跌幅、頭到頸線漲幅；顏色 形成中橘、觀察中藍、已突破紅；標題連到 TradingView
（用 `tv_symbol`）。價格依數量級顯示位數（0.0001750）。型態從掃描結果消失超過 14 天才會從 state 清掉。

### 右肩確認表格與關注清單（D34）

設計與介面：`docs/watchlist-bot-design.md`（Discord 機器人＋Cloudflare Worker `bot/`）。

- **表格**（`ihs_scanner/table_image.py`）：每次掃描（加密貨幣每 4 小時、台股每日）把「新變成右肩確認」的型態畫成一張表（#、標的、
  週期、分數、目前價、距頸線%、頸線斜率%/根、TP1、TP2；依分數排序；每張最多 25 列，超過分多張），訊息標題
  「右肩確認（加密貨幣）2026-10-22 08:00 共 N 筆」／「右肩確認（台股）…」。沒有新的右肩確認就不發。圖內文字英數；runner 有 CJK 字型時
  台股會加中文名。
- **有機器人**（`DISCORD_BOT_TOKEN` ＋ `DISCORD_MAIN_CHANNEL_ID`）：用 `POST /api/v10/channels/{id}/messages` 發到主頻道，表格圖下方附
  下拉選單（`custom_id=watch_select`，每個最多 25 選項、每則最多 5 個選單；選項如「#3 STARUSDT 4h 83分」，value＝`pattern_key`），
  可多選，點選後 Worker 把它們加入關注清單。超過 125 筆分多則訊息。同一則訊息內 custom_id 必須唯一，所以第 2 個起的選單是
  `watch_select_2`、`watch_select_3`…。
- **沒有機器人**：同一張表格圖改用 webhook 發（沒有選單）。兩者都沒有：只記錄 state。
- **關注通知**（`notify_watchlist.py`，workflow 的「Notify watchlist changes」步驟，失敗只警告；D35 起**每人一份清單**）：需要
  `WATCHLIST_API_URL`、`WATCHLIST_SYNC_KEY`（讀 `GET /api/watchlist` → `{"users":[{user_id, notify, items}]}`）與 `DISCORD_BOT_TOKEN`、
  `DISCORD_WATCH_CHANNEL_ID`。對每個人、只看該市場的項目，用 `pattern_key` 在本次 scan.json 找目前狀態：與該人的 `last_status` 不同
  （形成中→右肩確認→已突破、任何→已過期）就通知；型態消失視為 `gone`，只通知一次；第一次看到（`last_status` 為 null）只記錄；台股缺中文名時順便補。
  同一型態的同一變化合併：選「頻道」的人合成**一則** #關注 訊息 `<@id1> <@id2>`（`allowed_mentions` 只允許這些人）；選「私訊」的人由機器人私訊，
  對方關閉私訊（403）時改在 #關注 @ 他並註明「私訊失敗」。每則都附 embed＋型態圖＋「取消關注」按鈕（`custom_id=unwatch:<pattern_key>`，
  超過 100 字元改 sha1 前 16 碼；只移除按的人自己的）。送達的人才 `POST /api/watchlist {"update":[{user_id, pattern_key, …}]}` 回寫。
  缺機器人 token→略過並 log（不記錄，補上設定後才會補發）；缺 API 設定→整個略過。通知方式在 Discord 用 `/notify`、或網站登入後在設定裡改。
- 密鑰只從環境變數讀（GitHub secrets），不會出現在任何輸出；`--dry-run` 不送、不 POST，預覽圖存到 `--preview-dir`。
  本機預覽關注通知：`python notify_watchlist.py --dry-run --users-file users.json`（`{"users":[…]}`，不連 API）。

### 你要做的步驟（一次性）

1. Discord：頻道設定 → 整合 → Webhook → 新增 Webhook → 複製 Webhook 網址。
2. 在**自己的終端機**執行（它會提示貼上網址；不要貼到對話或任何檔案）：
   ```
   gh secret set DISCORD_WEBHOOK_URL --repo chasen8/head-shoulders-scanner
   ```
   或 GitHub 網頁：repo → Settings → Secrets and variables → Actions → New repository secret，名稱 `DISCORD_WEBHOOK_URL`。
3. 沒設 secret 也能跑：只更新 state，log 印「未設定 webhook，略過通知」。
4. （D34，選配）機器人與關注清單：`DISCORD_BOT_TOKEN`、`DISCORD_MAIN_CHANNEL_ID`、`DISCORD_WATCH_CHANNEL_ID`、`WATCHLIST_API_URL`、
   `WATCHLIST_SYNC_KEY` 同樣用 `gh secret set` 設定；沒設就自動降級（見上節）。

### 手動觸發測試

```
gh workflow run scan-and-notify.yml --repo chasen8/head-shoulders-scanner -f dry_run=true     # 只印內容，不送、不更新 scan-state
gh workflow run scan-and-notify.yml --repo chasen8/head-shoulders-scanner -f test_message=true # 只送一則附圖測試訊息（D32）
gh workflow run scan-and-notify.yml --repo chasen8/head-shoulders-scanner                      # 正式（有 secret 才會發）
gh run list --repo chasen8/head-shoulders-scanner --workflow scan-and-notify.yml --limit 3
gh workflow run scan-tw.yml --repo chasen8/head-shoulders-scanner -f dry_run=true             # 台股
```
`-f notify_all=true`：沒有 state 時逐筆通知。本機試跑：`python notify_discord.py --dry-run`（讀 `site/data/scan.json` 與 `state.json`）。

### 從 scan-state 分支取最新結果在本機看網站

```
git fetch origin scan-state
git checkout origin/scan-state -- site/data      # 覆蓋本機 site/data（scan.json + bars/，以及台股的 tw/）
python -m http.server --directory site 8000      # 開 http://localhost:8000
```
（`scan-state` 每次是全新的單一 commit、force push，不累積歷史；只含 `state.json`、`state_tw.json` 與 `site/data/`。）

### Actions 用量估算

私有 repo 免費方案每月 2000 分鐘。每 4 小時一次（每月約 180 次）× 約 4–5 分鐘 ≈ 每月 700–900 分鐘，在額度內。
台股（D29）每個平日一次 × 約 20 分鐘（含測試與發布）× 每月約 22 天 ≈ 每月 440 分鐘；合計約 1150–1350 分鐘，仍在額度內。
實際耗時以 Actions 頁面為準；若接近額度，可改成每 8 小時（同時改 `build_site.py` 的 `SCAN_EVERY_HOURS`）。
