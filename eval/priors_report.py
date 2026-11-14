"""Priors and VLM, honestly: every CubiCasa template under each configuration (dev split only).

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
    cat = "shower" if template == "cc_showers_baths" else fuse.PRIORS[template]["category"]
    return any(
        c["category"] == cat and c["region"] == "page" and c.get("note", "").startswith("prior:")
        for c in doc["coverage"]
    )


# Configurations, left to right. VLM answers come from the cache only (no network); write-back per
# translator.vlm.config.WRITEBACK_ENABLED (shower on).
CONFIGS = {
    "pipeline_%": {"priors": False, "vlm": False},
    "count_priors_%": {"priors": True, "vlm": False},
    "vlm_%": {"priors": True, "vlm": True, "shower_prior": False},
    "vlm+shower_prior_%": {"priors": True, "vlm": True, "shower_prior": True},
}


def build(paths: list[str], root: str, fuse_params: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for p in paths:
        for doc in translate_all(p, root, fuse_params=fuse_params):
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

    rows, last_index, last_df = {}, None, None
    for col, fp in CONFIGS.items():
        index = build(paths, str(root), fp)
        df = score.score(cc_q, index, holdout, "dev")
        df = df[df.status != "text"]
        for intent, g in df.groupby("intent"):
            rows.setdefault(intent, {"n": len(g)})[col] = round(100 * (g.status == "correct").mean(), 1)
        last_index, last_df = index, df
    table = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    # the honest baseline: always answer the template's dev majority
    maj = []
    for intent in table.index:
        answers = last_df[last_df.intent == intent].answer.astype(float)
        maj.append(round(100 * (answers == answers.mode().iloc[0]).mean(), 1) if len(answers) else None)
    table["always_majority_%"] = maj
    table["prior_fired"] = ""
    for t in (*fuse.PRIORS, "cc_showers_baths"):
        asked = set(last_df[last_df.intent == t].path)
        n = sum(fired(d, t) for f in asked for d in last_index.get(f, []))
        table.loc[t, "prior_fired"] = f"{n}/{len(asked)} images"
    print(f"dev split, {len(paths)} CubiCasa images. Columns: pipeline only; + kitchen/bathroom priors;")
    print("+ cached VLM write-back (enabled categories); + shower fallback prior; always-majority baseline.")
    print("prior_fired = images whose answer is an assumed prior (last configuration)")
    print()
    print(table.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
