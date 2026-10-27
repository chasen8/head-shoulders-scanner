"""Default configuration and helpers for the IHS scanner.

Keys follow docs/spec.md step 12, plus the charter additions (D6, D9, ...).
Per-timeframe values are dicts keyed by "4h" / "1d" / "1w".

D23: two rule-set profiles share one key set.
  "classic"  = the D19/D21 behaviour (CLASSIC_CONFIG below), kept switchable.
  "template" = calibrated on the user's five hand-marked examples (tests/golden_examples.py);
               TEMPLATE_OVERRIDES lists every value that differs from classic. New default.
merge_config({"profile": "classic"}) selects the classic base; other overrides apply on top.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

TIMEFRAMES = ("4h", "1d", "1w")

# D45 symbol-list filter (build_site.py, before any K line is fetched): a perpetual whose largest position one
# account can open is worth less than this many USDT is not scanned (OKX: highest position tier; Gate:
# risk_limit_max; formulas in data_okx / data_gate). Spot has no position limit and is not filtered; binance-perp's
# brackets need a signed request, so it is not filtered either. 0 = off (build_site.py --min-max-position).
MIN_MAX_POSITION_USDT = 10_000.0
PROFILES = ("template", "classic")
DEFAULT_PROFILE = "template"  # D23

CLASSIC_CONFIG: dict[str, Any] = {
    "profile": "classic",  # D23: "template" or "classic"
    # --- spec step 12 ---
    # D19: upper end of the spec's suggested range (4h/1d 3-5, 1w 2-3) to cut noisy pivots
    "pivot_window_by_timeframe": {"4h": 5, "1d": 5, "1w": 3},
    "shoulder_diff_limit_by_timeframe": {"4h": 0.05, "1d": 0.10, "1w": 0.10},
    "min_head_depth_by_timeframe": {"4h": 0.03, "1d": 0.05, "1w": 0.05},
    "max_neckline_angle": 30.0,  # degrees; scalar or per-timeframe dict (D36 template: 1w 45)
    "angle_scale": 100.0,
    "breakout_buffer_by_timeframe": {"4h": 0.005, "1d": 0.01, "1w": 0.01},
    "enable_breakout_filter": False,  # False = candidate mode, True = breakout mode
    "enable_volume_filter": False,
    "min_volume_ratio": 1.2,
    "min_pattern_score": 0.0,
    # --- charter additions ---
    "max_pattern_bars_by_timeframe": {"4h": 150, "1d": 120, "1w": 80},  # D6: LS->RS length
    # D9/D15: freshness windows in bars (int also accepted). 4h 30 bars = 5 days, 1d 20 = 4 weeks,
    # 1w 8 = 2 months; breakout age counted from the FIRST price cross (D14, no reset).
    "max_bars_after_rs": {"4h": 30, "1d": 20, "1w": 8},
    "max_breakout_age": {"4h": 6, "1d": 5, "1w": 3},
    "include_stale": False,  # D9
    "ideal_head_depth": 0.10,  # spec step 9
    "volume_avg_window": 20,  # D8: average of the N bars BEFORE the breakout bar
    "breakout_distance_range": 0.10,  # spec step 9: distance / 0.10
    # --- D19: extra shape rules (neurotrader888/TechnicalAnalysisAutomation check_ihs_pattern, MIT) ---
    # balance: reject if LS.low > (RS.low + N2.high)/2 or RS.low > (LS.low + N1.high)/2
    "enable_balance_rule": True,
    # symmetry: reject if one side (LS->H or H->RS, in bars) is > ratio x the other; None = off
    "max_time_symmetry_ratio": 2.5,
    # --- D19 optional, off by default (for later backtest comparison) ---
    "min_pattern_bars": None,  # minimum LS->RS length in bars (int or per-timeframe dict); None = off
    # prior downtrend (concept from yanivil/SignalSync, MIT): highest close in the
    # prior_trend_bars bars before LS must be >= LS.low + head height, where
    # head height = neckline_price_at(H.index) - H.low
    "require_prior_downtrend": False,
    "prior_trend_bars": {"4h": 60, "1d": 50, "1w": 20},
    # --- D21: five more shape rules (docs/pattern-rules-research.md section 2, priorities 1-5) ---
    # Notation: neck(i) = neckline_price_at(i) through N1.high / N2.high;
    # head_height = neck(H.index) - H.low.  Every rule is checked independently; None / False = off.
    # scan.py --rules-off R1,R11,R3,R2,R21 switches them off for comparisons.
    #
    # R1 head depth in ATR units (yanivil/SignalSync scan.py L151 IHS_MIN_HEAD_ATR, L1436, MIT):
    #   accept iff H.low <= min(LS.low, RS.low) - min_head_atr * ATR[H.index]
    #   (SignalSync uses strict "<" and a simple-mean ATR; D21 uses "<=" and Wilder ATR(atr_length):
    #   TR[0] = high-low, TR[i] = max(high-low, |high-prev close|, |low-prev close|),
    #   ATR[length-1] = mean(TR[0:length]), ATR[i] = (ATR[i-1]*(length-1) + TR[i]) / length.
    #   ATR[H.index] only uses bars <= H.index (no look-ahead). If ATR[H.index] is NaN
    #   (H.index < length-1) the rule is skipped, as the research report suggests.)
    "min_head_atr": 1.0,
    "atr_length": 14,
    # R11 pattern start (neurotrader888/TechnicalAnalysisAutomation head_shoulders.py check_ihs_pattern
    #   L227-244, MIT): walking left from LS, some bar j = LS.index-1 ... LS.index-L must have
    #   price[j] > neck(j) (the neckline extended to the left). L = pattern_start_lookback, or when None
    #   the original's range(1, head_width) with head_width = N2.index - N1.index, i.e. L = width - 1.
    #   As in the original, running out of data (j < 0) before a crossing is found rejects the pattern.
    #   price = close by default: the original runs on a single (log) close series; "high" is an option.
    #   Overlap: optional require_prior_downtrend (D19) asks "did price fall >= head_height into LS";
    #   R11 asks "was price above the extended neckline shortly before LS" -- related, kept both.
    "enable_pattern_start_rule": True,
    "pattern_start_lookback": None,
    "pattern_start_price": "high",  # D21: high, consistent with the high-based neckline (D5); original uses close
    # R3 total neckline tilt (SignalSync scan.py L156 IHS_MAX_NECK_SLOPE, L1450-1456, MIT):
    #   accept iff |slope * (RS.index - LS.index)| / close[H.index] <= max_neckline_total_slope,
    #   slope = (N2.high - N1.high) / (N2.index - N1.index) in price per bar (SignalSync width = rs - ls).
    #   Runs together with the +-30 deg max_neckline_angle rule.
    "max_neckline_total_slope": 0.15,
    # R2 shoulder difference relative to head height (SignalSync scan.py L153
    #   IHS_SHOULDER_SYM_OF_HEIGHT = 0.30, L1457-1459, MIT):
    #   accept iff |LS.low - RS.low| <= max_shoulder_diff_of_head * head_height.
    #   D21 default 0.5 (looser than SignalSync's US-daily 0.30). Runs together with the spec's
    #   percentage shoulder_diff_limit_by_timeframe.
    "max_shoulder_diff_of_head": 0.5,
    # R21 shoulder depth / head height band (Savin, Weller & Zvingelis, "The Predictive Power of
    #   Head-and-Shoulders Price Patterns in the U.S. Stock Market", sec. B rules R6/R7, mirrored for IHS;
    #   definition as written in docs/pattern-rules-research.md R21):
    #   shoulder_depth_ratio = mean(N1.high - LS.low, N2.high - RS.low) / head_height
    #   accept iff ratio >= min_shoulder_depth_ratio (R7, 0.25) and, when set, ratio <= max (R6, 0.7
    #   in the paper; the inequality direction was lost in the PDF text, so the upper bound is off).
    "min_shoulder_depth_ratio": 0.25,
    "max_shoulder_depth_ratio": None,
    # --- D23 template rules (off in classic; values of the template profile in TEMPLATE_OVERRIDES) ---
    # Notation: drop_L = (N1.high - LS.low) / N1.high, drop_R = (N2.high - RS.low) / N2.high
    #   (each shoulder's fall from its own neckline high -- how the user measures shoulders);
    #   neck(i) as above; head_rise = neck(H.index) / H.low - 1 (head to neckline, the user's "+45%");
    #   rise_L = N1.high / LS.low - 1, rise_R = N2.high / RS.low - 1 (shoulder rebounds, same units).
    # T1 both shoulder drops >= this (per timeframe); None = off
    "min_shoulder_drop_by_timeframe": None,
    # T2 shoulder symmetry by drop: |drop_L - drop_R| <= max_shoulder_drop_diff (absolute, 0.05 = 5 points)
    #   and |drop_L - drop_R| / max(drop_L, drop_R) <= max_shoulder_drop_rel_diff; None = off
    "max_shoulder_drop_diff": None,
    "max_shoulder_drop_rel_diff": None,
    # T3 head depth: head_rise >= min_head_rise (per timeframe) and
    #   head_prominence = head_rise / max(rise_L, rise_R) >= min_head_prominence; None = off
    "min_head_rise_by_timeframe": None,
    "min_head_prominence": None,
    # T4 clean neckline: every bar strictly between N1 and RS except N2 has high <= neck(i) * (1 + value)
    #   (neck extended past N2); None = off
    "max_neckline_violation": None,
    # T5 neckline tilt in head heights: |N2.high - N1.high| <= value * head_height; None = off
    "max_neckline_tilt_of_head": None,
    # T6 forming right shoulder: once N2 is a confirmed swing high, the lowest low after N2 is a
    #   provisional RS while it is still inside its pivot confirmation window (index > last - N);
    #   such a pattern is reported with status "forming" (it changes with every new bar)
    "enable_forming_rs": False,
    # T7 left shoulder starts the rally to N1: N1 must be the FIRST swing high after LS (no lower swing
    #   high in between) and LS.low = min(low[LS.index : N1.index]), i.e. LS is the bottom of the leg
    #   the neckline peak rose from (all five examples: LS->N1 4-7 bars). Without it an older low far
    #   left of N1 (AZTEC 04-29) or a higher low before the real bottom (PENDLE 11-14) becomes the
    #   "shoulder" just because its drop matches the right side better
    "require_n1_first_high_after_ls": False,
    # T8 head lowest including a lead-in: no low below H.low in the
    #   ceil(head_lead_in * (H.index - LS.index)) bars before LS (D6 already covers LS..RS).
    #   Rejects "patterns" sitting on top of a prior up-leg (price was below the head shortly before LS).
    #   Data running out (new listing) is not a rejection. None = off.
    "head_lead_in": None,
    # T9 rising neckline cap: N2.high / N1.high - 1 <= value (falling necklines are limited by T5). None = off.
    "max_neckline_rise": None,
    # T10 neckline highs must not be lone wick spikes: for N in (N1, N2),
    #   (N.high - max(open, close) over bars N.index-w .. N.index+w) / head_height <= value, w = pivot
    #   window (bars <= N.index + w, which exist because N is a confirmed pivot). None = off.
    "max_neckline_wick_of_head": None,
    # score: "spec" (spec step 9) or "template" (scoring.calculate_template_score)
    "score_method": "spec",
    # D28 TP1 (display only): Fibonacci extension from H (0) to the neckline midpoint (1), i.e.
    # the neckline at the time midpoint of N1 and N2; log=True works in log price like TradingView's
    # Fib tool with "log scale" on: TP1 = H * (mid / H) ** level; log=False: H + level * (mid - H)
    "tp1_fib_level": 1.414,
    "tp1_fib_log": True,
    "ideal_head_prominence": 1.5,  # template score: head_prominence giving full marks
    "ideal_head_rise": 0.40,  # template score: head_rise giving full marks
    # --- D26 shape validation V1-V4 (ihs_scanner/validation.py). Values are computed and output in BOTH
    # profiles; a threshold of None switches that check off (classic: all off). In the template profile a
    # pattern is output only when every enabled shape check passes (shape_valid); V3b/V3c do not reject the
    # shape, they turn the status into "stale" (stale_reason "runaway" / "failed_breakout").
    # Notation: z = natural log; head_height_log = log(neck(H.index)) - log(H.low); head_height (price) as above.
    #
    # V1 main turning points (Lo, Mamaysky & Wang 2000, Definition 1: E1..E5 are CONSECUTIVE extrema;
    #   Bulkowski hsb.html "avoid multi-peaks"): inside each leg LS->N1 (up), N1->H (down), H->N2 (up),
    #   N2->RS (down) no move against the leg's direction may reach value x head_height_log:
    #     down leg: max over s < i < j < e of log(high[j]) - log(low[i])   (rebound)
    #     up leg:   max over s < i < j < e of log(high[i]) - log(low[j])   (pullback)
    #   (end bars excluded, i < j strictly; output v1_max_counter_swing = max over legs / head_height_log).
    #   Lo's strict form would be a small value (the request started at 0.35), but the five examples measure
    #   0.44-0.62 (they touch the neckline mid-pattern: HMSTR, TRUST, PENDLE "mid"); 0.65 = examples' max +0.03.
    "max_counter_swing_of_head": None,
    # V2 skeleton fit (neurotrader888/TechnicalAnalysisAutomation head_shoulders.py compute_pattern_r2, MIT):
    #   model = straight lines through z_mid = log((high+low)/2) at LS, N1, H, N2, RS (+ the last bar for a
    #   forming RS) over bars LS .. RS (or last); residual = z_mid - model.
    #   v2_mad = mean|residual| / head_height_log  (PRIMARY, accept iff <= max_skeleton_mad_of_head)
    #   v2_r2  = 1 - SS_res / SS_tot              (shown; accept iff >= min_skeleton_r2 when set)
    #   R^2 is not used as the filter: the examples score -0.20..0.37 (their legs are not straight lines; the
    #   original author also found pattern_r2 ineffective), so any rho that keeps them is meaningless.
    #   MAD is in head heights (pattern scale), examples 0.137-0.178 -> 0.20.
    "max_skeleton_mad_of_head": None,
    "min_skeleton_r2": None,
    # V3 neckline integrity and chasing limit (yanivil/SignalSync scan.py MAX_RUNAWAY, MIT -- there 5% above
    #   the trigger; Bulkowski hsb.html: the neckline is the confirmation line):
    #   V3a closes: max(close[i] / neck(i) - 1) over N1 < i < RS, i != N2 must be <= value (0.005). T4 stays
    #       (it bounds the HIGHS at 1%, i.e. wicks); V3a is the stricter test on closes, the same quantity the
    #       breakout rule uses (D8): a close above the neckline before RS means it was not acting as resistance.
    #   V3b runaway: v3_runaway = (close[last] - neck(last)) / head_height (price units, negative = below);
    #       > value (0.5 = half way to TP1) -> status "stale", stale_reason "runaway".
    #   V3c failed breakout: once a close crossed the neckline after RS (first_cross_time set),
    #       v3_fallback = (neck(last) - close[last]) / head_height; > value (0.25) -> "stale", "failed_breakout".
    #       Small dips back to the neckline (Bulkowski's throwback, 65% of his cases) stay valid.
    "max_neckline_close_violation": None,
    "max_runaway_of_head": None,
    "max_fallback_of_head": None,
    # V4 example-template distance (Fu, Chung, Luk & Ng 2007 amplitude/temporal distance as restated in
    #   docs/academic-ihs-research.md 5.3; normalisation and weights: section 9 (c)):
    #   y'_k = (z_k - z_H) / (z_neck(H) - z_H), k in LS, N1, N2, RS;  x'_k = (t_k - t_LS) / (t_RS - t_LS), k in N1, H, N2
    #   AD_j = sqrt(mean (y' - q_j)^2), TD_j = sqrt(mean (x' - q^t_j)^2), D_j = w AD_j + (1-w) TD_j, D = min_j D_j
    #   over the five user examples (ihs_scanner/templates.py, scripts/build_templates.py); accept iff D <= value.
    #   0.25 = the research report's starting value; the examples' leave-one-out distances are 0.096-0.191.
    "max_template_distance": None,
    "template_amplitude_weight": 0.8,  # w
    "template_exclude": [],  # template names skipped by V4 (leave-one-out tests)
    # shape_score (0-100, validation.shape_score) = 100 * (0.5 * clip(1 - v1_max_counter_swing / gamma, 0, 1)
    #                                                     + 0.5 * clip(1 - v4_template_distance / theta, 0, 1))
    #   gamma / theta = the V1 / V4 thresholds (0.65 / 0.25 when off). 0 = at both limits, 100 = no counter
    #   swing and identical to an example. Shown and sortable; NOT mixed into pattern_score by default
    #   (weight 0): it measures the distance to the calibrated limits, and the user's own examples sit close to
    #   them (shape_score 15-40), so mixing would rank the examples' kind of shape down; D24's notify
    #   threshold (50) is also calibrated on the unmixed score.
    # pattern_score = (1 - weight) * base_score + weight * shape_score; base_score = the D23/spec total
    # (= shoulder + head_depth + neckline + breakout component scores).
    "shape_score_weight": 0.0,
    # --- D36 (user's TradingView cases BTCUSDT 1d, ATUSDT 4h, LDOUSDT 1w; tests/test_golden_d36.py) ---
    # A. breakout price: "close" (D8) = a close above neck(i) * (1 + buffer); "high" = the bar's high (its wick)
    #   crossing is enough. Used by every breakout-based value: breakout_time / first_cross_time (D14),
    #   signal_time (D13), status age, V3c's "has crossed", breakout_level (TP2). The pre-pattern checks T4 (highs
    #   <= neck * 1.01) and V3a (closes) between N1 and RS are unchanged.
    "breakout_price": "close",
    # B. breakout first: candidate B is dropped when another valid candidate A with the same head H (all shape
    #   rules + D7 passed; its status is not considered) first crossed its neckline at bar c < RS_B -- B's
    #   "right shoulder" is then the retest after A's breakout and its N2 typically the post-breakout high
    #   (BTCUSDT 1d 2026-09/10, LDOUSDT 1d, CRVUSDT 1d) -- except when
    #     (1) A's breakout failed: min(low[c .. RS_B]) < A.RS.low (the user's PENDLE example), or
    #     (2) B's neckline ranks better: rank = (0 if N2 < N1 else 1, -neckline_touches), B's rank < A's
    #         (falling beats rising, then more touches; the user's TRUST example: a steeper, untouched line was
    #         crossed first while the user's line through the 07-27 touch held).
    #   No look-ahead: everything is read from bars <= RS_B <= the last bar.
    "reject_post_breakout_rs": False,
    # C. neckline touches: a bar strictly between N1 and RS (N2 excluded) whose high is within +-tol of the
    #   neckline is a touch bar; neckline_touches = number of runs of consecutive touch bars, not counting runs
    #   that start right after N1 or are adjacent to N2 (those are the neckline points themselves).
    #   With v1_ignore_neckline_touches, V1 treats every touch (highest bar of a run of touch bars, anywhere in
    #   LS..RS) as an extra neckline vertex: each leg is split there (validation.leg_counter_swing), so the rally
    #   up to the neckline and the drop back are sub-legs, not a counter swing -- the neckline holding is not an
    #   extra turning point. Counter swings inside every sub-leg still count against the same V1 limit.
    "neckline_touch_tolerance": 0.01,
    "v1_ignore_neckline_touches": False,
    # C. de-duplication priority among overlapping patterns (D11): "score" = highest score (D11);
    #   "breakout_touches_slope" = breakout_detected first, then more neckline_touches, then a falling neckline
    #   (N2 < N1) before a flat / rising one, then the score.
    "dedup_priority": "score",
    # C. cross-timeframe (scanner / build_site, never inside the detector): a pattern on the smaller timeframe of
    #   a pair in SUPERSEDE_PAIRS (scanner.py) with a rising neckline (N2 > N1) is removed when the same symbol
    #   has a falling-neckline pattern on the larger timeframe whose [LS, RS] contains its H.
    "supersede_by_higher_timeframe": False,
    # T8 lead-in price: "low" (D23) or "body" = min(open, close): a single flash-crash wick (LDOUSDT 1w
    #   2025-10-06, low 0.2314 with open 1.16 / close 0.92) is not "price was below the head".
    "head_lead_in_price": "low",
    # --- D45 shoulder lows must not be lone long lower wicks (user's SKYAIUSDT Gate perp 4h case: LS = the
    #   2026-10-04 12:00 flash-crash bar, open 0.04064 / close 0.03509 / low 0.03103, wick 0.38 head heights,
    #   2.3 ATR). Mirror of T10 for the lows: for P in (LS, RS),
    #     shoulder_wick = (min(min(open, close) over bars P.index-w .. P.index+w) - P.low) / head_height
    #   w = pivot window; bars past the last bar are not read (a forming RS uses the bars it has). Accept iff
    #   max over LS, RS <= value. The examples measure 0.014-0.131 (LDOUSDT 1w LS the largest); None = off.
    "max_shoulder_wick_of_head": None,
}

# D23 template profile: calibrated on the five golden examples (per-example values:
# pytest -s tests/test_golden_examples.py; README). Every key here also exists in CLASSIC_CONFIG.
TEMPLATE_OVERRIDES: dict[str, Any] = {
    "profile": "template",
    # spec step 3 percentage shoulder difference: rejects TRUST (17.6%) and AZTEC (30.6%) -> optional, off
    "shoulder_diff_limit_by_timeframe": None,
    # wide patterns: TRUST 4h LS->RS ~830 bars, PENDLE 1d ~320 bars
    "max_pattern_bars_by_timeframe": {"4h": 1000, "1d": 600, "1w": 200},
    # ... and not a single-event V: the narrowest example is 100 bars (GRAM 1d), 4h examples >= 200
    "min_pattern_bars": {"4h": 60, "1d": 40, "1w": 16},
    # D19 balance rejects TRUST / AZTEC, time symmetry 2.5 rejects TRUST (6.5) / GRAM (3.5)
    "enable_balance_rule": False,
    "max_time_symmetry_ratio": None,
    # D21 R11 rejects TRUST / GRAM (no bar above the extended neckline in the available data);
    # R3 rejects TRUST (0.23) / AZTEC (0.68) -> replaced by T5
    "enable_pattern_start_rule": False,
    "max_neckline_total_slope": None,
    # D21 R1 at 1.0 is borderline for GRAM (1.00 ATR); 0.5 keeps the "not noise-level" intent
    "min_head_atr": 0.5,
    # distances to the neckline are 19-30% in the examples (price sits at the forming RS)
    "breakout_distance_range": 0.30,
    "min_shoulder_drop_by_timeframe": {"4h": 0.06, "1d": 0.08, "1w": 0.10},
    "max_shoulder_drop_diff": 0.05,
    "max_shoulder_drop_rel_diff": 0.25,
    "min_head_rise_by_timeframe": {"4h": 0.12, "1d": 0.15, "1w": 0.20},
    "min_head_prominence": 1.1,
    "max_neckline_violation": 0.01,
    "max_neckline_tilt_of_head": 0.75,
    "enable_forming_rs": True,
    "require_n1_first_high_after_ls": True,
    # HMSTR's closest earlier low below H is 84 bars before LS with H - LS = 118 (ratio 0.71)
    "head_lead_in": 0.5,
    # every example's neckline falls (-3% .. -25%); a strongly rising neckline puts RS far above LS
    "max_neckline_rise": 0.10,
    # examples: N1/N2 wick 0.05-0.21 head heights
    "max_neckline_wick_of_head": 0.32,
    "score_method": "template",
    # D26 shape validation (formulas above; per-example values: pytest -s tests/test_golden_examples.py).
    # Calibrated on the five examples (leave-one-out for V4); literature values are noted where they exist.
    "max_counter_swing_of_head": 0.65,  # V1: examples 0.44-0.62
    "max_skeleton_mad_of_head": 0.20,  # V2: examples 0.137-0.178
    "max_neckline_close_violation": 0.005,  # V3a: = the 4h breakout buffer (D8); examples <= -3%
    "max_runaway_of_head": 0.5,  # V3b: SignalSync uses 5% of price; here half the measured move to TP1
    "max_fallback_of_head": 0.25,  # V3c: judgment value (no literature number)
    "max_template_distance": 0.25,  # V4: research report start value; examples' LOO 0.096-0.191
    # D36 (formulas: CLASSIC_CONFIG "D36" block). The spec's +-30 deg angle is in %/bar, so a weekly bar
    # (7 days) is 7x stricter in time terms than a daily one; LDOUSDT 1w (neckline -25.6% over 37 weeks,
    # -34.7 deg) is the user's preferred neckline -> 1w 45 deg (1%/bar). 4h / 1d unchanged.
    "max_neckline_angle": {"4h": 30.0, "1d": 30.0, "1w": 45.0},
    "breakout_price": "high",
    "reject_post_breakout_rs": True,
    "v1_ignore_neckline_touches": True,
    "dedup_priority": "breakout_touches_slope",
    "supersede_by_higher_timeframe": True,
    "head_lead_in_price": "body",
    # D45: golden examples + D36 cases 0.014-0.131, SKYAI 4h flash-crash LS 0.379 -> 0.25 (about 2x the examples)
    "max_shoulder_wick_of_head": 0.25,
}


def _build_template() -> dict[str, Any]:
    cfg = deepcopy(CLASSIC_CONFIG)
    for key, value in TEMPLATE_OVERRIDES.items():
        if key not in cfg:
            raise KeyError(f"TEMPLATE_OVERRIDES has unknown key {key!r}")
        cfg[key] = deepcopy(value)
    return cfg


TEMPLATE_CONFIG: dict[str, Any] = _build_template()
PROFILE_CONFIGS: dict[str, dict[str, Any]] = {"template": TEMPLATE_CONFIG, "classic": CLASSIC_CONFIG}
DEFAULT_CONFIG: dict[str, Any] = PROFILE_CONFIGS[DEFAULT_PROFILE]

# D21: rule id -> config overrides that switch the rule off (scan.py --rules-off).
RULE_OFF_OVERRIDES: dict[str, dict[str, Any]] = {
    "R1": {"min_head_atr": None},
    "R11": {"enable_pattern_start_rule": False},
    "R3": {"max_neckline_total_slope": None},
    "R2": {"max_shoulder_diff_of_head": None},
    "R21": {"min_shoulder_depth_ratio": None, "max_shoulder_depth_ratio": None},
    # D26 shape validation
    "V1": {"max_counter_swing_of_head": None},
    "V2": {"max_skeleton_mad_of_head": None, "min_skeleton_r2": None},
    "V3": {"max_neckline_close_violation": None, "max_runaway_of_head": None, "max_fallback_of_head": None},
    "V4": {"max_template_distance": None},
}


def rules_off_overrides(rule_ids) -> dict[str, Any]:
    """Merge RULE_OFF_OVERRIDES for the given ids ("R1", "r11", ...); unknown ids raise KeyError."""
    out: dict[str, Any] = {}
    for rid in rule_ids:
        key = str(rid).strip().upper()
        if not key:
            continue
        if key not in RULE_OFF_OVERRIDES:
            raise KeyError(f"unknown rule id {rid!r}; expected one of {sorted(RULE_OFF_OVERRIDES)}")
        out.update(RULE_OFF_OVERRIDES[key])
    return out


def merge_config(overrides: Mapping[str, Any] | None = None,
                 base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a new config = base updated with overrides.

    base defaults to the profile's full config (D23): overrides["profile"] if given, else
    DEFAULT_PROFILE ("template"). An explicit base is used as is. Merging a full config again is
    a no-op, so detector / scanner can re-merge safely.
    Dict-valued keys are merged one level deep, so {"pivot_window_by_timeframe": {"4h": 5}}
    only changes 4h. Unknown keys raise KeyError (catches typos).
    """
    if base is None:
        profile = (overrides or {}).get("profile", DEFAULT_PROFILE)
        if profile not in PROFILE_CONFIGS:
            raise ValueError(f"unknown profile {profile!r}; expected one of {PROFILES}")
        base = PROFILE_CONFIGS[profile]
    cfg = deepcopy(dict(base))
    if not overrides:
        return cfg
    for key, value in overrides.items():
        if key not in cfg:
            raise KeyError(f"unknown config key: {key!r}")
        if isinstance(cfg[key], dict) and isinstance(value, Mapping):
            merged = dict(cfg[key])
            merged.update(deepcopy(dict(value)))
            cfg[key] = merged
        else:
            cfg[key] = deepcopy(value)
    return cfg


