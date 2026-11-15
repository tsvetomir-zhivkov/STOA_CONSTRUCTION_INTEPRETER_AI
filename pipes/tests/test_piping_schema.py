"""Piping schema v0.1 as the output of every route: shape, reference rules, the graph view the tools read."""

import copy
import json

import ezdxf
import pytest
from ezdxf import units

from pipes.extract.dxf_pipes import extract
from pipes.schema import convert, validate
from pipes.tests.test_graph_metrics import make
from pipes.tests.test_tools import CHAIN
from pipes.tools import PipeIndex
from pipes.write import PipeValidationError, finalize


def piping(graph_doc):
    return finalize(graph_doc)


def test_a_finalized_sheet_has_every_required_collection_and_validates():
    p = piping(CHAIN)
    assert p["schema_version"] == "piping-0.1" and validate.check(p) == []
    for k in ("project", "drawing", "sources", "pipe_segments", "pipe_fittings", "pipe_equipment",
              "pipe_connections", "texts", "coverage", "history"):  # fmt: skip
        assert k in p
    assert p["drawing"]["route"] == "raster" and p["drawing"]["image"]["width_px"] == 400
    assert not any(k in p for k in ("nodes", "edges", "sheet", "frame"))  # the old shape is gone


def test_symbols_are_fittings_edges_are_segments_and_each_snapped_end_is_a_connection():
    p = piping(CHAIN)
    assert len(p["pipe_fittings"]) == 4 and len(p["pipe_segments"]) == 3 and len(p["pipe_connections"]) == 6
    seg = p["pipe_segments"][0]
    assert seg["centerline_px"] and seg["start_connection_id"] and seg["end_connection_id"]
    conns = {c["id"]: c for c in p["pipe_connections"]}
    assert conns[seg["start_connection_id"]]["to_entity_id"] == seg["id"]
    assert conns[seg["start_connection_id"]]["is_verified"] is False  # raster snaps are not "explicit"
    f = p["pipe_fittings"][1]
    assert set(f["connected_segment_ids"]) == {"pipe_1", "pipe_2"} and len(f["connection_ids"]) == 2


def test_lengths_are_never_estimated_from_pixels_without_a_scale():
    p = piping(CHAIN)
    assert all(s["length_m"] is None and s["centerline_m"] is None for s in p["pipe_segments"])
    total = next(s for s in p["statistics"] if s["name"] == "total_pipe_length_m")
    assert total["value"] is None and total["status"] == "not_available"
    bad = copy.deepcopy(p)
    bad["pipe_segments"][0]["length_m"] = 1.0
    assert any("without a scale" in e for e in validate.check(bad))


def test_unsnapped_ends_are_flagged_and_have_no_connection():
    doc = copy.deepcopy(CHAIN)
    doc["edges"][0]["ends"][1] = {"node_id": None, "port_index": None, "snap": "unsnapped", "snap_distance_px": None,
                                  "point_px": doc["edges"][0]["polyline_px"][-1]}  # fmt: skip
    p = piping(doc)
    s = p["pipe_segments"][0]
    assert s["end_connection_id"] is None and "end1_unsnapped" in s["flags"]
    assert convert.view(p)["summary"]["unsnapped_end_count"] == 1
    bad = copy.deepcopy(p)
    bad["pipe_segments"][0]["flags"].remove("end1_unsnapped")
    assert any("not flagged" in e for e in validate.check(bad))


def test_reference_rules_catch_what_the_json_schema_cannot():
    p = piping(CHAIN)
    dup = copy.deepcopy(p)
    dup["pipe_fittings"][1]["id"] = dup["pipe_fittings"][0]["id"]
    assert any("duplicate id" in e for e in validate.check(dup))
    dangling = copy.deepcopy(p)
    dangling["pipe_connections"][0]["from_entity_id"] = "nope"
    assert any("nope" in e for e in validate.check(dangling))
    outside = copy.deepcopy(p)
    outside["pipe_fittings"][0]["bbox_px"] = [-5, 0, 10, 10]
    assert any("outside the page" in e for e in validate.check(outside))
    shape = copy.deepcopy(p)
    del shape["pipe_segments"][0]["service"]
    with pytest.raises(PipeValidationError):
        finalize({**shape})  # a piping doc missing a required field is refused, not repaired
    assert any(e.startswith("schema:") for e in validate.check(shape))


def test_the_tools_give_the_same_answers_on_the_file_and_on_the_working_graph():
    p = json.loads(json.dumps(piping(CHAIN)))  # as read back from disk
    a, b = PipeIndex(p), PipeIndex(CHAIN)
    assert a.count_symbols()["answer"] == b.count_symbols()["answer"]
    assert a.connected_to("node_2")["answer"]["connected"] == b.connected_to("node_2")["answer"]["connected"]
    r = a.path_between("node_1", "node_4")
    assert r["answer"]["connected"] and r["answer"]["path_edge_ids"] == ["pipe_1", "pipe_2", "pipe_3"]


def test_a_to_scale_dxf_gets_metre_lengths_quantities_and_verified_connections(tmp_path):
    d = ezdxf.new("R2018", setup=True)
    d.units = units.MM
    d.layers.add("P-HEAT-SUPPLY")
    msp = d.modelspace()
    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "P-HEAT-SUPPLY"})
    msp.add_text("PUTKISTOPOHJA 1:50", height=100, dxfattribs={"insert": (0, 800)})
    path = tmp_path / "t.dxf"
    d.saveas(path)
    p = extract(path, "unit/t.dxf")
    assert validate.check(p) == [] and p["drawing"]["route"] == "dxf" and p["sources"][0]["format"] == "dxf"
    assert p["project"]["units"]["length"] == {"unit": "mm", "to_si": 0.001, "status": "explicit",
                                               "source": "dxf $INSUNITS"}  # fmt: skip
    assert p["drawing"]["scale"]["source"] == "vector_units" and p["drawing"]["scale"]["m_per_px"]
    s = p["pipe_segments"][0]
    assert s["length_m"] == pytest.approx(5.0, abs=0.01)
    assert (
        s["quantities"]["length_m"]["status"] == "calculated" and s["quantities"]["length_m"]["unit"] == "m"
    )
    assert any(t["kind"] == "affine" for t in p["drawing"]["transforms"])  # DXF units -> page pixels
    total = next(x for x in p["statistics"] if x["name"] == "total_pipe_length_m")
    assert total["value"] == pytest.approx(5.0, abs=0.01) and total["status"] == "calculated"
    assert p["pipe_systems"] and p["pipe_systems"][0]["service"] == "heating_supply"
    assert p["pipe_types"] and all(c["is_verified"] for c in p["pipe_connections"])


def test_services_map_to_the_schema_enum_and_back():
    doc = copy.deepcopy(CHAIN)
    doc["edges"][0]["system"] = "heating_return"
    doc["edges"][1]["system"] = "waste"
    p = piping(doc)
    assert [s["service"] for s in p["pipe_segments"][:2]] == ["heating_return", "sanitary_waste"]
    back = convert.view(p)["edges"]
    assert back[0]["system"] == "heating_return" and back[1]["system"] == "waste"  # exact value kept


def test_extractor_registry_and_sheet_facts_survive_in_history():
    p = piping(make([(1, 2)]))
    actions = {h["action"] for h in p["history"]}
    assert {"extractor", "sheet_facts"} <= actions
    g = convert.view(p)
    assert g["extractors"] and g["sheet"]["id"].startswith("pid_dataset/")
