"""Web API for the pipe analysis, on the stoa-pipes Modal app (FastAPI behind @modal.asgi_app).

  POST /analyze   multipart `file` (.png .jpg .jpeg .dxf)
                  -> {sheet_id, cached, kind, summary, warnings, pipe_json_url, overlay_url, page_url}
  POST /ask       JSON {"sheet_id", "question"}
                  -> {answer, cited_ids, ungrounded_ids, reliable, tools, overlay_png_base64, overlay_url, cost_usd, ...}
  GET  /sheets/{sheet_id}/pipe.json | page.png | overlay.png     GET /sheets/{sheet_id}/ask/{ask_id}.png
  GET  /health    (no key)

Every route except /health needs the header `X-API-Key` (Modal Secret `stoa-pipes-api`, key STOA_API_KEY). /ask
needs GEMINI_API_KEY (Modal Secret `GEMINI_API_KEY`, variable GEMINI_API_KEY). Results are cached in the volume stoa-pipes-data under
/data/web/<sheet_id>/, sheet_id = the first 16 hex characters of the upload's SHA-256: one file is analysed once.

Deploy from the repository root (scales to zero when idle). The scan route's OCR is the live stoa-translator app's
Ocr class (run / run_rotated), looked up by name; deploy it first only if `modal app list` does not show it:
  python -m modal deploy -m translator.modal_app      # app stoa-translator
  python -m modal deploy -m pipes.web_api             # app stoa-pipes: this API + the detector functions
Environment flags, read at deploy time:
  STOA_PIPES_MIN_CONTAINERS=1   keep one web container warm (default 0)
  STOA_CORS_ORIGINS=...         comma-separated browser origins (default: http://localhost:3000,http://localhost:5173)

The pipeline is unchanged: the scan route's three GPU calls (pipes.scan.fetch_detections / fetch_upscale /
fetch_ocr, stubbed the same way in the tests) are pointed at the deployed functions, because their `with app.run()`
only works from a laptop.
"""

import os
from pathlib import Path, PurePosixPath

import modal

from pipes.modal_pipes import app, detect, upscale, vol

