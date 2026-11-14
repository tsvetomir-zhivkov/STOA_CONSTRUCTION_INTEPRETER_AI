"""Step 7: OCR text, glossary room naming, room merging and storeys for raster plans (CPU only).

The GPU part (PaddleOCR, Latin-script model) lives in translator/modal_app.py (class Ocr). It reads the
ocr_gray / ocr_upscaled copies from translator.raster.preprocess, once as drawn and once flipped left-right,
and maps every box back to ORIGINAL image pixels with to_original() below. One raw file per image:
index/_raw/ocr/<name>.ocr.json. Everything here works from those raw files, so tuning never reruns the GPU.

fuse() adds to a CubiCasa document (translator.extract.cubicasa.to_parts output):
  orientation  the reading (as drawn or mirrored) with more glossary hits, then higher mean confidence;
               a mirrored plan gets drawing.transforms {"kind": "other", "note": "mirrored"}
  texts[]      every OCR hit, with a role: room_label, level_title, area, dimension, other
  rooms        a room label names the room whose polygon holds its centre;
               more labels in one region become extra rooms (flag shares_region); a label in no region
               becomes a room of its own (flag label_only, low confidence). Unlabelled regions keep the
               segmentation class, except (label_priority, default on) when the page has a labelled room of
               the same type: then the region steps back to type 'unknown' (flag unlabelled_region), so rooms
               are counted by label, not by segmentation fragment. Optional (merge, default off): same-class
               regions within MERGE_GAP_PX are merged, parts kept as superseded. On the dev split merging
               changed no kitchen count and cost bedrooms 2-4 points; label priority fixed the kitchens.
  kitchens     (kitchen_fallback, default on) when no kitchen label was read, the segmentation Kitchen class
               counts as one kitchen: the largest Kitchen region; the others step back (kitchen_fragment).
  rotated      (rotation, default OFF) a second reading of a copy turned 90 degrees clockwise, for vertical
               labels, run on the GPU only when needs_rotation() says so. Merged per box (merge_rotated).
               On the dev split it found 16% more room labels but lowered bedrooms and kitchens, so it is off.
  levels[]     storey titles (1. KRS, KELLARI, ...); without titles, one level per building blob (connected
               wall/room mask). If titles and blobs disagree, coverage 'floors' is partial, low confidence.
               floors_rule 'max' (default, agreed with k): whichever signal counts more storeys, because
               OCR misses storey titles far more often than it invents them; 'titles' = titles first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np

from translator import glossary

EXTRACTOR = "ocr"
RAW_DIR_NAME = "ocr"
LANG = "latin_PP-OCRv3_mobile_rec"  # PaddleOCR Latin-script recogniser (keeps ä/ö/å); never English

MIRROR_CONF_MARGIN = 0.05  # equal glossary hits: the flipped reading must be this much more confident
LABEL_ONLY_CONF = 0.3  # confidence factor for a room made from a label that lies in no region
SHARED_REGION_CONF = 0.6  # confidence factor for the second+ label in one region
MERGE_GAP_PX = 4  # same-class room regions closer than this are one room
BLOB_CLASSES_EXCLUDED = {"Background", "Outdoor", "Railing"}  # building mask = walls + indoor rooms
BLOB_DILATE_FRAC = 0.01  # close small gaps in the building mask: 1% of the longest side
BLOB_MIN_SHARE = 0.15  # a building blob counts if it is at least this share of the largest one

# rotated pass (vertical labels): triggered from the as-drawn reading
ROT_TALL_RATIO = 1.5  # a box at least this much taller than wide holds vertical text
ROT_MIN_TALL = 2  # this many tall boxes trigger the rotated pass
ROT_LOW_SCORE = 0.7  # ... or this share of boxes below this score
ROT_LOW_SHARE = 0.4
ROT_MIN_BOXES = 4
ROT_OVERLAP = 0.3  # boxes overlap when the intersection covers this share of the smaller box

_AREA = re.compile(r"\d+\s*([,.]\s*\d+)?\s*(m2|m²|m\^2|m\?)")
_DIMENSION = re.compile(r"[\d\s.,x×*/+\-]+")


# ------------------------------------------------------------------ shared with the GPU side


def to_original(poly, scale: float, flipped: bool, width_s: int, rotated: bool = False) -> list[list[float]]:
    """A box from the OCR input -> original pixels.

    The input was upscaled by `scale`, and either flipped left-right or rotated 90 degrees clockwise
    (cv2.ROTATE_90_CLOCKWISE). width_s is the width of the input image the box is in.
    """
    out = []
    for x, y in poly:
        x, y = float(x), float(y)
        if flipped:
            x = width_s - x  # continuous coordinates: the flipped image's x maps to width - x
        if rotated:  # clockwise: (x, y) -> (H - y, x), so back: x = y_r, y = H - x_r (H = rotated width)
            x, y = y, width_s - x
        out.append([round(x / scale, 1), round(y / scale, 1)])
    return out


def items_from_result(
    texts, scores, polys, scale: float, flipped: bool, width_s: int, rotated: bool = False
) -> list[dict]:
    """PaddleOCR rec_texts / rec_scores / rec_polys -> raw items in original pixels."""
    items = []
    for text, score, poly in zip(texts, scores, polys, strict=True):
        if not str(text).strip():
            continue
        items.append(
            {
                "text": str(text),
                "score": round(float(score), 4),
                "poly": to_original(
                    np.asarray(poly).reshape(-1, 2).tolist(), scale, flipped, width_s, rotated
                ),
            }
        )
    return items


# ------------------------------------------------------------------ local: raw files


def raw_dir() -> Path:
    from translator.config import INDEX_DIR

    return INDEX_DIR / "_raw" / RAW_DIR_NAME


def raw_path(name: str) -> Path:
    return raw_dir() / f"{name}.ocr.json"


def load_raw(name: str) -> dict | None:
    p = raw_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------ text roles and orientation


def text_role(text: str) -> str:
    if glossary.level_title(text):
        return "level_title"
    if glossary.is_unit_summary(text):  # '1H+KK+ALK+S': the apartment, not a room
        return "unit_label"
    if glossary.ocr_room_types(text):
        return "room_label"
    s = glossary.fold(text)
    if _AREA.search(s):
        return "area"
    if _DIMENSION.fullmatch(s) and sum(ch.isdigit() for ch in s) >= 3:
        return "dimension"
    return "other"


def orientation_stats(items: list[dict]) -> dict:
    hits = sum(text_role(i["text"]) in ("room_label", "level_title") for i in items)
    mean = float(np.mean([i["score"] for i in items])) if items else 0.0
    return {"texts": len(items), "hits": hits, "mean_score": round(mean, 4)}


def choose_orientation(raw: dict) -> tuple[str, list[dict], dict]:
    """('original' | 'flipped', items, stats of both). Flipped wins on more glossary hits, or on equal
    (non-zero) hits with a clearly higher mean confidence. Otherwise the plan is read as drawn."""
    o_items = raw["orientations"]["original"]
    f_items = raw["orientations"].get("flipped", [])
    o, f = orientation_stats(o_items), orientation_stats(f_items)
    flipped = f["hits"] > o["hits"] or (
        f["hits"] == o["hits"] > 0 and f["mean_score"] > o["mean_score"] + MIRROR_CONF_MARGIN
    )
    stats = {"original": o, "flipped": f}
    return ("flipped", f_items, stats) if flipped else ("original", o_items, stats)


def needs_rotation(items: list[dict]) -> tuple[bool, str]:
    """Run the rotated pass? Yes when the as-drawn reading has tall boxes or mostly low-confidence boxes."""
    tall = 0
    for it in items:
        x0, y0, x1, y1 = _bbox(it["poly"])
        if (y1 - y0) >= ROT_TALL_RATIO * max(x1 - x0, 1e-6):
            tall += 1
    low = sum(it["score"] < ROT_LOW_SCORE for it in items)
    share = low / len(items) if items else 0.0
    if tall >= ROT_MIN_TALL:
        return True, f"{tall} tall boxes"
    if len(items) >= ROT_MIN_BOXES and share >= ROT_LOW_SHARE:
        return True, f"{low}/{len(items)} boxes below {ROT_LOW_SCORE}"
    return False, f"{tall} tall boxes, {low}/{len(items)} low-confidence"


def _overlap(a, b) -> float:
    ax0, ay0, ax1, ay1 = _bbox(a)
    bx0, by0, bx1, by1 = _bbox(b)
    iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    small = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return iw * ih / small if small > 0 else 0.0


def merge_rotated(base: list[dict], rotated: list[dict]) -> tuple[list[dict], int]:
    """Per box: a rotated-pass box replaces the overlapping normal-pass boxes when it scores higher than all
    of them, or when it names a room and none of them does. A rotated box that overlaps nothing is kept
    only if it names a room. Returns (items, number taken from the rotated pass)."""
    out = [dict(b) for b in base]
    taken = 0
    for r in rotated:
        over = [
            i
            for i, b in enumerate(out)
            if b.get("orientation") != "rotated" and _overlap(b["poly"], r["poly"]) >= ROT_OVERLAP
        ]
        r_hit = bool(glossary.ocr_room_types(r["text"]))
        if over:
            higher = r["score"] > max(out[i]["score"] for i in over)
            missed = r_hit and not any(glossary.ocr_room_types(out[i]["text"]) for i in over)
            if not (higher or missed):
                continue
            out = [b for i, b in enumerate(out) if i not in over]
        elif not r_hit:
            continue
        out.append({**r, "orientation": "rotated"})
        taken += 1
    return out, taken


def _bbox(poly) -> list[float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]


def _angle(poly) -> float:
    (x0, y0), (x1, y1) = poly[0], poly[1]
    return round(float(np.degrees(np.arctan2(y1 - y0, x1 - x0))), 1)


def _centre(poly) -> tuple[float, float]:
    b = _bbox(poly)
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


# ------------------------------------------------------------------ rooms


def _shape(poly):
    from shapely.geometry import Polygon

    p = Polygon(poly)
    return p if p.is_valid else p.buffer(0)


_REPLACED = ("id", "polygon_px", "confidence", "provenance")


def _find(parent: list[int], i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def merge_rooms(rooms: list[dict], gap_px: float = MERGE_GAP_PX) -> tuple[list[dict], list[dict]]:
    """Merge same-class regions closer than gap_px. Returns (rooms incl. superseded parts, merge records)."""
    from shapely.ops import unary_union

    active = [r for r in rooms if r.get("status", "active") == "active" and r.get("polygon_px")]
    by_class: dict[str, list[dict]] = {}
    for r in active:
        by_class.setdefault(r.get("subtype") or r["type"], []).append(r)
    out, merges = list(rooms), []
    for cls, group in by_class.items():
        if len(group) < 2:
            continue
        grown = [_shape(r["polygon_px"]).buffer(gap_px / 2) for r in group]
        parent = list(range(len(group)))
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if grown[i].intersects(grown[j]):
                    parent[_find(parent, i)] = _find(parent, j)
        clusters: dict[int, list[int]] = {}
        for i in range(len(group)):
            clusters.setdefault(_find(parent, i), []).append(i)
        for n, idx in enumerate(c for c in clusters.values() if len(c) > 1):
            parts = [group[i] for i in idx]
            geom = unary_union([grown[i] for i in idx]).buffer(-gap_px / 2)
            if geom.geom_type != "Polygon":  # parts only touched through the grown margin
                geom = unary_union([grown[i] for i in idx])
                if geom.geom_type != "Polygon":
                    geom = max(geom.geoms, key=lambda g: g.area)
            geom = geom.simplify(1.0)
            areas = [_shape(p["polygon_px"]).area for p in parts]
            conf = float(np.average([p["confidence"] for p in parts], weights=areas))
            base = parts[int(np.argmax(areas))]
            mid = f"room-{glossary.fold(cls).replace(' ', '_')}-merged-{n}"
            merged = {
                **{k: v for k, v in base.items() if k not in _REPLACED},
                "id": mid,
                "polygon_px": [[round(x, 1), round(y, 1)] for x, y in list(geom.exterior.coords)[:-1]],
                "confidence": round(conf, 4),
                "provenance": {
                    "extractor": base["provenance"]["extractor"],
                    "source_ref": "merge:" + "+".join(p["provenance"]["source_ref"] for p in parts),
                    **({"file": base["provenance"]["file"]} if base["provenance"].get("file") else {}),
                    "details": {"merged_from": [p["id"] for p in parts], "gap_px": gap_px, "class": cls},
                },
                "status": "active",
                "flags": sorted(set(base.get("flags", [])) | {"merged"}),
            }
            for p in parts:
                p["status"] = "superseded"
                p["superseded_by"] = mid
            out.append(merged)
            merges.append({"id": mid, "class": cls, "parts": [p["id"] for p in parts]})
    return out, merges


def name_rooms(
    rooms: list[dict],
    texts: list[dict],
    read_from: str,
    label_priority: bool = False,
    kitchen_fallback: bool = False,
) -> tuple[list[dict], dict]:
    """Attach room_label texts to rooms (see the module doc); sets texts' room_id. -> (rooms, stats)."""
    from shapely.geometry import Point

    active = [r for r in rooms if r.get("status", "active") == "active" and r.get("polygon_px")]
    shapes = [_shape(r["polygon_px"]) for r in active]
    by_room: dict[str, list[tuple[dict, list[str]]]] = {}
    label_only = []
    for t in texts:
        if t["role"] != "room_label":
            continue
        types = glossary.ocr_room_types(t["text"])
        pt = Point(*_centre(t["polygon_px"]))
        hits = [i for i, s in enumerate(shapes) if s.contains(pt)]
        if hits:
            i = min(hits, key=lambda k: shapes[k].area)
            by_room.setdefault(active[i]["id"], []).append((t, types))
        else:
            label_only.append((t, types))

    def label_room(t, rtype, k, conf, flags, parent=None):
        return {
            "id": f"room-label-{t['id'].removeprefix('text-')}-{k}",
            "name": t["text"],
            "number": None,
            "label": t["text"],
            "type": rtype,
            "subtype": None,
            "is_outdoor": rtype in glossary.OUTDOOR_TYPES,
            "parent_room_id": parent,
            "polygon_px": t["polygon_px"],
            "polygon_m": None,
            "area_m2": None,
            "confidence": round(conf, 4),
            "provenance": {
                "extractor": EXTRACTOR,
                "source_ref": t["provenance"]["source_ref"],
                "file": read_from,
                "details": {"text_id": t["id"], "region": parent},
            },
            "status": "active",
            "flags": flags,
            "text_ids": [t["id"]],
        }

    out = list(rooms)
    stats = {"labels": 0, "named_regions": 0, "shared": 0, "label_only": 0}
    rid_shape = {r["id"]: s for r, s in zip(active, shapes, strict=True)}
    for room in active:
        found = by_room.get(room["id"])
        if not found:
            continue
        anchor = rid_shape[room["id"]].representative_point()
        found.sort(key=lambda ft: anchor.distance(Point(*_centre(ft[0]["polygon_px"]))))
        first_t, first_types = found[0]
        seg_type, seg_conf = room["type"], room["confidence"]
        room.update(
            name=first_t["text"],
            label=first_t["text"],
            type=first_types[0],
            is_outdoor=first_types[0] in glossary.OUTDOOR_TYPES or room.get("is_outdoor", False),
            confidence=round(float(first_t["confidence"]), 4),
            text_ids=[first_t["id"]],
        )
        room["provenance"].setdefault("details", {}).update(
            type_from="ocr", text_id=first_t["id"], seg_type=seg_type, seg_confidence=seg_conf
        )
        first_t["room_id"] = room["id"]
        stats["named_regions"] += 1
        extra = [(first_t, t) for t in first_types[1:]] + [(t, ty) for t, types in found[1:] for ty in types]
        for k, (t, rtype) in enumerate(extra):
            r = label_room(t, rtype, k, SHARED_REGION_CONF * t["confidence"], ["shares_region"], room["id"])
            out.append(r)
            if t is not first_t:
                t["room_id"] = r["id"]
            stats["shared"] += 1
    stats["stray_kitchen_skipped"] = 0
    for t, types in label_only:
        for k, rtype in enumerate(types):
            single = len(re.sub(r"[^a-z]", "", glossary.fold(t["text"]))) == 1
            has_kitchen = any(x["type"] == "kitchen" and x.get("status", "active") == "active" for x in out)
            if (
                rtype == "kitchen" and single and has_kitchen
            ):  # a stray 'K' outside every room: no 2nd kitchen
                stats["stray_kitchen_skipped"] += 1
                continue
            r = label_room(t, rtype, k, LABEL_ONLY_CONF * t["confidence"], ["label_only", "low_confidence"])
            out.append(r)
            if k == 0:
                t["room_id"] = r["id"]
            stats["label_only"] += 1
    stats["labels"] = sum(len(v) for v in by_room.values()) + len(label_only)

    if label_priority:  # count labelled rooms, not regions: unlabelled regions of a labelled type step back
        labelled = {r["type"] for r in out if r.get("text_ids") and r.get("status", "active") == "active"}
        for r in out:
            if r.get("status", "active") == "active" and not r.get("text_ids") and r["type"] in labelled:
                r["provenance"].setdefault("details", {})["seg_type"] = r["type"]
                r["type"] = "unknown"
                r["flags"] = sorted(set(r.get("flags", [])) | {"unlabelled_region"})
    if kitchen_fallback:  # no kitchen label read: the segmentation Kitchen class counts as one kitchen
        act = [r for r in out if r.get("status", "active") == "active"]
        if not any(r["type"] == "kitchen" and r.get("text_ids") for r in act):
            seg_k = [r for r in act if r["type"] == "kitchen" and r.get("polygon_px")]
            if len(seg_k) > 1:
                keep = max(seg_k, key=lambda r: _shape(r["polygon_px"]).area)
                for r in seg_k:
                    if r is not keep:
                        r["provenance"].setdefault("details", {})["seg_type"] = r["type"]
                        r["type"] = "unknown"
                        r["flags"] = sorted(set(r.get("flags", [])) | {"kitchen_fragment"})
                stats["kitchen_fragments"] = len(seg_k) - 1
    return out, stats


