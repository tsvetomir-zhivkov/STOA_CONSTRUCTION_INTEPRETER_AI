"""Workspace boundaries: owned process cancellation and source-native CAD previews."""

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from workspace.server import directory, own_process_tree, stop_process
from workspace.source_view import dxf_preview


def test_document_paths_reject_arbitrary_paths():
    for value in ("../outside", "C:/outside", "not-an-id"):
        with pytest.raises(ValueError):
            directory(value)


def test_cancellation_stops_parser_descendants(tmp_path):
    marker = tmp_path / "late-write.txt"
    child = (
        f"import time; from pathlib import Path; time.sleep(1.5); Path({str(marker)!r}).write_text('late')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "print('ready',flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", parent],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    own_process_tree(process)
    try:
        assert process.stdout.readline().strip() == "ready"
        stop_process(process)
        time.sleep(1.7)
        assert process.poll() is not None
        assert not marker.exists(), "A cancelled parser child must never write into a removed document"
    finally:
        stop_process(process)


def test_dxf_preview_retains_handles_layers_curves_and_original_coordinates(tmp_path):
    drawing = ezdxf.new()
    drawing.units = 6
    drawing.layers.new("OPENINGS")
    arc = drawing.modelspace().add_arc((10, 20), 1, 0, 90, dxfattribs={"layer": "OPENINGS"})
    text = drawing.modelspace().add_text("D01", dxfattribs={"insert": (10, 20), "height": 0.2})
    source, preview = tmp_path / "source.dxf", tmp_path / "preview.svg"
    drawing.saveas(source)
    dxf_preview(source, preview)
    root = ET.fromstring(preview.read_text())
    nodes = list(root.iter())
    assert any(n.get("data-source-handle") == arc.dxf.handle for n in nodes)
    assert any(n.get("data-layer") == "OPENINGS" for n in nodes)
    assert any(n.get("data-source-handle") == text.dxf.handle for n in nodes)
    manifest = json.loads(preview.with_suffix(".json").read_text())
    assert manifest["units"] == 6
    assert manifest["entities"][arc.dxf.handle]["type"] == "ARC"
    assert len(manifest["source_to_preview"]) == 16
