"""Write a VLM finding back into a schema v0.2 document (in place). Nothing is ever deleted.

For one (category, count n, boxes) answer:
  - pipeline elements of that kind whose shape holds a VLM box's centre are CONFIRMED (they stay, flag
    vlm_confirmed); a room the count prior stepped back (prior_stepped_back) is restored to its type;
  - VLM boxes that match nothing become NEW elements: provenance.extractor "vlm_check", source_ref
    "vlm:<category>", the box, confidence config.VLM_CONFIDENCE, flag vlm_check;
  - the VLM count wins: unmatched pipeline elements are kept only while the total stays <= n; extra ones are
    stepped back (rooms: type 'unknown', flag vlm_stepped_back) or superseded (objects, openings; flag
    vlm_rejected, superseded_by null). If the VLM gives fewer boxes than its count, box-less elements make up
    the difference (flag vlm_no_box);
  - assumed prior rooms of that type are always superseded (superseded_by the first VLM-backed element);
  - the matching coverage record becomes 'analysed' (shower: 'partial' if the confidence is low), and a
    history record carries cost_usd.
Floors: n == current storeys -> confirmed; otherwise n new level entities supersede the old ones.
"""

from __future__ import annotations

from translator.vlm import config

EXTRACTOR = "vlm_check"
# VLM boxes are approximate (normalised 0-1000 boxes from a general model, not traced outlines), and the
# confidence config.VLM_CONFIDENCE is a fixed value, not calibrated.
BOX_PROPS = {"box_quality": "approximate", "confidence_kind": "fixed, not calibrated"}
ROOM_CATS = {"kitchen": ("kitchen", "kitchens"), "bathroom": ("bathroom", "bathrooms")}
OBJECT_CATS = {"shower": ("shower", "bathtub")}
OPENING_CATS = {"doors": "door", "windows": "window"}


def _active(items):
    return [x for x in items if x.get("status", "active") == "active"]


def _bbox(el) -> list[float] | None:
    if el.get("bbox_px"):
        return el["bbox_px"]
    poly = el.get("polygon_px")
    if not poly:
        return None
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def _holds(el, box) -> bool:
    """Does the element's shape hold the centre of the box?"""
    from shapely.geometry import Point, Polygon
    from shapely.geometry import box as sbox

    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    poly = el.get("polygon_px")
    if poly and len(poly) >= 3:
        g = Polygon([p[:2] for p in poly])
        g = g if g.is_valid else g.buffer(0)
    else:
        b = _bbox(el)
        if not b:
            return False
        g = sbox(*b)
    return g.buffer(2).contains(Point(cx, cy))


def _rect(b):
    return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]


def _prov(category, outcome, extra=None):
    details = {"model": outcome.get("model"), "cache_key": outcome.get("key"), "gate": outcome.get("gate"),
               "vlm_count": outcome["count"], **(extra or {})}  # fmt: skip
    return {"extractor": EXTRACTOR, "source_ref": f"vlm:{category}", "details": details}


def _flag(el, f):
    el["flags"] = sorted(set(el.get("flags", [])) | {f})


def _set_cov(doc, category, status, conf, note):
    rec = {"category": category, "region": "page", "status": status, "extractor": EXTRACTOR,
           "confidence": conf, "note": note}  # fmt: skip
    doc["coverage"] = [
        c for c in doc["coverage"] if not (c["category"] == category and c["region"] == "page")
    ]
    doc["coverage"].append(rec)


def _reconcile(pipeline, boxes, n, match_only=()):
    """-> (confirmed [(el, box)], new boxes, kept unmatched, rejected unmatched, n box-less).

    match_only elements (rooms the prior stepped back) are confirmed when a box lands on them, but never
    fill the count otherwise.
    """
    confirmed, used, new = [], set(), []
    pool = list(pipeline) + list(match_only)
    for b in boxes:
        hit = next((e for e in pool if id(e) not in used and _holds(e, b)), None)
        if hit is not None:
            used.add(id(hit))
            confirmed.append((hit, b))
        else:
            new.append(b)
    unmatched = [e for e in pipeline if id(e) not in used]  # match_only elements never fill the count
    room = max(0, n - len(confirmed))
    new = new[:room]
    room -= len(new)
    kept, rejected = unmatched[:room], unmatched[room:]
    room -= len(kept)
    return confirmed, new, kept, rejected, room