def tf_value(config: Mapping[str, Any], key: str, timeframe: str) -> Any:
    """Read a value that may be a scalar, None (rule off) or a per-timeframe dict."""
    value = config[key]
    if isinstance(value, Mapping):
        if timeframe not in value:
            raise ValueError(f"config[{key!r}] has no value for timeframe {timeframe!r}")
        return value[timeframe]
    return value


# D33 per-market overrides on top of the profile. Taiwan stocks trade 4.5 h a day, so a TW "4h" series has
# only 2 bars per day (09:00-13:00, 13:00-13:30; D29): 1000 bars would be ~2 years. Cap TW 4h patterns at
# 300 bars (~6 months) and leave longer structures to 1d / 1w.
MARKET_OVERRIDES: dict[str, dict[str, Any]] = {
    "crypto": {},
    "tw": {"max_pattern_bars_by_timeframe": {"4h": 300}},
}


def market_config(market: str, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Config for one market: profile defaults + MARKET_OVERRIDES[market] + caller overrides."""
    base = merge_config(MARKET_OVERRIDES.get(market, {}))
    return merge_config(overrides, base=base) if overrides else base


# ---------------------------------------------------------------------------------------------------------------
# D38 converging triangles (ihs_scanner/triangles.py). A separate config: nothing here is read by the IHS detector
# and no IHS key is read by the triangle detector. Values = the starting values of docs/triangle-research.md
# sections 5-6 (marked "建議" there: engineering assumptions, not literature numbers, to be calibrated with the
# user's examples). Notation: log prices; H0 = log width R(s) - S(s) at the first pivot s; drift = a line's change
# between its first and last touch, in units of H0; p = apex progress (x - s) / (x_apex - s); rho = 1 - p.
TRIANGLE_CONFIG: dict[str, Any] = {
    # pivots: swings.pivot_mask, strict, confirmed N bars later (D4); same N as the IHS default
    "pivot_window_by_timeframe": {"4h": 5, "1d": 5, "1w": 3},
    "atr_length": 14,
    # touches (report 5.2): >= 2 pivots on each line and >= 5 in total (Bulkowski 3+2; Lo 2000: 5 extrema)
    "min_touches_per_line": 2,
    "min_touches_total": 5,
    # fit (report 5.2): least squares through a line's pivots in log price; a pivot whose residual exceeds
    # eps = max(touch_atr_mult * ATR / close, touch_min_pct) (ATR / close at the bar the newest pivot is confirmed)
    # is dropped and the line refitted, at most max_fit_removals times per line; the first pivot must stay a touch.
    # The fitted line is then moved so no touch sticks out by more than the wick tolerance (enclosure below).
    "touch_atr_mult": 0.3,
    "touch_min_pct": 0.005,
    "max_fit_removals": 2,
    # classification (report 5.3), drift in units of H0:
    #   ascending:    |drift_R| <= horizontal_max_drift and drift_S >= sloped_min_drift
    #   descending:   drift_R <= -sloped_min_drift and |drift_S| <= horizontal_max_drift
    #   symmetrical:  drift_R <= -sym_min_drift and drift_S >= sym_min_drift and
    #                 max(|sR|, |sS|) / min(|sR|, |sS|) <= sym_max_slope_ratio
    #   anything else (wedges, channels, rectangles, in-between slopes) is not output
    "horizontal_max_drift": 0.15,
    "sloped_min_drift": 0.30,
    "sym_min_drift": 0.20,
    "sym_max_slope_ratio": 3.0,
    # convergence (report 5.4): lines must meet to the right (sS > sR); valid while
    # min_apex_progress <= p < 1 (rho <= 0.75: at least 25% converged); p >= near_apex_progress (rho < 0.20) only
    # sets near_apex; reaching p = 1 without a breakout -> stale "apex"
    "min_apex_progress": 0.25,
    "near_apex_progress": 0.80,
    # width (report 5.4.4): H0 >= min_height_atr * ATR/close (log) and exp(H0) - 1 <= max_height_pct
    "min_height_atr": 2.0,
    "max_height_pct": 0.80,
    # start alignment (report 5.4.5): |first high touch - first low touch| <= value * (last touch - first pivot)
    "max_start_offset_of_width": 0.5,
    # length in bars from the first pivot to now (forming) or to the breakout bar (report 5.5)
    "min_pattern_bars_by_timeframe": {"4h": 24, "1d": 20, "1w": 10},
    "max_pattern_bars_by_timeframe": {"4h": 200, "1d": 120, "1w": 60},
    # enclosure (report 5.6; IHS V3a / T4 values): bars from the first pivot to the newest pivot's confirmation
    # need close <= R*(1+close_tol), close >= S*(1-close_tol), high <= R*(1+wick_tol), low >= S*(1-wick_tol)
    "enclosure_close_tol": 0.005,
    "enclosure_wick_tol": 0.01,
    # breakout (report 5.7): after the newest pivot's confirmation, the first bar with
    # close > R*(1+buffer) (up) or close < S*(1-buffer) (down); "high" = the bar's high / low (wick) instead
    "breakout_buffer_by_timeframe": {"4h": 0.005, "1d": 0.01, "1w": 0.01},
    "breakout_price": "close",
    # freshness (IHS D15 values): breakout_up / breakout_down for <= this many bars after the breakout bar
    "max_breakout_age": {"4h": 6, "1d": 5, "1w": 3},
    # failed breakout (report 5.7, like IHS V3c): a later close back inside the broken line by more than
    # value * H0 (log) -> stale "failed_breakout"
    "max_fallback_of_height": 0.25,
    # conservative target = breakout price +- target height * Bulkowski "meets price target" rate
    # (thepatternsite.com at.html / dt.html / st.html, US daily stocks in bull markets; docs/triangle-research.md B1)
    "target_hit_rate": {
        "ascending": {"up": 0.70, "down": 0.44},
        "descending": {"up": 0.64, "down": 0.50},
        "symmetrical": {"up": 0.58, "down": 0.36},
    },
    # score: convergence ratio giving full marks (report 7.3: near Bulkowski's median breakout point)
    "ideal_converge_ratio": 0.40,
    "include_stale": False,
    "min_score": 0.0,
}

# Taiwan stocks: a "4h" series has 2 bars per day (D29 / D33), so cap 4h triangles at 120 bars (report 5.5)
TRIANGLE_MARKET_OVERRIDES: dict[str, dict[str, Any]] = {
    "crypto": {},
    "tw": {"max_pattern_bars_by_timeframe": {"4h": 120}},
}


def triangle_config(overrides: Mapping[str, Any] | None = None, market: str | None = None) -> dict[str, Any]:
    """TRIANGLE_CONFIG + TRIANGLE_MARKET_OVERRIDES[market] + overrides (dicts merged one level deep, unknown keys
    raise KeyError). Merging a full triangle config again is a no-op."""
    base = merge_config(TRIANGLE_MARKET_OVERRIDES.get(market or "crypto", {}), base=TRIANGLE_CONFIG)
    return merge_config(overrides, base=base) if overrides else base


# ---------------------------------------------------------------------------------------------------------------
# D49 double bottom ("W", ihs_scanner/double_bottom.py). A separate config: nothing here is read by the IHS or the
# triangle detector. Notation: B1 / B2 = first / second bottom (swing lows, low prices), P = the highest high between
# them (a swing high; the horizontal neckline), H = min(B1, B2). Every threshold below is an engineering assumption
# chosen BEFORE looking at any backtest outcome (reasons: docs/backtest-double-bottom.md section 2); the only
# literature numbers are Lo, Mamaysky & Wang 2000's 1.5% (DBOT: two bottoms within 1.5% of their average) and its
# >= 22 trading days between the bottoms. Bulkowski's per-pattern thresholds were not available
# (docs/pattern-winrate-research.md section 6).
DOUBLE_BOTTOM_CONFIG: dict[str, Any] = {
    "pivot_window_by_timeframe": {"4h": 5, "1d": 5, "1w": 3},  # = IHS (D19)
    "atr_length": 14,
    # bottoms close together: -max_undercut*tol <= B2 - B1 <= tol,
    # tol = clip(bottom_tol_atr_mult * ATR[B2], floor * avg(B1, B2), cap * avg(B1, B2))
    "bottom_tol_atr_mult": 1.0,
    "bottom_tol_floor_pct": 0.015,  # Lo 2000
    "bottom_tol_cap_pct_by_timeframe": {"4h": 0.03, "1d": 0.05, "1w": 0.10},
    "max_undercut_of_tol": 0.5,  # B2 may be at most this x tol BELOW B1 (a shallow spring); above B1 up to tol
    # rally to the neckline: P / max(B1, B2) - 1 >= min_rise_pct and P - max(B1, B2) >= min_rise_atr * ATR[B2]
    "min_rise_pct_by_timeframe": {"4h": 0.03, "1d": 0.06, "1w": 0.10},
    "min_rise_atr": 2.5,
    # bars from B1 to B2 (Lo: >= 22 trading days on 1d)
    "min_bars_between_by_timeframe": {"4h": 24, "1d": 22, "1w": 8},
    "max_bars_between_by_timeframe": {"4h": 300, "1d": 150, "1w": 60},
    # prior decline: the highest high in the prior_lookback bars before B1 must be >= P, and fall to B1 by
    # >= min_prior_decline_pct and >= min_prior_decline_atr * ATR[B1]
    "prior_lookback_by_timeframe": {"4h": 90, "1d": 60, "1w": 30},
    "min_prior_decline_pct_by_timeframe": {"4h": 0.06, "1d": 0.10, "1w": 0.15},
    "min_prior_decline_atr": 4.0,
    # breakout (D36 conventions): a bar's HIGH above P * (1 + buffer); the first such bar wins
    "breakout_price": "high",
    "breakout_buffer_by_timeframe": {"4h": 0.005, "1d": 0.01, "1w": 0.01},
    # freshness (same values as IHS D9 / D15)
    "max_bars_after_b2": {"4h": 30, "1d": 20, "1w": 8},
    "max_breakout_age": {"4h": 6, "1d": 5, "1w": 3},
    "max_runaway_of_height": 0.5,  # (close - P) / (P - H) above this -> stale "runaway" (IHS V3b)
    "max_fallback_of_height": 0.25,  # (P - close) / (P - H) after a cross -> stale "failed_breakout" (IHS V3c)
    "tp1_fib_level": 1.414,  # D28: TP1 = H * (P / H) ** level
    # score (display / ordering only): rise and decline giving full marks
    "ideal_rise_pct_by_timeframe": {"4h": 0.08, "1d": 0.15, "1w": 0.30},
    "ideal_prior_decline_pct_by_timeframe": {"4h": 0.15, "1d": 0.25, "1w": 0.40},
    "include_stale": False,
    "min_score": 0.0,
}

# Taiwan stocks: a "4h" series has 2 bars per day (D29 / D33) -> 4h distance between the bottoms 16..150 bars
DOUBLE_BOTTOM_MARKET_OVERRIDES: dict[str, dict[str, Any]] = {
    "crypto": {},
    "tw": {"max_bars_between_by_timeframe": {"4h": 150}, "min_bars_between_by_timeframe": {"4h": 16}},
}


def double_bottom_config(overrides: Mapping[str, Any] | None = None, market: str | None = None) -> dict[str, Any]:
    """DOUBLE_BOTTOM_CONFIG + DOUBLE_BOTTOM_MARKET_OVERRIDES[market] + overrides (unknown keys raise KeyError)."""
    base = merge_config(DOUBLE_BOTTOM_MARKET_OVERRIDES.get(market or "crypto", {}), base=DOUBLE_BOTTOM_CONFIG)
    return merge_config(overrides, base=base) if overrides else base
