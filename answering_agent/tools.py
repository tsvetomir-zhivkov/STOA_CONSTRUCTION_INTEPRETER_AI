"""Deterministic drawing tools for Stoa schema v0.2.

Install: uv add shapely
Every function accepts the already-validated v0.2 drawing dict as its first argument.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from shapely.geometry import Polygon

COLLECTIONS = ("levels", "units", "rooms", "walls", "openings", "objects", "texts", "edges")
MEASURES = ("area_m2", "area_written_m2", "area_computed_m2", "height_m", "volume_m3", "length_m", "width_m", "thickness_m", "net_side_area_m2", "net_volume_m3")


def _active(item: dict) -> bool:
    return item.get("status", "active") == "active"


def _brief(item: dict, collection: str) -> dict:
    fields = ("id", "name", "label", "number", "type", "kind", "category", "room_id", "room_ids", "unit_id", "level_id", "area_m2", "width_m", "confidence", "flags")
    out = {k: item[k] for k in fields if k in item}
    out["collection"] = collection
    out["provenance"] = item.get("provenance")
    return out


def _coverage(data: dict, category: str) -> dict:
    """Page-wide coverage only; partial regional coverage is not proof of absence."""
    matches = [c for c in data.get("coverage", []) if c.get("region") == "page" and c.get("category") == category]
    return {"records": matches, "absence_verified": bool(matches) and any(c.get("status") == "analysed" and c.get("confidence", 0) >= 0.8 for c in matches) and not any(c.get("status") != "analysed" for c in matches)}


def query_entities(data: dict, collection: str, field: str = "", value: str = "", limit: int = 50) -> dict:
    """Search active entities in one v0.2 collection. Filter by an exact string field, or search all if field is empty."""
    if collection not in COLLECTIONS:
        return {"error": f"Unknown collection: {collection}", "allowed": COLLECTIONS}
    if field and field not in ("id", "name", "label", "number", "type", "kind", "category", "unit_id", "level_id", "room_id", "text", "role", "is_entrance"):
        return {"error": "Unsupported filter field"}
    items = [x for x in data.get(collection, []) if _active(x)]
    if field:
        items = [x for x in items if str(x.get(field, "")).casefold() == str(value).casefold()]
    limit = max(1, min(int(limit), 100))
    category = {"rooms": "rooms", "walls": "walls", "openings": "doors" if field == "kind" and value == "door" else "windows" if field == "kind" and value == "window" else "openings", "objects": "fixtures", "texts": "text"}.get(collection, collection)
    return {"matches": [_brief(x, collection) for x in items[:limit]], "match_count": len(items), "truncated": len(items) > limit, "coverage": _coverage(data, category), "note": "A zero match count is not proof that the feature is absent unless relevant coverage establishes it."}


def get_entity(data: dict, entity_id: str) -> dict:
    """Return one entity including measurements, geometry and provenance."""
    for collection in COLLECTIONS:
        for item in data.get(collection, []):
            if item.get("id") == entity_id:
                return {"collection": collection, "entity": item}
    return {"error": f"Entity {entity_id!r} not indexed", "absence_verified": False}


def aggregate(data: dict, collection: str, operation: str, measure: str = "area_m2", field: str = "", value: str = "") -> dict:
    """Count, sum, min, max or average an optional numeric field over matching active entities."""
    if collection not in COLLECTIONS or operation not in ("count", "sum", "min", "max", "average"):
        return {"error": "Unsupported collection or operation"}
    if operation != "count" and measure not in MEASURES:
        return {"error": "Unsupported measure"}
    if field and field not in ("type", "kind", "category", "unit_id", "level_id", "room_id"):
        return {"error": "Unsupported filter"}
    items = [x for x in data.get(collection, []) if _active(x) and (not field or str(x.get(field, "")).casefold() == str(value).casefold())]
    if operation == "count":
        return {"result": len(items), "entity_ids": [x["id"] for x in items], "coverage": _coverage(data, collection)}
    known = [(x["id"], x.get(measure)) for x in items if isinstance(x.get(measure), (int, float)) and not isinstance(x.get(measure), bool) and isfinite(x[measure])]
    unknown = [x["id"] for x in items if x["id"] not in {i for i, _ in known}]
    if not known:
        return {"result": None, "error": "No numeric values available", "missing_entity_ids": unknown}
    values = [v for _, v in known]
    result = {"sum": sum(values), "min": min(values), "max": max(values), "average": sum(values) / len(values)}[operation]
    return {"result": result, "measure": measure, "operation": operation, "entity_ids": [i for i, _ in known], "missing_entity_ids": unknown, "complete_for_indexed_matches": not unknown, "note": "Missing measurements are excluded, not treated as zero."}


def _polygon(item: dict):
    coords = item.get("polygon_m")
    units = "m"
    if not coords:
        coords = item.get("polygon_px")
        units = "px"
    if not coords or len(coords) < 3:
        return None, None
    try:
        holes = item.get("holes_m" if units == "m" else "holes_px") or []
        poly = Polygon([(p[0], p[1]) for p in coords], holes=[[(p[0], p[1]) for p in h] for h in holes if len(h) >= 3])
        if poly.is_empty or not poly.is_valid:
            return None, None
        return poly, units
    except (TypeError, ValueError, IndexError):
        return None, None


def geometry_operation(data: dict, first_id: str, second_id: str, operation: str) -> dict:
    """Compute distance, touches, intersects, or contains between two polygon entities."""
    if operation not in ("distance", "touches", "intersects", "contains"):
        return {"error": "Allowed operations: distance, touches, intersects, contains"}
    a = get_entity(data, first_id).get("entity")
    b = get_entity(data, second_id).get("entity")
    if not a or not b or not _active(a) or not _active(b):
        return {"error": "Both entities must exist and be active"}
    pa, ua = _polygon(a)
    pb, ub = _polygon(b)
    if pa is None or pb is None:
        return {"error": "Both entities need valid polygons"}
    if ua != ub:
        return {"error": "Mixed coordinate units; cannot compare"}
    if operation == "distance":
        result = pa.distance(pb)
    else:
        result = getattr(pa, operation)(pb)
    return {"operation": operation, "result": result, "unit": ua if operation == "distance" else None, "entity_ids": [first_id, second_id], "note": "Polygon distance is boundary-to-boundary; touching polygons do not necessarily have a doorway."}


def connected_rooms(data: dict, room_id: str, kind: str = "door") -> dict:
    """Return room IDs linked through an opening's explicit room_ids. No guessed connections."""
    if kind not in ("door", "doorway", "window"):
        return {"error": "kind must be door, doorway or window"}
    openings = [o for o in data.get("openings", []) if _active(o) and o.get("kind") == kind and room_id in (o.get("room_ids") or [])]
    return {"room_id": room_id, "kind": kind, "connections": [{"opening_id": o["id"], "room_ids": [r for r in o.get("room_ids", []) if r != room_id]} for o in openings], "note": "Only explicitly indexed opening-room relationships are returned; absence is not proof of no connection.", "coverage": _coverage(data, "doors" if kind == "door" else "windows" if kind == "window" else "openings")}


