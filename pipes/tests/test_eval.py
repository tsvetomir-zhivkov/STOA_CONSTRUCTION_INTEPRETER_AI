import json
from pathlib import Path

from pipes.eval.score_graph import score_symbols
from pipes.ingest.pid_dataset import gt_document
from pipes.raster.tiles import origins, tile_boxes
from pipes.write import finalize

SYMS = [
    {"cls_raw": "Symbol", "bbox_px": [10, 10, 50, 50]},
    {"cls_raw": "Symbol", "bbox_px": [100, 10, 140, 60]},
]


def gt():
    return {"s": finalize(gt_document("s", "test", 300, 200, SYMS))}


def test_gt_against_itself_is_perfect():
    rows, tot = score_symbols(gt(), gt())
    assert tot == {"tp": 2, "fp": 0, "fn": 0}


def test_empty_prediction_scores_zero_without_crashing():
    rows, tot = score_symbols({}, gt())
    assert tot == {"tp": 0, "fp": 0, "fn": 2} and rows[0]["R"] == 0


def test_tiles_cover_page_and_clip_boxes():
    assert origins(500) == [0] and origins(1300)[-1] == 1300 - 640
    assert tile_boxes([[600, 10, 700, 50]], 0, 0, 640, 640) == []  # only 40% inside
    assert tile_boxes([[600, 10, 660, 50]], 0, 0, 640, 640) == [[600, 10, 640, 50]]


def test_dev_ground_truth_validates():
    dev = Path(__file__).parent.parent / "data_pipes" / "dev"
    files = list(dev.glob("*/*/gt.pipe.json"))
    if not files:  # dataset not ingested on this machine
        return
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert doc["edges"] == [] or doc["coverage"][1]["status"] == "analysed"
