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


def merge(dets: list[dict], conf: float = DEFAULT_CONF, iou_thr: float = MERGE_IOU) -> list[dict]:
    """Greedy NMS over the tile detections, highest score first; clipped duplicates are absorbed."""
    keep: list[dict] = []
    for d in sorted((d for d in dets if d["score"] >= conf), key=lambda d: -d["score"]):
        if any(
            iou(d["bbox_px"], k["bbox_px"]) > iou_thr or contained(d["bbox_px"], k["bbox_px"]) for k in keep
        ):
            continue
        keep.append(d)
    return keep


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
