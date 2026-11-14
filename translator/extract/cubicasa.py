"""Step 6: CubiCasa5K segmentation -> schema document (local post-processing, CPU only).

The GPU part lives in translator/modal_app.py. It runs the CubiCasa5K model on the normalized image
(translator.raster.preprocess) and calls raw_from_maps() below, so the GPU side and this module share one
component extractor. Its output, one raw file per image in index/_raw/cubicasa/<name>.seg.json, holds every
connected component >= MIN_RAW_PX pixels per class as a polygon in ORIGINAL image pixels, with area and mean
class probability. Everything here works from those raw files, so tuning never reruns the GPU.

Post-processing (to_parts):
  icons  one instance per connected component of an icon class; components smaller than min_icon_px are
         dropped (tunable). Merged components are not split yet. Window/Door -> openings[];
         Toilet/Sink/Bathtub/Sauna bench -> objects[]; Closet/Electrical appliance/Fireplace/Chimney ->
         objects[] as category "other" with the class as subtype.
  rooms  one room per connected component of a room class (not Background/Wall/Railing), polygon from
         cv2.findContours + approxPolyDP, confidence = mean class probability.
  The model has no shower class: coverage marks "shower" not_analysed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np

EXTRACTOR = "cubicasa5k"

# CubiCasa5K hg_furukawa_original output channels: 21 junction heatmaps, 12 room, 11 icon classes.
SPLIT = (21, 12, 11)
ROOM_CLASSES = [
    "Background", "Outdoor", "Wall", "Kitchen", "Living room", "Bedroom",
    "Bath", "Entry", "Railing", "Storage", "Garage", "Undefined",
]  # fmt: skip
ICON_CLASSES = [
    "No icon", "Window", "Door", "Closet", "Electrical appliance", "Toilet",
    "Sink", "Sauna bench", "Fireplace", "Bathtub", "Chimney",
]  # fmt: skip

MIN_RAW_PX = 4  # components kept in the raw file (the GPU side); local filters are on top of this
RAW_EPSILON_PX = 1.0  # approxPolyDP tolerance for raw polygons
MIN_ICON_PX = 16  # default N: icon components smaller than this are dropped (tunable)
MIN_ROOM_PX = 200  # room components smaller than this are dropped (tunable)

# room class -> (schema room type, is_outdoor); Background / Wall / Railing are not rooms
ROOM_TYPES = {
    "Outdoor": ("outdoor", True),
    "Kitchen": ("kitchen", False),
    "Living room": ("living", False),
    "Bedroom": ("bedroom", False),
    "Bath": ("bathroom", False),
    "Entry": ("entry", False),
    "Storage": ("storage", False),
    "Garage": ("garage", False),
    "Undefined": ("unknown", False),
}
OPENING_KINDS = {"Window": "window", "Door": "door"}
OBJECT_CATEGORIES = {"Toilet": "toilet", "Sink": "sink", "Bathtub": "bathtub", "Sauna bench": "sauna_bench"}
OTHER_OBJECTS = {"Closet", "Electrical appliance", "Fireplace", "Chimney"}

RAW_DIR_NAME = "cubicasa"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ------------------------------------------------------------------ shared with the GPU side


def components(
    cls_map: np.ndarray,
    prob_map: np.ndarray,
    names: list[str],
    skip: tuple[int, ...] = (0,),
    min_px: int = MIN_RAW_PX,
    epsilon_px: float = RAW_EPSILON_PX,
) -> list[dict]:
    """Connected components (8-connected) of every class in a class-index map, as polygons.

    prob_map holds the probability of the predicted class per pixel. Component index k is the label order
    of cv2.connectedComponentsWithStats (raster scan), so ids are stable for the same maps.
    """
    out = []
    for c, name in enumerate(names):
        if c in skip:
            continue
        mask = (cls_map == c).astype(np.uint8)
        if not mask.any():
            continue
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        k = 0
        for i in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[i])
            if area < min_px:
                continue
            sub = labels[y : y + h, x : x + w] == i
            contours, _ = cv2.findContours(sub.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            contour = max(contours, key=cv2.contourArea)
            approx = cv2.approxPolyDP(contour, epsilon_px, True).reshape(-1, 2)
            if len(approx) < 3:  # degenerate (a line): use the bounding box
                approx = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
            poly = (approx + [x, y]).tolist()
            probs = prob_map[y : y + h, x : x + w][sub]
            out.append(
                {
                    "class_id": c,
                    "class": name,
                    "k": k,
                    "area_px": area,
                    "bbox": [x, y, x + w, y + h],
                    "polygon": poly,
                    "mean_prob": round(float(probs.mean()), 4),
                }
            )
            k += 1
    return out


def class_summary(cls_map: np.ndarray, prob_map: np.ndarray, names: list[str]) -> dict:
    """Pixels and mean probability per predicted class."""
    out = {}
    for c, name in enumerate(names):
        sel = cls_map == c
        n = int(sel.sum())
        if n:
            out[name] = {"pixels": n, "mean_prob": round(float(prob_map[sel].mean()), 4)}
    return out


def raw_from_maps(room_cls, room_prob, icon_cls, icon_prob, meta: dict | None = None) -> dict:
    """The raw per-image result: components of every room and icon class, in original pixels."""
    h, w = room_cls.shape
    return {
        "width_px": int(w),
        "height_px": int(h),
        "room_classes": ROOM_CLASSES,
        "icon_classes": ICON_CLASSES,
        "rooms": components(room_cls, room_prob, ROOM_CLASSES, skip=(0,)),
        "icons": components(icon_cls, icon_prob, ICON_CLASSES, skip=(0,)),
        "room_summary": class_summary(room_cls, room_prob, ROOM_CLASSES),
        "icon_summary": class_summary(icon_cls, icon_prob, ICON_CLASSES),
        "mean_max_prob": {
            "rooms": round(float(room_prob.mean()), 4),
            "icons": round(float(icon_prob.mean()), 4),
        },
        **(meta or {}),
    }


# ------------------------------------------------------------------ local: raw files


def raw_dir() -> Path:
    from translator.config import INDEX_DIR

    return INDEX_DIR / "_raw" / RAW_DIR_NAME


def raw_path(name: str) -> Path:
    return raw_dir() / f"{name}.seg.json"


def load_raw(name: str) -> dict | None:
    p = raw_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def holdout_files() -> set[str]:
    from translator.config import REPO_ROOT

    lines = (REPO_ROOT / "eval" / "holdout_files.txt").read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def dev_images(root: Path | None = None) -> list[str]:
    """Dataset-relative paths of cubicasa5k images NOT in the hold-out list (listed from the folder only)."""
    from translator.config import data_root

    folder = data_root(root) / "data" / "cubicasa5k"
    hold = holdout_files()
    paths = sorted(
        f"data/cubicasa5k/{p.name}" for p in folder.iterdir() if p.suffix.lower() in (".png", ".jpg")
    )
    return [p for p in paths if p not in hold]


# ------------------------------------------------------------------ local: raw -> schema parts


def _bbox(poly: list[list[float]]) -> list[float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]


def _prov(kind: str, comp: dict, read_from: str) -> dict:
    return {
        "extractor": EXTRACTOR,
        "source_ref": f"{kind}:{slug(comp['class'])}:{comp['k']}",
        "file": read_from,
        "details": {"area_px": comp["area_px"], "class": comp["class"]},
    }


def to_parts(
    raw: dict, read_from: str, min_icon_px: int = MIN_ICON_PX, min_room_px: int = MIN_ROOM_PX
) -> dict:
    """Schema parts {rooms, openings, objects, coverage, history, transforms} from one raw result."""
    rooms, openings, objects = [], [], []
    for c in raw["rooms"]:
        if c["class"] not in ROOM_TYPES or c["area_px"] < min_room_px:
            continue
        rtype, outdoor = ROOM_TYPES[c["class"]]
        rooms.append(
            {
                "id": f"room-{slug(c['class'])}-{c['k']}",
                "name": None,
                "number": None,
                "label": None,
                "type": rtype,
                "subtype": c["class"],
                "is_outdoor": outdoor,
                "polygon_px": c["polygon"],
                "polygon_m": None,
                "area_m2": None,
                "confidence": c["mean_prob"],
                "provenance": _prov("room", c, read_from),
                "status": "active",
                "flags": [],
            }
        )
    for c in raw["icons"]:
        if c["area_px"] < min_icon_px:
            continue
        base = {
            "bbox_px": _bbox(c["polygon"]),
            "polygon_px": c["polygon"],
            "polygon_m": None,
            "confidence": c["mean_prob"],
            "provenance": _prov("icon", c, read_from),
            "status": "active",
        }
        cid = f"{slug(c['class'])}-{c['k']}"
        if c["class"] in OPENING_KINDS:
            openings.append({"id": f"opening-{cid}", "kind": OPENING_KINDS[c["class"]], **base})
        elif c["class"] in OBJECT_CATEGORIES:
            objects.append({"id": f"object-{cid}", "category": OBJECT_CATEGORIES[c["class"]], **base})
        elif c["class"] in OTHER_OBJECTS:
            objects.append({"id": f"object-{cid}", "category": "other", "subtype": slug(c["class"]), **base})

    def mean_conf(items):
        return round(float(np.mean([e["confidence"] for e in items])), 4) if items else 0.0

    by_cat = {
        "rooms": rooms,
        "doors": [o for o in openings if o["kind"] == "door"],
        "windows": [o for o in openings if o["kind"] == "window"],
        "fixtures": [o for o in objects if o["category"] != "other"],
    }
    mmp = raw["mean_max_prob"]
    note = f"CubiCasa5K segmentation; mean max-prob rooms {mmp['rooms']}, icons {mmp['icons']}"
    coverage = [
        {"category": cat, "region": "page", "status": "analysed", "extractor": EXTRACTOR,
         "confidence": mean_conf(items) or mmp["rooms" if cat == "rooms" else "icons"], "note": note}
        for cat, items in by_cat.items()
    ]  # fmt: skip
    coverage += [
        {"category": "shower", "region": "page", "status": "not_analysed", "extractor": EXTRACTOR,
         "confidence": 0.0, "note": "the CubiCasa5K model has no shower class"},
        {"category": "walls", "region": "page", "status": "not_analysed", "extractor": EXTRACTOR,
         "confidence": 0.0, "note": "wall pixels are in the raw file; not turned into walls[] yet"},
        {"category": "text", "region": "page", "status": "not_analysed", "extractor": EXTRACTOR,
         "confidence": 0.0, "note": "OCR is step 7"},
    ]  # fmt: skip

    transforms = []
    t = raw.get("transforms", {})
    if t.get("scale_x", 1.0) != 1.0 or t.get("scale_y", 1.0) != 1.0:
        transforms.append({"kind": "resize", "from": "original", "to": "model_input",
                           "scale_x": t["scale_x"], "scale_y": t["scale_y"],
                           "note": "model input only; outputs mapped back to original pixels"})  # fmt: skip
    if t.get("inverted"):
        transforms.append({"kind": "invert", "from": "original", "to": "model_input",
                           "note": "dark background inverted before the model"})  # fmt: skip
    history = [
        {
            "extractor": EXTRACTOR,
            "action": "segment",
            "note": f"min_icon_px={min_icon_px}, min_room_px={min_room_px}: {len(rooms)} rooms, "
            f"{len(openings)} openings, {len(objects)} objects; model {raw.get('model', {})}",
            "cost_usd": raw.get("cost_usd"),
        }
    ]
    return {"rooms": rooms, "openings": openings, "objects": objects, "coverage": coverage,
            "history": history, "transforms": transforms}  # fmt: skip


def write_documents(
    names: list[str] | None = None,
    min_icon_px: int = MIN_ICON_PX,
    min_room_px: int = MIN_ROOM_PX,
    ocr_params: dict | None = None,
    index_dir: Path | None = None,
    fuse_params: dict | None = None,
) -> list[Path]:
    """Translate and write index/cubicasa5k/<name>.json for every image that has a raw file (dev only).

    ocr_params go to translator.extract.ocr.fuse (merge, label_priority), fuse_params to
    translator.fuse.fuse_raster (priors, grid); index_dir defaults to index/.
    """
    from translator import translate_all
    from translator.config import INDEX_DIR
    from translator.store import write_drawing

    hold = holdout_files()
    if names is None:
        names = sorted(p.name[: -len(".seg.json")] for p in raw_dir().glob("*.seg.json"))
    out = []
    for name in names:
        path = f"data/cubicasa5k/{name}.png"
        if path in hold:  # never touch a held-out image, even if a raw file exists
            continue
        params = {"min_icon_px": min_icon_px, "min_room_px": min_room_px}
        for doc in translate_all(
            path, cubicasa_params=params, ocr_params=ocr_params, fuse_params=fuse_params
        ):
            out.append(write_drawing(doc, index_dir or INDEX_DIR))
    return out


if __name__ == "__main__":
    # python -m translator.extract.cubicasa [--min-icon-px N] [--merge] [--no-label-priority] [--no-priors]
    #                                       [--index DIR]
    import argparse

    ap = argparse.ArgumentParser(
        description="Write CubiCasa documents from the raw segmentation and OCR files (dev only)."
    )
    ap.add_argument("--min-icon-px", type=int, default=MIN_ICON_PX)
    ap.add_argument("--min-room-px", type=int, default=MIN_ROOM_PX)
    ap.add_argument("--merge", action="store_true", help="merge same-class room regions (default off)")
    ap.add_argument(
        "--no-label-priority", action="store_true", help="count segmentation regions even next to labels"
    )
    ap.add_argument("--no-priors", action="store_true", help="no kitchen/bathroom count priors (default on)")
    ap.add_argument("--index", help="index directory to write to (default: index/)")
    args = ap.parse_args()
    written = write_documents(
        min_icon_px=args.min_icon_px,
        min_room_px=args.min_room_px,
        ocr_params={"merge": args.merge, "label_priority": not args.no_label_priority},
        index_dir=Path(args.index) if args.index else None,
        fuse_params={"priors": not args.no_priors},
    )
    print(f"wrote {len(written)} documents")
