"""Score the index against questions.jsonl with the deterministic probe.

Rules: counts exact; area/length/volume within 2%; text questions are not scored.
Prints accuracy per source x intent and writes failures to eval/failures.jsonl.

  python eval/score.py [--data DATASET_ROOT] [--index INDEX_DIR] [--split dev|holdout|all]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent))
sys.path.insert(0, str(EVAL_DIR))

import pandas as pd  # noqa: E402
import probe  # noqa: E402
import split  # noqa: E402

from translator.config import INDEX_DIR, data_root, questions_path  # noqa: E402
from translator.store import read_drawing  # noqa: E402

MEASURE_TOL = 0.02
FAILURES = EVAL_DIR / "failures.jsonl"
FAIL_STATUSES = ("wrong", "no_answer", "no_index", "no_template")


def load_questions(root: Path) -> list[dict]:
    qp = questions_path(root)
    if not qp.exists():
        raise SystemExit(f"questions.jsonl not found at {qp}. Set STOA_DATA or pass --data.")
    with qp.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_index(index_dir: Path) -> dict[str, list[dict]]:
    """drawing.file -> documents. Invalid documents raise (fail loudly)."""
    docs: dict[str, list[dict]] = defaultdict(list)
    if not index_dir.exists():
        return docs
    for p in sorted(index_dir.rglob("*.json")):
        if "_raw" in p.relative_to(index_dir).parts:
            continue
        doc = read_drawing(p)
        docs[doc["drawing"]["file"]].append(doc)
    return docs


def is_correct(qtype: str, expected, got) -> bool:
    if got is None or isinstance(got, bool):
        return False
    try:
        got = float(got)
    except (TypeError, ValueError):
        return False
    if qtype == "count":
        return got == float(expected)
    if expected == 0:
        return got == 0
    return abs(got - float(expected)) <= MEASURE_TOL * abs(float(expected))


def score(questions: list[dict], index: dict, holdout: set[str], which: str = "dev") -> pd.DataFrame:
    rows = []
    for q in questions:
        path = q["path"]
        in_holdout = path in holdout
        if (which == "dev" and in_holdout) or (which == "holdout" and not in_holdout):
            continue
        source = path.split("/")[1]
        if q["type"] == "text":
            rows.append(dict(q, source=source, intent="(text, not scored)", status="text", got=None))
            continue
        docs = index.get(path, [])
        intent, got = probe.answer(docs, source, q["question"])
        if intent is None:
            status, intent = "no_template", "(no template)"
        elif not docs:
            status = "no_index"
        elif got is None:
            status = "no_answer"
        else:
            status = "correct" if is_correct(q["type"], q["answer"], got) else "wrong"
        rows.append(dict(q, source=source, intent=intent, status=status, got=got))
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> str:
    if df.empty:
        return "no questions in this split"
    scored = df[df.status != "text"].assign(correct=lambda d: d.status == "correct")
    agg = dict(n=("correct", "size"), correct=("correct", "sum"))
    table = scored.groupby(["source", "intent"]).agg(**agg).reset_index()
    by_src = scored.groupby("source").agg(**agg).reset_index().assign(intent="== ALL ==")
    total = pd.DataFrame(
        [{"source": "~TOTAL", "intent": "", "n": len(scored), "correct": int(scored.correct.sum())}]
    )
    table = pd.concat([table, by_src, total], ignore_index=True)
    table["acc_%"] = (100 * table.correct / table.n).round(1)
    table = table.sort_values(["source", "intent"]).reset_index(drop=True)
    status = df.groupby(["source", "status"]).size().unstack(fill_value=0)
    with pd.option_context("display.max_rows", 500, "display.width", 200):
        return f"{table.to_string(index=False)}\n\nStatus counts:\n{status.to_string()}"


def write_failures(df: pd.DataFrame, path: Path) -> int:
    fails = df[df.status.isin(FAIL_STATUSES)] if not df.empty else df
    with open(path, "w", encoding="utf-8") as f:
        for r in fails.itertuples():
            rec = dict(
                path=r.path,
                source=r.source,
                intent=r.intent,
                status=r.status,
                question=r.question,
                expected=r.answer,
                got=r.got,
            )
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(fails)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="dataset root (has questions.jsonl and data/); default $STOA_DATA")
    ap.add_argument("--index", default=str(INDEX_DIR), help="index directory (default: index/)")
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    ap.add_argument("--failures", default=str(FAILURES))
    args = ap.parse_args(argv)

    questions = load_questions(data_root(args.data))
    holdout = split.load_or_make([q["path"] for q in questions])
    index = load_index(Path(args.index))
    df = score(questions, index, holdout, args.split)

    n_files = df.path.nunique() if not df.empty else 0
    print(f"split={args.split}  questions={len(df)}  files={n_files}  indexed files={len(index)}\n")
    print(report(df))
    n = write_failures(df, Path(args.failures))
    print(f"\n{n} failures -> {args.failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
