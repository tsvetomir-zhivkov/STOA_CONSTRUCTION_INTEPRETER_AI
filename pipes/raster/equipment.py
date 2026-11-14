"""Large closed contours that pipes attach to become equipment nodes (type unknown) instead of traced outlines.

Runs on the ink AFTER symbol boxes, text and frames are erased. A candidate is a hole (closed outline interior) that
  * is large: area >= max(0.2% of the page, (10 w)^2) and <= 30% of the page;
  * is not a rectilinear pipe loop: less than ORTHO_MAX of its outline length lies on long axis-parallel runs, so
    equipment with curves or diagonals (vessels, pumps, filters, ellipses) passes and orthogonal pipe grids do not;
  * has ink attached from the outside (a pipe joins it): without a pipe it is an unattached frame/box, which the
    frame rule in regions.py handles;
  * is not already covered by a detector box;
  * is not a stamp or seal: not entirely inside the outer page margin and not dense with ink inside.
The result is a box (outline bbox + one stroke width); the caller erases its outline and interior like any symbol,
so the pipes that joined it end at its border and snap to it. Known limit: rectilinear equipment (a plain
rectangular tank) looks like a pipe loop and is not found.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

MIN_PAGE_SHARE = 0.002
MAX_PAGE_SHARE = 0.30
ORTHO_MAX = 0.6
MARGIN = 0.08  # candidates entirely inside this outer page strip are title-block stamps and seals
INTERIOR_INK_MAX = 0.15  # a stamp or seal is dense inside; equipment outlines are mostly empty
BOX_COVER_MAX = 0.4  # skip when a detector box covers this share of the candidate
SCORE = 0.6  # confidence given to a contour-derived node


def _ortho_share(contour: np.ndarray, w: float) -> float:
    per = cv2.arcLength(contour, True)
    if per <= 0:
        return 1.0
    poly = cv2.approxPolyDP(contour, max(2.0, 1.5 * w), True).reshape(-1, 2)
    ortho = 0.0
    for a, b in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        d = b - a
        ln = math.hypot(*d)
        if ln < 6 * w:
            continue
        ang = abs(math.degrees(math.atan2(d[1], d[0]))) % 90
        if min(ang, 90 - ang) <= 8:
            ortho += ln
    return ortho / per


def _attached(ink: np.ndarray, hole: np.ndarray, w: float) -> bool:
    d0, d1 = int(2 * w + 2), int(2 * w + 2 + 3 * w)
    k0 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d0 + 1, 2 * d0 + 1))
    k1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d1 + 1, 2 * d1 + 1))
    u = hole.astype(np.uint8)
    ring = (cv2.dilate(u, k1) > 0) & ~(cv2.dilate(u, k0) > 0)
    return bool((ink & ring).sum() >= 1.5 * w * w)


def find_equipment(
    ink: np.ndarray, w: float, boxes: list[list[float]], skip: np.ndarray | None = None
) -> list[dict]:
    H, W = ink.shape
    k = max(3, int(round(2 * w)))
    closed = cv2.morphologyEx(ink.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    cnts, hier = cv2.findContours(closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None:
        return []
    lo, hi = max(MIN_PAGE_SHARE * W * H, (10 * w) ** 2), MAX_PAGE_SHARE * W * H
    cands = []
    for i, c in enumerate(cnts):
        if hier[0][i][3] < 0:
            continue  # outer boundaries; only holes are closed interiors
        area = cv2.contourArea(c)
        if lo <= area <= hi:
            cands.append((area, c))
    out: list[dict] = []
    for _area, c in sorted(cands, key=lambda t: -t[0]):
        x, y, bw, bh = cv2.boundingRect(c)
        bbox = [x, y, x + bw, y + bh]
        if any(bbox[0] >= o["bbox_px"][0] and bbox[1] >= o["bbox_px"][1] and bbox[2] <= o["bbox_px"][2]
               and bbox[3] <= o["bbox_px"][3] for o in out):  # fmt: skip
            continue  # nested inside equipment already found
        covered = 0.0
        for b in boxes:
            ix = max(0.0, min(bbox[2], b[2]) - max(bbox[0], b[0]))
            iy = max(0.0, min(bbox[3], b[3]) - max(bbox[1], b[1]))
            covered += ix * iy
        if covered >= BOX_COVER_MAX * bw * bh:
            continue
        if (
            bbox[0] >= (1 - MARGIN) * W
            or bbox[2] <= MARGIN * W
            or bbox[1] >= (1 - MARGIN) * H
            or bbox[3] <= MARGIN * H
        ):
            continue
        if skip is not None and skip[y : y + bh, x : x + bw].mean() > 0.5:
            continue
        if _ortho_share(c, w) >= ORTHO_MAX:
            continue
        hole = np.zeros((H, W), np.uint8)
        cv2.drawContours(hole, [c], -1, 1, cv2.FILLED)
        if ink[hole > 0].mean() > INTERIOR_INK_MAX:
            continue
        if not _attached(ink, hole.astype(bool), w):
            continue
        pad = w + 2
        out.append({
            "bbox_px": [round(max(bbox[0] - pad, 0), 1), round(max(bbox[1] - pad, 0), 1),
                        round(min(bbox[2] + pad, W), 1), round(min(bbox[3] + pad, H), 1)],
            "score": SCORE, "ext": "ext_graph", "origin": "closed_contour",
        })  # fmt: skip
    return out
