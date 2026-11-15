"""Step 6: CubiCasa5K post-processing from synthetic class maps (no GPU, no network)."""

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

from translator import translate_all  # noqa: E402
from translator.config import data_root, questions_path  # noqa: E402
from translator.extract import cubicasa as cc  # noqa: E402

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)
R = {name: i for i, name in enumerate(cc.ROOM_CLASSES)}
I = {name: i for i, name in enumerate(cc.ICON_CLASSES)}  # noqa: E741


def maps(h=200, w=300):
    """Synthetic class maps: rooms (bedroom, bath, outdoor, wall) and icons (2 windows, 1 door, fixtures)."""
    room_cls = np.zeros((h, w), np.uint8)
    room_cls[10:100, 10:150] = R["Bedroom"]
    room_cls[10:100, 160:290] = R["Bath"]
    room_cls[110:190, 10:150] = R["Outdoor"]
    room_cls[105:108, :] = R["Wall"]
    room_cls[150:152, 200:202] = R["Kitchen"]  # 4 px: below the room filter
    room_prob = np.full((h, w), 0.9, np.float32)
    room_prob[10:100, 160:290] = 0.6
    icon_cls = np.zeros((h, w), np.uint8)
    icon_cls[5:9, 20:60] = I["Window"]  # 160 px
    icon_cls[5:9, 200:240] = I["Window"]  # 160 px, separate
    icon_cls[60:62, 60:62] = I["Window"]  # 4 px speck: dropped by min_icon_px
    icon_cls[100:104, 40:70] = I["Door"]
    icon_cls[30:45, 170:185] = I["Toilet"]
    icon_cls[30:45, 200:215] = I["Sink"]
    icon_cls[50:90, 220:280] = I["Bathtub"]
    icon_cls[150:160, 20:60] = I["Sauna bench"]
    icon_cls[150:165, 100:115] = I["Closet"]
    icon_prob = np.full((h, w), 0.8, np.float32)
    return room_cls, room_prob, icon_cls, icon_prob


def test_components_are_polygons_in_image_pixels():
    room_cls, room_prob, icon_cls, icon_prob = maps()
    comps = cc.components(icon_cls, icon_prob, cc.ICON_CLASSES)
    wins = [c for c in comps if c["class"] == "Window"]
    assert [c["k"] for c in wins] == [0, 1, 2]
    big = wins[0]
    assert big["area_px"] == 160 and big["bbox"] == [20, 5, 60, 9]
    xs = [p[0] for p in big["polygon"]]
    ys = [p[1] for p in big["polygon"]]
    assert (min(xs), min(ys), max(xs), max(ys)) == (20, 5, 59, 8)
    assert big["mean_prob"] == pytest.approx(0.8)


def test_raw_filter_drops_specks():
    room_cls, room_prob, icon_cls, icon_prob = maps()
    comps = cc.components(icon_cls, icon_prob, cc.ICON_CLASSES, min_px=10)
    assert sum(c["class"] == "Window" for c in comps) == 2


def test_merged_components_are_not_split():
    icon_cls = np.zeros((50, 100), np.uint8)
    icon_cls[10:14, 10:40] = I["Window"]
    icon_cls[10:14, 40:70] = I["Window"]  # touching: one component
    comps = cc.components(icon_cls, np.ones_like(icon_cls, np.float32), cc.ICON_CLASSES)
    assert len(comps) == 1 and comps[0]["area_px"] == 240


def _raw():
    return cc.raw_from_maps(
        *maps(), meta={"name": "synthetic", "transforms": {"scale_x": 1.0, "scale_y": 1.0}}
    )


def test_to_parts_maps_classes_to_the_schema():
    parts = cc.to_parts(_raw(), "data/cubicasa5k/synthetic.png", min_icon_px=16, min_room_px=200)
    kinds = sorted(o["kind"] for o in parts["openings"])
    assert kinds == ["door", "window", "window"]  # the 4 px window speck is dropped
    cats = sorted((o["category"], o.get("subtype")) for o in parts["objects"])
    assert cats == [
        ("bathtub", None),
        ("other", "closet"),
        ("sauna_bench", None),
        ("sink", None),
        ("toilet", None),
    ]
    rooms = {r["type"]: r for r in parts["rooms"]}
    assert set(rooms) == {"bedroom", "bathroom", "outdoor"}  # no wall, no tiny kitchen
    assert rooms["outdoor"]["is_outdoor"] and not rooms["bedroom"]["is_outdoor"]
    assert rooms["bathroom"]["confidence"] == pytest.approx(0.6)


