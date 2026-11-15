"""OCR for pipe sheets through the translator's Modal Ocr class (app stoa-translator), imported, not edited.

  python -m pipes.raster.ocr_run pipes/data_pipes/dev/test     # raw -> index_pipes/_raw/dev_<split>/<id>.ocr.json

Two passes like the translator's: as drawn, and rotated 90 degrees clockwise (vertical tags along risers).
Boxes are in ORIGINAL page pixels. Raw files are cached; sheets that already have one are skipped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent.parent


def raw_path(split: str, sid: str) -> Path:
    return HERE / "index_pipes" / "_raw" / f"dev_{split}" / f"{sid}.ocr.json"


def load(split: str, sid: str) -> list[dict]:
    """OCR items {text, score, poly} for a sheet (both passes), or [] when there is no raw file."""
    p = raw_path(split, sid)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    return raw["items"]


def main(argv: list[str]) -> int:
    from pipes.reuse import translator_ocr

    Ocr, tr_app = translator_ocr()  # TranslatorMissing (merge-v03) instead of an ImportError

    root = Path(argv[0])
    split = root.name
    todo = [d for d in sorted(root.iterdir()) if d.is_dir() and not raw_path(split, d.name).exists()]
    with tr_app.run():
        ocr = Ocr()
        for d in todo:
            data = (d / "sheet.jpg").read_bytes()
            a = ocr.run.remote(data, d.name)
            r = ocr.run_rotated.remote(data, d.name)
            items = [*a["orientations"]["original"], *r["items"]]
            p = raw_path(split, d.name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"name": d.name, "items": items, "model": a["model"]["rec_model"]}))
            print(d.name, len(items), "text boxes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
