"""Run the VLM check over gated dev images: resumable, in priority order, inside a budget.

  python -m translator.vlm.run [--images a,b] [--gates shower,prior,no_evidence,floors] [--cap 1.0]
                               [--dry-run --per-call 0.003] [--debug] [--name RUN]

Tasks are (image, category) pairs from translator.vlm.gate, ordered by gate priority (shower, prior,
no_evidence, floors; win_door only when asked), then by image name, so an abort keeps the most important
answers. Every answer is cached by the client (index/_raw/vlm/<key>.json), so a rerun after an abort resumes
for free. Outcomes go to index/_raw/vlm/outcomes/<run>.jsonl; the ledger gets one record per run.
Dev only: every image and index document passes the hold-out guard before it is read.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from translator.vlm import gate, images, ledger, prompts
from translator.vlm.base import BaseClient, Budget, BudgetExceeded, cache_dir, get_client, guard_source

DEFAULT_GATES = ("shower", "prior", "no_evidence", "floors")
SUPPORTED = {"shower", "kitchen", "bathroom", "floors", "doors", "windows"}


@dataclass(frozen=True)
class Task:
    source: str
    category: str
    gate: str


def dev_sources() -> list[str]:
    from translator.extract import cubicasa

    return [
        f"data/cubicasa5k/{p.name[: -len('.seg.json')]}.png"
        for p in sorted(cubicasa.raw_dir().glob("*.seg.json"))
    ]


def plan(sources: list[str], gates=DEFAULT_GATES, index_dir: Path | None = None) -> list[Task]:
    """All tasks for these images, in gate priority order. One task per (image, category)."""
    per_gate: dict[str, list[Task]] = {g: [] for g in gate.GATES}
    for src in sorted(sources):
        doc = gate.load_doc(src, index_dir)  # guarded
        for cat, g in gate.tasks(doc, enabled_gates=gates):
            if cat in SUPPORTED:
                per_gate[g].append(Task(src, cat, g))
    return [t for g in gate.GATES for t in per_gate[g]]


def execute(client: BaseClient, task: Task, root=None) -> dict:
    img = images.load_page(task.source, root)  # guarded
    data, crop = images.page_image(img)
    r = client.check(data, task.category, prompts.page_prompt(task.category), source=task.source, crop=crop)
    return {"source": task.source, "category": task.category, "gate": task.gate, "count": r.count,
            "boxes_px": r.boxes_px, "cost_usd": r.raw["cost_usd"], "spent_usd": r.cost_usd,
            "cached": r.cached,
            "key": r.raw["key"], "model": r.model_name, "usage": r.usage, "answer_text": r.raw["answer_text"],
            "sent": {"w": crop.sent_w, "h": crop.sent_h, "scale": crop.scale, "x0": crop.page_x0,
                     "y0": crop.page_y0}}  # fmt: skip


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
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    new = [o for o in out if not o["cached"]]
    imgs = {o["source"] for o in out}
    ledger.append_run(name, calls=len(new), cost_usd=sum(o["spent_usd"] for o in out), images_sent=len(imgs),
                      tasks=len(out), tasks_planned=len(tasks), cached=len(out) - len(new),
                      model=client.model_name, settings=client.settings, aborted=abort)  # fmt: skip
    return out, abort


def load_outcomes() -> dict[tuple[str, str], dict]:
    """Latest outcome per (source, category) over every saved run (index/_raw/vlm/outcomes/*.jsonl)."""
    out: dict[tuple[str, str], dict] = {}
    for p in sorted((cache_dir() / "outcomes").glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                o = json.loads(line)
                out[(o["source"], o["category"])] = o
    return out


def apply_to_index(out_dir: Path, enable: set[str], sources: list[str] | None = None) -> dict:
    """Write every dev CubiCasa document, with VLM outcomes of the enabled categories written back, to
    out_dir/cubicasa5k/ (schema-validated). index/ itself is never modified. -> counts per category."""
    from translator.store import write_drawing
    from translator.vlm import writeback

    outcomes = load_outcomes()
    cats = (*writeback.ROOM_CATS, *writeback.OBJECT_CATS, *writeback.OPENING_CATS, "floors")
    enabled = {c: c in enable for c in cats}
    applied: dict[str, int] = {}
    for src in sources or dev_sources():
        doc = gate.load_doc(src)  # guarded
        for (s, cat), o in outcomes.items():
            if s == src and enabled.get(cat):
                writeback.apply(doc, o, enabled=enabled)
                applied[cat] = applied.get(cat, 0) + 1
        write_drawing(doc, out_dir)
    return applied


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", help="comma-separated image stems (default: every dev image with a raw file)")
    ap.add_argument("--gates", default=",".join(DEFAULT_GATES))
    ap.add_argument("--cap", type=float, default=None, help="budget cap in USD (default config, max 1.50)")
    ap.add_argument("--dry-run", action="store_true", help="plan and estimate only; no call")
    ap.add_argument("--per-call", type=float, default=0.003, help="USD per call for the estimate")
    ap.add_argument("--debug", action="store_true", help="overlays + write-back preview to index/debug_vlm/")
    ap.add_argument("--name", default="run")
    ap.add_argument("--apply-to", help="write dev documents with VLM write-back to this index dir; no call")
    ap.add_argument("--enable", default="", help="categories to write back with --apply-to, e.g. shower")
    args = ap.parse_args(argv)
    if args.apply_to:
        applied = apply_to_index(Path(args.apply_to), set(filter(None, args.enable.split(","))))
        print(f"wrote dev documents to {args.apply_to}; write-back applied: {applied}")
        return 0
    sources = [f"data/cubicasa5k/{s}.png" for s in args.images.split(",")] if args.images else dev_sources()
    tasks = plan(sources, tuple(args.gates.split(",")))
    by = {}
    for t in tasks:
        by[(t.gate, t.category)] = by.get((t.gate, t.category), 0) + 1
    print(f"{len(tasks)} tasks over {len({t.source for t in tasks})} images: {by}")
    if args.dry_run:
        print(f"estimate at ${args.per_call}/call: ${len(tasks) * args.per_call:.2f}")
        return 0
    client = get_client(budget=Budget(args.cap) if args.cap else Budget())
    outcomes, abort = run(tasks, client, args.name)
    for o in outcomes:
        print(f"{o['source']} {o['category']} ({o['gate']}): count {o['count']} ${o['cost_usd']:.5f}"
              f"{' cached' if o['cached'] else ''}")  # fmt: skip
    print(
        f"spent ${client.budget.spent_usd:.4f} on {client.budget.calls} new calls"
        + (f"; ABORTED: {abort}" if abort else "")
    )
    if args.debug:
        from translator.vlm import debug

        for p in debug.write(outcomes):
            print("debug:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
