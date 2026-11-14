import copy

from pipes.graph import edge_doc
from pipes.ingest.pid_dataset import gt_document
from pipes.tests.test_graph_metrics import BOXES, make
from pipes.tools import PipeIndex
from pipes.write import finalize

CHAIN = make([(1, 2), (2, 3), (3, 4)])  # four symbols 100 px apart on one line


def idx(doc=CHAIN):
    return PipeIndex(doc)


def end(node):
    return {
        "node_id": node,
        "port_index": None,
        "snap": "snapped" if node else "unsnapped",
        "snap_distance_px": 0,
    }


def test_count_symbols_all_and_in_a_region_with_highlights():
    r = idx().count_symbols()
    assert r["answer"]["count"] == 4 and r["reliable"] and r["node_ids"] == [f"node_{i}" for i in range(1, 5)]
    r = idx().count_symbols(region=[0, 0, 150, 100])
    assert r["answer"]["count"] == 2 and r["node_ids"] == ["node_1", "node_2"]
    assert r["highlights"]["regions"] == [[0, 0, 150, 100]]
    assert r["highlights"]["nodes"][0]["bbox_px"] == BOXES[0]


def test_an_empty_region_is_not_reported_as_empty_when_coverage_is_not_analysed():
    doc = copy.deepcopy(CHAIN)
    doc["coverage"][0]["status"] = "partial"
    r = idx(doc).count_symbols(region=[500, 0, 600, 100])
    assert r["answer"]["count"] == 0 and r["reliable"] is False
    assert any("do not say there are none" in c for c in r["caveats"])
    # analysed and confident: zero is a real answer
    assert idx().count_symbols(region=[500, 0, 600, 100])["reliable"] is True


def test_bad_region_and_unknown_ids_give_an_error_not_a_crash():
    assert idx().count_symbols(region=[10, 10, 5, 5])["reliable"] is False
    for r in (
        idx().connected_to("node_99"),
        idx().path_between("node_1", "nope"),
        idx().pipe_length(edge="x"),
    ):
        assert r["reliable"] is False and "error" in r["answer"]
    assert "error" in idx().pipe_length()["answer"]  # needs edge or tag


def test_connected_to_returns_direct_neighbours_with_polylines_running_away_from_the_node():
    r = idx().connected_to("node_2")
    assert r["answer"]["connected"] == ["node_1", "node_3"] and r["reliable"]
    links = {x["node_id"]: x for x in r["answer"]["links"]}
    assert links["node_3"]["polylines_px"][0][0] == [120.0, 20.0]  # starts at node_2's centre
    assert links["node_1"]["polylines_px"][0][0] == [120.0, 20.0]
    assert set(r["edge_ids"]) == {"pipe_1", "pipe_2"}


def test_connected_to_goes_through_a_tee_but_not_through_a_symbol():
    syms = [{"cls_raw": "symbol", "bbox_px": b} for b in BOXES[:3]]
    base = gt_document("s", "test", 400, 100, syms)
    tee = {**copy.deepcopy(base["nodes"][0]), "id": "node_4", "type": "tee", "subtype": None}
    base["nodes"].append(tee)
    e = lambda i, a, b, pts: edge_doc(i, [end(a), end(b)], pts, "ext_human", 1.0)  # noqa: E731
    base["edges"] = [
        e(1, "node_1", "node_4", [[20, 20], [100, 20]]),
        e(2, "node_4", "node_2", [[100, 20], [120, 20]]),
        e(3, "node_4", "node_3", [[100, 20], [100, 60], [220, 60]]),
    ]
    r = idx(finalize(base)).connected_to("node_1")
    assert r["answer"]["connected"] == ["node_2", "node_3"] and "node_4" in r["node_ids"]


def test_path_between_gives_the_route_the_symbols_on_it_and_the_length():
    r = idx().path_between("node_1", "node_4")
    a = r["answer"]
    assert a["connected"] and a["path_node_ids"] == ["node_1", "node_2", "node_3", "node_4"]
    assert a["path_edge_ids"] == ["pipe_1", "pipe_2", "pipe_3"] and a["via_symbols"] == ["node_2", "node_3"]
    assert (
        a["length_px"] == 300.0
        and a["path_polyline_px"][0] == [20.0, 20.0]
        and a["path_polyline_px"][-1] == [320.0, 20.0]
    )
    assert r["highlights"]["edges"][0]["id"] == "pipe_1" and r["reliable"]
    assert idx().path_between("node_4", "node_1")["answer"]["path_polyline_px"][0] == [320.0, 20.0]


def test_no_path_is_certain_only_when_nothing_dangles():
    split = make([(1, 2), (3, 4)])
    r = idx(split).path_between("node_1", "node_4")
    assert r["answer"]["connected"] is False and r["reliable"] is True
    dangling = copy.deepcopy(split)
    dangling["edges"].append(
        edge_doc(9, [end("node_3"), end(None)], [[220, 20], [260, 20]], "ext_graph", 0.5)
    )
    r = idx(dangling).path_between("node_1", "node_4")
    assert r["answer"]["connected"] is False and r["reliable"] is False and r["edge_ids"] == ["pipe_9"]
    assert any("not connected as traced" in c for c in r["caveats"])


def test_connected_to_flags_dangling_ends_next_to_the_node():
    doc = copy.deepcopy(CHAIN)
    doc["edges"].append(edge_doc(9, [end("node_4"), end(None)], [[320, 20], [360, 20]], "ext_graph", 0.5))
    r = idx(doc).connected_to("node_4")
    assert r["reliable"] is False and r["answer"]["dangling_edge_ids"] == ["pipe_9"]


def test_pipe_length_pixels_only_when_not_to_scale_and_metres_when_written():
    r = idx().pipe_length(edge="pipe_1")
    assert r["answer"]["length_px"] == 100.0 and r["answer"]["length_m"] is None
    assert any("not drawn to scale" in c for c in r["caveats"])
    doc = copy.deepcopy(CHAIN)
    doc["edges"][0]["length"]["written_m"] = 2.5
    r = idx(doc).pipe_length(edge="pipe_1")
    assert r["answer"]["length_m"] == 2.5 and r["answer"]["basis"] == "written"


def test_pipe_length_by_tag_is_a_lower_bound_when_a_length_is_missing_and_unreliable_without_tag_coverage():
    doc = copy.deepcopy(CHAIN)
    for i in (0, 1):
        doc["edges"][i]["line_tag"] = "KV-1"
    doc["edges"][0]["length"]["written_m"] = 2.5
    r = idx(doc).pipe_length(tag="kv-1")
    a = r["answer"]
    assert (
        a["n_edges"] == 2
        and a["length_m"] == 2.5
        and a["lower_bound"]
        and a["missing_length_edge_ids"] == ["pipe_2"]
    )
    assert r["reliable"] is False  # no 'tags' coverage entry: the index cannot back a tag answer
    assert idx(doc).pipe_length(tag="LV-9")["answer"]["n_edges"] == 0
