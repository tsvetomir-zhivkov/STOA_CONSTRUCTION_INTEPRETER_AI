"""Schema-validated reading and writing of index documents.

The contract is schema v0.3, translator/schema/building.schema.v0.3.json (a synced copy of
blueprint-ingest/schema/building.schema.v0.3.json). The extractors build v0.2 documents (drawing.schema.json);
write_drawing converts them with translator.schema_compat.migrate_v02_to_v03. Every write is validated; an
invalid document raises and is never written.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path, PurePosixPath

import jsonschema

from translator.config import INDEX_DIR, LEGACY_SCHEMA_PATH
from translator.schema_compat import SchemaError, as_v03, validate_document

__all__ = ["SchemaError", "index_path", "read_drawing", "validate", "write_drawing"]


@lru_cache(maxsize=1)
def _v02_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(LEGACY_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def validate(doc: dict, where: str = "<doc>") -> None:
    """v0.2 against drawing.schema.json; anything else through translator.schema_compat.validate_document
    (v0.3: building.schema.v0.3.json plus the relationship check). Raises SchemaError."""
    if doc.get("schema_version") != "0.2":
        validate_document(doc, where=where)
        return
    errors = sorted(_v02_validator().iter_errors(doc), key=lambda e: str(list(e.absolute_path)))
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
    """Validate, migrate to schema v0.3 (building.schema.v0.3.json), validate again and atomically write one
    document to index/<source>/<stem>[__<part>].json. Returns the path written."""
    validate(doc)
    doc = as_v03(doc)  # v0.2 -> v0.3 via schema_compat.migrate_v02_to_v03; a v0.3 document is kept as is
    validate(doc)
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
    """Read a v0.2 or v0.3 document, validated against the schema its schema_version names. Returned as stored
    (no migration); use translator.schema_compat.load_document to always get v0.3."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(doc, where=str(path))
    return doc
