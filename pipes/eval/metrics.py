"""Graph metrics between a predicted and a ground-truth pipe JSON (plan step 3). Symbol layer for now.

Symbols: non-structural nodes (all but tee, junction, elbow, cross, open_end), Hungarian assignment on centre
distance <= 0.5 x GT box diagonal. Precision, recall, F1 and, per type, the confusion counts.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter

import cv2
import networkx as nx
import numpy as np
from scipy.optimize import linear_sum_assignment

from pipes.schema.convert import graph

STRUCTURAL = {"tee", "junction", "elbow", "cross", "open_end"}


def symbols(doc: dict) -> list[dict]:
    doc = graph(doc)
    return [
        n
        for n in doc["nodes"]
        if n["type"] not in STRUCTURAL and n["provenance"].get("status", "active") == "active"
    ]


def match_symbols(pred: dict, gt: dict, radius_frac: float = 0.5) -> dict:
    pred = graph(pred)
    gt = graph(gt)
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


# ---------------------------------------------------------------- graph layer (edges, connectivity, pipes)


def _graph(doc: dict) -> tuple[nx.Graph, set[str]]:
    """Whole sheet as a graph (nodes = all node ids, edges = traced pipes); unsnapped ends are dead ends."""
    doc = graph(doc)
    g = nx.Graph()
    sym_ids = {n["id"] for n in symbols(doc)}
    g.add_nodes_from(n["id"] for n in doc["nodes"])
    for e in doc["edges"]:
        if e["provenance"].get("status", "active") != "active":
            continue
        a, b = (x["node_id"] for x in e["ends"])
        if a and b and a != b:
            g.add_edge(a, b, id=e["id"])
    return g, sym_ids


def adjacency(doc: dict) -> set[frozenset]:
    """Symbol pairs joined directly: a path that passes only through structural nodes (tee, junction, ...)."""
    doc = graph(doc)
    g, syms = _graph(doc)
    pairs: set[frozenset] = set()
    for s in syms:
        seen, stack = {s}, [s]
        while stack:
            cur = stack.pop()
            for nb in g.neighbors(cur):
                if nb in seen:
                    continue
                seen.add(nb)
                if nb in syms:
                    pairs.add(frozenset((s, nb)))
                else:
                    stack.append(nb)
    return pairs


def _raster(doc: dict, shape: tuple[int, int]) -> np.ndarray:
    doc = graph(doc)
    m = np.zeros(shape, np.uint8)
    for e in doc["edges"]:
        if e["provenance"].get("status", "active") == "active":
            cv2.polylines(m, [np.round(np.asarray(e["polyline_px"])).astype(np.int32)], False, 1, 1)
    return m


def pipe_pixels(pred: dict, gt: dict, tol: float) -> tuple[float, float]:
    """(precision, recall) of pipe pixels: share of predicted pixels within tol of a GT pixel, and the reverse."""
    pred = graph(pred)
    gt = graph(gt)
    shape = (gt["frame"]["height_px"], gt["frame"]["width_px"])
    p, g = _raster(pred, shape), _raster(gt, shape)

    def within(a: np.ndarray, b: np.ndarray) -> float:
        if not a.any():
            return 1.0
        if not b.any():
            return 0.0
        d = cv2.distanceTransform((b == 0).astype(np.uint8), cv2.DIST_L2, 3)
        return float((d[a > 0] <= tol).mean())

    return within(p, g), within(g, p)


def has_edges(doc: dict) -> bool:
    doc = graph(doc)
    cov = [c for c in doc["coverage"] if c["category"] == "pipes"]
    return bool(doc["edges"]) and any(c["status"] == "analysed" for c in cov)


def graph_metrics(pred: dict, gt: dict, tol: float = 8.0) -> dict:
    """Edge precision/recall on symbol adjacency, connectivity accuracy, pipe-pixel precision/recall.

    Two views: `matched` only counts symbols the detector found (tracing quality alone); `strict` also charges
    missed and spurious symbols to the edges (end-to-end). Connectivity accuracy is over all pairs of matched GT
    symbols: do both graphs agree on connected / not connected (any path, through symbols too)?
    """
    pred = graph(pred)
    gt = graph(gt)
    m = match_symbols(pred, gt)
    p_syms, g_syms = symbols(pred), symbols(gt)
    p2g = {p_syms[i]["id"]: g_syms[j]["id"] for i, j in m["pairs"]}
    g_adj = adjacency(gt)
    p_adj_raw = adjacency(pred)
    matched_g = set(p2g.values())
    p_adj_matched = {frozenset(p2g[x] for x in pair) for pair in p_adj_raw if all(x in p2g for x in pair)}
    g_adj_matched = {pair for pair in g_adj if all(x in matched_g for x in pair)}
    p_unmatched = len(p_adj_raw) - len([1 for pair in p_adj_raw if all(x in p2g for x in pair)])

    def pr(p: set, g: set, extra_fp: int = 0, extra_fn: int = 0) -> dict:
        tp = len(p & g)
        fp, fn = len(p - g) + extra_fp, len(g - p) + extra_fn
        a, b, c = prf(tp, fp, fn)
        return {"tp": tp, "fp": fp, "fn": fn, "P": a, "R": b, "F1": c}

    strict = pr(p_adj_matched, g_adj, extra_fp=p_unmatched)
    matched = pr(p_adj_matched, g_adj_matched)

    pg, _ = _graph(pred)
    gg, _ = _graph(gt)
    ids = sorted(matched_g)
    g2p = {v: k for k, v in p2g.items()}
    agree = total = 0
    for a, b in itertools.combinations(ids, 2):
        total += 1
        gt_conn = nx.has_path(gg, a, b)
        pr_conn = nx.has_path(pg, g2p[a], g2p[b])
        agree += gt_conn == pr_conn
    pp, pr_ = pipe_pixels(pred, gt, tol)
    unsnapped = sum(1 for e in pred["edges"] for x in e["ends"] if x["node_id"] is None)
    return {
        "edges_matched": matched,
        "edges_strict": strict,
        "connectivity": {"agree": agree, "total": total, "accuracy": agree / total if total else 1.0},
        "pipes": {"P": pp, "R": pr_},
        "unsnapped_ends": unsnapped,
        "gt_edges": len(gt["edges"]),
        "pred_edges": len(pred["edges"]),
    }