MIN_CONTAINERS = int(os.environ.get("STOA_PIPES_MIN_CONTAINERS", "0"))
CORS_ORIGINS = os.environ.get("STOA_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
OCR_APP = "stoa-translator"  # translator/modal_app.py APP_NAME
WEB_ROOT = Path(os.environ.get("STOA_WEB_ROOT", "/data/web"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
KINDS = {".png": "image", ".jpg": "image", ".jpeg": "image", ".dxf": "dxf"}
SKIP_DIRS = {"data_pipes", "index_pipes", "tests", "__pycache__"}


def _ignore(p) -> bool:
    """Ship .py files and the schema JSON files only (add_local_python_source's default drops the schemas)."""
    p = Path(p)
    if SKIP_DIRS & set(p.parts):
        return True
    if p.is_dir():
        return False
    return not (p.suffix == ".py" or (p.suffix == ".json" and p.parent.name == "schema"))


# ---------------------------------------------------------------- the website (optional)
# When the site folder is present at deploy time, the same app also serves the website at /: the site's own Node
# server (scripts/start.mjs, unchanged) runs inside the container for the building uploads and the building chat
# (/api/convert, /api/chat, blueprint-ingest + the raster translator + answering_agent) and for the static pages;
# FastAPI forwards those paths to it. The pipe routes above stay as they are.
REPO_LOCAL = Path(__file__).resolve().parent.parent
SITE_REL = "scrollcraft/builds/stoa-plan-to-evidence"
SERVE_SITE = (REPO_LOCAL / SITE_REL / "scripts" / "start.mjs").exists()
REPO = PurePosixPath("/repo")  # the repository subset inside the container (a Linux path)
NODE_PORT = 3000
MAX_SITE_UPLOAD_BYTES = 200 * 1024 * 1024  # IFC models can be large
NODE_SKIP = {"node_modules", "lab", "vendor", "out", "__pycache__", ".git", "test", "tests"}


def _ignore_node(p) -> bool:
    return bool(NODE_SKIP & set(Path(p).parts))


web_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "fonts-dejavu-core")
    .pip_install(
        "fastapi[standard]>=0.115",
        "numpy",
        "opencv-python-headless",
        "pillow",
        "scikit-image",
        "sknw",
        "networkx",
        "scipy",
        "shapely",
        "jsonschema",
        "ezdxf",
        "matplotlib",
        "python-dotenv",
        "google-genai",
        "pymupdf",  # the building raster route (translator.upload, router)
    )
)
if SERVE_SITE:
    web_image = web_image.apt_install("nodejs", "npm")
    for rel in (SITE_REL, "blueprint-ingest", "blueprint-viewer", "translator", "answering_agent"):
        web_image = web_image.add_local_dir(REPO_LOCAL / rel, str(REPO / rel), copy=True, ignore=_ignore_node)
    for rel in ("package.json", "package-lock.json"):
        web_image = web_image.add_local_file(REPO_LOCAL / rel, str(REPO / rel), copy=True)
    web_image = web_image.run_commands(
        f"cd {REPO} && npm ci --omit=dev --no-audit --no-fund",
        f"cd {REPO}/blueprint-ingest && npm ci --omit=dev --no-audit --no-fund",
        f"cd {REPO}/blueprint-viewer && npm ci --no-audit --no-fund",  # postinstall vendors web-ifc + its wasm
        f"cd {REPO}/{SITE_REL} && node scripts/build-cad.mjs && node scripts/sync-viewer.mjs",  # = npm run build
        f"node --check {REPO}/{SITE_REL}/workspace/workspace.js && node --check {REPO}/{SITE_REL}/scripts/start.mjs",
    )
    # the raster translator's hold-out guard (translator.vlm.base.default_guard) needs the list of names: only names
    holdout = "eval/holdout_files.txt"
    web_image = web_image.add_local_file(REPO_LOCAL / holdout, str(REPO / holdout), copy=True)
web_image = web_image.env({"STOA_CORS_ORIGINS": CORS_ORIGINS}).add_local_python_source(
    "pipes", "translator", ignore=_ignore, copy=True
)


# ---------------------------------------------------------------- the scan route's GPU calls, inside Modal


def _wire_gpu() -> None:
    """Same calls and arguments as pipes.scan.fetch_*, without `with app.run()` (we already run inside the app)."""
    import cv2
    import numpy as np

    from pipes import config as cfg
    from pipes import scan

    def fetch_detections(sid: str, png: bytes, run: str = cfg.DETECTOR_RUN) -> dict:
        res = detect.remote({sid: png}, 0.05, run, 1280, 640)
        d = res["sheets"][sid]
        d["model"] = f"yolo11s single-class symbol, run {run}"
        d["cost_usd"] = res["cost_usd"]
        return d

    def fetch_upscale(png: bytes, factor: int = 3):
        out = upscale.remote(png, factor)
        return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)

    def fetch_ocr(sid: str, png: bytes) -> list[dict]:
        ocr = modal.Cls.from_name(OCR_APP, "Ocr")()
        a = ocr.run.remote(png, sid)
        r = ocr.run_rotated.remote(png, sid)
        return [*a["orientations"]["original"], *r["items"]]

    scan.fetch_detections, scan.fetch_upscale, scan.fetch_ocr = fetch_detections, fetch_upscale, fetch_ocr


def _sync(action: str) -> None:
    """Volume reload / commit inside Modal; nothing locally (tests)."""
    if modal.is_local():
        return
    if action == "reload":
        vol.reload()
    else:
        vol.commit()


# ---------------------------------------------------------------- the FastAPI app


def _start_node(site_dir: Path) -> str:
    """Start the site's own server (scripts/start.mjs) inside this container; -> its base URL once it answers."""
    import subprocess
    import sys
    import time
    import urllib.request

    env = {**os.environ, "PORT": str(NODE_PORT), "PYTHON": sys.executable}
    subprocess.Popen(["node", "scripts/start.mjs"], cwd=site_dir, env=env)
    url = f"http://127.0.0.1:{NODE_PORT}"
    for _ in range(120):
        try:
            urllib.request.urlopen(f"{url}/", timeout=1)
            return url
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("the site server (scripts/start.mjs) did not start within 30 s")


