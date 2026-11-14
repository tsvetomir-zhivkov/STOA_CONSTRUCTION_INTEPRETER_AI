import copy

import pytest

from pipes.tags import attach_tags, classify, normalise
from pipes.tests.test_graph_metrics import make
from pipes.tests.test_tools import idx
from pipes.write import finalize

# four 20 px symbols on y=20, centres x = 20, 120, 220, 320; pipes pipe_1 (1-2), pipe_2 (2-3), pipe_3 (3-4)
CHAIN = make([(1, 2), (2, 3), (3, 4)])


def text(i, raw, box, score=0.9, rot=0):
    return {
        "id": f"text_{i}",
        "raw_text": raw,
        "bbox_px": box,
        "rotation_deg": rot,
        "role": "unknown",
        "value": None,
        "attached_to": [],
        "language": None,
        "provenance": {"extractor_id": "ext_human", "confidence": score, "source_ref": None},
    }


def tagged(texts):
    doc = copy.deepcopy(CHAIN)
    doc["texts"] = texts
    stats = attach_tags(doc)
    return finalize(doc), stats


@pytest.mark.parametrize(
    "raw,role,canon",
    [
        ('4"-S1-P-10014', "line_tag", '4"-S1-P-10014'),
        ("6-S1-RV-10028", "line_tag", "6-S1-RV-10028"),
        ("25-Pr-005-NI", "line_tag", "25-PR-005-NI"),
        ("30-P104A/B", "equipment_tag", "30-P104A/B"),
        ("30-F103", "equipment_tag", "30-F103"),
        ("V.100", "equipment_tag", "V-100"),  # OCR dropped the hyphen
        ("PV=10014A/B", "equipment_tag", "PV-10014A/B"),
        ("EV-600", "equipment_tag", "EV-600"),
        ('2"', "diameter", '2"'),
        ("DN50", "diameter", "DN50"),
        ("30-0102", "unknown", None),  # numbers only: not a tag
        ("VACUUM PUMP", "unknown", None),
        ("SHEET1", "unknown", None),
        ("10/28/19", "unknown", None),
        ('3"x4"', "unknown", None),  # a reducer note, not a pipe size
    ],
)
def test_roles(raw, role, canon):
    c = classify(raw)
    assert c["role"] == role and c["canon"] == canon


def test_normalise_only_repairs_separators():
    assert normalise(" v.101 ") == "V-101" and normalise("1.5”") == '1.5"' and normalise("203") == "203"


def test_equipment_tag_goes_to_the_nearest_symbol_and_only_within_range():
    doc, st = tagged([text(1, "V.101", [105, 32, 135, 42]), text(2, "P-999", [600, 80, 640, 90])])
    n2 = next(n for n in doc["nodes"] if n["id"] == "node_2")
    assert n2["tag"] == "V-101" and n2["tag_text_id"] == "text_1"
    t = {x["id"]: x for x in doc["texts"]}
    assert t["text_1"]["attached_to"] == ["node_2"] and t["text_1"]["role"] == "equipment_tag"
    assert t["text_2"]["attached_to"] == []  # too far from every symbol
    cov = next(c for c in doc["coverage"] if c["category"] == "tags")
    assert cov["status"] == "partial" and cov["element_ids"] == ["text_2"]  # honest: one tag found no home


def test_a_text_inside_the_symbol_box_wins_over_a_closer_neighbour_text_gap():
    doc, _ = tagged([text(1, "V-7", [110, 12, 130, 28])])
    assert next(n for n in doc["nodes"] if n["id"] == "node_2")["tag"] == "V-7"


def test_low_confidence_text_is_ignored():
    doc, st = tagged([text(1, "V-101", [105, 32, 135, 42], score=0.3)])
    assert all(n["tag"] is None for n in doc["nodes"]) and st["equipment_tags"] == 0


def test_line_tag_attaches_to_the_pipe_beside_it_and_gives_its_size():
    doc, _ = tagged([text(1, '4"-S1-P-10014', [40, 4, 100, 14])])  # above pipe_1 (y=20)
    e = {e["id"]: e for e in doc["edges"]}
    assert e["pipe_1"]["line_tag"] == '4"-S1-P-10014' and e["pipe_1"]["line_tag_text_id"] == "text_1"
    d = e["pipe_1"]["diameter"]
    assert d["source"] == "label" and d["value"]["dn_mm"] == 100 and d["text_id"] == "text_1"


