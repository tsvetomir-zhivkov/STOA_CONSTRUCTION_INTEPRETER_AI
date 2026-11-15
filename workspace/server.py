"""Loopback HTTP service: original documents, cancellable extraction, validated facts and AI.

Run: python -m workspace.server --port 3000
Uses the existing Python/Node pipelines; no new runtime dependencies.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from translator.store import validate

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "blueprint-viewer"
LANDING = ROOT / "scrollcraft" / "builds" / "stoa-plan-to-evidence"
DATA = ROOT / ".workspace-data"
LOCK = threading.RLock()
JOBS = {}
MAX_UPLOAD = 256 * 1024 * 1024


class WorkspaceHTTPServer(ThreadingHTTPServer):
    # The landing and module-based viewer request many local assets at once.
    # The stdlib's five-connection backlog drops bursts from multiple tabs.
    request_queue_size = 128


def atomic(path, value):
    with LOCK:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        for attempt in range(6):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                # Windows readers/scanners can briefly deny replacement of an open file.
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))


def directory(identity):
    if str(uuid.UUID(identity)) != identity:
        raise ValueError("Invalid document identifier")
    return DATA / identity


def metadata(folder):
    with LOCK:
        return json.loads((folder / "document.json").read_text(encoding="utf-8"))


def own_process_tree(process):
    """Put Windows descendants in an owned Job Object (no taskkill privilege required)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
            if handle:
                kernel.CloseHandle(handle)
            process.terminate()
            raise ctypes.WinError(ctypes.get_last_error())
        process.workspace_job = handle
    return process


def stop_process(process):
    """Stop the owned extraction tree, including the Node parser child."""
    if process is None:
        return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        handle = getattr(process, "workspace_job", None)
        if handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.TerminateJobObject(handle, 1)
            kernel.CloseHandle(handle)
            process.workspace_job = None
        elif process.poll() is None:
            process.terminate()
    elif process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=10)


def run_job(identity):
    try:
        _run_job(identity)
    except Exception as error:
        # A failed worker must publish an error instead of leaving the UI polling forever.
        with LOCK:
            job = JOBS.get(identity)
            folder = directory(identity)
            if not job or job["cancelled"] or not folder.exists():
                return
            try:
                stop_process(job.get("process"))
            finally:
                job["process"] = None
                meta = metadata(folder)
                meta.update(state="ERROR", error=f"Extraction failed: {error}")
                atomic(folder / "document.json", meta)


