"""Step 5 regions: frames, title block and legend rectangles, found from the ink and removed before tracing.

A frame is a rectangle drawn with four long straight sides (a side may be interrupted by text, so each side is a
union of collinear segments). It is NOT a pipe loop when no symbol box sits on its border: pipe loops carry
in-line valves, frames do not. Three kinds come out:
  title_block  frame touching the bottom or right page border with >= 3 inner horizontal lines
  legend       frame that contains a legend heading (glossary_pipes.LEGEND_HEADINGS) in its OCR text
  other        any other frame (table cells, borders): only its outline is erased, the content stays
title_block and legend are erased completely and reported as coverage `not_analysed`.
"""

from __future__ import annotations

import cv2
import numpy as np

from pipes.glossary_pipes import LEGEND_HEADINGS

MIN_AREA = 0.01  # share of the page
COVER = 0.5  # a side must be inked over this share of its length (text may interrupt it)


def _segments(mask: np.ndarray, horizontal: bool) -> list[list[float]]:
    """Connected components of a line mask -> [pos, start, end, inked_len] (pos = y for horizontal)."""
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if horizontal:
            out.append([float(cent[i][1]), float(x), float(x + w), float(w)])
        else:
            out.append([float(cent[i][0]), float(y), float(y + h), float(h)])
    return out


def _explained(text: np.ndarray | None, horizontal: bool, pos: float, a: float, b: float) -> bool:
    """A gap in a line is bridged only if erased text covers most of it (text interrupts frame sides)."""
    if b <= a:
        return True
    if text is None:
        return False
    t = np.linspace(a, b, max(4, int(b - a)))
    H, W = text.shape
    if horizontal:
        ys = np.clip(np.round(pos + np.arange(-2, 3))[:, None].astype(int), 0, H - 1)
        xs = np.clip(np.round(t)[None, :].astype(int), 0, W - 1)
    else:
        xs = np.clip(np.round(pos + np.arange(-2, 3))[:, None].astype(int), 0, W - 1)
        ys = np.clip(np.round(t)[None, :].astype(int), 0, H - 1)
    return bool(text[ys, xs].any(axis=0).mean() >= 0.6)


def _merge(segs: list[list[float]], tol: float, small_gap: float, text, horizontal: bool) -> list[dict]:
    """Collinear segments (same pos within tol) -> sides {pos, a, b, cover}. Gaps <= small_gap or text-covered join."""
    sides: list[dict] = []
    for pos, a, b, ln in sorted(segs):
        for s in sides:
            g0, g1 = (s["b"], a) if a >= s["b"] else (b, s["a"]) if b <= s["a"] else (0, 0)
            joins = g1 - g0 <= small_gap or _explained(text, horizontal, pos, g0, g1)
            if abs(s["pos"] - pos) <= tol and joins:
                s["pos"] = (s["pos"] * s["n"] + pos) / (s["n"] + 1)
                s["n"] += 1
                s["a"], s["b"], s["len"] = min(s["a"], a), max(s["b"], b), s["len"] + ln
                break
        else:
            sides.append({"pos": pos, "a": a, "b": b, "len": ln, "n": 1})
    for s in sides:
        s["cover"] = s["len"] / max(s["b"] - s["a"], 1.0)
    return sides


def find_frames(ink: np.ndarray, w: float, text: np.ndarray | None = None) -> list[list[float]]:
    H, W = ink.shape
    L = int(max(30, 0.05 * min(W, H)))
    u8 = ink.astype(np.uint8)
    hm = cv2.morphologyEx(u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    vm = cv2.morphologyEx(u8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    tol = max(4.0, 2 * w)
    gap = 3 * w
    hs = [
        s
        for s in _merge(_segments(hm, True), tol, gap, text, True)
        if s["cover"] >= COVER and s["b"] - s["a"] >= L
    ]
    vs = [
        s
        for s in _merge(_segments(vm, False), tol, gap, text, False)
        if s["cover"] >= COVER and s["b"] - s["a"] >= L
    ]
    rects = []
    for i, top in enumerate(hs):
        for bot in hs[i + 1 :]:
            t, b = (top, bot) if top["pos"] < bot["pos"] else (bot, top)
            if b["pos"] - t["pos"] < L:
                continue
            x0, x1 = max(t["a"], b["a"]), min(t["b"], b["b"])
            if x1 - x0 < L or (x1 - x0) < 0.85 * min(t["b"] - t["a"], b["b"] - b["a"]):
                continue
            left = [
                v
                for v in vs
                if abs(v["pos"] - x0) <= 3 * tol
                and v["a"] <= t["pos"] + 3 * tol
                and v["b"] >= b["pos"] - 3 * tol
            ]
            right = [
                v
                for v in vs
                if abs(v["pos"] - x1) <= 3 * tol
                and v["a"] <= t["pos"] + 3 * tol
                and v["b"] >= b["pos"] - 3 * tol
            ]
            if left and right:
                rects.append([x0, t["pos"], x1, b["pos"]])
    keep = []
    for r in rects:
        if (r[2] - r[0]) * (r[3] - r[1]) >= MIN_AREA * W * H and not any(
            all(abs(a - c) < 2 * tol for a, c in zip(r, k, strict=True)) for k in keep
        ):
            keep.append(r)
    return keep


def _inner_h_lines(ink: np.ndarray, rect, w: float) -> int:
    x0, y0, x1, y1 = (int(v) for v in rect)
    m = int(max(4, 2 * w)) + 2  # the frame's own top and bottom sides are not rows of the block
    sub = ink[y0 + m : y1 - m, x0 + m : x1 - m].astype(np.uint8)
    if sub.size == 0:
        return 0
    L = max(10, int(0.5 * (x1 - x0)))
    hm = cv2.morphologyEx(
        sub,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)),
        borderType=cv2.BORDER_CONSTANT,
        borderValue=0,
    )  # outside the crop is empty: a line that reaches the crop edge is not made longer
    ys = np.where(hm.sum(axis=1) > 0)[0]
    return int(1 + (np.diff(ys) > max(3, 2 * w)).sum()) if len(ys) else 0


