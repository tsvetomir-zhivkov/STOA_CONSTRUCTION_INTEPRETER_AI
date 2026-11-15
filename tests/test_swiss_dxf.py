"""Step 3: Swiss DXF extractor. One synthetic ezdxf fixture per rule, plus the real quick-test floor."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import ezdxf
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "eval"))

import probe  # noqa: E402

from translator import translate  # noqa: E402
from translator.config import data_root, questions_path  # noqa: E402
from translator.extract import swiss_dxf  # noqa: E402
from translator.store import validate  # noqa: E402

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)
LAYERS = ("WALL", "DOOR", "WINDOW", "ROOM_OUTLINE", "ROOM_TEXT", "FIXTURE", "ANNOTATION", "UNIT_OUTLINE")


# ------------------------------------------------------------------ fixture builder


class Plan:
    def __init__(self):
        self.doc = ezdxf.new("R2013")
        self.doc.header["$INSUNITS"] = 6
        for name in LAYERS:
            self.doc.layers.add(name)
        self.msp = self.doc.modelspace()

    def rect(self, layer, x0, y0, x1, y1, closed=True):
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return self.msp.add_lwpolyline(pts, close=closed, dxfattribs={"layer": layer})

    def line(self, layer, a, b):
        return self.msp.add_lwpolyline([a, b], dxfattribs={"layer": layer})

    def text(self, s, x, y, layer="ROOM_TEXT", h=0.25):
        t = self.msp.add_text(s, dxfattribs={"layer": layer, "height": h})
        t.set_placement((x, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
        return t

    def room(self, x0, y0, x1, y1, name=None, number=None, area=None, unit=None):
        self.rect("ROOM_OUTLINE", x0, y0, x1, y1)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        lines = [s for s in (unit, name, number, f"{area:.2f} m²" if area is not None else None) if s]
        for i, s in enumerate(lines):
            self.text(s, cx, cy + 0.5 * (len(lines) / 2 - i))

    def ellipse(self, cx, cy, rx, ry, n=48):
        pts = [
            (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
        return self.msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "FIXTURE"})

    def rounded_rect(self, x0, y0, x1, y1, r=0.08, per_corner=17):
        pts = []
        corners = [(x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)]
        for cx, cy, a0 in corners:
            for i in range(per_corner):
                a = math.radians(a0 + 90 * i / (per_corner - 1))
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return self.msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "FIXTURE"})

    def circle(self, x, y, r):
        return self.msp.add_circle((x, y), r, dxfattribs={"layer": "FIXTURE"})

    def save(self, path: Path) -> Path:
        self.doc.saveas(path)
        return path


def _extract(plan: Plan, tmp_path: Path) -> dict:
    return swiss_dxf.extract(plan.save(tmp_path / "plan.dxf"))


def _room(parts, number):
    return next(r for r in parts["rooms"] if r["number"] == number)


# ------------------------------------------------------------------ rooms and texts


def test_room_text_block_and_written_area(tmp_path):
    p = Plan()
    p.room(0, 0, 4, 4, name="Zimmer", number="2.10", area=16.0)
    r = _room(_extract(p, tmp_path), "2.10")
    assert (r["name"], r["area_m2"], r["area_written_m2"]) == ("Zimmer", 16.0, 16.0)
    assert r["area_computed_m2"] == pytest.approx(16.0)
    assert r["label"] == "Zimmer (2.10)" and "check_this" not in r["flags"]


def test_written_area_mismatch_flags_check_this(tmp_path):
    p = Plan()
    p.room(0, 0, 4, 4, name="Zimmer", number="2.10", area=15.0)  # drawn 16.0: 6.7% off
    r = _room(_extract(p, tmp_path), "2.10")
    assert r["area_m2"] == 15.0 and "check_this" in r["flags"]


def test_number_only_room_uses_polygon_area(tmp_path):
    p = Plan()
    p.room(0, 0, 2, 1.5, number="2.04")
    r = _room(_extract(p, tmp_path), "2.04")
    assert r["name"] is None and r["area_m2"] == pytest.approx(3.0)


def test_text_outside_outline_snaps_to_nearest_room(tmp_path):
    p = Plan()
    p.rect("ROOM_OUTLINE", 0, 0, 1, 1)
    p.text("WC", 0.5, 1.6)
    p.text("1.01", 0.5, 1.2)  # 0.2 m above the outline
    r = _room(_extract(p, tmp_path), "1.01")
    assert r["name"] == "WC" and r["type"] == "wc"


def test_x_marked_outline_is_a_shaft(tmp_path):
    p = Plan()
    p.rect("ROOM_OUTLINE", 0, 0, 0.4, 0.4)
    p.line("ROOM_OUTLINE", (0.05, 0.05), (0.35, 0.35))
    p.line("ROOM_OUTLINE", (0.05, 0.35), (0.35, 0.05))
    rooms = _extract(p, tmp_path)["rooms"]
    assert len(rooms) == 1 and rooms[0]["type"] == "shaft"


@pytest.mark.parametrize("label", ["Balkon", "Balcon", "Balcone", "Loggia", "Terrasse"])
def test_outdoor_labels(tmp_path, label):
    p = Plan()
    p.room(0, 0, 3, 2, name=label, number="2.05", area=6.0)
    assert _room(_extract(p, tmp_path), "2.05")["is_outdoor"]


# ------------------------------------------------------------------ units


def test_units_residential_non_residential_and_nested(tmp_path):
    p = Plan()
    p.rect("UNIT_OUTLINE", 0, 0, 10, 5)  # apartment A
    p.room(0, 0, 5, 5, name="Zimmer", number="1.01", area=25.0, unit="Whg. A")
    p.room(5, 0, 10, 5, name="Balkon", number="1.02", area=25.0)
    p.rect("UNIT_OUTLINE", 6, 1, 9, 4)  # unlabelled outline nested inside A
    p.rect("ROOM_OUTLINE", 6, 1, 9, 4)
    p.text("Korridor", 7.5, 2.75)
    p.text("1.03", 7.5, 2.25)
    p.rect("UNIT_OUTLINE", 20, 0, 25, 5)  # commercial unit
    p.room(20, 0, 25, 5, name="Bürofläche", number="1.04", area=25.0, unit="Gewerbe 1")
    parts = _extract(p, tmp_path)
    units = {u["label"]: u for u in parts["units"]}
    assert units["A"]["flags"] == []
    assert units["Gewerbe 1"]["flags"] == ["non_residential"]
    assert any(u["flags"] == ["unlabelled"] for u in parts["units"])
    assert _room(parts, "1.03")["unit_id"] != units["A"]["id"]  # smallest containing unit wins
    assert units["A"]["area_m2"] == 25.0  # balcony and nested corridor excluded
    assert len(probe._residential_units([parts])) == 1


# ------------------------------------------------------------------ doors and windows


def test_door_leaf_with_two_swings_is_one_door(tmp_path):
    p = Plan()
    leaf = [(0, 0), (0.9, 0), (0.9, 0.04), (0, 0.04), (0, 0)]  # 5-point ring, not flagged closed
    p.msp.add_lwpolyline(leaf, dxfattribs={"layer": "DOOR"})
    p.msp.add_arc((0, 0), 0.9, 0, 90, dxfattribs={"layer": "DOOR"})
    p.msp.add_arc((0.9, 0), 0.9, 90, 180, dxfattribs={"layer": "DOOR"})
    p.line("DOOR", (0, 0), (0, 0.9))
    doors = [o for o in _extract(p, tmp_path)["openings"] if o["kind"] == "door"]
    assert len(doors) == 1 and doors[0]["properties"]["swings"] == 2
    assert doors[0]["width_m"] == pytest.approx(0.9)


def test_window_frame_with_glazing_lines_is_one_window(tmp_path):
    p = Plan()
    p.rect("WINDOW", 0, 0, 1.2, 0.3)
    p.line("WINDOW", (0, 0.1), (1.2, 0.1))
    p.line("WINDOW", (0, 0.2), (1.2, 0.2))
    p.rect("WINDOW", 2, 0, 3.2, 0.3)  # a second window touching nothing
    wins = [o for o in _extract(p, tmp_path)["openings"] if o["kind"] == "window"]
    assert len(wins) == 2
    assert len(wins[0]["provenance"]["details"]["glazing_handles"]) == 2


# ------------------------------------------------------------------ fixtures


def _cats(parts):
    out = {}
    for o in parts["objects"]:
        key = o["category"] if o["category"] != "other" else f"other:{o.get('subtype')}"
        out[key] = out.get(key, 0) + 1
    return out


def test_fixture_rules(tmp_path):
    p = Plan()
    # toilet: body rect + elliptic bowl + cistern line, no drain
    p.rect("FIXTURE", 0, 0, 0.4, 0.65)
    p.ellipse(0.2, 0.3, 0.17, 0.25)
    p.line("FIXTURE", (0.02, 0.6), (0.38, 0.6))
    # washbasin: body rect + elliptic bowl + drain inside
    p.rect("FIXTURE", 2, 0, 2.6, 0.45)
    p.ellipse(2.3, 0.22, 0.25, 0.17)
    p.circle(2.3, 0.22, 0.03)
    # bathtub: body rect + rounded-rect tub + drain inside (must not become a shower)
    p.rect("FIXTURE", 4, 0, 5.7, 0.75)
    p.rounded_rect(4.05, 0.05, 5.65, 0.7)
    p.circle(4.2, 0.375, 0.03)
    # shower: tray (two nested rects) + drain
    p.rect("FIXTURE", 7, 0, 7.9, 0.9)
    p.rect("FIXTURE", 7.05, 0.05, 7.85, 0.85)
    p.circle(7.45, 0.45, 0.03)
    # kitchen counter: rect + 4 hob circles
    p.rect("FIXTURE", 9, 0, 11, 0.6)
    for i in range(4):
        p.circle(9.3 + 0.25 * (i % 2), 0.2 + 0.25 * (i // 2), 0.08)
    cats = _cats(_extract(p, tmp_path))
    assert cats == {"toilet": 1, "sink": 1, "bathtub": 1, "shower": 1, "other:kitchen_counter": 1}


def test_touching_fixtures_still_counted_separately(tmp_path):
    p = Plan()
    p.rect("FIXTURE", 0, 0, 1.2, 0.45)  # one vanity with two basins touching
    for x in (0.3, 0.9):
        p.ellipse(x, 0.22, 0.22, 0.15)
        p.circle(x, 0.22, 0.03)
    assert _cats(_extract(p, tmp_path)) == {"sink": 2}


# ------------------------------------------------------------------ integration


def test_translate_runs_swiss_extractor_and_validates(tmp_path):
    p = Plan()
    p.room(0, 0, 4, 4, name="Küche", number="1.01", area=16.0)
    p.save(tmp_path / "floor1.dxf")
    (tmp_path / "floor1.pdf").write_bytes(b"")  # question file; the router prefers the DXF sibling
    doc = translate(str(tmp_path / "floor1.pdf"), str(tmp_path))
    assert doc["drawings"][0]["route"] == "dxf"
    assert doc["drawings"][0]["scale"]["source"] == "vector_units"
    assert [r["number"] for r in doc["rooms"]] == ["1.01"]
    validate(doc)


QUICK = "data/swiss_dwellings/site1000_bldg2082_floor9385.pdf"
QUICK_EXPECTED = [
    ("How many apartments (residential units) are shown on this floor plan?", 4),
    ("How many rooms labelled 'Zimmer' are there on this floor?", 12),
    ("How many doors (interior doors and entrance doors together) are drawn on this floor plan?", 36),
    ("How many windows are drawn on this floor plan?", 28),
    ("How many toilets (WC) are drawn on this floor plan?", 4),
    ("How many washbasins are drawn on this floor plan?", 4),
    ("How many bathtubs are drawn on this floor plan?", 4),
    ("What is the total net area in m² of all rooms labelled 'Zimmer' on this floor?", 184.93),
    (
        "What is the total net floor area in m² of all numbered indoor rooms on this floor "
        "(excluding balconies, loggias, terraces and other outdoor spaces, shafts and voids)?",
        341.45,
    ),
]


@pytest.fixture(scope="module")
def quick_doc():
    return translate(QUICK)


@needs_data
@pytest.mark.parametrize("question, expected", QUICK_EXPECTED)
def test_real_quick_test_floor(quick_doc, question, expected):
    _, got = probe.answer([quick_doc], "swiss_dwellings", question)
    if isinstance(expected, int):
        assert got == expected
    else:
        assert got == pytest.approx(expected, rel=0.02)