def apply(doc: dict, outcome: dict, enabled: dict | None = None) -> dict:
    """Apply one VLM outcome {category, count, boxes_px, cost_usd, key, model, gate}. Returns a summary."""
    cat = outcome["category"]
    if not (enabled if enabled is not None else config.WRITEBACK_ENABLED).get(cat, False):
        return {"category": cat, "applied": False, "reason": "write-back disabled for this category"}
    n, boxes, conf = int(outcome["count"]), list(outcome.get("boxes_px") or []), config.VLM_CONFIDENCE
    ids_new, ids_sup = [], []
    if cat in ROOM_CATS:
        rtype, cov_cat = ROOM_CATS[cat]
        rooms = doc["rooms"]
        assumed = [r for r in _active(rooms) if r["type"] == rtype and "assumed_prior" in r.get("flags", [])]
        pipeline = [r for r in _active(rooms) if r["type"] == rtype and r not in assumed]
        stepped = [r for r in _active(rooms) if "prior_stepped_back" in r.get("flags", [])
                   and r["provenance"].get("details", {}).get("prior_type") == rtype]  # fmt: skip
        confirmed, new, kept, rejected, boxless = _reconcile(pipeline, boxes, n, match_only=stepped)
        stepped_ids = {id(r) for r in stepped}
        for el, _b in confirmed:
            if id(el) in stepped_ids:  # a VLM box landed on a room the prior had stepped back: restore it
                el["type"] = rtype
            _flag(el, "vlm_confirmed")
        for r in rejected:
            r["provenance"].setdefault("details", {})["vlm_type"] = r["type"]
            r["type"] = "unknown"
            _flag(r, "vlm_stepped_back")
        made = []
        for k, b in enumerate(new + [None] * boxless):
            el = {"id": f"room-vlm-{cat}-{k}", "name": None, "number": None, "label": None, "type": rtype,
                  "subtype": None, "is_outdoor": False, "polygon_px": _rect(b) if b else None,
                  "polygon_m": None,
                  "area_m2": None, "confidence": conf, "provenance": _prov(cat, outcome, {"box_px": b}),
                  "status": "active", "flags": ["vlm_check"] + ([] if b else ["vlm_no_box"]),
                  "properties": dict(BOX_PROPS)}  # fmt: skip
            made.append(el)
        rooms.extend(made)
        backing = (made + [e for e, _ in confirmed])[:1]
        for r in assumed:
            r["status"], r["superseded_by"] = "superseded", backing[0]["id"] if backing else None
            ids_sup.append(r["id"])
        ids_new = [e["id"] for e in made]
        prior_note = f"; superseded {len(assumed)} assumed prior element(s)" if assumed else ""
        _set_cov(doc, cov_cat, "analysed", conf, f"vlm_check: count {n} ({len(confirmed)} confirmed, "
                 f"{len(made)} new{prior_note}); {outcome.get('model')}")  # fmt: skip
    elif cat in OBJECT_CATS or cat in OPENING_CATS:
        coll = "objects" if cat in OBJECT_CATS else "openings"
        if cat in OBJECT_CATS:
            kinds = OBJECT_CATS[cat]
            pipeline = [o for o in _active(doc["objects"]) if o["category"] in kinds]
        else:
            kind = OPENING_CATS[cat]
            pipeline = [o for o in _active(doc["openings"]) if o["kind"] == kind]
        confirmed, new, kept, rejected, boxless = _reconcile(pipeline, boxes, n)
        for el, _b in confirmed:
            _flag(el, "vlm_confirmed")
        for el in rejected:
            el["status"], el["superseded_by"] = "superseded", None
            _flag(el, "vlm_rejected")
            ids_sup.append(el["id"])
        made = []
        for k, b in enumerate(new + [None] * boxless):
            el = {"id": f"{coll[:-1]}-vlm-{cat}-{k}", "bbox_px": b, "polygon_px": _rect(b) if b else None,
                  "polygon_m": None, "confidence": conf, "provenance": _prov(cat, outcome),
                  "status": "active", "flags": ["vlm_check"] + ([] if b else ["vlm_no_box"]),
                  "properties": dict(BOX_PROPS)}  # fmt: skip
            if cat in OBJECT_CATS:  # the VLM counts showers and bathtubs together and does not say which
                el.update(category="shower", subtype="shower_or_bathtub")
            else:
                el["kind"] = OPENING_CATS[cat]
            made.append(el)
        doc[coll].extend(made)
        ids_new = [e["id"] for e in made]
        status = "analysed" if cat not in OBJECT_CATS or conf >= 0.5 else "partial"
        note = f"vlm_check: count {n} ({len(confirmed)} confirmed, {len(made)} new, {len(rejected)} rejected)"
        _set_cov(doc, cat, status, conf, f"{note}; {outcome.get('model')}")
    elif cat == "floors":
        old = _active(doc["levels"])
        if n == len(old):
            for lv in old:
                _flag(lv, "vlm_confirmed")
        else:
            made = []
            for k in range(n):
                b = boxes[k] if k < len(boxes) else None
                made.append({"id": f"level-vlm-{k}", "name": f"floor {k + 1}", "elevation_m": None,
                             "height_m": None, "bbox_px": b, "confidence": conf,
                             "provenance": _prov(cat, outcome), "status": "active",
                             "flags": ["vlm_check"] + ([] if b else ["vlm_no_box"]),
                             "properties": dict(BOX_PROPS)})  # fmt: skip
            for lv in old:
                lv["status"], lv["superseded_by"] = "superseded", made[0]["id"] if made else None
                ids_sup.append(lv["id"])
            doc["levels"].extend(made)
            ids_new = [e["id"] for e in made]
        _set_cov(doc, "floors", "analysed", conf, f"vlm_check: {n} storey(s), pipeline had {len(old)}; "
                 f"{outcome.get('model')}")  # fmt: skip
    else:
        raise ValueError(f"no write-back for category {cat!r}")
    doc["history"].append(
        {"extractor": EXTRACTOR, "action": f"check_{cat}", "entity_ids": ids_new + ids_sup,
         "cost_usd": outcome.get("cost_usd"),
         "note": f"{outcome.get('gate')} gate: VLM count {n}, {len(boxes)} box(es); {len(ids_new)} new, "
                 f"{len(ids_sup)} superseded; cache {str(outcome.get('key'))[:12]}"}
    )  # fmt: skip
    return {"category": cat, "applied": True, "count": n, "new": ids_new, "superseded": ids_sup}


