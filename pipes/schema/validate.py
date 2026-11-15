"""Validate a piping index (schema piping-0.1): JSON Schema + the reference rules the schema says it does not check.

    python validate.py piping.json

check(pipe, drawings) -> list of error strings (empty = valid).
"""

import argparse
import json
import os
import sys

import jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))
ENTITY_LISTS = (
    "levels", "pipe_systems", "pipe_types", "pipe_segments", "pipe_fittings", "pipe_equipment",
    "pipe_connections", "texts",
)  # fmt: skip
_SCHEMA = None


def schema():
    global _SCHEMA
    if _SCHEMA is None:
        with open(os.path.join(HERE, "pipe.schema.json"), encoding="utf-8") as f:
            _SCHEMA = json.load(f)
    return _SCHEMA


def check(pipe, drawings=None):
    errs = [
        f"schema: {'/'.join(map(str, e.path))}: {e.message}"
        for e in jsonschema.Draft202012Validator(schema()).iter_errors(pipe)
    ]
    if errs:  # the reference rules assume the shape is right
        return errs
    ids = {}
    for key in ENTITY_LISTS:
        for el in pipe[key]:
            if el["id"] in ids:
                errs.append(f"duplicate id {el['id']}")
            ids[el["id"]] = key
    for key in ("site", "building"):
        if pipe[key] and pipe[key]["id"] in ids:
            errs.append(f"duplicate id {pipe[key]['id']}")
    src_ids = {s["id"] for s in pipe["sources"]}
    if pipe["drawing"]["source"] not in src_ids:
        errs.append(f"drawing.source {pipe['drawing']['source']} is not in sources")
    img = pipe["drawing"].get("image")
    W, H = (img["width_px"], img["height_px"]) if img else (None, None)

    def ref(owner, field, target, kinds):
        if target is not None and ids.get(target) not in kinds:
            errs.append(f"{owner}: {field} {target} is not a {'/'.join(kinds)} id")

    node_kinds = ("pipe_fittings", "pipe_equipment")
    for coll in ("pipe_segments", "pipe_fittings", "pipe_equipment", "pipe_systems", "pipe_types", "texts"):
        for el in pipe[coll]:
            ref(el["id"], "superseded_by", el.get("superseded_by"), (coll,))
            ref(el["id"], "level_id", el.get("level_id"), ("levels",))
            for tid in el.get("source_text_ids", []):
                ref(el["id"], "source_text_ids", tid, ("texts",))
            b = el.get("bbox_px") if coll in ("pipe_fittings", "pipe_equipment") else None  # symbols: inside the page
            if b and W is not None and not (0 <= b[0] <= b[2] <= W and 0 <= b[1] <= b[3] <= H):
                errs.append(f"{el['id']}: bbox_px outside the page image")
    conns = {c["id"]: c for c in pipe["pipe_connections"]}
    for c in pipe["pipe_connections"]:
        ref(c["id"], "from_entity_id", c["from_entity_id"], (*node_kinds, "pipe_segments"))
        ref(c["id"], "to_entity_id", c["to_entity_id"], (*node_kinds, "pipe_segments"))
        ref(c["id"], "system_id", c.get("system_id"), ("pipe_systems",))
    for s in pipe["pipe_segments"]:
        ref(s["id"], "system_id", s.get("system_id"), ("pipe_systems",))
        ref(s["id"], "type_id", s.get("type_id"), ("pipe_types",))
        pl = s.get("centerline_px")
        if not pl or len(pl) < 2:
            errs.append(f"{s['id']}: centerline_px needs at least two points")
        for i, key in enumerate(("start_connection_id", "end_connection_id")):
            cid = s.get(key)
            if cid is None:
                if f"end{i}_unsnapped" not in s.get("flags", []):
                    errs.append(f"{s['id']}: {key} is null but the end is not flagged end{i}_unsnapped")
                continue
            c = conns.get(cid)
            if c is None:
                errs.append(f"{s['id']}: {key} {cid} is not a connection")
            elif s["id"] not in (c["from_entity_id"], c["to_entity_id"]):
                errs.append(f"{s['id']}: {key} {cid} does not touch this segment")
        for cid in (s.get("start_connection_id"), s.get("end_connection_id")):
            if cid and cid in conns and conns[cid]["from_entity_id"] == s["id"] == conns[cid]["to_entity_id"]:
                errs.append(f"{cid}: a connection from a segment to itself")
    for el in (*pipe["pipe_fittings"], *pipe["pipe_equipment"]):
        for sid in el.get("connected_segment_ids", []):
            ref(el["id"], "connected_segment_ids", sid, ("pipe_segments",))
        for cid in el.get("connection_ids", []):
            ref(el["id"], "connection_ids", cid, ("pipe_connections",))
    for t in pipe["texts"]:
        for lid in t.get("linked_entity_ids", []):
            if lid not in ids:
                errs.append(f"{t['id']}: linked_entity_ids {lid} does not exist")
    for sy in pipe["pipe_systems"]:
        for key, kinds in (("segment_ids", ("pipe_segments",)), ("fitting_ids", ("pipe_fittings",)),
                           ("equipment_ids", ("pipe_equipment",))):  # fmt: skip
            for x in sy.get(key, []):
                ref(sy["id"], key, x, kinds)
    for ty in pipe["pipe_types"]:
        for x in ty.get("segment_ids", []):
            ref(ty["id"], "segment_ids", x, ("pipe_segments",))
    for st in pipe.get("statistics", []):
        for x in st["source_entity_ids"]:
            if x not in ids:
                errs.append(f"statistic {st['name']}: source_entity_ids {x} does not exist")
    k = (pipe["drawing"].get("scale") or {}).get("m_per_px")
    if k is None:
        for s in pipe["pipe_segments"]:
            if s.get("length_m") is not None:
                errs.append(f"{s['id']}: length_m without a scale (never estimate lengths from pixels)")
    return errs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pipe")
    a = ap.parse_args()
    with open(a.pipe, encoding="utf-8") as f:
        errs = check(json.load(f))
    print("\n".join(errs) if errs else f"OK: {a.pipe}")
    sys.exit(1 if errs else 0)
