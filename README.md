# 頭肩底V2（IHS Scanner）

純 OHLCV 數學規則的頭肩底（Inverse Head and Shoulders）掃描器，支援多 symbol × 4h/1d/1w。
規格：`docs/spec.md`；規格詮釋與補充：`docs/project-charter.md` 決策 D3–D12。
資料來源是幣安**公開** K 線（不需金鑰），只用已收盤 K 線，不 repaint。

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
| `--mode` | `candidate` | `candidate`＝watchlist＋confirmed；`breakout`＝只要已突破 |
| `--volume-filter` | 關 | 突破需 volume_ratio ≥ `min_volume_ratio` |
| `--limit` | 1000 | 每個 symbol/timeframe 的已收盤 K 線數（>1000 自動分頁） |
| `--min-score` | 0 | 覆寫 `min_pattern_score` |
| `--include-stale` | 關 | 也輸出過期型態（回測用） |
| `--output` | `C:\trading-data\ihs-scanner-v2\output\results_<UTC>.csv` | `.csv` 或 `.json` |
| `--no-cache` | 關 | 不寫 K 線快取（預設寫到 `C:\trading-data\ihs-scanner-v2\data`） |

程式內使用：

```python
from ihs_scanner import detect_inverse_head_shoulders, scan_symbols, export_results
res = detect_inverse_head_shoulders(df, "4h", {"enable_breakout_filter": True}, symbol="BTCUSDT")
```
`df` 欄位：`timestamp`（UTC）、`open`、`high`、`low`、`close`、`volume`，只放已收盤 K 線。
結果是「截至 df 最後一根」的狀態；`df[:t+1]` 就是 t 時刻的即時答案（可直接拿來回測）。

## 網站

```bash
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py            # 寫 site/data/scan.json
/c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe build_site.py --include-stale  # 本機預覽多看幾筆
cd site && /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe -m http.server 8000
```
參數：`--symbols`、`--timeframes`、`--limit`、`--include-stale`、`--out`、`--scheduled`。
設計與維護規則：`docs/site-design.md`。GitHub Actions：`.github/workflows/scan-and-deploy.yml`（目前手動觸發，產出 artifact）。

## config 參數（`ihs_scanner/config.py`，可部分覆寫）

| key | 預設 | 說明 |
|---|---|---|
| `pivot_window_by_timeframe` | 4h 3、1d 3、1w 2 | swing 左右各 N 根，嚴格不等式；確認延遲 N 根 |
| `shoulder_diff_limit_by_timeframe` | 4h 0.05、1d/1w 0.10 | 左右肩差異率上限 |
| `min_head_depth_by_timeframe` | 4h 0.03、1d/1w 0.05 | 最小頭部深度 |
| `max_neckline_angle` / `angle_scale` | 30 / 100 | `atan(slope_pct_per_bar × scale)` 度數上限 |
| `breakout_buffer_by_timeframe` | 4h 0.005、1d/1w 0.01 | 突破 = close > 頸線 × (1+buffer) |
| `enable_breakout_filter` | False | True＝breakout 模式 |
| `enable_volume_filter` / `min_volume_ratio` | False / 1.2 | 突破量比門檻 |
| `volume_avg_window` | 20 | 均量＝突破 K **之前** 20 根（不含自身） |
| `min_pattern_score` | 0 | 低於此分數不輸出（scan_symbols 套用） |
| `max_pattern_bars_by_timeframe` | 4h 150、1d 120、1w 80 | LS→RS 最大根數 |
| `max_bars_after_rs` | 30 | 未突破時 RS 後超過此根數 → stale |
| `max_breakout_age` | 5 | 突破後超過此根數 → stale |
| `include_stale` | False | 是否輸出 stale |
| `ideal_head_depth` | 0.10 | 頭深分數滿分點 |
| `breakout_distance_range` | 0.10 | 距頸線分數歸零距離 |

`max_bars_after_rs`、`max_breakout_age` 也可給 `{"4h": .., "1d": .., "1w": ..}`。

## 輸出欄位

規格第十步全部欄位，外加：

- `status`：`watchlist`（未突破）／`confirmed`（已突破且夠新）／`stale`
- `shoulder_score`、`head_depth_score`、`neckline_score`、`breakout_score`：四項分數
- `RS_confirm_time`：右肩 pivot 被確認的 K 線時間（= RS_time + N 根）
- `signal_time`：此訊號最早可被「即時」知道的時間＝max(RS_confirm_time, breakout_time)；
  回測進場請用這個，不要用 breakout_time（突破可能發生在 RS 確認前）
- `average_volume_20`、`bars_since_rs`、`bars_since_breakout`、`as_of_time`（計算基準 K 線）
- `pattern_id`：`symbol|tf|LS_time|H_time|RS_time`，給通知去重用

時間一律 ISO 8601 UTC（`2026-01-01T00:00:00Z`）；未突破時 `breakout_*`、`volume_ratio` 為空（JSON 為 `null`）。
排序：pattern_score ↓ → 已突破優先 → distance_to_neckline ↑ → volume_ratio ↓ → 1w > 1d > 4h。

## 結構

```
ihs_scanner/  config.py  swings.py  neckline.py  scoring.py  detector.py  scanner.py  data_binance.py
scan.py       CLI
tests/        pytest（不碰網路；截斷／不 repaint 測試在 test_truncation.py）
```
