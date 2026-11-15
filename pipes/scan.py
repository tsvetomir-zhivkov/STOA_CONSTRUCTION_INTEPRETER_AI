"""The scan route from the command line: python -m pipes <image.png|jpg|jpeg|webp> [--source-file NAME] [--force]

  1. the page is decoded (PIL, so WEBP works) and kept losslessly as data_pipes/ds/scan/<id>/sheet.png, never resized;
  2. symbols: the frozen detector (config.py: run full, tiles + whole sheet, 0.5 reported / 0.25 erased) on Modal;
  3. text: the translator's PaddleOCR class on Modal (both orientations);
  4. the skeleton tracer, tags and graph (pipes.raster.pipeline), written through finalize;
  5. published where the router and the agent look (index_pipes/<source>/<stem>#0.pipe.json + the page PNG) and an
     overlay PNG (symbols, traced pipes, unsnapped ends) in index_pipes/overlays/scan/.
Both GPU results are cached in index_pipes/_raw (keyed by the image's SHA-1), so a rerun costs nothing; --force refetches.

Resolution check: when the measured stroke width is under 3 px the page is too coarse to separate pipes from text and
symbols. A warning is printed and the sheet gets a `connectivity` coverage entry (partial, whole page), so the tools
answer "unknown, pipes not analysed here" instead of "not connected".
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from pipes import config as cfg
from pipes.eval.overlay import draw
from pipes.raster import ocr_run, pipeline, quality
from pipes.routing import PIPES_INDEX, pipe_index_path
from pipes.schema.convert import graph
from pipes.write import finalize

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
LOW_RES_STROKE_PX = 3.0
SPLIT = "scan"
FILE_FORMAT = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg"}  # anything else (webp) is "other" in the schema
STRUCTURAL = {"tee", "junction", "elbow", "cross", "open_end"}


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "sheet"


def work_dir(sid: str) -> Path:
    return pipeline.HERE / "data_pipes" / "ds" / SPLIT / sid


def det_path(sid: str, run: str = cfg.DETECTOR_RUN) -> Path:
    return pipeline.HERE / "index_pipes" / "_raw" / f"ds_{SPLIT}_{run}" / f"{sid}.det.json"


# ---------------------------------------------------------------- the two GPU calls (stubbed in tests)


def fetch_detections(sid: str, png: bytes, run: str = cfg.DETECTOR_RUN) -> dict:
    """Raw tile + whole-sheet detections in page pixels, from the Modal detector (pipes.modal_pipes.detect)."""
    from pipes.modal_pipes import app, detect

    with app.run():
        res = detect.remote({sid: png}, 0.05, run, 1280, 640)
    d = res["sheets"][sid]
    d["model"] = f"yolo11s single-class symbol, run {run}"
    d["cost_usd"] = res["cost_usd"]
    return d


def fetch_upscale(png: bytes, factor: int = quality.UPSCALE):
    """Real-ESRGAN x3 on Modal (pipes.modal_pipes.upscale) -> BGR array, or an exception the gate records."""
    import cv2
    import numpy as np

    from pipes.modal_pipes import app, upscale

    with app.run():
        out = upscale.remote(png, factor)
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)


def fetch_ocr(sid: str, png: bytes) -> list[dict]:
    """OCR items {text, score, poly} in page pixels: as drawn plus rotated 90 degrees (the translator's Ocr class)."""
    from translator.modal_app import Ocr
    from translator.modal_app import app as tr_app

    with tr_app.run():
        ocr = Ocr()
        a = ocr.run.remote(png, sid)
        r = ocr.run_rotated.remote(png, sid)
    return [*a["orientations"]["original"], *r["items"]]


# ---------------------------------------------------------------- the route


def _cached(path: Path, sha: str, force: bool):
    if force or not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if raw.get("sha1") == sha else None


def translate_scan(
    path: str | Path,
    source_file: str | None = None,
    publish: bool = True,
    force: bool = False,
    pipes_dir: Path | None = None,
    overlay: bool = True,
) -> dict:
    """-> {doc, warnings, stroke_width_px, overlay, published}. Raises on an unreadable image."""
    path = Path(path)
    data = path.read_bytes()
    sha = hashlib.sha1(data).hexdigest()[:12]
    img = Image.open(io.BytesIO(data)).convert("RGB")
    sid = slug(path.stem)
    wd = work_dir(sid)
    wd.mkdir(parents=True, exist_ok=True)
    sheet = wd / "sheet.png"
    original = wd / "original.png"
    arr = np.asarray(img)[..., ::-1].copy()  # BGR
    g = quality.gate(arr)  # measure first; fixes only for the checks that failed
    clean = g["image"] is not arr
    img.save(original if clean else sheet)  # lossless, same pixel grid as the decoded input
    if clean:
        cv2.imwrite(str(sheet), g["image"])  # what the tracer, detector and OCR see: same grid, cleaned
    elif original.exists():
        original.unlink()  # a stale cleaned copy from an earlier run must not shadow this page
    png = sheet.read_bytes()
    factor, big = g["factor"], {}

    def upscaled() -> tuple[bytes, str]:
        if not big:
            im, name = quality.upscale_image(
                g["image"], lambda a: fetch_upscale(cv2.imencode(".png", a)[1].tobytes())
            )
            big["png"], big["name"] = cv2.imencode(".png", im)[1].tobytes(), name
        return big["png"], big["name"]

    det = _cached(det_path(sid), sha, force)
    if det is None:
        if factor > 1:
            up_png, name = upscaled()
            det = quality.scale_detections(fetch_detections(sid, up_png), factor, img.size)
            det["gate_upscale"] = name
        else:
            det = fetch_detections(sid, png)
        det = {**det, "sha1": sha}
        det_path(sid).parent.mkdir(parents=True, exist_ok=True)
        det_path(sid).write_text(json.dumps(det), encoding="utf-8")
    ocr_file = ocr_run.raw_path(SPLIT, sid)
    ocr = _cached(ocr_file, sha, force)
    if ocr is None:
        items = fetch_ocr(sid, upscaled()[0] if factor > 1 else png)
        ocr = {"name": sid, "items": quality.scale_texts(items, factor), "sha1": sha}
        ocr_file.parent.mkdir(parents=True, exist_ok=True)
        ocr_file.write_text(json.dumps(ocr), encoding="utf-8")
    if factor > 1:
        g["fixes"].append(det.get("gate_upscale") or big.get("name") or f"upscale_x{factor}")

    doc = pipeline.run_sheet(wd, "det", texts=ocr["items"])
    dbg = doc.pop("_debug")
    doc = graph(doc)  # the working graph doc; finalize() writes the piping schema
    w = float(dbg["stroke_width_px"])
    source_file = (source_file or f"{SPLIT}/{path.name}").replace("\\", "/")
    doc["sheet"].update(
        id=f"{source_file}#0",
        source_file=source_file,
        source_dataset=None,
        file_format=FILE_FORMAT.get(path.suffix.lower(), "other"),
    )
    doc["frame"]["frame_key"] = f"{source_file}#0@{img.width}x{img.height}"
    if g[
        "failed"
    ]:  # provenance: which checks failed and which fixes ran (nothing is added for a normal sheet)
        doc["extractors"].append(
            {"id": "ext_gate", "kind": "geometry_code", "name": quality.note(g), "version": None,
             "run_at": None, "cost_usd": None}
        )  # fmt: skip
        doc["transforms"] = quality.transforms(g)
    warnings = []
    if w < LOW_RES_STROKE_PX:
        msg = f"stroke width {w:.1f} px (< {LOW_RES_STROKE_PX:g} px): resolution too low, results unreliable"
        warnings.append(msg)
        doc["coverage"].append(
            {"id": f"cov_{len(doc['coverage']) + 1}", "region": "page", "category": "connectivity",
             "status": "partial", "confidence": 0.2, "extractor_id": "ext_graph", "element_ids": [], "note": msg}
        )  # fmt: skip
    doc = finalize(doc)

    out = {
        "doc": doc,
        "warnings": warnings,
        "stroke_width_px": round(w, 2),
        "gate": {k: g[k] for k in ("measures", "failed", "fixes")},
        "overlay": None,
        "published": None,
    }
    if overlay:
        out["overlay"] = draw(
            original if clean else sheet,
            doc,
            pipeline.HERE / "index_pipes" / "overlays" / SPLIT / f"{sid}.png",
        )
    if publish:
        target = pipe_index_path(source_file, 0, pipes_dir or PIPES_INDEX)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        shutil.copyfile(
            original if clean else sheet, target.with_name(target.name.removesuffix(".pipe.json") + ".png")
        )
        out["published"] = target
    return out


def summary(doc: dict) -> dict:
    doc = graph(doc)
    nodes = doc["nodes"]
    count = lambda t: sum(1 for n in nodes if n["type"] == t)  # noqa: E731
    return {
        "symbols": sum(1 for n in nodes if n["type"] not in STRUCTURAL and n.get("subtype") != "equipment"),
        "equipment_nodes": sum(1 for n in nodes if n.get("subtype") == "equipment"),
        "edges": len(doc["edges"]),
        "tees": count("tee"),
        "junctions": count("junction"),
        "open_ends": count("open_end"),
        "unsnapped_ends": doc["summary"]["unsnapped_end_count"],
        "tags": {n["id"]: n["tag"] for n in nodes if n.get("tag")},
        "line_tags": sorted({e["line_tag"] for e in doc["edges"] if e["line_tag"]}),
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m pipes", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("image")
    ap.add_argument(
        "--source-file", help="name to publish under, e.g. site/plan.png (default: scan/<file name>)"
    )
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--force", action="store_true", help="refetch detections and OCR even if cached")
    a = ap.parse_args(argv)
    r = translate_scan(a.image, a.source_file, publish=not a.no_publish, force=a.force)
    for w in r["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    print(json.dumps({**summary(r["doc"]), "stroke_width_px": r["stroke_width_px"]}, ensure_ascii=False))
    print(f"overlay:   {Path(r['overlay']).resolve()}")
    if r["published"]:
        print(f"published: {Path(r['published']).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
