"""Offline augmentation of training tiles (plan step 6, domain-gap mitigation b), labels transformed with the image.

Geometry: 90/180/270 rotations and flips (boxes transformed exactly; symbol-only detector, so no pipe_h/pipe_v
swap is needed). Photometric (mild): blur, gaussian noise, JPEG. Applied to TRAIN tiles only, never to val/test.
"""

from __future__ import annotations

import io
import random

import numpy as np
from PIL import Image, ImageFilter

Box = list[float]


def rot90(img: Image.Image, boxes: list[Box]) -> tuple[Image.Image, list[Box]]:
    """Rotate 90 degrees counter-clockwise (like np.rot90): (x, y) -> (y, W - x)."""
    W = img.width
    return img.transpose(Image.Transpose.ROTATE_90), [[b[1], W - b[2], b[3], W - b[0]] for b in boxes]


def hflip(img: Image.Image, boxes: list[Box]) -> tuple[Image.Image, list[Box]]:
    W = img.width
    return img.transpose(Image.Transpose.FLIP_LEFT_RIGHT), [[W - b[2], b[1], W - b[0], b[3]] for b in boxes]


def vflip(img: Image.Image, boxes: list[Box]) -> tuple[Image.Image, list[Box]]:
    H = img.height
    return img.transpose(Image.Transpose.FLIP_TOP_BOTTOM), [[b[0], H - b[3], b[2], H - b[1]] for b in boxes]


GEOMETRY = [
    ("r90", lambda i, b: rot90(i, b)),
    ("r180", lambda i, b: rot90(*rot90(i, b))),
    ("r270", lambda i, b: rot90(*rot90(*rot90(i, b)))),
    ("hflip", hflip),
    ("vflip", vflip),
    ("hflip_r90", lambda i, b: rot90(*hflip(i, b))),
]


def photometric(img: Image.Image, rng: random.Random) -> Image.Image:
    if rng.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.1)))
    if rng.random() < 0.5:
        a = np.asarray(img).astype(np.float32)
        a += np.random.default_rng(rng.randrange(1 << 30)).normal(0, rng.uniform(3, 9), a.shape)
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    if rng.random() < 0.5:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=rng.randint(45, 85))
        img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return img


def variants(
    img: Image.Image, boxes: list[Box], n: int, rng: random.Random
) -> list[tuple[str, Image.Image, list[Box]]]:
    """n augmented copies: a random geometry transform (never identity) plus mild photometric noise."""
    out = []
    for name, fn in rng.sample(GEOMETRY, k=min(n, len(GEOMETRY))):
        im, bx = fn(img, boxes)
        out.append((name, photometric(im, rng), bx))
    return out
