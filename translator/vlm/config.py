"""Step 9 settings: the ONE price table, the model, generation settings, budget cap and enable flags."""

from __future__ import annotations

# Client chosen by env STOA_VLM: gemini (default) | fake | qwen
CLIENT_ENV = "STOA_VLM"
DEFAULT_CLIENT = "gemini"
API_KEY_ENV = "GEMINI_API_KEY"  # read from the environment only; never written anywhere

# Stable (not preview), newest Flash in the models list on 2026-11-14: "Stable" on
# aistudio.google.com/docs/models, model page "Latest update: September 2026". gemini-2.5-flash is deprecated.
GEMINI_MODEL = "gemini-3.8-flash"

# USD per 1M tokens, paid Standard tier; thinking tokens are billed as output ("including thinking tokens").
# Source: aistudio.google.com/docs/pricing (ai.google.dev/gemini-api/docs/pricing redirects there), read
# 2026-11-14: gemini-3.8-flash input "$0.75 through December 31, 2026." output "$3.75 through December 31,
# 2026." (both double from January 1, 2027: update this table then). Image inputs use the same input price.
PRICES_USD_PER_MTOK = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
    "fake": {"input": 0.0, "output": 0.0},
}

# Generation settings are part of the cache key: changing any of them never reuses an old answer.
# Thinking level for gemini-3.8-flash: "low" (k, 2026-11-14): on 3 dev shower images it was 3/3 like the
# default, at $0.0030 vs $0.0091 per image. "minimal" is not supported by this model (its model page).
THINKING_LEVEL = "low"

# Temperature stays at the model default (Gemini 3 guidance); every answer is cached, so runs repeat.
GENERATION = {"response_mime_type": "application/json"}

BUDGET_CAP_USD = 1.00  # k's cap for this run
BUDGET_HARD_MAX_USD = 1.50  # a cap above this needs k's explicit approval (approved_over_hard_max=True)

# Step-e candidate, saved for later (k, 2026-11-14), NOT run yet: pages whose long side is under
# UPSCALE_BELOW_PX are upscaled to UPSCALE_TO_PX before sending (same token cost per image). None = off.
UPSCALE_BELOW_PX: int | None = None  # candidate: 1000
UPSCALE_TO_PX = 1280

MAX_SIDE_PX = 1536  # longest side of an image sent to the model (downscaled, never upscaled)
CROP_PAD_FRAC = 0.15  # padding around a room crop, as a share of the room's longest side
CROP_MIN_PAD_PX = 24

# Confidence of a VLM finding: a fixed prior, NOT calibrated (no per-answer probability from the API).
VLM_CONFIDENCE = 0.6

# Write-back per category (k decides per category from dev scores). shower: ON (k, after stage 1: dev
# showers+baths 9.1 -> 68.8; always-1 baseline 72.0).
WRITEBACK_ENABLED = {"shower": True, "kitchen": False, "bathroom": False, "floors": False,
                     "doors": False, "windows": False, "rooms": False}  # fmt: skip

# Shower fallback prior (k, from the stage-1 failure list; no API cost): when the VLM says 0 and the page has
# a bathroom, or the page has no wet room so it was never sent, and no shower/bathtub is active, assume 1.
SHOWER_PRIOR = True

# Which saved runs (index/_raw/vlm/outcomes/<run>.jsonl) are applied, in order: a later run's answer for
# the same (image, category) replaces an earlier one. A run that is not listed is never applied.
# stage2 (windows/doors/floors) is listed but its categories stay off in WRITEBACK_ENABLED.
# step_e_variant: e1 answers for its 80-image dev sample (accepted, see docs/TODO.md); later runs win.
OUTCOME_RUNS = ["step_b", "stage1_shower", "stage2", "step_e_variant"]

# Variant used by default for new runs, per category (k accepted e1 for showers after step e).
DEFAULT_VARIANT = {"shower": "e1"}

# Prompt/input variants (step e). e1: upscale small pages + ignore cut-off neighbouring flats.
VARIANTS = {
    "e1": {
        "upscale_below": 1000,
        "extra_prompt": "Count only the main apartment; ignore neighbouring flats cut off at the page edge. ",
    },  # fmt: skip
}

# Total spend cap for all Step 9 work (k's final raise; ledger total incl. estimates). Runs abort above it.
TOTAL_BUDGET_USD = 1.50
