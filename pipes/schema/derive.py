"""Fill the code-written fields of a piping index (stdlib only), schema piping-0.1.

    python derive.py piping.json            -> rewrites the file in place

derive(piping_doc): segment length_m / centerline_m / bbox / quantities (only with a known scale), connection point_m,
connected_segment_ids / connection_ids on fittings and equipment, pipe_systems and pipe_types (one per service, one per
service + nominal diameter), statistics. Never touches extractor-written fields.

derive_graph(graph_doc): the same idea for the pipeline's working graph (pipes.schema.convert): *_m fields, edge length
flags, summary. convert.view() calls it so the query tools see the same numbers as before.
"""
import json, math, sys, argparse
from collections import defaultdict
from datetime import datetime, timezone

def apply_affine(t, p):
    a, b, c, d, e, f = t
    return [a * p[0] + b * p[1] + c, d * p[0] + e * p[1] + f]

def point_in_polygon(p, poly):
    x, y = p; inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside

def polyline_len(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

def active(el):
    return el["provenance"].get("status", "active") == "active"

def derive_graph(pipe, drawings=None):
    s = pipe["scale"]["metres_per_px"]
    not_to_scale = pipe["sheet"].get("is_to_scale") is False or pipe["scale"]["source"] == "not_to_scale"
    k = None if (s is None or not_to_scale) else s
    m = lambda v: None if k is None else round(v * k, 4)
    mp = lambda p: None if k is None else [round(p[0] * k, 4), round(p[1] * k, 4)]

    for n in pipe["nodes"]:
        n["center_m"] = mp(n["center_px"])
        n["bbox_m"] = None if k is None else [round(v * k, 4) for v in n["bbox_px"]]
    for e in pipe["edges"]:
        e["polyline_m"] = None if k is None else [mp(p) for p in e["polyline_px"]]
        L = e["length"]
        L["geometry_m"] = None if k is None else round(polyline_len(e["polyline_px"]) * k, 3)
        w = L.get("written_m")
        if L["geometry_m"] is None:
            L["difference_pct"] = None
            L["flag"] = "not_to_scale" if not_to_scale else "no_scale"
        elif not w:
            L["difference_pct"] = None
            L["flag"] = "no_written_length"
        else:
            L["difference_pct"] = round((L["geometry_m"] - w) / w * 100, 1)
            L["flag"] = "check_this" if abs(L["difference_pct"]) > 10 else "ok"
    pipe["metres_scale_revision"] = pipe["scale"]["revision"] if k is not None else None

    for n in pipe["nodes"]:
        n.setdefault("in_room_id", None)
        n.setdefault("in_room_link_id", None)

    # summary
    counts = defaultdict(int)
    for n in pipe["nodes"]:
        if active(n): counts[n["type"]] += 1
    groups = defaultdict(list)
    for e in pipe["edges"]:
        if not active(e): continue
        v = e["diameter"]["value"]
        groups[(e["system"], v["raw_text"] if v else None)].append(e)
    rows = []
    for (sysname, size), es in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        use_written = not_to_scale or k is None
        basis = "written" if use_written else "geometry"
        vals = {e["id"]: (e["length"]["written_m"] if use_written else e["length"]["geometry_m"]) for e in es}
        known = [v for v in vals.values() if v is not None]
        rows.append({"system": sysname, "size": size, "edge_ids": list(vals),
                     "length_m": round(sum(known), 3) if known else None,
                     "missing_length_edge_ids": [i for i, v in vals.items() if v is None],
                     "length_basis": basis if known else "none"})
    # connected components over active nodes + edges
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    unsnapped = 0
    for n in pipe["nodes"]:
        if active(n): find(n["id"])
    for e in pipe["edges"]:
        if not active(e): continue
        ids = []
        for i, end in enumerate(e["ends"]):
            if end["node_id"] is None:
                unsnapped += 1; ids.append(f'{e["id"]}#end{i}')
            else:
                ids.append(end["node_id"])
        parent[find(ids[0])] = find(ids[1])
    comps = len({find(x) for x in list(parent)})
    pipe["summary"] = {"node_counts": dict(sorted(counts.items())), "length_m_by_system_and_size": rows,
                       "connected_components": comps, "unsnapped_end_count": unsnapped,
                       "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return pipe

def _num(x):
    return None if x is None else round(x, 4)


def derive(p, drawings=None):
    """Piping doc: fill the computed fields in place and return it. Idempotent."""
    k = (p["drawing"].get("scale") or {}).get("m_per_px")
    segs, fits, eqs = p["pipe_segments"], p["pipe_fittings"], p["pipe_equipment"]
    conns = {c["id"]: c for c in p["pipe_connections"]}
    for c in p["pipe_connections"]:
        pt = c.get("point_px")
        c["point_m"] = None if (k is None or pt is None) else [_num(pt[0] * k), _num(pt[1] * k)]
    for s in segs:
        poly = s.get("centerline_px")
        if poly and len(poly) > 1:
            xs, ys = [q[0] for q in poly], [q[1] for q in poly]
            s["bbox_px"] = [min(xs), min(ys), max(xs), max(ys)]
        q = s.setdefault("quantities", {})
        q.pop("length_m", None)
        if k is not None and poly and len(poly) > 1:
            s["centerline_m"] = [[_num(x * k), _num(y * k)] for x, y in poly]
            s["length_m"] = round(polyline_len(poly) * k, 3)
            q["length_m"] = {"value": s["length_m"], "unit": "m", "status": "calculated",
                             "method": "centerline_px length x drawing.scale.m_per_px", "source": "centerline_px"}  # fmt: skip
        else:
            s["centerline_m"], s["length_m"] = None, None
        written = (_gp_written(s))
        q.pop("written_length_m", None)
        if written is not None:
            q["written_length_m"] = {"value": written, "unit": "m", "status": "explicit",
                                     "method": "length written on the drawing", "source": "text"}  # fmt: skip
        if not q:
            del s["quantities"]
    seg_of = {}
    for s in segs:
        for key in ("start_connection_id", "end_connection_id"):
            if s.get(key):
                seg_of.setdefault(conns[s[key]]["from_entity_id"], []).append((s["id"], s[key]))
    for el in [*fits, *eqs]:
        pairs = seg_of.get(el["id"], [])
        el["connected_segment_ids"] = sorted({a for a, _ in pairs})
        el["connection_ids"] = [b for _, b in pairs]

    live = [s for s in segs if s.get("status", "active") == "active"]
    systems, types = {}, {}
    for s in live:
        systems.setdefault(s["service"], []).append(s)
        types.setdefault((s["service"], s.get("nominal_diameter")), []).append(s)
    by_service_eq = defaultdict(list)
    for el in [*fits, *eqs]:
        if el.get("status", "active") == "active" and el.get("system_id"):
            by_service_eq[el["system_id"]].append(el["id"])
    p["pipe_systems"] = [
        {"id": f"sys_{svc}", "confidence": 0.5, "provenance": {"extractor": "derive", "source_ref": f"sys_{svc}"},
         "name": svc, "service": svc, "segment_ids": [s["id"] for s in ss], "segment_count": len(ss),
         "total_pipe_length_m": _total(ss, k)}
        for svc, ss in sorted(systems.items())
    ]  # fmt: skip
    p["pipe_types"] = []
    for i, ((svc, nd), ss) in enumerate(sorted(types.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))), 1):
        tid = f"type_{i}"
        p["pipe_types"].append(
            {"id": tid, "confidence": 0.5, "provenance": {"extractor": "derive", "source_ref": tid},
             "name": f"{svc} {nd or 'size unknown'}", "service": svc, "nominal_diameter": nd,
             "nominal_diameter_m": ss[0].get("nominal_diameter_m"), "segment_ids": [s["id"] for s in ss],
             "segment_count": len(ss), "total_length_m": _total(ss, k)}
        )  # fmt: skip
        for s in ss:
            s["type_id"] = tid
    for svc, ss in systems.items():
        for s in ss:
            s["system_id"] = f"sys_{svc}"

    def stat(name, value, unit, **kw):
        return {"name": name, "value": value, "unit": unit, "status": kw.pop("status", "calculated"),
                "method": kw.pop("method", "count of active entities"), "source_entity_ids": kw.pop("ids", []), **kw}  # fmt: skip

    def active(xs):
        return [x for x in xs if x.get("status", "active") == "active"]

    stats = [
        stat("segment_count", len(live), "count", ids=[s["id"] for s in live]),
        stat("fitting_count", len(active(fits)), "count", ids=[f["id"] for f in active(fits)]),
        stat("equipment_count", len(active(eqs)), "count", ids=[e["id"] for e in active(eqs)]),
        stat("connection_count", len(p["pipe_connections"]), "count", ids=[c["id"] for c in p["pipe_connections"]]),
        stat("total_pipe_length_m", _total(live, k), "m", status="calculated" if k is not None else "not_available",
             method="sum of active segment length_m (scale known)" if k is not None else "no scale: not computed",
             ids=[s["id"] for s in live]),  # fmt: skip
    ]
    for svc, ss in sorted(systems.items()):
        stats.append(stat(f"total_pipe_length_m:{svc}", _total(ss, k), "m", service=svc,
                          status="calculated" if k is not None else "not_available",
                          method="sum of active segment length_m" if k is not None else "no scale: not computed",
                          ids=[s["id"] for s in ss]))  # fmt: skip
    p["statistics"] = stats
    return p


def _gp_written(s):
    return ((s.get("properties") or {}).get("graph") or {}).get("written_m")


def _total(segs, k):
    if k is None or not segs:
        return None
    vals = [s["length_m"] for s in segs if s.get("length_m") is not None]
    return round(sum(vals), 3) if vals else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pipe")
    a = ap.parse_args()
    pipe = json.load(open(a.pipe, encoding="utf-8"))
    json.dump(derive(pipe), open(a.pipe, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("derived", a.pipe)
