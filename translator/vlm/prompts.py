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


_PLAN = "This image is a whole page of an architectural floor plan (often Finnish or Swedish). "

KITCHEN = (
    _PLAN + "Count the KITCHENS on it: kitchens, kitchenettes, open kitchens and utility kitchens "
    "(labels such as K, KK, KEITTIÖ, TUPAK, AVOK, KÖK, or a kitchen counter with a stove and sink). "
    "An open kitchen that is part of a living room counts as one kitchen. TK (tuulikaappi) is an entry "
    "vestibule, not a kitchen. Box each kitchen's area. " + _JSON
)
BATHROOM = (
    _PLAN + "Count the BATHROOMS or washrooms on it (labels such as KH, KPH, PH, PESUH, SH, BAD, DUSCH, "
    "or a room with a shower or bathtub). Do NOT count separate WC rooms (a room with only a toilet and a "
    "small basin), saunas or utility rooms (KHH) on their own. Box each bathroom. " + _JSON
)
FLOORS = (
    _PLAN + "How many FLOORS (storeys) does this page show? Count each separate storey plan once "
    "(titles such as 1. KRS, 2. KERROS, KELLARI, ULLAKKO, 1st floor help). A cut-away detail, section or "
    "site plan is not a floor. Box each storey plan. " + _JSON
)
OPENINGS = {
    "windows": "Count the WINDOWS drawn on it (window symbols in exterior or interior walls: thin parallel "
    "lines or a frame in the wall). Each separate window counts once, also when several sit side by side. ",
    "doors": "Count the DOORS drawn on it: doorways fitted with a door leaf (usually a swing arc or a "
    "sliding door). Open doorways without a door leaf do not count. ",
}
PAGE_PROMPTS = {"kitchen": KITCHEN, "bathroom": BATHROOM, "floors": FLOORS}


def page_prompt(category: str) -> str:
    """Whole-page prompt for a gated category (shower, kitchen, bathroom, floors, windows, doors)."""
    if category == "shower":
        return shower("page")
    if category in OPENINGS:
        return _PLAN + OPENINGS[category] + "Box each one. " + _JSON
    return PAGE_PROMPTS[category]
