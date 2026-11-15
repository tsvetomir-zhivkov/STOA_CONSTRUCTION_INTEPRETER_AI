"""DXF route (vector input): pipe lines, INSERT blocks as symbols, TEXT through the tag rules, drawing units as scale.

  python -m pipes data.dxf --source-file hypothetical/LVI-heating.dxf     # also writes + publishes the hand-off files

No detection and no model: everything is read from the file, so every end snaps `vector_exact` and the graph is exact
to the drawing. Rules (plan step 4, and what makes the answers trustworthy):
  * pipe layers by name (glossary_pipes.is_pipe_layer); when no layer matches, every LINE/LWPOLYLINE/POLYLINE in the
    modelspace on a layer that is not a frame/text/dimension layer (blocks are never searched); ARC only on pipe layers;
  * a pipe that runs through a symbol's box is cut at the box: it ends at the symbol on both sides;
  * a pipe end on the side of another pipe makes a tee (the other pipe is split there); two pipes that cross without
    a shared vertex are NOT connected, unless a small CIRCLE (junction dot) sits on the crossing;
  * a run of lines in a row on one layer is one pipe; the layer and the source entity handles are kept on it;
  * a free end near an off-sheet text ('JATKUU', 'TO', '->') is an open end, any other free end is `unsnapped` and gets
    a connectivity coverage entry;
  * scale = $INSUNITS (mm, m, inch, ...) unless the sheet says it is not to scale (title or note text such as
    'EI MITTAKAAVASSA', or a schematic keyword): then metres come only from written 'L=1,2' labels.
Pixel frame: the page is rendered to a PNG whose axes span the drawing extents, so source_to_px is exact:
x_px = s (x - xmin), y_px = s (ymax - y).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf import bbox as ezbbox
from ezdxf import path as ezpath
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry import box as sbox
from shapely.ops import substring

from pipes import graph as gr
from pipes.glossary_pipes import (
    CONTINUATION,
    fold,
    is_excluded_layer,
    is_pipe_layer,
    system_from_layer,
    type_from_block,
)
from pipes.schema.convert import graph
from pipes.tags import attach_tags
from pipes.write import finalize

LINE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE"}
UNIT_M = {1: 0.0254, 2: 0.3048, 3: 1609.344, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0, 10: 0.9144}
SOURCE_UNITS = {1: "inch", 4: "mm", 6: "m"}
TARGET_PX = 2400.0  # longest side of the rendered page
DPI = 100
TOL_M = 0.0005  # 0.5 mm: endpoint coincidence
SNAP_FACTOR = 5  # a free end snaps to a symbol within this many tolerances
DOT_MAX_FRAC = 0.01  # a junction dot is a CIRCLE no larger than this share of the drawing diagonal
SCHEMATIC = re.compile(r"KAAVIO|PERIAATE|STRANG|SCHEMA|PRINCIP|RISER|DIAGRAM|ISOMETRI")
PLAN = re.compile(r"POHJA|(^|[^A-Z])PLAN([^A-Z]|$)|GRUNDRISS|PLANTEGNING")
NOT_TO_SCALE = re.compile(
    r"EI MITTAKAAVASSA|NOT TO SCALE|(^|[^A-Z])NTS([^A-Z]|$)|ICKE SKALA|IKKE I MALESTOK|NICHT MASSSTAB"
)
TAG_ATTRIBS = ("TAG", "NAME", "ID")


# ---------------------------------------------------------------- reading


def _points(e, dist: float) -> list[tuple[float, float]]:
    pts = [(v.x, v.y) for v in ezpath.make_path(e).flattening(dist)]
    out = [pts[0]] if pts else []
    for p in pts[1:]:
        if math.dist(p, out[-1]) > 1e-9:
            out.append(p)
    return out


def _text_of(e) -> str:
    return e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text


def _text_height(e) -> float:
    return float(e.dxf.char_height if e.dxftype() == "MTEXT" else e.dxf.height)


def _text_box(e) -> tuple[float, float, float, float]:
    b = ezbbox.extents([e])
    if b.has_data:
        return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    x, y = e.dxf.insert.x, e.dxf.insert.y
    h = _text_height(e)
    return (x, y, x + 0.6 * h * len(_text_of(e)), y + h)


def read_dxf(path: Path) -> dict:
    """The modelspace split into pipe segments, inserts, texts and junction dots (all in drawing units)."""
    d = ezdxf.readfile(path)
    msp = d.modelspace()
    ents = list(msp)
    line_ents = [e for e in ents if e.dxftype() in LINE_TYPES | {"ARC"}]
    layers = sorted({e.dxf.layer for e in line_ents if e.dxftype() in LINE_TYPES})
    pipe_layers = [x for x in layers if is_pipe_layer(x)]
    fallback = not pipe_layers
    if fallback:
        pipe_layers = [x for x in layers if not is_excluded_layer(x)]
    keep = set(pipe_layers)
    all_x, all_y = [], []
    for e in ents:  # drawing extents from everything we use
        if e.dxftype() in LINE_TYPES and e.dxf.layer in keep:
            b = ezbbox.extents([e])
            if b.has_data:
                all_x += [b.extmin.x, b.extmax.x]
                all_y += [b.extmin.y, b.extmax.y]
    ext_guess = max(
        (max(all_x) - min(all_x)) if all_x else 1.0, (max(all_y) - min(all_y)) if all_y else 1.0, 1e-6
    )
    segs = []
    for e in line_ents:
        if e.dxf.layer not in keep:
            continue
        if e.dxftype() == "ARC" and is_pipe_layer(e.dxf.layer) is False:
            continue
        pts = _points(e, ext_guess * 1e-3)
        if len(pts) >= 2:
            segs.append({"pts": pts, "layer": e.dxf.layer, "handle": e.dxf.handle})
    inserts = []
    for e in ents:
        if e.dxftype() != "INSERT" or e.dxf.name.startswith("*"):
            continue
        b = ezbbox.extents(e.virtual_entities())
        inserts.append({
            "name": e.dxf.name, "layer": e.dxf.layer, "handle": e.dxf.handle, "rotation": float(e.dxf.rotation or 0),
            "at": (e.dxf.insert.x, e.dxf.insert.y),
            "box": (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y) if b.has_data else None,
            "attribs": {a.dxf.tag.upper(): a.dxf.text for a in e.attribs},
        })  # fmt: skip
    texts = [
        {"text": _text_of(e), "box": _text_box(e), "height": _text_height(e), "handle": e.dxf.handle,
         "rotation": float(e.dxf.rotation or 0)}
        for e in ents if e.dxftype() in ("TEXT", "MTEXT") and _text_of(e).strip()
    ]  # fmt: skip
    dots = [(e.dxf.center.x, e.dxf.center.y, e.dxf.radius) for e in ents if e.dxftype() == "CIRCLE"]
    code = int(d.header.get("$INSUNITS", 0))
    return {"dxf": d, "segs": segs, "inserts": inserts, "texts": texts, "dots": dots, "pipe_layers": pipe_layers,
            "fallback": fallback, "insunits": code, "unit_m": UNIT_M.get(code)}  # fmt: skip


# ---------------------------------------------------------------- geometry: clip, split, cluster


def _lines(geom) -> list[LineString]:
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString) or hasattr(geom, "geoms"):
        return [g for sub in geom.geoms for g in _lines(sub)]
    return []


def clip_to_symbols(segs: list[dict], boxes: list[tuple], tol: float) -> list[dict]:
    """Cut every pipe where it runs through a symbol box: only the parts outside remain, ending on the box border."""
    polys = [sbox(*b) for b in boxes]
    out = []
    for s in segs:
        parts = [LineString(s["pts"])]
        for poly in polys:
            nxt = []
            for part in parts:
                if part.intersection(poly).length > tol:
                    nxt += [x for x in _lines(part.difference(poly)) if x.length > tol]
                else:
                    nxt.append(part)
            parts = nxt
        out += [{**s, "pts": [tuple(c) for c in p.coords]} for p in parts]
    return out


def split_pieces(segs: list[dict], dots: list[tuple], tol: float) -> list[dict]:
    """Split a pipe where another pipe ends on its side (tee) or where a junction dot sits on a crossing."""
    lines = [LineString(s["pts"]) for s in segs]
    cuts: dict[int, list[float]] = {i: [] for i in range(len(segs))}
    ends = [(i, Point(ln.coords[0])) for i, ln in enumerate(lines)] + [
        (i, Point(ln.coords[-1])) for i, ln in enumerate(lines)
    ]
    for j, b in enumerate(lines):
        for i, pt in ends:
            if i == j or b.distance(pt) > tol:
                continue
            s = b.project(pt)
            if tol < s < b.length - tol:
                cuts[j].append(s)
    for cx, cy, r in dots:  # junction dots on crossings
        c = Point(cx, cy)
        near = [k for k, l in enumerate(lines) if l.distance(c) <= max(r, tol) * 1.5]  # noqa: E741
        for a in range(len(near)):
            for b in range(a + 1, len(near)):
                inter = lines[near[a]].intersection(lines[near[b]])
                if isinstance(inter, Point) and not inter.is_empty:
                    for k in (near[a], near[b]):
                        s = lines[k].project(inter)
                        if tol < s < lines[k].length - tol:
                            cuts[k].append(s)
    out = []
    for i, s in enumerate(segs):
        marks = sorted({round(x, 9) for x in cuts[i]})
        if not marks:
            out.append(s)
            continue
        bounds = [0.0, *marks, lines[i].length]
        for a, b in zip(bounds, bounds[1:], strict=False):
            if b - a > tol:
                out.append({**s, "pts": [tuple(c) for c in substring(lines[i], a, b).coords]})
    return out


def cluster_nodes(pieces: list[dict], tol: float) -> gr.G:
    """Endpoints within tol are one node; every piece becomes an edge between two nodes."""
    pts = [p["pts"][0] for p in pieces] + [p["pts"][-1] for p in pieces]
    parent = list(range(len(pts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(pts):
        key = (int(x // (2 * tol)), int(y // (2 * tol)))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((key[0] + dx, key[1] + dy), []):
                    if math.dist(pts[i], pts[j]) <= tol:
                        parent[find(i)] = find(j)
        grid.setdefault(key, []).append(i)
    members: dict[int, list[int]] = {}
    for i in range(len(pts)):
        members.setdefault(find(i), []).append(i)
    g = gr.G()
    node_of: dict[int, int] = {}
    for idx in members.values():
        pos = np.mean([pts[i] for i in idx], axis=0).tolist()
        n = g.add_node(pos)
        for i in idx:
            node_of[i] = n
    k = len(pieces)
    for i, p in enumerate(pieces):
        a, b = node_of[i], node_of[k + i]
        poly = [list(g.nodes[a]["pos"]), *[list(c) for c in p["pts"][1:-1]], list(g.nodes[b]["pos"])]
        g.edges.append({"a": a, "b": b, "poly": poly, "layer": p["layer"], "src": [p["handle"]]})
    return g


# ---------------------------------------------------------------- sheet-level facts


def sheet_facts(texts: list[dict], layers: list[str], file_name: str) -> dict:
    blob = fold(" ".join([*(t["text"] for t in texts), *layers, file_name]))
    note = next((t for t in texts if NOT_TO_SCALE.search(fold(t["text"]))), None)
    schematic = SCHEMATIC.search(blob)
    plan = PLAN.search(blob)
    if note or schematic:
        diagram = "riser_diagram" if "RISER" in blob else "isometric" if "ISOMETRI" in blob else "schematic"
        to_scale = False
    elif plan:
        diagram, to_scale = "pipe_plan", True
    else:
        diagram, to_scale = "other", None
    title = max(texts, key=lambda t: t["height"])["text"] if texts else None
    return {"diagram_type": diagram, "is_to_scale": to_scale, "note": note, "title": title}


def render_png(dxf, png: Path, ext: tuple[float, float, float, float], s: float) -> Path:
    """The modelspace on a white page whose axes span exactly `ext` (xmin, ymin, xmax, ymax): 1 unit = s pixels."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    xmin, ymin, xmax, ymax = ext
    w, h = round((xmax - xmin) * s), round((ymax - ymin) * s)
    fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)
    Frontend(RenderContext(dxf), MatplotlibBackend(ax), config=cfg).draw_layout(
        dxf.modelspace(), finalize=True
    )
    fig.set_size_inches(w / DPI, h / DPI)  # ezdxf resizes the figure when it finalises the drawing
    ax.set_position([0, 0, 1, 1])
    ax.set_aspect("auto")
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=DPI, facecolor="white")
    plt.close(fig)
    return png


