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
# Temperature stays at the model default (Gemini 3 guidance); every answer is cached, so runs repeat.
GENERATION = {"response_mime_type": "application/json"}

BUDGET_CAP_USD = 1.00  # k's cap for this run
BUDGET_HARD_MAX_USD = 1.50  # a cap above this needs k's explicit approval (approved_over_hard_max=True)

MAX_SIDE_PX = 1536  # longest side of an image sent to the model (downscaled, never upscaled)
CROP_PAD_FRAC = 0.15  # padding around a room crop, as a share of the room's longest side
CROP_MIN_PAD_PX = 24

# Write-back per category: only enabled once it beats the pipeline on dev (decided after step d).
WRITEBACK_ENABLED = {"shower": False, "kitchen": False, "bathroom": False, "floors": False,
                     "doors": False, "windows": False, "rooms": False}  # fmt: skip
