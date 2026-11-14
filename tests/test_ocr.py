"""Step 7: OCR fusion from synthetic OCR output (no GPU, no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "eval"))

import probe  # noqa: E402

from translator import glossary, translate_all  # noqa: E402
from translator.extract import cubicasa as cc  # noqa: E402
from translator.extract import ocr  # noqa: E402

# ------------------------------------------------------------------ glossary


@pytest.mark.parametrize(
    "text, types",
    [
        ("MH", ["bedroom"]),
        ("H", ["bedroom"]),  # huone, as drawn on high_quality-5815
        ("OH", ["living"]),
        ("TUPA", ["living"]),
        ("KEITTIÖ", ["kitchen"]),
        ("KK", ["kitchen"]),
        ("AVOK", ["kitchen"]),
        ("TK", ["entry"]),  # tuulikaappi (vestibule)
        ("TUPAK", ["kitchen"]),
        ("tupakeittiö", ["kitchen"]),
        ("TPK", ["kitchen"]),
        ("KH", ["bathroom"]),
        ("KPH", ["bathroom"]),
        ("PESUH.", ["bathroom"]),
        ("SH", ["bathroom"]),
        ("WC", ["wc"]),
        ("SAUNA", ["sauna"]),
        ("ET", ["entry"]),
        ("ETEINEN", ["entry"]),
        ("VH", ["closet"]),
        ("VAR", ["storage"]),
        ("PARVEKE", ["balcony"]),
        ("PARV", ["balcony"]),
        ("LASIT. PARV", ["balcony"]),
        ("TERASSI", ["terrace"]),
        ("RT", ["dining"]),
        ("SOV", ["bedroom"]),
        ("KÖK", ["kitchen"]),
        ("BAD", ["bathroom"]),
        ("BALK", ["balcony"]),
        ("MH 8,0 m2", ["bedroom"]),
        ("OH+K", ["living", "kitchen"]),
        ("K/RT", ["kitchen", "dining"]),
        ("K", ["kitchen"]),
        ("S 4,5 m²", ["sauna"]),
        ("1200", []),
        ("IS*5", []),
        ("kaide h=1100", []),
    ],
)
def test_glossary_maps_labels_to_schema_types(text, types):
    assert glossary.ocr_room_types(text) == types


def test_glossary_types_are_in_the_schema_enum():
    schema = json.loads((REPO / "translator/schema/drawing.schema.json").read_text(encoding="utf-8"))
    enum = set(schema["$defs"]["room"]["properties"]["type"]["enum"])
    assert set(glossary._FI_SV) <= enum


@pytest.mark.parametrize(
    "text, types",
    [
        ("0H", ["living"]),  # 0 read for O
        ("MH1", ["bedroom"]),  # numbered rooms
        ("MH 2", ["bedroom"]),
        ("mh", ["bedroom"]),  # case
        ("KEITTIO", ["kitchen"]),  # missing dots
        ("Kylpyhuone", ["bathroom"]),
        ("W.C.", ["wc"]),
        ("VVC", ["wc"]),
        ("K ABC", []),  # a single letter next to an unknown word is not trusted
        ("S-1200 KAAPPI", []),
        ("H39", []),  # a letter with digits is a dimension or a code, not a room number
        ("K115116", []),
        ("S0", []),
    ],
)
def test_ocr_noise_is_normalised(text, types):
    assert glossary.ocr_room_types(text) == types


def test_outdoor_types():
    for label in ("PARVEKE", "TERASSI", "LASIT. PARV", "loggia"):
        (t,) = glossary.ocr_room_types(label)
        assert t in glossary.OUTDOOR_TYPES


@pytest.mark.parametrize(
    "text, title",
    [
        ("1. KRS", "1 krs"),
        ("1.KRS", "1 krs"),
        ("2. KERROS", "2 krs"),
        ("KELLARI", "kellari"),
        ("ULLAKKO", "ullakko"),
        ("1st floor", "1st floor"),
        ("MH", None),
        ("KRS", None),
    ],
)
def test_level_titles(text, title):
    assert glossary.level_title(text) == title


@pytest.mark.parametrize(
    "text, summary",
    [
        ("1H+KK+ALK+S", True),
        ("H+KK+S", True),
        ("H+TK+S", True),
        ("2 H+K+S", True),
        ("1-2H+TK+KPH", True),
        ("3H+KK 70M", True),
        ("OH+RT+K", False),  # one open room: living + dining + kitchen
        ("KHH+PH", False),
        ("ET+porras", False),
        ("KEITTIO+RUOK", False),
        ("MH", False),
        ("H", False),
    ],
)
def test_apartment_summaries(text, summary):
    assert glossary.is_unit_summary(text) is summary
    assert (ocr.text_role(text) == "unit_label") is summary


@pytest.mark.parametrize(
    "text, role",
    [
        ("MH 8,0 m2", "room_label"),
        ("7,0 m2", "area"),
        ("1200", "dimension"),
        ("2. KERROS", "level_title"),
        ("IS*5", "other"),
    ],
)
def test_text_roles(text, role):
    assert ocr.text_role(text) == role


# ------------------------------------------------------------------ boxes back to original pixels


def test_boxes_map_back_through_upscale_and_flip():
    poly = [[10, 4], [30, 4], [30, 14], [10, 14]]
    assert ocr.to_original(poly, 2, False, 200) == [[5, 2], [15, 2], [15, 7], [5, 7]]
    # flipped copy of a 200 px wide (upscaled) image: x -> 200 - x, then / 2
    assert ocr.to_original(poly, 2, True, 200) == [[95, 2], [85, 2], [85, 7], [95, 7]]


def _item(text, score=0.9, box=(0, 0, 10, 10)):
    x0, y0, x1, y1 = box
    return {"text": text, "score": score, "poly": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def test_mirrored_plan_reads_the_flipped_copy():
    raw = {
        "orientations": {
            "original": [_item("HM", 0.6), _item("HK", 0.5), _item("1200", 0.9)],
            "flipped": [_item("MH", 0.95), _item("KH", 0.9), _item("0021", 0.6)],
        }
    }
    side, items, stats = ocr.choose_orientation(raw)
    assert side == "flipped" and items[0]["text"] == "MH"
    assert stats["flipped"]["hits"] == 2 and stats["original"]["hits"] == 0


def test_normal_plan_reads_as_drawn():
    raw = {
        "orientations": {
            "original": [_item("MH", 0.95), _item("OH", 0.9)],
            "flipped": [_item("HM", 0.5), _item("HO", 0.6)],
        }
    }
    assert ocr.choose_orientation(raw)[0] == "original"
    # no hits either way: read as drawn, even if the flipped copy scores higher
    raw = {"orientations": {"original": [_item("1200", 0.5)], "flipped": [_item("0021", 0.9)]}}
    assert ocr.choose_orientation(raw)[0] == "original"
    # equal hits: flipped only when clearly more confident
    raw = {"orientations": {"original": [_item("WC", 0.6)], "flipped": [_item("WC", 0.62)]}}
    assert ocr.choose_orientation(raw)[0] == "original"
    raw = {"orientations": {"original": [_item("WC", 0.6)], "flipped": [_item("WC", 0.9)]}}
    assert ocr.choose_orientation(raw)[0] == "flipped"


# ------------------------------------------------------------------ rooms


def _room(rid, cls, rtype, box, conf=0.8):
    x0, y0, x1, y1 = box
    return {
        "id": rid,
        "name": None,
        "number": None,
        "label": None,
        "type": rtype,
        "subtype": cls,
        "is_outdoor": rtype == "outdoor",
        "polygon_px": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        "polygon_m": None,
        "area_m2": None,
        "confidence": conf,
        "provenance": {"extractor": "cubicasa5k", "source_ref": f"room:{rid}", "file": "x.png"},
        "status": "active",
        "flags": [],
    }


def _texts(*items):
    return ocr.texts_from_items(list(items), "original", "x.png", "test")


def test_label_names_the_room_that_holds_its_centre():
    rooms = [
        _room("a", "Bedroom", "bedroom", (0, 0, 100, 100)),
        _room("b", "Undefined", "unknown", (110, 0, 200, 100)),
    ]
    texts = _texts(_item("SAUNA", 0.95, (140, 40, 170, 50)))
    out, stats = ocr.name_rooms(rooms, texts, "x.png")
    b = next(r for r in out if r["id"] == "b")
    assert b["type"] == "sauna" and b["name"] == "SAUNA" and b["confidence"] == pytest.approx(0.95)
    assert b["provenance"]["details"]["seg_type"] == "unknown" and texts[0]["room_id"] == "b"
    a = next(r for r in out if r["id"] == "a")
    assert a["type"] == "bedroom" and a["name"] is None  # unlabelled: keeps the segmentation class
    assert stats["named_regions"] == 1 and stats["label_only"] == 0


def test_label_in_no_region_becomes_a_low_confidence_room():
    rooms = [_room("a", "Bedroom", "bedroom", (0, 0, 100, 100))]
    texts = _texts(_item("PARVEKE", 0.9, (300, 300, 340, 310)))
    out, stats = ocr.name_rooms(rooms, texts, "x.png")
    (r,) = [r for r in out if r["id"] != "a"]
    assert r["type"] == "balcony" and r["is_outdoor"] and r["name"] == "PARVEKE"
    assert "label_only" in r["flags"] and r["confidence"] == pytest.approx(0.27)
    assert r["polygon_px"] == texts[0]["polygon_px"] and r["provenance"]["extractor"] == "ocr"
    assert texts[0]["room_id"] == r["id"] and stats["label_only"] == 1


def test_two_labels_in_one_region_make_two_rooms():
    rooms = [_room("a", "Bath", "bathroom", (0, 0, 100, 100))]
    texts = _texts(_item("KH", 0.9, (40, 40, 50, 50)), _item("SAUNA", 0.8, (80, 80, 95, 90)))
    out, _ = ocr.name_rooms(rooms, texts, "x.png")
    types = sorted(r["type"] for r in out)
    assert types == ["bathroom", "sauna"]
    sauna = next(r for r in out if r["type"] == "sauna")
    assert sauna["parent_room_id"] == "a" and "shares_region" in sauna["flags"]


def test_combined_label_names_two_rooms():
    rooms = [_room("a", "Living room", "living", (0, 0, 100, 100))]
    out, _ = ocr.name_rooms(rooms, _texts(_item("OH+K", 0.9, (40, 40, 60, 50))), "x.png")
    assert sorted(r["type"] for r in out) == ["kitchen", "living"]


def test_label_priority_steps_back_unlabelled_regions_of_a_labelled_type():
    rooms = [
        _room("a", "Kitchen", "kitchen", (0, 0, 50, 50)),
        _room("b", "Kitchen", "kitchen", (200, 0, 250, 50)),
    ]
    texts = _texts(_item("K", 0.9, (20, 20, 25, 25)))
    out, _ = ocr.name_rooms([dict(r) for r in rooms], texts, "x.png")
    assert sum(r["type"] == "kitchen" for r in out) == 2
    out, _ = ocr.name_rooms(
        [dict(r) for r in rooms], _texts(_item("K", 0.9, (20, 20, 25, 25))), "x.png", True
    )
    assert sum(r["type"] == "kitchen" for r in out) == 1


def test_touching_same_class_regions_merge():
    rooms = [
        _room("k0", "Kitchen", "kitchen", (0, 0, 50, 50), 0.9),
        _room("k1", "Kitchen", "kitchen", (52, 0, 100, 50), 0.5),  # 2 px gap: same room
        _room("k2", "Kitchen", "kitchen", (300, 0, 350, 50)),  # far away: a second kitchen
        _room("b0", "Bedroom", "bedroom", (0, 52, 50, 100)),  # other class: never merged
    ]
    out, merges = ocr.merge_rooms(rooms)
    active = [r for r in out if r["status"] == "active"]
    assert sum(r["type"] == "kitchen" for r in active) == 2 and len(merges) == 1
    (m,) = [r for r in active if "merged" in r["flags"]]
    assert m["provenance"]["details"]["merged_from"] == ["k0", "k1"]
    assert 0.5 < m["confidence"] < 0.9
    assert {r["id"]: r.get("superseded_by") for r in out if r["status"] == "superseded"} == {
        "k0": m["id"],
        "k1": m["id"],
    }


# ------------------------------------------------------------------ storeys


def _seg_raw(boxes, h=200, w=400):
    cls = np.zeros((h, w), np.uint8)
    for x0, y0, x1, y1 in boxes:
        cls[y0:y1, x0:x1] = cc.ROOM_CLASSES.index("Wall")
        cls[y0 + 3 : y1 - 3, x0 + 3 : x1 - 3] = cc.ROOM_CLASSES.index("Bedroom")
    prob = np.ones((h, w), np.float32)
    icons = np.zeros((h, w), np.uint8)
    return cc.raw_from_maps(cls, prob, icons, prob, meta={"name": "s", "transforms": {}})


def test_building_blobs_and_levels():
    raw = _seg_raw([(10, 10, 150, 150), (220, 10, 380, 150)])
    blobs = ocr.building_blobs(raw)
    assert len(blobs) == 2
    lv, cov = ocr.levels([], blobs, "x.png")  # no titles: one level per blob
    assert [x["name"] for x in lv] == ["floor 1", "floor 2"] and cov["status"] == "analysed"
    texts = _texts(_item("1. KRS", 0.9, (20, 160, 60, 170)), _item("2. KERROS", 0.9, (230, 160, 280, 170)))
    lv, cov = ocr.levels(texts, blobs, "x.png")
    assert [x["name"] for x in lv] == ["1. KRS", "2. KERROS"] and cov["status"] == "analysed"
    lv, cov = ocr.levels(texts[:1], blobs, "x.png")  # 1 title, 2 blobs: disagree
    assert len(lv) == 1 and cov["status"] == "partial" and cov["confidence"] <= 0.3
    assert "check_this" in lv[0]["flags"]


# ------------------------------------------------------------------ the whole raster document


@pytest.fixture
def plan(tmp_path, monkeypatch):
    folder = tmp_path / "cubicasa5k"
    folder.mkdir()
    ok, buf = cv2.imencode(".png", np.full((200, 400, 3), 255, np.uint8))
    buf.tofile(str(folder / "synthetic.png"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    monkeypatch.setattr(cc, "raw_dir", lambda: raw_dir)
    monkeypatch.setattr(ocr, "raw_dir", lambda: raw_dir)
    seg = _seg_raw([(10, 10, 150, 150), (150, 10, 300, 150)])  # one building, two bedroom regions
    (raw_dir / "synthetic.seg.json").write_text(json.dumps(seg), encoding="utf-8")
    return folder / "synthetic.png", tmp_path, raw_dir


def _write_ocr(raw_dir, original, flipped=()):
    raw = {"name": "synthetic", "width_px": 400, "height_px": 200, "ocr_upscale": 2,
           "orientations": {"original": list(original), "flipped": list(flipped)},
           "model": {"rec_model": "latin_test_rec"}, "cost_usd": 0.0001}  # fmt: skip
    (raw_dir / "synthetic.ocr.json").write_text(json.dumps(raw), encoding="utf-8")


def test_translate_all_names_rooms_and_writes_texts(plan):
    path, root, raw_dir = plan
    items = [
        _item("MH", 0.9, (60, 60, 80, 70)),
        _item("SAUNA", 0.9, (200, 60, 240, 70)),
        _item("PARVEKE", 0.8, (320, 160, 380, 170)),  # outside the building: a label-only room
        _item("10,5 m2", 0.9, (60, 75, 90, 85)),
    ]
    _write_ocr(raw_dir, items)
    (doc,) = translate_all(str(path), str(root))  # schema-validated inside
    act = [r for r in doc["rooms"] if r.get("status", "active") == "active"]
    assert sorted(r["type"] for r in act if r["provenance"]["extractor"] != "prior") == [
        "balcony", "bedroom", "sauna",
    ]  # fmt: skip
    # none read: the kitchen and bathroom priors assume one each, without geometry
    assert sorted(r["type"] for r in act if "assumed_prior" in r["flags"]) == ["bathroom", "kitchen"]
    assert {t["role"] for t in doc["texts"]} == {"room_label", "area"}
    assert all(t["provenance"]["extractor"] == "ocr" for t in doc["texts"])
    cov = {c["category"]: c for c in doc["coverage"]}
    assert cov["text"]["status"] == "analysed" and cov["text"]["extractor"] == "ocr"
    assert cov["shower"]["status"] == "not_analysed"
    assert cov["floors"]["status"] == "analysed" and len(doc["levels"]) == 1
    assert {"kind": "other", "note": "mirrored"} not in doc["drawing"]["transforms"]
    ask = lambda q: probe.answer([doc], "cubicasa5k", q)[1]  # noqa: E731
    assert ask("How many saunas are there in this apartment?") == 1
    assert ask("How many balconies does this apartment have?") == 1
    assert ask("How many floors (storeys) does this plan show?") == 1


def test_translate_all_records_a_mirrored_plan(plan):
    path, root, raw_dir = plan
    _write_ocr(raw_dir, [_item("HM", 0.5, (60, 60, 80, 70))], [_item("MH", 0.95, (60, 60, 80, 70))])
    (doc,) = translate_all(str(path), str(root))
    assert {"kind": "other", "note": "mirrored"} in doc["drawing"]["transforms"]
    assert doc["texts"][0]["text"] == "MH" and doc["texts"][0]["provenance"]["source_ref"] == "ocr:flipped:0"


def test_ocr_size_mismatch_fails_loudly(plan):
    path, root, raw_dir = plan
    _write_ocr(raw_dir, [])
    raw = json.loads((raw_dir / "synthetic.ocr.json").read_text(encoding="utf-8"))
    raw["width_px"] = 999
    (raw_dir / "synthetic.ocr.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="OCR size differs"):
        translate_all(str(path), str(root))


# ------------------------------------------------------- rotated pass, kitchen fallback, floors rule


def test_rotated_boxes_map_back_to_original_pixels():
    # original 100 wide x 60 high, upscaled x2 -> 200 x 120; rotated clockwise -> 120 wide x 200 high
    img = np.zeros((120, 200), np.uint8)
    img[20:30, 50:90] = 255  # a box in the (upscaled) original
    rot = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    ys, xs = np.nonzero(rot)
    poly = [
        [xs.min(), ys.min()],
        [xs.max() + 1, ys.min()],
        [xs.max() + 1, ys.max() + 1],
        [xs.min(), ys.max() + 1],
    ]
    back = ocr.to_original(poly, 2, False, rot.shape[1], rotated=True)
    assert ocr._bbox(back) == [25, 10, 45, 15]  # x 50..90 / 2, y 20..30 / 2


def _tall(text, score=0.9, x=0):
    return {"text": text, "score": score, "poly": [[x, 0], [x + 10, 0], [x + 10, 40], [x, 40]]}


def test_rotated_pass_trigger():
    assert ocr.needs_rotation([_tall("E"), _tall("o", x=50)])[0]  # tall boxes: vertical text
    assert ocr.needs_rotation([_item(t, 0.3) for t in "abcd"])[0]  # mostly low confidence
    assert not ocr.needs_rotation([_item("MH", 0.95), _item("KH", 0.9), _tall("E")])[0]


def test_rotated_boxes_merge_per_box():
    base = [
        {**_item("E", 0.6, (0, 0, 10, 40)), "orientation": "original"},  # misread vertical KH
        {**_item("MH", 0.95, (50, 0, 70, 10)), "orientation": "original"},
        {**_item("OH", 0.9, (100, 0, 120, 10)), "orientation": "original"},
    ]
    rotated = [
        _item("KH", 0.99, (0, 0, 10, 40)),  # higher score over a misread: replaces it
        _item("MN", 0.7, (50, 0, 70, 10)),  # lower score, no room name: the normal reading stays
        _item("0H", 0.5, (100, 0, 120, 10)),  # names a room, but so does the normal reading: stays
        _item("ET", 0.6, (200, 0, 210, 40)),  # overlaps nothing, names a room: added
        _item("1200", 0.99, (300, 0, 310, 40)),  # overlaps nothing, no room name: dropped
    ]
    items, taken = ocr.merge_rotated(base, rotated)
    assert taken == 2
    assert sorted((i["text"], i["orientation"]) for i in items) == [
        ("ET", "rotated"),
        ("KH", "rotated"),
        ("MH", "original"),
        ("OH", "original"),
    ]


def test_kitchen_fallback_counts_the_segmentation_class_once():
    rooms = [
        _room("k0", "Kitchen", "kitchen", (0, 0, 80, 80)),
        _room("k1", "Kitchen", "kitchen", (200, 0, 210, 10)),  # small fragment
        _room("b0", "Bedroom", "bedroom", (300, 0, 380, 80)),
    ]
    texts = _texts(_item("MH", 0.9, (330, 30, 340, 40)))
    out, stats = ocr.name_rooms(
        [dict(r) for r in rooms], texts, "x.png", label_priority=True, kitchen_fallback=True
    )
    kitchens = [r for r in out if r["type"] == "kitchen"]
    assert [r["id"] for r in kitchens] == ["k0"] and stats["kitchen_fragments"] == 1
    assert "kitchen_fragment" in next(r for r in out if r["id"] == "k1")["flags"]
    # with a kitchen label, the label decides (label priority); the fallback does nothing
    texts = _texts(_item("K", 0.9, (205, 2, 208, 8)))
    out, _ = ocr.name_rooms(
        [dict(r) for r in rooms], texts, "x.png", label_priority=True, kitchen_fallback=True
    )
    assert [r["id"] for r in out if r["type"] == "kitchen"] == ["k1"]


def test_floors_rule_max_takes_the_larger_count():
    raw = _seg_raw([(10, 10, 150, 150), (220, 10, 380, 150)])
    blobs = ocr.building_blobs(raw)
    one_title = _texts(_item("1. KRS", 0.9, (20, 160, 60, 170)))
    lv, cov = ocr.levels(one_title, blobs, "x.png", rule="titles")
    assert len(lv) == 1 and cov["status"] == "partial"
    lv, cov = ocr.levels(one_title, blobs, "x.png", rule="max")
    assert len(lv) == 2 and cov["status"] == "partial" and cov["confidence"] <= 0.3  # still flagged
    assert lv[0]["provenance"]["details"]["titles"] == ["1. KRS"]


def test_stray_single_letter_k_outside_rooms_makes_no_second_kitchen():
    rooms = [_room("k0", "Kitchen", "kitchen", (0, 0, 80, 80))]
    out, stats = ocr.name_rooms(
        [dict(r) for r in rooms], _texts(_item("K", 0.9, (300, 300, 305, 310))), "x.png"
    )
    assert [r["id"] for r in out if r["type"] == "kitchen"] == ["k0"] and stats["stray_kitchen_skipped"] == 1
    # a longer label outside the rooms still makes a room, and so does a stray K when there is no kitchen
    out, _ = ocr.name_rooms(
        [dict(r) for r in rooms], _texts(_item("KEITTIÖ", 0.9, (300, 300, 340, 310))), "x.png"
    )
    assert sum(r["type"] == "kitchen" for r in out) == 2
    out, _ = ocr.name_rooms([], _texts(_item("K", 0.9, (300, 300, 305, 310))), "x.png")
    assert [r["type"] for r in out] == ["kitchen"]


def test_bedrooms_count_labels_plus_uncovered_regions():
    rooms = [
        _room("b0", "Bedroom", "bedroom", (0, 0, 80, 80)),
        _room("b1", "Bedroom", "bedroom", (100, 0, 180, 80)),  # unlabelled bedroom region
        _room("b2", "Bedroom", "bedroom", (200, 0, 280, 80)),  # labelled VH: a walk-in closet, not a bedroom
    ]
    texts = _texts(_item("MH", 0.9, (30, 30, 40, 40)), _item("VH", 0.9, (230, 30, 240, 40)))
    out, _ = ocr.name_rooms([dict(r) for r in rooms], texts, "x.png", label_priority=True)
    assert sorted(r["id"] for r in out if r["type"] == "bedroom") == ["b0", "b1"]
