"""Demo overlay: draw any tool result's highlights on the sheet image (semi-transparent, labelled with the ids).

  python -m pipes.demo <pipe.json> count_symbols --region 1300 200 2200 1400 -o out.png
  python -m pipes.demo <pipe.json> path_between node_3 node_9 -o out.png [--image sheet.jpg]
  python -m pipes.demo --result result.json --image sheet.jpg -o out.png     # a saved tool result

Highlights are in ORIGINAL page pixels, so the sheet image is never resized; a caption band is added ABOVE the
page and everything is drawn shifted by the band height. Nodes: filled boxes with the node id; pipes: thick
translucent polylines with the edge id at their midpoint; regions: blue outline; the route of path_between is
numbered along its length. Caveats are printed in the band, and an unreliable answer is drawn in amber.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipes import tools

HERE = Path(__file__).parent
NODE = (0, 150, 80)
EDGE = (230, 90, 0)
REGION = (30, 90, 220)
AMBER = (200, 140, 0)


def sheet_image(doc: dict) -> Path | None:
    """data_pipes/ds/<split>/<id>/sheet.jpg for ids like 'pid_dataset/test/157#0'."""
    parts = doc["sheet"]["id"].split("#")[0].split("/")
    if len(parts) == 3:
        p = HERE / "data_pipes" / "ds" / parts[1] / parts[2] / "sheet.jpg"
        return p if p.exists() else None
    return None


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _label(d: ImageDraw.ImageDraw, xy, text: str, font, fill, size: int) -> None:
    x, y = xy
    w = d.textlength(text, font=font)
    d.rectangle([x, y - size - 2, x + w + 6, y + 2], fill=(*fill, 235))
    d.text((x + 3, y - size - 1), text, font=font, fill=(255, 255, 255, 255))


def _midpoint(poly) -> tuple[float, float]:
    total = sum(math.dist(poly[i], poly[i + 1]) for i in range(len(poly) - 1))
    acc = 0.0
    for a, b in zip(poly, poly[1:], strict=False):
        seg = math.dist(a, b)
        if acc + seg >= total / 2 and seg > 0:
            t = (total / 2 - acc) / seg
            return a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        acc += seg
    return tuple(poly[0])


def _summary(result: dict) -> str:
    a = result["answer"]
    if "error" in a:
        return f"error: {a['error']}"
    t = result["tool"]
    if t == "count_symbols":
        extra = "".join(f"  ({k} {a[k]})" for k in ("type_filter", "tag_filter") if a.get(k))
        return f"count = {a['count']}{extra}"
    if t == "connected_to":
        return f"{a['node_id']} is connected to {a['n_connected']} symbol(s): {', '.join(a['connected']) or 'none'}"
    if t == "path_between":
        if not a["connected"]:
            return f"no route between {a['a']} and {a['b']} in the traced graph"
        return f"route {a['a']} -> {a['b']}: {len(a['path_edge_ids'])} pipe(s), {a['length_px']} px"
    if t == "pipe_length":
        m = "n/a" if a["length_m"] is None else f"{a['length_m']} m"
        what = f"tag {a['tag']}" if a.get("tag") else f"edge {a['edge']}"
        return f"{what}: {a['n_edges']} pipe(s), length {m}, {a['length_px']} px" + (
            "  (lower bound)" if a["lower_bound"] else ""
        )
    return ""


def render(result: dict, image: Path, out: Path) -> Path:
    base = Image.open(image).convert("RGBA")
    W, H = base.size
    unit = (
        max(W, H) / 1200
    )  # one "screen pixel" at a 1200 px page; keeps strokes and text readable on big sheets
    fs = max(12, round(11 * unit))
    font, cap_size = _font(fs), max(14, round(13 * unit))
    cap_font = _font(cap_size)
    lines = [
        f"{result['tool']}  |  sheet {result['sheet']}  |  "
        + ("reliable" if result["reliable"] else "NOT RELIABLE"),
        _summary(result),
        *[f"! {c}" for c in (result.get("caveats") or [])[:3]],
    ]
    lh = round(cap_size * 1.4)
    band = lh * len(lines) + 12
    canvas = Image.new("RGBA", (W, H + band), (255, 255, 255, 255))
    canvas.paste(base, (0, band))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    accent = NODE if result["reliable"] else AMBER
    hl = result["highlights"]

    def sh(p):
        return (p[0], p[1] + band)

    line_w, box_w = max(4, round(7 * unit)), max(2, round(3 * unit))
    for r in hl.get("regions", []):
        d.rectangle([sh(r[:2]), sh(r[2:])], fill=(*REGION, 28), outline=(*REGION, 230), width=box_w)
    route = result["answer"].get("path_edge_ids") if isinstance(result["answer"], dict) else None
    for e in hl["edges"]:
        d.line([sh(p) for p in e["polyline_px"]], fill=(*EDGE, 150), width=line_w, joint="curve")
    for n in hl["nodes"]:
        x0, y0, x1, y1 = n["bbox_px"]
        d.rectangle([sh((x0, y0)), sh((x1, y1))], fill=(*accent, 70), outline=(*accent, 235), width=box_w)
    for e in hl["edges"]:  # labels last so they sit on top of the fills
        label = e["id"]
        if route and e["id"] in route:
            label = f"{route.index(e['id']) + 1}: {e['id']}"
        _label(d, sh(_midpoint(e["polyline_px"])), label, font, EDGE, fs)
    for n in hl["nodes"]:
        _label(d, sh(n["bbox_px"][:2]), n["id"], font, accent, fs)

    img = Image.alpha_composite(canvas, layer)
    cd = ImageDraw.Draw(img)
    cd.rectangle([0, 0, W, band], fill=(250, 250, 250, 255))
    cd.rectangle([0, 0, 8, band], fill=(*accent, 255))
    for i, ln in enumerate(lines):
        cd.text(
            (16, 6 + i * lh),
            ln[:230],
            font=cap_font,
            fill=(90, 60, 0) if ln.startswith("!") else (20, 20, 20),
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out)
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pre = argparse.ArgumentParser(add_help=False)  # these three may appear anywhere, also after the tool name
    pre.add_argument("--result", help="a saved tool result (JSON) instead of running a tool")
    pre.add_argument("--image", help="sheet image (default: found from the sheet id)")
    pre.add_argument("-o", "--out", required=True)
    opts, rest = pre.parse_known_args(argv)
    ap = argparse.ArgumentParser(
        prog="python -m pipes.demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pipe_json", nargs="?")
    tools.add_tool_args(ap.add_subparsers(dest="tool"))
    a = ap.parse_args(rest)
    a.result, a.image, a.out = opts.result, opts.image, opts.out
    if a.result:
        result, image = json.loads(Path(a.result).read_text(encoding="utf-8")), a.image
    else:
        if not a.pipe_json or not a.tool:
            ap.error("give <pipe.json> and a tool, or --result")
        idx = tools.PipeIndex.load(a.pipe_json)
        result = tools.call(idx, a)
        image = a.image or sheet_image(idx.doc)
    if not image:
        ap.error("no sheet image found: pass --image")
    print(render(result, Path(image), Path(a.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
