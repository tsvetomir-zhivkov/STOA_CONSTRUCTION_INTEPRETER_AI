"""Raster prep for tracing (plan steps 7 and 8): ink mask, stroke width, ports, erase symbols and text.

The ink mask comes from the translator's preprocessing (invert dark backgrounds, adaptive threshold); the page
grid is never changed, so every coordinate here is an ORIGINAL page pixel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from skimage.morphology import skeletonize

from pipes.reuse import load_image, preprocess


@dataclass
class Prep:
    ink: np.ndarray  # bool, True = dark stroke (un-erased)
    w: float  # stroke width in px, median over the skeleton
    transforms: dict = field(default_factory=dict)
    shape: tuple[int, int] = (0, 0)


def ink_mask(path: Path) -> tuple[np.ndarray, dict]:
    pre = preprocess(load_image(path), source=str(path))
    return pre.binary == 0, pre.transforms


def stroke_width(ink: np.ndarray) -> float:
    dt = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    sk = skeletonize(ink)
    return float(max(1.5, np.median(2 * dt[sk]))) if sk.any() else 1.5


def prep(path: Path) -> Prep:
    ink, tf = ink_mask(path)
    return Prep(ink=ink, w=stroke_width(ink), transforms=tf, shape=ink.shape)


def text_mask(shape: tuple[int, int], texts: list[dict], pad: int = 1) -> np.ndarray:
    """Boolean mask of OCR boxes (polygons in original pixels), grown by pad px."""
    m = np.zeros(shape, np.uint8)
    for t in texts:
        poly = np.round(np.asarray(t["poly"], dtype=float)).astype(np.int32)
        cv2.fillPoly(m, [poly], 1)
    if pad:
        m = cv2.dilate(m, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8))
    return m.astype(bool)


def box_mask(shape: tuple[int, int], boxes: list[list[float]], shrink: float = 1.0) -> np.ndarray:
    m = np.zeros(shape, bool)
    h, w = shape
    for x0, y0, x1, y1 in boxes:
        a, b = int(np.ceil(max(x0 + shrink, 0))), int(np.ceil(max(y0 + shrink, 0)))
        c, d = int(np.floor(min(x1 - shrink, w))), int(np.floor(min(y1 - shrink, h)))
        m[b : d + 1, a : c + 1] = True
    return m


def remove_small(ink: np.ndarray, w: float) -> np.ndarray:
    """Drop connected components smaller than (3w)^2 px: noise and leftover characters."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= (3 * w) ** 2
    return keep[lab]


def erase(
    ink: np.ndarray, w: float, boxes: list[list[float]], text: np.ndarray, extra: np.ndarray | None = None
) -> np.ndarray:
    """Ink with symbol boxes (shrunk by 1 px), OCR text and `extra` (frames, legend, title block) removed,
    then tiny components dropped."""
    out = ink & ~box_mask(ink.shape, boxes) & ~text
    if extra is not None:
        out &= ~extra
    return remove_small(out, w)


def ports(ink_no_text: np.ndarray, w: float, boxes: list[list[float]]) -> list[list[list[float]]]:
    """Per symbol box: points where the skeleton of the (text-free) ink crosses the box border, clustered.

    0 ports = free-standing or decoration, 1 = terminal, 2+ = in-line. Cluster centres, original pixels.
    """
    sk = skeletonize(ink_no_text)
    H, W = sk.shape
    pad = max(2, int(round(w)) + 1)
    out = []
    for x0, y0, x1, y1 in boxes:
        a, b = max(int(x0) - pad, 0), max(int(y0) - pad, 0)
        c, d = min(int(x1) + pad + 1, W), min(int(y1) + pad + 1, H)
        ring = np.zeros((d - b, c - a), np.uint8)
        ring[:] = 1
        ix0, iy0, ix1, iy1 = int(x0) - a, int(y0) - b, int(x1) - a + 1, int(y1) - b + 1
        ring[max(iy0, 0) : iy1, max(ix0, 0) : ix1] = 0  # hollow: only the band just outside the box
        # plus a thin band just inside, so a stub that starts at the border is still caught
        inner = np.zeros_like(ring)
        inner[max(iy0, 0) : iy1, max(ix0, 0) : ix1] = 1
        inner[max(iy0, 0) + pad : iy1 - pad, max(ix0, 0) + pad : ix1 - pad] = 0
        hit = (sk[b:d, a:c] & ((ring | inner) > 0)).astype(np.uint8)
        hit = cv2.dilate(hit, np.ones((3, 3), np.uint8), iterations=max(1, int(round(w))))
        n, _, _, cent = cv2.connectedComponentsWithStats(hit, connectivity=8)
        out.append([[round(float(cx + a), 1), round(float(cy + b), 1)] for cx, cy in cent[1:]])
    return out
