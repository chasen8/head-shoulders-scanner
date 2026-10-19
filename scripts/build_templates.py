"""Generate ihs_scanner/templates.py (D26 V4) from the user's five golden examples.

For each example in tests/golden_examples.py: load tests/fixtures/<name>.csv, keep the bars closed at the
screenshot time (same cut as tests/test_golden_examples.py), and take the bars at the user's marked times:
LS / H / RS -> that bar's low, N1 / N2 -> that bar's high (how the detector measures the five points).
AZTEC's RS was still forming (no time): it is the lowest low after N2, as the detector's forming RS (T6).
Then y' / x' are computed with ihs_scanner.validation.template_coords (log prices, head = 0, neckline above
the head = 1; time 0 = LS, 1 = RS).

Run from the project root (Git Bash):
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe scripts/build_templates.py          # write
    /c/trading-data/ihs-scanner-v2/venv/Scripts/python.exe scripts/build_templates.py --check  # compare only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ihs_scanner.validation import template_coords  # noqa: E402
from tests.golden_examples import GOLDEN_EXAMPLES  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
OUT = ROOT / "ihs_scanner" / "templates.py"
BAR = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1), "1w": pd.Timedelta(days=7)}
DIGITS = 4


def example_points(name: str) -> tuple[dict[str, float], dict[str, int], float]:
    spec = GOLDEN_EXAMPLES[name]
    df = pd.read_csv(FIXTURES / f"{name}.csv", encoding="utf-8")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    shot = pd.Timestamp(spec["screenshot_utc"])
    df = df[df["timestamp"] + BAR[spec["timeframe"]] <= shot].reset_index(drop=True)
    ts = df["timestamp"]
    index, prices = {}, {}
    for key in ("LS", "N1", "H", "N2", "RS"):
        t_user = spec["points"][key][0]
        if t_user is None:  # forming RS: lowest low after N2 (T6)
            s = index["N2"] + 1
            i = s + int(np.argmin(df["low"].to_numpy()[s:]))
        else:
            i = int(np.flatnonzero(ts == pd.Timestamp(t_user))[0])
        index[key] = i
        prices[key] = float(df["high"].iloc[i] if key in ("N1", "N2") else df["low"].iloc[i])
    n1, n2 = index["N1"], index["N2"]
    neck_h = prices["N1"] + (prices["N2"] - prices["N1"]) * (index["H"] - n1) / (n2 - n1)
    return prices, index, neck_h


def build() -> dict[str, dict[str, tuple[float, ...]]]:
    out = {}
    for name in GOLDEN_EXAMPLES:
        prices, index, neck_h = example_points(name)
        y, x = template_coords(prices, index, neck_h)
        out[name] = {"y": tuple(round(v, DIGITS) for v in y), "x": tuple(round(v, DIGITS) for v in x)}
    return out


def render(templates: dict[str, dict[str, tuple[float, ...]]]) -> str:
    lines = [
        '"""D26 V4 templates: the user\'s five golden examples, normalised (GENERATED -- do not edit by hand).',
        "",
        "Regenerate: scripts/build_templates.py (reads tests/golden_examples.py + tests/fixtures/).",
        "y = (LS, N1, N2, RS): y'_k = (log P_k - log H.low) / (log neck(H) - log H.low); H = 0, neckline above H = 1.",
        "x = (N1, H, N2):      x'_k = (bar_k - bar_LS) / (bar_RS - bar_LS); LS = 0, RS = 1.",
        '"""',
        "",
        "TEMPLATES = {",
    ]
    for name, q in templates.items():
        ys = ", ".join(f"{v:.{DIGITS}f}" for v in q["y"])
        xs = ", ".join(f"{v:.{DIGITS}f}" for v in q["x"])
        lines.append(f'    "{name}": {{"y": ({ys}), "x": ({xs})}},')
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true", help="exit 1 if ihs_scanner/templates.py is out of date")
    args = p.parse_args(argv)
    text = render(build())
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("ihs_scanner/templates.py is out of date; run scripts/build_templates.py")
            return 1
        print("ihs_scanner/templates.py is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
