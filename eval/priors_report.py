"""Priors, VLM and baselines, honestly: every dev template under each configuration (dev split only).

  python eval/priors_report.py [--data DATASET_ROOT] [--write-md docs/EVAL_TABLE.md]

CubiCasa documents are translated in memory for each configuration (VLM answers from the cache only; the
API is never called). Swiss / IFC documents are read from index/<source>/ (they do not depend on these
switches). Baseline = always answer the template's most common dev answer, scored with eval/score.py's
rules. With --write-md the table, totals, VLM cost figures and the disclosures are written to a markdown file
(docs/EVAL_TABLE.md), so every number there can be reproduced with this one command.
"""

# ruff: noqa: E501  (long prose lines of the generated markdown and its disclosures)
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent))
sys.path.insert(0, str(EVAL_DIR))

import pandas as pd  # noqa: E402
import score  # noqa: E402
import split  # noqa: E402

from translator import fuse, translate_all  # noqa: E402
from translator.config import INDEX_DIR, data_root  # noqa: E402
from translator.extract import cubicasa  # noqa: E402
from translator.store import read_drawing  # noqa: E402

# Configurations, left to right (CubiCasa only; other sources have one column).
CONFIGS = {
    "pipeline": {"priors": False, "vlm": False},
    "count_priors": {"priors": True, "vlm": False},
    "vlm": {"priors": True, "vlm": True, "shower_prior": False},
    "final": {"priors": True, "vlm": True, "shower_prior": True},  # + shower fallback prior
}
OTHER_SOURCES = ("swiss_dwellings", "ifc_models", "ifc_bench")


def fired(doc: dict, template: str) -> bool:
    cat = "shower" if template == "cc_showers_baths" else fuse.PRIORS[template]["category"]
    return any(
        c["category"] == cat and c["region"] == "page" and c.get("note", "").startswith("prior:")
        for c in doc["coverage"]
    )


