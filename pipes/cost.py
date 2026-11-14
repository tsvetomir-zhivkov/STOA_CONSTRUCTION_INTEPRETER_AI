"""Cost of an agent call from its token usage and the model's list price (USD per 1M tokens).

gemini-3.8-flash, Gemini Developer API, standard tier (https://ai.google.dev/gemini-api/docs/pricing, checked
2026-11-15): input $0.75 and output $3.75 per 1M tokens through 2026-12-31 (introductory), $1.50 and $7.50 from
2027-01-01. Thinking tokens are billed as output tokens. Batch, priority and context-caching tiers are not used here.
Prices change: re-check the page before quoting a number in the pitch.
"""

from __future__ import annotations

from datetime import date

PRICES_USD_PER_M: dict[str, list[dict]] = {
    "gemini-3.8-flash": [
        {"until": date(2026, 12, 31), "input": 0.75, "output": 3.75},
        {"until": None, "input": 1.50, "output": 7.50},
    ],
}


def price(model: str, on: date | None = None) -> dict | None:
    on = on or date.today()
    for tier in PRICES_USD_PER_M.get(model, []):
        if tier["until"] is None or on <= tier["until"]:
            return tier
    return None


def cost_usd(usage: dict | None, model: str | None, on: date | None = None) -> float | None:
    """USD for one call; None when the model has no known price or there is no usage. Thinking counts as output."""
    p = price(model, on) if model else None
    if not usage or p is None:
        return None
    tokens_in = usage.get("input") or 0
    tokens_out = (usage.get("output") or 0) + (usage.get("thinking") or 0)
    return (tokens_in * p["input"] + tokens_out * p["output"]) / 1_000_000
