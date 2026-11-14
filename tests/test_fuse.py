"""Step 8: duplicate merging, count priors, coverage grid and scale (synthetic documents, no GPU)."""

from __future__ import annotations

import copy

import pytest

from translator import coverage, fuse, scale
from translator.store import validate

IMG = "data/cubicasa5k/x.png"
PAGE = ("rooms", "doors", "windows", "fixtures", "text")


def _page(cat, status="analysed", extractor="cubicasa5k", conf=0.9):
    return {"category": cat, "region": "page", "status": status, "extractor": extractor, "confidence": conf}


def _doc(rooms=(), openings=(), objects=(), texts=(), w=400, h=400, dpi=None):
    image = {"width_px": w, "height_px": h, "dpi": dpi}
    return {
        "schema_version": "0.2",
        "drawing": {"file": IMG, "source": "cubicasa5k", "route": "raster", "read_from": IMG,
                    "image": image, "transforms": []},  # fmt: skip
        "levels": [], "units": [], "walls": [], "edges": [],
        "rooms": list(rooms), "openings": list(openings), "objects": list(objects), "texts": list(texts),
        "coverage": [_page(c) for c in PAGE]
        + [_page("floors", "partial", "ocr", 0.3), _page("shower", "not_analysed", conf=0.0)],
        "history": [],
    }  # fmt: skip


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _opening(oid, kind, box, conf):
    return {"id": oid, "kind": kind, "bbox_px": list(map(float, box)), "polygon_px": _box(*box),
            "confidence": conf, "provenance": {"extractor": "cubicasa5k", "source_ref": oid},
            "status": "active"}  # fmt: skip


def _room(rid, rtype, box=None, name=None, flags=(), parent=None, conf=0.9):
    return {"id": rid, "name": name, "number": None, "label": name, "type": rtype, "subtype": None,
            "is_outdoor": False, "parent_room_id": parent, "polygon_px": _box(*box) if box else None,
            "polygon_m": None, "area_m2": None, "confidence": conf,
            "provenance": {"extractor": "cubicasa5k", "source_ref": rid, "details": {}},
            "status": "active", "flags": list(flags),
            **({"text_ids": [f"t-{rid}"]} if name else {})}  # fmt: skip


def _count(doc, rtype):
    return sum(r["type"] == rtype and r.get("status", "active") == "active" for r in doc["rooms"])


def _cov(doc, cat):
    return next(c for c in doc["coverage"] if c["category"] == cat and c["region"] == "page")


# ------------------------------------------------------------------ duplicates


def test_duplicates_keep_the_higher_confidence_and_delete_nothing():
    a = _opening("w-a", "window", (0, 0, 100, 10), 0.6)
    b = _opening("w-b", "window", (5, 0, 100, 10), 0.9)  # IoU 0.95 with a
    c = _opening("d-c", "door", (0, 0, 100, 10), 0.99)  # same place, other category
    d = _opening("w-d", "window", (70, 0, 170, 10), 0.5)  # IoU with b ~0.18
    doc = _doc(openings=[a, b, c, d])
    recs = fuse.dedupe_document(doc)
    assert recs == [{"kept": "w-b", "superseded": "w-a", "iou": 0.95}]
    assert len(doc["openings"]) == 4
    assert a["status"] == "superseded" and a["superseded_by"] == "w-b" and "duplicate" in a["flags"]
    assert b["status"] == c["status"] == d["status"] == "active"
    assert doc["history"][-1]["action"] == "dedupe"


def test_elements_without_geometry_never_match():
    r1, r2 = _room("r1", "kitchen"), _room("r2", "kitchen")
    assert fuse.dedupe([r1, r2], lambda x: x["type"]) == []


# ------------------------------------------------------------------ priors


def test_prior_zero_detected_assumes_the_majority():
    doc = _doc(rooms=[_room("b", "bedroom", (0, 0, 50, 50), "MH")])
    res = fuse.apply_prior(doc, "cc_kitchens")
    assert res == {"branch": "zero", "detected": 0, "final": 1, "strong": 0, "fired": True}
    assert _count(doc, "kitchen") == 1
    cov = _cov(doc, "kitchens")
    assert cov["status"] == "partial" and cov["note"].startswith(
        "prior: assumed 1, not read from the drawing"
    )


