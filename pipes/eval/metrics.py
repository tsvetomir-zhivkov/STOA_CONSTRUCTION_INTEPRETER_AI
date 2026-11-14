"""Graph metrics between a predicted and a ground-truth pipe JSON (plan step 3). Symbol layer for now.

Symbols: non-structural nodes (all but tee, junction, elbow, cross, open_end), Hungarian assignment on centre
distance <= 0.5 x GT box diagonal. Precision, recall, F1 and, per type, the confusion counts.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
from scipy.optimize import linear_sum_assignment

STRUCTURAL = {"tee", "junction", "elbow", "cross", "open_end"}


def symbols(doc: dict) -> list[dict]:
    return [
        n
        for n in doc["nodes"]
        if n["type"] not in STRUCTURAL and n["provenance"].get("status", "active") == "active"
    ]


def match_symbols(pred: dict, gt: dict, radius_frac: float = 0.5) -> dict:
    p, g = symbols(pred), symbols(gt)
    pairs: list[tuple[int, int]] = []
    if p and g:
        cost = np.full((len(p), len(g)), 1e9)
        for i, a in enumerate(p):
            for j, b in enumerate(g):
                x0, y0, x1, y1 = b["bbox_px"]
                lim = radius_frac * math.hypot(x1 - x0, y1 - y0)
                d = math.dist(a["center_px"], b["center_px"])
                if d <= lim:
                    cost[i, j] = d
        rows, cols = linear_sum_assignment(cost)
        pairs = [(i, j) for i, j in zip(rows, cols, strict=True) if cost[i, j] < 1e9]
    tp = len(pairs)
    confusion = Counter((p[i]["type"], g[j]["type"]) for i, j in pairs)
    return {
        "tp": tp,
        "fp": len(p) - tp,
        "fn": len(g) - tp,
        "n_pred": len(p),
        "n_gt": len(g),
        "confusion": confusion,
        "pairs": pairs,
    }


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    prec = tp / (tp + fp) if tp + fp else 1.0 if fn == 0 else 0.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1
