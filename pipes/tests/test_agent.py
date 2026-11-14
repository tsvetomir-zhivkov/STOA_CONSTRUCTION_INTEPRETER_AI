import copy
import inspect
import json

import pytest

from pipes.agent_tools import TOOL_DEFINITIONS, call_tool, compact, make_tools, validate_args
from pipes.eval import agent_questions as aq
from pipes.routing import pipe_index_path, publish, route_page
from pipes.tests.test_graph_metrics import make
from pipes.tests.test_tools import CHAIN, idx
from pipes.tools import PipeIndex

NAMES = ["count_symbols", "connected_to", "path_between", "pipe_length"]


def test_json_definitions_match_the_bound_callables():
    assert [d["name"] for d in TOOL_DEFINITIONS] == NAMES
    tools = {f.__name__: f for f in make_tools(CHAIN)}
    assert sorted(tools) == sorted(f"{n}_tool" for n in NAMES)  # the translator agent's `<tool>_tool` naming
    for d in TOOL_DEFINITIONS:
        fn = tools[f"{d['name']}_tool"]
        sig = inspect.signature(fn).parameters
        assert set(sig) == set(d["parameters"]["properties"]), d["name"]
        required = {k for k, p in sig.items() if p.default is inspect.Parameter.empty}
        assert required == set(d["parameters"]["required"]), d["name"]
        assert d["description"] in fn.__doc__ and d["description"].strip()
        for pname, spec in d["parameters"]["properties"].items():
            assert spec["description"], (d["name"], pname)


def test_wrappers_give_the_model_a_compact_result_and_log_the_full_one():
    log: list = []
    tools = {f.__name__: f for f in make_tools(CHAIN, log)}
    seen = tools["path_between_tool"]("node_1", "node_4")
    assert (
        seen["answer"]["connected"] and "path_polyline_px" not in seen["answer"] and "highlights" not in seen
    )
    assert seen["answer"]["path_edge_ids"] == ["pipe_1", "pipe_2", "pipe_3"] and seen["reliable"] is True
    assert log[0]["tool"] == "path_between" and log[0]["args"] == {"a": "node_1", "b": "node_4"}
    assert log[0]["result"]["highlights"]["edges"][0]["polyline_px"]  # the UI still gets the geometry
    link = tools["connected_to_tool"]("node_2")["answer"]["links"][0]
    assert set(link) == {"node_id", "edge_ids"}  # polylines stripped for the model


def test_empty_string_arguments_mean_not_given():
    tools = {f.__name__: f for f in make_tools(CHAIN)}
    assert tools["count_symbols_tool"]()["answer"]["count"] == 4
    assert tools["count_symbols_tool"](tag="", type="")["answer"]["count"] == 4
    assert tools["pipe_length_tool"](edge="pipe_1")["answer"]["n_edges"] == 1


def test_json_calls_are_validated_and_never_raise():
    ok = call_tool(idx(), "count_symbols", {"region": [0, 0, 150, 100]})
    assert ok["answer"]["count"] == 2
    for name, args in [
        ("nope", {}),
        ("connected_to", {}),  # missing node
        ("connected_to", {"node": "node_1", "extra": 1}),
        ("count_symbols", {"region": [1, 2, 3]}),
        ("count_symbols", {"region": "all"}),
        ("pipe_length", {"edge": 5}),
    ]:
        r = call_tool(idx(), name, args)
        assert r["reliable"] is False and "error" in r["answer"], (name, args)
    assert validate_args("path_between", {"a": "x", "b": "y"}) is None


def test_compact_truncates_long_id_lists():
    r = {"tool": "t", "sheet": "s", "reliable": True, "caveats": [], "answer": {}, "coverage": [],
         "node_ids": [f"node_{i}" for i in range(100)], "edge_ids": []}  # fmt: skip
    c = compact(r, max_ids=10)
    assert len(c["node_ids"]) == 10 and c["node_ids_truncated"] == 100


def test_symbol_counts_from_the_detector_carry_the_measured_recall_caveat():
    doc = copy.deepcopy(CHAIN)
    assert not any("detector" in c and "found about" in c for c in idx(doc).count_symbols()["caveats"])
    doc["coverage"][0]["extractor_id"] = "ext_det"
    r = idx(doc).count_symbols()
    assert any("found about 87%" in c for c in r["caveats"])
    assert r["reliable"] is True  # a warning, not a refusal


