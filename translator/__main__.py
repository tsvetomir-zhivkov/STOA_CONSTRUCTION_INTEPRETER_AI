"""CLI: python -m translator <path> [--data DATASET_ROOT]"""

from __future__ import annotations

import argparse
import json
import sys

from translator import translate


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m translator")
    ap.add_argument("path", help="drawing file, e.g. data/swiss_dwellings/x.pdf")
    ap.add_argument("--data", help="dataset root (default: $STOA_DATA)")
    args = ap.parse_args(argv)
    doc = translate(args.path, args.data)
    json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
