"""Graph creation on tiny synthetic inputs whose correct graph is known exactly.

Scan cases draw a PNG with cv2 and give the tracer ground-truth symbol boxes (and OCR-style text boxes), so the detector
and the OCR are not under test. DXF cases build the same layouts with ezdxf and run the vector route. Every case also
runs through finalize (schema validation) because both routes call it.

Layout used by the scan cases (600 x 400 px, black 3 px lines on white, symbol = 50 x 50 outline box):
  A at (50,150)-(100,200)   B at (400,150)-(450,200)   C at (225,330)-(275,380)   D at (225,20)-(275,70)
"""

from __future__ import annotations

import json
from collections import Counter

import cv2
import ezdxf
import numpy as np
import pytest
from ezdxf import units

from pipes.extract.dxf_pipes import extract
from pipes.ingest.pid_dataset import gt_document
from pipes.raster.pipeline import run_sheet
from pipes.tools import PipeIndex
from pipes.write import finalize

W, H = 600, 400
FONT = cv2.FONT_HERSHEY_SIMPLEX
A, B, C, D = (50, 150, 100, 200), (400, 150, 450, 200), (225, 330, 275, 380), (225, 20, 275, 70)
STRUCTURAL = {"tee", "junction", "open_end", "elbow", "cross"}


# ---------------------------------------------------------------- scan helpers


def canvas(w=W, h=H):
    return np.full((h, w), 255, np.uint8)


def box(img, b):
    """Draw a symbol outline and return the ground-truth box (the outline grown by 2 px)."""
    cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), 0, 2)
    return [b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2]


def pipe(img, pts, t=3):
    cv2.polylines(img, [np.array(pts, np.int32)], False, 0, t)


