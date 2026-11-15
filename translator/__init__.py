"""Stoa vision translator: supported sources -> schema v0.3 JSON (building.schema.v0.3.json).

One document per drawing page; an IFC model gives one document per IFC building with all its storeys.

Hand-off contract: translate(path) -> dict, and `python -m translator <path>`.
Outputs go to index/<source>/<file>.json (see translator.store.index_path).
"""

from __future__ import annotations

from datetime import datetime, timezone
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


def translate(
    path: str, data: str | None = None, part: str | None = None, allow_holdout: bool = False
) -> dict:
    """Translate one drawing file into one schema v0.3 document (validated, not written).

    Most files give one document. A file that gives several (an IFC model with several IFC buildings) needs
    part=<drawing.part>, or use translate_all(path). Hold-out files are refused unless allow_holdout=True.
    """
    docs = translate_all(path, data, allow_holdout=allow_holdout)
    if part is not None:
        hits = [d for d in docs if d["drawing"].get("part") == part]
        if not hits:
            raise KeyError(f"{path}: no part {part!r}")
        return hits[0]
    if len(docs) > 1:
        parts = [d["drawing"].get("part") for d in docs]
        raise ValueError(
            f"{path} gives {len(docs)} documents (parts {parts}); pass part= or use translate_all()"
        )
    return docs[0]


def translate_all(
    path: str,
    data: str | None = None,
    cubicasa_params: dict | None = None,
    ocr_params: dict | None = None,
    fuse_params: dict | None = None,
    allow_holdout: bool = False,
) -> list[dict]:
    """Every document for one file, schema v0.3 (building.schema.v0.3.json), validated.

    The extractors build schema v0.2 page documents (drawing.schema.json); each is validated, then converted
    with translator.schema_compat.migrate_v02_to_v03 and validated again against building.schema.v0.3.json.
    One document per drawing page; an IFC model gives one per IFC building, with all its storeys.

    Hold-out files (eval/holdout_files.txt) are refused (HoldoutError) by the same filename guard as
    translator/vlm, before any file is read; allow_holdout=True overrides it with a warning.

    Raster CubiCasa plans only: cubicasa_params go to translator.extract.cubicasa.to_parts, ocr_params to
    translator.extract.ocr.fuse, fuse_params (priors, grid) to translator.fuse.fuse_raster.
    """
    from translator import router
    from translator.config import data_root
    from translator.schema_compat import migrate_v02_to_v03
    from translator.store import validate
    from translator.vlm.base import HoldoutError, default_guard

    try:
        default_guard().check_source(path)  # names only: nothing is read before this
    except HoldoutError:
        if not allow_holdout:
            raise
        import warnings

        warnings.warn(f"allow_holdout=True: translating hold-out file {path}", stacklevel=2)
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
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
    out = []
    for d in _run_extractor(doc, r, root, file_path, cubicasa_params, ocr_params, fuse_params):
        validate(d, where=r["file"])  # schema v0.2: what the extractors produce
        v03 = migrate_v02_to_v03(d)
        validate(v03, where=r["file"])  # the contract: building.schema.v0.3.json + relationship check
        out.append(v03)
    return out


def _ifc_documents(base: dict, r: dict, root: Path, file_path: Path) -> list[dict]:
    """IFC route. A rendered plan of one storey gets that storey (mapped onto the page); the model itself gets
    one document per IFC building with all its storeys (and the elements without a storey)."""
    import copy

    from translator.extract import ifc

    data = ifc.extract_records(_abs(root, r["read_from"]))
    scale = {
        "m_per_px": None,
        "source": "vector_units",
        "confidence": 1.0,
        "note": f"IFC model units converted to metres ({data['schema']})",
    }
    level = r.get("level") if file_path.suffix.lower() != ".ifc" else None
    if level is not None:
        out = []
        for parts in ifc.documents(data, r["read_from"], level_name=level):
            d = copy.deepcopy(base)
            parts.pop("part")
            d["drawing"]["part"] = None
            d["drawing"]["scale"] = dict(scale)
            d["history"].extend(parts.pop("history"))
            d.update(parts)
            _voids_to_objects(d)
            _map_ifc_to_page(d, file_path)
            out.append(d)
        if not out:
            base["history"].append(
                {"extractor": "ifc", "action": "skip", "note": f"no storey named {level!r}"}
            )
            return [base]
        return out
    buildings = [b["global_id"] for b in data.get("metadata", {}).get("buildings", [])] or [None]
    if len(buildings) > 1 and any(rec.get("building") is None for rec in data["records"]):
        buildings.append(None)
    out = []
    for gid in buildings:
        subset = data
        if len(buildings) > 1:
            subset = {
                **data,
                "storeys": [st for st in data["storeys"] if st.get("building") == gid],
                "records": [rec for rec in data["records"] if rec.get("building") == gid],
            }
        d = copy.deepcopy(base)
        d["drawing"]["part"] = None if len(buildings) == 1 else f"building-{gid or 'none'}"
        d["drawing"]["scale"] = dict(scale)
        _merge_storeys(d, ifc.documents(subset, r["read_from"]))
        _voids_to_objects(d)
        out.append(d)
    return out


