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

from translator.building import migrate, reference_errors
from translator.config import INDEX_DIR, LEGACY_SCHEMA_PATH, SCHEMA_PATH


class SchemaError(ValueError):
    """Raised when a document does not match drawing.schema.json."""


@lru_cache(maxsize=2)
def _validator(version="0.3") -> jsonschema.Draft202012Validator:
    path = LEGACY_SCHEMA_PATH if version == "0.2" else SCHEMA_PATH
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def validate(doc: dict, where: str = "<doc>") -> None:
    errors = sorted(
        _validator(doc.get("schema_version")).iter_errors(doc), key=lambda e: str(list(e.absolute_path))
    )
    if errors:
        lines = [f"{where}: {len(errors)} schema error(s)"]
        for e in errors[:20]:
            path = "/".join(str(p) for p in e.absolute_path) or "<root>"
            lines.append(f"  {path}: {e.message[:300]}")
        raise SchemaError("\n".join(lines))
    if doc.get("schema_version") == "0.3":
        refs = reference_errors(doc)
        if refs:
            raise SchemaError(f"{where}: reference errors\n" + "\n".join(refs[:20]))


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
    validate(doc)
    doc = migrate(doc)
    validate(doc)
    primary = doc["drawings"][0] if doc["drawings"] else doc["source_documents"][0]
    # IFC outputs use model identity, never a storey/page suffix.
    file = primary.get("read_from") if primary.get("route") == "ifc" else primary["file"]
    out = index_path(
        file,
        doc["building_id"].replace("ifc:", "")
        if doc.get("project", {}).get("ifc_building_global_id")
        else None,
        index_dir,
    )
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
    (no migration, so v0.2 consumers see exactly what was written); use translator.schema_compat.load_document for v0.3."""
    from translator.schema_compat import validate_document

    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(doc, where=str(path))
    doc = migrate(doc)
    validate(doc, where=str(path))
    return doc
