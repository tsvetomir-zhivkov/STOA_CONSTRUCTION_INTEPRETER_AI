"""Deterministic answerer: question template -> lookup in the index JSON.

This is NOT the answering agent. It isolates translator errors from agent errors:
if the probe gets a question wrong, the JSON is wrong or incomplete.

Each intent has regexes (all languages) and an answer function over the documents
for one drawing file. First matching intent wins, so order matters.
Only entities with status != superseded are counted.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# Quote characters used in the questions: 'x', «x», „x“, "x".
_O = r"[\"'«„“‘]"
_C = r"[\"'»“”’]"
Q = _O + r"(.+)" + _C  # greedy; every pattern pins the text after the closing quote


# ---------------------------------------------------------------- helpers


def _active(items):
    return [x for x in items if x.get("status", "active") != "superseded"]


def _all(docs, key):
    out = []
    for d in docs:
        out.extend(_active(d.get(key, [])))
    return out


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def _area(room):
    for k in ("area_m2", "area_written_m2", "area_computed_m2"):
        v = room.get(k)
        if v is not None:
            return v
    return None


def _sum(values):
    values = [v for v in values if v is not None]
    return round(sum(values), 6) if values else None


def _level_ids(docs, level_name):
    want = _norm(level_name)
    return {lv["id"] for lv in _all(docs, "levels") if _norm(lv.get("name")) == want}


def _on_level(items, ids):
    return [x for x in items if x.get("level_id") in ids]


def _objects(docs, *cats):
    return [o for o in _all(docs, "objects") if o.get("category") in cats]


def _openings(docs, kind):
    return [o for o in _all(docs, "openings") if o.get("kind") == kind]


def _rooms_of_type(docs, *types):
    return [r for r in _all(docs, "rooms") if r.get("type") in types]


NOT_RESIDENTIAL = {"non_residential", "unlabelled"}


def _residential_units(docs):
    """Units that are apartments: not flagged non_residential / unlabelled."""
    return [u for u in _all(docs, "units") if not NOT_RESIDENTIAL & set(u.get("flags", []))]


# Element words in IFC count questions -> (collection, filter)
_ELEMENT_WORDS = {
    "door": ["doors", "døre", "ust", "türen"],
    "window": ["windows", "vinduer", "akent", "fenster"],
    "wall": ["walls", "vægge", "seina", "wände"],
    "column": ["columns", "søjler", "posti", "stützen"],
    "stair": ["stairs", "trapper", "treppi", "treppen"],
    "room": ["rooms (spaces)", "rooms", "rum", "ruumi", "räume"],
    "beam": ["beams", "bjælker", "tala", "träger"],
    "sanitary": ["sanitary fixtures"],
}
_WORD_TO_ELEMENT = {w: k for k, ws in _ELEMENT_WORDS.items() for w in ws}
_EL = "(" + "|".join(re.escape(w) for w in sorted(_WORD_TO_ELEMENT, key=len, reverse=True)) + ")"


def _elements(docs, element):
    if element in ("door", "window"):
        return _openings(docs, element)
    if element == "wall":
        return _all(docs, "walls")
    if element == "room":
        return _all(docs, "rooms")
    if element == "sanitary":
        return _objects(docs, "toilet", "sink", "shower", "bathtub", "sanitary_other")
    return _objects(docs, element)


# ---------------------------------------------------------------- answer functions
# Each takes (docs, match) and returns a number or None.


def _count(fn):
    return lambda docs, m: len(fn(docs))


def cc_floors(docs, m):
    return len({_norm(lv.get("name")) or lv["id"] for lv in _all(docs, "levels")})


def sw_rooms_labelled(docs, m):
    want = _norm(m.group(1))
    return sum(1 for r in _all(docs, "rooms") if _norm(r.get("name")) == want)


def sw_label_area(docs, m):
    want = _norm(m.group(1))
    rooms = [r for r in _all(docs, "rooms") if _norm(r.get("name")) == want]
    return _sum(_area(r) for r in rooms)


def sw_room_area(docs, m):
    number, label = _norm(m.group(1)), _norm(m.group(2))
    rooms = [r for r in _all(docs, "rooms") if _norm(r.get("number")) == number]
    if len(rooms) > 1:  # disambiguate by label
        rooms = [r for r in rooms if _norm(r.get("name")) == label] or rooms
    return _area(rooms[0]) if rooms else None


def sw_apartment_area(docs, m):
    want = _norm(m.group(1))
    unit_ids = {u["id"] for u in _all(docs, "units") if _norm(u.get("label")) == want}
    if not unit_ids:
        return None
    rooms = [
        r
        for r in _all(docs, "rooms")
        if r.get("unit_id") in unit_ids
        and (r.get("name") or r.get("number"))  # labelled rooms only, not bare outlines
        and not r.get("is_outdoor")
        and r.get("type") != "shaft"
    ]
    return _sum(_area(r) for r in rooms)


def sw_numbered_indoor_area(docs, m):
    rooms = [
        r
        for r in _all(docs, "rooms")
        if r.get("number") and not r.get("is_outdoor") and r.get("type") != "shaft"
    ]
    return _sum(_area(r) for r in rooms)


def ifc_room_area(docs, m):
    label, level = _norm(m.group(1)), m.group(2)
    rooms = _on_level(_all(docs, "rooms"), _level_ids(docs, level))
    for r in rooms:
        cands = {
            _norm(r.get("label")),
            _norm(f"{r.get('name')} ({r.get('number')})"),
            _norm(r.get("name")),
        }
        if label in cands:
            return _area(r)
    return None


def _level_sum(collection, field, category=None):
    def fn(docs, m):
        ids = _level_ids(docs, m.group(1))
        if not ids:
            return None
        items = _on_level(_all(docs, collection), ids)
        if category:
            items = [x for x in items if x.get("category") == category]
        if field == "area":
            return _sum(_area(x) for x in items)
        return _sum(x.get(field) for x in items)

    return fn


def ifc_count_level(docs, m):
    element, level = _WORD_TO_ELEMENT[m.group(1).casefold()], m.group(2)
    ids = _level_ids(docs, level)
    if not ids:
        return None
    return len(_on_level(_elements(docs, element), ids))


def ifc_count_model(docs, m):
    return len(_elements(docs, _WORD_TO_ELEMENT[m.group(1).casefold()]))


# ---------------------------------------------------------------- intents


@dataclass(frozen=True)
class Intent:
    name: str
    sources: tuple[str, ...]
    patterns: tuple[str, ...]
    answer: Callable

    def match(self, question: str):
        for p in self.patterns:
            m = re.search(p, question, flags=re.IGNORECASE)
            if m:
                return m
        return None


CC = ("cubicasa5k",)
SW = ("swiss_dwellings",)
IFC = ("ifc_models",)

INTENTS: list[Intent] = [
    # --- CubiCasa (EN + FI). bathrooms before wc_rooms: its text mentions "separate WC rooms".
    Intent(
        "cc_windows",
        CC,
        (r"^How many windows", r"^Kuinka monta ikkunaa"),
        _count(lambda d: _openings(d, "window")),
    ),
    Intent(
        "cc_doors", CC, (r"^How many doors", r"^Kuinka monta ovea"), _count(lambda d: _openings(d, "door"))
    ),
    Intent("cc_toilets", CC, (r"toilet bowls", r"WC-istuinta"), _count(lambda d: _objects(d, "toilet"))),
    Intent(
        "cc_sinks",
        CC,
        (r"sinks and wash basins", r"pesu- ja tiskiallasta"),
        _count(lambda d: _objects(d, "sink")),
    ),
    Intent(
        "cc_showers_baths",
        CC,
        (r"showers and bathtubs", r"suihkua ja kylpyammetta"),
        _count(lambda d: _objects(d, "shower", "bathtub")),
    ),
    Intent("cc_floors", CC, (r"floors \(storeys\)", r"monta kerrosta"), cc_floors),
    Intent(
        "cc_bedrooms",
        CC,
        (r"^How many bedrooms", r"makuuhuonetta"),
        _count(lambda d: _rooms_of_type(d, "bedroom")),
    ),
    Intent(
        "cc_kitchens",
        CC,
        (r"^How many kitchens", r"keittiötä"),
        _count(lambda d: _rooms_of_type(d, "kitchen")),
    ),
    Intent(
        "cc_bathrooms",
        CC,
        (r"^How many bathrooms", r"kylpy- tai pesuhuonetta"),
        _count(lambda d: _rooms_of_type(d, "bathroom")),
    ),
    Intent(
        "cc_wc_rooms",
        CC,
        (r"^How many separate WC rooms", r"erillistä WC-tilaa"),
        _count(lambda d: _rooms_of_type(d, "wc")),
    ),
    Intent(
        "cc_saunas", CC, (r"^How many saunas", r"monta saunaa"), _count(lambda d: _rooms_of_type(d, "sauna"))
    ),
    Intent(
        "cc_balconies",
        CC,
        (r"^How many balconies", r"parveketta"),
        _count(lambda d: _rooms_of_type(d, "balcony")),
    ),
    # --- Swiss dwellings (EN, DE, FR, IT)
    Intent(
        "sw_apartments",
        SW,
        (
            r"^How many apartments",
            r"^Wie viele Wohnungen",
            r"^Combien d.appartements",
            r"^Quanti appartamenti",
        ),
        _count(_residential_units),
    ),
    Intent(
        "sw_doors",
        SW,
        (r"^How many doors", r"^Wie viele Türen", r"^Combien de portes", r"^Quante porte"),
        _count(lambda d: _openings(d, "door")),
    ),
    Intent(
        "sw_windows",
        SW,
        (r"^How many windows", r"^Wie viele Fenster", r"^Combien de fenêtres", r"^Quante finestre"),
        _count(lambda d: _openings(d, "window")),
    ),
    Intent(
        "sw_toilets",
        SW,
        (r"^How many toilets", r"^Wie viele WCs", r"^Combien de WC", r"^Quanti WC"),
        _count(lambda d: _objects(d, "toilet")),
    ),
    Intent(
        "sw_washbasins",
        SW,
        (r"^How many washbasins", r"^Wie viele Lavabos", r"^Combien de lavabos", r"^Quanti lavabi"),
        _count(lambda d: _objects(d, "sink")),
    ),
    Intent(
        "sw_bathtubs",
        SW,
        (r"^How many bathtubs", r"^Wie viele Badewannen", r"^Combien de baignoires", r"^Quante vasche"),
        _count(lambda d: _objects(d, "bathtub")),
    ),
    Intent(
        "sw_showers",
        SW,
        (r"^How many showers", r"^Wie viele Duschen", r"^Combien de douches", r"^Quante docce"),
        _count(lambda d: _objects(d, "shower")),
    ),
    Intent(
        "sw_rooms_labelled",
        SW,
        (
            r"^How many rooms labelled " + Q + r" are there",
            r"^Wie viele Räume mit der Bezeichnung " + Q + r" gibt es",
            r"^Combien de pièces portant la désignation " + Q + r" y a",
            r"^Quanti locali con la denominazione " + Q + r" ci sono",
        ),
        sw_rooms_labelled,
    ),
    Intent(
        "sw_label_area",
        SW,
        (
            r"of all rooms labelled " + Q + r" on this floor",
            r"aller Räume mit der Bezeichnung " + Q + r" in diesem",
            r"toutes les pièces désignées " + Q + r" à cet",
            r"tutti i locali denominati " + Q + r" in questo",
        ),
        sw_label_area,
    ),
    Intent(
        "sw_numbered_indoor_area",
        SW,
        (
            r"numbered indoor rooms",
            r"nummerierten Innenräume",
            r"pièces intérieures numérotées",
            r"locali interni numerati",
        ),
        sw_numbered_indoor_area,
    ),
    Intent(
        "sw_room_area",
        SW,
        (
            r"net floor area of room (\S+) \(" + Q + r"\)",
            r"Nettofläche von Raum (\S+) \(" + Q + r"\)",
            r"surface nette de la pièce (\S+) \(" + Q + r"\)",
            r"superficie netta del locale (\S+) \(" + Q + r"\)",
        ),
        sw_room_area,
    ),
    Intent(
        "sw_apartment_area",
        SW,
        (
            r"net floor area of apartment (\w+)",
            r"Nettowohnfläche von Wohnung (\w+)",
            r"surface nette de l.appartement (\w+)",
            r"superficie netta dell.appartamento (\w+)",
        ),
        sw_apartment_area,
    ),
    # --- IFC models (EN, DA, ET, DE); questions may point at the .ifc or a rendered level .pdf
    Intent(
        "ifc_room_area",
        IFC,
        (
            r"area of the room " + Q + r" on " + Q + r", in m",
            r"nettoarealet af rummet " + Q + r" på " + Q + r" i m",
            r"ruumi " + Q + r" netopõrandapind korrusel " + Q + r" \(m",
        ),
        ifc_room_area,
    ),
    Intent(
        "ifc_level_room_area",
        IFC,
        (
            r"net floor area of all rooms \(spaces\) on " + Q + r", in m",
            r"samlede nettoareal af alle rum på " + Q + r" i m",
            r"kõigi ruumide netopõrandapind kokku korrusel " + Q + r" \(m",
        ),
        _level_sum("rooms", "area"),
    ),
    Intent(
        "ifc_level_wall_side_area",
        IFC,
        (
            r"net side area of all walls on " + Q + r", in m",
            r"nettosideareal af alle vægge på " + Q + r" i m",
            r"Netto-Seitenfläche aller Wände im Geschoss " + Q + r" in m",
        ),
        _level_sum("walls", "net_side_area_m2"),
    ),
    Intent(
        "ifc_level_wall_length",
        IFC,
        (
            r"total length of all walls on " + Q + r", in m",
            r"samlede længde af alle vægge på " + Q + r" i m",
            r"Wie lang sind alle Wände im Geschoss " + Q + r" zusammen",
        ),
        _level_sum("walls", "length_m"),
    ),
    Intent(
        "ifc_level_wall_volume",
        IFC,
        (
            r"net volume of all walls on " + Q + r", in m",
            r"nettovolumen af alle vægge på " + Q + r" i m",
            r"Netto-Volumen aller Wände im Geschoss " + Q + r" in m",
        ),
        _level_sum("walls", "net_volume_m3"),
    ),
    Intent(
        "ifc_level_slab_volume",
        IFC,
        (
            r"net volume of all slabs on " + Q + r", in m",
            r"nettovolumen af alle dæk og plader på " + Q + r" i m",
            r"Netto-Volumen aller Decken und Bodenplatten im Geschoss " + Q + r" in m",
        ),
        _level_sum("objects", "net_volume_m3", category="slab"),
    ),
    Intent(
        "ifc_count_level",
        IFC,
        (
            r"^How many " + _EL + r" are shown on the floor plan of " + Q + r"\?",
            r"^Hvor mange " + _EL + r" er vist på plantegningen for " + Q + r"\?",
            r"^Mitu " + _EL + r" on näidatud korruse " + Q + r" plaanil",
            r"^Wie viele " + _EL + r" sind im Grundriss " + Q + r" dargestellt",
        ),
        ifc_count_level,
    ),
    Intent(
        "ifc_count_model",
        IFC,
        (
            r"^How many " + _EL + r" are there in the model",
            r"^Hvor mange " + _EL + r" er der i modellen",
            r"^Mitu " + _EL + r" on mudelis",
            r"^Wie viele " + _EL + r" enthält das Modell",
        ),
        ifc_count_model,
    ),
]


def classify(source: str, question: str):
    """Return (intent, match) or (None, None)."""
    for intent in INTENTS:
        if source in intent.sources:
            m = intent.match(question)
            if m:
                return intent, m
    return None, None


def answer(docs: list[dict], source: str, question: str):
    """Return (intent_name or None, value or None)."""
    intent, m = classify(source, question)
    if intent is None:
        return None, None
    if not docs:
        return intent.name, None
    return intent.name, intent.answer(docs, m)
