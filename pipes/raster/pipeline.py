"""Scan route, skeleton tracer variant (plan step 8): prep -> erase symbols + text -> skeleton -> graph -> JSON.

  python -m pipes.raster.pipeline pipes/data_pipes/dev/test --boxes gt            # oracle symbol boxes
  python -m pipes.raster.pipeline pipes/data_pipes/dev/test --boxes det --conf 0.5 --conf-erase 0.25

Symbol boxes: gt = the sheet's gt.pipe.json nodes, det = raw detections in index_pipes/_raw/dev_<split>/ merged
at --conf. OCR text comes from the raw OCR cache (ocr_run). Writes index_pipes/<split>_trace_<boxes>/<id>.pipe.json
(through finalize) and an overlay PNG in index_pipes/overlays/. Everything is in ORIGINAL page pixels.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from pipes import graph as gr
from pipes.ingest.pid_dataset import gt_document
from pipes.raster import equipment as eq
from pipes.raster import erase as er
from pipes.raster import ocr_run
from pipes.raster import regions as rg
from pipes.raster.detect import merge
from pipes.raster.trace_skeleton import trace
from pipes.write import finalize

HERE = Path(__file__).parent.parent
MIN_TEXT_SCORE = 0.3
DOT_RADIUS_W = 2.5
DOT_SHARE = 0.8


def _bbox(poly) -> list[float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def _clip(b, W, H):
    return [round(max(b[0], 0), 1), round(max(b[1], 0), 1), round(min(b[2], W), 1), round(min(b[3], H), 1)]


def symbol_boxes(sheet_dir: Path, mode: str, conf: float, run: str = "dev") -> list[dict]:
    split, sid = sheet_dir.parent.name, sheet_dir.name
    if mode == "gt":
        doc = json.loads((sheet_dir / "gt.pipe.json").read_text(encoding="utf-8"))
        return [{"bbox_px": n["bbox_px"], "score": 1.0} for n in doc["nodes"]]
    raw_dir = f"ds_{split}_{run}"
    raw = json.loads((HERE / "index_pipes/_raw" / raw_dir / f"{sid}.det.json").read_text(encoding="utf-8"))
    return [
        {"bbox_px": [round(v, 2) for v in d["bbox_px"]], "score": d["score"]}
        for d in merge(raw["dets"], conf)
    ]


def run_sheet(
    sheet_dir: Path,
    mode: str = "gt",
    conf: float = 0.5,
    run: str = "dev",
    conf_erase: float = 0.25,
    equipment: bool = True,
) -> dict:
    """conf = reported symbols (nodes); conf_erase = boxes erased before tracing (>= recall, not reported)."""
    split, sid = sheet_dir.parent.name, sheet_dir.name
    sheet = sheet_dir / "sheet.jpg"
    p = er.prep(sheet)
    H, W = p.shape
    w = p.w
    syms_all = symbol_boxes(sheet_dir, mode, min(conf_erase, conf), run)
    for s in syms_all:
        s["reported"] = mode == "gt" or s["score"] >= conf
    boxes_all = [s["bbox_px"] for s in syms_all]
    texts = [t for t in ocr_run.load(split, sid) if t["score"] >= MIN_TEXT_SCORE]
    tmask = er.text_mask((H, W), texts)

    # step 5: frames, title block and legend rectangles are removed before tracing
    frames = rg.find_frames(p.ink, w, tmask)
    regions = rg.classify(p.ink, w, frames, boxes_all, texts, tmask)
    rmask = rg.mask((H, W), regions, w) if regions else None
    # stroke width from the pipes alone: the page-wide median is inflated by text and symbol outlines
    pipes_only = p.ink & ~tmask & ~er.box_mask((H, W), boxes_all)
    if rmask is not None:
        pipes_only &= ~rmask
    if pipes_only.sum() > 200:
        w = er.stroke_width(pipes_only)
    # large closed contours that pipes attach to: equipment nodes, not traced outlines
    found = []
    if equipment:
        found = eq.find_equipment(er.erase(p.ink, w, boxes_all, tmask, rmask), w, boxes_all, rmask)
    boxes_erase = boxes_all + [f["bbox_px"] for f in found]
    syms = [s for s in syms_all if s["reported"]] + [{**f, "reported": True} for f in found]
    port_list = er.ports(p.ink & ~tmask, w, [s["bbox_px"] for s in syms])
    clean = er.erase(p.ink, w, boxes_erase, tmask, rmask)

    # junction dots: dark share in a disk on the UN-erased ink
    yy, xx = np.mgrid[0:H, 0:W] if H * W < 4e6 else (None, None)
    r_dot = DOT_RADIUS_W * w

    def has_dot(pos) -> bool:
        x, y = pos
        x0, x1, y0, y1 = (
            int(max(x - r_dot, 0)),
            int(min(x + r_dot + 1, W)),
            int(max(y - r_dot, 0)),
            int(min(y + r_dot + 1, H)),
        )
        sub = p.ink[y0:y1, x0:x1]
        gy, gx = np.mgrid[y0:y1, x0:x1]
        disk = (gx - x) ** 2 + (gy - y) ** 2 <= r_dot**2
        return bool(disk.any() and sub[disk].mean() > DOT_SHARE)

    nodes, edges = trace(clean, w)
    g = gr.from_trace(nodes, edges)
    gr.collapse_deg2(g)
    n_spurs = gr.prune_spurs(g, w)
    gr.contract_close(g, w)
    bstats = gr.resolve_branches(g, w, has_dot)
    n_stitch = gr.stitch(g, w, tmask)

    return assemble(sheet_dir, mode, conf, syms, port_list, texts, g, w, (W, H), p.transforms,
                    {"spurs_pruned": n_spurs, "stitched": n_stitch, "regions": len(regions), "equipment": len(found),
                     "low_conf_erased": sum(not s["reported"] for s in syms_all), **bstats},
                    regions)  # fmt: skip


def assemble(sheet_dir, mode, conf, syms, port_list, texts, g, w, size, transforms, stats, regions) -> dict:
    split, sid = sheet_dir.parent.name, sheet_dir.name
    W, H = size
    r = max(8.0, 3 * w)
    doc = gt_document(sid, split, W, H, [{"cls_raw": "symbol", "bbox_px": s["bbox_px"]} for s in syms])
    sym_ext = "ext_human" if mode == "gt" else "ext_det"
    n_syms = len(syms)
    for n, s in zip(doc["nodes"], syms, strict=True):
        n["ports_px"] = port_list[int(n["id"].split("_")[1]) - 1]
        n["provenance"]["extractor_id"] = sym_ext
        n["provenance"]["confidence"] = round(float(s["score"]), 4)
        if s.get("ext"):  # derived by the tracer from a closed contour, not by the detector
            n["provenance"]["extractor_id"] = s["ext"]
            n["subtype"] = "equipment"
            n["attributes"] = {"cls_raw": "symbol", "origin": s["origin"]}
    nodes = doc["nodes"]
    nid = n_syms

    def add_struct(kind: str, pos) -> str:
        nonlocal nid
        nid += 1
        x, y = pos
        nodes.append(
            {**{k: None for k in ("tag", "tag_text_id", "label", "nominal_size")},
             "id": f"node_{nid}", "type": kind, "subtype": None,
             "bbox_px": _clip([x - w, y - w, x + w, y + w], W, H),
             "center_px": [round(x, 2), round(y, 2)], "rotation_deg": 0, "ports_px": [], "system": None,
             "attributes": {}, "in_room_id": None, "in_room_link_id": None,
             "provenance": {"extractor_id": "ext_graph", "confidence": 0.8, "source_ref": None}}
        )  # fmt: skip
        return f"node_{nid}"

    struct: dict[int, str] = {}
    for n, d in g.nodes.items():
        if d["kind"] in ("tee", "junction"):
            struct[n] = add_struct(d["kind"], d["pos"])

    def snap_end(pos):
        """-> (node_id or None, port_index, distance, kind) for a free end."""
        best = None
        for i, ports in enumerate(port_list):
            for pi, q in enumerate(ports):
                d = math.dist(pos, q)
                if d <= r and (best is None or d < best[2]):
                    best = (f"node_{i + 1}", pi, d)
        if best:
            return best
        for i, s in enumerate(syms):
            d = gr.rect_dist(pos, s["bbox_px"])
            if d <= r and (best is None or d < best[2]):
                best = (f"node_{i + 1}", None, d)
        if best:
            return best
        if min(pos[0], pos[1], W - pos[0], H - pos[1]) <= 3 * w:
            return (add_struct("open_end", pos), None, 0.0)
        return None

    out_edges, cov_unsnapped, fragments = [], [], 0
    free_cache: dict[int, tuple] = {}
    for e in g.edges:
        ends = []
        for key, pos in (("a", e["poly"][0]), ("b", e["poly"][-1])):
            n = e[key]
            if n in struct:
                ends.append(
                    {"node_id": struct[n], "port_index": None, "snap": "snapped", "snap_distance_px": 0}
                )
                continue
            if n not in free_cache:
                free_cache[n] = snap_end(pos)
            hit = free_cache[n]
            if hit is None:
                ends.append(
                    {"node_id": None, "port_index": None, "snap": "unsnapped", "snap_distance_px": None}
                )
            else:
                ends.append(
                    {
                        "node_id": hit[0],
                        "port_index": hit[1],
                        "snap": "snapped",
                        "snap_distance_px": round(hit[2], 2),
                    }
                )
        if len(e["poly"]) < 2 or (
            all(x["node_id"] is None for x in ends) and gr.plen(e["poly"]) < gr.FRAGMENT_W * w
        ):
            fragments += 1
            continue
        poly = [[round(x, 1), round(y, 1)] for x, y in e["poly"]]
        ok = all(x["node_id"] for x in ends)
        ed = gr.edge_doc(len(out_edges) + 1, ends, poly, "ext_graph", 0.8 if ok else 0.5)
        out_edges.append(ed)
        for k, x in enumerate(ends):
            if x["node_id"] is None:
                px, py = poly[0] if k == 0 else poly[-1]
                cov_unsnapped.append(
                    (ed["id"], _clip([px - 3 * r, py - 3 * r, px + 3 * r, py + 3 * r], W, H))
                )

    mean_e = float(np.mean([e["provenance"]["confidence"] for e in out_edges])) if out_edges else 0.0
    cov = doc["coverage"]
    cov[0].update(
        extractor_id=sym_ext,
        confidence=round(float(np.mean([s["score"] for s in syms])) if syms else 0.0, 4),
        note="oracle boxes (dataset annotation)"
        if mode == "gt"
        else f"detector, reported at conf >= {conf}; {stats['low_conf_erased']} lower-confidence boxes erased "
        f"for tracing, not reported; {stats['equipment']} equipment nodes from closed contours",
    )
    cov[1].update(
        status="analysed", confidence=round(mean_e, 4), extractor_id="ext_graph",
        note=f"skeleton tracer; {fragments} noise fragments dropped; legend/title frames are not removed yet",
    )  # fmt: skip
    cov.append({"id": "cov_3", "region": "page", "category": "connectivity", "status": "analysed",
                "confidence": 0.6, "extractor_id": "ext_graph", "element_ids": [],
                "note": "unsnapped ends listed separately as partial"})  # fmt: skip
    for k, (eid, box) in enumerate(cov_unsnapped, 4):
        cov.append({"id": f"cov_{k}", "region": box, "category": "connectivity", "status": "partial",
                    "confidence": 0.3, "extractor_id": "ext_graph", "element_ids": [eid],
                    "note": "pipe end that reaches no symbol, junction or border"})  # fmt: skip
    cov.append({"id": f"cov_{len(cov) + 1}", "region": "page", "category": "text", "status": "analysed",
                "confidence": round(float(np.mean([t["score"] for t in texts])) if texts else 0.0, 4),
                "extractor_id": "ext_ocr", "element_ids": [], "note": "PaddleOCR Latin model, two orientations"})  # fmt: skip

    doc["nodes"] = nodes
    doc["edges"] = out_edges
    doc["texts"] = []
    for i, t in enumerate(texts, 1):
        bb = _bbox(t["poly"])
        tall = (bb[3] - bb[1]) > 2 * (bb[2] - bb[0]) and len(t["text"]) > 2
        doc["texts"].append(
            {"id": f"text_{i}", "raw_text": t["text"], "bbox_px": _clip(bb, W, H),
             "rotation_deg": 90 if tall else 0, "role": "unknown", "value": None, "attached_to": [],
             "language": None, "provenance": {"extractor_id": "ext_ocr", "confidence": t["score"], "source_ref": None}}
        )  # fmt: skip
    doc["extractors"] = [
        {"id": sym_ext, "kind": "human" if mode == "gt" else "symbol_detector",
         "name": "dataset boxes" if mode == "gt" else "yolo11s single-class", "version": None, "run_at": None,
         "cost_usd": 0.0},
        {"id": "ext_ocr", "kind": "ocr", "name": "PaddleOCR latin_PP-OCRv3_mobile_rec", "version": None,
         "run_at": None, "cost_usd": 0.0},
        {"id": "ext_graph", "kind": "graph_builder", "name": "skeleton tracer + graph.py", "version": None,
         "run_at": None, "cost_usd": 0.0},
    ]  # fmt: skip
    doc["sheet"]["provenance"]["extractor_id"] = "ext_graph"
    doc["scale"]["provenance"]["extractor_id"] = "ext_graph"
    doc["sheet"]["regions"] = [
        {"kind": rgn["kind"], "bbox_px": _clip(rgn["bbox_px"], W, H),
         "provenance": {"extractor_id": "ext_graph", "confidence": 0.7, "source_ref": None}}
        for rgn in regions
    ]  # fmt: skip
    for rgn in regions:
        if rgn["erase"] == "full":
            doc["coverage"].append(
                {"id": f"cov_{len(doc['coverage']) + 1}", "region": _clip(rgn["bbox_px"], W, H),
                 "category": "pipes", "status": "not_analysed", "confidence": 0.0, "extractor_id": "ext_graph",
                 "element_ids": [], "note": f"{rgn['kind']} region is not traced"}
            )  # fmt: skip
    doc["history"] = []
    doc = finalize(doc)
    doc["_debug"] = {"stroke_width_px": round(w, 2), "snap_radius_px": r, **stats, "transforms": transforms}
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipes.raster.pipeline", description=__doc__)
    ap.add_argument("root", help="e.g. pipes/data_pipes/dev/test")
    ap.add_argument("--boxes", choices=["gt", "det"], default="gt")
    ap.add_argument("--conf", type=float, default=0.5, help="reported symbols")
    ap.add_argument("--conf-erase", type=float, default=0.25, help="boxes erased before tracing")
    ap.add_argument("--no-equipment", action="store_true", help="skip closed-contour equipment nodes")
    ap.add_argument("--tag", default="", help="suffix for the output folder")
    ap.add_argument("--run", default="dev", help="Modal training run whose detections to use (dev, full)")
    ap.add_argument("--overlay", action="store_true")
    a = ap.parse_args(argv)
    from pipes.eval.overlay import draw

    root = Path(a.root)
    suffix = "" if a.run == "dev" or a.boxes == "gt" else f"_{a.run}"
    out = HERE / "index_pipes" / f"{root.name}_trace_{a.boxes}{suffix}{a.tag}"
    out.mkdir(parents=True, exist_ok=True)
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        doc = run_sheet(d, a.boxes, a.conf, a.run, a.conf_erase, not a.no_equipment)
        dbg = doc.pop("_debug")
        (out / f"{d.name}.pipe.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        draw(d / "sheet.jpg", doc, HERE / "index_pipes" / "overlays" / out.name / f"{d.name}.png")
        s = doc["summary"]
        print(d.name, f"nodes={sum(s['node_counts'].values())} edges={len(doc['edges'])}",
              f"components={s.get('connected_components')} unsnapped={s.get('unsnapped_ends')}", dbg)  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
