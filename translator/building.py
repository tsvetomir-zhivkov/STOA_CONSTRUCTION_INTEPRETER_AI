"""Central v0.2 migration, source-scoped identity and explicit legacy page views.

No geometric deduplication across sheets: only an IFC GlobalId establishes identity.
Legacy envelopes are an internal extraction/coordinate adapter, never a persisted output.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import PurePosixPath

COLLECTIONS = (
    "levels",
    "units",
    "rooms",
    "walls",
    "openings",
    "objects",
    "texts",
    "edges",
    "types",
    "systems",
    "schedules",
)
OLD_COLLECTIONS = COLLECTIONS[:8]
PIXELS = ("polygon_px", "polyline_px", "bbox_px", "holes_px")
SINGLE_REFS = {
    "level_id": "levels",
    "unit_id": "units",
    "parent_room_id": "rooms",
    "room_id": "rooms",
    "wall_id": "walls",
    "type_id": "types",
    "parent_id": None,
    "superseded_by": None,
    "from_id": None,
    "to_id": None,
}
LIST_REFS = {
    "room_ids": "rooms",
    "text_ids": "texts",
    "system_ids": "systems",
    "schedule_ids": "schedules",
    "component_ids": None,
    "instance_ids": None,
    "entity_ids": None,
    "level_ids": "levels",
    "boundary_ids": None,
}


def stable_id(prefix, *parts):
    return (
        prefix
        + "-"
        + hashlib.sha256("\n".join(str(p) if p is not None else "" for p in parts).encode()).hexdigest()[:16]
    )


def empty_building(identity):
    return {
        "schema_version": "0.3",
        "building_id": identity,
        "project": {},
        "source_documents": [],
        "drawings": [],
        **{c: [] for c in COLLECTIONS},
        "relationships": [],
        "coverage": [],
        "history": [],
    }


def _source(file, source=None):
    file = file.replace("\\", "/")
    return {
        "id": stable_id("source", file),
        "file": file,
        "format": PurePosixPath(file).suffix.lstrip(".").lower() or "unknown",
        "source": source,
    }


def _remap(value, mapping):
    if isinstance(value, list):
        return [_remap(v, mapping) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k in ("properties", "details", "provenance"):
            out[k] = v
        elif k in SINGLE_REFS:
            out[k] = mapping.get(v, v)
        elif k in LIST_REFS and isinstance(v, list):
            out[k] = [mapping.get(i, i) for i in v]
        else:
            out[k] = _remap(v, mapping)
    return out


def _ifc_guid(entity, drawing):
    guid = entity.get("ifc_global_id") or entity.get("properties", {}).get("global_id")
    if not guid and drawing["route"] == "ifc":
        candidate = entity.get("provenance", {}).get("source_ref", "")
        if re.fullmatch(r"[0-3][0-9A-Za-z_$]{21}", candidate):
            guid = candidate
    return guid


def migrate(doc):
    """Return a detached v0.3 building; reject unknown versions, never mutate the caller."""
    if doc.get("schema_version") == "0.3":
        return copy.deepcopy(doc)
    if doc.get("schema_version") != "0.2":
        raise ValueError(f"Unsupported schema version: {doc.get('schema_version')!r}")
    old = copy.deepcopy(doc)
    drawing = old["drawing"]
    source = _source(drawing["read_from"], drawing.get("source"))
    did = stable_id("drawing", drawing["file"], drawing.get("page_index"), drawing.get("part"))
    out = empty_building(stable_id("building", source["file"]))
    out["source_documents"].append(source)
    if drawing["file"] != source["file"]:
        out["source_documents"].append(_source(drawing["file"], drawing.get("source")))
    mapping = {}
    for c in COLLECTIONS:
        for e in old.get(c, []):
            guid = _ifc_guid(e, drawing)
            mapping[e["id"]] = f"ifc:{guid}" if guid else f"{did}:{e['id']}"
    for c in COLLECTIONS:
        for e in old.get(c, []):
            guid = _ifc_guid(e, drawing)
            e["id"] = mapping[e["id"]]
            e = _remap(e, mapping)
            e["ifc_global_id"] = guid
            e["source_refs"] = [
                {
                    "source_document_id": source["id"],
                    "drawing_id": did,
                    "source_ref": e["provenance"]["source_ref"],
                    "extractor": e["provenance"]["extractor"],
                }
            ]
            e["source_drawing_id"] = did
            e["observations"] = [{"drawing_id": did, **{k: e[k] for k in PIXELS if k in e}}]
            props = e.get("properties") or {}
            e.setdefault("ifc_class", props.get("ifc_class") or props.get("ifc_type"))
            for field in ("description", "predefined_type", "type_name"):
                e.setdefault(field, props.get(field))
            if c == "openings":
                e.setdefault("is_physical", e["kind"] in ("door", "window"))
            # Quantities from old files retain uncertainty when their method was never recorded.
            e.setdefault("quantities", [])
            for field, value in list(e.items()):
                if not isinstance(value, (float, int)) or isinstance(value, bool):
                    continue
                unit = next((u for u in ("m3", "m2", "m") if field.endswith("_" + u)), None)
                if unit and not any(q["name"] == field for q in e["quantities"]):
                    origin = (
                        "source"
                        if "_written_" in field
                        else "calculated"
                        if "_computed_" in field
                        else "unknown"
                    )
                    e["quantities"].append(
                        {
                            "name": field,
                            "value": value,
                            "unit": unit,
                            "origin": origin,
                            "method": "v0.2 migration; original provenance retained",
                            "source_refs": copy.deepcopy(e["source_refs"]),
                        }
                    )
            out[c].append(e)
    out["drawings"] = [
        {
            **drawing,
            "id": did,
            "source_document_id": source["id"],
            "level_ids": [e["id"] for e in out["levels"]],
        }
    ]
    out["coverage"] = [{**c, "drawing_id": did} for c in old["coverage"]]
    out["history"] = _remap(old["history"], mapping)
    return out


def _union(a, b):
    return a + [copy.deepcopy(x) for x in b if x not in a]


def merge_buildings(documents, building_id=None):
    """Combine explicitly associated sources. Conflicting facts require review, not a guess."""
    docs = [migrate(d) for d in documents]
    if not docs:
        raise ValueError("At least one source is required")
    out = docs[0]
    out["building_id"] = building_id or out["building_id"]
    for doc in docs[1:]:
        for key, value in doc["project"].items():
            if key in out["project"] and out["project"][key] not in (None, value):
                raise ValueError(f"Conflicting project fact: {key}")
            out["project"][key] = value
        for c in ("source_documents", "drawings", *COLLECTIONS, "relationships"):
            existing = {e["id"]: e for e in out[c]}
            for item in doc[c]:
                if item["id"] not in existing:
                    out[c].append(item)
                    existing[item["id"]] = item
                    continue
                previous = existing[item["id"]]
                for key, value in item.items():
                    if key in ("source_refs", "observations", "quantities", "level_ids"):
                        previous[key] = _union(previous.get(key, []), value)
                    elif key in (*PIXELS, "source_drawing_id", "provenance", "created_at"):
                        continue  # coordinates for each frame remain in observations
                    elif key not in previous or previous[key] is None:
                        previous[key] = value
                    elif value is not None and previous[key] != value:
                        raise ValueError(f"Conflicting {c}/{item['id']}/{key}; reconcile sources explicitly")
        out["coverage"] = _union(out["coverage"], doc["coverage"])
        out["history"] = _union(out["history"], doc["history"])
    out.pop("statistics", None)  # derived values must be recalculated after merging
    return out


def page_views(document):
    """Explicit source-frame views for legacy evaluation and display; never persist these.

    A v0.2 document, a building.schema.v0.3 document (one drawing, singular `drawing`) and a view are
    already in a single source frame: returned as they are."""
    if (
        document.get("schema_version") in ("0.2", "0.3-view")
        or "schema_version" not in document
        or "drawings" not in document
    ):
        return [document]
    doc = migrate(document)
    views = []
    for drawing in doc["drawings"]:
        view = {"schema_version": "0.3-view", "drawing": drawing, "history": doc["history"]}
        for c in COLLECTIONS:
            view[c] = []
            for e in doc[c]:
                if not any(r["drawing_id"] == drawing["id"] for r in e["source_refs"]):
                    continue
                item = copy.deepcopy(e)
                observation = next(
                    (o for o in item.get("observations", []) if o["drawing_id"] == drawing["id"]), {}
                )
                item.update({k: observation.get(k) for k in PIXELS if k in item})
                view[c].append(item)
        view["coverage"] = [c for c in doc["coverage"] if c["drawing_id"] == drawing["id"]]
        views.append(view)
    return views


def reference_errors(doc):
    """JSON Schema cannot validate foreign keys; enforce them separately at every boundary."""
    groups = {c: {e["id"] for e in doc[c]} for c in COLLECTIONS}
    all_ids = {doc["building_id"]}
    errors = []
    for c in (*COLLECTIONS, "source_documents", "drawings", "relationships"):
        for e in doc[c]:
            if e["id"] in all_ids:
                errors.append(f"Duplicate id {e['id']}")
            all_ids.add(e["id"])
    sources = {s["id"] for s in doc["source_documents"]}
    drawings = {d["id"] for d in doc["drawings"]}
    entities = {doc["building_id"]} | set().union(*groups.values())

    def walk(value, path=""):
        if isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}/{i}")
        elif isinstance(value, dict):
            for k, v in value.items():
                if k in ("properties", "details", "provenance"):
                    continue
                target = None
                if k in SINGLE_REFS:
                    target = groups.get(SINGLE_REFS[k], entities)
                elif k in LIST_REFS:
                    target = groups.get(LIST_REFS[k], entities)
                elif k == "source_document_id":
                    target = sources
                elif k in ("drawing_id", "source_drawing_id", "source_sheet_id"):
                    target = drawings
                if target is not None:
                    for identifier in v if isinstance(v, list) else [v]:
                        if identifier is not None and identifier not in target:
                            errors.append(f"{path}/{k}: unknown reference {identifier}")
                walk(v, f"{path}/{k}")

    walk(doc)
    for e in doc["openings"]:
        if e["kind"] in ("doorway", "void") and e.get("is_physical") is True:
            errors.append(f"{e['id']}: an unfilled opening is not a physical door/window")
    return errors
