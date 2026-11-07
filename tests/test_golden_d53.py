"""D53 golden case: FIGHTUSDT Gate perpetual 4h (the user's screenshot, site score 89.1, "RS confirmed").

Before D53 the right shoulder was 10-31 20:00 (0.004116) although the pullback from N2 had already bottomed at
10-29 20:00 (0.003652). D53 requires RS to be the lowest low of [N2, RS], the mirror of T7 on the left shoulder.
Points / time: tests/golden_examples.py D53_CASES. Run with -s to see the before / after rows.
"""
from pathlib import Path

import pandas as pd

from ihs_scanner.detector import detect_inverse_head_shoulders

from .golden_examples import D53_CASES

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "FIGHTUSDT_gate_perp_4h"
BAR = pd.Timedelta(hours=4)
OFF = {"require_rs_lowest_after_n2": False}


def load() -> tuple[pd.DataFrame, dict]:
    spec = D53_CASES[NAME]
    df = pd.read_csv(FIXTURES / f"{NAME}.csv", encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df[df["timestamp"] + BAR <= pd.Timestamp(spec["screenshot_utc"])].reset_index(drop=True), spec


def _show(title, rows):
    print(f"\n{title}")
    for r in rows:
        print(f"  {r['status']} score {r['pattern_score']:.2f} shape {r['shape_score']:.1f} "
              + " ".join(f"{k} {r[k + '_time']:%m-%d %H:%M} {r[k + '_price']:.5g}" for k in ("LS", "N1", "H", "N2", "RS")))


def _lowest_after_n2(df, r) -> float:
    return float(df[(df["timestamp"] >= r["N2_time"]) & (df["timestamp"] <= r["RS_time"])]["low"].min())


def test_fight_reproduces_the_site_without_the_rule():
    df, spec = load()
    before = detect_inverse_head_shoulders(df, "4h", OFF, symbol="FIGHTUSDT")
    _show("D53 off (what the site showed)", before)
    old = [r for r in before if r["RS_time"] == pd.Timestamp(spec["wrong_rs"][0])]
    assert old and old[0]["RS_price"] == spec["wrong_rs"][1] and old[0]["status"] == "watchlist"
    assert abs(old[0]["pattern_score"] - 89.1) < 0.1  # the user's screenshot
    assert _lowest_after_n2(df, old[0]) == spec["real_low"][1] < old[0]["RS_price"]


def test_fight_rs_is_not_above_the_pullback_low():
    df, spec = load()
    for cfg in (None, {"include_stale": True}):
        after = detect_inverse_head_shoulders(df, "4h", cfg, symbol="FIGHTUSDT")
        assert all(r["RS_time"] != pd.Timestamp(spec["wrong_rs"][0]) for r in after)
        assert all(_lowest_after_n2(df, r) == r["RS_price"] for r in after)
    _show("D53 on", detect_inverse_head_shoulders(df, "4h", None, symbol="FIGHTUSDT"))


def test_classic_profile_has_the_rule_off():
    df, spec = load()
    classic = detect_inverse_head_shoulders(df, "4h", {"profile": "classic"}, symbol="FIGHTUSDT")
    same = detect_inverse_head_shoulders(df, "4h", {"profile": "classic", **OFF}, symbol="FIGHTUSDT")
    assert classic == same


def test_rule_no_lookahead():
    """As of each of the last 20 bars the result equals a fresh run with every later bar mutated."""
    df, _spec = load()
    n = len(df)
    for t in range(n - 20, n):
        res = detect_inverse_head_shoulders(df.iloc[:t + 1], "4h", None, symbol="FIGHTUSDT")
        mutated = df.copy()
        mutated.loc[t + 1:, ["open", "high", "low", "close"]] *= 0.3
        assert detect_inverse_head_shoulders(mutated.iloc[:t + 1], "4h", None, symbol="FIGHTUSDT") == res
