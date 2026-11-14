"""Multilingual room-label glossary: label -> schema room type, outdoor and void flags.

Swiss dwellings labels come in DE/FR/IT/EN. Finnish/Swedish abbreviations (step 7) go here too.
Matching is on the normalised full label (casefold, single spaces, no trailing dots).
"""

from __future__ import annotations

import re
import unicodedata

# label -> room type (schema rooms[].type enum)
_TYPES: dict[str, list[str]] = {
    "bedroom": ["schlafzimmer", "chambre à coucher", "chambre", "camera da letto", "bedroom"],
    "living": [
        "wohnen",
        "wohnzimmer",
        "wohnen/essen",
        "séjour",
        "séjour/repas",
        "soggiorno",
        "soggiorno/pranzo",
        "living",
        "living/dining",
    ],
    "dining": ["essen", "repas", "pranzo", "dining"],
    "kitchen": ["küche", "cuisine", "cucina", "kitchen", "kochen"],
    "bathroom": ["bad", "bain", "bagno", "bathroom", "dusche", "douche", "doccia", "bad/wc", "salle de bain"],
    "wc": ["wc", "toilette", "toilet"],
    "corridor": ["korridor", "couloir", "corridoio", "flur", "gang", "corridor", "hall"],
    "entry": ["eingang", "eingangshalle", "entrée", "ingresso", "entry"],
    "storage": ["reduit", "réduit", "ripostiglio", "abstellraum", "keller", "cave", "cantina", "storage"],
    "stair": ["treppenhaus", "treppe", "cage d'escalier", "escalier", "vano scala", "scala", "staircase"],
    "technical": ["lift", "ascenseur", "ascensore", "aufzug", "elevator", "technik", "haustechnik"],
    "office": ["bürofläche", "büro", "bureau", "ufficio", "office"],
    "balcony": ["balkon", "balcon", "balcone", "loggia", "balcony"],
    "terrace": ["terrasse", "terrazza", "terrazzo", "terrace", "sitzplatz"],
    "other": ["zimmer", "pièce", "stanza", "room", "locale", "gewerbe"],
}
_TYPE_OF = {label: t for t, labels in _TYPES.items() for label in labels}

OUTDOOR_TYPES = {"balcony", "terrace", "outdoor"}

# Apartment (residential unit) labels: "Whg. A", "App. B", "Appt. C", "Wohnung D", ...
_RESIDENTIAL_UNIT = re.compile(
    r"^(whg|wohnung|app|appt|apt|appartement|appartamento|apartment|logement)\.?\s*(\S+)$", re.IGNORECASE
)


def normalise(label: str | None) -> str:
    s = unicodedata.normalize("NFC", str(label or "")).casefold().strip()
    s = s.replace("’", "'")
    return re.sub(r"\s+", " ", s).rstrip(".")


def room_type(label: str | None) -> str:
    """Schema room type for a label; 'unknown' when there is no label, 'other' when not in the glossary."""
    if not label:
        return "unknown"
    return _TYPE_OF.get(normalise(label), "other")


def is_outdoor(label: str | None) -> bool:
    return room_type(label) in OUTDOOR_TYPES


def residential_unit_letter(text: str) -> str | None:
    """'Whg. A' -> 'A'. None if the text is not a residential unit label."""
    m = _RESIDENTIAL_UNIT.match(text.strip())
    return m.group(2) if m else None


# ------------------------------------------------------------------ Finnish / Swedish plan labels (step 7)
# A separate table from the Swiss one above: the same word can mean different things ("hall" is a corridor
# in the Swiss data, an entry hall in Swedish), and OCR text needs noise normalisation first.
# Keys are written without diacritics (matching is diacritic-insensitive, so KEITTIO matches KEITTIÖ).
_FI_SV: dict[str, list[str]] = {
    "bedroom": ["mh", "makuuhuone", "makuuh", "sov", "sovrum", "sovr"],
    # H / huone mean "room", not bedroom (k, Step 10; reverses the Step-7 decision "H = bedroom label").
    # A generic label names a region but never sets its type: the segmentation class stays (see GENERIC).
    "unknown": ["h", "huone"],
    "living": ["oh", "olohuone", "oleskelu", "tupa", "vardagsrum", "vardagsr", "allrum"],
    # KK (keittokomero) and AVOK (avokeittio) are kitchenettes; TUPAK/TPK = tupakeittio (farmhouse kitchen)
    "kitchen": [
        "k", "kk", "keittio", "keitt", "avok", "avokeittio", "keittokomero", "tupak", "tpk", "tupakeittio",
        "kok", "kokvra",
    ],
    "dining": ["rt", "ruokailu", "ruokailutila", "ruok", "matrum", "matplats"],
    "bathroom": [
        "kh", "kph", "ph", "pesuh", "pesuhuone", "kylpyhuone", "kylpyh", "sh", "suihkuhuone",
        "bad", "badrum", "dusch",
    ],
    "wc": ["wc", "vessa", "toalett"],
    "sauna": ["s", "sauna", "loylyhuone", "bastu"],
    "entry": ["et", "eteinen", "tk", "tuulikaappi", "hall", "farstu", "vindfang"],  # TK = tuulikaappi (k)
    "closet": ["vh", "vaatehuone", "klk", "kladkammare"],
    "storage": ["var", "varasto", "forrad", "frd"],
    "utility": ["khh", "kodinhoitohuone", "kodinhoito", "tvattstuga", "tvatt"],
    "office": ["tyoh", "tyohuone", "arbetsrum"],
    "corridor": ["kaytava"],
    "stair": ["porras", "portaat", "porrashuone", "trappa"],
    "garage": ["autotalli", "garage"],
    "technical": ["tekn", "tekninen"],
    "balcony": [
        "parv", "parveke", "lasit parv", "lasitettu parveke", "lasit parveke", "balk", "balkong",
        "inglasad balkong", "loggia",
    ],
    "terrace": ["terassi", "terrass", "uteplats"],
}  # fmt: skip
_FI_SV_OF = {label: t for t, labels in _FI_SV.items() for label in labels}
GENERIC = "unknown"  # type of a generic room label (H, huone): names a region, never types or counts it
_SINGLE_LETTER = {"k", "s", "h"}  # only trusted when the rest of the text is numbers/units or other labels
_UNIT_TOKEN = re.compile(r"^(m2|m²|m|mm|cm|krs)$")