def query_rooms(data: dict, room_type: str, operation: str) -> dict:
    """Compatibility wrapper for the original agent's query_rooms tool."""
    if operation == "count":
        return aggregate(data, "rooms", "count", field="type", value=room_type)
    if operation == "sum_area":
        return aggregate(data, "rooms", "sum", measure="area_m2", field="type", value=room_type)
    return {"error": "operation must be count or sum_area"}

# Coverage is evidence about extraction, not evidence that a feature exists.
# Confidence on a `not_analysed` record must never certify absence.
COVERAGE_THRESHOLD = 0.8
CATEGORY_ALIASES = {
    "room": "rooms", "rooms": "rooms", "bedroom": "rooms", "living": "rooms",
    "kitchen": "rooms", "staircase": "stairs", "stairs": "stairs",
    "door": "doors", "doors": "doors", "doorway": "openings",
    "window": "windows", "windows": "windows", "opening": "openings",
    "wall": "walls", "walls": "walls", "object": "fixtures",
    "objects": "fixtures", "fixture": "fixtures", "fixtures": "fixtures",
    "fireplace": "fireplace", "symbol": "symbols", "symbols": "symbols",
    "text": "text", "texts": "text",
}


def _bbox_contains(outer: list, inner: list) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _bbox_intersects(a: list, b: list) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def check_coverage(data: dict, category: str, region: list[float] | None = None) -> dict:
    """Conservatively evaluate extraction coverage for a category and pixel bbox.

    region=None means the entire page. A subregion is certified only when one
    reliable analysed record fully covers it, and no overlapping contradictory
    partial/not_analysed record exists. This is deliberately conservative.
    """
    category = CATEGORY_ALIASES.get(category.strip().casefold(), category.strip().casefold())
    if region is not None:
        if (len(region) != 4 or any(not isinstance(v, (int, float)) or
                isinstance(v, bool) or not isfinite(v) for v in region) or
                region[0] >= region[2] or region[1] >= region[3]):
            return {"error": "region must be [left, top, right, bottom] in pixels"}
    matches = []
    for i, rec in enumerate(data.get("coverage", [])):
        if rec.get("category", "").casefold() != category:
            continue
        area = rec.get("region")
        if area == "page" or region is None or (
            isinstance(area, list) and len(area) == 4 and _bbox_intersects(area, region)
        ):
            matches.append((i, rec))
    def covers(rec: dict) -> bool:
        area = rec.get("region")
        return area == "page" or (region is not None and isinstance(area, list)
                                   and len(area) == 4 and _bbox_contains(area, region))
    reliable = [(i, rec) for i, rec in matches if covers(rec)
                and rec.get("status") == "analysed"
                and rec.get("confidence", 0) >= COVERAGE_THRESHOLD]
    conflicting = [(i, rec) for i, rec in matches if rec.get("status") != "analysed"
                   or rec.get("confidence", 0) < COVERAGE_THRESHOLD]
    verified = bool(reliable) and not conflicting
    return {
        "category": category, "region": region if region is not None else "page",
        "status": "analysed" if verified else "gap",
        "absence_verified": verified,
        "coverage_references": [f"coverage[{i}]" for i, _ in matches],
        "coverage_records": [dict(index=i, **rec) for i, rec in matches],
        "reason": ("Relevant area and category reliably analysed" if verified else
                   "No complete, reliable, non-conflicting coverage for this category and region"),
    }


def detect_gap(data: dict, category: str, entity_ids: list[str] | None = None,
               region: list[float] | None = None) -> dict:
    """Check whether a proposed answer has evidence, or needs more extraction.

    Entity IDs are validated against active indexed entities. When no entities
    support the answer, reliable coverage can certify absence; otherwise gap.
    This is an aid to answer verification, not a claim that a model's text is true.
    """
    coverage = check_coverage(data, category, region)
    if "error" in coverage:
        return coverage
    valid = []
    invalid = []
    for entity_id in dict.fromkeys(entity_ids or []):
        found = get_entity(data, entity_id).get("entity")
        (valid if found and _active(found) else invalid).append(entity_id)
    if valid:
        outcome = "supported_entities"
    elif coverage["absence_verified"]:
        outcome = "verified_absence"
    else:
        outcome = "gap"
    return {
        "outcome": outcome, "needs_fallback": outcome == "gap",
        "valid_entity_ids": valid, "invalid_entity_ids": invalid,
        "coverage": coverage,
        "note": ("Entity citations establish that indexed entities exist, not that a claim "
                 "about them is correct. Validate measurements and relations using tools."),
    }