def cubicasa_index(paths: list[str], root: str, fuse_params: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for p in paths:
        for doc in translate_all(p, root, fuse_params=fuse_params):
            index[doc["drawing"]["file"]].append(doc)
    return index


def other_index() -> dict[str, list[dict]]:
    """Swiss / IFC documents from index/<source>/ only (never index/debug_*)."""
    index: dict[str, list[dict]] = defaultdict(list)
    for src in OTHER_SOURCES:
        for p in sorted((INDEX_DIR / src).glob("*.json")):
            doc = read_drawing(p)
            index[doc["drawing"]["file"]].append(doc)
    return index


def baseline(g: pd.DataFrame) -> float:
    """Always answer the most common dev answer of this template (official scoring rule)."""
    mode = g.answer.astype(float).mode().iloc[0]
    hits = sum(score.is_correct(t, a, mode) for t, a in zip(g.type, g.answer, strict=True))
    return round(100 * hits / len(g), 1)


def build_table(root: Path) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    questions = score.load_questions(root)
    holdout = split.load_or_make([q["path"] for q in questions])
    dev = [q for q in questions if q["path"] not in holdout]
    paths = [p for p in cubicasa.dev_images(root) if cubicasa.raw_path(Path(p).stem).exists()]
    assert not set(paths) & holdout, "hold-out image in the dev list"
    others = other_index()
    results, last_cc = {}, {}
    for col, fp in CONFIGS.items():
        idx = defaultdict(list, others)
        cc = cubicasa_index(paths, str(root), fp)
        idx.update(cc)
        results[col] = score.score(dev, idx, holdout, "dev")
        last_cc = cc
    final = results["final"]
    rows = []
    for (src, intent), g in final[final.status != "text"].groupby(["source", "intent"]):
        r = {"source": src, "template": intent, "n": len(g)}
        for col, df in results.items():
            gg = df[(df.source == src) & (df.intent == intent)]
            r[col] = round(100 * (gg.status == "correct").mean(), 1)
        r["baseline"] = baseline(g)
        rows.append(r)
    fired_n = {}
    for t in (*fuse.PRIORS, "cc_showers_baths"):
        asked = set(final[final.intent == t].path)
        fired_n[t] = (sum(fired(d, t) for f in asked for d in last_cc.get(f, [])), len(asked))
    return pd.DataFrame(rows), fired_n, final


def vlm_costs() -> dict:
    """Spend from the ledger, and the cost of the answers actually applied (config.OUTCOME_RUNS)."""
    from translator.vlm import config as vconfig
    from translator.vlm import ledger, run

    led = ledger.load()
    applied = [o for (s, c), o in run.load_outcomes().items() if vconfig.WRITEBACK_ENABLED.get(c)]
    images = {o["source"] for o in applied}
    cost = sum(o["cost_usd"] for o in applied)
    return {
        "total_spend_usd": led["totals"]["total_usd"],
        "logged_usd": led["totals"]["logged_usd"],
        "estimated_unlogged_usd": led["totals"]["estimated_unlogged_usd"],
        "runs": [(r["name"], r["calls"], r["cost_usd"]) for r in led["runs"]],
        "applied_answers": len(applied),
        "applied_images": len(images),
        "applied_cost_usd": round(cost, 4),
        "per_gated_image_usd": round(cost / len(images), 5) if images else None,
        "per_dev_image_usd": round(cost / 193, 5),
        "model": vconfig.GEMINI_MODEL,
        "thinking": vconfig.THINKING_LEVEL,
    }


def totals(final: pd.DataFrame) -> dict:
    scored = final[final.status != "text"]
    sc = scored[scored.status != "no_template"]
    return {
        "all": (int((scored.status == "correct").sum()), len(scored)),
        "scoreable": (int((sc.status == "correct").sum()), len(sc)),
        "no_template": scored[scored.status == "no_template"].groupby("source").size().to_dict(),
        "text": final[final.status == "text"].groupby("source").size().to_dict(),
    }


DISCLOSURES = [
    "**Always-majority baseline**: kitchens and bathrooms only equal it (their output is mostly the count "
    "prior, flagged `assumed_prior` with coverage `partial`). Showers + baths is reported next to its always-1 "
    "baseline everywhere.",
    '**Priors are flagged**: every assumed element has `flags: ["assumed_prior"]`, no geometry, confidence 0.2, '
    'provenance `prior`; its coverage record is `partial` with a note "prior: assumed N, not read from the '
    'drawing". The agent must never highlight it as evidence.',
    "**Dev-fitted choices** (they will cost accuracy on hold-out): the count priors themselves (kitchens 1, bathrooms 1: dev majority answers) and the shower fallback prior (dev majority 1; rule taken from the stage-1 failure list); the strong-signal rule for the count priors "
    "(found on 2 dev images); MIN_ICON_PX = 46 (from a dev sweep); the e1 shower-prompt sample was drawn using "
    "dev labels (32 wrong + 40 right small pages + 8 never-gated pages).",
    "**The dev shower score mixes two prompts**: 80 pages have e1 answers, the other ~103 gated pages the "
    "original prompt's answers (re-running them was over budget).",
    "**Principled fixes** (justified without dev labels, found while reading dev failures): the sauna fallback "
    'from the sauna bench icon; H / huone as generic room labels (reverses the Step-7 decision "H = bedroom").',
    "**high_quality_architectural-5006**: a one-off raster test on a hold-out image (approved by k) in Step 5; "
    "its outputs sit in `index/debug_jpg_test/` and are never scored.",
    "**Hold-out hash code**: an early version of the VLM hold-out guard read the hold-out files' bytes to hash "
    "them (in memory only, in two smoke-test processes); no hash was written anywhere and the code was removed. "
    "The guard now uses names only.",
    "**Swiss**: 10 dev questions fail on two floors whose area sums include a numbered room without a name "
    "(and no written areas); not fixed, a rule would be fitted to 2 floors.",
    "VLM confidence 0.6 is a fixed value, not calibrated; VLM boxes are approximate (`properties.box_quality`).",
]


def markdown(table: pd.DataFrame, fired_n: dict, final: pd.DataFrame, costs: dict) -> str:
    t = totals(final)
    (ok_a, n_a), (ok_s, n_s) = t["all"], t["scoreable"]
    out = [
        "# Evaluation table (dev split)",
        "",
        "Generated by `python eval/priors_report.py --write-md docs/EVAL_TABLE.md` on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC. Dev split only: hold-out files are never "
        "opened or scored. VLM answers come from the cache; generating this table makes no API call.",
        "",
        "**Expect lower accuracy on the hold-out split than on dev**: several rules and thresholds were chosen "
        "while looking at dev results (see Disclosures).",
        "",
        "## Totals",
        "",
        f"- All dev questions with a numeric answer: **{ok_a}/{n_a} = {100 * ok_a / n_a:.1f}%** (questions "
        f"without a template count as failures: {t['no_template']}).",
        f"- Scoreable questions only (a template exists): **{ok_s}/{n_s} = {100 * ok_s / n_s:.1f}%**.",
        f"- Not scored: free-text questions {t['text']} (no automatic scorer).",
        "",
        "## Per source and template",
        "",
        "CubiCasa columns: pipeline only; + kitchen/bathroom count priors; + VLM write-back (enabled: showers); "
        "+ shower fallback prior (final). Other sources do not use these switches (one value).",
        "",
        "| source | template | questions | pipeline | + count priors | + VLM | final | always-majority | "
        "prior fired |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in table.sort_values(["source", "template"]).to_dict("records"):
        f = fired_n.get(r["template"])
        fired_s = f"{f[0]}/{f[1]} images" if f else ""
        mid = (f"{r['pipeline']} | {r['count_priors']} | {r['vlm']}" if r["source"] == "cubicasa5k"
               else "— | — | —")  # fmt: skip
        out.append(f"| {r['source']} | {r['template']} | {r['n']} | {mid} | **{r['final']}** | {r['baseline']} | "
                   f"{fired_s} |")  # fmt: skip
    for src, g in table.groupby("source"):
        out.append(
            f"| {src} | **all** | {g.n.sum()} | | | | **{(g.final * g.n).sum() / g.n.sum():.1f}** | | |"
        )
    c = costs
    out += [
        "",
        "## VLM cost (Step 9)",
        "",
        f"- Model `{c['model']}`, thinking level `{c['thinking']}`; prices in `translator/vlm/config.py`.",
        f"- Total spend, all runs and experiments: **${c['total_spend_usd']:.3f}** (${c['logged_usd']:.3f} logged "
        f"+ ${c['estimated_unlogged_usd']:.3f} estimated for a timed-out call).",
        f"- Answers applied in the index: {c['applied_answers']} on {c['applied_images']} images, "
        f"${c['applied_cost_usd']:.3f}: **${c['per_gated_image_usd']:.4f} per gated image**, "
        f"**${c['per_dev_image_usd']:.4f} per dev image** (193 CubiCasa dev images). Target < $0.002 per dev "
        "image: not met.",
        "- Runs: " + "; ".join(f"{n} ({k} calls, ${v:.3f})" for n, k, v in c["runs"]) + ".",
        "",
        "## Disclosures",
        "",
        *[f"- {d}" for d in DISCLOSURES],
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="dataset root (has questions.jsonl and data/); default $STOA_DATA")
    ap.add_argument("--write-md", help="write the markdown table (e.g. docs/EVAL_TABLE.md)")
    args = ap.parse_args(argv)
    table, fired_n, final = build_table(data_root(args.data))
    with pd.option_context("display.max_rows", 200, "display.width", 250):
        print(table.to_string(index=False))
    print("prior fired:", json.dumps(fired_n))
    print("totals:", json.dumps(totals(final)))
    if args.write_md:
        Path(args.write_md).write_text(markdown(table, fired_n, final, vlm_costs()), encoding="utf-8")
        print(f"wrote {args.write_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
