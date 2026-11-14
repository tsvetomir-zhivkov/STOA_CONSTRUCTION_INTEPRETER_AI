"""Count priors, honestly: every CubiCasa template scored with the priors off and on (dev split only).

Translates every dev CubiCasa image that has a raw segmentation file twice, in memory (no GPU, nothing
written), scores both with eval/score.py's rules, and counts per prior template the images where the prior
fired (detected 0, or a different count without two strong signals), among the images asked about it.

  python eval/priors_report.py [--data DATASET_ROOT]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent))
sys.path.insert(0, str(EVAL_DIR))

import pandas as pd  # noqa: E402
import score  # noqa: E402
import split  # noqa: E402

from translator import fuse, translate_all  # noqa: E402
from translator.config import data_root  # noqa: E402
from translator.extract import cubicasa  # noqa: E402


def fired(doc: dict, template: str) -> bool:
    cat = fuse.PRIORS[template]["category"]
    return any(
        c["category"] == cat and c["region"] == "page" and c.get("note", "").startswith("prior:")
        for c in doc["coverage"]
    )


def build(paths: list[str], root: str, priors: bool) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for p in paths:
        for doc in translate_all(p, root, fuse_params={"priors": priors}):
            index[doc["drawing"]["file"]].append(doc)
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="dataset root (has questions.jsonl and data/); default $STOA_DATA")
    args = ap.parse_args(argv)
    root = data_root(args.data)
    questions = score.load_questions(root)
    holdout = split.load_or_make([q["path"] for q in questions])
    cc_q = [q for q in questions if q["path"].startswith("data/cubicasa5k/") and q["path"] not in holdout]
    paths = [p for p in cubicasa.dev_images(root) if cubicasa.raw_path(Path(p).stem).exists()]
    assert not set(paths) & holdout, "hold-out image in the dev list"

    rows, on_index = {}, None
    for priors in (False, True):
        index = build(paths, str(root), priors)
        df = score.score(cc_q, index, holdout, "dev")
        df = df[df.status != "text"]
        for intent, g in df.groupby("intent"):
            rows.setdefault(intent, {"n": len(g)})[f"priors_{'on' if priors else 'off'}_%"] = round(
                100 * (g.status == "correct").mean(), 1
            )
        if priors:
            on_index, on_df = index, df
    table = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    table["fired_images"] = ""
    for t in fuse.PRIORS:
        asked = set(on_df[on_df.intent == t].path)
        n = sum(fired(d, t) for f in asked for d in on_index.get(f, []))
        table.loc[t, "fired_images"] = f"{n}/{len(asked)}"
    print(f"dev split, {len(paths)} CubiCasa images; fired = prior used instead of the detected count\n")
    print(table.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
