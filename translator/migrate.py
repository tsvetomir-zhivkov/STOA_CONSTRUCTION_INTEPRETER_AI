"""CLI: python -m translator.migrate page1.json page2.json --out building.json.

Inputs are explicitly associated by the caller; filenames never prove building identity.
Original files are left intact. Unsupported versions and conflicting source facts fail loudly.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from translator.building import merge_buildings
from translator.store import read_drawing, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--building-id")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output already exists; choose a new path to preserve the existing file")
    doc = merge_buildings([read_drawing(p) for p in args.inputs], args.building_id)
    validate(doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=args.out.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(doc, stream, indent=2, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, args.out)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(args.out)


if __name__ == "__main__":
    main()
