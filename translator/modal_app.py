"""Steps 6 and 9: ALL GPU code (Modal app stoa-translator) lives here and nowhere else.

CubiCasa5K segmentation (hg_furukawa_original, CubiCasa5K repo pinned at CC5K_COMMIT), weights in the Modal
Volume cubicasa-weights at /model_best_val_loss_var.pkl (mounted at /w).

Run (the modal command is not on PATH here, so through python -m; the app has two entrypoints):
  python -m modal run -m translator.modal_app::main --limit 1      # segmentation smoke test, one dev image
  python -m modal run -m translator.modal_app::main                # every dev image without a raw seg file
  python -m modal run -m translator.modal_app::ocr_main --limit 1  # step 7 OCR (PaddleOCR, Latin model)
  python -m modal run -m translator.modal_app::ocr_main --names colorful-7513,high_quality-5815

Only images NOT in eval/holdout_files.txt are ever read or sent (translator.extract.cubicasa.dev_images).
Raw results go to index/_raw/cubicasa/<name>.seg.json; images that already have one are skipped.
Python 3.11: Modal's 2026.10 image builder no longer builds 3.10 or 3.9 (agreed with k). STOA_CC_TORCH=2.0
selects the fallback image (torch 2.0.1, the oldest torch with Python 3.11 wheels) if torch 2.2 cannot load
the weights.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import modal

APP_NAME = "stoa-translator"
CC5K_REPO = "https://github.com/CubiCasa/CubiCasa5k"
CC5K_COMMIT = "c34440266665a11f4484eb06cd2e4b7d72ad76c1"
WEIGHTS = "/w/model_best_val_loss_var.pkl"
N_OUT = 44  # 21 heatmaps + 12 room + 11 icon channels
MAX_SIDE = 2048  # longest side fed to the model; larger images are resized, recorded and mapped back
PAD_MULTIPLE = 64  # stride of the network (conv /2, pool /2, 4 more pools /16)
# (forward, back) turns, as in CubiCasa's own eval (floortrans/metrics.py)
ROTATIONS = [(0, 0), (1, -1), (2, 2), (-1, 1)]
T4_USD_PER_S = 0.000164  # Modal list price for a T4, for the cost estimate only

TORCH_VARIANTS = {
    "2.2": {
        "python": "3.11",
        "pip": ["torch==2.2.2", "torchvision==0.17.2", "numpy==1.26.4", "opencv-python-headless==4.10.0.84",
                "scipy==1.13.1", "shapely==2.0.6"],
    },
    "2.0": {
        "python": "3.11",
        "pip": ["torch==2.0.1", "torchvision==0.15.2", "numpy==1.24.4", "opencv-python-headless==4.8.1.78",
                "scipy==1.10.1", "shapely==2.0.1"],
    },
}  # fmt: skip
VARIANT = os.environ.get("STOA_CC_TORCH", "2.2")

app = modal.App(APP_NAME)
weights = modal.Volume.from_name("cubicasa-weights")
image = (
    modal.Image.debian_slim(python_version=TORCH_VARIANTS[VARIANT]["python"])
    .apt_install("git", "libgl1", "libglib2.0-0")
    .pip_install(*TORCH_VARIANTS[VARIANT]["pip"])
    .run_commands(f"git clone {CC5K_REPO} /cc5k && cd /cc5k && git checkout {CC5K_COMMIT}")
    .add_local_python_source("translator")
)


# Step 7 OCR image. PyPI has no Paddle 3 GPU wheel for Python 3.11 (only paddlepaddle-gpu 2.6.2), so the
# GPU wheel comes from Paddle's own index; PaddleOCR / PaddleX 3.0.3 are the matching release.
# paddleocr 3.0.3 pulls paddlex[ie,multimodal,ocr]; "ie" brings an unpinned langchain whose current release
# has no langchain.docstore, and PaddleX imports it at start-up. So: paddleocr without its dependencies,
# plus paddlex[ocr] only.
PADDLE_INDEX = "https://www.paddlepaddle.org.cn/packages/stable/cu118/"
OCR_PIP = ["paddleocr==3.0.3"]
OCR_DEPS = ["paddlex[ocr]==3.0.3", "PyYAML>=6", "typing-extensions>=4.12"]
# PaddleOCR 3.0.3 has no "fi" in its Latin language list (lang="fi" raises "No models are available"), so the
# models are named: the Latin-script recogniser (keeps ä/ö/å; never the English model) and the
# language-independent PP-OCRv5 server text detector.
OCR_KW = {
    "text_detection_model_name": "PP-OCRv5_server_det",
    "text_recognition_model_name": "latin_PP-OCRv3_mobile_rec",
    "use_doc_orientation_classify": False,
    "use_doc_unwarping": False,
    "use_textline_orientation": True,  # labels drawn upside down / rotated
}
ocr_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "libgomp1")
    .pip_install("paddlepaddle-gpu==3.0.0", index_url=PADDLE_INDEX)
    .pip_install(*OCR_PIP, extra_options="--no-deps")
    .pip_install(*OCR_DEPS)
    .run_commands("pip freeze > /pip-freeze.txt")
    .env({"PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True"})
    # download the models at build time, so containers start without network fetches
    .run_commands(f"python -c \"from paddleocr import PaddleOCR; PaddleOCR(device='cpu', **{OCR_KW!r})\"")
    .add_local_python_source("translator")
)


def _rot(t, n: int):
    """CubiCasa's RotateNTurns.rot_tensor on an NCHW tensor."""
    if n == 1:
        return t.flip(2).transpose(3, 2)
    if n == -1:
        return t.transpose(3, 2).flip(2)
    if n == 2:
        return t.flip(2).flip(3)
    return t


