"""Prompt texts. The exact text is part of the cache key, so editing a prompt never reuses an old answer."""

from __future__ import annotations

_JSON = (
    'Answer only with JSON {"count": <integer>, "boxes": [[y_min, x_min, y_max, x_max], ...]}: one box per '
    "counted item, coordinates normalised to 0-1000 of THIS image. 0 is a valid answer (then boxes is [])."
)

SHOWER = (
    "This image is <scope> of an architectural floor plan (often Finnish or Swedish). Count every SHOWER and "
    "every BATHTUB drawn in it, together, as one number. A bathtub is a long rectangle, usually with a "
    "rounded inner outline. A shower is a shower tray or shower area: often a square or corner area with a "
    "cross, diagonal lines, a drain, a curved screen or a label such as SUIHKU, SH or DUSCH. Do not count "
    "toilets, sinks, washing machines, sauna benches, sauna stoves or a floor drain alone. " + _JSON
)
SCOPES = {"page": "a whole page", "crop": "a crop around one wet room (bathroom, WC or sauna)"}


def shower(scope: str) -> str:
    return SHOWER.replace("<scope>", SCOPES[scope])
