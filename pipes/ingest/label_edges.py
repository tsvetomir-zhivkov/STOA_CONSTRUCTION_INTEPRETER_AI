"""Hand-label pipe connections between symbols on a sheet (for the graph metrics).

  python -m pipes.ingest.label_edges html  pipes/data_pipes/dev/test/<id>
      writes label.html next to the sheet. Open it in a browser: click symbol A, click empty spots for
      bends, click symbol B = one connection. 'Undo' removes the last step; 'Download' saves connections.json.
      Work is kept in the browser (localStorage) until cleared.
  python -m pipes.ingest.label_edges apply pipes/data_pipes/dev/test/<id> connections.json

connections.json: [{"from": 3, "to": 7, "waypoints": [[x, y], ...]}], node numbers (node_3 = 3), waypoints in
ORIGINAL page pixels. `apply` writes gt.edges.json (schema edges, extractor ext_human) and rebuilds the
sheet's gt.pipe.json with them through finalize(); pipes coverage becomes 'analysed'.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipes.ingest.pid_dataset import DS, gt_document
from pipes.write import finalize

TEMPLATE = Path(__file__).with_name("label_edges.html")


def make_html(sheet_dir: Path) -> Path:
    boxes = [
        [float(v) for v in ln.split()[1:]]
        for ln in (sheet_dir / "yolo.txt").read_text().splitlines()
        if ln.strip()
    ]
    html = (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("__ID__", sheet_dir.name)
        .replace("__BOXES__", json.dumps(boxes))
    )
    out = sheet_dir / "label.html"
    out.write_text(html, encoding="utf-8")
    return out


def _edge_doc(i: int, a: dict, b: dict, wp: list, conf: float = 1.0) -> dict:
    pts = [a["center_px"], *[[float(x), float(y)] for x, y in wp], b["center_px"]]
    prov = {"extractor_id": "ext_human", "confidence": conf, "source_ref": None}
    return {
        "id": f"pipe_{i}",
        "ends": [
            {
                "node_id": a["id"],
                "point_px": pts[0],
                "port_index": None,
                "snap": "snapped",
                "snap_distance_px": 0,
            },
            {
                "node_id": b["id"],
                "point_px": pts[-1],
                "port_index": None,
                "snap": "snapped",
                "snap_distance_px": 0,
            },
        ],
        "polyline_px": pts,
        "line_tag": None,
        "line_tag_text_id": None,
        "system": "unknown",
        "system_raw": None,
        "diameter": {"value": None, "source": "unknown", "text_id": None, "confidence": 0.0},
        "material": None,
        "insulation": None,
        "line_style": "solid",
        "layer": None,
        "flow": {"direction": "unknown", "evidence_ids": [], "confidence": 0.0},
        "length": {
            "geometry_m": None,
            "written_m": None,
            "written_text_id": None,
            "difference_pct": None,
            "flag": "not_to_scale",
        },
        "elevation_m": None,
        "provenance": prov,
    }


def apply(sheet_dir: Path, connections: Path) -> dict:
    """connections.json -> gt.edges.json + gt.pipe.json (finalize-validated), in the dev dir and in ds/."""
    conns = json.loads(connections.read_text(encoding="utf-8"))
    doc = json.loads((sheet_dir / "gt.pipe.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in doc["nodes"]}
    edges = []
    for i, c in enumerate(conns, 1):
        a, b = nodes.get(f"node_{c['from']}"), nodes.get(f"node_{c['to']}")
        if a is None or b is None or a is b:
            raise ValueError(f"connection {i}: bad node numbers {c['from']}->{c['to']}")
        edges.append(_edge_doc(i, a, b, c.get("waypoints", [])))
    w, h = doc["frame"]["width_px"], doc["frame"]["height_px"]
    split = sheet_dir.parent.name
    syms = [{"cls_raw": n["attributes"]["cls_raw"], "bbox_px": n["bbox_px"]} for n in doc["nodes"]]
    new = finalize(gt_document(sheet_dir.name, split, w, h, syms, edges))
    for d in (sheet_dir, DS / split / sheet_dir.name):
        if d.is_dir():
            (d / "gt.edges.json").write_text(json.dumps(edges, indent=1), encoding="utf-8")
            (d / "gt.pipe.json").write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    return new["summary"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipes.ingest.label_edges", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("html")
    h.add_argument("sheet_dir")
    s = sub.add_parser("apply")
    s.add_argument("sheet_dir")
    s.add_argument("connections")
    a = ap.parse_args(argv)
    if a.cmd == "html":
        print(make_html(Path(a.sheet_dir)))
    else:
        print(json.dumps(apply(Path(a.sheet_dir), Path(a.connections)).get("connected_components")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
