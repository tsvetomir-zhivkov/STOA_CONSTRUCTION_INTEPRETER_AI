"""Every route writes through finalize(): derive, validate, raise with the error list.

Output is the piping schema v0.1 (pipes/schema/pipe.schema.json). The routes build a working graph doc (nodes /
edges / texts); finalize() converts it (pipes.schema.convert.to_piping), derives and validates. It also accepts a
piping doc (convert.view() first), so re-finalizing a published file works. Never writes unvalidated JSON.
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
        raise PipeSchemaMissing(f"pipes/schema/{{convert,derive,validate}}.py are missing ({e})") from e
    return derive, validate


def finalize(pipe: dict, drawings: dict | None = None) -> dict:
    """graph doc (or piping doc) -> piping doc: convert, derive, validate; raises PipeValidationError."""
    derive, validate = _schema_modules()
    from pipes.schema import convert

    drawings = drawings or {}
    if convert.is_piping(pipe) and (errors := validate.check(pipe, drawings)):
        raise PipeValidationError(errors)  # a piping doc is checked as given before it is re-derived
    out = derive.derive(convert.to_piping(convert.view(copy.deepcopy(pipe))), drawings)
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
