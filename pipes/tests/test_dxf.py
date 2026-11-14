import json
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from ezdxf import units
from PIL import Image

from pipes.extract.dxf_pipes import extract, translate_dxf
from pipes.ingest.gt_to_dxf import pipe_json_to_dxf
from pipes.routing import route_page
from pipes.tools import PipeIndex

EXAMPLE = Path(__file__).parent.parent / "schema" / "example_standalone_heating_schematic.json"


def new(tmp_path, unit=units.MM, layers=("P-HEAT-SUPPLY",)):
    d = ezdxf.new("R2018", setup=True)
    d.units = unit
    for name in layers:
        d.layers.add(name)
    return d, d.modelspace(), tmp_path / "t.dxf"


def run(d, path, **kw):
    d.saveas(path)
    return extract(path, "unit/t.dxf", **kw)


def box_block(d, name, w=100, h=100, attdef=True):
    blk = d.blocks.new(name)
    blk.add_lwpolyline([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)], close=True)
    if attdef:
        blk.add_attdef("TAG", (0, -h), height=20)


def comps(doc):
    return doc["summary"]["connected_components"]


# ---------------------------------------------------------------- the synthetic heating DXF (from the schema example)


@pytest.fixture(scope="module")
def heating(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("heating")
    dxf = pipe_json_to_dxf(json.loads(EXAMPLE.read_text(encoding="utf-8")), tmp / "LVI-heating.dxf")
    doc = translate_dxf(dxf, "hypothetical/LVI-heating.dxf", pipes_dir=tmp / "idx")
    return doc, tmp


def test_heating_dxf_reproduces_the_examples_summary(heating):
    doc, _ = heating
    s = doc["summary"]
    # the example calls the four-way point a tee; four pipes meeting is a junction in the DXF route
    assert s["node_counts"] == {"balancing_valve": 2, "ball_valve": 1, "boiler": 1, "junction": 1,
                                "off_sheet_connector": 1, "pump": 1, "radiator": 2}  # fmt: skip
    assert len(doc["edges"]) == 8 and s["connected_components"] == 1 and s["unsnapped_end_count"] == 0
    rows = {(r["system"], r["size"]): r for r in s["length_m_by_system_and_size"]}
    assert rows[("heating_supply", "DN25")]["length_m"] == 4.5 and rows[("heating_supply", "DN25")][
        "missing_length_edge_ids"
    ] == ["pipe_8"]
    assert (
        rows[("heating_supply", "DN20")]["length_m"] == 6.4
        and rows[("heating_supply", "DN15")]["length_m"] == 3.1
    )
    assert all(e["ends"][0]["snap"] == "vector_exact" for e in doc["edges"])


def test_heating_dxf_symbols_tags_types_and_provenance(heating):
    doc, _ = heating
    by_tag = {n["tag"]: n for n in doc["nodes"] if n["tag"]}
    assert set(by_tag) == {"K1", "P1", "R1", "R2", "A"}
    assert (
        by_tag["K1"]["type"] == "boiler" and by_tag["K1"]["subtype"] == "KATTILA"
    )  # block name is the subtype
    assert by_tag["P1"]["type"] == "pump" and by_tag["R1"]["type"] == "radiator"
    assert (
        by_tag["K1"]["provenance"]["extractor_id"] == "ext_dxf" and by_tag["K1"]["provenance"]["source_ref"]
    )  # DXF handle
    assert by_tag["K1"]["attributes"]["tag_source"] == "attribute" and by_tag["K1"]["tag_text_id"] is None
    assert len(by_tag["K1"]["ports_px"]) == 1 and len(by_tag["P1"]["ports_px"]) == 2
    e = doc["edges"][0]
    assert e["layer"] == "P-HEAT-SUPPLY" and e["system"] == "heating_supply" and e["provenance"]["source_ref"]


def test_heating_dxf_sheet_is_not_to_scale_and_metres_come_from_written_lengths(heating):
    doc, _ = heating
    assert doc["sheet"]["is_to_scale"] is False and doc["scale"]["source"] == "not_to_scale"
    assert doc["scale"]["metres_per_px"] is None and doc["scale"]["evidence_ids"]
    assert doc["sheet"]["route"] == "vector_parser" and doc["frame"]["source_units"] == "mm"
    idx = PipeIndex(doc)
    r = idx.pipe_length(tag="LV-1")
    assert r["answer"]["length_m"] == 4.5 and r["answer"]["lower_bound"] and r["answer"]["basis"] == "written"
    assert r["answer"]["missing_length_edge_ids"] == ["pipe_8"] and r["reliable"]
    assert not any("OCR" in c for c in r["caveats"])  # vector text is exact
    lengths = next(c for c in doc["coverage"] if c["category"] == "lengths")
    assert lengths["status"] == "partial" and lengths["element_ids"] == ["pipe_5", "pipe_7", "pipe_8"]


def test_heating_dxf_is_published_where_the_router_looks_with_an_aligned_page_image(heating):
    doc, tmp = heating
    r = route_page("hypothetical/LVI-heating.dxf", pipes_dir=tmp / "idx")
    assert r["tool_sets"] == ["pipe"]
    png = Path(r["pipe"]).with_name("LVI-heating#0.png")
    im = Image.open(png)
    assert im.size == (doc["frame"]["width_px"], doc["frame"]["height_px"])
    dark = np.asarray(im.convert("L")) < 140
    (x0, y0), (x1, _) = doc["edges"][0]["polyline_px"][0], doc["edges"][0]["polyline_px"][-1]
    rows = [r_ for r_ in np.where(dark[:, int((x0 + x1) / 2)])[0] if abs(r_ - y0) < 8]
    assert (
        rows and min(abs(r_ - y0) for r_ in rows) <= 1.5
    )  # the drawn pipe is where source_to_px says (within 1.5 px)
    bx = doc["nodes"][0]["bbox_px"]  # boiler box edges
    cols = np.where(dark[int((bx[1] + bx[3]) / 2), :])[0]
    assert min(abs(c - bx[0]) for c in cols) <= 1.5 and min(abs(c - bx[2]) for c in cols) <= 1.5


# ---------------------------------------------------------------- topology rules


def test_crossing_without_a_shared_vertex_is_not_connected_but_a_junction_dot_connects(tmp_path):
    d, msp, path = new(tmp_path)
    msp.add_line((0, 500), (1000, 500), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    msp.add_line((500, 0), (500, 1000), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    doc = run(d, path)
    assert len(doc["edges"]) == 2 and comps(doc) == 2 and doc["summary"]["node_counts"] == {}
    msp.add_circle((500, 500), 8, dxfattribs={"layer": "P-HEAT-SUPPLY"})  # a junction dot on the crossing
    doc = run(d, path)
    assert len(doc["edges"]) == 4 and comps(doc) == 1 and doc["summary"]["node_counts"] == {"junction": 1}


def test_a_pipe_ending_on_the_side_of_another_makes_a_tee(tmp_path):
    d, msp, path = new(tmp_path)
    msp.add_line((0, 500), (1000, 500), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    msp.add_line((500, 500), (500, 1000), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    doc = run(d, path)
    assert doc["summary"]["node_counts"] == {"tee": 1} and len(doc["edges"]) == 3 and comps(doc) == 1


def test_a_pipe_through_a_symbol_is_cut_at_its_box_and_the_symbol_joins_both_sides(tmp_path):
    d, msp, path = new(tmp_path)
    box_block(d, "PALLOVENTTIILI")
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"})  # runs straight through the valve
    msp.add_blockref("PALLOVENTTIILI", (500, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"}).add_auto_attribs(
        {"TAG": "V1"}
    )
    doc = run(d, path)
    v = next(n for n in doc["nodes"] if n["tag"] == "V1")
    assert v["type"] == "ball_valve" and len(v["ports_px"]) == 2 and len(doc["edges"]) == 2
    assert {e["ends"][i]["node_id"] for e in doc["edges"] for i in (0, 1)} >= {v["id"]}
    assert sum(1 for e in doc["edges"] for x in e["ends"] if x["node_id"] == v["id"]) == 2
    assert doc["summary"]["unsnapped_end_count"] == 2  # the far ends of the two stubs reach nothing


def test_a_run_of_lines_on_one_layer_is_one_pipe_and_a_layer_change_keeps_both_layers(tmp_path):
    d, msp, path = new(tmp_path, layers=("P-HEAT-SUPPLY", "P-HEAT-RETURN"))
    for x in (0, 300, 600):
        msp.add_line((x, 0), (x + 300, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    msp.add_line((900, 0), (1200, 0), dxfattribs={"layer": "P-HEAT-RETURN"})
    doc = run(d, path)
    assert len(doc["edges"]) == 1 and doc["edges"][0]["layer"] == "P-HEAT-RETURN+P-HEAT-SUPPLY"
    assert (
        len(doc["edges"][0]["provenance"]["source_ref"].split("+")) == 4
    )  # the four source handles are kept


def test_unsnapped_ends_get_coverage_and_a_continuation_text_makes_an_open_end(tmp_path):
    d, msp, path = new(tmp_path)
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    doc = run(d, path)
    assert doc["summary"]["unsnapped_end_count"] == 2
    partial = [c for c in doc["coverage"] if c["category"] == "connectivity" and c["status"] == "partial"]
    assert len(partial) == 2 and all(c["element_ids"] == ["pipe_1"] for c in partial)
    msp.add_text("JATKUU", height=40, dxfattribs={"insert": (1010, 20)})
    doc = run(d, path)
    assert doc["summary"]["unsnapped_end_count"] == 1 and doc["summary"]["node_counts"] == {"open_end": 1}


def test_fallback_uses_modelspace_lines_outside_frame_layers_and_never_block_contents(tmp_path):
    d, msp, path = new(tmp_path, layers=("FOO", "A-DIM"))
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "FOO"})
    msp.add_line((0, 200), (1000, 200), dxfattribs={"layer": "A-DIM"})  # a dimension line: not a pipe
    blk = d.blocks.new("DETAIL")
    blk.add_line((0, 0), (0, 5000))  # inside a block: never read as a pipe
    msp.add_blockref("DETAIL", (3000, 3000))
    doc = run(d, path)
    assert len(doc["edges"]) == 1 and doc["edges"][0]["layer"] == "FOO"
    assert "no pipe layer matched" in next(c for c in doc["coverage"] if c["category"] == "pipes")["note"]


def test_bulged_polyline_is_flattened_and_a_layer_keyword_sets_the_system(tmp_path):
    d, msp, path = new(tmp_path, layers=("KV-WATER",))
    msp.add_lwpolyline(
        [(0, 0, 0, 0, 1), (1000, 0, 0, 0, 0)], format="xyseb", dxfattribs={"layer": "KV-WATER"}
    )  # a half circle
    doc = run(d, path)
    pts = doc["edges"][0]["polyline_px"]
    assert len(pts) > 5 and doc["edges"][0]["system"] == "cold_water"


# ---------------------------------------------------------------- scale and units


def plan_dxf(tmp_path, unit, extra_text=None):
    d, msp, path = new(tmp_path, unit=unit)
    box_block(d, "VALVE", 100, 100, attdef=False)
    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    msp.add_text("PUTKISTOPOHJA 1:50", height=100, dxfattribs={"insert": (0, 800)})
    if extra_text:
        msp.add_text(extra_text, height=100, dxfattribs={"insert": (0, 1000)})
    return d, msp, path


def test_a_plan_drawn_in_mm_gives_pipe_lengths_in_metres_from_the_units(tmp_path):
    d, msp, path = plan_dxf(tmp_path, units.MM)
    doc = run(d, path)
    assert doc["sheet"]["diagram_type"] == "pipe_plan" and doc["sheet"]["is_to_scale"] is True
    assert doc["scale"]["source"] == "vector_units" and doc["frame"]["source_units"] == "mm"
    r = PipeIndex(doc).pipe_length(edge="pipe_1")
    assert r["answer"]["length_m"] == pytest.approx(5.0, abs=0.01) and r["answer"]["basis"] == "geometry"
    assert doc["edges"][0]["length"]["flag"] == "no_written_length"


def test_the_same_geometry_in_metres_or_inches_scales_correctly(tmp_path):
    d, msp, path = new(tmp_path, unit=units.M)
    msp.add_line((0, 0), (5, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    msp.add_text("PUTKISTOPOHJA", height=0.1, dxfattribs={"insert": (0, 1)})
    assert PipeIndex(run(d, path)).pipe_length(edge="pipe_1")["answer"]["length_m"] == pytest.approx(
        5.0, abs=0.01
    )
    d, msp, path = new(tmp_path, unit=units.IN)
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    msp.add_text("PUTKISTOPOHJA", height=3, dxfattribs={"insert": (0, 30)})
    assert PipeIndex(run(d, path)).pipe_length(edge="pipe_1")["answer"]["length_m"] == pytest.approx(
        2.54, abs=0.01
    )


def test_not_to_scale_note_blocks_geometry_metres_and_unitless_drawings_have_no_scale(tmp_path):
    d, msp, path = plan_dxf(tmp_path, units.MM, extra_text="EI MITTAKAAVASSA")
    doc = run(d, path)
    assert doc["sheet"]["is_to_scale"] is False and doc["scale"]["source"] == "not_to_scale"
    r = PipeIndex(doc).pipe_length(edge="pipe_1")
    assert r["answer"]["length_m"] is None and any("not drawn to scale" in c for c in r["caveats"])
    d, msp, path = plan_dxf(tmp_path, units.IN)
    d.header["$INSUNITS"] = 0
    doc = run(d, path)
    assert (
        doc["scale"]["source"] == "none"
        and PipeIndex(doc).pipe_length(edge="pipe_1")["answer"]["length_m"] is None
    )


def test_no_pipes_raises_a_clear_error(tmp_path):
    d, msp, path = new(tmp_path)
    msp.add_text("EMPTY", height=10)
    with pytest.raises(ValueError, match="no pipe lines"):
        run(d, path)
