"""Overlays and write-back previews for VLM outcomes, in index/debug_vlm/ (git-ignored, never scored).

Overlay: the sent region (yellow), the pipeline's elements of that kind (green), the VLM boxes on the page
(red, numbered), each with a size flag:
  - when a pipeline element holds the box centre: 'wider'/'narrower'/'taller'/'shorter' if a side differs
    by more than SIZE_RATIO from that element's box;
  - otherwise against a typical real size, through the page scale (door-width estimate, low confidence):
    'larger than ~X m' / 'smaller than ~Y m'.
Preview: a copy of the document with every outcome written back (all categories enabled), schema-validated.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import cv2

from translator.vlm import gate, images, writeback

SIZE_RATIO = 1.5
TYPICAL_M = {"shower": (0.6, 2.2), "kitchen": (1.2, 9.0), "bathroom": (1.0, 6.0), "doors": (0.5, 2.2),
             "windows": (0.3, 4.0)}  # fmt: skip


def out_dir() -> Path:
    from translator.config import INDEX_DIR

    return INDEX_DIR / "debug_vlm"


def pipeline_elements(doc: dict, category: str) -> list[dict]:
    act = [x for x in doc["rooms"] if x.get("status", "active") == "active"]
    if category in writeback.ROOM_CATS:
        rtype = writeback.ROOM_CATS[category][0]
        return [
            r
            for r in act
            if r["type"] == rtype or r["provenance"].get("details", {}).get("prior_type") == rtype
        ]
    if category == "shower":
        return [
            o
            for o in doc["objects"]
            if o.get("status", "active") == "active" and o["category"] in ("shower", "bathtub")
        ]
    if category in writeback.OPENING_CATS:
        k = writeback.OPENING_CATS[category]
        return [o for o in doc["openings"] if o.get("status", "active") == "active" and o["kind"] == k]
    if category == "floors":
        return [lv for lv in doc["levels"] if lv.get("status", "active") == "active"]
    return []


def size_flag(box, category: str, doc: dict) -> str:
    w, h = box[2] - box[0], box[3] - box[1]
    for el in pipeline_elements(doc, category):
        if el.get("polygon_px") or el.get("bbox_px"):
            if writeback._holds(el, box):
                eb = writeback._bbox(el)
                ew, eh = eb[2] - eb[0], eb[3] - eb[1]
                flags = [
                    n for n, r in (("wider", w / max(ew, 1)), ("taller", h / max(eh, 1))) if r > SIZE_RATIO
                ]
                flags += [
                    n
                    for n, r in (("narrower", ew / max(w, 1)), ("shorter", eh / max(h, 1)))
                    if r > SIZE_RATIO
                ]
                return (
                    ("box " + "/".join(flags) + " than matched element") if flags else "fits matched element"
                )
    sc = (doc["drawing"].get("scale") or {}).get("m_per_px")
    if sc and category in TYPICAL_M:
        lo, hi = TYPICAL_M[category]
        long_m = max(w, h) * sc
        if long_m > hi:
            return f"larger than typical ({long_m:.1f} m > {hi} m, door-width scale)"
        if long_m < lo:
            return f"smaller than typical ({long_m:.1f} m < {lo} m, door-width scale)"
        return f"typical size ({long_m:.1f} m)"
    return "no reference"


def overlay(img, doc: dict, outcome: dict):
    ov = img.copy()
    t = max(2, max(img.shape[:2]) // 400)
    s = outcome["sent"]
    x0, y0 = s["x0"], s["y0"]
    x1, y1 = int(x0 + s["w"] / s["scale"]) - 1, int(y0 + s["h"] / s["scale"]) - 1
    cv2.rectangle(ov, (int(x0), int(y0)), (x1, y1), (0, 200, 255), t)
    for el in pipeline_elements(doc, outcome["category"]):
        b = writeback._bbox(el)
        if b:
            cv2.rectangle(ov, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 170, 0), max(1, t // 2))
    flags = []
    for i, b in enumerate(outcome["boxes_px"]):
        cv2.rectangle(ov, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 0, 230), t)
        org = (int(b[0]) + 2, int(b[1]) + 6 * t + 4)
        cv2.putText(ov, str(i + 1), org, cv2.FONT_HERSHEY_SIMPLEX, 0.25 * t, (0, 0, 230), max(1, t // 2))
        flags.append(f"#{i + 1} {size_flag(b, outcome['category'], doc)}")
    return ov, flags


def write(outcomes: list[dict], root=None) -> list[Path]:
    from translator.store import validate

    folder = out_dir()
    folder.mkdir(parents=True, exist_ok=True)
    written, by_src = [], {}
    for o in outcomes:
        by_src.setdefault(o["source"], []).append(o)
    for src, outs in by_src.items():
        doc = gate.load_doc(src)  # guarded
        img = images.load_page(src, root)  # guarded
        stem = Path(src).stem
        report = {"source": src, "tasks": []}
        for o in outs:
            ov, flags = overlay(img, doc, o)
            p = folder / f"{stem}__{o['category']}.png"
            cv2.imwrite(str(p), ov)
            written.append(p)
            report["tasks"].append({"category": o["category"], "gate": o["gate"], "count": o["count"],
                                    "boxes_px": o["boxes_px"], "box_flags": flags, "cost_usd": o["cost_usd"],
                                    "usage": o["usage"], "answer_text": o["answer_text"]})  # fmt: skip
        after = copy.deepcopy(doc)
        enabled = dict.fromkeys(writeback.ROOM_CATS | writeback.OBJECT_CATS | writeback.OPENING_CATS, True)
        enabled["floors"] = True
        report["writeback"] = [writeback.apply(after, o, enabled=enabled) for o in outs]
        validate(after, where=f"{src} (vlm write-back preview)")
        (folder / f"{stem}.vlm_doc.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        p = folder / f"{stem}.vlm.json"
        p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(p)
    return written
