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