@app.cls(image=image, gpu="T4", volumes={"/w": weights}, scaledown_window=120, timeout=900)
class Seg:
    @modal.enter()
    def load(self):
        import sys

        import torch

        sys.path.insert(0, "/cc5k")
        from floortrans.models.hg_furukawa_original import hg_furukawa_original

        # get_model() would also load ImageNet-style init weights from a relative path; they are all
        # overwritten by the checkpoint, so build the network directly (as eval.py does after get_model).
        model = hg_furukawa_original(n_classes=51)
        model.conv4_ = torch.nn.Conv2d(256, N_OUT, bias=True, kernel_size=1)
        model.upsample = torch.nn.ConvTranspose2d(N_OUT, N_OUT, kernel_size=4, stride=4)
        checkpoint = torch.load(WEIGHTS, map_location="cpu")
        model.load_state_dict(checkpoint["model_state"])
        self.model = model.eval().cuda()
        self.info = {
            # plain str: torch.__version__ is a TorchVersion, which would need torch to unpickle locally
            "torch": str(torch.__version__),
            "cuda": str(torch.version.cuda),
            "gpu": str(torch.cuda.get_device_name(0)),
            "python": sys.version.split()[0],
            "cc5k_commit": CC5K_COMMIT,
            "weights": WEIGHTS,
            "variant": VARIANT,
        }
        print("CubiCasa5K weights loaded:", self.info)

    @modal.method()
    def run(self, png_bytes: bytes, name: str) -> dict:
        import cv2
        import numpy as np
        import torch
        import torch.nn.functional as F

        from translator.extract.cubicasa import SPLIT, raw_from_maps
        from translator.raster.preprocess import preprocess, to_bgr

        t0 = time.time()
        img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
        pre = preprocess(to_bgr(img, name), source=name)
        bgr = pre.normalized  # original pixel grid
        H, W = bgr.shape[:2]
        s = min(1.0, MAX_SIDE / max(H, W))
        inp = cv2.resize(bgr, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA) if s < 1 else bgr
        h, w = inp.shape[:2]
        rgb = np.ascontiguousarray(inp[:, :, ::-1])
        x = torch.from_numpy(2 * (rgb / 255.0) - 1).permute(2, 0, 1).float()[None].cuda()
        ph, pw = -(-h // PAD_MULTIPLE) * PAD_MULTIPLE, -(-w // PAD_MULTIPLE) * PAD_MULTIPLE
        x = F.pad(x, (0, pw - w, 0, ph - h), value=1.0)  # white padding right/bottom: no pixel moves

        heat = SPLIT[0]
        acc = None
        with torch.no_grad():
            t1 = time.time()
            for fwd, back in ROTATIONS:
                pred = _rot(self.model(_rot(x, fwd)), back)[:, heat:]
                pred = F.interpolate(pred, size=(ph, pw), mode="bilinear", align_corners=True)
                acc = pred if acc is None else acc + pred
            torch.cuda.synchronize()
            t_model = time.time() - t1
            logits = (acc / len(ROTATIONS))[:, :, :h, :w]
            if (h, w) != (H, W):  # back to the original pixel grid before anything is extracted
                logits = F.interpolate(logits, size=(H, W), mode="bilinear", align_corners=False)
            rooms = F.softmax(logits[0, : SPLIT[1]], 0)
            icons = F.softmax(logits[0, SPLIT[1] :], 0)
            room_prob, room_cls = rooms.max(0)
            icon_prob, icon_cls = icons.max(0)
            maps = [t.cpu().numpy() for t in (room_cls, room_prob, icon_cls, icon_prob)]
        room_cls, room_prob, icon_cls, icon_prob = maps
        raw = raw_from_maps(
            room_cls.astype(np.uint8),
            room_prob.astype(np.float32),
            icon_cls.astype(np.uint8),
            icon_prob.astype(np.float32),
            meta={
                "name": name,
                "model": self.info,
                "transforms": {"scale_x": w / W, "scale_y": h / H, "inverted": pre.transforms["inverted"],
                               "pad_px": [pw - w, ph - h], "rotations": len(ROTATIONS)},
            },
        )  # fmt: skip
        raw["timing_s"] = {"model": round(t_model, 3), "total": round(time.time() - t0, 3)}
        return raw


@app.cls(image=ocr_image, gpu="T4", scaledown_window=120, timeout=900)
class Ocr:
    @modal.enter()
    def load(self):
        import sys

        import paddle
        from paddleocr import PaddleOCR

        self.ocr = PaddleOCR(device="gpu", **OCR_KW)
        models = Path.home() / ".paddlex" / "official_models"
        names = sorted(p.name for p in models.iterdir()) if models.exists() else []
        frozen = Path("/pip-freeze.txt").read_text().splitlines() if Path("/pip-freeze.txt").exists() else []
        pins = {ln.split("==")[0].lower(): ln.split("==")[1] for ln in frozen if "==" in ln}
        self.info = {
            "paddle": str(paddle.__version__),
            "paddleocr": pins.get("paddleocr", "?"),
            "paddlex": pins.get("paddlex", "?"),
            "opencv": pins.get("opencv-contrib-python", pins.get("opencv-python", "?")),
            "numpy": pins.get("numpy", "?"),
            "cuda": str(paddle.version.cuda()),
            "cudnn": str(paddle.version.cudnn()),
            "compiled_with_cuda": bool(paddle.device.is_compiled_with_cuda()),
            "gpu_count": int(paddle.device.cuda.device_count()),
            "place": str(paddle.get_device()),
            "gpu": str(paddle.device.cuda.get_device_name(0)) if paddle.device.cuda.device_count() else None,
            "python": sys.version.split()[0],
            "params": OCR_KW,
            "models": names,
            "rec_model": OCR_KW["text_recognition_model_name"],
            "det_model": OCR_KW["text_detection_model_name"],
        }
        print("PaddleOCR loaded:", self.info)

    @modal.method()
    def run(self, png_bytes: bytes, name: str) -> dict:
        import cv2
        import numpy as np

        from translator.extract.ocr import items_from_result
        from translator.raster.preprocess import preprocess, to_bgr

        t0 = time.time()
        img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
        pre = preprocess(to_bgr(img, name), source=name)
        H, W = pre.normalized.shape[:2]
        up = pre.ocr_upscaled is not None
        k = pre.transforms["ocr_upscale"]
        bgr = cv2.cvtColor(pre.ocr_upscaled if up else pre.ocr_gray, cv2.COLOR_GRAY2BGR)
        orientations, timing = {}, {}
        for key, im in (("original", bgr), ("flipped", np.ascontiguousarray(bgr[:, ::-1]))):
            t1 = time.time()
            r = self.ocr.predict(im)[0]
            d = r if "rec_texts" in r else r.json["res"]
            orientations[key] = items_from_result(
                d["rec_texts"], d["rec_scores"], d["rec_polys"], k, key == "flipped", im.shape[1]
            )
            timing[key] = round(time.time() - t1, 3)
        timing["total"] = round(time.time() - t0, 3)
        return {
            "name": name,
            "width_px": int(W),
            "height_px": int(H),
            "ocr_input": "ocr_upscaled" if up else "ocr_gray",
            "ocr_upscale": k,
            "inverted": pre.transforms["inverted"],
            "orientations": orientations,
            "model": self.info,
            "timing_s": timing,
        }


def _select(files: list[str], names: str, limit: int, overwrite: bool, exists) -> list[str]:
    from translator.extract import cubicasa

    if names:
        wanted = {n.strip() for n in names.split(",")}
        files = [f for f in files if Path(f).stem in wanted]
    if not overwrite:
        files = [f for f in files if not exists(Path(f).stem)]
    if limit:
        files = files[:limit]
    assert not set(files) & cubicasa.holdout_files(), "held-out image selected"
    return files


@app.local_entrypoint()
def ocr_main(limit: int = 0, names: str = "", overwrite: bool = False):
    """OCR dev images without a raw OCR file (both orientations). Same selection flags as main."""
    from translator.config import data_root
    from translator.extract import cubicasa, ocr

    files = _select(cubicasa.dev_images(), names, limit, overwrite, lambda s: ocr.raw_path(s).exists())
    if not files:
        print("nothing to do: every selected dev image already has a raw OCR file")
        return
    stems = [Path(f).stem for f in files]
    blobs = [(data_root() / f).read_bytes() for f in files]
    ocr.raw_dir().mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    gpu_s, failures, done = 0.0, [], 0
    for stem, res in zip(stems, Ocr().run.map(blobs, stems, return_exceptions=True), strict=True):
        if isinstance(res, Exception):
            failures.append((stem, repr(res)[:300]))
            print("FAIL", stem, repr(res)[:300])
            continue
        res["cost_usd"] = round(res["timing_s"]["total"] * T4_USD_PER_S, 6)
        ocr.raw_path(stem).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        gpu_s += res["timing_s"]["total"]
        done += 1
        side, _, st = ocr.choose_orientation(res)
        o, f = st["original"], st["flipped"]
        print(f"{stem}: {len(res['orientations']['original'])} texts, read {side} "
              f"(hits {o['hits']}/{f['hits']}, mean {o['mean_score']}/{f['mean_score']}) "
              f"in {res['timing_s']['total']}s")  # fmt: skip
    print(
        f"\n{done}/{len(files)} images, {len(failures)} failures, wall {time.time() - t0:.0f}s, "
        f"GPU time in run() {gpu_s:.0f}s (~${gpu_s * T4_USD_PER_S:.3f} at T4 list price, excl. start-up)"
    )


@app.local_entrypoint()
def main(limit: int = 0, names: str = "", overwrite: bool = False):
    """Segment dev images without a raw file. --names a,b picks images by stem; --limit N takes N."""
    from translator.config import data_root
    from translator.extract import cubicasa

    files = _select(cubicasa.dev_images(), names, limit, overwrite, lambda s: cubicasa.raw_path(s).exists())
    if not files:
        print("nothing to do: every selected dev image already has a raw file")
        return

    stems = [Path(f).stem for f in files]
    blobs = [(data_root() / f).read_bytes() for f in files]
    out_dir = cubicasa.raw_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    gpu_s, failures, done = 0.0, [], 0
    for stem, res in zip(stems, Seg().run.map(blobs, stems, return_exceptions=True), strict=True):
        if isinstance(res, Exception):
            failures.append((stem, repr(res)[:300]))
            print("FAIL", stem, repr(res)[:300])
            continue
        res["cost_usd"] = round(res["timing_s"]["total"] * T4_USD_PER_S, 6)
        cubicasa.raw_path(stem).write_text(json.dumps(res), encoding="utf-8")
        gpu_s += res["timing_s"]["total"]
        done += 1
        icons = {}
        for c in res["icons"]:
            if c["area_px"] >= cubicasa.MIN_ICON_PX:
                icons[c["class"]] = icons.get(c["class"], 0) + 1
        rooms = {}
        for c in res["rooms"]:
            if c["class"] in cubicasa.ROOM_TYPES and c["area_px"] >= cubicasa.MIN_ROOM_PX:
                rooms[c["class"]] = rooms.get(c["class"], 0) + 1
        print(f"{stem}: {res['width_px']}x{res['height_px']} scale {res['transforms']['scale_x']:.3f} "
              f"in {res['timing_s']['total']}s | icons {icons} | rooms {rooms}")  # fmt: skip
    print(
        f"\n{done}/{len(files)} images, {len(failures)} failures, wall {time.time() - t0:.0f}s, "
        f"GPU time in run() {gpu_s:.0f}s (~${gpu_s * T4_USD_PER_S:.3f} at T4 list price, excl. start-up)"
    )
