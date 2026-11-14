import copy

from pipes.eval.metrics import adjacency, graph_metrics
from pipes.eval.score_graph import score_edges
from pipes.graph import edge_doc
from pipes.ingest.pid_dataset import gt_document
from pipes.write import finalize

BOXES = [[10, 10, 30, 30], [110, 10, 130, 30], [210, 10, 230, 30], [310, 10, 330, 30]]


def make(links, boxes=BOXES):
    syms = [{"cls_raw": "symbol", "bbox_px": b} for b in boxes]
    base = gt_document("s", "test", 400, 100, syms)
    nodes = {n["id"]: n for n in base["nodes"]}
    edges = []
    for i, (a, b) in enumerate(links, 1):
        na, nb = nodes[f"node_{a}"], nodes[f"node_{b}"]
        ends = [
            {"node_id": na["id"], "port_index": None, "snap": "snapped", "snap_distance_px": 0},
            {"node_id": nb["id"], "port_index": None, "snap": "snapped", "snap_distance_px": 0},
        ]
        edges.append(edge_doc(i, ends, [na["center_px"], nb["center_px"]], "ext_human", 1.0))
    return finalize(gt_document("s", "test", 400, 100, syms, edges))


GT = make([(1, 2), (2, 3), (3, 4)])


def test_gt_against_itself_is_perfect():
    m = graph_metrics(GT, GT)
    assert m["edges_matched"]["F1"] == 1 and m["edges_strict"]["F1"] == 1
    assert m["connectivity"]["accuracy"] == 1 and m["pipes"] == {"P": 1.0, "R": 1.0}


def test_empty_prediction_scores_zero_recall_without_crashing():
    pred = make([])
    m = graph_metrics(pred, GT)
    assert m["edges_matched"]["R"] == 0 and m["gt_edges"] == 3 and m["pred_edges"] == 0
    assert m["connectivity"]["accuracy"] < 1


def test_deleting_one_edge_drops_recall_by_exactly_one_over_n():
    pred = make([(1, 2), (2, 3)])  # 3 -- 4 missing
    m = graph_metrics(pred, GT)
    assert m["edges_matched"]["R"] == 2 / 3 and m["edges_matched"]["P"] == 1
    assert m["connectivity"]["accuracy"] < 1  # 4 is no longer connected to the rest
    assert m["pipes"]["R"] < 1


def test_a_spurious_edge_costs_precision_and_connectivity():
    gt = make([(1, 2), (3, 4)])
    pred = make([(1, 2), (3, 4), (2, 3)])
    m = graph_metrics(pred, gt)
    assert m["edges_matched"]["P"] == 2 / 3 and m["edges_matched"]["R"] == 1
    assert m["connectivity"]["accuracy"] < 1


def test_an_in_line_tee_is_not_a_symbol():
    """1 -- tee -- 2 is the adjacency (1, 2), the tee is structural."""
    base = gt_document("s", "test", 400, 100, [{"cls_raw": "symbol", "bbox_px": b} for b in BOXES[:2]])
    base["nodes"].append({**copy.deepcopy(base["nodes"][0]), "id": "node_3", "type": "tee", "subtype": None})
    ends = lambda a, b: [  # noqa: E731
        {"node_id": a, "port_index": None, "snap": "snapped", "snap_distance_px": 0},
        {"node_id": b, "port_index": None, "snap": "snapped", "snap_distance_px": 0},
    ]
    base["edges"] = [
        edge_doc(1, ends("node_1", "node_3"), [[20, 20], [60, 20]], "ext_graph", 1.0),
        edge_doc(2, ends("node_3", "node_2"), [[60, 20], [120, 20]], "ext_graph", 1.0),
    ]
    assert adjacency(base) == {frozenset({"node_1", "node_2"})}


def test_sheets_without_labelled_edges_are_skipped():
    assert score_edges({"s": GT}, {"s": make([])}) == []
    assert len(score_edges({"s": GT}, {"s": GT})) == 1
