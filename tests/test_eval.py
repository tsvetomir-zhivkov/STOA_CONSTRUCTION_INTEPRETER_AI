"""Step 1 checks: schema validation, the Swiss quick test, and the empty-index run."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "eval"))

import score  # noqa: E402
import split  # noqa: E402

from translator.config import data_root, questions_path  # noqa: E402
from translator.store import SchemaError, index_path, validate, write_drawing  # noqa: E402

FIXTURE_INDEX = REPO / "eval" / "fixtures" / "index"
FIXTURE = FIXTURE_INDEX / "swiss_dwellings" / "site1000_bldg2082_floor9385.json"
QUICK_FILE = "data/swiss_dwellings/site1000_bldg2082_floor9385.pdf"

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_valid():
    validate(_fixture())


def test_invalid_doc_is_never_written(tmp_path):
    doc = _fixture()
    del doc["rooms"][0]["provenance"]
    with pytest.raises(SchemaError):
        write_drawing(doc, index_dir=tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_unknown_field_rejected():
    doc = copy.deepcopy(_fixture())
    doc["rooms"][0]["area"] = 1.0  # typo for area_m2
    with pytest.raises(SchemaError):
        validate(doc)


def test_write_roundtrip(tmp_path):
    out = write_drawing(_fixture(), index_dir=tmp_path)
    assert out == tmp_path / "swiss_dwellings" / "site1000_bldg2082_floor9385.json"
    assert index_path("data/ifc_models/m.ifc", "1.korrus", tmp_path).name == "m__1.korrus.json"


@needs_data
def test_quick_two_rooms_score_correct():
    questions = score.load_questions(data_root())
    df = score.score(questions, score.load_index(FIXTURE_INDEX), holdout=set(), which="all")
    correct = df[df.status == "correct"]
    assert set(correct.path) == {QUICK_FILE}
    assert sorted(correct.answer) == [15.9, 15.9, 16.78, 16.78]
    assert set(correct.intent) == {"sw_room_area"}


@needs_data
def test_empty_index_scores_zero(tmp_path):
    questions = score.load_questions(data_root())
    df = score.score(questions, score.load_index(tmp_path), holdout=set(), which="all")
    assert (df.status != "correct").all()
    assert len(df) == len(questions)


@needs_data
def test_holdout_is_by_file_and_about_20pct():
    questions = score.load_questions(data_root())
    paths = sorted({q["path"] for q in questions})
    held = split.make_split(paths)
    assert QUICK_FILE not in held
    assert 0.15 <= len(held) / len(paths) <= 0.25
    # siblings of one IFC model land on the same side
    ifc = [p for p in paths if "bsc-molio" in p]
    assert len({p in held for p in ifc}) == 1