def create_app(wire_gpu: bool = True, site_dir: Path | None = None, node_url: str | None = None):
    """The API; with site_dir (in Modal) or node_url (tests) also the website, forwarded to the site's own server."""
    import base64
    import hashlib
    import hmac
    import json
    import re
    import shutil
    import tempfile
    import threading
    from datetime import datetime, timezone
    from typing import Annotated

    from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, Response
    from pydantic import BaseModel, Field

    from pipes import scan
    from pipes.eval.overlay import draw
    from pipes.routing import pipe_index_path

    if wire_gpu:
        _wire_gpu()
    ids = re.compile(r"\b(?:node|pipe)_\d+\b")  # as pipes/eval/agent_questions.py
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    api = FastAPI(title="Stoa pipe analysis", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in os.environ.get("STOA_CORS_ORIGINS", "").split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    def key_ok(value: str | None) -> bool:
        expected = os.environ.get("STOA_API_KEY", "")
        return bool(expected and value and hmac.compare_digest(value.encode(), expected.encode()))

    def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if not key_ok(x_api_key):
            raise HTTPException(401, "missing or wrong X-API-Key")

    def sheet_dir(sheet_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", sheet_id):
            raise HTTPException(400, "sheet_id must be 16 lowercase hex characters")
        return WEB_ROOT / sheet_id

    def urls(sheet_id: str) -> dict:
        base = f"/sheets/{sheet_id}"
        return {
            "pipe_json_url": f"{base}/pipe.json",
            "overlay_url": f"{base}/overlay.png",
            "page_url": f"{base}/page.png",
        }

    @api.get("/health")
    def health() -> dict:
        return {"ok": True}

    @api.post("/analyze", dependencies=[Depends(require_key)])
    def analyze(file: Annotated[UploadFile, File()]) -> dict:
        name = Path(file.filename or "upload").name
        suffix = Path(name).suffix.lower()
        if suffix not in KINDS:
            raise HTTPException(415, f"unsupported file type {suffix or '(none)'}: use {', '.join(KINDS)}")
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if not data:
            raise HTTPException(400, "empty file")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        sheet_id = hashlib.sha256(data).hexdigest()[:16]
        d = sheet_dir(sheet_id)
        with locks_guard:
            lock = locks.setdefault(sheet_id, threading.Lock())
        with lock:
            _sync("reload")
            if (d / "meta.json").exists():
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                return {**meta, "cached": True, **urls(sheet_id)}
            kind = KINDS[suffix]
            source_file = f"web/{sheet_id}{suffix}"
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / f"{sheet_id}{suffix}"
                src.write_bytes(data)
                target = pipe_index_path(source_file, 0)
                page = target.with_name(target.name.removesuffix(".pipe.json") + ".png")
                try:
                    if kind == "dxf":
                        from pipes.extract.dxf_pipes import translate_dxf

                        doc, warnings = translate_dxf(src, source_file, publish=True, render=True), []
                        overlay = draw(page, doc, Path(tmp) / "overlay.png")
                    else:
                        r = scan.translate_scan(src, source_file, publish=True)
                        doc, warnings, overlay = r["doc"], r["warnings"], Path(r["overlay"])
                except HTTPException:
                    raise
                except Exception as e:  # unreadable file, OCR app not deployed, ...: say what failed
                    raise HTTPException(422, f"analysis failed: {type(e).__name__}: {e}") from e
                d.mkdir(parents=True, exist_ok=True)
                (d / "pipe.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
                shutil.copyfile(page, d / "page.png")
                shutil.copyfile(overlay, d / "overlay.png")
            meta = {
                "sheet_id": sheet_id,
                "file_name": name,
                "kind": kind,
                "source_file": source_file,
                "summary": scan.summary(doc),
                "warnings": warnings,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            _sync("commit")
        return {**meta, "cached": False, **urls(sheet_id)}

    class AskBody(BaseModel):
        sheet_id: str = Field(pattern=r"^[0-9a-f]{16}$")
        question: str = Field(min_length=1, max_length=2000)

    @api.post("/ask", dependencies=[Depends(require_key)])
    def ask(body: AskBody) -> dict:
        from pipes.agent import answer_question
        from pipes.cost import cost_usd
        from pipes.demo import render

        d = sheet_dir(body.sheet_id)
        _sync("reload")
        if not (d / "meta.json").exists():
            raise HTTPException(404, "unknown sheet_id: POST the file to /analyze first")
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        # the agent's router looks for the index (and its page image) where the routes publish them
        target = pipe_index_path(meta["source_file"], 0)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(d / "pipe.json", target)
        shutil.copyfile(d / "page.png", target.with_name(target.name.removesuffix(".pipe.json") + ".png"))
        try:
            r = answer_question(body.question, meta["source_file"])
        except RuntimeError as e:  # e.g. no GEMINI_API_KEY
            raise HTTPException(503, str(e)) from e
        calls = r["tool_calls"]
        known = {i for c in calls for k in ("node_ids", "edge_ids") for i in (c["result"].get(k) or [])}
        cited = sorted(set(ids.findall(r["answer"])), key=lambda s: (s.split("_")[0], int(s.split("_")[1])))

        def cites(c: dict) -> bool:
            res = c["result"]
            return bool(set(cited) & (set(res.get("node_ids") or []) | set(res.get("edge_ids") or [])))

        # the overlay shows the last tool call the answer cites from (else the last call)
        pick = next((c for c in reversed(calls) if cites(c)), calls[-1] if calls else None)
        ask_id = hashlib.sha256(body.question.encode("utf-8")).hexdigest()[:12]
        png_b64, overlay_url = None, None
        if pick:
            out = d / "ask" / f"{ask_id}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            doc = json.loads((d / "pipe.json").read_text(encoding="utf-8"))
            render(pick["result"], d / "page.png", out, context=doc)
            png_b64 = base64.b64encode(out.read_bytes()).decode("ascii")
            overlay_url = f"/sheets/{body.sheet_id}/ask/{ask_id}.png"
            _sync("commit")
        return {
            "sheet_id": body.sheet_id,
            "question": body.question,
            "answer": r["answer"],
            "cited_ids": [i for i in cited if i in known],
            "ungrounded_ids": [i for i in cited if i not in known],
            "reliable": all(c["result"].get("reliable", False) for c in calls) if calls else None,
            "tools": [{"tool": c["tool"], "args": c["args"]} for c in calls],
            "overlay_tool": pick["tool"] if pick else None,
            "overlay_png_base64": png_b64,
            "overlay_url": overlay_url,
            "model": r["model"],
            "usage": r["usage"],
            "cost_usd": cost_usd(r["usage"], r["model"]),
        }

    def send(path: Path, media: str):
        _sync("reload")
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type=media)

    @api.get("/sheets/{sheet_id}/pipe.json", dependencies=[Depends(require_key)])
    def get_pipe_json(sheet_id: str):
        return send(sheet_dir(sheet_id) / "pipe.json", "application/json")

    @api.get("/sheets/{sheet_id}/{image}.png", dependencies=[Depends(require_key)])
    def get_image(sheet_id: str, image: str):
        if image not in ("page", "overlay"):
            raise HTTPException(404, "not found")
        return send(sheet_dir(sheet_id) / f"{image}.png", "image/png")

    @api.get("/sheets/{sheet_id}/ask/{ask_id}.png", dependencies=[Depends(require_key)])
    def get_ask_overlay(sheet_id: str, ask_id: str):
        if not re.fullmatch(r"[0-9a-f]{12}", ask_id):
            raise HTTPException(404, "not found")
        return send(sheet_dir(sheet_id) / "ask" / f"{ask_id}.png", "image/png")

    # ------------------------------------------------------------ the website (forwarded to scripts/start.mjs)
    if site_dir is not None:
        node_url = _start_node(site_dir)
    if node_url is None:
        return api
    import httpx

    @api.get("/workspace/config.js")
    def site_config() -> Response:
        """The page's settings: same-origin API (base ''), and the key. Readable by anyone who opens the site."""
        cfg = {"apiBase": "", "apiKey": os.environ.get("STOA_API_KEY", "")}
        body = f"window.STOA_CONFIG = {json.dumps(cfg)};\n"
        return Response(body, media_type="text/javascript", headers={"cache-control": "no-store"})

    keep = {"content-type", "cache-control", "content-length", "content-disposition"}

    @api.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "DELETE"])
    async def to_site(path: str, request: Request) -> Response:
        if path.startswith("api/") and not key_ok(request.headers.get("x-api-key")):
            raise HTTPException(401, "missing or wrong X-API-Key")
        size = int(request.headers.get("content-length") or 0)
        if size > MAX_SITE_UPLOAD_BYTES:
            raise HTTPException(413, f"file larger than {MAX_SITE_UPLOAD_BYTES // (1024 * 1024)} MB")
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept")}
        async with httpx.AsyncClient(timeout=900) as client:
            r = await client.request(
                request.method,
                f"{node_url}/{path}",
                params=request.query_params,
                content=body,
                headers=headers,
            )
        out = {k: v for k, v in r.headers.items() if k.lower() in keep and k.lower() != "content-length"}
        return Response(r.content, status_code=r.status_code, headers=out)

    return api


@app.function(
    image=web_image,
    volumes={"/data": vol},
    secrets=[
        modal.Secret.from_name("stoa-pipes-api", required_keys=["STOA_API_KEY"]),
        modal.Secret.from_name("GEMINI_API_KEY", required_keys=["GEMINI_API_KEY"]),
    ],
    min_containers=MIN_CONTAINERS,
    # the site's server keeps its converted files in memory and in out/: one container, so chats find them
    max_containers=1 if SERVE_SITE else None,
    scaledown_window=300,
    timeout=900,
    cpu=2.0,
    memory=4096,
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app(label="stoa-pipes-api")
def web():
    site = Path(str(REPO / SITE_REL))  # runs inside the container
    return create_app(site_dir=site if (site / "scripts" / "start.mjs").exists() else None)
