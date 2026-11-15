"""Pipe glossary (grows with the steps that need it). Step 5 so far: legend and title-block headings."""

LEGEND_HEADINGS = (
    "LEGEND", "LEGENDE", "SELITYKSET", "SELITE", "MERKINNÄT", "MERKINNAT", "SIGNATURFORKLARING",
    "TEGNFORKLARING", "TECKENFÖRKLARING", "TECKENFORKLARING", "SYMBOLS", "SYMBOL LIST", "KEY",
)  # fmt: skip


# ---------------------------------------------------------------- DXF route: layers, systems, blocks
import re  # noqa: E402

_TR = str.maketrans(
    {"Ä": "A", "Ö": "O", "Å": "A", "Æ": "A", "Ø": "O", "Ü": "U", "ä": "a", "ö": "o", "å": "a", "ü": "u"}
)


def fold(s: str) -> str:
    """Upper case with the Nordic and German letters folded, for keyword matching."""
    return s.translate(_TR).upper()


PIPE_LAYER = re.compile(
    r"(^|[-_. ])(PIPE|PIPING|PLUMB\w*|SAN\w*|HEAT\w*|VVS|LVI|KV|LV|LVK|VV|VVC|JV|ROR|ROHR\w*|HEIZ\w*)([-_. ]|\d|$)|^P-"
)
NOT_PIPE_LAYER = re.compile(
    r"DIM|TEXT|TXT|FRAME|TITLE|BORDER|GRID|HATCH|WALL|DEFPOINTS|NOTE|LEGEND|TABLE|ANNO"
)


def is_pipe_layer(name: str) -> bool:
    return bool(PIPE_LAYER.search(fold(name)))


def is_excluded_layer(name: str) -> bool:
    return bool(NOT_PIPE_LAYER.search(fold(name)))


# (pattern, system); the first match wins, so the specific ones come first
SYSTEM_LAYERS = [
    (re.compile(r"CIRC|(^|[-_. ])LVK|(^|[-_. ])VVC|PWH-?C"), "hot_water_circulation"),
    (re.compile(r"COLD|(^|[-_. ])KV([-_. ]|\d|$)|PWC|KALT|KALL"), "cold_water"),
    (re.compile(r"HOT|(^|[-_. ])LV([-_. ]|\d|$)|PWH|(^|[-_. ])VV([-_. ])"), "hot_water"),
    (
        re.compile(
            r"(HEAT|LAMM|HEIZ|VARME).*(RETURN|PALUU|RETUR|RUCK)|(RETURN|PALUU|RETUR|RUCK).*(HEAT|LAMM|HEIZ)"
        ),
        "heating_return",
    ),
    (re.compile(r"HEAT|LAMM|HEIZ|VARME|SUPPLY|MENO|VORLAUF"), "heating_supply"),
    (re.compile(r"WASTE|VIEMARI|ABLAUF|AFLOB|(^|[-_. ])JV([-_. ]|\d|$)|SANIT|(^|[-_. ])SAN"), "waste"),
    (re.compile(r"RAIN|SADE"), "rainwater"),
    (re.compile(r"SPRINK"), "sprinkler"),
    (re.compile(r"(^|[-_. ])GAS|KAASU"), "gas"),
    (re.compile(r"VENTIL|(^|[-_. ])VENT([-_. ]|$)"), "ventilation_duct"),
]


def system_from_layer(name: str) -> str:
    f = fold(name)
    for pat, system in SYSTEM_LAYERS:
        if pat.search(f):
            return system
    return "unknown"


# (pattern on the folded block name, node type); specific kinds before the generic ones
BLOCK_TYPES = [
    (re.compile(r"BALL|PALLO"), "ball_valve"),
    (re.compile(r"GATE|SULKU|SCHIEBER"), "gate_valve"),
    (re.compile(r"GLOBE|KARA"), "globe_valve"),
    (re.compile(r"BUTTERFLY|PELTI|KLAPPE"), "butterfly_valve"),
    (re.compile(r"CHECK|TAKAISKU|RUCKSCHLAG|NON.?RETURN"), "check_valve"),
    (re.compile(r"BALANC|TASAPAINO|REGULIER|DRIFT"), "balancing_valve"),
    (re.compile(r"SAFETY|VARO(VENT|TIILI)|RELIEF"), "safety_valve"),
    (re.compile(r"CONTROL|SAATO"), "control_valve"),
    (re.compile(r"VALVE|VENTTIILI|VENTIL"), "valve_other"),
    (re.compile(r"PUMP|PUMPPU|KIERTO"), "pump"),
    (re.compile(r"RADIAT|PATTERI|HEIZK|(^|[^A-Z])RAD([^A-Z]|$)"), "radiator"),
    (re.compile(r"BOILER|KATTILA|KESSEL"), "boiler"),
    (re.compile(r"EXPANS|LAAJENNUS|AUSDEHN"), "expansion_vessel"),
    (re.compile(r"HEAT.?EXCH|LAMMONSIIRT|TAUSCHER|(^|[^A-Z])HX([^A-Z]|$)"), "heat_exchanger"),
    (re.compile(r"TANK|SAILIO|BEHALTER|VESSEL"), "tank"),
    (re.compile(r"METER|MITTARI|ZAHLER"), "meter"),
    (re.compile(r"STRAINER|SUODATIN|SIEB"), "strainer"),
    (re.compile(r"FILTER"), "filter"),
    (re.compile(r"FLOOR.?DRAIN|LATTIAKAIVO|ABLAUF|(^|[^A-Z])DRAIN"), "floor_drain"),
    (re.compile(r"SINK|WASH|ALTAAN|LAVATORY|FIXTURE|WC|TOILET"), "fixture"),
    (re.compile(r"GAUGE|MANOMETRI|MANOMETER"), "gauge"),
    (re.compile(r"SENSOR|ANTURI|FUHLER"), "sensor"),
    (re.compile(r"REDUC|SUPISTUS"), "reducer"),
    (re.compile(r"(^|[^A-Z])CAP([^A-Z]|$)|TULPPA"), "cap"),
    (re.compile(r"JATKUU|CONTINU|OFF.?SHEET|FORTSAETTER|FORTSATTER"), "off_sheet_connector"),
    (re.compile(r"RISER|NOUSU|STIGE"), "riser"),
]


def type_from_block(name: str) -> str:
    f = fold(name)
    for pat, t in BLOCK_TYPES:
        if pat.search(f):
            return t
    return "unknown"


# a free pipe end with one of these texts nearby is an open end (off-sheet continuation), not a tracing gap. The text
# STARTS with TO/FROM (or says continues/jatkuu/->): a bare 'SHEET NO.' in a title block or a range '-148 to 68F' is not one.
CONTINUATION = re.compile(r"JATKUU|CONTINU|FORTS|WEITER|->|^\s*(TO|FROM|FRA|TIL|VON|NACH|SIIRTYY)\s", re.I)
