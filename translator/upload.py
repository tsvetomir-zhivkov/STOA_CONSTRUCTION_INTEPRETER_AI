"""Raster upload (PNG / JPG, or a scanned PDF's first page) -> one JSON in blueprint-ingest's schema v0.3.

  python -m translator.upload <input> <out.json> [--name <original file name>]

The raster translator: CubiCasa5K segmentation and OCR run on Modal (translator/modal_app.py, app stoa-translator,
deployed with `modal deploy translator/modal_app.py`); their raw results are cached in index/_raw/ under a content
hash, so the same file never reruns the GPU. The local raster route (translator._run_extractor: cubicasa.to_parts,
fuse.fuse_raster) builds the v0.2 drawing, which is lifted to v0.3 with translator.schema_compat.migrate_v02_to_v03
(the Python mirror of blueprint-ingest/src/schema/v03.js) and validated against the same per-drawing v0.3 schema the
vector extractor (blueprint-ingest) writes.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from translator.config import REPO_ROOT

APP_NAME = "stoa-translator"  # translator/modal_app.py
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf"}
T4_USD_PER_S = 0.000164  # as in modal_app.py, for the cost note in the raw file


class TranslatorUnavailable(RuntimeError):
    """The Modal side (package, login or deployed app) is not ready; the message says what to do."""


SETUP = (
    "set it up once from the repo root: .venv/bin/pip install modal && .venv/bin/python -m modal setup && "
    ".venv/bin/python -m modal deploy translator/modal_app.py (the CubiCasa weights must be in the Modal Volume "
    "cubicasa-weights)"
)


def _modal_cls(name: str):
    try:
        import modal
    except ImportError as e:
        raise TranslatorUnavailable(f"the Modal client is not installed; {SETUP}") from e
    try:
        cls = modal.Cls.from_name(APP_NAME, name)
        cls.hydrate()  # fails here, with a clear error, when the app is not deployed or there is no login
        return cls
    except Exception as e:
        raise TranslatorUnavailable(f"Modal app {APP_NAME!r} ({name}) is not reachable: {e}; {SETUP}") from e


def page_png(path: Path, width_px: int, height_px: int, dpi: int) -> bytes:
    """First page of a scanned PDF as PNG, exactly the router's pixel grid (no resize: crop/pad by at most a pixel)."""
    import pymupdf
    from PIL import Image

    with pymupdf.open(path) as pdf:
        pix = pdf[0].get_pixmap(dpi=dpi, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if img.size != (width_px, height_px):
        canvas = Image.new("RGB", (width_px, height_px), "white")
        canvas.paste(img, (0, 0))
        img = canvas
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ensure_raw(stem: str, image: bytes) -> list[str]:
    """Segmentation (required) and OCR (optional) raw files for this image; calls Modal only for what is missing."""
    from translator.extract import cubicasa, ocr

    notes = []
    jobs = {}
    if not cubicasa.raw_path(stem).exists():
        jobs["seg"] = _modal_cls("Seg")
    if not ocr.raw_path(stem).exists():
        jobs["ocr"] = _modal_cls("Ocr")
    if not jobs:
        return ["raw segmentation and OCR reused from index/_raw"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {k: pool.submit(lambda c: c().run.remote(image, stem), cls) for k, cls in jobs.items()}
        results = {}
        for k, f in futures.items():
            try:
                results[k] = f.result()
            except Exception as e:  # noqa: BLE001 - reported below; segmentation is required, OCR is not
                results[k] = e
    seg = results.get("seg")
    if isinstance(seg, Exception):
        raise RuntimeError(f"CubiCasa segmentation failed on Modal: {seg}") from seg
    for kind, module, res in (("seg", cubicasa, seg), ("ocr", ocr, results.get("ocr"))):
        if res is None:
            continue
        if isinstance(res, Exception):
            notes.append(f"OCR failed on Modal, room labels not read: {str(res)[:200]}")
            continue
        res["cost_usd"] = round(res.get("timing_s", {}).get("total", 0) * T4_USD_PER_S, 6)
        module.raw_dir().mkdir(parents=True, exist_ok=True)
        module.raw_path(stem).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        notes.append(f"{'segmentation' if kind == 'seg' else 'OCR'} run on Modal ({res['timing_s']['total']} s GPU)")
    return notes


def convert(input_path: str | Path, out_json: str | Path, name: str | None = None) -> dict:
    """Translate one raster upload and write the v0.3 JSON to out_json. Returns the document."""
    from translator import router, translate_drawings
    from translator.schema_compat import migrate_v02_to_v03, validate_document

    src = Path(input_path)
    name = name or src.name
    suffix = src.suffix.lower()
    if suffix not in RASTER_SUFFIXES:
        raise ValueError(f"{name}: not a raster file (.png, .jpg, .jpeg or a scanned .pdf)")
    data = src.read_bytes()
    stem = "upload-" + hashlib.sha256(data).hexdigest()[:16]  # the raw-cache key: same file, same GPU results
    with tempfile.TemporaryDirectory(prefix="stoa-raster-") as tmp:
        work = Path(tmp) / f"{stem}{suffix}"
        shutil.copyfile(src, work)
        r = router.route(work, tmp)
        if r["route"] != "raster":
            raise ValueError(f"{name}: routed to {r['route']!r}, not raster ({r['reason']}); vector files go to blueprint-ingest")
        ras = r["raster"]
        image = page_png(work, ras["width_px"], ras["height_px"], ras["render_dpi"]) if suffix == ".pdf" else data
        notes = ensure_raw(stem, image)
        docs = translate_drawings(str(work), tmp)
    doc = docs[0]
    drawing = doc["drawing"]
    drawing["file"] = drawing["read_from"] = name
    drawing["source"] = "upload"
    doc["history"].append({"extractor": "translator.upload", "action": "raster_upload", "note": "; ".join(notes)})
    out = migrate_v02_to_v03(doc, note="raster translator (CubiCasa5K + OCR) output lifted to schema v0.3")
    validate_document(out, where=name)
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m translator.upload", description=__doc__.split("\n\n")[0])
    ap.add_argument("input")
    ap.add_argument("out")
    ap.add_argument("--name", help="original file name (drawing.file)")
    a = ap.parse_args(argv)
    try:
        convert(a.input, a.out, a.name)
    except TranslatorUnavailable as e:
        print(f"TranslatorUnavailable: {e}", file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001 - one line for the caller
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    raise SystemExit(main())
