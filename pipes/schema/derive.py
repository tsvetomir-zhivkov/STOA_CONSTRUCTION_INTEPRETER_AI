"""Fill the code-written fields of a pipe index file (no models, stdlib only).

    python derive.py pipe.json [--drawing drawing.json ...]  -> rewrites pipe.json in place

Writes: *_m fields, edges[].length.geometry_m / difference_pct / flag,
nodes[].in_room_id (only through a link with a transform), summary,
metres_scale_revision. Never touches extractor-written fields.
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

def derive(pipe, drawings):
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

    # room lookup through registered links only
    for n in pipe["nodes"]:
        n["in_room_id"] = None; n["in_room_link_id"] = None
    for link in pipe["links"]:
        d = drawings.get(link["drawing_id"])
        if d is None or link.get("transform") is None:
            continue
        rooms = [r for r in d["rooms"] if r["provenance"].get("status", "active") == "active"]
        for n in pipe["nodes"]:
            q = apply_affine(link["transform"], n["center_px"])
            hit = next((r["id"] for r in rooms if point_in_polygon(q, r["polygon_px"])), None)
            if hit and n["in_room_id"] is None:
                n["in_room_id"] = hit; n["in_room_link_id"] = link["id"]

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

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("pipe"); ap.add_argument("--drawing", action="append", default=[])
    a = ap.parse_args()
    pipe = json.load(open(a.pipe))
    drawings = {}
    for path in a.drawing:
        d = json.load(open(path)); drawings[d["drawing"]["id"]] = d
    json.dump(derive(pipe, drawings), open(a.pipe, "w"), indent=2, ensure_ascii=False)
    print("derived", a.pipe)
