"""CLI: python -m translator <path> [<path> ...] [--write] [--source NAME] [--data DATASET_ROOT]

Without --write the document is printed. With --write it is validated and written to index/.
--source NAME translates every questions.jsonl file of that source (implies --write).
"""

from __future__ import annotations

import argparse
import json
import sys

from translator import translate


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m translator", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("paths", nargs="*", help="drawing file(s), e.g. data/swiss_dwellings/x.pdf")
    ap.add_argument("--data", help="dataset root (default: $STOA_DATA)")
    ap.add_argument("--write", action="store_true", help="write to index/ instead of printing")
    ap.add_argument("--source", help="translate every question file of this source (implies --write)")
    args = ap.parse_args(argv)

    paths = list(args.paths)
    if args.source:
        from translator.config import data_root, questions_path

        with questions_path(data_root(args.data)).open(encoding="utf-8") as f:
            qpaths = {json.loads(line)["path"] for line in f if line.strip()}
        paths += sorted(p for p in qpaths if p.split("/")[1] == args.source)
        args.write = True
    if not paths:
        ap.error("give a path or --source")

    if not args.write:
        for p in paths:
            json.dump(translate(p, args.data), sys.stdout, ensure_ascii=False, indent=1)
            print()
        return 0

    from translator.store import write_drawing

    for p in paths:
        out = write_drawing(translate(p, args.data))
        print(f"{p} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
