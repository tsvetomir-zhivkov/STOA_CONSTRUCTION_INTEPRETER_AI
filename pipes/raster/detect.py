"""Symbol detector post-processing: raw per-tile detections (page pixels) -> merged boxes -> pipe JSON.

Raw model output is cached in index_pipes/_raw/<set>/<id>.det.json so the GPU is never rerun while tuning.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pipes.ingest.pid_dataset import gt_document
from pipes.write import finalize

MERGE_IOU = 0.5
DEFAULT_CONF = 0.25


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def contained(a, b, frac: float = 0.8) -> bool:
    """a lies >= frac inside b (tile cuts produce a clipped copy of a symbol next to the whole one)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return area > 0 and ix * iy / area >= frac


def area(b) -> float:
    return max(b[2] - b[0], 0) * max(b[3] - b[1], 0)


def merge(
    dets: list[dict],
    conf: float = DEFAULT_CONF,
    iou_thr: float = MERGE_IOU,
    passes: tuple[str, ...] | None = None,
) -> list[dict]:
    """Greedy NMS over raw detections (tile and/or whole-sheet pass), highest score first.

    A box is dropped when it overlaps a kept box (IoU) or is mostly inside one (clipped tile fragment). A larger
    box that mostly contains kept boxes of similar confidence REPLACES them: a whole-sheet box of a big machine
    wins over the fragments the 640 px tiles cut it into.
    """
    keep: list[dict] = []
    pool = (d for d in dets if d["score"] >= conf and (passes is None or d.get("pass", "tile") in passes))
    for d in sorted(pool, key=lambda d: -d["score"]):
        b = d["bbox_px"]
        if any(iou(b, k["bbox_px"]) > iou_thr or contained(b, k["bbox_px"]) for k in keep):
            continue
        keep.append(d)
    # replacement pass: big boxes that contain kept boxes (>= 2x area) take over when nearly as confident
    out = []
    for k in keep:
        inner = [
            o for o in keep
            if o is not k and area(o["bbox_px"]) * 2 <= area(k["bbox_px"]) and contained(o["bbox_px"], k["bbox_px"])
        ]  # fmt: skip
        out.append((k, inner))
    dropped = {id(o) for k, inner in out for o in inner if k["score"] >= 0.6 * o["score"]}
    return [k for k in keep if id(k) not in dropped]


def pred_document(sid: str, split: str, raw: dict, conf: float = DEFAULT_CONF) -> dict:
    """Nodes-only pipe JSON from a raw detection file, written like every route through finalize()."""
    kept = merge(raw["dets"], conf)
    syms = [{"cls_raw": "symbol", "bbox_px": [round(v, 2) for v in d["bbox_px"]]} for d in kept]
    doc = gt_document(sid, split, raw["width"], raw["height"], syms)
    doc["extractors"] = [
        {"id": "ext_det", "kind": "symbol_detector", "name": raw.get("model", "yolo"), "version": None,
         "run_at": None, "cost_usd": raw.get("cost_usd", 0.0)},
    ]  # fmt: skip
    text = json.dumps(doc).replace("ext_human", "ext_det")
    doc = json.loads(text)
    for n, d in zip(doc["nodes"], kept, strict=True):
        n["provenance"]["confidence"] = round(float(d["score"]), 4)
    mean = float(np.mean([d["score"] for d in kept])) if kept else 0.0
    doc["coverage"][0].update(confidence=round(mean, 4), note=f"detector, conf >= {conf}")
    doc["coverage"][1].update(note="pipes are traced in a later step")
    return finalize(doc)


def write_pred(raw_dir: Path, out_dir: Path, split: str, conf: float = DEFAULT_CONF) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(raw_dir.glob("*.det.json")):
        sid = f.name.removesuffix(".det.json")
        doc = pred_document(sid, split, json.loads(f.read_text(encoding="utf-8")), conf)
        (out_dir / f"{sid}.pipe.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        n += 1
    return n