def _has_bathroom(doc: dict) -> bool:
    """A bathroom read from the drawing (label or segmentation), not an assumed one."""
    for r in _active(doc["rooms"]):
        if r["provenance"]["extractor"] == "prior":
            continue
        det = r["provenance"].get("details", {})
        if (
            r["type"] == "bathroom"
            or r.get("subtype") == "Bath"
            or "bathroom"
            in (
                det.get("seg_type"),
                det.get("prior_type"),
            )
        ):
            return True
    return False


def shower_prior(doc: dict, outcome: dict | None) -> dict:
    """Shower fallback prior (k, from the stage-1 failure list; no API cost).

    Fires when no shower/bathtub is active and either the VLM said 0 on a page that has a bathroom, or the
    page has no wet room and no VLM answer (gate 'shower' not passed, so the VLM never saw it; a page
    answered by a later run, e.g. step e, is judged by that answer instead). Adds 1 assumed object:
    category shower, subtype shower_or_bathtub, flags ["assumed_prior"], confidence 0.2, no geometry,
    provenance prior / prior:shower; coverage 'shower' partial "prior: assumed 1, not read from the drawing".
    """
    from translator.vlm import gate as _gate

    active = [o for o in _active(doc["objects"]) if o["category"] in ("shower", "bathtub")]
    not_gated = not _gate.gates(doc)["shower"] and outcome is None  # the VLM never saw this page
    vlm_zero = outcome is not None and int(outcome["count"]) == 0 and _has_bathroom(doc)
    if active or not (not_gated or vlm_zero):
        return {"fired": False}
    why = (
        "page has no wet room, never sent to the VLM" if not_gated else "VLM said 0 on a page with a bathroom"
    )
    prov = {"extractor": "prior", "source_ref": "prior:shower",
            "details": {"rule": why, "note": "dev majority answer for showers+baths is 1"}}  # fmt: skip
    el = {"id": "object-prior-shower-0", "category": "shower", "subtype": "shower_or_bathtub",
          "bbox_px": None, "polygon_px": None, "polygon_m": None, "confidence": 0.2, "status": "active",
          "flags": ["assumed_prior"], "provenance": prov}  # fmt: skip
    doc["objects"].append(el)
    note = f"prior: assumed 1, not read from the drawing ({why})"
    _set_cov(doc, "shower", "partial", 0.2, note)
    doc["coverage"][-1]["extractor"] = "prior"
    doc["history"].append(
        {"extractor": "prior", "action": "prior_shower", "entity_ids": [el["id"]], "note": note}
    )
    return {"fired": True, "why": why}
