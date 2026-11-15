"""Workspace boundaries: owned process cancellation and source-native CAD previews."""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from workspace.server import directory, own_process_tree, stop_process
from workspace.source_view import dxf_preview


def test_atomic_metadata_retries_a_transient_windows_read_lock(tmp_path, monkeypatch):
    from workspace import server

    destination = tmp_path / "document.json"
    server.atomic(destination, {"state": "UPLOADING"})
    replace = os.replace
    attempts = []

    def briefly_locked(source, target):
        attempts.append(True)
        if len(attempts) < 3:
            raise PermissionError("Windows file is temporarily open")
        replace(source, target)

    monkeypatch.setattr(server.os, "replace", briefly_locked)
    server.atomic(destination, {"state": "READY"})
    assert server.metadata(tmp_path) == {"state": "READY"}
    assert len(attempts) == 3


def test_extractor_start_failure_becomes_error_instead_of_endless_processing(tmp_path, monkeypatch):
    from workspace import server

    monkeypatch.setattr(server, "DATA", tmp_path)
    identity = str(uuid.uuid4())
    folder = server.directory(identity)
    folder.mkdir()
    server.atomic(folder / "document.json", {"state": "UPLOADING", "stored_name": "source.ifc"})
    job = {"cancelled": False, "process": None}
    monkeypatch.setattr(server, "JOBS", {identity: job})

    def failed_start(*args, **kwargs):
        raise OSError("Extractor could not start")

    monkeypatch.setattr(server.subprocess, "Popen", failed_start)
    server.run_job(identity)
    meta = server.metadata(folder)
    assert meta["state"] == "ERROR"
    assert "Extractor could not start" in meta["error"]
    assert job["process"] is None


def test_starting_on_an_occupied_port_does_not_interrupt_active_imports(tmp_path, monkeypatch):
    from workspace import server

    monkeypatch.setattr(server, "DATA", tmp_path)
    folder = tmp_path / str(uuid.uuid4())
    folder.mkdir()
    server.atomic(folder / "document.json", {"state": "PROCESSING"})
    monkeypatch.setattr(sys, "argv", ["workspace.server"])

    def occupied(*args):
        raise OSError("Port already in use")

    monkeypatch.setattr(server, "WorkspaceHTTPServer", occupied)
    with pytest.raises(SystemExit):
        server.main()
    assert server.metadata(folder)["state"] == "PROCESSING"


def test_home_workspace_assets_and_api_share_one_server(tmp_path, monkeypatch):
    from workspace import server

    monkeypatch.setattr(server, "DATA", tmp_path)
    http = server.WorkspaceHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{http.server_port}"
    try:
        for route, expected in [
            ("/", b"Drawings, made answerable."),
            ("/workspace", b"Building workspace"),
            ("/workspace/", b"Building workspace"),
            ("/workspace/workspace.js", b"class Workspace"),
            ("/workspace/ifc.worker.js", b""),
            ("/workspace/vendor/web-ifc/web-ifc.wasm", b"\x00asm"),
            ("/assets/cad-plan.svg", b"<svg"),
            ("/api/documents", b"[]"),
        ]:
            with urllib.request.urlopen(base + route) as response:
                assert response.status == 200
                assert expected in response.read()
        for route in [
            "/.env",
            "/workspace/package.json",
            "/workspace/node_modules/web-ifc/package.json",
            "/workspace/scripts/start.mjs",
            "/workspace/../workspace/server.py",
            "/assets/%2e%2e/BRIEF.md",
            "/workspace/vendor/%2e%2e/%2e%2e/.env",
            "/workspace/vendor/",
            "/lab/",
        ]:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + route)
            assert error.value.code == 404
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


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
