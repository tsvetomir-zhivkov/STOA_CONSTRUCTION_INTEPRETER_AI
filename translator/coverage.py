"""Step 8: coverage map per category and region.

build(doc) rewrites the coverage records of the GRID_CATEGORIES (rooms, doors, windows, fixtures, text):
  - one 'page' record: the extractor's status, confidence = mean confidence of the active elements;
  - one record per cell of a grid x grid page grid (bbox in original pixels). A cell with elements is
    'analysed' when their mean confidence >= GOOD_CONF, else 'partial'; confidence = that mean. A cell with
    no elements inherits the page status and confidence (the extractor looked there and found nothing).
Elements without geometry (assumed priors) count on the page record only, never in a cell.
Records of other categories (floors, shower, walls, kitchens, bathrooms, ...) are kept as they are: floors
stays partial at 0.3 when storey titles and building blobs disagree (translator.extract.ocr.levels), shower
stays not_analysed for raster plans until the VLM check (step 9) runs.
"""

from __future__ import annotations

GRID_CATEGORIES = ("rooms", "doors", "windows", "fixtures", "text")
FIXTURES = {"toilet", "sink", "shower", "bathtub", "sauna_bench", "stair"}
GOOD_CONF = 0.5


def _active(items):
    return [x for x in items if x.get("status", "active") == "active"]


def elements(doc: dict, category: str) -> list[dict]:
    if category == "rooms":
        return _active(doc["rooms"])
    if category in ("doors", "windows"):
        kind = category[:-1]
        return [o for o in _active(doc["openings"]) if o["kind"] == kind]
    if category == "fixtures":
        return [o for o in _active(doc["objects"]) if o["category"] in FIXTURES]
    if category == "text":
        return _active(doc["texts"])
    raise ValueError(category)


def centre(el: dict) -> tuple[float, float] | None:
    b = el.get("bbox_px")
    if not b:
        poly = el.get("polygon_px")
        if not poly:
            return None
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        b = [min(xs), min(ys), max(xs), max(ys)]
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def cells(width: int, height: int, grid: int) -> list[list[float]]:
    """Grid cells as [x0, y0, x1, y1], row by row; edges are rounded to 0.1 px."""
    xs = [round(width * i / grid, 1) for i in range(grid + 1)]
    ys = [round(height * j / grid, 1) for j in range(grid + 1)]
    return [[xs[i], ys[j], xs[i + 1], ys[j + 1]] for j in range(grid) for i in range(grid)]


def _cell_index(pt, width, height, grid) -> int:
    i = min(grid - 1, max(0, int(pt[0] * grid / width)))
    j = min(grid - 1, max(0, int(pt[1] * grid / height)))
    return j * grid + i


def _mean(vals) -> float:
    vals = list(vals)
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def build(doc: dict, grid: int = 4) -> list[dict]:
    """Rewrite the coverage records of GRID_CATEGORIES in place (see module doc). Returns the new records."""
    img = doc["drawing"].get("image")
    old = {c["category"]: c for c in doc["coverage"] if c["region"] == "page"}
    new = []
    for cat in GRID_CATEGORIES:
        page = old.get(cat)
        els = elements(doc, cat)
        if page is None or page["status"] == "not_analysed":
            status, extractor, note = "not_analysed", (page or {}).get("extractor", "none"), "not analysed"
            conf = 0.0
        else:
            status, extractor = page["status"], page["extractor"]
            conf = _mean(e["confidence"] for e in els) if els else page["confidence"]
            note = (page.get("note") or "") + f"; {len(els)} element(s), mean confidence {conf}"
        new.append({"category": cat, "region": "page", "status": status, "extractor": extractor,
                    "confidence": conf, "note": note.lstrip("; ")})  # fmt: skip
        if not img:
            continue
        w, h = img["width_px"], img["height_px"]
        per_cell: list[list[float]] = [[] for _ in range(grid * grid)]
        for e in els:
            pt = centre(e)
            if pt is not None:
                per_cell[_cell_index(pt, w, h, grid)].append(e["confidence"])
        for k, box in enumerate(cells(w, h, grid)):
            confs = per_cell[k]
            if status == "not_analysed":
                rec = {"status": "not_analysed", "confidence": 0.0, "note": "not analysed"}
            elif confs:
                m = _mean(confs)
                rec = {"status": "analysed" if m >= GOOD_CONF else "partial", "confidence": m,
                       "note": f"{len(confs)} element(s)"}  # fmt: skip
            else:
                rec = {"status": status, "confidence": conf, "note": "no elements found"}
            new.append({"category": cat, "region": box, "extractor": extractor, **rec})
    keep = [c for c in doc["coverage"] if c["category"] not in GRID_CATEGORIES]
    doc["coverage"] = keep + new
    return new
