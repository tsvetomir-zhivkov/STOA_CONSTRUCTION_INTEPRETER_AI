"""path_between / connected_to must read coverage: a negative answer is 'unknown' where pipes were not analysed."""

import copy

from PIL import Image

from pipes.agent_tools import make_tools
from pipes.demo import render
from pipes.tests.test_graph_metrics import make
from pipes.tools import PipeIndex

# node_1 -- node_2 are joined; node_3 and node_4 have no pipes. Boxes: x 10-30, 110-130, 210-230, 310-330 (y 10-30).
BASE = make([(1, 2)])


def cov(status, region, category="pipes", note="title_block region is not traced", cid="cov_9"):
    return {"id": cid, "region": region, "category": category, "status": status, "confidence": 0.0,
            "extractor_id": "ext_human", "element_ids": [], "note": note}  # fmt: skip


def doc_with(*entries):
    d = copy.deepcopy(BASE)
    d["coverage"] = [*d["coverage"], *entries]
    return d


def test_without_a_coverage_gap_nothing_changes():
    idx = PipeIndex(BASE)
    r = idx.connected_to("node_3")
    assert r["answer"]["connected"] == [] and r["answer"].get("unknown") is None and r["reliable"] is True
    r = idx.path_between("node_1", "node_3")
    assert r["answer"]["connected"] is False and r["reliable"] is True


def test_not_analysed_pipes_over_a_node_make_the_negative_answers_unknown():
    idx = PipeIndex(doc_with(cov("not_analysed", [200, 0, 240, 40])))  # covers node_3 only
    r = idx.connected_to("node_3")
    assert r["answer"]["connected"] is None and r["answer"]["unknown"] is True and r["reliable"] is False
    assert r["answer"]["reason"] == "unknown: pipes not analysed here"
    assert (
        "pipes not analysed here" in r["caveats"][0] and "title_block region is not traced" in r["caveats"][0]
    )
    assert r["highlights"]["regions"] == [[200, 0, 240, 40]]  # the gap is drawn on the sheet
    r = idx.path_between("node_1", "node_3")  # one end sits in the gap
    assert r["answer"]["connected"] is None and r["answer"]["unknown"] is True and r["reliable"] is False
    # elsewhere the answer is as before: node_4 is outside the gap, node_1 -- node_4 is not connected
    assert idx.connected_to("node_4")["answer"]["connected"] == []
    assert idx.path_between("node_1", "node_4")["answer"]["connected"] is False


def test_positive_answers_stand_even_with_a_gap_elsewhere():
    idx = PipeIndex(doc_with(cov("not_analysed", [200, 0, 240, 40])))
    r = idx.path_between("node_1", "node_2")
    assert r["answer"]["connected"] is True and r["reliable"] is True  # a found route is evidence
    assert idx.connected_to("node_1")["answer"]["connected"] == ["node_2"]


def test_a_whole_page_gap_makes_every_negative_answer_unknown():
    idx = PipeIndex(doc_with(cov("not_analysed", "page")))
    for r in (idx.connected_to("node_4"), idx.path_between("node_3", "node_4")):
        assert r["answer"]["connected"] is None and r["answer"]["unknown"] is True and r["reliable"] is False
        assert "on the whole page" in r["caveats"][0] and r["highlights"]["regions"] == []


def test_partial_connectivity_counts_like_not_analysed_but_analysed_does_not():
    partial = cov(
        "partial", [300, 0, 340, 40], "connectivity", "pipe end that reaches no symbol, junction or open end"
    )
    idx = PipeIndex(doc_with(partial))  # a free pipe end was traced beside node_4
    assert idx.connected_to("node_4")["answer"]["unknown"] is True
    assert idx.path_between("node_1", "node_4")["answer"]["unknown"] is True
    fine = cov("analysed", "page", "connectivity", "exact", cid="cov_8")
    assert PipeIndex(doc_with(fine)).connected_to("node_4")["answer"]["connected"] == []


def test_the_agent_wrappers_hand_the_model_the_unknown_marker_and_the_demo_can_draw_it(tmp_path):
    d = doc_with(cov("not_analysed", "page"))
    log: list = []
    tools = {f.__name__: f for f in make_tools(d, log)}
    seen = tools["path_between_tool"]("node_3", "node_4")
    assert (
        seen["answer"]["unknown"] is True
        and seen["answer"]["connected"] is None
        and seen["reliable"] is False
    )
    sheet = tmp_path / "s.png"
    Image.new("RGB", (400, 100), "white").save(sheet)
    assert render(log[0]["result"], sheet, tmp_path / "o.png").exists()
