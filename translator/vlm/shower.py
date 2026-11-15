"""Showers + bathtubs with a VLM: variant A (one downscaled page) or B (one crop per wet-room region).

B: wet regions (gate.wet_regions) are padded; padded boxes that overlap are merged into one crop, so no
area is sent twice. Counts are summed over crops, minus boxes that two crops both report (IoU > DUP_IOU on
the page), so an item seen in two crops counts once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from translator.vlm import images, prompts
from translator.vlm.base import BaseClient
from translator.vlm.gate import wet_regions

DUP_IOU = 0.3
MERGE_OVERLAP = 0.2  # padded crops overlapping by this share of the smaller one become one crop


def _bbox(poly) -> list[float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]


def _inter(a, b) -> float:
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(a) -> float:
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


def iou(a, b) -> float:
    i = _inter(a, b)
    return i / (_area(a) + _area(b) - i) if i else 0.0


def crop_boxes(doc: dict, w: int, h: int) -> list[dict]:
    """Merged padded page boxes to crop, each with the room ids it covers."""
    boxes = [
        {"box": images.padded_box(_bbox(r["polygon_px"]), w, h), "rooms": [r["id"]]} for r in wet_regions(doc)
    ]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i]["box"], boxes[j]["box"]
                if _inter(a, b) > MERGE_OVERLAP * min(_area(a), _area(b)):
                    boxes[i] = {"box": [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])],
                                "rooms": boxes[i]["rooms"] + boxes[j]["rooms"]}  # fmt: skip
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return boxes


@dataclass
class ShowerResult:
    variant: str
    count: int
    boxes_px: list[list[float]]
    cost_usd: float
    calls: list[dict] = field(default_factory=list)


def count_page(client: BaseClient, img: np.ndarray, source: str) -> ShowerResult:
    data, crop = images.page_image(img)
    r = client.check(data, "shower", prompts.shower("page"), source=source, crop=crop)
    call = {"region": "page", "crop": crop, "count": r.count, "boxes_px": r.boxes_px,
            "cost_usd": r.raw["cost_usd"], "usage": r.usage, "answer_text": r.raw["answer_text"],
            "cached": r.cached}  # fmt: skip
    return ShowerResult("A_page", r.count, r.boxes_px, r.raw["cost_usd"], [call])


def count_crops(client: BaseClient, img: np.ndarray, doc: dict, source: str) -> ShowerResult:
    h, w = img.shape[:2]
    calls, all_boxes, total = [], [], 0
    prompt = prompts.shower("crop")
    for cb in crop_boxes(doc, w, h):
        data, crop, padded = images.crop_image(img, cb["box"], min_side=images.MIN_CROP_SIDE_PX)
        r = client.check(data, "shower", prompt, source=source, crop=crop)
        calls.append({"region": padded, "rooms": cb["rooms"], "crop": crop, "count": r.count,
                      "boxes_px": r.boxes_px, "cost_usd": r.raw["cost_usd"], "usage": r.usage,
                      "answer_text": r.raw["answer_text"], "cached": r.cached})  # fmt: skip
        total += r.count
        all_boxes.append(r.boxes_px)
    # an item reported by two crops counts once
    kept: list[list[float]] = []
    dups = 0
    for boxes in all_boxes:
        new = []
        for b in boxes:
            if any(iou(b, k) > DUP_IOU for k in kept):
                dups += 1
            else:
                new.append(b)
        kept += new
    cost = sum(c["cost_usd"] for c in calls)
    return ShowerResult("B_crops", max(0, total - dups), kept, cost, calls)