# ------------------------------------------------------------------ storeys


def building_blobs(seg_raw: dict) -> list[list[float]]:
    """Bounding boxes of separate buildings in the segmentation (walls + indoor rooms), largest first."""
    h, w = seg_raw["height_px"], seg_raw["width_px"]
    mask = np.zeros((h, w), np.uint8)
    for c in seg_raw["rooms"]:
        if c["class"] not in BLOB_CLASSES_EXCLUDED:
            cv2.fillPoly(mask, [np.asarray(c["polygon"], np.int32)], 1)
    if not mask.any():
        return []
    k = max(3, round(max(h, w) * BLOB_DILATE_FRAC))
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n < 2:
        return []
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = [i + 1 for i in np.argsort(-areas) if areas[i] >= BLOB_MIN_SHARE * areas.max()]
    x, y, w_, h_ = (stats[:, c] for c in range(4))
    return [[float(x[i]), float(y[i]), float(x[i] + w_[i]), float(y[i] + h_[i])] for i in keep]


FLOORS_RULES = ("titles", "max")


def levels(
    texts: list[dict], blobs: list[list[float]], read_from: str, rule: str = "titles"
) -> tuple[list[dict], dict]:
    """levels[] and the 'floors' coverage record, from storey titles and building blobs.

    rule 'titles': storey titles when any were read, else the building blobs.
    rule 'max': whichever signal counts more storeys (titles on a tie).
    """
    assert rule in FLOORS_RULES, rule
    titles: dict[str, dict] = {}
    for t in texts:
        if t["role"] == "level_title":
            titles.setdefault(glossary.level_title(t["text"]), t)
    n_t, n_b = len(titles), len(blobs)
    agree = n_t == n_b
    out = []
    if titles and (rule == "titles" or n_t >= n_b):
        conf = 0.9 if agree else 0.4
        for i, (key, t) in enumerate(titles.items()):
            out.append(
                {
                    "id": f"level-{i}",
                    "name": t["text"],
                    "elevation_m": None,
                    "height_m": None,
                    "bbox_px": t["bbox_px"],
                    "confidence": round(conf * t["confidence"], 4),
                    "provenance": {
                        "extractor": EXTRACTOR,
                        "source_ref": t["provenance"]["source_ref"],
                        "file": read_from,
                        "details": {"title": key, "text_id": t["id"], "blobs": n_b},
                    },
                    "flags": [] if agree else ["check_this"],
                }
            )
    else:
        conf = 0.6 if n_b == 1 and not titles else 0.4
        for i, b in enumerate(blobs):
            out.append(
                {
                    "id": f"level-{i}",
                    "name": f"floor {i + 1}",
                    "elevation_m": None,
                    "height_m": None,
                    "bbox_px": b,
                    "confidence": conf,
                    "provenance": {
                        "extractor": "cubicasa5k",
                        "source_ref": f"blob:{i}",
                        "file": read_from,
                        "details": {
                            "from": "building blob in the wall/room mask",
                            "titles": [t["text"] for t in titles.values()],
                        },
                    },
                    "flags": ["unnamed"] + ([] if n_b == 1 and not titles else ["check_this"]),
                }
            )
    if titles and not agree:
        status, cov_conf = "partial", 0.3
    elif not titles and not blobs:
        status, cov_conf = "not_analysed", 0.0
    else:
        status, cov_conf = "analysed", conf
    note = f"{n_t} storey title(s), {n_b} building blob(s)" + ("" if agree or not titles else ": disagree")
    by = EXTRACTOR if titles else "cubicasa5k"
    note += f"; rule {rule}"
    cov = {"category": "floors", "region": "page", "status": status, "extractor": by,
           "confidence": cov_conf, "note": note}  # fmt: skip
    return out, cov


