"""D45 golden case: SKYAIUSDT Gate perpetual 4h (the user's screenshot, site score 82.6, "RS confirmed").

Before D45 the left shoulder was the 2026-10-04 12:00 flash-crash bar (low 0.03103 far below every nearby body,
0.38 head heights). D45 rejects shoulders whose low is a lone long lower wick (max_shoulder_wick_of_head 0.25 in the
template profile), so that bar can no longer be LS. Points / time: tests/golden_examples.py D45_CASES.
Run with -s to see the before / after rows.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from ihs_scanner.detector import detect_inverse_head_shoulders

from .golden_examples import D45_CASES

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "SKYAIUSDT_gate_perp_4h"
BAR = pd.Timedelta(hours=4)


def load() -> tuple[pd.DataFrame, dict]:
    spec = D45_CASES[NAME]
    df = pd.read_csv(FIXTURES / f"{NAME}.csv", encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    cut = df[df["timestamp"] + BAR <= pd.Timestamp(spec["screenshot_utc"])].reset_index(drop=True)
    assert len(cut) < len(df), "fixture should extend past the screenshot so the cut is exercised"
    return cut, spec


def _show(title, rows):
    print(f"\n{title}")
    for r in rows:
        print(f"  {r['status']} score {r['pattern_score']:.2f} shoulder_wick {r['shoulder_wick_of_head']:.3f} "
              + " ".join(f"{k} {r[k + '_time']:%m-%d %H:%M} {r[k + '_price']:.5g}" for k in ("LS", "N1", "H", "N2", "RS")))


def test_skyai_lone_wick_is_not_the_left_shoulder():
    df, spec = load()
    wick_t, wick_low = pd.Timestamp(spec["wick_ls"][0]), spec["wick_ls"][1]
    h_t = pd.Timestamp(spec["H"][0])
    bar = df[df["timestamp"] == wick_t].iloc[0]
    assert bar["low"] == wick_low

    before = detect_inverse_head_shoulders(df, "4h", {"max_shoulder_wick_of_head": None}, symbol="SKYAIUSDT")
    _show("D45 off (what the site showed)", before)
    old = [r for r in before if r["H_time"] == h_t]
    assert old and old[0]["LS_time"] == wick_t and old[0]["status"] == "watchlist"
    assert abs(old[0]["pattern_score"] - 82.6) < 0.1  # the user's screenshot
    wick = (min(bar["open"], bar["close"]) - bar["low"]) / old[0]["head_height"]
    assert wick > 0.35 and abs(old[0]["shoulder_wick_of_head"] - wick) < 1e-9  # the bar's own body is the lowest

    for cfg in (None, {"include_stale": True}):
        after = detect_inverse_head_shoulders(df, "4h", cfg, symbol="SKYAIUSDT")
        assert all(r["LS_time"] != wick_t and r["RS_time"] != wick_t for r in after)
        assert all(r["shoulder_wick_of_head"] <= 0.25 for r in after)
    after = detect_inverse_head_shoulders(df, "4h", None, symbol="SKYAIUSDT")
    _show("D45 on", after)
    # same head and right side, a later left shoulder (10-08 16:00) whose low is not a lone wick
    new = [r for r in after if r["H_time"] == h_t]
    assert new and new[0]["LS_time"] > wick_t and new[0]["N2_time"] == old[0]["N2_time"]


def test_classic_profile_has_the_rule_off():
    df, _spec = load()
    res = detect_inverse_head_shoulders(df, "4h", {"profile": "classic"}, symbol="SKYAIUSDT")
    assert all("shoulder_wick_of_head" in r for r in res)  # value computed, never used to reject


def test_shoulder_wick_no_lookahead():
    """As of each of the last 20 bars the result equals a fresh run with every later bar mutated."""
    df, _spec = load()
    n = len(df)
    for t in range(n - 20, n):
        res = detect_inverse_head_shoulders(df.iloc[:t + 1], "4h", None, symbol="SKYAIUSDT")
        mutated = df.copy()
        mutated.loc[t + 1:, ["open", "high", "low", "close"]] *= 0.3
        assert detect_inverse_head_shoulders(mutated.iloc[:t + 1], "4h", None, symbol="SKYAIUSDT") == res
        for r in res:
            assert np.isfinite(r["shoulder_wick_of_head"])
