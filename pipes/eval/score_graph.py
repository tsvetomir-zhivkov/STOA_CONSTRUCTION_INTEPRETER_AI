"""python -m pipes.eval.score_graph --pred <dir of <id>.pipe.json> --gt <dir of <id>/gt.pipe.json>

Prints one table per layer. Only the symbol layer exists so far; pipes/graph layers come with the tracer.
An empty prediction scores 0 recall without crashing; GT against itself scores 100%.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipes.eval.metrics import graph_metrics, has_edges, match_symbols, prf
from pipes.schema.convert import graph


def load_gt(gt_dir: Path) -> dict[str, dict]:
    return {
        d.name: graph(json.loads((d / "gt.pipe.json").read_text(encoding="utf-8")))
        for d in sorted(gt_dir.iterdir())
        if (d / "gt.pipe.json").exists()
    }


def load_pred(pred_dir: Path) -> dict[str, dict]:
    return {
        f.name.removesuffix(".pipe.json"): graph(json.loads(f.read_text(encoding="utf-8")))
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


def score_edges(preds: dict[str, dict], gts: dict[str, dict], tol: float = 8.0) -> list[dict]:
    """Graph metrics for the sheets whose GT has hand-labelled edges (pipes coverage 'analysed')."""
    rows = []
    for sid, gt in gts.items():
        if not has_edges(gt):
            continue
        pred = preds.get(sid) or {**gt, "nodes": [], "edges": []}
        rows.append({"sheet": sid, **graph_metrics(pred, gt, tol)})
    return rows


def report_edges(rows: list[dict]) -> str:
    if not rows:
        return "EDGES: no sheet has hand-labelled edges yet (gt.edges.json); label some with pipes.ingest.label_edges"
    head = f"{'sheet':>6} {'gtE':>4} {'predE':>5} | matched P / R | strict P / R | conn acc | pipe-px P / R | unsnapped"
    out = [head]
    tot = {"m": [0, 0, 0], "s": [0, 0, 0], "a": 0, "n": 0}
    for r in rows:
        m, st, c, pp = r["edges_matched"], r["edges_strict"], r["connectivity"], r["pipes"]
        out.append(
            f"{r['sheet']:>6} {r['gt_edges']:>4} {r['pred_edges']:>5} | {m['P']:6.1%} {m['R']:6.1%} | "
            f"{st['P']:6.1%} {st['R']:6.1%} | {c['accuracy']:7.1%} | {pp['P']:6.1%} {pp['R']:6.1%} | "
            f"{r['unsnapped_ends']:>5}"
        )
        for k, key in (("m", m), ("s", st)):
            tot[k] = [tot[k][0] + key["tp"], tot[k][1] + key["fp"], tot[k][2] + key["fn"]]
        tot["a"] += c["agree"]
        tot["n"] += c["total"]
    pm, rm, _ = prf(*tot["m"])
    ps, rs, _ = prf(*tot["s"])
    out.append(
        f"{'TOTAL':>6} {'':>4} {'':>5} | {pm:6.1%} {rm:6.1%} | {ps:6.1%} {rs:6.1%} | "
        f"{(tot['a'] / tot['n'] if tot['n'] else 1.0):7.1%} |"
    )
    return (
        "EDGES (symbol adjacency through pipes and tees; matched = only detected symbols, strict = also charges "
        "missed/spurious symbols)\n" + "\n".join(out)
    )


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
    ap.add_argument(
        "--tol", type=float, default=8.0, help="pipe-pixel tolerance in px (hand-labelled pipes are loose)"
    )
    a = ap.parse_args(argv)
    gts = load_gt(Path(a.gt))
    preds = load_pred(Path(a.pred)) if Path(a.pred).is_dir() else {}
    print(report(*score_symbols(preds, gts)))
    print()
    print(report_edges(score_edges(preds, gts, a.tol)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
