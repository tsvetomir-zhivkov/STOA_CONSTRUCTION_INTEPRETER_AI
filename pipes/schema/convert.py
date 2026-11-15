"""Boundary between the pipeline's working graph ("graph doc": nodes / edges / texts, what the tracer, the DXF reader
and the tags build) and the piping schema v0.1 (pipe_segments / pipe_fittings / pipe_equipment / pipe_connections).

  to_piping(graph_doc)  -> piping doc (before derive.py / validate.py; write.finalize() runs all three)
  view(doc)             -> graph doc again, from a piping doc (identity on a graph doc): what the query tools, the demo
                           overlay and the evaluation read. graph(doc) is the same function.

Mapping (everything the schema has no field for travels in `properties.graph` of the entity, or in `history`):
  symbol / tee / junction / open_end node   -> pipe_fittings (kind valve / tee / elbow / ... / other / unknown)
  pump, tank, boiler, ... and equipment nodes -> pipe_equipment
  pipe edge                                 -> pipe_segments (centerline_px, tag, nominal_diameter, service, length_m)
  node <-> edge end                         -> pipe_connections (from_entity_id = node, to_entity_id = segment);
                                               an end that reaches nothing has no connection and the flag end0/end1_unsnapped
  text                                      -> texts (role pipe_label / diameter / length / ...)
  coverage                                  -> coverage (entity lists become flags cov:<category> on the entities)
  sheet / frame / scale                     -> drawing, sources, project.units, drawing.transforms
  extractor registry, sheet facts           -> history entries (action "extractor" / "sheet_facts")
"""

from __future__ import annotations

import copy
import json
import math
import re

SCHEMA_VERSION = "piping-0.1"
GRAPH_VERSION = "0.1"

SERVICE = {
    "cold_water": "domestic_cold_water",
    "hot_water": "domestic_hot_water",
    "hot_water_circulation": "hot_water_return",
    "heating_supply": "heating_supply",
    "heating_return": "heating_return",
    "cooling_supply": "cooling_supply",
    "cooling_return": "cooling_return",
    "waste": "sanitary_waste",
    "soil": "sanitary_waste",
    "vent": "vent",
    "rainwater": "stormwater",
    "gas": "gas",
    "sprinkler": "fire_sprinkler",
    "fire_main": "fire_sprinkler",
    "compressed_air": "compressed_air",
    "ventilation_duct": "other",
    "other": "other",
    "unknown": "unknown",
    None: "unknown",
}
SERVICE_BACK = {}
for _k, _v in SERVICE.items():
    SERVICE_BACK.setdefault(_v, _k)

EQUIPMENT = {
    "pump": "pump", "tank": "tank", "boiler": "boiler", "heat_exchanger": "heat_exchanger", "meter": "meter",
    "filter": "filter", "strainer": "filter", "expansion_vessel": "tank", "radiator": "terminal",
    "fixture": "fixture", "floor_drain": "fixture", "equipment_other": "other",
}  # fmt: skip
FITTING = {
    "tee": "tee", "elbow": "elbow", "cross": "cross", "reducer": "reducer", "cap": "cap", "flange": "flange",
    "union": "union", "cleanout": "cleanout", "backflow_preventer": "valve", "junction": "other",
    "open_end": "other", "off_sheet_connector": "other", "pipe_down": "other", "pipe_up": "other",
    "riser": "other", "gauge": "other", "sensor": "other", "instrument_other": "other", "unknown": "unknown",
}  # fmt: skip
VALVES = (
    "gate_valve ball_valve globe_valve butterfly_valve check_valve control_valve balancing_valve "
    "pressure_reducing_valve safety_valve shutoff_valve valve_other"
).split()
FITTING.update({v: "valve" for v in VALVES})