def test_line_tag_is_carried_through_small_in_line_symbols_but_never_over_another_tag():
    doc, st = tagged(
        [text(1, '4"-S1-P-10014', [40, 4, 100, 14]), text(2, '6"-S1-P-10015', [240, 4, 300, 14])]
    )
    e = {e["id"]: e for e in doc["edges"]}
    assert e["pipe_1"]["line_tag"] == '4"-S1-P-10014' and e["pipe_3"]["line_tag"] == '6"-S1-P-10015'
    # pipe_2 sits between two valves, one tag on each side: it takes one of them, from a neighbour, marked as carried
    assert e["pipe_2"]["line_tag"] in ('4"-S1-P-10014', '6"-S1-P-10015')
    assert any(x.startswith("pipe_") for x in e["pipe_2"]["provenance"]["evidence_ids"])
    assert e["pipe_2"]["diameter"]["source"] == "propagated"
    assert not any(
        x.startswith("pipe_") for x in e["pipe_1"]["provenance"]["evidence_ids"]
    )  # read, not carried
    assert st["propagated"] == 1


def test_a_tag_does_not_pass_through_large_equipment():
    boxes = [
        [10, 10, 30, 30],
        [110, 0, 190, 80],
        [210, 10, 230, 30],
        [310, 10, 330, 30],
    ]  # node_2 is a big machine
    doc = make([(1, 2), (2, 3), (3, 4)], boxes)
    doc["texts"] = [text(1, '4"-S1-P-10014', [40, 4, 100, 14])]
    attach_tags(doc)
    e = {e["id"]: e for e in doc["edges"]}
    assert e["pipe_1"]["line_tag"] and e["pipe_2"]["line_tag"] is None


def test_instrument_code_and_loop_number_make_one_tag():
    doc, _ = tagged([text(1, "TIC", [112, 36, 128, 46]), text(2, "203", [112, 48, 128, 58])])
    n2 = next(n for n in doc["nodes"] if n["id"] == "node_2")
    assert n2["tag"] == "TIC-203"
    assert {x["id"]: x["attached_to"] for x in doc["texts"]} == {"text_1": ["node_2"], "text_2": ["node_2"]}


def test_retagging_is_idempotent():
    doc, _ = tagged([text(1, "V.101", [105, 32, 135, 42]), text(2, '4"-S1-P-10014', [40, 4, 100, 14])])
    again = copy.deepcopy(doc)
    attach_tags(again)
    assert [n["tag"] for n in again["nodes"]] == [n["tag"] for n in doc["nodes"]]
    assert [e["line_tag"] for e in again["edges"]] == [e["line_tag"] for e in doc["edges"]]
    assert sum(c["category"] == "tags" for c in again["coverage"]) == 1


def test_tools_filter_by_tag_and_measure_a_tagged_line():
    doc, _ = tagged(
        [
            text(1, "V.101", [105, 32, 135, 42]),
            text(2, "V-102", [205, 32, 235, 42]),
            text(3, '4"-S1-P-10014', [40, 4, 100, 14]),
        ]
    )
    r = idx(doc).count_symbols(tag="V-*")
    assert r["answer"]["count"] == 2 and r["answer"]["tags"] == {"node_2": "V-101", "node_3": "V-102"}
    assert r["reliable"] and r["node_ids"] == ["node_2", "node_3"]
    assert (
        idx(doc).count_symbols(tag="v-101")["answer"]["count"] == 1
    )  # case and OCR separators do not matter
    r = idx(doc).pipe_length(tag='4"-S1-P-10014')
    a = r["answer"]
    assert a["n_edges"] == 3 and a["propagated_edge_ids"] == ["pipe_2", "pipe_3"] and r["reliable"]
    assert a["length_px"] == 300.0 and a["length_m"] is None
    assert any("inherit the tag" in c for c in r["caveats"])
    assert r["highlights"]["edges"][0]["line_tag"] == '4"-S1-P-10014'


def test_tag_queries_say_when_the_tag_text_exists_but_is_attached_to_nothing():
    doc, _ = tagged([text(1, "V-555", [700, 80, 740, 90])])  # far from every symbol
    r = idx(doc).count_symbols(tag="V-555")
    assert r["answer"]["count"] == 0 and r["reliable"] is False  # tags coverage is partial
    assert any("attached to no symbol" in c for c in r["caveats"])
    r = idx(doc).pipe_length(tag="V-555")
    assert any("attached to no pipe" in c for c in r["caveats"])


def test_partial_tag_coverage_only_blocks_absence_claims_and_queries_it_can_affect():
    # V-101 is attached; a stray unrelated tag-like text elsewhere makes the sheet's tags coverage partial
    doc, _ = tagged([text(1, "V.101", [105, 32, 135, 42]), text(2, "P-999", [700, 80, 740, 90])])
    cov = next(c for c in doc["coverage"] if c["category"] == "tags")
    assert cov["status"] == "partial"
    found = idx(doc).count_symbols(tag="V-101")
    assert (
        found["answer"]["count"] == 1 and found["reliable"] is True
    )  # positive answer, nothing contradicts it
    assert any("only partly attached" in c for c in found["caveats"])
    missing = idx(doc).count_symbols(tag="V-555")
    assert missing["answer"]["count"] == 0 and missing["reliable"] is False
    assert any("do not say the tag is absent" in c for c in missing["caveats"])
