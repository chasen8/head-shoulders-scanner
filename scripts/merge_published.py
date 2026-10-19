"""Build the tree to publish for ONE market while keeping the OTHER market's files (charter D29).

Both the public site repo (chasen8/head-shoulders-site) and the private `scan-state` branch are force
pushed as a single fresh commit. With two markets updated by two workflows, a plain "push my site/"
would delete the other market's data, so each publish step:

  1. takes this run's tree (`--new`: the site/ artifact, or a staging dir with the state file + site/data),
  2. drops every path owned by ANOTHER market from it (a checkout could carry stale copies),
  3. copies the other markets' paths from the currently published tree (`--prev`: a clone of the public
     repo, or the scan-state worktree) into the output,
  4. writes the result to `--out` (which the workflow commits and pushes).

Ownership (relative paths; a directory owns everything under it):
  site  : crypto -> data/scan.json, data/bars        tw -> data/tw
  state : crypto -> state.json, site/data/scan.json, site/data/bars
          tw     -> state_tw.json, site/data/tw

If --prev is given but missing, the step fails (exit 1) instead of publishing a tree without the other
market; pass --allow-missing-prev only for the very first publish.

Usage (stdlib only, any Python 3.10+):
    python scripts/merge_published.py --kind site  --market tw --new site --prev /tmp/public --out /tmp/out
    python scripts/merge_published.py --kind state --market crypto --new stage --prev /tmp/scan-state --out /tmp/o
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

OWNED: Mapping[str, Mapping[str, Sequence[str]]] = {
    "site": {"crypto": ("data/scan.json", "data/bars"), "tw": ("data/tw",)},
    "state": {"crypto": ("state.json", "site/data/scan.json", "site/data/bars"),
              "tw": ("state_tw.json", "site/data/tw")},
}
SKIP_NAMES = {".git"}


def _under(rel: PurePosixPath, owned: Sequence[str]) -> bool:
    return any(rel == PurePosixPath(p) or PurePosixPath(p) in rel.parents for p in owned)


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def merge_tree(new_root: Path, prev_root: Path | None, out_root: Path, market: str,
               owned: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """Write new_root (minus other markets' paths) + other markets' paths from prev_root into out_root.

    Returns {"kept": [paths copied from prev], "absent": [other-market paths prev does not have],
    "dropped": [other-market paths removed from new_root]}."""
    if market not in owned:
        raise ValueError(f"unknown market {market!r}; choose from {sorted(owned)}")
    others = [p for m, paths in owned.items() if m != market for p in paths]
    new_root, out_root = Path(new_root), Path(out_root)
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    report: dict[str, list[str]] = {"kept": [], "absent": [], "dropped": []}

    def walk(src: Path, rel: PurePosixPath) -> None:
        for child in sorted(src.iterdir()):
            if child.name in SKIP_NAMES:
                continue
            crel = rel / child.name
            if _under(crel, others):
                report["dropped"].append(str(crel))
                continue
            if child.is_dir():
                # a directory that only *contains* another market's path (e.g. data/ holding data/tw)
                walk(child, crel)
            else:
                _copy(child, out_root / crel)

    walk(new_root, PurePosixPath("."))
    for p in others:
        src = Path(prev_root) / p if prev_root is not None else None
        if src is not None and src.exists():
            _copy(src, out_root / p)
            report["kept"].append(p)
        else:
            report["absent"].append(p)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kind", choices=sorted(OWNED), required=True)
    ap.add_argument("--market", required=True, help="the market this run publishes (crypto | tw)")
    ap.add_argument("--new", required=True, help="this run's tree")
    ap.add_argument("--prev", default=None, help="the currently published tree (other markets come from here)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-missing-prev", action="store_true",
                    help="publish even when --prev does not exist (first publish only)")
    args = ap.parse_args(argv)
    prev = Path(args.prev) if args.prev else None
    if prev is not None and not prev.is_dir() and not args.allow_missing_prev:
        print(f"error: previous tree {prev} not found; refusing to publish without the other market's data",
              file=sys.stderr)
        return 1
    if not Path(args.new).is_dir():
        print(f"error: new tree {args.new} not found", file=sys.stderr)
        return 1
    rep = merge_tree(Path(args.new), prev if prev is not None and prev.is_dir() else None, Path(args.out),
                     args.market, OWNED[args.kind])
    print(f"[merge {args.kind}/{args.market}] kept from previous: {rep['kept'] or 'nothing'}; "
          f"not in previous: {rep['absent'] or 'nothing'}; dropped from new: {rep['dropped'] or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
