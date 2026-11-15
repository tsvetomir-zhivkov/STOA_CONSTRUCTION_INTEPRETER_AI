"""Run ledger for the pitch: index/_raw/vlm/run_summary.json (git-ignored with index/).

Each run appends a record. Totals are recomputed from the records, plus explicit estimates for spend the API
may have billed without returning token counts (timed-out calls).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from translator.vlm.base import cache_dir

DEV_IMAGES = 193


def path() -> Path:
    return cache_dir() / "run_summary.json"


def load() -> dict:
    p = path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"runs": [], "estimates": []}


def _save(data: dict) -> None:
    logged = sum(r.get("cost_usd", 0.0) for r in data["runs"])
    est = sum(e.get("cost_usd", 0.0) for e in data["estimates"])
    data["totals"] = {"logged_usd": round(logged, 6), "estimated_unlogged_usd": round(est, 6),
                      "total_usd": round(logged + est, 6)}  # fmt: skip
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def append_run(name: str, calls: int, cost_usd: float, images_sent: int, **extra) -> dict:
    """One run: new (non-cached) calls and their logged cost, images sent, plus gate/pitch figures."""
    data = load()
    rec = {"name": name, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "calls": calls,
           "cost_usd": round(cost_usd, 6), "images_sent": images_sent,
           "gate_rate": round(images_sent / DEV_IMAGES, 4),
           "avg_cost_per_gated_image": round(cost_usd / images_sent, 6) if images_sent else None,
           "avg_cost_per_dev_image": round(cost_usd / DEV_IMAGES, 6), **extra}  # fmt: skip
    data["runs"].append(rec)
    _save(data)
    return rec


def add_estimate(note: str, cost_usd: float) -> None:
    data = load()
    data["estimates"].append({"note": note, "cost_usd": cost_usd})
    _save(data)
