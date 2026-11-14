"""Overlay PNGs for checking alignment by eye: symbol boxes, traced pipes by component, unsnapped ends in red."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from PIL import Image, ImageDraw

PALETTE = [(0, 114, 178), (230, 159, 0), (0, 158, 115), (204, 121, 167), (86, 180, 233), (213, 94, 0)]
STRUCT = {"tee": (0, 0, 255), "junction": (255, 0, 255), "open_end": (255, 140, 0)}


def draw(image: Path, doc: dict, out: Path) -> Path:
    im = Image.open(image).convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    for t in doc.get("texts", []):
        d.rectangle(t["bbox_px"], outline=(150, 150, 150, 160), width=1)
    g = nx.Graph()
    for e in doc["edges"]:
        a, b = (x["node_id"] or f"{e['id']}#{i}" for i, x in enumerate(e["ends"]))
        g.add_edge(a, b, id=e["id"])
    comp = {}
    for k, cc in enumerate(nx.connected_components(g)):
        for n in cc:
            comp[n] = k
    for e in doc["edges"]:
        a = e["ends"][0]["node_id"] or f"{e['id']}#0"
        col = PALETTE[comp.get(a, 0) % len(PALETTE)]
        d.line([tuple(p) for p in e["polyline_px"]], fill=(*col, 255), width=3)
        for end in e["ends"]:
            if end["node_id"] is None:
                x, y = end["point_px"]
                d.ellipse([x - 8, y - 8, x + 8, y + 8], outline=(255, 0, 0, 255), width=3)
    for n in doc["nodes"]:
        col = STRUCT.get(n["type"])
        if col:
            x, y = n["center_px"]
            d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(*col, 255))
        else:
            d.rectangle(n["bbox_px"], outline=(0, 170, 0, 255), width=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out