def text(img, s, x, y):
    """Draw text with its baseline at y and return the OCR-style item (page-pixel polygon around it)."""
    (tw, th), bl = cv2.getTextSize(s, FONT, 0.5, 1)
    cv2.putText(img, s, (x, y), FONT, 0.5, 0, 1, cv2.LINE_AA)
    x0, y0, x1, y1 = x - 1, y - th - 1, x + tw + 1, y + bl + 1
    return {"text": s, "score": 0.95, "poly": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def scan(tmp_path, img, boxes, texts=(), name="c"):
    """Run the scan route on `img` with GT boxes; returns the finalized pipe doc."""
    d = tmp_path / "test" / name
    d.mkdir(parents=True)
    cv2.imwrite(str(d / "sheet.png"), img)
    h, w = img.shape[:2]
    syms = [{"cls_raw": "symbol", "bbox_px": [float(v) for v in b]} for b in boxes]
    (d / "gt.pipe.json").write_text(
        json.dumps(finalize(gt_document(name, "test", w, h, syms))), encoding="utf-8"
    )
    doc = run_sheet(d, "gt", texts=list(texts))
    doc.pop("_debug", None)
    json.dumps(doc)  # serialisable
    return finalize(doc)  # and still schema-valid


def structural(doc, kind=None):
    return [n for n in doc["nodes"] if n["type"] in ({kind} if kind else STRUCTURAL)]


def symbols(doc):
    return [n for n in doc["nodes"] if n["type"] not in STRUCTURAL]


def end_nodes(doc):
    return [[x["node_id"] for x in e["ends"]] for e in doc["edges"]]


def components(doc):
    return doc["summary"]["connected_components"]


def sym_ids(doc):
    """node ids of the four boxes in the order they were given (node_1 = first box ...)."""
    return {n["id"] for n in symbols(doc)}


# ---------------------------------------------------------------- scan layouts (shared by cases 3, 4, 11, 12)


def layout_t(img=None):
    img = canvas() if img is None else img
    boxes = [box(img, A), box(img, B), box(img, C)]
    pipe(img, [(100, 175), (400, 175)])  # A -- B along y = 175
    pipe(img, [(250, 175), (250, 330)])  # tee at (250,175) down to C
    return img, boxes


def layout_cross(dot: bool):
    img = canvas()
    boxes = [box(img, A), box(img, B), box(img, C), box(img, D)]
    pipe(img, [(100, 175), (400, 175)], t=2)  # A -- B
    pipe(img, [(250, 70), (250, 330)], t=2)  # D -- C, crossing A -- B at (250,175)
    if dot:  # a dot ~5 line widths across: the smallest the tracer reads as a dot (see has_dot)
        cv2.circle(img, (250, 175), 9, 0, -1)
    return img, boxes


# ---------------------------------------------------------------- 1. two boxes, one line


def test_01_two_boxes_joined_by_one_line(tmp_path):
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    pipe(img, [(100, 175), (400, 175)])
    doc = scan(tmp_path, img, boxes)
    assert len(symbols(doc)) == 2 and structural(doc) == [] and len(doc["edges"]) == 1
    e = doc["edges"][0]
    assert {x["node_id"] for x in e["ends"]} == {"node_1", "node_2"} and all(
        x["snap"] == "snapped" for x in e["ends"]
    )
    assert components(doc) == 1 and doc["summary"]["unsnapped_end_count"] == 0


# ---------------------------------------------------------------- 2. an L-shaped pipe is one edge


def test_02_a_bend_does_not_split_the_pipe(tmp_path):
    img = canvas()
    boxes = [box(img, A), box(img, (275, 330, 325, 380))]
    pipe(img, [(100, 175), (300, 175), (300, 330)])
    doc = scan(tmp_path, img, boxes)
    assert len(doc["edges"]) == 1 and structural(doc) == [] and components(doc) == 1
    assert {x["node_id"] for x in doc["edges"][0]["ends"]} == {"node_1", "node_2"}
    assert len(doc["edges"][0]["polyline_px"]) >= 3  # the corner is a vertex of the one polyline


# ---------------------------------------------------------------- 3. a T-junction


def test_03_t_junction_is_one_tee_and_three_edges(tmp_path):
    img, boxes = layout_t()
    doc = scan(tmp_path, img, boxes)
    tees = structural(doc, "tee")
    assert len(tees) == 1 and len(structural(doc)) == 1 and len(doc["edges"]) == 3 and components(doc) == 1
    ends = end_nodes(doc)
    assert all(tees[0]["id"] in e for e in ends)  # every pipe runs from the tee to a symbol
    assert {n for e in ends for n in e} == sym_ids(doc) | {tees[0]["id"]}
    assert doc["summary"]["unsnapped_end_count"] == 0


# ---------------------------------------------------------------- 4. crossing without and with a dot


def test_04a_crossing_without_a_dot_is_not_connected(tmp_path):
    img, boxes = layout_cross(dot=False)
    doc = scan(tmp_path, img, boxes)
    assert structural(doc) == [] and len(doc["edges"]) == 2 and components(doc) == 2
    idx = PipeIndex(doc)
    assert idx.path_between("node_1", "node_2")["answer"]["connected"] is True  # A - B
    assert idx.path_between("node_1", "node_3")["answer"]["connected"] is False  # A - C: only crossed


def test_04b_crossing_with_a_filled_dot_is_a_junction_of_four_edges(tmp_path):
    img, boxes = layout_cross(dot=True)
    doc = scan(tmp_path, img, boxes)
    junctions = structural(doc, "junction")
    assert len(junctions) == 1 and len(doc["edges"]) == 4 and components(doc) == 1
    assert all(junctions[0]["id"] in e for e in end_nodes(doc))


# ---------------------------------------------------------------- 5. a free end


def test_05a_a_pipe_ending_in_empty_space_is_flagged_unsnapped(tmp_path):
    img = canvas()
    boxes = [box(img, A)]
    pipe(img, [(100, 175), (300, 175)])
    doc = scan(tmp_path, img, boxes)
    assert len(doc["edges"]) == 1 and doc["summary"]["unsnapped_end_count"] == 1 and structural(doc) == []
    ends = doc["edges"][0]["ends"]
    assert (
        sorted(x["snap"] for x in ends) == ["snapped", "unsnapped"]
        and [x["node_id"] for x in ends].count(None) == 1
    )
    partial = [c for c in doc["coverage"] if c["category"] == "connectivity" and c["status"] == "partial"]
    assert len(partial) == 1 and partial[0]["element_ids"] == ["pipe_1"]


def test_05b_the_same_end_next_to_jatkuu_is_an_open_end(tmp_path):
    img = canvas()
    boxes = [box(img, A)]
    pipe(img, [(100, 175), (300, 175)])
    doc = scan(tmp_path, img, boxes, [text(img, "JATKUU", 312, 180)])
    assert [n["type"] for n in structural(doc)] == ["open_end"] and doc["summary"]["unsnapped_end_count"] == 0
    assert all(x["node_id"] for x in doc["edges"][0]["ends"])


# ---------------------------------------------------------------- 6. a dashed line


def dashed_layout(dash=24):
    """A -- B drawn pixel-exactly as dashes with gaps of 3, 4, 5 px (cv2 line caps would close such gaps)."""
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    x, k = 102, 0
    while x < 398:
        img[174:177, x : min(x + dash, 398)] = 0  # a 3 px thick dash
        x += dash + (3, 4, 5)[k % 3]
        k += 1
    row = np.concatenate([[0], img[175, 102:398] < 128, [0]]).astype(int)
    assert (np.diff(row) == 1).sum() >= 10, (
        "the fixture must really contain gaps"
    )  # guards against a vacuous test
    return img, boxes


@pytest.mark.xfail(
    strict=True,
    reason=(
        "today the dashed line VANISHES (0 edges, not a split): each dash is smaller than the (3w)^2 px noise limit in "
        "erase.remove_small and is dropped. Keeping elongated fragments + bridging short gaps makes this pass, but added "
        "~660 edges and ~700 unsnapped ends on the 20 real test sheets (and made the tracer several times slower), so it "
        "was reverted."
    ),
)
def test_06_a_dashed_line_with_small_gaps_is_one_pipe(tmp_path):
    img, boxes = dashed_layout()
    doc = scan(tmp_path, img, boxes)
    assert len(doc["edges"]) == 1 and {x["node_id"] for x in doc["edges"][0]["ends"]} == {"node_1", "node_2"}
    assert (
        doc["edges"][0]["line_style"] == "dashed"
    )  # and it should say so (line_style is not detected at all yet)


# ---------------------------------------------------------------- 7. text attached to the right element


def test_07a_dn50_parallel_to_a_pipe_goes_to_that_edge(tmp_path):
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    pipe(img, [(100, 175), (400, 175)])
    doc = scan(tmp_path, img, boxes, [text(img, "DN50", 230, 160)])
    e = doc["edges"][0]
    assert e["diameter"]["value"]["dn_mm"] == 50 and e["diameter"]["source"] == "label"
    t = next(t for t in doc["texts"] if t["raw_text"] == "DN50")
    assert t["role"] == "diameter" and t["attached_to"] == [e["id"]] and e["diameter"]["text_id"] == t["id"]


def test_07b_v101_next_to_a_box_goes_to_that_node(tmp_path):
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    pipe(img, [(100, 175), (400, 175)])
    doc = scan(tmp_path, img, boxes, [text(img, "V-101", 48, 222)])
    nodes = {n["id"]: n for n in symbols(doc)}
    assert nodes["node_1"]["tag"] == "V-101" and nodes["node_2"]["tag"] is None
    t = next(t for t in doc["texts"] if t["raw_text"] == "V-101")
    assert t["role"] == "equipment_tag" and t["attached_to"] == ["node_1"]


# ---------------------------------------------------------------- 8. sheet frame and title block


def test_08_frame_and_title_block_are_not_traced_as_pipes(tmp_path):
    img = canvas()
    boxes = [box(img, A), box(img, B)]
    pipe(img, [(100, 175), (400, 175)])
    cv2.rectangle(img, (6, 6), (593, 393), 0, 3)  # sheet frame
    cv2.rectangle(img, (400, 300), (593, 393), 0, 3)  # title block in the corner ...
    for y in (325, 350, 372):  # ... with rows
        cv2.line(img, (400, y), (593, y), 0, 2)
    doc = scan(tmp_path, img, boxes)
    kinds = Counter(r["kind"] for r in doc["sheet"]["regions"])
    assert (
        kinds["title_block"] == 1 and kinds["other"] >= 1
    )  # the corner block and the sheet frame are recognised
    assert len(doc["edges"]) == 1 and {x["node_id"] for x in doc["edges"][0]["ends"]} == {"node_1", "node_2"}
    xs = [p[0] for p in doc["edges"][0]["polyline_px"]]
    assert min(xs) > 90 and max(xs) < 410  # the one edge is the pipe, not a frame line


# ---------------------------------------------------------------- 9. big closed shape with three pipes


@pytest.mark.xfail(
    strict=True,
    reason=(
        "a closed RECTANGLE reads as a pipe loop: equipment.find_equipment only accepts outlines with curves/diagonals "
        "(ORTHO_MAX), and regions.classify then erases its outline as a frame, which strands the three pipes. Accepting "
        "rectilinear outlines with >= 3 attached pipes would also turn the cells of a pipe grid into equipment (sheet 159 "
        "has 63 such rectangles), and making the frame rule see attached pipes changes the traced graph of real sheets."
    ),
)
def test_09_a_large_closed_rectangle_with_three_pipes_is_one_equipment_node(tmp_path):
    img = canvas()
    boxes = [box(img, (20, 175, 60, 215)), box(img, (540, 175, 580, 215)), box(img, (280, 350, 320, 390))]
    cv2.rectangle(img, (200, 100), (400, 300), 0, 3)  # the equipment: not in the GT boxes
    pipe(img, [(60, 195), (200, 195)])
    pipe(img, [(400, 195), (540, 195)])
    pipe(img, [(300, 300), (300, 350)])
    doc = scan(tmp_path, img, boxes)
    equipment = [n for n in symbols(doc) if n.get("subtype") == "equipment"]
    assert len(equipment) == 1 and len(doc["edges"]) == 3  # three pipes to it, no loop of edges around it
    assert all(equipment[0]["id"] in e for e in end_nodes(doc)) and components(doc) == 1


# ---------------------------------------------------------------- 10. DXF: the same layouts


def dxf_new(tmp_path, unit=units.MM, extra_layers=()):
    d = ezdxf.new("R2018", setup=True)
    d.units = unit
    d.layers.add("P-HEAT-SUPPLY")
    for name in extra_layers:
        d.layers.add(name)
    blk = d.blocks.new("VALVE")
    blk.add_lwpolyline([(-50, -50), (50, -50), (50, 50), (-50, 50)], close=True)
    blk.add_attdef("TAG", (0, -90), height=20)
    return d, d.modelspace(), tmp_path / "c.dxf"


def put(msp, at, tag=None):
    ref = msp.add_blockref("VALVE", at, dxfattribs={"layer": "P-HEAT-SUPPLY"})
    if tag:
        ref.add_auto_attribs({"TAG": tag})


def line(msp, *pts):
    msp.add_lwpolyline(list(pts), dxfattribs={"layer": "P-HEAT-SUPPLY"})


def vector(d, path):
    d.saveas(path)
    return extract(path, "creation/c.dxf")


def test_10_dxf_case1_two_blocks_one_line(tmp_path):
    d, msp, path = dxf_new(tmp_path)
    put(msp, (0, 0))
    put(msp, (1000, 0))
    line(msp, (50, 0), (950, 0))
    doc = vector(d, path)
    assert len(symbols(doc)) == 2 and structural(doc) == [] and len(doc["edges"]) == 1
    assert {x["node_id"] for x in doc["edges"][0]["ends"]} == {"node_1", "node_2"}
    assert all(x["snap"] == "vector_exact" for x in doc["edges"][0]["ends"])


def test_10_dxf_case2_l_shaped_pipe_is_one_edge(tmp_path):
    d, msp, path = dxf_new(tmp_path)
    put(msp, (0, 0))
    put(msp, (1000, -1000))
    line(msp, (50, 0), (1000, 0), (1000, -950))
    doc = vector(d, path)
    assert len(doc["edges"]) == 1 and structural(doc) == [] and len(doc["edges"][0]["polyline_px"]) >= 3


def test_10_dxf_case3_t_junction(tmp_path):
    d, msp, path = dxf_new(tmp_path)
    for at in ((0, 0), (1000, 0), (500, -800)):
        put(msp, at)
    line(msp, (50, 0), (950, 0))
    line(msp, (500, 0), (500, -750))
    doc = vector(d, path)
    tees = structural(doc, "tee")
    assert len(tees) == 1 and len(structural(doc)) == 1 and len(doc["edges"]) == 3 and components(doc) == 1
    assert all(tees[0]["id"] in e for e in end_nodes(doc))


def test_10_dxf_case4_crossing_without_and_with_a_dot(tmp_path):
    d, msp, path = dxf_new(tmp_path)
    for at in ((0, 0), (1000, 0), (500, 500), (500, -500)):
        put(msp, at)
    line(msp, (50, 0), (950, 0))
    line(msp, (500, 450), (500, -450))
    doc = vector(d, path)
    assert structural(doc) == [] and len(doc["edges"]) == 2 and components(doc) == 2
    msp.add_circle((500, 0), 8, dxfattribs={"layer": "P-HEAT-SUPPLY"})
    doc = vector(d, path)
    assert len(structural(doc, "junction")) == 1 and len(doc["edges"]) == 4 and components(doc) == 1


def test_10_dxf_case5_free_end_unsnapped_then_open_end(tmp_path):
    d, msp, path = dxf_new(tmp_path)
    put(msp, (0, 0))
    line(msp, (50, 0), (800, 0))
    doc = vector(d, path)
    assert doc["summary"]["unsnapped_end_count"] == 1 and structural(doc) == []
    partial = [c for c in doc["coverage"] if c["category"] == "connectivity" and c["status"] == "partial"]
    assert len(partial) == 1 and partial[0]["element_ids"] == ["pipe_1"]
    msp.add_text("JATKUU", height=40, dxfattribs={"insert": (810, -20)})
    doc = vector(d, path)
    assert [n["type"] for n in structural(doc)] == ["open_end"] and doc["summary"]["unsnapped_end_count"] == 0


def test_10_dxf_a_5000_mm_line_is_5_metres(tmp_path):
    d, msp, path = dxf_new(tmp_path)
    put(msp, (0, 0))
    put(msp, (5100, 0))
    line(msp, (50, 0), (5050, 0))  # exactly 5000 mm between the two symbol boxes
    msp.add_text("PUTKISTOPOHJA", height=60, dxfattribs={"insert": (0, 400)})
    doc = vector(d, path)
    assert doc["sheet"]["is_to_scale"] is True and doc["scale"]["source"] == "vector_units"
    r = PipeIndex(doc).pipe_length(edge="pipe_1")
    assert r["answer"]["length_m"] == pytest.approx(5.0, abs=0.005) and r["answer"]["basis"] == "geometry"


# ---------------------------------------------------------------- 11. robustness


def test_11_a_blank_image_gives_an_empty_valid_document(tmp_path):
    doc = scan(tmp_path, canvas(), [])
    assert doc["nodes"] == [] and doc["edges"] == [] and doc["summary"]["connected_components"] == 0


def test_11_an_image_with_only_text_gives_no_pipes(tmp_path):
    img = canvas()
    items = [
        text(img, "GENERAL NOTES", 40, 60),
        text(img, "1. SEE SHEET 2", 40, 90),
        text(img, "V-101", 300, 200),
    ]
    doc = scan(tmp_path, img, [], items)
    assert doc["edges"] == [] and doc["nodes"] == [] and len(doc["texts"]) == 3


def test_11_a_rotated_t_junction_gives_the_same_graph(tmp_path):
    img, boxes = layout_t()
    rot = np.ascontiguousarray(np.rot90(img))  # 90 degrees counter-clockwise: (x, y) -> (y, W - x)
    rboxes = [[b[1], W - b[2], b[3], W - b[0]] for b in boxes]
    doc = scan(tmp_path, rot, rboxes)
    base = scan(tmp_path, img, boxes, name="base")
    assert Counter(n["type"] for n in doc["nodes"]) == Counter(n["type"] for n in base["nodes"])
    assert len(doc["edges"]) == 3 and components(doc) == 1 and len(structural(doc, "tee")) == 1
    assert doc["frame"]["width_px"] == H and doc["frame"]["height_px"] == W


# ---------------------------------------------------------------- 12. tools on the T-junction


@pytest.fixture(params=["scan", "dxf"])
def t_doc(request, tmp_path):
    if request.param == "scan":
        img, boxes = layout_t()
        return scan(tmp_path, img, boxes)
    d, msp, path = dxf_new(tmp_path)
    for at in ((0, 0), (1000, 0), (500, -800)):
        put(msp, at)
    line(msp, (50, 0), (950, 0))
    line(msp, (500, 0), (500, -750))
    return vector(d, path)


def test_12_path_between_the_far_ends_goes_through_the_tee_and_connected_to_lists_both_neighbours(t_doc):
    idx = PipeIndex(t_doc)
    tee = structural(t_doc, "tee")[0]["id"]
    r = idx.path_between("node_1", "node_2")  # A to B, the two ends of the through-line
    assert (
        r["answer"]["connected"] and tee in r["answer"]["path_node_ids"] and r["answer"]["via_symbols"] == []
    )
    assert len(r["answer"]["path_edge_ids"]) == 2 and r["reliable"]
    c = idx.connected_to("node_1")  # A: reached B and C, both through the tee
    assert sorted(c["answer"]["connected"]) == ["node_2", "node_3"] and c["reliable"]
    assert tee in c["node_ids"]  # the tee is part of the evidence
    assert sorted(idx.connected_to(tee)["answer"]["connected"]) == ["node_1", "node_2", "node_3"]