def test_assumed_element_shape_is_honest_and_schema_valid():
    doc = _doc()
    fuse.apply_prior(doc, "cc_bathrooms")
    (r,) = doc["rooms"]
    assert r["flags"] == ["assumed_prior"]
    assert r["polygon_px"] is None and r["polygon_m"] is None and "bbox_px" not in r
    assert r["confidence"] <= 0.3
    assert r["provenance"]["extractor"] == "prior" and r["provenance"]["source_ref"] == "prior:cc_bathrooms"
    assert r["type"] == "bathroom" and r["status"] == "active"
    coverage.build(doc)
    validate(doc)
    # never evidence on the page: it is in no coverage cell
    cells = [c for c in doc["coverage"] if c["category"] == "rooms" and c["region"] != "page"]
    assert all(c["note"] == "no elements found" for c in cells)


def test_prior_equal_keeps_the_count():
    doc = _doc(rooms=[_room("k", "kitchen", (0, 0, 50, 50), "K")])
    res = fuse.apply_prior(doc, "cc_kitchens")
    assert res["branch"] == "equal" and not res["fired"]
    assert _count(doc, "kitchen") == 1 and len(doc["rooms"]) == 1
    assert _cov(doc, "kitchens")["status"] == "analysed"


def test_prior_differs_with_two_strong_signals_keeps_the_detected_count():
    doc = _doc(
        rooms=[_room("k1", "kitchen", (0, 0, 50, 50), "K"), _room("k2", "kitchen", (100, 0, 150, 50), "KK")]
    )
    res = fuse.apply_prior(doc, "cc_kitchens")
    assert res == {"branch": "kept_strong", "detected": 2, "final": 2, "strong": 2, "fired": False}
    assert _count(doc, "kitchen") == 2
    assert "kept: 2 strong signals" in _cov(doc, "kitchens")["note"]


@pytest.mark.parametrize(
    "second",
    [
        _room("k2", "kitchen", (100, 0, 150, 50), "K", flags=["label_only", "low_confidence"]),  # no region
        _room("k2", "kitchen", (10, 10, 20, 20), "kk", flags=["shares_region"], parent="k1"),  # same region
        _room("k2", "kitchen", (100, 0, 150, 50), "TK"),  # TK is never strong
        _room("k2", "kitchen", (100, 0, 150, 50)),  # segmentation class only, no label
    ],
    ids=["label_only", "same_region", "tk", "unlabelled"],
)
def test_prior_differs_without_two_strong_signals_falls_back(second):
    first = _room("k1", "kitchen", (0, 0, 50, 50), "K", conf=0.95)
    doc = _doc(rooms=[first, second])
    res = fuse.apply_prior(doc, "cc_kitchens")
    assert res["branch"] == "fallback" and res["fired"] and res["final"] == 1
    assert _count(doc, "kitchen") == 1 and len(doc["rooms"]) == 2  # nothing deleted, nothing assumed
    assert first["type"] == "kitchen"
    assert second["type"] == "unknown" and "prior_stepped_back" in second["flags"]
    assert second["provenance"]["details"]["prior_type"] == "kitchen"
    assert _cov(doc, "kitchens")["note"].startswith("prior: assumed 1, not read from the drawing")


def test_prior_fallback_below_the_majority_adds_assumed_rooms():
    prior = {"type": "bathroom", "majority": 2, "share": 0.8, "category": "bathrooms"}
    doc = _doc(rooms=[_room("b1", "bathroom", (0, 0, 50, 50), "KH")])
    res = fuse.apply_prior(doc, "cc_bathrooms", prior)
    assert res["branch"] == "fallback" and res["final"] == 2
    assert _count(doc, "bathroom") == 2
    assert [r["id"] for r in doc["rooms"] if "assumed_prior" in r["flags"]] == ["room-prior-cc_bathrooms-0"]


def test_priors_cover_kitchens_and_bathrooms_only():
    assert set(fuse.PRIORS) == {"cc_kitchens", "cc_bathrooms"}
    assert all(p["majority"] == 1 for p in fuse.PRIORS.values())