def _run_job(identity):
    folder = directory(identity)
    with LOCK:
        job = JOBS.get(identity)
        if not job or job["cancelled"]:
            return
        meta = metadata(folder)
        meta.update(state="PROCESSING", error=None)
        atomic(folder / "document.json", meta)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "workspace.extract",
                str(folder / meta["stored_name"]),
                str(folder / "buildings"),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            start_new_session=os.name != "nt",
        )
        job["process"] = process
        own_process_tree(process)
    stdout, stderr = process.communicate()
    stop_process(process)
    with LOCK:
        if job["cancelled"] or not folder.exists():
            return
        meta = metadata(folder)
        if process.returncode:
            meta.update(state="ERROR", error=(stderr or stdout)[-4000:])
        else:
            try:
                meta.update(state="READY", error=None, buildings=json.loads(stdout.strip().splitlines()[-1]))
            except (ValueError, IndexError):
                meta.update(state="ERROR", error="Extractor did not return a valid document list")
        meta["has_preview"] = (folder / "preview.svg").exists() or (folder / "thumbnail.png").exists()
        docs = [json.loads(p.read_text(encoding="utf-8")) for p in (folder / "buildings").glob("*.json")]
        meta["building_names"] = [
            d.get("project", {}).get("building_name")
            for d in docs
            if d.get("project", {}).get("building_name")
        ]
        meta["floor_names"] = [
            level["name"] for d in docs for level in d.get("levels", []) if level.get("name")
        ]
        atomic(folder / "document.json", meta)
        job["process"] = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER), **kwargs)

    def send_head(self):
        """Serve the existing landing and viewer without exposing the repository."""
        path = unquote(urlparse(self.path).path)
        if path == "/workspace":
            self.send_response(308)
            self.send_header("Location", "/workspace/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        workspace = path.startswith("/workspace/")
        base = VIEWER if workspace else LANDING
        relative = path.removeprefix("/workspace/") if workspace else path.lstrip("/")
        relative = relative or "index.html"
        parts = relative.split("/")
        allowed = {"vendor"} if workspace else {"assets"}
        file = (base / relative).resolve()
        if (
            "\\" in relative
            or any(p.startswith(".") or p == "node_modules" for p in parts)
            or not file.is_relative_to(base.resolve())
            or (len(parts) > 1 and parts[0] not in allowed)
            or (len(parts) == 1 and file.suffix not in {".html", ".js", ".css", ".ico"})
            or not file.is_file()
        ):
            self.send_error(404, "Not found")
            return None
        stream = file.open("rb")
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(file)))
        self.send_header("Content-Length", str(file.stat().st_size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        return stream

    def log_message(self, format, *args):
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)

    def reply(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # Browser cancelled a document session.

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def body(self, limit=MAX_UPLOAD):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > limit:
            raise ValueError("Request exceeds upload limit")
        return self.rfile.read(length)

    def do_GET(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            # Only serve the application assets, never source data or server files.
            if "/node_modules/" in path or path.startswith("/."):
                return self.reply({"error": "Not found"}, 404)
            return super().do_GET()
        try:
            if path == "/api/documents":
                with LOCK:
                    docs = [
                        metadata(p) for p in DATA.iterdir() if p.is_dir() and (p / "document.json").exists()
                    ]
                return self.reply(docs)
            parts = path.strip("/").split("/")
            folder = directory(parts[2])
            meta = metadata(folder)
            if len(parts) == 3:
                return self.reply(meta)
            if parts[3] == "buildings":
                docs = [
                    json.loads((folder / "buildings" / name).read_text(encoding="utf-8"))
                    for name in meta.get("buildings", [])
                ]
                for document in docs:
                    validate(document)
                return self.reply(docs)
            if parts[3] == "source-info":
                return self.reply(json.loads((folder / "preview.json").read_text(encoding="utf-8")))
            if parts[3] == "analysis":
                file = folder / "analysis.json"
                return self.reply(json.loads(file.read_text(encoding="utf-8")) if file.exists() else [])
            if parts[3] in ("source", "preview", "thumbnail"):
                file = folder / (meta["stored_name"] if parts[3] == "source" else "preview.svg")
                if parts[3] == "thumbnail" and (folder / "thumbnail.png").exists():
                    file = folder / "thumbnail.png"
                if not file.exists():
                    return self.reply({"error": "Preview not generated"}, 404)
                data = file.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:",
                )
                self.end_headers()
                self.wfile.write(data)
                return
            return self.reply({"error": "Not found"}, 404)
        except (ValueError, FileNotFoundError, IndexError) as error:
            return self.reply({"error": str(error)}, 404)

    def do_POST(self):
        if not self.same_origin():
            return self.reply({"error": "Origin rejected"}, 403)
        try:
            path = urlparse(self.path).path
            if path == "/api/documents":
                name = Path(unquote(self.headers.get("X-Filename", "drawing"))).name
                suffix = Path(name).suffix.lower()
                if suffix not in (".ifc", ".json", ".dxf", ".svg", ".pdf", ".png", ".jpg", ".jpeg", ".webp"):
                    raise ValueError("Unsupported document format")
                payload = self.body()
                identity = str(uuid.uuid4())
                folder = directory(identity)
                folder.mkdir(parents=True)
                stored = "source" + suffix
                (folder / stored).write_bytes(payload)
                meta = {
                    "id": identity,
                    "name": name,
                    "stored_name": stored,
                    "type": suffix[1:],
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "state": "UPLOADING",
                    "buildings": [],
                    "error": None,
                }
                with LOCK:
                    atomic(folder / "document.json", meta)
                    JOBS[identity] = {"cancelled": False, "process": None}
                threading.Thread(target=run_job, args=(identity,), daemon=True).start()
                return self.reply(meta, 202)
            parts = path.strip("/").split("/")
            folder = directory(parts[2])
            if parts[3] == "analysis":
                body = json.loads(self.body(1024 * 1024))
                meta = metadata(folder)
                index = int(body.get("building", 0))
                document = json.loads(
                    (folder / "buildings" / meta["buildings"][index]).read_text(encoding="utf-8")
                )
                validate(document)
                from answering_agent.agent import answer_question

                answer = answer_question(str(body["question"]), document)
                result = {
                    "question": str(body["question"]),
                    "answer": answer,
                    "building_id": document["building_id"],
                }
                with LOCK:
                    if not folder.exists():
                        return self.reply({"error": "Document was removed"}, 409)
                    history_path = folder / "analysis.json"
                    history = (
                        json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
                    )
                    history.append(result)
                    atomic(history_path, history)
                return self.reply(result)
            return self.reply({"error": "Not found"}, 404)
        except Exception as error:
            return self.reply({"error": str(error)}, 400)

    def do_DELETE(self):
        if not self.same_origin():
            return self.reply({"error": "Origin rejected"}, 403)
        try:
            identity = urlparse(self.path).path.strip("/").split("/")[2]
            folder = directory(identity)
            with LOCK:
                job = JOBS.get(identity)
                if job:
                    job["cancelled"] = True
                    if job["process"] and job["process"].poll() is None:
                        stop_process(job["process"])
                # folder is a validated UUID child of DATA, never a user path.
                shutil.rmtree(folder)
                JOBS.pop(identity, None)
            return self.reply({"removed": identity})
        except (ValueError, FileNotFoundError, IndexError) as error:
            return self.reply({"error": str(error)}, 404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    try:
        server = WorkspaceHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        parser.exit(
            1,
            f"Could not start Stoa on port {args.port}: {error}\n"
            "Stop the server using this port, then run npm start again, "
            "or use npm start -- --port 4601.\n",
        )
    DATA.mkdir(exist_ok=True)
    for folder in DATA.iterdir():
        if folder.is_dir() and (folder / "document.json").exists():
            meta = metadata(folder)
            if meta["state"] in ("UPLOADING", "PROCESSING"):
                meta.update(
                    state="ERROR",
                    error="Processing was interrupted by server shutdown; upload again to retry",
                )
                atomic(folder / "document.json", meta)
    print(f"Stoa home: http://localhost:{args.port}/", flush=True)
    print(f"Building workspace: http://localhost:{args.port}/workspace/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for job in JOBS.values():
            if job["process"] and job["process"].poll() is None:
                stop_process(job["process"])


if __name__ == "__main__":
    main()
