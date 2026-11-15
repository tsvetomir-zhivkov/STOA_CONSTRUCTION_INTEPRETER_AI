"""Paths. The dataset root holds questions.jsonl and data/<source>/<file>.

Resolution order: explicit argument > STOA_DATA env var > repo root.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = REPO_ROOT / "index"
LEGACY_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "drawing.schema.json"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "building.v0.3.schema.json"


def data_root(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("STOA_DATA")
    if env:
        return Path(env)
    return REPO_ROOT


def questions_path(root: Path | None = None) -> Path:
    return (root or data_root()) / "questions.jsonl"
