"""Write a pipe JSON as a DXF (plan step 2.6): a controlled test input for the DXF route.

  python -m pipes.ingest.gt_to_dxf pipes/schema/example_standalone_heating_schematic.json out.dxf

One layer per edge layer (or per system), edges as LWPOLYLINE, symbols as BLOCK + INSERT (block named in Finnish for
the common types, so the glossary is exercised; a TAG attribute when the node has a tag), texts as TEXT, and with
label_edges a "LV-1" and a "DN25" TEXT beside every pipe. Structural nodes (tee, junction, open_end) are not drawn:
in a DXF they are just lines that meet. Pixels convert to mm through the frame's source_to_px when it has real units,
else at a fixed 0.1 mm per pixel.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import ezdxf
from ezdxf import units

STRUCTURAL = {"tee", "junction", "elbow", "cross", "open_end"}
BLOCK_NAMES = {
    "boiler": "KATTILA", "pump": "KIERTOPUMPPU", "ball_valve": "PALLOVENTTIILI",
    "balancing_valve": "TASAPAINOTUSVENTTIILI", "radiator": "PATTERI", "off_sheet_connector": "JATKUU",
    "gate_valve": "SULKUVENTTIILI", "check_valve": "TAKAISKUVENTTIILI", "expansion_vessel": "LAAJENNUSASTIA",
    "tank": "SAILIO", "meter": "MITTARI", "strainer": "SUODATIN",
}  # fmt: skip


def _to_dxf(frame: dict):
    """-> function (x_px, y_px) -> (x, y) in drawing units, and the INSUNITS code."""
    a, b, c, d, e, f = frame["source_to_px"]
    if frame["source_units"] == "px" or abs(a * e - b * d) < 1e-12:
        h = frame["height_px"]
        return (lambda x, y: (0.1 * x, 0.1 * (h - y))), units.MM
    det = a * e - b * d
    inv = lambda x, y: (  # noqa: E731
        (e * (x - c) - b * (y - f)) / det,
        (-d * (x - c) + a * (y - f)) / det,
    )
    code = {"mm": units.MM, "m": units.M, "inch": units.IN}.get(frame["source_units"], units.MM)
    return inv, code


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name)


def pipe_json_to_dxf(doc: dict, path: str | Path, label_edges: bool = True) -> Path:
    conv, code = _to_dxf(doc["frame"])
    scale_back = (
        1.0 / math.hypot(doc["frame"]["source_to_px"][0], doc["frame"]["source_to_px"][3])
        if code != units.MM or doc["frame"]["source_units"] != "px"
        else 10.0
    )
    out = ezdxf.new("R2018", setup=True)
    out.units = code
    msp = out.modelspace()
    edge_layer = lambda e: e.get("layer") or f"P-{(e.get('system') or 'UNKNOWN').upper()}"  # noqa: E731
    for name in {edge_layer(e) for e in doc["edges"]} | {"P-SYMBOLS", "P-TEXT"}:
        out.layers.add(name)
    text_h = 25.0 * scale_back / 2  # about 12 px of text height

    for e in doc["edges"]:
        pts = [conv(*p) for p in e["polyline_px"]]
        msp.add_lwpolyline(pts, dxfattribs={"layer": edge_layer(e)})
        if label_edges:
            mid = (
                pts[len(pts) // 2]
                if len(pts) % 2
                else tuple(
                    (a + b) / 2 for a, b in zip(pts[len(pts) // 2 - 1], pts[len(pts) // 2], strict=True)
                )
            )
            horizontal = abs(pts[-1][0] - pts[0][0]) >= abs(pts[-1][1] - pts[0][1])
            ox, oy = (0, 3 * text_h) if horizontal else (2 * text_h, 0)
            labels = [x for x in (e.get("line_tag"), (e["diameter"]["value"] or {}).get("raw_text")) if x]
            for k, label in enumerate(labels):
                msp.add_text(
                    label,
                    height=text_h,
                    dxfattribs={"layer": "P-TEXT", "insert": (mid[0] + ox, mid[1] + oy - k * 1.6 * text_h)},
                )

    made: dict[str, str] = {}
    for n in doc["nodes"]:
        if n["type"] in STRUCTURAL:
            continue
        name = BLOCK_NAMES.get(n["type"], _safe(n["type"]).upper())
        x0, y0 = conv(n["bbox_px"][0], n["bbox_px"][1])
        x1, y1 = conv(n["bbox_px"][2], n["bbox_px"][3])
        w, h = abs(x1 - x0), abs(y1 - y0)
        if name not in made:
            blk = out.blocks.new(name)
            blk.add_lwpolyline(
                [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)], close=True
            )
            if n["type"] == "pump":
                blk.add_circle((0, 0), min(w, h) / 2.5)
            blk.add_attdef("TAG", (0, -h / 2 - text_h * 1.3), height=text_h * 0.9)
            made[name] = name
        cx, cy = conv(*n["center_px"])
        ref = msp.add_blockref(name, (cx, cy), dxfattribs={"layer": "P-SYMBOLS"})
        if n.get("tag"):
            ref.add_auto_attribs({"TAG": n["tag"]})
        if n["type"] == "off_sheet_connector":
            msp.add_text(
                "JATKUU VVS-102 A", height=text_h, dxfattribs={"layer": "P-TEXT", "insert": (cx + w, cy)}
            )

    for t in doc["texts"]:
        x0, y1 = conv(t["bbox_px"][0], t["bbox_px"][3])  # lower-left corner of the text box
        _, ytop = conv(t["bbox_px"][0], t["bbox_px"][1])
        h = abs(ytop - y1) * 0.8
        msp.add_text(
            t["raw_text"],
            height=h,
            dxfattribs={"layer": "P-TEXT", "insert": (x0, y1), "rotation": t.get("rotation_deg", 0)},
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.saveas(path)
    return path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m pipes.ingest.gt_to_dxf", description=__doc__)
    ap.add_argument("pipe_json")
    ap.add_argument("out_dxf")
    ap.add_argument(
        "--no-labels", action="store_true", help="do not write the line tag / size text beside each pipe"
    )
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.pipe_json).read_text(encoding="utf-8"))
    print(pipe_json_to_dxf(doc, a.out_dxf, not a.no_labels))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
