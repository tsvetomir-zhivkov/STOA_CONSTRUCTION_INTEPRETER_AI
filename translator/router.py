"""Step 2: pick the best source file for a drawing, and the extractor route.

Order of preference (first hit wins):
  1. ifc         sibling or parent IFC (<model>.ifc for <model>_<level>.pdf, or same stem)
  2. dxf         sibling DXF with the same stem
  3. svg         same-stem SVG (or the input itself) with real shapes and <text>
  4. vector_pdf  PDF page with words, > 50 drawings, and no image over 50% of the page
  5. raster      PNG/JPG, or a scanned PDF (rendered at 300 dpi)
  otherwise      unknown (with a reason)

No model or VLM is called here. Usage:
  python -m translator.router <path> [--json] [--data DATASET_ROOT]
  python -m translator.router --all         # every file in questions.jsonl, table by source
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from translator.config import data_root, questions_path

RASTER_EXTS = {".png", ".jpg", ".jpeg"}
PDF_DPI = 150  # fixed render dpi for vector PDF pages (highlight pixel space)
SCAN_DPI = 300  # render dpi for scanned PDFs

# Vector PDF rule (Molio rule from the plan)
PDF_MIN_DRAWINGS = 50
MAX_IMAGE_COVER = 0.5
# SVG rule: enough real shapes and at least one non-empty text
SVG_MIN_SHAPES = 10
SVG_SHAPES = {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse"}

DARK_MEDIAN = 100  # median grey below this -> dark background (step 5 inverts it)
COLOUR_SPREAD = 12  # mean channel spread above this -> colour image

HISTORICAL_SOURCES = {"europeana", "finna", "habs"}
MODERN_PLAN_SOURCES = {"cubicasa5k"}

# Expected route per source on the preview set ("Done when" in the plan; ifc_bench from the source table).
EXPECTED = {
    "cubicasa5k": "raster",
    "swiss_dwellings": "dxf",
    "ifc_models": "ifc",
    "ifc_bench": "ifc",
    "opening_design": "vector_pdf",
}


# ------------------------------------------------------------------ paths


def _resolve(path: str | Path, root: Path) -> Path:
    p = Path(path)
    if p.exists() or p.is_absolute():
        return p
    q = root / p
    return q if q.exists() else p


def _rel(p: Path, root: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _source(p: Path, root: Path) -> str:
    parts = Path(_rel(p, root)).parts
    if len(parts) >= 3 and parts[0] == "data":
        return parts[1]
    return p.parent.name


def _sibling(p: Path, ext: str) -> Path | None:
    """Same-stem file with another extension, case-insensitive on the extension."""
    for cand in (p.with_suffix(ext), p.with_suffix(ext.upper())):
        if cand.exists() and cand != p:
            return cand
    return None


# ------------------------------------------------------------------ IFC


def find_ifc(p: Path) -> Path | None:
    """Same-stem .ifc, else strip trailing '_...' sections until <prefix>.ifc exists (same folder)."""
    if p.suffix.lower() == ".ifc":
        return p
    stem = p.stem
    while True:
        cand = p.with_name(stem + ".ifc")
        if cand.exists():
            return cand
        if "_" not in stem:
            return None
        stem = stem.rsplit("_", 1)[0]


def decode_step_string(s: str) -> str:
    r"""Decode IFC/STEP string escapes: '' \X\hh \X2\hhhh..\X0\ \X4\hhhhhhhh..\X0\ \S\c."""
    s = s.replace("''", "'")
    s = re.sub(
        r"\\X2\\((?:[0-9A-Fa-f]{4})+)\\X0\\",
        lambda m: bytes.fromhex(m.group(1)).decode("utf-16-be"),
        s,
    )
    s = re.sub(
        r"\\X4\\((?:[0-9A-Fa-f]{8})+)\\X0\\",
        lambda m: bytes.fromhex(m.group(1)).decode("utf-32-be"),
        s,
    )
    s = re.sub(r"\\X\\([0-9A-Fa-f]{2})", lambda m: bytes.fromhex(m.group(1)).decode("latin-1"), s)
    s = re.sub(r"\\S\\(.)", lambda m: chr(ord(m.group(1)) + 128), s)
    return s


_STOREY = re.compile(r"IFCBUILDINGSTOREY\s*\(\s*'([^']*)'\s*,[^,]*,\s*'((?:[^']|'')*)'", re.IGNORECASE)


def ifc_storeys(ifc: Path) -> list[dict]:
    """[{global_id, name}] for every IfcBuildingStorey, read from the STEP text (no ifcopenshell)."""
    out = []
    with ifc.open(encoding="latin-1", errors="replace") as f:
        for line in f:
            if "BUILDINGSTOREY" not in line.upper():
                continue
            m = _STOREY.search(line)
            if m:
                out.append({"global_id": m.group(1), "name": decode_step_string(m.group(2))})
    return out


def slug(s: str) -> str:
    """ASCII slug as used in the rendered file names: Kælder -> klder, 4.korrus -> 4-korrus."""
    s = s.casefold().encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def level_from_slug(p: Path, ifc: Path, storeys: list[dict]) -> dict | None:
    """Match the part of the file stem after the model stem against storey-name slugs."""
    rest = p.stem[len(ifc.stem) :].strip("_").casefold()
    if not rest:
        return None
    hits = [
        s
        for s in storeys
        if slug(s["name"]) and (rest == slug(s["name"]) or rest.endswith("-" + slug(s["name"])))
    ]
    if not hits:
        return None
    return max(hits, key=lambda s: len(slug(s["name"])))


_TITLE = re.compile(r"—\s*[^:—]+:\s*(.+?)\s*$")


def level_from_title(p: Path) -> str | None:
    """Rendered plans carry a title like 'Molio Demo-hus — Plantegning: Kælder'."""
    texts: list[str] = []
    try:
        if p.suffix.lower() == ".pdf":
            import pymupdf

            with pymupdf.open(p) as doc:
                texts = doc[0].get_text().splitlines()
        elif p.suffix.lower() == ".svg":
            for _, el in ET.iterparse(p, events=("end",)):
                if el.tag.rsplit("}", 1)[-1] == "text":
                    texts.append("".join(el.itertext()))
    except Exception:
        return None
    for t in texts:
        m = _TITLE.search(t)
        if m:
            return m.group(1)
    return None


def _level_info(p: Path, ifc: Path) -> dict:
    if p == ifc:
        return {"level": None, "level_source": "whole_model"}
    storeys = ifc_storeys(ifc)
    hit = level_from_slug(p, ifc, storeys)
    if hit:
        return {"level": hit["name"], "level_global_id": hit["global_id"], "level_source": "ifc_storey_slug"}
    title = level_from_title(p)
    if title:
        match = next((s for s in storeys if s["name"].casefold() == title.casefold()), None)
        info = {"level": title, "level_source": "page_title"}
        if match:
            info["level_global_id"] = match["global_id"]
        return info
    return {"level": None, "level_source": "not_found"}


# ------------------------------------------------------------------ SVG


def _num(v: str | None) -> float | None:
    if not v:
        return None
    m = re.match(r"\s*([-+]?[0-9]*\.?[0-9]+)", v)
    return float(m.group(1)) if m else None


def check_svg(p: Path) -> tuple[bool, str, dict]:
    shapes = texts = 0
    root_area = None
    max_img = 0.0
    try:
        for event, el in ET.iterparse(p, events=("start", "end")):
            tag = el.tag.rsplit("}", 1)[-1]
            if event == "start":
                if tag == "svg" and root_area is None:
                    vb = (el.get("viewBox") or "").replace(",", " ").split()
                    if len(vb) == 4:
                        root_area = float(vb[2]) * float(vb[3])
                    else:
                        w, h = _num(el.get("width")), _num(el.get("height"))
                        root_area = w * h if w and h else None
                continue
            if tag in SVG_SHAPES:
                shapes += 1
            elif tag == "text" and "".join(el.itertext()).strip():
                texts += 1
            elif tag == "image":
                w, h = _num(el.get("width")), _num(el.get("height"))
                if root_area and w and h:
                    max_img = max(max_img, w * h / root_area)
                else:
                    max_img = max(max_img, 1.0)  # unknown size: assume it covers the page
    except (ET.ParseError, OSError, ValueError) as e:
        return False, f"svg unreadable: {e}", {}
    stats = {"shapes": shapes, "texts": texts, "max_image_cover": round(max_img, 3)}
    ok = shapes >= SVG_MIN_SHAPES and texts >= 1 and max_img <= MAX_IMAGE_COVER
    why = f"svg {shapes} shapes, {texts} texts, max image cover {max_img:.2f}"
    return ok, why, stats


# ------------------------------------------------------------------ PDF


def pdf_pages(p: Path) -> list[dict]:
    """Per page: words, drawings, max image cover, and whether it passes the vector rule."""
    import pymupdf

    pages = []
    with pymupdf.open(p) as doc:
        for i, page in enumerate(doc):
            area = page.rect.width * page.rect.height or 1.0
            cover = 0.0
            for info in page.get_image_info():
                r = pymupdf.Rect(info["bbox"]) & page.rect
                if not r.is_empty:
                    cover = max(cover, r.width * r.height / area)
            words = len(page.get_text("words"))
            drawings = len(page.get_drawings())
            pages.append(
                {
                    "index": i,
                    "words": words,
                    "drawings": drawings,
                    "max_image_cover": round(cover, 3),
                    "width_pt": round(page.rect.width, 2),
                    "height_pt": round(page.rect.height, 2),
                    "vector": words > 0 and drawings > PDF_MIN_DRAWINGS and cover <= MAX_IMAGE_COVER,
                }
            )
    return pages


# ------------------------------------------------------------------ raster


def raster_kind(source: str) -> str:
    if source in MODERN_PLAN_SOURCES:
        return "modern_floorplan"
    if source in HISTORICAL_SOURCES:
        return "historical_scan"
    return "unknown"


def _image_stats(img) -> dict:
    import numpy as np

    small = img.convert("RGB")
    small.thumbnail((512, 512))
    a = np.asarray(small, dtype=np.int16)
    spread = float((a.max(axis=2) - a.min(axis=2)).mean())
    grey = np.asarray(small.convert("L"))
    median = float(np.median(grey))
    return {
        "is_colour": spread > COLOUR_SPREAD,
        "dark_background": median < DARK_MEDIAN,
        "median_grey": round(median, 1),
        "channel_spread": round(spread, 2),
    }


def raster_info(p: Path) -> dict:
    from PIL import Image

    with Image.open(p) as img:
        info = {"width_px": img.width, "height_px": img.height, "mode": img.mode}
        info.update(_image_stats(img))
    return info


def scanned_pdf_info(p: Path) -> dict:
    import pymupdf
    from PIL import Image

    with pymupdf.open(p) as doc:
        page = doc[0]
        pix = page.get_pixmap(dpi=72)
        img = Image.frombytes("RGB" if pix.n < 4 else "RGBA", (pix.width, pix.height), pix.samples)
        scale = SCAN_DPI / 72
        info = {
            "width_px": round(page.rect.width * scale),
            "height_px": round(page.rect.height * scale),
            "render_dpi": SCAN_DPI,
        }
    info.update(_image_stats(img))
    return info


# ------------------------------------------------------------------ route


def route(path: str | Path, data: str | Path | None = None) -> dict:
    """Pick the best representation for one input file. Never raises on bad input: route=unknown."""
    root = data_root(data)
    p = _resolve(path, root)
    source = _source(p, root)
    out: dict = {"file": _rel(p, root), "source": source, "checks": []}
    checks = out["checks"]

    def done(route_: str, read_from: Path | None, reason: str, **extra) -> dict:
        out.update(route=route_, read_from=_rel(read_from, root) if read_from else None, reason=reason)
        out.update(extra)
        return out

    if not p.exists():
        return done("unknown", None, "file not found")
    ext = p.suffix.lower()

    # 1. IFC
    ifc = find_ifc(p)
    if ifc:
        checks.append(f"ifc: {ifc.name}")
        why = "input is IFC" if ifc == p else f"IFC model {ifc.name} found for this file"
        return done("ifc", ifc, why, **_level_info(p, ifc))
    checks.append("ifc: none")

    # 2. DXF
    dxf = p if ext == ".dxf" else _sibling(p, ".dxf")
    if dxf:
        checks.append(f"dxf: {dxf.name}")
        return done("dxf", dxf, "input is DXF" if dxf == p else f"same-stem DXF {dxf.name}")
    checks.append("dxf: none")

    # 3. SVG
    svg = p if ext == ".svg" else _sibling(p, ".svg")
    if svg:
        ok, why, stats = check_svg(svg)
        checks.append(f"svg: {svg.name}: {why} -> {'ok' if ok else 'rejected'}")
        if ok:
            return done("svg", svg, why, svg=stats)
    else:
        checks.append("svg: none")

    # 4. vector PDF / 5. scanned PDF
    if ext == ".pdf":
        try:
            pages = pdf_pages(p)
        except Exception as e:
            return done("unknown", p, f"pdf unreadable: {e}")
        n_vec = sum(pg["vector"] for pg in pages)
        checks.append(f"pdf: {n_vec}/{len(pages)} vector pages")
        if n_vec:
            return done("vector_pdf", p, f"{n_vec}/{len(pages)} pages pass the vector rule", pages=pages)
        try:
            info = scanned_pdf_info(p)
        except Exception as e:
            return done("unknown", p, f"scanned pdf, render failed: {e}", pages=pages)
        return done(
            "raster",
            p,
            "pdf without vector pages: scanned, render at 300 dpi",
            pages=pages,
            raster_kind=raster_kind(source),
            raster=info,
        )

    # 5. raster image
    if ext in RASTER_EXTS:
        try:
            info = raster_info(p)
        except Exception as e:
            return done("unknown", p, f"image unreadable: {e}")
        return done("raster", p, f"{ext[1:]} image", raster_kind=raster_kind(source), raster=info)

    if ext == ".svg":
        return done("unknown", p, "svg without enough vector content and no better sibling")
    return done("unknown", p, f"no reader for '{ext or p.name}' and no usable sibling")


def summary(r: dict) -> str:
    """One line, e.g. "ifc (bsc-molio_molio-with-uris.ifc), level 'Kælder'"."""
    name = Path(r["read_from"]).name if r.get("read_from") else "-"
    if r["route"] == "unknown":
        return f"unknown: {r['reason']}"
    line = f"{r['route']} ({name})"
    if r["route"] == "ifc" and r.get("level"):
        line += f", level '{r['level']}'"
    elif r["route"] == "ifc":
        line += ", whole model" if r.get("level_source") == "whole_model" else ", level not found"
    elif r["route"] == "raster":
        line += f", kind {r['raster_kind']}"
    elif r["route"] == "vector_pdf":
        line += f", {sum(p['vector'] for p in r['pages'])}/{len(r['pages'])} vector pages"
    return line


# ------------------------------------------------------------------ CLI


def route_all(data: str | None = None) -> list[dict]:
    root = data_root(data)
    with questions_path(root).open(encoding="utf-8") as f:
        files = sorted({json.loads(line)["path"] for line in f if line.strip()})
    return [route(p, root) for p in files]


def report_all(results: list[dict]) -> str:
    import pandas as pd

    df = pd.DataFrame(results)
    table = pd.crosstab(df.source, df.route, margins=True, margins_name="total")
    lines = [f"{len(df)} files\n", table.to_string(), "", "ifc_models files:"]
    for r in results:
        if r["source"] == "ifc_models":
            lines.append(f"  {r['file']}\n    -> {summary(r)}")
    mism = [r for r in results if EXPECTED.get(r["source"]) not in (None, r["route"])]
    lines += ["", f"Route differs from expected: {len(mism)}"]
    for r in mism:
        lines.append(f"  {r['file']}\n    expected {EXPECTED[r['source']]}, got {summary(r)}")
        lines.append(f"    reason: {r['reason']}; checks: {' | '.join(r['checks'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m translator.router",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("path", nargs="?", help="drawing file (absolute, or relative to the dataset root)")
    ap.add_argument("--data", help="dataset root (default: $STOA_DATA)")
    ap.add_argument("--json", action="store_true", help="print the full route dict")
    ap.add_argument("--all", action="store_true", help="route every file in questions.jsonl")
    args = ap.parse_args(argv)
    if args.all:
        results = route_all(args.data)
        print(json.dumps(results, ensure_ascii=False, indent=1) if args.json else report_all(results))
        return 0
    if not args.path:
        ap.error("give a path or --all")
    r = route(args.path, args.data)
    print(json.dumps(r, ensure_ascii=False, indent=1) if args.json else summary(r))
    return 0 if r["route"] != "unknown" else 1


if __name__ == "__main__":
    raise SystemExit(main())
