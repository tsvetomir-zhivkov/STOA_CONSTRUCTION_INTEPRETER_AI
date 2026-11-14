"""CLI: python -m translator <path>"""

from __future__ import annotations

import argparse
import json
import sys

from translator import translate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m translator")
    ap.add_argument("path", help="drawing file, e.g. data/swiss_dwellings/x.pdf")
    args = ap.parse_args(argv)
    doc = translate(args.path)
    json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
