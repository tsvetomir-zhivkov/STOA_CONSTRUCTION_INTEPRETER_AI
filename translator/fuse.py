"""Step 8: merge extractor outputs into one schema document; mark duplicates superseded; count priors.

fuse_raster() is the raster-plan pipeline after the extractors' raw results are loaded:
  1. translator.extract.ocr.fuse: OCR texts, room naming, levels (its settings are the defaults here)
  2. dedupe(): two active elements of the same category with IoU > DUP_IOU are one element; the higher-
     confidence one stays, the other gets status "superseded" and superseded_by. Nothing is deleted.
     Segmentation components of one class never overlap, so today this mostly matters once vlm_check adds
     elements (step 9).
  3. apply_priors() (kitchens and bathrooms only, approved by k): see PRIORS below.
  4. translator.scale.estimate, translator.coverage.build.
"""

from __future__ import annotations

import re

from translator import glossary

DUP_IOU = 0.5

# Majority answer per template on the DEV split of questions.jsonl (eval/holdout_files.txt excluded),
# computed 2026-11-14 with eval/probe.py's template classification:
#   cc_kitchens  answers {0: 6, 1: 244, 2: 10} of 260 questions (130 images) -> 1 (93.8%)
#   cc_bathrooms answers {0: 14, 1: 164, 2: 8, 4: 2} of 188 questions (94 images) -> 1 (87.2%)
# Do not add other templates without asking k.
PRIORS = {
    "cc_kitchens": {"type": "kitchen", "majority": 1, "share": 0.938, "category": "kitchens"},
    "cc_bathrooms": {"type": "bathroom", "majority": 1, "share": 0.872, "category": "bathrooms"},
}
PRIOR_CONF = 0.2  # confidence of an assumed element (low on purpose)
PRIOR_EXTRACTOR = "prior"
NOT_STRONG_LABELS = {"tk"}  # tuulikaappi (vestibule): never a strong kitchen/bathroom signal
MIN_STRONG = 2  # independent strong signals needed to keep a count that differs from the majority


def _active(items: list[dict]) -> list[dict]:
    return [x for x in items if x.get("status", "active") == "active"]


# ------------------------------------------------------------------ duplicates


def _geom(el: dict):
    from shapely.geometry import Polygon, box

    poly = el.get("polygon_px")
    if poly and len(poly) >= 3:
        p = Polygon([pt[:2] for pt in poly])
        return p if p.is_valid else p.buffer(0)
    b = el.get("bbox_px")
    return box(*b) if b else None


def iou(a: dict, b: dict) -> float:
    ga, gb = _geom(a), _geom(b)
    if ga is None or gb is None or ga.is_empty or gb.is_empty:
        return 0.0
    inter = ga.intersection(gb).area
    if inter <= 0:
        return 0.0
    return inter / ga.union(gb).area


def dedupe(items: list[dict], category_of, threshold: float = DUP_IOU) -> list[dict]:
    """Mark duplicates superseded in place. Returns [{kept, superseded, iou}] records.

    Higher confidence wins; on a tie the element listed first wins. Elements without geometry never match.
    """
    out = []
    act = sorted(
        (x for x in _active(items) if _geom(x) is not None),
        key=lambda x: -x["confidence"],
    )
    for i, keep in enumerate(act):
        if keep.get("status", "active") != "active":
            continue
        for other in act[i + 1 :]:
            if other.get("status", "active") != "active" or category_of(other) != category_of(keep):
                continue
            v = iou(keep, other)
            if v > threshold:
                other["status"] = "superseded"
                other["superseded_by"] = keep["id"]
                other["flags"] = sorted(set(other.get("flags", [])) | {"duplicate"})
                out.append({"kept": keep["id"], "superseded": other["id"], "iou": round(v, 3)})
    return out


def dedupe_document(doc: dict) -> list[dict]:
    """Duplicates per collection: openings by kind, objects by category, rooms by type, texts by text."""
    records = []
    records += dedupe(doc["openings"], lambda x: x["kind"])
    records += dedupe(doc["objects"], lambda x: (x["category"], x.get("subtype")))
    records += dedupe(doc["rooms"], lambda x: x["type"])
    records += dedupe(doc["texts"], lambda x: glossary.fold(x["text"]))
    if records:
        doc["history"].append(
            {"extractor": "fuse", "action": "dedupe", "entity_ids": [r["superseded"] for r in records],
             "note": f"{len(records)} duplicate(s) superseded (same category, IoU > {DUP_IOU})"}
        )  # fmt: skip
    return records


# ------------------------------------------------------------------ priors


def _region_key(room: dict) -> str | None:
    """The segmentation region a labelled room sits in, or None when it is not a strong signal."""
    if not room.get("text_ids"):
        return None  # segmentation class only, or an assumed element
    flags = set(room.get("flags", []))
    if "label_only" in flags or "assumed_prior" in flags:
        return None  # a label in no region: no room polygon behind it
    label = re.sub(r"[^a-z]", "", glossary.fold(room.get("name") or ""))
    if label in NOT_STRONG_LABELS:
        return None
    if "shares_region" in flags:
        return room.get("parent_room_id")
    return room["id"] if room.get("polygon_px") else None


def strong_signals(rooms: list[dict], rtype: str) -> set[str]:
    """Independent strong signals for a room type: distinct regions holding a label of that type."""
    keys = {_region_key(r) for r in _active(rooms) if r["type"] == rtype}
    keys.discard(None)
    return keys