def fold(text: str | None) -> str:
    """casefold, no diacritics (ä -> a, ö -> o, å -> a), single spaces."""
    s = unicodedata.normalize("NFKD", str(text or "")).casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip()


def ocr_token(token: str) -> str:
    """Normalise one OCR token: drop dots, trailing room numbers (MH1 -> mh), read 0 as o inside words."""
    t = token.replace(".", "").replace("'", "")
    if re.search(r"[a-z]", t):
        if re.match(r"^[a-z]{2,}\d+$", t):  # MH2, KH10: numbered rooms (never K115 or S0 -> k, s)
            t = re.sub(r"\d+$", "", t)
        t = t.replace("0", "o")  # 0H -> oh
        t = t.replace("vv", "w")  # VVC -> wc
    return t


def ocr_room_types(text: str | None) -> list[str]:
    """Schema room types named by one OCR text, in reading order ('OH+K' -> ['living', 'kitchen']).

    Empty when the text names no room. Multi-word labels ('LASIT. PARV') are tried on the whole text first.
    """
    s = fold(text)
    if not s:
        return []
    whole = re.sub(r"\s+", " ", re.sub(r"[.']", " ", s)).strip()
    whole = re.sub(r"\s*\d+([,.]\d+)?\s*(m2|m²)?$", "", whole).strip()  # trailing area
    if " " in whole and whole in _FI_SV_OF:
        return [_FI_SV_OF[whole]]
    tokens = [ocr_token(t) for t in re.split(r"[\s+/&,;:()\-]+", s) if t]
    tokens = [t for t in tokens if t]
    out, single = [], []
    for i, t in enumerate(tokens):
        if t in _FI_SV_OF:
            (single if t in _SINGLE_LETTER else out).append((i, _FI_SV_OF[t]))
    if single:
        others_ok = all(t in _FI_SV_OF or re.fullmatch(r"[\d,.]+", t) or _UNIT_TOKEN.match(t) for t in tokens)
        if others_ok:
            out += single
    types = [t for _, t in sorted(out)]
    return [t for i, t in enumerate(types) if i == 0 or t != types[i - 1]]


_ROOM_COUNT = re.compile(r"(\d+(-\d+)?)?h")


def is_unit_summary(text: str | None) -> bool:
    """An apartment description such as '1H+KK+ALK+S' or '2 H+K+S' (raster OCR only).

    Rule: a '+'-joined text whose first part is a room count (H with an optional number in front: H, 1H,
    2 H, 1-2H) describes the apartment, not one room. 'OH+RT+K' or 'KHH+PH' stay room labels.
    """
    s = fold(text)
    if "+" not in s:
        return False
    first = re.sub(r"[\s.]", "", s.split("+", 1)[0])
    return bool(_ROOM_COUNT.fullmatch(first))


# storey titles: 1. KRS, 2. KERROS, KELLARI, ULLAKKO, 1st floor, 1. VÅN, KÄLLARE ...
_LEVEL_TITLE = re.compile(
    r"^(\d{1,2}|i{1,3}|iv)\s*\.?\s*(krs|kerros|kerroksen|vaning|van|plan)\b"
    r"|^(kellari|kellarikerros|ullakko|ullakkokerros|pohjakerros|parvikerros|vintti|vinttikerros)\b"
    r"|^(\d{1,2}(st|nd|rd|th)|first|second|third|ground|top)\s+floor\b|^(basement|attic)\b"
    r"|^(kallare|kallarvaning|vind|vindsvaning|bottenvaning|entreplan)\b"
)


def level_title(text: str | None) -> str | None:
    """Normalised storey title ('1. KRS' -> '1 krs'), or None if the text is not one."""
    s = re.sub(r"\s+", " ", fold(text).replace(".", " ")).strip()
    m = _LEVEL_TITLE.match(s)
    if not m:
        return None
    s = re.sub(r"\bkerroksen\b|\bkerros\b", "krs", m.group(0))
    s = re.sub(r"\bvaning\b", "van", s)
    return re.sub(r"\s+", " ", s).strip()