def attached(ink: np.ndarray, rect, w: float) -> bool:
    """True when ink touches a side from inside or outside (a pipe joining it): a frame has clear sides.

    Looks at a 4 px band at 2w+2 px from each side, on both sides of the line, corners excluded.
    """
    H, W = ink.shape
    x0, y0, x1, y1 = (int(round(v)) for v in rect)
    d0 = int(2 * w + 2)
    d1 = d0 + 4
    cx = d1 + 8  # corner exclusion
    band = np.zeros((H, W), bool)
    ys = [(y0 - d1, y0 - d0), (y0 + d0, y0 + d1), (y1 + d0, y1 + d1), (y1 - d1, y1 - d0)]
    xs = [(x0 - d1, x0 - d0), (x0 + d0, x0 + d1), (x1 + d0, x1 + d1), (x1 - d1, x1 - d0)]
    for r0, r1 in ys:  # horizontal sides (top, bottom): bands above and below the line
        band[max(r0, 0) : max(r1, 0), max(x0 + cx, 0) : max(x1 - cx, 0)] = True
    for c0, c1 in xs:
        band[max(y0 + cx, 0) : max(y1 - cx, 0), max(c0, 0) : max(c1, 0)] = True
    return bool((ink & band).sum() > 18 * w)


def classify(ink, w, frames, boxes, texts, text_mask=None) -> list[dict]:
    """-> [{kind, bbox_px, erase: 'full'|'outline'}] for frames that are not pipe loops."""
    H, W = ink.shape
    tol = max(4.0, 2 * w)
    out = []
    for r in frames:
        x0, y0, x1, y1 = r
        on_border = 0
        small = 0.5 * min(x1 - x0, y1 - y0)  # an in-line symbol is small next to its frame
        for bx0, by0, bx1, by1 in boxes:
            if max(bx1 - bx0, by1 - by0) >= small:
                continue  # a box as big as the frame is a detector error, not a valve on the line
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            near_h = min(abs(cy - y0), abs(cy - y1)) <= tol + (by1 - by0) / 2 and x0 - tol <= cx <= x1 + tol
            near_v = min(abs(cx - x0), abs(cx - x1)) <= tol + (bx1 - bx0) / 2 and y0 - tol <= cy <= y1 + tol
            on_border += near_h or near_v
        if on_border or attached(ink & ~text_mask if text_mask is not None else ink, r, w):
            continue  # symbols sit on its sides or a pipe joins it: a pipe loop, not a frame
        inside = " ".join(t["text"].upper() for t in texts if x0 <= np.mean([p[0] for p in t["poly"]]) <= x1
                          and y0 <= np.mean([p[1] for p in t["poly"]]) <= y1)  # fmt: skip
        touches = x1 >= W - 3 * tol or y1 >= H - 3 * tol
        if touches and _inner_h_lines(ink, r, w) >= 3:
            kind, erase = "title_block", "full"
        elif any(h in inside for h in LEGEND_HEADINGS):
            kind, erase = "legend", "full"
        else:
            kind, erase = "other", "outline"
        out.append({"kind": kind, "bbox_px": [round(v, 1) for v in r], "erase": erase})
    return out


def mask(shape, regions: list[dict], w: float) -> np.ndarray:
    """Pixels to erase: whole rectangle for title_block/legend, a ring along the outline for other frames."""
    H, W = shape
    m = np.zeros((H, W), bool)
    pad = int(max(4, 2 * w)) + 1
    for rg in regions:
        x0, y0, x1, y1 = (int(round(v)) for v in rg["bbox_px"])
        if rg["erase"] == "full":
            m[max(y0 - pad, 0) : y1 + pad + 1, max(x0 - pad, 0) : x1 + pad + 1] = True
        else:
            ring = np.zeros((H, W), bool)
            ring[max(y0 - pad, 0) : y1 + pad + 1, max(x0 - pad, 0) : x1 + pad + 1] = True
            ring[y0 + pad : max(y1 - pad, y0 + pad), x0 + pad : max(x1 - pad, x0 + pad)] = False
            m |= ring
    return m
