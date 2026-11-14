"""Schema-validated reading and writing of index documents.

Every write is validated; an invalid document raises and is never written.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path, PurePosixPath

import jsonschema

from translator.config import INDEX_DIR, SCHEMA_PATH


class SchemaError(ValueError):
    """Raised when a document does not match drawing.schema.json."""


@lru_cache(maxsize=1)
def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def validate(doc: dict, where: str = "<doc>") -> None:
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f"{where}: {len(errors)} schema error(s)"]
        for e in errors[:20]:
            path = "/".join(str(p) for p in e.absolute_path) or "<root>"
            lines.append(f"  {path}: {e.message[:300]}")
        raise SchemaError("\n".join(lines))


def _slug(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-") or "x"


def index_path(drawing_file: str, part: str | None = None, index_dir: Path = INDEX_DIR) -> Path:
    """index/<source>/<stem>.json, or index/<source>/<stem>__<part>.json for multi-page/level files.

    drawing_file is the dataset-relative path, e.g. data/swiss_dwellings/x.pdf.
    """
    p = PurePosixPath(drawing_file.replace("\\", "/"))
    source = p.parent.name
    name = p.stem if part is None else f"{p.stem}__{_slug(part)}"
    return index_dir / source / f"{name}.json"


def write_drawing(doc: dict, index_dir: Path = INDEX_DIR) -> Path:
    """Validate and atomically write one document. Returns the path written."""
    validate(doc, where=doc.get("drawing", {}).get("file", "<doc>"))
    out = index_path(doc["drawing"]["file"], doc["drawing"].get("part"), index_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out


def read_drawing(path: Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(doc, where=str(path))
    return doc
