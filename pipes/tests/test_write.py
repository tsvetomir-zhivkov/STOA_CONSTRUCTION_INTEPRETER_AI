import copy
import json
from pathlib import Path

import pytest

from pipes.write import PipeValidationError, finalize

SCHEMA = Path(__file__).parent.parent / "schema"
EXAMPLES = ["example_standalone_heating_schematic.json", "example_linked_pipe_plan_5815.json"]


def load(name):
    return json.loads((SCHEMA / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_finalize(name):
    out = finalize(load(name))
    assert out["schema_version"] == "piping-0.1" and out["pipe_segments"] and out["pipe_connections"]
    assert out == finalize(out)  # finalize is idempotent on a piping doc


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_are_valid_piping_files_and_round_trip_through_the_graph_view(name):
    from pipes.schema import convert, validate

    doc = load(name)
    assert validate.check(doc) == []
    g = convert.view(doc)
    assert len(g["edges"]) == len(doc["pipe_segments"])
    assert len(g["nodes"]) == len(doc["pipe_fittings"]) + len(doc["pipe_equipment"])
    assert {e["id"] for e in g["edges"]} == {s["id"] for s in doc["pipe_segments"]}


@pytest.mark.parametrize("name", EXAMPLES)
def test_missing_node_raises(name):
    pipe = copy.deepcopy(load(name))
    pipe["pipe_connections"][0]["from_entity_id"] = "n_does_not_exist"
    with pytest.raises(PipeValidationError):
        finalize(pipe)
