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
    assert finalize(load(name))["nodes"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_missing_node_raises(name):
    pipe = copy.deepcopy(load(name))
    pipe["edges"][0]["ends"][0]["node_id"] = "n_does_not_exist"
    with pytest.raises(PipeValidationError):
        finalize(pipe)