TEXT_ROLE = {
    "line_tag": "pipe_label", "equipment_tag": "other", "diameter": "diameter", "length": "length",
    "system_label": "system", "flow_note": "other", "elevation": "elevation", "legend_entry": "legend",
    "title": "title", "scale_note": "other", "sheet_ref": "other", "level": "other", "note": "other",
    "unknown": "other",
}  # fmt: skip
ROUTE = {"vector_parser": "dxf", "raster_pipeline": "raster", "vision_fallback": "vlm"}
ROUTE_BACK = {v: k for k, v in ROUTE.items()}
FORMAT = {"dxf": "dxf", "png": "raster", "jpg": "raster", "pdf": "pdf", "svg": "svg", "ifc": "ifc"}
SCALE_SOURCE = {"vector_units": "vector_units", "scale_note": "scale_note", "dimension_line": "dimension_lines"}
TO_SI = {"mm": 0.001, "m": 1.0, "pt": 0.0003527778, "inch": 0.0254, "px": None, "unitless": None}
MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4, "dn": 1.0}


def is_piping(doc: dict) -> bool:
    return doc.get("schema_version") == SCHEMA_VERSION


def _prov(p: dict, ref: str) -> dict:
    return {"extractor": p.get("extractor_id") or "unknown", "source_ref": str(p.get("source_ref") or ref)}


def _extra(p: dict) -> dict:
    return {k: v for k, v in p.items() if k in ("evidence_ids", "source_region_px") and v}


def _bbox_of(pts) -> list:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _entity(el: dict, ref: str, graph: dict) -> dict:
    p = el.get("provenance") or {}
    out = {
        "id": el["id"],
        "confidence": p.get("confidence", 0.0),
        "provenance": _prov(p, el["id"]),
        "flags": [],
        "properties": {"graph": {k: v for k, v in graph.items() if v not in (None, [], {}, "")}},
    }
    extra = _extra(p)
    if extra:
        out["properties"]["graph"]["prov_extra"] = extra
    if p.get("status", "active") != "active":
        out["status"] = p["status"]
        if p.get("superseded_by"):
            out["superseded_by"] = p["superseded_by"]
    return out


def _mm(size: dict | None) -> float | None:
    """Nominal diameter in metres from the graph doc's size {raw_text, dn_mm, outer_mm}."""
    if not size:
        return None
    mm = size.get("dn_mm") if size.get("dn_mm") is not None else size.get("outer_mm")
    return None if mm is None else round(mm / 1000.0, 5)


def _units(units: str, vector: bool) -> dict:
    si = TO_SI.get(units)
    st = "explicit" if vector and si is not None else "not_available"
    src = "dxf $INSUNITS" if vector else None
    return {
        "length": {"unit": units, "to_si": si, "status": st, "source": src},
        "area": {"unit": f"{units}^2", "to_si": None if si is None else si * si, "status": st, "source": src},
        "volume": {"unit": f"{units}^3", "to_si": None if si is None else si**3, "status": st, "source": src},
        "angle": {"unit": "DEGREE", "to_si": 1.0, "status": "inferred", "source": "convention"},
    }


