"""Box-level detector scores from the raw detection cache, over every test sheet.

  python -m pipes.eval.score_dets --runs dev full full1280   # tile / whole / merged, conf 0.25 0.35 0.5

Micro-averaged P/R/F1 (a symbol matches when its centre is within 0.5 x the GT box diagonal, Hungarian). The
subset column is the 5 dev test sheets, for comparison with the earlier numbers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipes.eval.metrics import match_symbols, prf
from pipes.raster.detect import merge

HERE = Path(__file__).parent.parent
DEV5 = {"0", "145", "157", "159", "192"}
VARIANTS = {"tile": ("tile",), "whole": ("whole",), "tile+whole": ("tile", "whole")}


def light(boxes: list[dict]) -> dict:
    nodes = [
        {
            "type": "unknown",
            "bbox_px": d["bbox_px"],
            "center_px": [(d["bbox_px"][0] + d["bbox_px"][2]) / 2, (d["bbox_px"][1] + d["bbox_px"][3]) / 2],
            "provenance": {},
        }
        for d in boxes
    ]
    return {"nodes": nodes}


def load(run: str) -> tuple[dict, dict]:
    raw = {
        f.name.removesuffix(".det.json"): json.loads(f.read_text(encoding="utf-8"))
        for f in (HERE / "index_pipes/_raw" / f"ds_test_{run}").glob("*.det.json")
    }
    gt = {
        sid: json.loads((HERE / "data_pipes/ds/test" / sid / "gt.pipe.json").read_text(encoding="utf-8"))
        for sid in raw
    }
    return raw, gt


def score(
    raw: dict, gt: dict, passes, conf: float, only: set[str] | None = None
) -> tuple[float, float, float, dict]:
    tot = {"tp": 0, "fp": 0, "fn": 0}
    per = {}
    for sid, r in raw.items():
        if only and sid not in only:
            continue
        m = match_symbols(light(merge(r["dets"], conf, passes=passes)), gt[sid])
        for k in tot:
            tot[k] += m[k]
        per[sid] = prf(m["tp"], m["fp"], m["fn"])[2]
    return (*prf(**tot), per)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipes.eval.score_dets")
    ap.add_argument("--runs", nargs="+", default=["dev", "full"])
    for run in ap.parse_args(argv).runs:
        raw, gt = load(run)
        print(
            f"\n### model {run}: {len(raw)} test sheets, {sum(len(g['nodes']) for g in gt.values())} GT symbols"
        )
        print(f"{'variant':<11} {'conf':>5} | {'P':>6} {'R':>6} {'F1':>6} | dev-5 F1")
        for name, passes in VARIANTS.items():
            for conf in (0.25, 0.35, 0.5):
                p, r, f, _ = score(raw, gt, passes, conf)
                f5 = score(raw, gt, passes, conf, DEV5)[2]
                print(f"{name:<11} {conf:>5} | {p:6.1%} {r:6.1%} {f:6.1%} | {f5:6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