# ------------------------------------------------------------------ fusion into a document


def texts_from_items(items: list[dict], orientation: str, read_from: str, model: str) -> list[dict]:
    out = []
    for i, it in enumerate(items):
        side = it.get("orientation", orientation)
        out.append(
            {
                "id": f"text-{i}",
                "text": it["text"],
                "bbox_px": _bbox(it["poly"]),
                "polygon_px": it["poly"],
                "angle_deg": _angle(it["poly"]),
                "role": text_role(it["text"]),
                "room_id": None,
                "confidence": min(1.0, max(0.0, it["score"])),
                "provenance": {
                    "extractor": EXTRACTOR,
                    "source_ref": f"ocr:{side}:{i}",
                    "file": read_from,
                    "details": {"orientation": side, "model": model},
                },
                "status": "active",
            }
        )
    return out


def _replace_coverage(coverage: list[dict], record: dict) -> list[dict]:
    return [c for c in coverage if c["category"] != record["category"]] + [record]


def fuse(
    doc: dict,
    seg_raw: dict,
    ocr_raw: dict | None,
    read_from: str,
    merge: bool = False,
    label_priority: bool = True,
    kitchen_fallback: bool = True,
    rotation: bool = False,
    floors_rule: str = "max",
) -> dict:
    """Add OCR texts, room names, merged rooms and levels to a CubiCasa document. Returns a summary."""
    summary: dict = {}
    rooms = doc["rooms"]
    if merge:
        rooms, merges = merge_rooms(rooms)
        summary["merged"] = len(merges)
        doc["history"].append(
            {"extractor": "cubicasa5k", "action": "merge_rooms", "entity_ids": [m["id"] for m in merges],
             "note": f"{len(merges)} merged room(s) from same-class regions within {MERGE_GAP_PX} px"}
        )  # fmt: skip

    texts: list[dict] = []
    if ocr_raw is None:
        doc["history"].append({"extractor": EXTRACTOR, "action": "skip", "note": "no raw OCR file yet"})
    else:
        orientation, items, stats = choose_orientation(ocr_raw)
        model = ocr_raw.get("model", {}).get("rec_model", LANG)
        rot = ocr_raw["orientations"].get("rotated")
        if rotation and rot:
            items, taken = merge_rotated([{**i, "orientation": orientation} for i in items], rot)
            summary["rotated_taken"] = taken
            if taken:
                doc["drawing"]["transforms"].append(
                    {"kind": "other", "from": "original", "to": "ocr_input",
                     "note": f"rotated 90 degrees clockwise for vertical text: {taken} box(es) read from "
                             "the rotated copy, mapped back to original pixels"}
                )  # fmt: skip
        texts = texts_from_items(items, orientation, read_from, model)
        rooms, nstats = name_rooms(
            rooms, texts, read_from, label_priority=label_priority, kitchen_fallback=kitchen_fallback
        )
        summary.update(orientation=orientation, orientation_stats=stats, **nstats)
        if orientation == "flipped":
            doc["drawing"]["transforms"].append({"kind": "other", "note": "mirrored"})
        if ocr_raw.get("ocr_upscale", 1) != 1:
            k = float(ocr_raw["ocr_upscale"])
            doc["drawing"]["transforms"].append(
                {"kind": "upscale", "from": "ocr_gray", "to": "ocr_input", "scale_x": k, "scale_y": k,
                 "note": "OCR input only; text boxes mapped back to original pixels"}
            )  # fmt: skip
        mean = round(float(np.mean([t["confidence"] for t in texts])), 4) if texts else 0.0
        doc["coverage"] = _replace_coverage(
            doc["coverage"],
            {"category": "text", "region": "page", "status": "analysed", "extractor": EXTRACTOR,
             "confidence": mean,
             "note": f"PaddleOCR {model}, read {orientation}; {len(texts)} texts, mean score {mean}"},
        )  # fmt: skip
        doc["history"].append(
            {"extractor": EXTRACTOR, "action": "read_text", "cost_usd": ocr_raw.get("cost_usd"),
             "note": f"{len(texts)} texts read {orientation} (hits original {stats['original']['hits']}, "
                     f"flipped {stats['flipped']['hits']}); {nstats['labels']} room labels: "
                     f"{nstats['named_regions']} named regions, {nstats['shared']} shared, "
                     f"{nstats['label_only']} label-only; label_priority={label_priority}"}
        )  # fmt: skip

    lv, floors_cov = levels(texts, building_blobs(seg_raw), read_from, rule=floors_rule)
    doc["levels"] = lv
    doc["coverage"] = _replace_coverage(doc["coverage"], floors_cov)
    summary["levels"] = len(lv)
    summary["floors"] = floors_cov["note"]

    active = [r for r in rooms if r.get("status", "active") == "active"]
    labelled = sum(bool(r.get("text_ids")) for r in active)
    rconf = round(float(np.mean([r["confidence"] for r in active])), 4) if active else 0.0
    src = "CubiCasa5K regions" + (" named by OCR labels" if ocr_raw is not None else " (no OCR yet)")
    doc["coverage"] = _replace_coverage(
        doc["coverage"],
        {"category": "rooms", "region": "page", "status": "analysed",
         "extractor": "cubicasa5k+ocr" if ocr_raw is not None else "cubicasa5k", "confidence": rconf,
         "note": f"{src}; {len(active)} rooms, {labelled} with a label"},
    )  # fmt: skip
    doc["rooms"] = rooms
    doc["texts"] = texts
    return summary
