"""The quality gate before detection: normal sheets pass untouched; photo-like, noisy and small pages are fixed."""

import glob
from pathlib import Path

import cv2
import numpy as np
import pytest

from pipes import scan
from pipes.demo import gate_line
from pipes.raster import ocr_run, quality
from pipes.schema.convert import graph
from pipes.tests.test_creation import A, B, box, canvas, pipe

HERE = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "pipw_test"
TEST_SHEETS = sorted(glob.glob(str(HERE / "data_pipes" / "ds" / "test" / "*" / "sheet.*")))


def read(p):
    return cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)


@pytest.mark.skipif(len(TEST_SHEETS) != 20, reason="the 20 test sheets are not on this machine")
def test_every_normal_test_sheet_passes_all_checks_and_comes_out_byte_identical():
    for p in TEST_SHEETS:
        img = read(p)
        g = quality.gate(img)
        assert g["failed"] == [] and g["fixes"] == [] and g["factor"] == 1, (p, g["measures"])
        assert g["image"] is img  # not copied, not touched: the scan route writes today's bytes
        assert quality.note(g) == "quality gate: all checks passed"


def photo_like(clean_gray):
    """Gradient shading (right side 55% as bright) plus a yellow tint (blue channel down)."""
    h, w = clean_gray.shape
    shade = (
        np.linspace(1.0, 0.55, w, dtype=np.float32)[None, :]
        * np.linspace(1.0, 0.8, h, dtype=np.float32)[:, None]
    )
    bgr = np.stack([clean_gray * 0.55, clean_gray * 0.85, clean_gray], axis=-1).astype(np.float32)
    return np.clip(bgr * shade[..., None], 0, 255).astype(np.uint8)


def drawing():
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    pipe(img, [(100, 175), (400, 175)])
    return img, boxes


def test_a_shaded_yellow_photo_fails_only_the_background_check_and_is_whitened():
    clean, _ = drawing()
    shot = photo_like(clean)
    g = quality.gate(shot)
    assert g["failed"] == ["background"]
    assert g["fixes"] == ["illumination_correction", "whiten_paper"]
    m = g["measures"]
    assert m["bg_spread"] > quality.MAX_CELL_SPREAD and m["tint"] > quality.MAX_TINT
    after = quality.measure(g["image"])
    assert quality.failed_checks(after) == [] and after["bg_median"] >= 250 and after["tint"] <= 2
    assert g["image"].shape == shot.shape  # the page grid is untouched
    ink = cv2.cvtColor(g["image"], cv2.COLOR_BGR2GRAY) < 128
    assert abs(int(ink.sum()) - int((clean < 128).sum())) < 0.1 * (clean < 128).sum()  # the lines survived


def test_a_dark_page_is_inverted_with_the_translators_rule():
    clean, _ = drawing()
    g = quality.gate(cv2.cvtColor(255 - clean, cv2.COLOR_GRAY2BGR))
    assert g["failed"] == ["background"] and g["fixes"][0] == "invert"
    assert quality.failed_checks(quality.measure(g["image"])) == []


def test_speckle_noise_is_removed_below_w_squared():
    clean, _ = drawing()
    rng = np.random.default_rng(1)
    noisy = clean.copy()
    for x, y in zip(rng.integers(5, 595, 400), rng.integers(5, 395, 400)):
        noisy[y, x] = 0
    g = quality.gate(cv2.cvtColor(noisy, cv2.COLOR_GRAY2BGR))
    assert g["failed"] == ["noise"] and g["fixes"] == ["remove_small_components"]
    assert quality.measure(g["image"])["tiny_per_mp"] < 50
    assert (cv2.cvtColor(g["image"], cv2.COLOR_BGR2GRAY) < 128).sum() >= 0.9 * (clean < 128).sum()


def test_the_pipw_sheet_trips_the_noise_check_only():
    g = quality.gate(read(str(FIX / "sheet.jpg")))
    assert g["failed"] == ["noise"] and g["factor"] == 1


