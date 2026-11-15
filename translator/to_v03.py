"""Write schema v0.3 copies of v0.2 index documents.

    python -m translator.to_v03                      # index/ -> index_v03/ (same relative paths)
    python -m translator.to_v03 a.json b.json --out DIR [--now 2026-11-14T00:00:00Z]

The input directory is never modified. Outputs use the historical drawing-based v0.3 schema.
The workspace instead reads the canonical building contract through translator.store.
and the relationship check before it is written; an invalid document raises and nothing is written for it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from translator.config import INDEX_DIR, REPO_ROOT
from translator.schema_compat import load_document, validate_document

INDEX_V03_DIR = REPO_ROOT / "index_v03"


def _write(doc: dict, out: Path) -> None:
    validate_document(doc, where=str(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def convert(inputs: list[Path], out_dir: Path, base: Path | None, now: str | None) -> list[Path]:
    written = []
    for p in inputs:
        doc = load_document(p, now=now)
        rel = p.relative_to(base) if base else Path(p.name)
        out = out_dir / rel
        _write(doc, out)
        written.append(out)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", help="v0.2 / v0.3 JSON files (default: every file under index/)")
    ap.add_argument("--out", default=None, help="output directory (default: index_v03/)")
    ap.add_argument("--now", default=None, help="timestamp for the migration history entry (default: now)")
    a = ap.parse_args(argv)
    if a.inputs:
        inputs, base = [Path(x) for x in a.inputs], None
    else:
        base = INDEX_DIR
        inputs = (
            [p for p in sorted(INDEX_DIR.rglob("*.json")) if "_raw" not in p.relative_to(INDEX_DIR).parts]
            if INDEX_DIR.exists()
            else []
        )
    out_dir = Path(a.out) if a.out else INDEX_V03_DIR
    written = convert(inputs, out_dir, base, a.now)
    print(f"{len(written)} document(s) written to {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
