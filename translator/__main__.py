"""CLI: python -m translator <path> [...] [--write] [--source NAME] [--data DATASET_ROOT] [--allow-holdout]

Without --write the document is printed. With --write it is validated and written to index/.
--source NAME translates every questions.jsonl file of that source (implies --write).

Hold-out files (eval/holdout_files.txt) are refused by default, by the same filename guard as translator/vlm
(normalised path, basename, stem), before any file is read: an explicit hold-out path exits with an error, and
--source skips them. --allow-holdout overrides this and prints a warning.
"""

from __future__ import annotations

import argparse
import json
import sys

from translator import translate_all


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
    ap.add_argument(
        "--allow-holdout", action="store_true", help="translate hold-out files too (prints a warning)"
    )
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

    from translator.vlm.base import HoldoutError, default_guard

    held = []
    for p in paths:
        try:
            default_guard().check_source(p)
        except HoldoutError:
            held.append(p)
    if held and args.allow_holdout:
        print(f"WARNING: --allow-holdout: translating {len(held)} hold-out file(s): {held}", file=sys.stderr)
    elif held and args.source:
        explicit = [p for p in held if p in args.paths]
        if explicit:
            print(f"refused: hold-out file(s) {explicit} (use --allow-holdout to override)", file=sys.stderr)
            return 2
        paths = [p for p in paths if p not in held]
        print(f"skipped {len(held)} hold-out file(s) of source {args.source}", file=sys.stderr)
    elif held:
        print(f"refused: hold-out file(s) {held} (use --allow-holdout to override)", file=sys.stderr)
        return 2

    if not args.write:
        for p in paths:
            for doc in translate_all(p, args.data, allow_holdout=args.allow_holdout):
                json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)
                print()
        return 0

    from translator.store import write_drawing

    for p in paths:
        for doc in translate_all(p, args.data, allow_holdout=args.allow_holdout):
            out = write_drawing(doc)
            print(f"{p} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
