"""Skeleton tracer (plan step 8.1): skeletonize the erased ink, sknw -> nodes and polylines in original pixels."""

from __future__ import annotations

import cv2
import numpy as np
import sknw
from skimage.morphology import skeletonize


def _simplify(pts: np.ndarray, eps: float) -> list[list[float]]:
    p = np.round(pts.astype(np.float32)).reshape(-1, 1, 2)
    if len(p) > 2:
        p = cv2.approxPolyDP(p, eps, False)
    return [[float(x), float(y)] for x, y in p.reshape(-1, 2)]


def trace(ink: np.ndarray, w: float) -> tuple[dict[int, list[float]], list[dict]]:
    """Returns (nodes {id: [x, y]}, edges [{a, b, poly}]), poly oriented a -> b, endpoints = node positions.

    Nodes are skeleton end points (degree 1) and branch points (degree >= 3); degree-2 pixels are inside edges.
    """
    sk = skeletonize(ink)
    if not sk.any():
        return {}, []
    g = sknw.build_sknw(sk.astype(np.uint16), multi=True)
    nodes = {int(n): [float(d["o"][1]), float(d["o"][0])] for n, d in g.nodes(data=True)}
    edges = []
    for u, v, d in g.edges(data=True):
        pts = np.asarray(d["pts"])[:, ::-1]  # (row, col) -> (x, y)
        if len(pts) == 0:
            continue
        poly = _simplify(pts, max(1.0, w / 2))
        du = np.hypot(*(np.asarray(poly[0]) - nodes[int(u)]))
        dv = np.hypot(*(np.asarray(poly[0]) - nodes[int(v)]))
        a, b = (int(u), int(v)) if du <= dv else (int(v), int(u))  # orient so poly starts at node a
        poly[0], poly[-1] = list(nodes[a]), list(nodes[b])
        edges.append({"a": a, "b": b, "poly": poly})
    return nodes, edges
