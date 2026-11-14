"""python -m pipes.eval.score_graph --pred <dir of <id>.pipe.json> --gt <dir of <id>/gt.pipe.json>

Prints one table per layer. Only the symbol layer exists so far; pipes/graph layers come with the tracer.
An empty prediction scores 0 recall without crashing; GT against itself scores 100%.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipes.eval.metrics import match_symbols, prf


def load_gt(gt_dir: Path) -> dict[str, dict]:
    return {
        d.name: json.loads((d / "gt.pipe.json").read_text(encoding="utf-8"))
        for d in sorted(gt_dir.iterdir())
        if (d / "gt.pipe.json").exists()
    }


def load_pred(pred_dir: Path) -> dict[str, dict]:
    return {
        f.name.removesuffix(".pipe.json"): json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(pred_dir.glob("*.pipe.json"))
    }


def score_symbols(preds: dict[str, dict], gts: dict[str, dict]) -> tuple[list[dict], dict]:
    rows, tot = [], {"tp": 0, "fp": 0, "fn": 0}
    for sid, gt in gts.items():
        pred = preds.get(sid) or {**gt, "nodes": []}
        m = match_symbols(pred, gt)
        p, r, f = prf(m["tp"], m["fp"], m["fn"])
        rows.append(
            {"sheet": sid, "gt": m["n_gt"], "pred": m["n_pred"], "tp": m["tp"], "P": p, "R": r, "F1": f}
        )
        for k in tot:
            tot[k] += m[k]
    return rows, tot


def report(rows: list[dict], tot: dict) -> str:
    lines = [f"{'sheet':>8} {'gt':>4} {'pred':>5} {'tp':>4} {'P':>6} {'R':>6} {'F1':>6}"]
    for r in rows:
        lines.append(
            f"{r['sheet']:>8} {r['gt']:>4} {r['pred']:>5} {r['tp']:>4} "
            f"{r['P']:6.1%} {r['R']:6.1%} {r['F1']:6.1%}"
        )
    p, r, f = prf(tot["tp"], tot["fp"], tot["fn"])
    n_gt, n_pred = tot["tp"] + tot["fn"], tot["tp"] + tot["fp"]
    lines.append(f"{'TOTAL':>8} {n_gt:>4} {n_pred:>5} {tot['tp']:>4} {p:6.1%} {r:6.1%} {f:6.1%}")
    return "SYMBOLS (centre within 0.5 x GT box diagonal)\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipes.eval.score_graph")
    ap.add_argument("--pred", required=True)
    ap.add_argument("--gt", required=True)
    a = ap.parse_args(argv)
    gts = load_gt(Path(a.gt))
    preds = load_pred(Path(a.pred)) if Path(a.pred).is_dir() else {}
    print(report(*score_symbols(preds, gts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
