"""Stoa vision translator: supported sources -> one v0.3 JSON per building.

Hand-off contract: translate(path) -> dict, and `python -m translator <path>`.
Outputs go to index/<source>/<file>.json (see translator.store.index_path).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

__version__ = "0.3.0"

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
        import pymupdf

        # same outward-rounded pixel rect pymupdf renders at this dpi (get_pixmap(dpi=...))
        k = router.PDF_DPI / 72
        with pymupdf.open(file_path) as pdf:
            irect = (pdf[0].rect * pymupdf.Matrix(k, k)).irect
        img = {"width_px": irect.width, "height_px": irect.height, "dpi": router.PDF_DPI}
        return img, 0, len(pages)
    if ext in router.RASTER_EXTS:
        try:
            info = router.raster_info(file_path)
            return {"width_px": info["width_px"], "height_px": info["height_px"], "dpi": None}, 0, 1
        except Exception:
            return None, 0, 1
    return None, None, None


def translate(path: str, data: str | None = None, part: str | None = None) -> dict:
    """Return one validated building. Multi-building IFC inputs require translate_all().

    The deprecated part argument returns an explicit, non-persistable source-frame view.
    """
    docs = translate_all(path, data)
    if part is not None:
        from translator.building import page_views

        hits = [d for doc in docs for d in page_views(doc) if d["drawing"].get("part") == part]
        if not hits:
            raise KeyError(f"{path}: no part {part!r}")
        return hits[0]
    if len(docs) > 1:
        parts = [d["building_id"] for d in docs]
        raise ValueError(f"{path} contains {len(docs)} buildings ({parts}); use translate_all()")
    return docs[0]


def translate_all(path: str, data: str | None = None) -> list[dict]:
    """Validated buildings for one input, including all IFC storeys and uncontained elements."""
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
    from translator.building import merge_buildings

    if r["route"] == "ifc":
        docs = _ifc_buildings(doc, r, root, file_path)
    else:
        docs = [merge_buildings(_run_extractor(doc, r, root, file_path))]
    for d in docs:
        validate(d, where=r["file"])
    return docs


def _ifc_buildings(base, route, root, file_path):
    import copy

    from translator.building import merge_buildings, stable_id
    from translator.extract import ifc
    from translator.extract.ifc_metadata import enrich_building

    data = ifc.extract_records(_abs(root, route["read_from"]))
    buildings = data["metadata"]["buildings"]
    groups = [b["global_id"] for b in buildings] or [None]
    if len(groups) > 1 and any(r.get("building") is None for r in data["records"]):
        groups.append(None)
    result = []
    for gid in groups:
        subset = {
            **data,
            "storeys": [s for s in data["storeys"] if s.get("building") == gid or len(groups) == 1],
            "records": [r for r in data["records"] if r.get("building") == gid or len(groups) == 1],
        }
        fragments = []
        for parts in ifc.documents(subset, route["read_from"]):
            fragment = copy.deepcopy(base)
            fragment["drawing"]["part"] = parts.pop("part")
            fragment["drawing"]["scale"] = {
                "m_per_px": None,
                "source": "vector_units",
                "confidence": 1.0,
                "note": f"IFC {data['schema']}; normalized SI geometry",
            }
            fragment["history"].extend(parts.pop("history"))
            fragment.update(parts)
            # Only the requested level belongs to this rendered sheet's coordinate frame.
            if route.get("level") and any(level["name"] == route["level"] for level in fragment["levels"]):
                _map_ifc_to_page(fragment, file_path)
            elif file_path.suffix.lower() != ".ifc":
                fragment["drawing"].update(
                    file=route["read_from"], image=None, page_index=None, page_count=None
                )
            fragments.append(fragment)
        building = merge_buildings(
            fragments or [base],
            building_id=f"ifc:{gid}" if gid else stable_id("building", route["read_from"]),
        )
        result.append(enrich_building(building, data, gid))
    return result


def _abs(root: Path, p: str) -> Path:
    return Path(p) if Path(p).is_absolute() else root / p


def _run_extractor(doc: dict, r: dict, root: Path, file_path: Path) -> list[dict]:
    """Fill the document(s) from the extractor for this route, if one exists yet."""
    if r["route"] == "dxf":
        from translator.extract import swiss_dxf

        dxf = _abs(root, r["read_from"])
        if not swiss_dxf.is_swiss_dxf(dxf):
            return [doc]
        parts = swiss_dxf.extract(dxf, read_from=r["read_from"])
        doc["drawing"]["scale"] = parts.pop("scale")
        doc["history"].extend(parts.pop("history"))
        for key, value in parts.items():
            doc[key] = value
        _map_to_page(doc, file_path)
        return [doc]

    return [doc]


def _map_ifc_to_page(doc: dict, file_path: Path) -> None:
    """IFC storey -> pixels of the rendered plan page, through the plan's SVG sibling."""
    svg = file_path.with_suffix(".svg")
    if file_path.suffix.lower() != ".pdf" or not svg.exists():
        doc["history"].append(
            {"extractor": "align", "action": "skip", "note": "no PDF page with an SVG sibling"}
        )
        return
    from translator import align

    try:
        summary = align.map_ifc_document(doc, file_path, svg, doc["drawing"].get("page_index") or 0)
    except Exception as e:  # mapping is for highlighting only; never lose the extraction over it
        summary = {"ok": False, "reason": f"alignment failed: {e}"}
    note = ", ".join(f"{k}={v}" for k, v in summary.items())
    doc["history"].append({"extractor": "align", "action": "affine_fit", "note": note})


def _map_to_page(doc: dict, file_path: Path) -> None:
    """Model metres -> pixels of the asked PDF page (affine on room-number anchors)."""
    if file_path.suffix.lower() != ".pdf":
        doc["history"].append({"extractor": "align", "action": "skip", "note": "question file is not a PDF"})
        return
    from translator import align

    try:
        summary = align.map_document(doc, file_path, doc["drawing"].get("page_index") or 0)
    except Exception as e:  # mapping is for highlighting only; never lose the extraction over it
        summary = {"ok": False, "reason": f"alignment failed: {e}"}
    note = ", ".join(f"{k}={v}" for k, v in summary.items())
    doc["history"].append({"extractor": "align", "action": "affine_fit", "note": note})