def to_piping(g: dict) -> dict:
    """Graph doc -> piping doc (not yet derived). Raises KeyError on a malformed graph doc."""
    g = copy.deepcopy(g)
    sh, fr, sc = g["sheet"], g["frame"], g["scale"]
    W, H = fr["width_px"], fr["height_px"]
    page = sh.get("page_index", 0)
    vector = sh["route"] == "vector_parser"
    cov_flags: dict[str, list[str]] = {}
    for c in g.get("coverage", []):
        if c["category"] in ("tags", "diameters", "lengths", "connectivity"):
            for eid in c.get("element_ids", []):
                fl = cov_flags.setdefault(eid, [])
                if f"cov:{c['category']}" not in fl:
                    fl.append(f"cov:{c['category']}")

    fittings, equipment, segments, connections, texts = [], [], [], [], []
    for n in g["nodes"]:
        gtype = n["type"]
        graph = {
            "type": gtype, "subtype": n.get("subtype"), "tag_text_id": n.get("tag_text_id"),
            "label": n.get("label"), "rotation_deg": n.get("rotation_deg"), "ports_px": n.get("ports_px"),
            "nominal_size": n.get("nominal_size"), "system": n.get("system"), "attributes": n.get("attributes"),
            "center_px": n["center_px"], "in_room_id": n.get("in_room_id"),
            "in_room_link_id": n.get("in_room_link_id"),
        }  # fmt: skip
        el = _entity(n, n["id"], graph)
        el["flags"] += cov_flags.get(n["id"], [])
        el["tag"] = n.get("tag")
        el["bbox_px"] = n["bbox_px"]
        if gtype in ("junction", "open_end"):
            el["flags"].append(gtype)
        if n.get("subtype") == "equipment" or gtype in EQUIPMENT:
            el["kind"] = EQUIPMENT.get(gtype, "unknown")
            equipment.append(el)
        else:
            el["kind"] = FITTING.get(gtype, "other")
            el["center_px"] = n["center_px"]
            if n.get("nominal_size") and n["nominal_size"].get("raw_text"):
                el["nominal_diameter"] = n["nominal_size"]["raw_text"]
            fittings.append(el)

    k = 0
    for e in g["edges"]:
        d, ln = e.get("diameter") or {}, e.get("length") or {}
        size = d.get("value")
        graph = {
            "system": e.get("system"), "system_raw": e.get("system_raw"), "line_tag_text_id": e.get("line_tag_text_id"),
            "diameter": {x: d.get(x) for x in ("source", "text_id", "confidence", "value")},
            "insulation": e.get("insulation"), "line_style": e.get("line_style"), "layer": e.get("layer"),
            "flow": e.get("flow"), "written_m": ln.get("written_m"), "written_text_id": ln.get("written_text_id"),
            "elevation_m": e.get("elevation_m"),
            "ends": [{x: end.get(x) for x in ("port_index", "snap", "snap_distance_px")} for end in e["ends"]],
        }  # fmt: skip
        seg = _entity(e, e["id"], graph)
        seg["flags"] += cov_flags.get(e["id"], [])
        flow = (e.get("flow") or {}).get("direction", "unknown")
        seg.update(
            service=SERVICE.get(e.get("system"), "other"),
            tag=e.get("line_tag"),
            material=e.get("material"),
            nominal_diameter=size.get("raw_text") if size else None,
            nominal_diameter_m=_mm(size),
            centerline_px=e["polyline_px"],
            bbox_px=_bbox_of(e["polyline_px"]),
            flow_direction={"both": "bidirectional"}.get(flow, flow),
            start_connection_id=None,
            end_connection_id=None,
        )
        for i, end in enumerate(e["ends"]):
            if end["node_id"] is None:
                seg["flags"].append(f"end{i}_unsnapped")
                continue
            k += 1
            cid = f"conn_{k}"
            connections.append(
                {"id": cid, "confidence": e["provenance"].get("confidence", 0.0),
                 "provenance": {"extractor": e["provenance"].get("extractor_id") or "unknown", "source_ref": e["id"]},
                 "from_entity_id": end["node_id"], "to_entity_id": e["id"], "kind": "connected",
                 "point_px": end["point_px"], "is_verified": end.get("snap") == "vector_exact"}
            )  # fmt: skip
            seg["start_connection_id" if i == 0 else "end_connection_id"] = cid
        segments.append(seg)

    for t in g["texts"]:
        graph = {
            "role": t.get("role"), "value": t.get("value"), "rotation_deg": t.get("rotation_deg"),
            "language": t.get("language"),
        }  # fmt: skip
        el = _entity(t, t["id"], graph)
        el["flags"] += ["unattached_tag"] if "cov:tags" in cov_flags.get(t["id"], []) else []
        el["flags"] = [f for f in el["flags"] if f != "cov:tags"]
        el.update(text=t["raw_text"], role=TEXT_ROLE.get(t.get("role"), "other"), bbox_px=t["bbox_px"],
                  linked_entity_ids=list(t.get("attached_to", [])))  # fmt: skip
        texts.append(el)

    coverage = []
    for c in g.get("coverage", []):
        out = {k2: c[k2] for k2 in ("category", "region", "status", "confidence")}
        out["extractor"] = c.get("extractor_id", "unknown")
        if c.get("note"):
            out["note"] = c["note"]
        coverage.append(out)

    history = []
    for x in g.get("extractors", []):
        history.append({"extractor": x["id"], "action": "extractor", "cost_usd": x.get("cost_usd"),
                        "note": json.dumps({k2: x.get(k2) for k2 in ("kind", "name", "version", "run_at")})})  # fmt: skip
    facts = {
        "sheet_id": sh["id"], "diagram_type": sh.get("diagram_type"), "is_to_scale": sh.get("is_to_scale"),
        "file_format": sh.get("file_format"), "source_dataset": sh.get("source_dataset"), "title": sh.get("title"),
        "systems_shown": sh.get("systems_shown"), "languages": sh.get("languages"), "regions": sh.get("regions"),
        "dxf_layout": sh.get("dxf_layout"), "sheet_ref": sh.get("sheet_id"), "level_id": sh.get("level_id"),
        "source_units": fr.get("source_units"), "scale_provenance": sc.get("provenance"),
    }  # fmt: skip
    history.append({"extractor": sh.get("provenance", {}).get("extractor_id", "unknown"), "action": "sheet_facts",
                    "note": json.dumps({k2: v for k2, v in facts.items() if v not in (None, [], "")})})  # fmt: skip
    for h in g.get("history", []):
        history.append({"at": h.get("at"), "extractor": h.get("extractor_id", "unknown"), "action": "write_back",
                        "entity_ids": h.get("added_ids", []), "cost_usd": h.get("cost_usd"),
                        "note": h.get("trigger_question")})  # fmt: skip
    for h in history:
        for key in [k2 for k2, v in h.items() if v is None]:
            del h[key]

    units = fr.get("source_units", "px")
    transforms = list(g.get("transforms", []))
    s2p = fr.get("source_to_px")
    if s2p and list(s2p) != [1, 0, 0, 0, 1, 0]:
        transforms.insert(0, {"kind": "affine", "from": units, "to": "px",
                              "matrix": [list(s2p[:3]), list(s2p[3:])]})  # fmt: skip
    note = sc["source"]
    if sc.get("evidence_ids"):
        note += "; evidence=" + ",".join(sc["evidence_ids"])
    if sc.get("scale_text"):
        note += "; text=" + sc["scale_text"]
    scale = {"m_per_px": sc.get("metres_per_px"), "source": SCALE_SOURCE.get(sc["source"], "none"),
             "confidence": sc.get("confidence", 0.0), "note": note}  # fmt: skip
    src_id = "src_1"
    return {
        "schema_version": SCHEMA_VERSION,
        "building_schema_version": None,
        "project": {
            "name": None, "global_id": None, "schema_identifier": None, "application": None,
            "units": _units(units, vector),
        },
        "site": None,
        "building": None,
        "drawing": {
            "file": sh["source_file"], "source": src_id, "page_index": page,
            "image": {"width_px": W, "height_px": H, "dpi": fr.get("render_dpi")},
            "route": ROUTE.get(sh["route"], "none"), "read_from": sh["source_file"],
            "transforms": transforms, "scale": scale,
        },
        "sources": [{"id": src_id, "file": sh["source_file"], "role": "primary",
                     "format": FORMAT.get(sh.get("file_format"), "raster" if not vector else "dxf")}],  # fmt: skip
        "levels": [],
        "pipe_systems": [],
        "pipe_types": [],
        "pipe_segments": segments,
        "pipe_fittings": fittings,
        "pipe_equipment": equipment,
        "pipe_connections": connections,
        "texts": texts,
        "coverage": coverage,
        "history": history,
    }


