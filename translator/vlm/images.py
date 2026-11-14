"""Images sent to a VLM: a downscaled whole page, or a padded crop. Each comes with its Crop mapping, so
boxes map back to ORIGINAL page pixels. The page image itself is never modified."""

from __future__ import annotations

import cv2
import numpy as np

from translator.vlm import config
from translator.vlm.base import Crop, guard_source

MIN_CROP_SIDE_PX = 512  # small crops are upscaled to this (recorded in Crop.scale)


def load_page(source: str, root=None) -> np.ndarray:
    """Read a page image (dataset-relative path) after the hold-out guard; a hold-out file is never opened."""
    from pathlib import Path

    from translator.config import data_root

    guard_source(source)
    img = cv2.imread(str(Path(data_root(root)) / source))
    if img is None:
        raise FileNotFoundError(source)
    return img


def _encode(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("PNG encoding failed")
    return buf.tobytes()


def page_image(img: np.ndarray, max_side: int = config.MAX_SIDE_PX) -> tuple[bytes, Crop]:
    """The whole page, downscaled so its longest side <= max_side (never upscaled)."""
    h, w = img.shape[:2]
    s = min(1.0, max_side / max(h, w))
    out = img if s == 1.0 else cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return _encode(out), Crop(0.0, 0.0, s, out.shape[1], out.shape[0])


def padded_box(bbox: list[float], w: int, h: int, pad_frac: float = config.CROP_PAD_FRAC,
               min_pad: int = config.CROP_MIN_PAD_PX) -> list[int]:  # fmt: skip
    """bbox [x0, y0, x1, y1] padded by pad_frac of its longest side (>= min_pad), clipped to the page."""
    x0, y0, x1, y1 = bbox
    pad = max(min_pad, pad_frac * max(x1 - x0, y1 - y0))
    return [
        max(0, int(x0 - pad)),
        max(0, int(y0 - pad)),
        min(w, int(np.ceil(x1 + pad))),
        min(h, int(np.ceil(y1 + pad))),
    ]


def crop_image(
    img: np.ndarray, bbox: list[float], max_side: int = config.MAX_SIDE_PX, min_side: int = MIN_CROP_SIDE_PX
) -> tuple[bytes, Crop, list[int]]:
    """A padded crop of bbox, resized so its longest side is within [min_side, max_side].

    Returns (png bytes, Crop, padded page box). The crop is clipped to the page, so Crop.page_x0/y0 are the
    padded box's top-left corner.
    """
    h, w = img.shape[:2]
    px0, py0, px1, py1 = padded_box(bbox, w, h)
    sub = img[py0:py1, px0:px1]
    ch, cw = sub.shape[:2]
    longest = max(ch, cw)
    s = max_side / longest if longest > max_side else (min_side / longest if longest < min_side else 1.0)
    if s != 1.0:
        interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
        sub = cv2.resize(sub, (max(1, round(cw * s)), max(1, round(ch * s))), interpolation=interp)
    return _encode(sub), Crop(float(px0), float(py0), s, sub.shape[1], sub.shape[0]), [px0, py0, px1, py1]