# ------------------------------------------------------------------ coverage


def test_coverage_grid_per_category_and_cell():
    doors = [_opening("d0", "door", (10, 10, 40, 20), 0.9), _opening("d1", "door", (350, 350, 390, 360), 0.3)]
    doc = _doc(openings=doors)
    coverage.build(doc, grid=4)
    recs = [c for c in doc["coverage"] if c["category"] == "doors"]
    page = next(c for c in recs if c["region"] == "page")
    cells = [c for c in recs if c["region"] != "page"]
    assert len(cells) == 16 and cells[0]["region"] == [0.0, 0.0, 100.0, 100.0]
    assert page["status"] == "analysed" and page["confidence"] == pytest.approx(0.6)
    assert cells[0]["status"] == "analysed" and cells[0]["confidence"] == 0.9
    assert cells[15]["status"] == "partial" and cells[15]["confidence"] == 0.3
    assert cells[5]["status"] == "analysed" and cells[5]["note"] == "no elements found"
    # other categories are kept as they were: floors partial 0.3, shower not_analysed
    assert _cov(doc, "floors")["status"] == "partial" and _cov(doc, "floors")["confidence"] == 0.3
    assert _cov(doc, "shower")["status"] == "not_analysed"
    assert {c["category"] for c in doc["coverage"]} >= {"rooms", "doors", "windows", "fixtures", "text"}
    validate(doc)


def test_coverage_not_analysed_stays_not_analysed_in_every_cell():
    doc = _doc()
    for c in doc["coverage"]:
        if c["category"] == "text":
            c.update(status="not_analysed", confidence=0.0)
    coverage.build(doc)
    text = [c for c in doc["coverage"] if c["category"] == "text"]
    assert len(text) == 17 and all(c["status"] == "not_analysed" and c["confidence"] == 0 for c in text)


def test_coverage_ignores_superseded_elements():
    a = _opening("w-a", "window", (0, 0, 10, 10), 0.2)
    a.update(status="superseded", superseded_by="w-b")
    doc = _doc(openings=[a, _opening("w-b", "window", (0, 0, 10, 10), 0.8)])
    coverage.build(doc)
    assert _cov(doc, "windows")["confidence"] == 0.8


# ------------------------------------------------------------------ scale


def _text(tid, text, role="other"):
    return {"id": tid, "text": text, "role": role, "confidence": 0.9, "bbox_px": [0, 0, 10, 10],
            "provenance": {"extractor": "ocr", "source_ref": tid}, "status": "active"}  # fmt: skip


def test_scale_from_door_width():
    doors = [_opening(f"d{i}", "door", (0, 0, 40, 4), 0.9) for i in range(3)]
    s = scale.estimate(_doc(openings=doors))
    assert s["source"] == "door_width" and s["confidence"] == 0.2
    assert s["m_per_px"] == pytest.approx(0.85 / 40)


def test_scale_note_needs_a_known_dpi():
    doors = [_opening(f"d{i}", "door", (0, 0, 40, 4), 0.9) for i in range(3)]
    doc = _doc(openings=doors, texts=[_text("t0", "Pohjapiirros 1:50")], dpi=150)
    s = scale.estimate(doc)
    assert s["source"] == "scale_note" and s["m_per_px"] == pytest.approx(0.0254 / 150 * 50)
    assert doc["texts"][0]["role"] == "scale_note"
    nodpi = copy.deepcopy(doc)
    nodpi["drawing"]["image"]["dpi"] = None
    assert scale.estimate(nodpi)["source"] == "door_width"


def test_scale_none_and_vector_units_kept():
    s = scale.estimate(_doc(openings=[_opening("d", "door", (0, 0, 40, 4), 0.9)]))
    assert s == {**s, "source": "none", "confidence": 0.0, "m_per_px": None}
    doc = _doc()
    doc["drawing"]["scale"] = {"m_per_px": 0.01, "source": "vector_units", "confidence": 1.0}
    assert scale.estimate(doc) is doc["drawing"]["scale"]