_OPENING_ONLY = ("kind", "width_m", "height_m", "wall_id", "room_ids", "is_entrance", "mark", "operation")


def _voids_to_objects(doc: dict) -> None:
    """IfcOpeningElement records (kind 'void') are cuts, not doors or windows; the schema's openings[] has no
    kind for them. Keep them as objects[] (category 'other', subtype 'opening_void'), same id and geometry."""
    voids = [o for o in doc["openings"] if o.get("kind") == "void"]
    if not voids:
        return
    doc["openings"] = [o for o in doc["openings"] if o.get("kind") != "void"]
    for o in voids:
        obj = {k: v for k, v in o.items() if k not in _OPENING_ONLY}
        obj.update(category="other", subtype="opening_void")
        doc["objects"].append(obj)
    doc["history"].append(
        {
            "extractor": "ifc",
            "action": "map",
            "note": f"{len(voids)} IfcOpeningElement void(s) kept as objects[] (category other, "
            "subtype opening_void): openings[] only has door / window / doorway",
        }
    )


_STATUS_RANK = {"not_analysed": 0, "partial": 1, "analysed": 2}


def _merge_storeys(doc: dict, parts: list[dict]) -> None:
    """All storey parts of one IFC building into one v0.2 document. Coverage per category keeps the weakest
    status and the lowest confidence over the storeys (coverage regions are page-level only)."""
    for key in ("levels", "rooms", "walls", "openings", "objects"):
        doc[key] = [e for p in parts for e in p[key]]
    for p in parts:
        doc["history"].extend(p["history"])
    by_cat: dict[str, list[dict]] = {}
    for p in parts:
        for c in p["coverage"]:
            by_cat.setdefault(c["category"], []).append(c)
    coverage = []
    for cs in by_cat.values():
        worst = min(cs, key=lambda c: (_STATUS_RANK[c["status"]], c["confidence"]))
        entry = {**worst, "region": "page", "confidence": min(c["confidence"] for c in cs)}
        if len(parts) > 1:
            notes = sorted({c["note"] for c in cs if c.get("note")})
            entry["note"] = f"{len(parts)} storey parts: " + "; ".join(notes)
        coverage.append(entry)
    if parts:
        doc["coverage"] = coverage


def _abs(root: Path, p: str) -> Path:
    return Path(p) if Path(p).is_absolute() else root / p


def _run_extractor(
    doc: dict,
    r: dict,
    root: Path,
    file_path: Path,
    cubicasa_params: dict | None = None,
    ocr_params: dict | None = None,
    fuse_params: dict | None = None,
) -> list[dict]:
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
    if r["route"] == "ifc":
        return _ifc_documents(doc, r, root, file_path)
    if r["route"] == "raster" and r.get("source") == "cubicasa5k":
        # CubiCasa5K segmentation + OCR from their raw GPU results (modal_app.py); never calls the GPU,
        # then fusion, priors, scale and coverage (translator.fuse.fuse_raster)
        from translator import fuse
        from translator.extract import cubicasa, ocr

        raw = cubicasa.load_raw(file_path.stem)
        if raw is None:
            doc["history"].append(
                {"extractor": cubicasa.EXTRACTOR, "action": "skip", "note": "no raw segmentation file yet"}
            )
            return [doc]
        if (raw["width_px"], raw["height_px"]) != (
            doc["drawing"]["image"]["width_px"],
            doc["drawing"]["image"]["height_px"],
        ):
            raise ValueError(f"{r['file']}: raw segmentation size differs from the image")
        parts = cubicasa.to_parts(raw, r["read_from"], **(cubicasa_params or {}))
        doc["drawing"]["transforms"].extend(parts.pop("transforms"))
        doc["history"].extend(parts.pop("history"))
        for key, value in parts.items():
            doc[key] = value
        ocr_raw = ocr.load_raw(file_path.stem)
        if ocr_raw is not None and (ocr_raw["width_px"], ocr_raw["height_px"]) != (
            raw["width_px"],
            raw["height_px"],
        ):
            raise ValueError(f"{r['file']}: raw OCR size differs from the image")
        fuse.fuse_raster(doc, raw, ocr_raw, r["read_from"], **(ocr_params or {}), **(fuse_params or {}))
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
