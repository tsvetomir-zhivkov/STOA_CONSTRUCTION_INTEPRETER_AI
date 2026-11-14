"""Loopback HTTP service: original documents, cancellable extraction, validated facts and AI.

Run: python -m workspace.server --port 4600
Uses the existing Python/Node pipelines; no new runtime dependencies.
"""
import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from translator.store import validate

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "blueprint-viewer"
DATA = ROOT / ".workspace-data"
LOCK = threading.RLock()
JOBS = {}
MAX_UPLOAD = 256 * 1024 * 1024


def atomic(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def directory(identity):
    if str(uuid.UUID(identity)) != identity:
        raise ValueError("Invalid document identifier")
    return DATA / identity


def metadata(folder):
    return json.loads((folder / "document.json").read_text(encoding="utf-8"))


def run_job(identity):
    folder = directory(identity)
    with LOCK:
        job = JOBS.get(identity)
        if not job or job["cancelled"]:
            return
        meta = metadata(folder)
        meta.update(state="PROCESSING", error=None)
        atomic(folder / "document.json", meta)
        process = subprocess.Popen(
            [sys.executable, "-m", "workspace.extract", str(folder / meta["stored_name"]), str(folder / "buildings")],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        job["process"] = process
    stdout, stderr = process.communicate()
    with LOCK:
        if job["cancelled"] or not folder.exists():
            return
        meta = metadata(folder)
        if process.returncode:
            meta.update(state="ERROR", error=(stderr or stdout)[-4000:])
        else:
            meta.update(state="READY", buildings=json.loads(stdout.strip().splitlines()[-1]))
        atomic(folder / "document.json", meta)
        job["process"] = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER), **kwargs)

    def reply(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

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
                    docs = [metadata(p) for p in DATA.iterdir() if p.is_dir() and (p / "document.json").exists()]
                return self.reply(docs)
            parts = path.strip("/").split("/")
            folder = directory(parts[2])
            meta = metadata(folder)
            if len(parts) == 3:
                return self.reply(meta)
            if parts[3] == "buildings":
                docs = [json.loads((folder / "buildings" / name).read_text(encoding="utf-8")) for name in meta.get("buildings", [])]
                for document in docs:
                    validate(document)
                return self.reply(docs)
            if parts[3] == "source-info":
                return self.reply(json.loads((folder / "preview.json").read_text(encoding="utf-8")))
            if parts[3] in ("source", "preview"):
                file = folder / ("preview.svg" if parts[3] == "preview" else meta["stored_name"])
                if not file.exists():
                    return self.reply({"error": "Preview not generated"}, 404)
                data = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:")
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
                meta = {"id": identity, "name": name, "stored_name": stored, "type": suffix[1:], "size": len(payload), "state": "UPLOADING", "buildings": [], "error": None}
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
                document = json.loads((folder / "buildings" / meta["buildings"][index]).read_text(encoding="utf-8"))
                validate(document)
                from answering_agent.agent import answer_question
                answer = answer_question(str(body["question"]), document)
                return self.reply({"answer": answer, "building_id": document["building_id"]})
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
                        job["process"].terminate()
                        job["process"].wait(timeout=10)
                # folder is a validated UUID child of DATA, never a user path.
                shutil.rmtree(folder)
                JOBS.pop(identity, None)
            return self.reply({"removed": identity})
        except (ValueError, FileNotFoundError, IndexError) as error:
            return self.reply({"error": str(error)}, 404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4600)
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    for folder in DATA.iterdir():
        if folder.is_dir() and (folder / "document.json").exists():
            meta = metadata(folder)
            if meta["state"] in ("UPLOADING", "PROCESSING"):
                meta.update(state="ERROR", error="Processing was interrupted by server shutdown; upload again to retry")
                atomic(folder / "document.json", meta)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Stoa workspace: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for job in JOBS.values():
            if job["process"] and job["process"].poll() is None:
                job["process"].terminate()


if __name__ == "__main__":
    main()