# ---------------------------------------------------------------- the route


def extract(path: str | Path, source_file: str | None = None, png: Path | None = None) -> dict:
    """DXF file -> schema-valid pipe doc (finalized). Writes the page PNG when `png` is given."""
    path = Path(path)
    source_file = (source_file or path.as_posix()).replace("\\", "/")
    r = read_dxf(path)
    segs, inserts, texts = r["segs"], r["inserts"], r["texts"]
    if not segs:
        raise ValueError(f"{path}: no pipe lines found (layers tried: {r['pipe_layers']})")
    unit_m = r["unit_m"]

    # drawing extents (pipes, symbols, texts) with a 3% margin, and the pixel scale s
    xs = [x for sg in segs for x, _ in sg["pts"]] + [b[i] for t in texts for b in [t["box"]] for i in (0, 2)]
    ys = [y for sg in segs for _, y in sg["pts"]] + [b[i] for t in texts for b in [t["box"]] for i in (1, 3)]
    for ins in inserts:
        if ins["box"]:
            xs += [ins["box"][0], ins["box"][2]]
            ys += [ins["box"][1], ins["box"][3]]
    w0, h0 = max(xs) - min(xs), max(ys) - min(ys)
    pad = 0.03 * max(w0, h0)
    xmin, ymin, xmax, ymax = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    s = TARGET_PX / max(xmax - xmin, ymax - ymin)
    W, H = round((xmax - xmin) * s), round((ymax - ymin) * s)
    diag = math.hypot(xmax - xmin, ymax - ymin)
    tol = TOL_M / unit_m if unit_m else 1e-4 * diag
    snap = SNAP_FACTOR * tol

    def px(p):
        return [round(s * (p[0] - xmin), 2), round(s * (ymax - p[1]), 2)]

    def px_box(b):
        return [
            round(s * (b[0] - xmin), 2),
            round(s * (ymax - b[3]), 2),
            round(s * (b[2] - xmin), 2),
            round(s * (ymax - b[1]), 2),
        ]

    # symbols: INSERTs on pipe layers, or touched by a pipe end
    seg_ends = [pt for sg in segs for pt in (sg["pts"][0], sg["pts"][-1])]

    def touched(ins) -> bool:
        b = ins["box"]
        return bool(b) and any(gr.rect_dist(p, b) <= snap for p in seg_ends)

    syms = []
    for ins in inserts:
        if ins["box"] is None:
            r_ = 0.005 * diag
            ins = {**ins, "box": (ins["at"][0] - r_, ins["at"][1] - r_, ins["at"][0] + r_, ins["at"][1] + r_)}
        if ins["layer"] in r["pipe_layers"] or is_pipe_layer(ins["layer"]) or touched(ins):
            syms.append(ins)

    pieces = clip_to_symbols(segs, [x["box"] for x in syms], tol)
    dots = [
        c
        for c in r["dots"]
        if c[2] <= DOT_MAX_FRAC * diag
        and not any(x["box"][0] <= c[0] <= x["box"][2] and x["box"][1] <= c[1] <= x["box"][3] for x in syms)
    ]
    pieces = split_pieces(pieces, dots, tol)
    g = cluster_nodes(pieces, tol)
    # nodes at a symbol keep their identity (never merged into a longer pipe)
    sym_of: dict[int, int] = {}
    for n, d in g.nodes.items():
        best = min(((gr.rect_dist(d["pos"], x["box"]), k) for k, x in enumerate(syms)), default=None)
        if best and best[0] <= snap:
            sym_of[n] = best[1]
            d["kind"] = "port"
    gr.collapse_deg2(g)
    gr.resolve_branches(g, 1.0, lambda _p: True)  # shared vertex = connected: deg 3 tee, deg >= 4 junction

    # ---- document
    prov = lambda c=1.0, ref=None: {"extractor_id": "ext_dxf", "confidence": c, "source_ref": ref}  # noqa: E731
    facts = sheet_facts(texts, r["pipe_layers"], Path(source_file).name)
    note_id = None
    doc_texts = []
    for i, t in enumerate(texts, 1):
        tall = (t["rotation"] % 180) == 90
        doc_texts.append({
            "id": f"text_{i}", "raw_text": t["text"], "bbox_px": px_box(t["box"]), "rotation_deg": 90 if tall else 0,
            "role": "unknown", "value": None, "attached_to": [], "language": None, "provenance": prov(1.0, t["handle"]),
        })  # fmt: skip
        if facts["note"] is t:
            note_id = f"text_{i}"
    nodes = []
    ports: list[list[list[float]]] = [[] for _ in syms]
    for n, k in sym_of.items():
        if g.inc().get(n) and all(n in (e["a"], e["b"]) for e in g.edges if n in (e["a"], e["b"])):
            ports[k].append(g.nodes[n]["pos"])
    for k, x in enumerate(syms):
        tag = next((x["attribs"][a] for a in TAG_ATTRIBS if x["attribs"].get(a)), None)
        uniq: list[list[float]] = []
        for p in ports[k]:
            if not any(math.dist(p, q) <= tol for q in uniq):
                uniq.append(p)
        ports[k] = uniq
        b = px_box(x["box"])
        nodes.append({
            "id": f"node_{k + 1}", "type": type_from_block(x["name"]), "subtype": x["name"], "tag": tag,
            "tag_text_id": None, "label": None, "bbox_px": b, "center_px": [round((b[0] + b[2]) / 2, 2), round((b[1] + b[3]) / 2, 2)],
            "rotation_deg": round(x["rotation"]), "ports_px": [px(p) for p in uniq], "nominal_size": None, "system": None,
            "attributes": {"block": x["name"], "layer": x["layer"], **({"tag_source": "attribute"} if tag else {}), **{f"attr_{a.lower()}": v for a, v in x["attribs"].items()}},
            "in_room_id": None, "in_room_link_id": None, "provenance": prov(1.0, x["handle"]),
        })  # fmt: skip
    nid = len(nodes)

    def add_struct(kind: str, pos) -> str:
        nonlocal nid
        nid += 1
        p = px(pos)
        nodes.append({
            "id": f"node_{nid}", "type": kind, "subtype": None, "tag": None, "tag_text_id": None, "label": None,
            "bbox_px": [p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], "center_px": p, "rotation_deg": 0, "ports_px": [],
            "nominal_size": None, "system": None, "attributes": {}, "in_room_id": None, "in_room_link_id": None,
            "provenance": prov(1.0),
        })  # fmt: skip
        return f"node_{nid}"

    struct = {
        n: add_struct(d["kind"], d["pos"]) for n, d in g.nodes.items() if d["kind"] in ("tee", "junction")
    }
    cont = [t for t in texts if CONTINUATION.search(t["text"])]

    def end_for(n: int) -> dict:
        pos = g.nodes[n]["pos"]
        if n in struct:
            return {"node_id": struct[n], "port_index": None, "snap": "vector_exact", "snap_distance_px": 0}
        near = min(((gr.rect_dist(pos, x["box"]), k) for k, x in enumerate(syms)), default=None)
        if near and near[0] <= snap:
            k = near[1]
            pi = next((i for i, q in enumerate(ports[k]) if math.dist(pos, q) <= snap), None)
            return {
                "node_id": f"node_{k + 1}",
                "port_index": pi,
                "snap": "vector_exact",
                "snap_distance_px": round(near[0] * s, 2),
            }
        if any(gr.rect_dist(pos, t["box"]) <= max(10 * tol, 0.03 * diag) for t in cont):
            return {
                "node_id": add_struct("open_end", pos),
                "port_index": None,
                "snap": "snapped",
                "snap_distance_px": 0,
            }
        return {"node_id": None, "port_index": None, "snap": "unsnapped", "snap_distance_px": None}

    cache: dict[int, dict] = {}
    edges, unsnapped = [], []
    dxf = r["dxf"]
    for e in g.edges:
        ends = []
        for key in ("a", "b"):
            n = e[key]
            cache.setdefault(n, end_for(n))
            ends.append(dict(cache[n]))
        poly = [px(p) for p in e["poly"]]
        ed = gr.edge_doc(len(edges) + 1, ends, poly, "ext_dxf", 1.0)
        ed["layer"] = e.get("layer")
        ed["system"] = system_from_layer(e.get("layer") or "")
        ed["system_raw"] = e.get("layer")
        ed["provenance"]["source_ref"] = "+".join(e.get("src", []))
        ent = dxf.entitydb.get(e["src"][0]) if e.get("src") else None
        lt = fold(ent.dxf.linetype if ent is not None and ent.dxf.hasattr("linetype") else "")
        if "DASHDOT" in lt or "CENTER" in lt:
            ed["line_style"] = "dash_dot"
        elif "DASH" in lt or "HIDDEN" in lt:
            ed["line_style"] = "dashed"
        elif "DOT" in lt:
            ed["line_style"] = "dotted"
        edges.append(ed)
        for k, x in enumerate(ends):
            if x["node_id"] is None:
                p = poly[0] if k == 0 else poly[-1]
                unsnapped.append(
                    (ed["id"], [max(p[0] - 40, 0), max(p[1] - 40, 0), min(p[0] + 40, W), min(p[1] + 40, H)])
                )

    # scale from the drawing units, unless the sheet says it is not to scale
    if facts["is_to_scale"] is False:
        scale = {"metres_per_px": None, "source": "not_to_scale", "confidence": 0.95,
                 "scale_text": facts["note"]["text"] if facts["note"] else facts["title"], "evidence_ids": [note_id] if note_id else []}  # fmt: skip
    elif unit_m:
        scale = {"metres_per_px": unit_m / s, "source": "vector_units", "confidence": 0.9 if facts["is_to_scale"] else 0.6,
                 "scale_text": None, "evidence_ids": []}  # fmt: skip
    else:
        scale = {
            "metres_per_px": None,
            "source": "none",
            "confidence": 0.0,
            "scale_text": None,
            "evidence_ids": [],
        }
    scale |= {"revision": 1, "provenance": prov(scale["confidence"])}
    cov = [
        {"id": "cov_1", "region": "page", "category": "symbols", "status": "analysed", "confidence": 1.0, "extractor_id": "ext_dxf", "element_ids": [],
         "note": f"{len(syms)} INSERT block(s) on pipe layers or touched by a pipe"},
        {"id": "cov_2", "region": "page", "category": "pipes", "status": "analysed", "confidence": 1.0, "extractor_id": "ext_dxf", "element_ids": [],
         "note": ("no pipe layer matched: every line in the modelspace outside frame/text layers was used" if r["fallback"]
                  else f"pipe layers: {', '.join(r['pipe_layers'])}")},
        {"id": "cov_3", "region": "page", "category": "connectivity", "status": "analysed", "confidence": 1.0, "extractor_id": "ext_graph", "element_ids": [],
         "note": "exact: shared vertices connect, crossings without a shared vertex or a junction dot do not"},
        {"id": "cov_4", "region": "page", "category": "text", "status": "analysed", "confidence": 1.0, "extractor_id": "ext_dxf", "element_ids": [],
         "note": "TEXT and MTEXT read from the file"},
    ]  # fmt: skip
    for k, (eid, region) in enumerate(unsnapped, 5):
        cov.append({"id": f"cov_{k}", "region": [round(v, 1) for v in region], "category": "connectivity", "status": "partial", "confidence": 0.3,
                    "extractor_id": "ext_graph", "element_ids": [eid], "note": "pipe end that reaches no symbol, junction or open end"})  # fmt: skip
    systems = sorted({e["system"] for e in edges if e["system"] != "unknown"})
    doc = {
        "schema_version": "0.1", "kind": "pipe_index",
        "sheet": {"id": f"{source_file}#Model", "source_file": source_file, "source_dataset": None, "file_format": "dxf",
                  "page_index": 0, "dxf_layout": "Model", "diagram_type": facts["diagram_type"], "is_to_scale": facts["is_to_scale"],
                  "route": "vector_parser", "sheet_id": None, "level_id": None, "title": facts["title"], "systems_shown": systems,
                  "languages": [], "regions": [], "provenance": prov(0.9)},
        "frame": {"frame_key": f"{source_file}#0@{W}x{H}", "width_px": W, "height_px": H, "render_dpi": DPI,
                  "source_units": SOURCE_UNITS.get(r["insunits"], "unitless"), "source_to_px": [s, 0, -s * xmin, 0, -s, s * ymax]},
        "scale": scale, "metres_scale_revision": None, "links": [],
        "extractors": [
            {"id": "ext_dxf", "kind": "vector_parser", "name": f"ezdxf {ezdxf.__version__}", "version": ezdxf.__version__, "run_at": None, "cost_usd": 0.0},
            {"id": "ext_graph", "kind": "graph_builder", "name": "pipes.graph + dxf_pipes (exact)", "version": None, "run_at": None, "cost_usd": 0.0},
        ],
        "nodes": nodes, "edges": edges, "markers": [], "texts": doc_texts, "relations": [], "coverage": cov, "history": [],
    }  # fmt: skip
    attach_tags(doc, vector=True)
    out = finalize(doc)
    if png is not None:
        render_png(dxf, png, (xmin, ymin, xmax, ymax), s)
    return out


