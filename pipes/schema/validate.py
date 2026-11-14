"""Validate a pipe index file: JSON Schema + the cross-reference rules the schema cannot express.

    python validate.py pipe.json [--drawing drawing.json ...]
"""
import json, sys, argparse, os
import jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))

def check(pipe, drawings):
    errs = []
    schema = json.load(open(os.path.join(HERE, "pipe.schema.json")))
    for e in jsonschema.Draft202012Validator(schema).iter_errors(pipe):
        errs.append(f"schema: {'/'.join(map(str, e.path))}: {e.message}")
    ids = {}
    for key in ("nodes", "edges", "markers", "texts", "relations", "coverage", "links"):
        for el in pipe.get(key, []):
            if el["id"] in ids: errs.append(f"duplicate id {el['id']}")
            ids[el["id"]] = key
    ext = {x["id"] for x in pipe["extractors"]}
    def walk(o):
        if isinstance(o, dict):
            if "extractor_id" in o and o["extractor_id"] not in ext: errs.append(f"unknown extractor {o['extractor_id']}")
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(pipe)
    W, H = pipe["frame"]["width_px"], pipe["frame"]["height_px"]
    cov_elements = {i for c in pipe["coverage"] if c["category"] == "connectivity" for i in c.get("element_ids", [])}
    for e in pipe["edges"]:
        for i, end in enumerate(e["ends"]):
            if end["node_id"] is not None and ids.get(end["node_id"]) != "nodes":
                errs.append(f"{e['id']} end {i}: unknown node {end['node_id']}")
            if end["snap"] == "unsnapped" and end["node_id"] is not None:
                errs.append(f"{e['id']} end {i}: unsnapped but has node_id")
            if end["node_id"] is None and e["id"] not in cov_elements:
                errs.append(f"{e['id']} end {i}: unsnapped end without a 'connectivity' coverage entry")
        if e["ends"][0]["point_px"] != e["polyline_px"][0] or e["ends"][1]["point_px"] != e["polyline_px"][-1]:
            errs.append(f"{e['id']}: ends[].point_px must equal first/last polyline point")
        for t in (e.get("line_tag_text_id"), e["diameter"].get("text_id"), e["length"].get("written_text_id")):
            if t and ids.get(t) != "texts": errs.append(f"{e['id']}: unknown text {t}")
    for n in pipe["nodes"]:
        x0, y0, x1, y1 = n["bbox_px"]
        if not (0 <= x0 <= x1 <= W and 0 <= y0 <= y1 <= H): errs.append(f"{n['id']}: bbox outside frame")
        if n.get("tag_text_id") and ids.get(n["tag_text_id"]) != "texts": errs.append(f"{n['id']}: unknown text")
    for mk in pipe["markers"]:
        if mk.get("on_edge_id") and ids.get(mk["on_edge_id"]) != "edges": errs.append(f"{mk['id']}: unknown edge {mk['on_edge_id']}")
    for r in pipe["relations"]:
        if r["from_id"] not in ids: errs.append(f"{r['id']}: unknown from_id {r['from_id']}")
        to = r.get("to") or {}
        if r["kind"] == "same_as":
            d = drawings.get(to.get("file_id"))
            if d is not None:
                all_ids = {el["id"] for k in ("rooms", "walls", "openings", "texts", "objects") for el in d[k]}
                if to.get("element_id") not in all_ids: errs.append(f"{r['id']}: {to.get('element_id')} not in {to.get('file_id')}")
    for l in pipe["links"]:
        if l["relation"] == "same_page" and l.get("transform") != [1, 0, 0, 0, 1, 0]:
            errs.append(f"{l['id']}: same_page needs the identity transform")
        d = drawings.get(l["drawing_id"])
        if d is not None and l["relation"] == "same_page":
            img = d["drawing"]["image"]
            if (img["width_px"], img["height_px"]) != (W, H): errs.append(f"{l['id']}: same_page but image sizes differ")
    if pipe["metres_scale_revision"] not in (None, pipe["scale"]["revision"]):
        errs.append("metres are stale: run derive.py")
    return errs

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("pipe"); ap.add_argument("--drawing", action="append", default=[])
    a = ap.parse_args()
    drawings = {}
    for p in a.drawing:
        d = json.load(open(p)); drawings[d["drawing"]["id"]] = d
    errs = check(json.load(open(a.pipe)), drawings)
    print("\n".join(errs) if errs else f"OK: {a.pipe}")
    sys.exit(1 if errs else 0)
