"""Step 2: router tests. Synthetic files in tmp_path, plus one real preview file per source."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

from translator import translate  # noqa: E402
from translator.config import data_root, questions_path  # noqa: E402
from translator.router import decode_step_string, route, slug, summary  # noqa: E402

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)

IFC_TEXT = """ISO-10303-21;
DATA;
#1=IFCBUILDINGSTOREY('2kjDTDuEyhIROnV$p_hGxY',#10,'Fundament',$,$,#11,$,$,.ELEMENT.,-4400.);
#2=IFCBUILDINGSTOREY('2Wut7I5gIrJOhO$ut8PFMi',#10,'K\\X\\E6lder',$,$,#12,$,$,.ELEMENT.,-3500.);
#3=IFCBUILDINGSTOREY('1oZ0wPs_PE8ANCPg3bIs4j',#10,'Grundplan',$,$,#13,$,$,.ELEMENT.,0.);
ENDSEC;
END-ISO-10303-21;
"""


def _svg(path: Path, shapes: int = 20, text: str = "Kitchen", image: bool = False) -> Path:
    body = "".join(f'<path d="M{i},0 L{i},10"/>' for i in range(shapes))
    if text:
        body += f'<text x="1" y="1">{text}</text>'
    if image:
        body += '<image x="0" y="0" width="100" height="100" href="scan.png"/>'
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{body}</svg>', encoding="utf-8"
    )
    return path


def _vector_pdf(path: Path, lines: int = 60, text: str = "Room 2.10") -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(lines):
        page.draw_line((50 + i * 5, 50), (50 + i * 5, 400))
    if text:
        page.insert_text((60, 450), text)
    doc.save(path)
    doc.close()
    return path


def _png(path: Path, colour=(255, 255, 255), size=(300, 200)) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


def _scanned_pdf(path: Path, tmp: Path) -> Path:
    img = _png(tmp / "scan.png", colour=(240, 240, 240), size=(400, 600))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, filename=str(img))
    doc.save(path)
    doc.close()
    return path


# ------------------------------------------------------------------ helpers


def test_decode_step_string():
    assert decode_step_string("K\\X\\E6lder") == "Kælder"
    assert decode_step_string("\\X2\\00C400D6\\X0\\") == "ÄÖ"
    assert decode_step_string("it''s") == "it's"


def test_slug_matches_rendered_names():
    assert slug("Kælder") == "klder"
    assert slug("4.korrus") == "4-korrus"


# ------------------------------------------------------------------ synthetic cases


def test_ifc_parent_with_level(tmp_path):
    (tmp_path / "model_x.ifc").write_text(IFC_TEXT, encoding="latin-1")
    pdf = _vector_pdf(tmp_path / "model_x_plan-02-klder.pdf")
    _svg(tmp_path / "model_x_plan-02-klder.svg")  # IFC must win over the SVG and the PDF
    r = route(pdf, tmp_path)
    assert r["route"] == "ifc"
    assert Path(r["read_from"]).name == "model_x.ifc"
    assert r["level"] == "Kælder"
    assert r["level_global_id"] == "2Wut7I5gIrJOhO$ut8PFMi"
    assert summary(r) == "ifc (model_x.ifc), level 'Kælder'"


def test_ifc_same_stem_and_input_itself(tmp_path):
    ifc = tmp_path / "house.ifc"
    ifc.write_text(IFC_TEXT, encoding="latin-1")
    png = _png(tmp_path / "house.png")
    assert route(png, tmp_path)["route"] == "ifc"
    r = route(ifc, tmp_path)
    assert r["route"] == "ifc" and r["level_source"] == "whole_model"


def test_ifc_level_from_title_when_slug_fails(tmp_path):
    (tmp_path / "m.ifc").write_text(IFC_TEXT, encoding="latin-1")
    # SVG, not PDF: pymupdf's base-14 font cannot write the em dash used in the real titles
    svg = _svg(tmp_path / "m_sheet-a.svg", text="Demo — Plantegning: Grundplan")
    r = route(svg, tmp_path)
    assert r["route"] == "ifc"
    assert (r["level"], r["level_source"]) == ("Grundplan", "page_title")


def test_dxf_sibling_beats_pdf_and_png(tmp_path):
    (tmp_path / "floor1.dxf").write_text("0\nEOF\n")
    pdf = _vector_pdf(tmp_path / "floor1.pdf")
    png = _png(tmp_path / "floor1.png")
    for p in (pdf, png):
        r = route(p, tmp_path)
        assert r["route"] == "dxf" and Path(r["read_from"]).name == "floor1.dxf"


def test_svg_input_and_sibling(tmp_path):
    svg = _svg(tmp_path / "plan.svg")
    assert route(svg, tmp_path)["route"] == "svg"
    jpg = tmp_path / "plan.jpg"
    Image.new("RGB", (50, 50), "white").save(jpg)
    r = route(jpg, tmp_path)
    assert r["route"] == "svg" and Path(r["read_from"]).name == "plan.svg"


@pytest.mark.parametrize(
    "kwargs",
    [dict(shapes=3), dict(text=""), dict(image=True)],
    ids=["too-few-shapes", "no-text", "embedded-raster"],
)
def test_weak_svg_falls_through_to_raster(tmp_path, kwargs):
    _svg(tmp_path / "p.svg", **kwargs)
    png = _png(tmp_path / "p.png")
    r = route(png, tmp_path)
    assert r["route"] == "raster"
    assert any("rejected" in c for c in r["checks"])


def test_vector_pdf(tmp_path):
    r = route(_vector_pdf(tmp_path / "sheet.pdf"), tmp_path)
    assert r["route"] == "vector_pdf"
    assert r["pages"][0]["vector"] and r["pages"][0]["drawings"] > 50


def test_pdf_with_few_drawings_is_not_vector(tmp_path):
    r = route(_vector_pdf(tmp_path / "sheet.pdf", lines=10), tmp_path)
    assert r["route"] == "raster"


def test_scanned_pdf_is_raster_at_300dpi(tmp_path):
    r = route(_scanned_pdf(tmp_path / "scan_sheet.pdf", tmp_path), tmp_path)
    assert r["route"] == "raster"
    assert r["raster"]["render_dpi"] == 300
    assert r["pages"][0]["max_image_cover"] > 0.5


def test_raster_kind_and_stats(tmp_path):
    cc = tmp_path / "cubicasa5k"
    cc.mkdir()
    r = route(_png(cc / "a.png", colour=(230, 120, 120)), tmp_path)
    assert r["route"] == "raster" and r["raster_kind"] == "modern_floorplan"
    assert r["raster"]["is_colour"] and not r["raster"]["dark_background"]
    hist = tmp_path / "finna"
    hist.mkdir()
    r = route(_png(hist / "b.png", colour=(20, 20, 20)), tmp_path)
    assert r["raster_kind"] == "historical_scan"
    assert r["raster"]["dark_background"] and not r["raster"]["is_colour"]


def test_unknown(tmp_path):
    dwg = tmp_path / "x.dwg"
    dwg.write_bytes(b"AC1027")
    r = route(dwg, tmp_path)
    assert r["route"] == "unknown" and ".dwg" in r["reason"]
    assert route(tmp_path / "missing.png", tmp_path)["route"] == "unknown"


def test_translate_writes_none_for_unknown(tmp_path):
    dwg = tmp_path / "x.dwg"
    dwg.write_bytes(b"AC1027")
    doc = translate(str(dwg), str(tmp_path))
    assert doc["drawings"][0]["route"] == "none"
    assert all(c["status"] == "not_analysed" for c in doc["coverage"])


def test_translate_keeps_asked_file(tmp_path):
    (tmp_path / "floor1.dxf").write_text("0\nEOF\n")
    pdf = _vector_pdf(tmp_path / "floor1.pdf")
    doc = translate(str(pdf), str(tmp_path))
    assert doc["drawings"][0]["file"].endswith("floor1.pdf")
    assert doc["drawings"][0]["read_from"].endswith("floor1.dxf")
    assert doc["drawings"][0]["route"] == "dxf"
    assert doc["drawings"][0]["image"]["dpi"] == 150


# ------------------------------------------------------------------ real preview files


@needs_data
@pytest.mark.parametrize(
    "path, expected, read_from",
    [
        ("data/cubicasa5k/high_quality-5815.png", "raster", "high_quality-5815.png"),
        ("data/swiss_dwellings/site1000_bldg2082_floor9385.pdf", "dxf", "site1000_bldg2082_floor9385.dxf"),
        (
            "data/ifc_models/bsc-molio_molio-with-uris_plan-02-klder.pdf",
            "ifc",
            "bsc-molio_molio-with-uris.ifc",
        ),
        ("data/ifc_bench/duplex_arc.ifc", "ifc", "duplex_arc.ifc"),
        ("data/opening_design/Sports_Complex/Sheets - Current Set/A-701 - Restrooms.pdf", "vector_pdf", None),
        ("data/finna/hka.139877703731200_160449953667200.jpg", "raster", None),
    ],
)
def test_real_preview_files(path, expected, read_from):
    r = route(path)
    assert r["route"] == expected, r
    assert r["file"] == path
    if read_from:
        assert Path(r["read_from"]).name == read_from


@needs_data
def test_real_quick_test_line():
    r = route("data/ifc_models/bsc-molio_molio-with-uris_plan-02-klder.pdf")
    assert summary(r) == "ifc (bsc-molio_molio-with-uris.ifc), level 'Kælder'"


@needs_data
def test_real_cubicasa_kind():
    assert route("data/cubicasa5k/high_quality-5815.png")["raster_kind"] == "modern_floorplan"
