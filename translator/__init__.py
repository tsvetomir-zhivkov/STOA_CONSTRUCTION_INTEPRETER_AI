"""Stoa vision translator: any drawing file -> one schema v0.2 JSON per page.

Hand-off contract: translate(path) -> dict, and `python -m translator <path>`.
Outputs go to index/<source>/<file>.json (see translator.store.index_path).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

__version__ = "0.2.0"

COVERAGE_CATEGORIES = ("rooms", "doors", "windows", "fixtures", "walls", "text")


def _page_image(r: dict, file_path: Path) -> tuple[dict | None, int | None, int | None]:
    """Pixel space of the page the question asks about: (image, page_index, page_count)."""
    from translator import router

    ext = file_path.suffix.lower()
    if r["route"] == "raster" and "raster" in r:
        ras = r["raster"]
        dpi = ras.get("render_dpi")
        img = {"width_px": ras["width_px"], "height_px": ras["height_px"], "dpi": dpi}
        return img, 0, len(r.get("pages", [])) or 1
    if ext == ".pdf":
        pages = r.get("pages")
        if pages is None:  # routed to a sibling: still record the asked page's pixel space
            try:
                pages = router.pdf_pages(file_path)
            except Exception:
                return None, 0, None
        if not pages:
            return None, 0, None
        scale = router.PDF_DPI / 72
        p0 = pages[0]
        img = {
            "width_px": round(p0["width_pt"] * scale),
            "height_px": round(p0["height_pt"] * scale),
            "dpi": router.PDF_DPI,
        }
        return img, 0, len(pages)
    if ext in router.RASTER_EXTS:
        try:
            info = router.raster_info(file_path)
            return {"width_px": info["width_px"], "height_px": info["height_px"], "dpi": None}, 0, 1
        except Exception:
            return None, 0, 1
    return None, None, None


def translate(path: str, data: str | None = None) -> dict:
    """Translate one drawing file into a schema v0.2 document (validated, not written).

    Step 2: only the router runs. The document records drawing.route / read_from and
    marks every category not_analysed; extractors (steps 3+) fill the rest.
    """
    from translator import router
    from translator.config import data_root
    from translator.store import validate

    root = data_root(data)
    r = router.route(path, root)
    file_path = router._resolve(path, root)
    image, page_index, page_count = _page_image(r, file_path) if file_path.exists() else (None, None, None)

    note = f"{router.summary(r)}; {r['reason']}"
    doc = {
        "schema_version": "0.2",
        "drawing": {
            "file": r["file"],
            "source": r["source"],
            "page_index": page_index,
            "page_count": page_count,
            "image": image,
            # route() says 'unknown'; the schema's word for that is 'none'
            "route": "none" if r["route"] == "unknown" else r["route"],
            "read_from": r["read_from"] or r["file"],
            "transforms": [],
            "translator_version": __version__,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        "levels": [],
        "units": [],
        "rooms": [],
        "walls": [],
        "openings": [],
        "objects": [],
        "texts": [],
        "edges": [],
        "coverage": [
            {
                "category": c,
                "region": "page",
                "status": "not_analysed",
                "extractor": "router",
                "confidence": 0.0,
                "note": "no extractor has run yet",
            }
            for c in COVERAGE_CATEGORIES
        ],
        "history": [{"extractor": "router", "action": "route", "note": note}],
    }
    validate(doc, where=r["file"])
    return doc
