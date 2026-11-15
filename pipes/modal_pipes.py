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


@app.function(image=image, gpu="T4", volumes={"/data": vol}, timeout=2 * 3600, scaledown_window=60)
def train(
    epochs: int = 40,
    model: str = "yolo11s.pt",
    imgsz: int = 640,
    batch: int = 16,
    data: str = "yolo_dev",
    name: str = "dev",
    patience: int = 15,
) -> dict:
    """Train on /data/<data> (tiles + pipes.yaml). Symbols only; no pipe-box classes. Weights: runs/<name>."""
    import time

    from ultralytics import YOLO

    t0 = time.time()
    m = YOLO(model)
    m.train(
        data=f"/data/{data}/pipes.yaml", imgsz=imgsz, epochs=epochs, batch=batch, project="/data/runs",
        name=name, exist_ok=True, patience=patience, flipud=0.5, fliplr=0.5, degrees=0, mosaic=1.0, hsv_v=0.4,
        workers=2, plots=False,
    )  # fmt: skip
    vol.commit()
    secs = time.time() - t0
    return {"seconds": secs, "cost_usd": secs * T4_USD_PER_S, "weights": f"/data/runs/{name}/weights/best.pt"}


@app.function(image=image, gpu="T4", volumes={"/data": vol}, timeout=1800, scaledown_window=60)
def detect(
    sheets: dict[str, bytes],
    conf: float = 0.25,
    run: str = "dev",
    whole_imgsz: int = 1280,
    tile_imgsz: int = 640,
) -> dict:
    """Two passes per sheet, raw boxes in ORIGINAL page pixels (no merging): 640 px tiles (pass=tile) and the
    whole sheet at imgsz=whole_imgsz (pass=whole, 0 = skip), which sees big equipment the tiles cut."""
    import io
    import time

    from PIL import Image
    from ultralytics import YOLO

    from pipes.raster.tiles import TILE, origins

    t0 = time.time()
    model = YOLO(f"/data/runs/{run}/weights/best.pt")
    out = {}
    for sid, data in sheets.items():
        im = Image.open(io.BytesIO(data)).convert("RGB")
        W, H = im.size
        w, h = min(TILE, W), min(TILE, H)
        dets = []
        for y0 in origins(H):
            for x0 in origins(W):
                tile = im.crop((x0, y0, x0 + w, y0 + h))
                r = model.predict(tile, imgsz=tile_imgsz, conf=conf, verbose=False)[0]
                for (a, b, c, d), s in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), strict=True):
                    dets.append(
                        {
                            "bbox_px": [a + x0, b + y0, c + x0, d + y0],
                            "score": s,
                            "tile": [x0, y0],
                            "pass": "tile",
                        }
                    )
        if whole_imgsz:
            r = model.predict(im, imgsz=whole_imgsz, conf=conf, verbose=False)[0]
            for (a, b, c, d), s in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), strict=True):
                dets.append({"bbox_px": [a, b, c, d], "score": s, "tile": None, "pass": "whole"})
        out[sid] = {"width": W, "height": H, "dets": dets, "whole_imgsz": whole_imgsz}
    secs = time.time() - t0
    return {"sheets": out, "seconds": secs, "cost_usd": secs * T4_USD_PER_S}


@app.local_entrypoint()
def train_main(
    epochs: int = 40,
    model: str = "yolo11s.pt",
    data: str = "yolo_dev",
    name: str = "dev",
    patience: int = 15,
    imgsz: int = 640,
    batch: int = 16,
):
    import json

    print(
        json.dumps(
            train.remote(
                epochs=epochs, model=model, data=data, name=name, patience=patience, imgsz=imgsz, batch=batch
            )
        )
    )


@app.local_entrypoint()
def detect_main(
    root: str = "data_pipes/ds/test",
    conf: float = 0.05,
    run: str = "dev",
    whole_imgsz: int = 1280,
    tile_imgsz: int = 640,
):
    """Detect on every <root>/<id>/sheet.jpg (root relative to pipes/); raw -> index_pipes/_raw/<set>_<run>/."""
    import json
    from pathlib import Path

    here = Path(__file__).parent
    base = here / root
    sheets = {p.name: (p / "sheet.jpg").read_bytes() for p in sorted(base.iterdir()) if p.is_dir()}
    res = detect.remote(sheets, conf, run, whole_imgsz, tile_imgsz)
    raw = here / "index_pipes" / "_raw" / f"{base.parent.name}_{base.name}_{run}"
    raw.mkdir(parents=True, exist_ok=True)
    for sid, d in res["sheets"].items():
        d["model"] = f"yolo11s single-class symbol, run {run}"
        d["cost_usd"] = res["cost_usd"] / len(sheets)
        (raw / f"{sid}.det.json").write_text(json.dumps(d), encoding="utf-8")
    print(f"{len(sheets)} sheets, {res['seconds']:.1f} s GPU, ${res['cost_usd']:.4f}; raw in {raw}")


# ---------------------------------------------------------------- quality gate: Real-ESRGAN x3 upscale (scan route)

esrgan_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "wget")
    .pip_install(
        "torch==2.0.1",
        "torchvision==0.15.2",
        "numpy<2",
        "basicsr==1.4.2",
        "realesrgan==0.3.0",
        "opencv-python-headless",
    )  # fmt: skip
    .run_commands(
        "wget -q -O /RealESRGAN_x4plus.pth "
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
    )
)


@app.function(image=esrgan_image, gpu="T4", timeout=900, scaledown_window=60)
def upscale(png: bytes, factor: int = 3) -> bytes:
    """Real-ESRGAN x4plus, output resized to `factor` x the input. Used for detection/OCR only: the caller maps
    every box back to the original page pixels."""
    import cv2
    import numpy as np
    import torch
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer

    net = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
    up = RealESRGANer(scale=4, model_path="/RealESRGAN_x4plus.pth", model=net, tile=400, tile_pad=10, pre_pad=0,
                      half=torch.cuda.is_available())  # fmt: skip
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    out, _ = up.enhance(img, outscale=factor)
    ok, buf = cv2.imencode(".png", out)
    return buf.tobytes()
