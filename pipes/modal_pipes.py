"""All GPU / Modal code for pipes (Modal app stoa-pipes).

Dev run (single-class symbol detector, 640 px tiles, data uploaded once with modal volume put):
  python -m modal volume put stoa-pipes-data pipes/data_pipes/dev/yolo /yolo_dev --force
  python -m modal run -m pipes.modal_pipes::train_main --epochs 40
  python -m modal run -m pipes.modal_pipes::detect_main --split test
The dataset is NOT downloaded on Modal: it was fetched by hand (0__raw_data only is used).
"""

from __future__ import annotations

import modal

APP_NAME = "stoa-pipes"
app = modal.App(APP_NAME)
vol = modal.Volume.from_name("stoa-pipes-data", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "wget", "unzip")
    .pip_install("ultralytics==8.3.*", "sahi", "opencv-python-headless")
    .add_local_python_source("pipes")
)

T4_USD_PER_S = 0.000164  # Modal list price for a T4, cost estimate only
RUN = "/data/runs/dev"


@app.function(image=image, gpu="T4", volumes={"/data": vol}, timeout=2 * 3600, scaledown_window=60)
def train(epochs: int = 40, model: str = "yolo11s.pt", imgsz: int = 640, batch: int = 16) -> dict:
    """Train on /data/yolo_dev (tiles + pipes.yaml). Symbols only; no pipe-box classes."""
    import time

    from ultralytics import YOLO

    t0 = time.time()
    m = YOLO(model)
    m.train(
        data="/data/yolo_dev/pipes.yaml", imgsz=imgsz, epochs=epochs, batch=batch, project="/data/runs",
        name="dev", exist_ok=True, patience=15, flipud=0.5, fliplr=0.5, degrees=0, mosaic=1.0, hsv_v=0.4,
        workers=2, plots=False,
    )  # fmt: skip
    vol.commit()
    secs = time.time() - t0
    return {"seconds": secs, "cost_usd": secs * T4_USD_PER_S, "weights": f"{RUN}/weights/best.pt"}


@app.function(image=image, gpu="T4", volumes={"/data": vol}, timeout=1800, scaledown_window=60)
def detect(sheets: dict[str, bytes], conf: float = 0.25) -> dict:
    """Tiled inference on whole sheets. Returns raw per-tile boxes in ORIGINAL page pixels (no merging)."""
    import io
    import time

    from PIL import Image
    from ultralytics import YOLO

    from pipes.raster.tiles import TILE, origins

    t0 = time.time()
    model = YOLO(f"{RUN}/weights/best.pt")
    out = {}
    for sid, data in sheets.items():
        im = Image.open(io.BytesIO(data)).convert("RGB")
        W, H = im.size
        w, h = min(TILE, W), min(TILE, H)
        dets = []
        for y0 in origins(H):
            for x0 in origins(W):
                tile = im.crop((x0, y0, x0 + w, y0 + h))
                r = model.predict(tile, imgsz=TILE, conf=conf, verbose=False)[0]
                for (a, b, c, d), s in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), strict=True):
                    dets.append({"bbox_px": [a + x0, b + y0, c + x0, d + y0], "score": s, "tile": [x0, y0]})
        out[sid] = {"width": W, "height": H, "dets": dets}
    secs = time.time() - t0
    return {"sheets": out, "seconds": secs, "cost_usd": secs * T4_USD_PER_S}


@app.local_entrypoint()
def train_main(epochs: int = 40, model: str = "yolo11s.pt"):
    import json

    print(json.dumps(train.remote(epochs=epochs, model=model)))


@app.local_entrypoint()
def detect_main(split: str = "test", conf: float = 0.25):
    """Detect on data_pipes/dev/<split>; raw tile detections -> index_pipes/_raw/dev_<split>/<id>.det.json."""
    import json
    from pathlib import Path

    here = Path(__file__).parent
    sheets = {
        p.name: (p / "sheet.jpg").read_bytes()
        for p in sorted((here / "data_pipes/dev" / split).iterdir())
        if p.is_dir()
    }
    res = detect.remote(sheets, conf)
    raw = here / "index_pipes" / "_raw" / f"dev_{split}"
    raw.mkdir(parents=True, exist_ok=True)
    for sid, d in res["sheets"].items():
        d["model"] = "yolo11s single-class symbol, dev subset"
        (raw / f"{sid}.det.json").write_text(json.dumps(d), encoding="utf-8")
    print(f"{len(sheets)} sheets, {res['seconds']:.1f} s GPU, ${res['cost_usd']:.4f}; raw in {raw}")
