"""Every route writes through finalize(): derive, validate, raise with the error list.

Needs pipes/schema/{pipe.schema.json,derive.py,validate.py}, copied from schema/pipe/ (see schema/README.md).
Fails loudly when they are missing: never writes unvalidated JSON.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
INDEX = HERE / "index_pipes"


class PipeSchemaMissing(RuntimeError):
    pass


class PipeValidationError(ValueError):
    def __init__(self, errors: list):
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} pipe schema error(s):\n  " + "\n  ".join(map(str, self.errors)))


def _schema_modules():
    try:
        from pipes.schema import derive, validate
    except ImportError as e:
        raise PipeSchemaMissing(
            "pipes/schema/derive.py and validate.py are missing; copy them (and pipe.schema.json) "
            f"from schema/pipe/ ({e})"
        ) from e
    return derive, validate


def finalize(pipe: dict, drawings: dict | None = None) -> dict:
    """derive.derive(), then validate.check(); raises PipeValidationError with the error list."""
    derive, validate = _schema_modules()
    drawings = drawings or {}
    out = derive.derive(copy.deepcopy(pipe), drawings)
    errors = validate.check(out, drawings)
    if errors:
        raise PipeValidationError(errors)
    return out


def write(pipe: dict, source: str, name: str, drawings: dict | None = None) -> Path:
    """finalize(), then write index_pipes/<source>/<name>.pipe.json."""
    out = finalize(pipe, drawings)
    path = INDEX / source / f"{name}.pipe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