# ---------------------------------------------------------------- router


@pytest.fixture
def dirs(tmp_path):
    floor, pipes = tmp_path / "index", tmp_path / "pipes"
    (floor / "src").mkdir(parents=True)
    return floor, pipes


def put_pipe(pipes, doc, name="plan#0.pipe.json", source="src"):
    p = pipes / source / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def test_router_picks_tool_sets_from_the_files_that_exist(dirs):
    floor, pipes = dirs
    f = "data/src/plan.pdf"
    r = route_page(f, index_dir=floor, pipes_dir=pipes)
    assert r["tool_sets"] == [] and "python -m pipes" in r["reason"] and "python -m translator" in r["reason"]
    put_pipe(pipes, CHAIN)
    r = route_page(f, index_dir=floor, pipes_dir=pipes)
    assert r["tool_sets"] == ["pipe"] and r["pipe"].endswith("plan#0.pipe.json")
    (floor / "src" / "plan.json").write_text("{}", encoding="utf-8")
    r = route_page(f, index_dir=floor, pipes_dir=pipes)
    assert r["tool_sets"] == ["floor_plan", "pipe"] and "pipes, valves" in r["reason"]
    (pipes / "src" / "plan#0.pipe.json").unlink()
    assert route_page(f, index_dir=floor, pipes_dir=pipes)["tool_sets"] == ["floor_plan"]


def test_router_finds_multi_part_floor_indexes_and_the_right_page(dirs):
    floor, pipes = dirs
    (floor / "src" / "plan__level-1.json").write_text("{}", encoding="utf-8")
    put_pipe(pipes, CHAIN, "plan#2.pipe.json")
    assert route_page("a/src/plan.pdf", 0, index_dir=floor, pipes_dir=pipes)["tool_sets"] == ["floor_plan"]
    assert route_page("a/src/plan.pdf", 2, index_dir=floor, pipes_dir=pipes)["tool_sets"] == [
        "floor_plan",
        "pipe",
    ]


def test_publish_validates_and_uses_the_sheets_own_source_file(tmp_path):
    doc = make([(1, 2)])
    doc["sheet"]["source_file"] = "ds/src/plan.png"
    src = tmp_path / "in.pipe.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    out = publish(src, tmp_path / "pipes")
    assert out == pipe_index_path("ds/src/plan.png", 0, tmp_path / "pipes") and out.exists()
    assert PipeIndex.load(out).count_symbols()["answer"]["count"] == 4
    bad = copy.deepcopy(doc)
    bad["edges"][0]["ends"][0]["node_id"] = "node_99"
    src.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        publish(src, tmp_path / "pipes")


# ---------------------------------------------------------------- the example questions


def test_ten_questions_on_three_sheets_each_with_an_expected_answer_and_a_basis():
    assert len(aq.QUESTIONS) == 10 and {q["sheet"] for q in aq.QUESTIONS} == {"157", "151", "176"}
    assert {q["basis"] for q in aq.QUESTIONS} <= {"ground_truth", "printed", "visual", "honesty"}
    assert all(q["expected"] and q["question"].endswith("?") and q["tool"][0] in NAMES for q in aq.QUESTIONS)


def test_answer_checkers():
    assert aq.first_number("There are 46 symbols. Source: node_1, node_2") == 46
    assert aq.first_number('Three pipes carry 4"-S1-P-10014.') == 3  # the tag's digits are not the count
    assert aq.first_number("Symbol node_35 carries it. Source: node_35") is None
    assert aq.says_yes("Yes, node_8 is connected to node_22.") and not aq.says_yes(
        "No, they are not connected."
    )
    assert aq.declines_metres("Not given in metres: the sheet is not drawn to scale, 879 pixels.")
    assert not aq.declines_metres("The route is 12.5 m long and to scale.")
    assert aq.says_none_found("No symbol tagged V-9999 was found.") and not aq.says_none_found(
        "Yes, node_3 is tagged."
    )


def test_null_arguments_are_dropped_before_validation():
    """Regression: the wrappers pass region=None; it once failed validation and the model had to retry."""
    r = call_tool(idx(), "count_symbols", {"region": None, "type": "", "tag": ""})
    assert r["answer"]["count"] == 4 and r["reliable"]
    assert call_tool(idx(), "connected_to", {"node": None})["reliable"] is False  # still required
