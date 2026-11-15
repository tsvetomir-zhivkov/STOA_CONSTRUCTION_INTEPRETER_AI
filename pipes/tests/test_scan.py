"""The scan route from the command line, and the regression for the sheet whose frame was erased as a title block.

The GPU calls (Modal detector and OCR) are replaced by cached output: pipes/tests/fixtures/pipw_test/ holds the page
(768 x 497, a US Army Corps piping plan, M-101A), its raw detections (frozen detector) and its OCR boxes.
"""

import copy
import json
import sys
from pathlib import Path

import cv2
import pytest
from PIL import Image

from pipes import scan
from pipes.__main__ import main as cli_main
from pipes.raster import ocr_run, regions
from pipes.routing import route_page
from pipes.schema.convert import graph
from pipes.tests.test_creation import A, B, box, canvas, pipe
from pipes.tests.test_creation import scan as scan_gt
from pipes.tools import PipeIndex

FIX = Path(__file__).parent / "fixtures" / "pipw_test"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Work and cache folders under tmp_path; both Modal calls replaced by the fixture's cached output."""
    from pipes.raster import pipeline

    monkeypatch.setattr(pipeline, "HERE", tmp_path)
    monkeypatch.setattr(ocr_run, "HERE", tmp_path)
    calls = {"det": 0, "ocr": 0}
    det = json.loads((FIX / "det.json").read_text(encoding="utf-8"))
    ocr = json.loads((FIX / "ocr.json").read_text(encoding="utf-8"))["items"]

    def fake_det(sid, png, run=None):
        calls["det"] += 1
        return copy.deepcopy(det)

    def fake_ocr(sid, png):
        calls["ocr"] += 1
        return copy.deepcopy(ocr)

    monkeypatch.setattr(scan, "fetch_detections", fake_det)
    monkeypatch.setattr(scan, "fetch_ocr", fake_ocr)
    return tmp_path, calls


def run(env, path=FIX / "sheet.jpg", **kw):
    tmp, _ = env
    return scan.translate_scan(path, pipes_dir=tmp / "idx", **kw)


# ---------------------------------------------------------------- bug 1: the sheet that was erased


def test_pipw_sheet_is_traced_and_its_border_is_not_erased(env):
    r = run(env)
    doc = graph(r["doc"])
    assert len(doc["edges"]) > 0  # before the fix: 0 edges, the whole drawing was erased as a "title block"
    page = doc["frame"]["width_px"] * doc["frame"]["height_px"]
    for reg in doc["sheet"]["regions"]:
        x0, y0, x1, y1 = reg["bbox_px"]
        if (x1 - x0) * (y1 - y0) > 0.4 * page:
            assert reg["kind"] == "other"  # never a title block or legend
    assert not any(c["category"] == "pipes" and c["status"] == "not_analysed" and c["region"] != "page" and
                   (c["region"][2] - c["region"][0]) * (c["region"][3] - c["region"][1]) > 0.4 * page
                   for c in doc["coverage"])  # fmt: skip
    assert scan.summary(doc)["symbols"] == 6  # the detector's six boxes are unchanged


def test_a_page_sized_frame_with_long_pipes_is_not_a_title_block(tmp_path, monkeypatch):
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    pipe(img, [(100, 175), (400, 175)])
    for y in (60, 250, 320):  # long lines across the sheet, each wider than half the page
        pipe(img, [(60, y), (540, y)])
    cv2.rectangle(img, (4, 4), (595, 395), 0, 3)  # the sheet border touches the page edge
    doc = scan_gt(tmp_path, img, boxes)
    assert all(r["kind"] == "other" for r in doc["sheet"]["regions"])
    assert len(doc["edges"]) >= 4  # the A-B pipe and the three long lines survive

    # the guard: with the rule disabled the same sheet IS wiped as a title block (this is the bug)
    monkeypatch.setattr(regions, "MAX_SHARE", 2.0)
    monkeypatch.setattr(regions, "classify", _classify_without_big(regions.classify))
    wiped = scan_gt(tmp_path, img, boxes, name="wiped")
    assert len(wiped["edges"]) < 4


def _classify_without_big(orig):
    """The old behaviour: a page-sized frame may be a title block (erased in full)."""

    def classify(ink, w, frames, boxes, texts, text_mask=None):
        out = orig(ink, w, frames, boxes, texts, text_mask)
        H, W = ink.shape
        for rg in out:
            x0, y0, x1, y1 = rg["bbox_px"]
            if (x1 - x0) * (y1 - y0) > 0.4 * W * H and regions._inner_h_lines(ink, rg["bbox_px"], w) >= 3:
                rg["kind"], rg["erase"] = "title_block", "full"
        return out

    return classify


# ---------------------------------------------------------------- item 3: python -m pipes <image>