def translate_dxf(
    path: str | Path,
    source_file: str | None = None,
    publish: bool = True,
    render: bool = True,
    pipes_dir: Path | None = None,
) -> dict:
    """extract() and, with publish, write <pipes_dir>/<source>/<stem>#0.pipe.json (+ .png) where the router looks."""
    from pipes.routing import PIPES_INDEX, pipe_index_path

    source_file = (source_file or Path(path).as_posix()).replace("\\", "/")
    out_json = pipe_index_path(source_file, 0, pipes_dir or PIPES_INDEX)
    png = (
        out_json.with_name(out_json.name.removesuffix(".pipe.json") + ".png") if render and publish else None
    )
    doc = extract(path, source_file, png)
    if publish:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m pipes.extract.dxf_pipes",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("dxf")
    ap.add_argument(
        "--source-file", help="dataset-relative name, e.g. hypothetical/LVI-heating.dxf (default: the path)"
    )
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args(argv)
    doc = graph(translate_dxf(a.dxf, a.source_file, publish=not a.no_publish))
    s = doc["summary"]
    print(json.dumps({"nodes": s["node_counts"], "edges": len(doc["edges"]), "components": s["connected_components"],
                      "unsnapped": s["unsnapped_end_count"], "to_scale": doc["sheet"]["is_to_scale"],
                      "scale": doc["scale"]["source"], "length_by_system_and_size": s["length_m_by_system_and_size"]}, ensure_ascii=False))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
