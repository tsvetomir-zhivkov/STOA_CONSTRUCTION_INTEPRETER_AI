"""Tiling for the symbol detector: fixed-size tiles with overlap, boxes kept when >= 60% inside, clipped.

Tiles are only used for training and inference input; boxes are always mapped back to ORIGINAL page pixels
(tile origin added) before anything is written. No resizing here, tiles are plain crops.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from PIL import Image

from pipes.ingest.augment import variants

TILE = 640
OVERLAP = 128
KEEP_FRAC = 0.6
EMPTY_KEEP = 0.2  # keep 20% of tiles without labels (training only)


def origins(length: int, size: int = TILE, overlap: int = OVERLAP) -> list[int]:
    """Tile start offsets along one axis; the last tile is pulled back so it ends at the page edge."""
    if length <= size:
        return [0]
    step = size - overlap
    xs = list(range(0, length - size, step)) + [length - size]
    return sorted(set(xs))


def tile_boxes(boxes: list[list[float]], x0: int, y0: int, w: int, h: int) -> list[list[float]]:
    """Boxes (page px x0,y0,x1,y1) that are >= KEEP_FRAC inside the tile, clipped, in tile px."""
    out = []
    for bx0, by0, bx1, by1 in boxes:
        area = (bx1 - bx0) * (by1 - by0)
        cx0, cy0, cx1, cy1 = max(bx0, x0), max(by0, y0), min(bx1, x0 + w), min(by1, y0 + h)
        if cx1 <= cx0 or cy1 <= cy0 or (cx1 - cx0) * (cy1 - cy0) < KEEP_FRAC * area:
            continue
        out.append([cx0 - x0, cy0 - y0, cx1 - x0, cy1 - y0])
    return out


def read_boxes(path: Path) -> list[list[float]]:
    return [[float(v) for v in ln.split()[1:]] for ln in path.read_text().splitlines() if ln.strip()]


def build_yolo_dataset(
    dev: Path,
    out: Path,
    seed: int = 0,
    splits=("train", "val"),
    augment: int = 0,
    yaml_path: str = "/data/yolo_dev",
    whole: bool = False,
) -> dict:
    """dev/<split>/<id>/{sheet.jpg,yolo.txt} -> out/{images,labels}/<split>/<id>_<x>_<y>.{jpg,txt} + yaml."""
    rng = random.Random(seed)
    if out.exists():
        shutil.rmtree(out)
    stats = {}
    for sp in splits:
        (out / "images" / sp).mkdir(parents=True)
        (out / "labels" / sp).mkdir(parents=True)
        n_tiles = n_boxes = 0
        for d in sorted((dev / sp).iterdir()):
            if not d.is_dir():
                continue
            im = Image.open(d / "sheet.jpg").convert("RGB")
            W, H = im.size
            boxes = read_boxes(d / "yolo.txt")
            if whole and boxes:  # the whole sheet too (trained at a larger imgsz), plus augmented copies
                extra = variants(im, boxes, augment, rng) if sp == "train" and augment else []
                for name, wim, wbx in [("whole", im, boxes), *extra]:
                    ww, wh = wim.size
                    stem = d.name + ("_whole" if name == "whole" else f"_whole_{name}")
                    wim.save(out / "images" / sp / f"{stem}.jpg", quality=95)
                    (out / "labels" / sp / f"{stem}.txt").write_text(
                        "".join(
                            f"0 {(b[0] + b[2]) / 2 / ww:.6f} {(b[1] + b[3]) / 2 / wh:.6f} "
                            f"{(b[2] - b[0]) / ww:.6f} {(b[3] - b[1]) / wh:.6f}\n"
                            for b in wbx
                        )
                    )
                    n_tiles += 1
                    n_boxes += len(wbx)
            for y0 in origins(H):
                for x0 in origins(W):
                    w, h = min(TILE, W), min(TILE, H)
                    tb = tile_boxes(boxes, x0, y0, w, h)
                    if not tb and rng.random() > EMPTY_KEEP:
                        continue
                    stem = f"{d.name}_{x0}_{y0}"
                    im.crop((x0, y0, x0 + w, y0 + h)).save(out / "images" / sp / f"{stem}.jpg", quality=95)
                    lines = [
                        f"0 {(b[0] + b[2]) / 2 / w:.6f} {(b[1] + b[3]) / 2 / h:.6f} "
                        f"{(b[2] - b[0]) / w:.6f} {(b[3] - b[1]) / h:.6f}\n"
                        for b in tb
                    ]
                    (out / "labels" / sp / f"{stem}.txt").write_text("".join(lines))
                    n_tiles += 1
                    n_boxes += len(tb)
                    if augment and sp == "train" and tb:
                        crop = im.crop((x0, y0, x0 + w, y0 + h))
                        for name, aim, abx in variants(crop, tb, augment, rng):
                            aw, ah = aim.size
                            aim.save(out / "images" / sp / f"{stem}_{name}.jpg", quality=95)
                            (out / "labels" / sp / f"{stem}_{name}.txt").write_text(
                                "".join(
                                    f"0 {(b[0] + b[2]) / 2 / aw:.6f} {(b[1] + b[3]) / 2 / ah:.6f} "
                                    f"{(b[2] - b[0]) / aw:.6f} {(b[3] - b[1]) / ah:.6f}\n"
                                    for b in abx
                                )
                            )
                            n_tiles += 1
                            n_boxes += len(abx)
        stats[sp] = {"tiles": n_tiles, "boxes": n_boxes}
    (out / "pipes.yaml").write_text(
        f"path: {yaml_path}\ntrain: images/train\nval: images/val\nnames:\n  0: symbol\n", encoding="utf-8"
    )
    return stats