# ---------------------------------------------------------------- piping -> graph doc


def _gp(el: dict) -> dict:
    return (el.get("properties") or {}).get("graph") or {}


def _prov_back(el: dict) -> dict:
    ref = el["provenance"]["source_ref"]
    p = {"extractor_id": el["provenance"]["extractor"], "confidence": el["confidence"],
         "source_ref": None if ref == el["id"] else ref}  # fmt: skip
    p.update(_gp(el).get("prov_extra", {}))
    if el.get("status", "active") != "active":
        p["status"] = el["status"]
        p["superseded_by"] = el.get("superseded_by")
    return p


def view(doc: dict) -> dict:
    """Piping doc -> graph doc (a graph doc is returned as it is)."""
    if not is_piping(doc):
        return doc
    d = doc["drawing"]
    facts, extractors, hist = {}, [], []
    for h in doc.get("history", []):
        if h["action"] == "extractor":
            extractors.append({"id": h["extractor"], **json.loads(h.get("note") or "{}"), "cost_usd": h.get("cost_usd")})
        elif h["action"] == "sheet_facts":
            facts = json.loads(h.get("note") or "{}")
        elif h["action"] == "write_back":
            hist.append({"at": h.get("at"), "extractor_id": h["extractor"], "trigger_question": h.get("note"),
                         "added_ids": h.get("entity_ids", []), "cost_usd": h.get("cost_usd")})  # fmt: skip
    img = d.get("image") or {"width_px": 1, "height_px": 1}
    units = facts.get("source_units") or doc["project"]["units"]["length"]["unit"] or "px"
    s2p = [1, 0, 0, 0, 1, 0]
    transforms = []
    for t in d.get("transforms", []):
        if t["kind"] == "affine" and t.get("matrix"):
            s2p = [*t["matrix"][0], *t["matrix"][1]]
        else:
            transforms.append(t)
    sc = d.get("scale") or {"m_per_px": None, "source": "none", "confidence": 0.0}
    parts = (sc.get("note") or "").split("; ")
    old_source = parts[0] if parts and parts[0] else "none"
    evidence = next((x[9:].split(",") for x in parts if x.startswith("evidence=")), [])
    stext = next((x[5:] for x in parts if x.startswith("text=")), None)
    page = d.get("page_index") or 0
    sheet_prov = facts.get("scale_provenance") or {"extractor_id": "ext_graph", "confidence": 1.0, "source_ref": None}
    sheet = {
        "id": facts.get("sheet_id") or f"{d['file']}#{page}", "source_file": d["file"],
        "source_dataset": facts.get("source_dataset"), "file_format": facts.get("file_format") or "other",
        "page_index": page, "dxf_layout": facts.get("dxf_layout"),
        "diagram_type": facts.get("diagram_type") or "schematic", "is_to_scale": facts.get("is_to_scale"),
        "route": ROUTE_BACK.get(d["route"], "raster_pipeline"), "sheet_id": facts.get("sheet_ref"),
        "level_id": facts.get("level_id"), "title": facts.get("title"),
        "systems_shown": facts.get("systems_shown", []), "languages": facts.get("languages", []),
        "regions": facts.get("regions", []), "provenance": sheet_prov,
    }  # fmt: skip
    g = {
        "schema_version": GRAPH_VERSION, "kind": "pipe_index", "sheet": sheet,
        "frame": {"frame_key": f"{d['file']}#{page}@{img['width_px']}x{img['height_px']}",
                  "width_px": img["width_px"], "height_px": img["height_px"], "render_dpi": img.get("dpi"),
                  "source_units": units, "source_to_px": s2p},  # fmt: skip
        "scale": {"metres_per_px": sc.get("m_per_px"), "source": old_source, "confidence": sc["confidence"],
                  "scale_text": stext, "evidence_ids": evidence, "revision": 1, "provenance": sheet_prov},  # fmt: skip
        "metres_scale_revision": None, "links": [], "extractors": extractors, "markers": [], "relations": [],
        "history": hist, "transforms": transforms,
    }  # fmt: skip

    conn = {c["id"]: c for c in doc["pipe_connections"]}
    nodes = []
    for el in [*doc["pipe_fittings"], *doc["pipe_equipment"]]:
        gp = _gp(el)
        nodes.append({
            "id": el["id"], "type": gp.get("type", "unknown"), "subtype": gp.get("subtype"), "tag": el.get("tag"),
            "tag_text_id": gp.get("tag_text_id"), "label": gp.get("label"), "bbox_px": el["bbox_px"],
            "center_px": gp.get("center_px") or el.get("center_px"), "rotation_deg": gp.get("rotation_deg", 0),
            "ports_px": gp.get("ports_px", []), "nominal_size": gp.get("nominal_size"), "system": gp.get("system"),
            "attributes": gp.get("attributes", {}), "in_room_id": gp.get("in_room_id"),
            "in_room_link_id": gp.get("in_room_link_id"), "provenance": _prov_back(el),
        })  # fmt: skip
    order = {n["id"]: i for i, n in enumerate(nodes)}
    nodes.sort(key=lambda n: order[n["id"]])  # fittings then equipment, as stored
    edges = []
    for s in doc["pipe_segments"]:
        gp = _gp(s)
        poly = s["centerline_px"]
        ends = []
        for i, key in enumerate(("start_connection_id", "end_connection_id")):
            c = conn.get(s.get(key))
            info = (gp.get("ends") or [{}, {}])[i]
            ends.append({"node_id": c["from_entity_id"] if c else None, "port_index": info.get("port_index"),
                         "snap": info.get("snap") or ("snapped" if c else "unsnapped"),
                         "snap_distance_px": info.get("snap_distance_px"),
                         "point_px": poly[0] if i == 0 else poly[-1]})  # fmt: skip
        dgr = gp.get("diameter") or {}
        flow = s.get("flow_direction") or "unknown"
        edges.append({
            "id": s["id"], "ends": ends, "polyline_px": poly, "line_tag": s.get("tag"),
            "line_tag_text_id": gp.get("line_tag_text_id"), "system": gp.get("system", SERVICE_BACK.get(s["service"], "unknown")),
            "system_raw": gp.get("system_raw"),
            "diameter": {"value": dgr.get("value"), "source": dgr.get("source") or "unknown",
                         "text_id": dgr.get("text_id"), "confidence": dgr.get("confidence") or 0.0},
            "material": s.get("material"), "insulation": gp.get("insulation"), "line_style": gp.get("line_style"),
            "layer": gp.get("layer"),
            "flow": gp.get("flow") or {"direction": "unknown", "evidence_ids": [], "confidence": 0.0},
            "length": {"geometry_m": None, "written_m": gp.get("written_m"), "written_text_id": gp.get("written_text_id"),
                       "difference_pct": None, "flag": "no_scale"},
            "elevation_m": gp.get("elevation_m"), "provenance": _prov_back(s), "polyline_m": None,
        })  # fmt: skip
    texts = []
    for t in doc["texts"]:
        gp = _gp(t)
        texts.append({
            "id": t["id"], "raw_text": t["text"], "bbox_px": t.get("bbox_px"),
            "rotation_deg": gp.get("rotation_deg") or 0, "role": gp.get("role") or "unknown",
            "value": gp.get("value"), "attached_to": list(t.get("linked_entity_ids", [])),
            "language": gp.get("language"), "provenance": _prov_back(t),
        })  # fmt: skip
    flagged = {}
    for coll in ("pipe_segments", "pipe_fittings", "pipe_equipment", "texts"):
        for el in doc[coll]:
            for f in el.get("flags", []):
                if f.startswith("cov:"):
                    flagged.setdefault(f[4:], []).append(el["id"])
                elif f == "unattached_tag":
                    flagged.setdefault("tags", []).append(el["id"])
    seg_ends = {e["id"]: [end["point_px"] for end in e["ends"] if end["node_id"] is None] for e in edges}
    coverage = []
    for i, c in enumerate(doc["coverage"], 1):
        cat = c["category"]
        if cat == "connectivity" and isinstance(c["region"], list):
            ids = [sid for sid in flagged.get("connectivity", []) if any(_inside(p, c["region"]) for p in seg_ends.get(sid, []))]
        elif cat in ("tags", "diameters", "lengths") and c["region"] == "page":
            ids = flagged.get(cat, [])
        else:
            ids = []
        coverage.append({"id": f"cov_{i}", "region": c["region"], "category": cat, "status": c["status"],
                         "confidence": c["confidence"], "extractor_id": c["extractor"], "element_ids": ids,
                         "note": c.get("note")})  # fmt: skip
    g.update(nodes=nodes, edges=edges, texts=texts, coverage=coverage)
    from pipes.schema.derive import derive_graph

    return derive_graph(g)


def _inside(p, box) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


graph = view