def _strength(room: dict) -> tuple:
    return (_region_key(room) is not None, bool(room.get("text_ids")), room["confidence"])


def assumed_room(template: str, prior: dict, k: int, detected: int) -> dict:
    """An element assumed from the dev prior: no geometry, never evidence to highlight."""
    return {
        "id": f"room-prior-{template}-{k}",
        "name": None,
        "number": None,
        "label": None,
        "type": prior["type"],
        "subtype": None,
        "is_outdoor": False,
        "polygon_px": None,
        "polygon_m": None,
        "area_m2": None,
        "confidence": PRIOR_CONF,
        "provenance": {
            "extractor": PRIOR_EXTRACTOR,
            "source_ref": f"prior:{template}",
            "details": {
                "majority": prior["majority"],
                "share": prior["share"],
                "detected": detected,
                "note": "assumed from the dev-split majority answer, not read from the drawing",
            },
        },
        "status": "active",
        "flags": ["assumed_prior"],
    }


def apply_prior(doc: dict, template: str, prior: dict | None = None) -> dict:
    """Apply one count prior to a document in place. Returns {branch, detected, final, strong, fired}.

    detected 0           -> add `majority` assumed rooms                         (branch 'zero')
    detected == majority -> keep                                                 (branch 'equal')
    detected != majority -> keep with >= MIN_STRONG independent strong signals   (branch 'kept_strong')
                            otherwise fall back to the majority                  (branch 'fallback'):
                            too few -> add assumed rooms; too many -> the weakest step back to type
                            'unknown' (flag prior_stepped_back; nothing is deleted)
    """
    prior = prior or PRIORS[template]
    rtype, maj = prior["type"], prior["majority"]
    rooms = doc["rooms"]
    found = [r for r in _active(rooms) if r["type"] == rtype]
    detected, strong = len(found), strong_signals(rooms, rtype)
    if detected == maj:
        branch = "equal"
    elif detected == 0:
        branch = "zero"
    elif len(strong) >= MIN_STRONG:
        branch = "kept_strong"
    else:
        branch = "fallback"
    assumed, stepped = [], []
    if branch in ("zero", "fallback") and detected < maj:
        assumed = [assumed_room(template, prior, k, detected) for k in range(maj - detected)]
        rooms.extend(assumed)
    elif branch == "fallback":
        for r in sorted(found, key=_strength, reverse=True)[maj:]:
            r["provenance"].setdefault("details", {})["prior_type"] = r["type"]
            r["type"] = "unknown"
            r["flags"] = sorted(set(r.get("flags", [])) | {"prior_stepped_back"})
            stepped.append(r["id"])
    fired = branch in ("zero", "fallback")
    final = maj if fired else detected
    confs = [r["confidence"] for r in _active(rooms) if r["type"] == rtype]
    conf = round(sum(confs) / len(confs), 4) if confs else 0.0
    if fired:
        note = (f"prior: assumed {maj}, not read from the drawing (detected {detected}, "
                f"{len(strong)} strong signal(s); dev majority {maj} = {prior['share']:.1%})")  # fmt: skip
        cov = {"category": prior["category"], "region": "page", "status": "partial",
               "extractor": PRIOR_EXTRACTOR, "confidence": min(conf, PRIOR_CONF), "note": note}  # fmt: skip
        doc["history"].append(
            {"extractor": PRIOR_EXTRACTOR, "action": f"prior_{branch}",
             "entity_ids": [r["id"] for r in assumed] + stepped, "note": note}
        )  # fmt: skip
    else:
        note = f"detected {detected}" + (
            f"; differs from the dev majority {maj}, kept: {len(strong)} strong signals"
            if branch == "kept_strong"
            else ""
        )
        cov = {"category": prior["category"], "region": "page", "status": "analysed",
               "extractor": "cubicasa5k+ocr", "confidence": conf, "note": note}  # fmt: skip
    doc["coverage"] = [c for c in doc["coverage"] if c["category"] != prior["category"]] + [cov]
    return {"branch": branch, "detected": detected, "final": final, "strong": len(strong), "fired": fired}


def apply_priors(doc: dict, templates=tuple(PRIORS)) -> dict:
    return {t: apply_prior(doc, t) for t in templates}


# ------------------------------------------------------------------ raster pipeline


def fuse_raster(
    doc: dict,
    seg_raw: dict,
    ocr_raw: dict | None,
    read_from: str,
    merge: bool = False,
    label_priority: bool = True,
    kitchen_fallback: bool = True,
    rotation: bool = False,
    floors_rule: str = "max",
    priors: bool = True,
    grid: int = 4,
) -> dict:
    """Raster plan (CubiCasa segmentation + OCR) -> fused document, in place. Returns a summary."""
    from translator import coverage, scale
    from translator.extract import ocr

    summary = ocr.fuse(
        doc, seg_raw, ocr_raw, read_from, merge=merge, label_priority=label_priority,
        kitchen_fallback=kitchen_fallback, rotation=rotation, floors_rule=floors_rule,
    )  # fmt: skip
    summary["duplicates"] = len(dedupe_document(doc))
    if priors:
        summary["priors"] = apply_priors(doc)
    doc["drawing"]["scale"] = scale.estimate(doc)
    coverage.build(doc, grid=grid)
    return summary
