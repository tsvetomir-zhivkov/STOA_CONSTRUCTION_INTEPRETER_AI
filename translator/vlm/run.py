"""Run the VLM check over gated dev images: resumable, in priority order, inside a budget.

  python -m translator.vlm.run [--images a,b | --images-file F] [--gates shower,prior,no_evidence,floors]
                               [--categories windows,doors] [--variant e1] [--dry-run --per-call 0.005]
                               [--debug] [--name RUN]

Tasks are (image, category) pairs from translator.vlm.gate, ordered by gate priority (shower, prior,
no_evidence, floors; win_door only when asked), then by image name, so an abort keeps the most important
answers. Every answer is cached by the client (index/_raw/vlm/<key>.json), so a rerun after an abort resumes
for free. Outcomes go to index/_raw/vlm/outcomes/<run>.jsonl; the ledger gets one record per run.
Budget: each run's cap = config.TOTAL_BUDGET_USD minus everything spent so far (ledger, incl. estimates).
Dev only: every image and index document passes the hold-out guard before it is read.

Applying answers: translator.fuse.fuse_raster calls apply_cached(), which writes back the cached outcomes
of the runs in config.OUTCOME_RUNS for the enabled categories (no network), then the shower fallback prior.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from translator.vlm import config, gate, images, ledger, prompts
from translator.vlm.base import BaseClient, Budget, BudgetExceeded, cache_dir, get_client, guard_source

DEFAULT_GATES = ("shower", "prior", "no_evidence", "floors")
SUPPORTED = {"shower", "kitchen", "bathroom", "floors", "doors", "windows"}


@dataclass(frozen=True)
class Task:
    source: str
    category: str
    gate: str
    variant: str | None = None


def dev_sources() -> list[str]:
    from translator.extract import cubicasa

    return [
        f"data/cubicasa5k/{p.name[: -len('.seg.json')]}.png"
        for p in sorted(cubicasa.raw_dir().glob("*.seg.json"))
    ]


def plan(
    sources: list[str],
    gates=DEFAULT_GATES,
    index_dir: Path | None = None,
    categories: set[str] | None = None,
    variant: str | None = None,
) -> list[Task]:
    """All tasks for these images, in gate priority order. One task per (image, category)."""
    per_gate: dict[str, list[Task]] = {g: [] for g in gate.GATES}
    for src in sorted(sources):
        doc = gate.load_doc(src, index_dir)  # guarded
        for cat, g in gate.tasks(doc, enabled_gates=gates):
            if cat in SUPPORTED and (categories is None or cat in categories):
                per_gate[g].append(Task(src, cat, g, variant or config.DEFAULT_VARIANT.get(cat)))
    return [t for g in gate.GATES for t in per_gate[g]]


def execute(client: BaseClient, task: Task, root=None) -> dict:
    img = images.load_page(task.source, root)  # guarded
    v = config.VARIANTS[task.variant] if task.variant else {}
    data, crop = images.page_image(img, upscale_below=v.get("upscale_below") or 0)
    prompt = prompts.page_prompt(task.category, v.get("extra_prompt", ""))
    r = client.check(data, task.category, prompt, source=task.source, crop=crop)
    return {"source": task.source, "category": task.category, "gate": task.gate, "variant": task.variant,
            "count": r.count, "boxes_px": r.boxes_px, "cost_usd": r.raw["cost_usd"], "spent_usd": r.cost_usd,
            "cached": r.cached, "key": r.raw["key"], "model": r.model_name, "usage": r.usage,
            "answer_text": r.raw["answer_text"],
            "sent": {"w": crop.sent_w, "h": crop.sent_h, "scale": crop.scale, "x0": crop.page_x0,
                     "y0": crop.page_y0}}  # fmt: skip


def remaining_budget() -> float:
    """config.TOTAL_BUDGET_USD minus everything spent so far (ledger, incl. estimates)."""
    return round(config.TOTAL_BUDGET_USD - ledger.load().get("totals", {}).get("total_usd", 0.0), 6)


def run(tasks: list[Task], client: BaseClient, name: str, root=None) -> tuple[list[dict], str | None]:
    """Execute tasks in order; stop at the budget cap. -> (outcomes, abort reason or None)."""
    out, abort = [], None
    for t in tasks:
        guard_source(t.source)
        try:
            out.append(execute(client, t, root))
        except BudgetExceeded as e:
            abort = str(e)
            break
    folder = cache_dir() / "outcomes"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{name}.jsonl", "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps({**o, "run": name}, ensure_ascii=False) + "\n")
    new = [o for o in out if not o["cached"]]
    imgs = {o["source"] for o in out}
    ledger.append_run(name, calls=len(new), cost_usd=sum(o["spent_usd"] for o in out), images_sent=len(imgs),
                      tasks=len(out), tasks_planned=len(tasks), cached=len(out) - len(new),
                      model=client.model_name, settings=client.settings, aborted=abort)  # fmt: skip
    load_outcomes.cache_clear()
    return out, abort


@lru_cache(maxsize=8)
def _load(runs: tuple[str, ...]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for name in runs:
        p = cache_dir() / "outcomes" / f"{name}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                o = json.loads(line)
                out[(o["source"], o["category"])] = o
    return out


def load_outcomes(runs: list[str] | tuple[str, ...] | None = None) -> dict[tuple[str, str], dict]:
    """Outcome per (source, category) from the given runs (default config.OUTCOME_RUNS); later runs win."""
    return _load(tuple(config.OUTCOME_RUNS if runs is None else runs))


load_outcomes.cache_clear = _load.cache_clear  # type: ignore[attr-defined]


def apply_cached(doc: dict, enabled: dict | None = None, runs=None, shower_prior: bool | None = None) -> dict:
    """Write back the cached VLM outcomes for this document (no network), then the shower fallback prior."""
    from translator.vlm import writeback

    enabled = config.WRITEBACK_ENABLED if enabled is None else enabled
    src = doc["drawing"]["file"]
    guard_source(src)
    summary = {}
    shower_out = None
    for (s, cat), o in load_outcomes(runs).items():
        if s == src and enabled.get(cat):
            summary[cat] = writeback.apply(doc, o, enabled=enabled)
            if cat == "shower":
                shower_out = o
    use_prior = config.SHOWER_PRIOR if shower_prior is None else shower_prior
    if use_prior and enabled.get("shower"):
        summary["shower_prior"] = writeback.shower_prior(doc, shower_out)
    return summary


def apply_to_index(out_dir: Path, enable: set[str], sources: list[str] | None = None, runs=None,
                   shower_prior: bool | None = None) -> dict:  # fmt: skip
    """Write every dev CubiCasa document re-translated with VLM write-back for the enabled categories to
    out_dir/cubicasa5k/ (schema-validated). index/ itself is never modified. -> counts per category."""
    from translator import translate_all
    from translator.store import write_drawing

    cats = ("kitchen", "bathroom", "shower", "doors", "windows", "floors")
    fp = {"vlm_enabled": {c: c in enable for c in cats}, "vlm_runs": runs, "shower_prior": shower_prior}
    applied: dict[str, int] = {}
    for src in sources or dev_sources():
        guard_source(src)
        for doc in translate_all(src, fuse_params=fp):
            for h in doc["history"]:
                if h["extractor"] in ("vlm_check", "prior") and h["action"].startswith(
                    ("check_", "prior_shower")
                ):
                    applied[h["action"]] = applied.get(h["action"], 0) + 1
            write_drawing(doc, out_dir)
    return applied


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", help="comma-separated image stems (default: every dev image with a raw file)")
    ap.add_argument("--images-file", help="file with one image stem per line")
    ap.add_argument("--gates", default=",".join(DEFAULT_GATES))
    ap.add_argument("--categories", help="only these categories, e.g. windows,doors,floors")
    ap.add_argument("--variant", help="prompt/input variant from config.VARIANTS, e.g. e1")
    ap.add_argument("--dry-run", action="store_true", help="plan and estimate only; no call")
    ap.add_argument("--per-call", type=float, default=0.005, help="USD per call for the estimate")
    ap.add_argument("--debug", action="store_true", help="overlays + write-back preview to index/debug_vlm/")
    ap.add_argument("--name", default="run")
    ap.add_argument("--apply-to", help="write dev documents with VLM write-back to this index dir; no call")
    ap.add_argument("--enable", default="", help="categories to write back with --apply-to, e.g. shower")
    ap.add_argument("--runs", help="outcome runs to apply with --apply-to (default config.OUTCOME_RUNS)")
    ap.add_argument(
        "--no-shower-prior", action="store_true", help="with --apply-to: no shower fallback prior"
    )
    args = ap.parse_args(argv)
    if args.apply_to:
        runs = args.runs.split(",") if args.runs else None
        applied = apply_to_index(Path(args.apply_to), set(filter(None, args.enable.split(","))), runs=runs,
                                 shower_prior=False if args.no_shower_prior else None)  # fmt: skip
        print(f"wrote dev documents to {args.apply_to}; write-back: {applied}")
        return 0
    stems = args.images.split(",") if args.images else None
    if args.images_file:
        stems = [
            s.strip() for s in Path(args.images_file).read_text(encoding="utf-8").splitlines() if s.strip()
        ]
    sources = [f"data/cubicasa5k/{s}.png" for s in stems] if stems else dev_sources()
    cats = set(args.categories.split(",")) if args.categories else None
    tasks = plan(sources, tuple(args.gates.split(",")), categories=cats, variant=args.variant)
    by = {}
    for t in tasks:
        by[(t.gate, t.category)] = by.get((t.gate, t.category), 0) + 1
    left = remaining_budget()
    print(f"{len(tasks)} tasks over {len({t.source for t in tasks})} images: {by}; budget left ${left:.4f}")
    if args.dry_run:
        print(f"estimate at ${args.per_call}/call: ${len(tasks) * args.per_call:.2f}")
        return 0
    if left <= 0:
        print(f"ABORTED: total budget ${config.TOTAL_BUDGET_USD} already spent")
        return 1
    client = get_client(budget=Budget(left))
    outcomes, abort = run(tasks, client, args.name)
    for o in outcomes:
        print(f"{o['source']} {o['category']} ({o['gate']}): count {o['count']} ${o['cost_usd']:.5f}"
              f"{' cached' if o['cached'] else ''}")  # fmt: skip
    print(
        f"spent ${client.budget.spent_usd:.4f} on {client.budget.calls} new calls; total left "
        f"${remaining_budget():.4f}" + (f"; ABORTED: {abort}" if abort else "")
    )
    if args.debug:
        from translator.vlm import debug

        for p in debug.write(outcomes):
            print("debug:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