def test_min_icon_px_is_tunable():
    raw = _raw()
    n = [len(cc.to_parts(raw, "x.png", min_icon_px=m)["openings"]) for m in (1, 16, 161)]
    assert n == [4, 3, 0]


def test_provenance_ids_and_coverage():
    parts = cc.to_parts(_raw(), "data/cubicasa5k/synthetic.png")
    win = next(o for o in parts["openings"] if o["kind"] == "window")
    assert win["provenance"]["extractor"] == "cubicasa5k"
    assert win["provenance"]["source_ref"] == "icon:window:0"
    assert win["bbox_px"] == [20.0, 5.0, 59.0, 8.0]
    cov = {c["category"]: c for c in parts["coverage"]}
    assert cov["shower"]["status"] == "not_analysed"
    assert cov["windows"]["status"] == "analysed" and cov["windows"]["confidence"] == pytest.approx(0.8)
    every = parts["rooms"] + parts["openings"] + parts["objects"]
    assert all(0 <= e["confidence"] <= 1 and e["provenance"]["source_ref"] for e in every)


def test_model_resize_is_recorded_as_a_transform():
    raw = _raw()
    raw["transforms"] = {"scale_x": 0.5, "scale_y": 0.5, "inverted": True}
    t = cc.to_parts(raw, "x.png")["transforms"]
    assert [x["kind"] for x in t] == ["resize", "invert"] and t[0]["scale_x"] == 0.5


@pytest.fixture
def plan_with_raw(tmp_path, monkeypatch):
    """A CubiCasa-folder PNG whose raw segmentation file exists (in a temporary raw dir)."""
    folder = tmp_path / "cubicasa5k"
    folder.mkdir()
    img = np.full((200, 300, 3), 255, np.uint8)
    ok, buf = cv2.imencode(".png", img)
    buf.tofile(str(folder / "synthetic.png"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    monkeypatch.setattr(cc, "raw_dir", lambda: raw_dir)
    (raw_dir / "synthetic.seg.json").write_text(json.dumps(_raw()), encoding="utf-8")
    return folder / "synthetic.png", tmp_path


def test_translate_all_writes_a_schema_valid_document(plan_with_raw):
    path, root = plan_with_raw
    (doc,) = translate_all(str(path), str(root))  # validated inside translate_all
    assert doc["drawing"]["route"] == "raster"
    assert doc["drawing"]["image"]["width_px"] == 300
    seg_rooms = [r for r in doc["rooms"] if r["provenance"]["extractor"] != "prior"
                 and "inferred_from_icon" not in r.get("flags", [])]  # fmt: skip
    assert len(doc["openings"]) == 3 and len(seg_rooms) == 3
    # the synthetic plan has a sauna bench and no sauna label: the sauna fallback infers one sauna
    assert [r["type"] for r in doc["rooms"] if "inferred_from_icon" in r.get("flags", [])] == ["sauna"]
    # no kitchen detected: the kitchen prior assumes one (no geometry); the Bath region equals the prior
    (assumed,) = [r for r in doc["rooms"] if r["provenance"]["extractor"] == "prior"]
    assert assumed["type"] == "kitchen" and assumed["polygon_px"] is None
    ask = lambda q: probe.answer([doc], "cubicasa5k", q)[1]  # noqa: E731
    assert ask("How many windows are shown on this floor plan?") == 2
    assert ask("How many toilet bowls (WC seats) are drawn on this floor plan?") == 1
    assert ask("How many showers and bathtubs are drawn on this floor plan in total?") == 1


def test_translate_all_without_raw_marks_nothing_analysed(plan_with_raw):
    path, root = plan_with_raw
    (cc.raw_dir() / "synthetic.seg.json").unlink()
    (doc,) = translate_all(str(path), str(root))
    # the last history entry is the v0.2 -> v0.3 schema migration
    assert doc["openings"] == [] and any("no raw segmentation" in h.get("note", "") for h in doc["history"])


def test_raw_size_mismatch_fails_loudly(plan_with_raw):
    path, root = plan_with_raw
    raw = _raw()
    raw["width_px"] = 999
    (cc.raw_dir() / "synthetic.seg.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="size differs"):
        translate_all(str(path), str(root))


@needs_data
def test_dev_images_never_include_the_holdout():
    dev = cc.dev_images()
    assert dev and not set(dev) & cc.holdout_files()
    assert all(p.startswith("data/cubicasa5k/") for p in dev)