def test_low_resolution_is_warned_and_recorded_so_the_tools_say_unknown(env):
    r = run(env)
    assert r["stroke_width_px"] < 3 and len(r["warnings"]) == 1
    assert "resolution too low, results unreliable" in r["warnings"][0]
    gap = [
        c
        for c in graph(r["doc"])["coverage"]
        if c["category"] == "connectivity" and c["region"] == "page" and c["status"] == "partial"
    ]
    assert len(gap) == 1 and "resolution too low" in gap[0]["note"]
    idx = PipeIndex(graph(r["doc"]))
    syms = sorted(idx.symbol_ids)
    answer = idx.path_between(syms[0], syms[-1])  # unconnected symbols: before, a confident "not connected"
    assert (
        answer["answer"]["unknown"] is True
        and answer["answer"]["connected"] is None
        and answer["reliable"] is False
    )


def test_a_sharp_page_gets_no_warning(tmp_path, monkeypatch):
    from pipes.raster import pipeline

    monkeypatch.setattr(pipeline, "HERE", tmp_path)
    monkeypatch.setattr(ocr_run, "HERE", tmp_path)
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    pipe(img, [(100, 175), (400, 175)])
    src = tmp_path / "sharp.png"
    cv2.imwrite(str(src), img)
    det = {"width": 600, "height": 400, "model": "stub", "dets": [
        {"bbox_px": [float(v) for v in b], "score": 0.9, "tile": None, "pass": "whole"} for b in boxes]}  # fmt: skip
    monkeypatch.setattr(scan, "fetch_detections", lambda sid, png, run=None: copy.deepcopy(det))
    monkeypatch.setattr(scan, "fetch_ocr", lambda sid, png: [])
    r = scan.translate_scan(src, pipes_dir=tmp_path / "idx")
    assert r["warnings"] == [] and r["stroke_width_px"] >= 3
    assert len(graph(r["doc"])["edges"]) == 1 and not any(
        c["note"] and "resolution" in c["note"] for c in graph(r["doc"])["coverage"]
    )
    assert PipeIndex(graph(r["doc"])).connected_to("node_1")["answer"]["connected"] == ["node_2"]


def test_webp_png_and_jpeg_inputs_all_work_and_the_page_is_kept_losslessly(env, tmp_path):
    img = Image.open(FIX / "sheet.jpg")
    for name in ("page.webp", "page.png", "page.JPEG"):
        src = tmp_path / name
        img.save(src, lossless=True) if name.endswith("webp") else img.save(src)
        r = run(env, src)
        assert graph(r["doc"])["frame"]["width_px"] == 768 and graph(r["doc"])["frame"]["height_px"] == 497
    kept = Image.open(scan.work_dir("page") / "sheet.png")
    assert kept.size == (768, 497)  # the page grid is untouched
    webp_doc = graph(run(env, tmp_path / "page.webp")["doc"])
    assert (
        webp_doc["sheet"]["file_format"] == "other"
        and graph(run(env, tmp_path / "page.png")["doc"])["sheet"]["file_format"] == "png"
    )


def test_the_result_is_published_where_the_router_finds_it_with_an_overlay(env):
    tmp, _ = env
    r = run(env, source_file="site/pipw test.webp")
    assert Path(r["overlay"]).exists() and Path(r["overlay"]).stat().st_size > 1000
    route = route_page("site/pipw test.webp", pipes_dir=tmp / "idx")
    assert route["tool_sets"] == ["pipe"]
    published = Path(route["pipe"])
    assert (
        published.exists()
        and published.with_name(published.name.removesuffix(".pipe.json") + ".png").exists()
    )
    doc = graph(json.loads(published.read_text(encoding="utf-8")))
    assert doc["sheet"]["source_file"] == "site/pipw test.webp" and doc["sheet"]["route"] == "raster_pipeline"
    assert doc["sheet"]["source_dataset"] is None and "pid_dataset" not in doc["sheet"]["id"]


def test_gpu_results_are_cached_by_content_and_refetched_on_force_or_change(env, tmp_path):
    _, calls = env
    run(env)
    run(env)
    assert calls == {"det": 1, "ocr": 1}  # the second run costs nothing
    run(env, force=True)
    assert calls == {"det": 2, "ocr": 2}
    other = tmp_path / "sheet.jpg"  # same name, different pixels: the cache must not be reused
    Image.open(FIX / "sheet.jpg").transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(other)
    run(env, other)
    assert calls == {"det": 3, "ocr": 3}


def test_command_line_dispatch_and_output(env, tmp_path, capsys, monkeypatch):
    src = tmp_path / "pipw test.webp"  # a name with a space, like the real file
    Image.open(FIX / "sheet.jpg").save(src, lossless=True)
    monkeypatch.setattr(sys, "argv", ["pipes"])
    monkeypatch.setattr(
        scan, "PIPES_INDEX", tmp_path / "idx"
    )  # the CLI publishes to the default folder: keep it in tmp
    assert cli_main([str(src)]) == 0
    out = capsys.readouterr()
    assert "resolution too low, results unreliable" in out.err and out.err.startswith("warning:")
    first = json.loads(out.out.splitlines()[0])
    assert first["symbols"] == 6 and first["edges"] > 0 and first["stroke_width_px"] < 3
    assert {"tees", "junctions", "unsnapped_ends", "equipment_nodes", "tags"} <= set(first)
    assert "overlay:" in out.out and "published:" in out.out
    assert cli_main([str(tmp_path / "x.txt")]) == 2 and cli_main([]) == 2