def test_small_page_is_upscaled_and_boxes_map_back_to_page_pixels():
    clean, _ = drawing()
    small = cv2.cvtColor(cv2.resize(clean, (400, 266), interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR)
    g = quality.gate(small)
    assert g["failed"] == ["small_thin"] and g["factor"] == 3
    big, name = quality.upscale_image(small, upscaler=None)
    assert big.shape[:2] == (266 * 3, 400 * 3) and name == "upscale_x3_lanczos"
    det = {"width": 1200, "height": 798, "dets": [{"bbox_px": [30.0, 60.0, 90.0, 120.0], "score": 0.9}]}
    back = quality.scale_detections(det, 3, (400, 266))
    assert back["dets"][0]["bbox_px"] == [10.0, 20.0, 30.0, 40.0] and back["width"] == 400
    assert quality.scale_texts([{"text": "x", "poly": [[3, 6], [9, 6]]}], 3)[0]["poly"] == [[1, 2], [3, 2]]
    failing = quality.upscale_image(small, upscaler=lambda a: 1 / 0)[1]
    assert failing.startswith("realesrgan_failed(ZeroDivisionError)") and failing.endswith("lanczos")


def stub_modal(monkeypatch, tmp_path, boxes, size):
    from pipes.raster import pipeline

    monkeypatch.setattr(pipeline, "HERE", tmp_path)
    monkeypatch.setattr(ocr_run, "HERE", tmp_path)
    seen = {}

    def fake_det(sid, png, run=None):
        im = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        seen["det_shape"] = im.shape[:2]
        f = im.shape[1] / size[0]  # the detector sees the page at whatever size it was sent
        dets = [{"bbox_px": [v * f for v in b], "score": 0.9, "tile": None, "pass": "whole"} for b in boxes]
        return {"width": im.shape[1], "height": im.shape[0], "model": "stub", "dets": dets}

    monkeypatch.setattr(scan, "fetch_detections", fake_det)
    monkeypatch.setattr(scan, "fetch_ocr", lambda sid, png: [])
    monkeypatch.setattr(
        scan, "fetch_upscale", lambda png, factor=3: 1 / 0
    )  # no GPU: the gate falls back to Lanczos
    return seen


def test_scan_route_on_a_photo_like_page_matches_the_clean_page_and_records_provenance(tmp_path, monkeypatch):
    clean, boxes = drawing()
    stub_modal(monkeypatch, tmp_path, boxes, (600, 400))
    cv2.imwrite(str(tmp_path / "clean.png"), clean)
    cv2.imwrite(str(tmp_path / "photo.png"), photo_like(clean))
    ref = scan.translate_scan(tmp_path / "clean.png", pipes_dir=tmp_path / "idx", overlay=False)
    r = scan.translate_scan(tmp_path / "photo.png", pipes_dir=tmp_path / "idx", overlay=False)
    assert "ext_gate" not in [
        x["id"] for x in graph(ref["doc"])["extractors"]
    ]  # a normal page records nothing
    assert (
        len(graph(r["doc"])["edges"]) == len(graph(ref["doc"])["edges"]) == 1
    )  # shading and tint did not break the trace
    assert scan.summary(graph(r["doc"]))["symbols"] == 2
    gate_ext = next(x for x in graph(r["doc"])["extractors"] if x["id"] == "ext_gate")
    assert "failed background" in gate_ext["name"] and "illumination_correction" in gate_ext["name"]
    assert r["gate"]["failed"] == ["background"]
    kept = cv2.imread(str(scan.work_dir("photo") / "original.png"))
    assert np.array_equal(kept, photo_like(clean))  # the published page keeps the original pixels


def test_scan_route_on_a_small_page_detects_on_3x_and_maps_back(tmp_path, monkeypatch):
    clean, boxes = drawing()
    small = cv2.resize(clean, (400, 266), interpolation=cv2.INTER_AREA)
    sb = [[v * 400 / 600 for v in b] for b in boxes]
    seen = stub_modal(monkeypatch, tmp_path, sb, (400, 266))
    cv2.imwrite(str(tmp_path / "small.png"), small)
    r = scan.translate_scan(tmp_path / "small.png", pipes_dir=tmp_path / "idx", overlay=False)
    assert seen["det_shape"] == (266 * 3, 400 * 3)  # detection ran on the 3x page
    assert graph(r["doc"])["frame"]["width_px"] == 400  # but every coordinate is an original page pixel
    assert max(n["bbox_px"][2] for n in graph(r["doc"])["nodes"] if n.get("bbox_px")) < 400
    name = next(x for x in graph(r["doc"])["extractors"] if x["id"] == "ext_gate")["name"]
    assert "upscale_x3_realesrgan_failed" not in name and "upscale_x3_lanczos" in name


def test_demo_caption_shows_the_gate_provenance(tmp_path, monkeypatch):
    clean, boxes = drawing()
    stub_modal(monkeypatch, tmp_path, boxes, (600, 400))
    cv2.imwrite(str(tmp_path / "photo.png"), photo_like(clean))
    r = scan.translate_scan(
        tmp_path / "photo.png", source_file="s/photo.png", pipes_dir=tmp_path / "idx", overlay=False
    )
    import pipes.routing as routing

    orig = routing.pipe_index_path
    monkeypatch.setattr(
        routing, "pipe_index_path", lambda src, page, base=None: orig(src, page, tmp_path / "idx")
    )
    line = gate_line({"sheet": graph(r["doc"])["sheet"]["id"]})
    assert line and "failed background" in line and "illumination_correction" in line
    monkeypatch.setattr(
        routing, "pipe_index_path", lambda src, page, base=None: orig(src, page, tmp_path / "none")
    )
    assert (
        gate_line({"sheet": graph(r["doc"])["sheet"]["id"]}) is None
    )  # nothing written: no extra caption line
