"""Step 5: raster preprocessing. Synthetic images for every rule, plus one real dev image."""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import cv2
import jsonschema
import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from translator.config import SCHEMA_PATH, data_root, questions_path  # noqa: E402
from translator.raster import RasterError, load_image, preprocess, preprocess_path  # noqa: E402

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)
HOLDOUT = {
    ln.strip()
    for ln in (REPO / "eval" / "holdout_files.txt").read_text(encoding="utf-8").splitlines()
    if ln.strip() and not ln.startswith("#")
}


# ------------------------------------------------------------------ synthetic images


def plan(h=1600, w=2000, dark=False, channels=3) -> np.ndarray:
    """A floor-plan-like drawing: thick walls, thin lines, a text-like block."""
    bg, ink = (20, 255) if dark else (250, 0)
    img = np.full((h, w), bg, np.uint8)
    cv2.rectangle(img, (w // 10, h // 10), (w - w // 10, h - h // 10), ink, 12)
    cv2.line(img, (w // 2, h // 10), (w // 2, h - h // 10), ink, 6)
    cv2.line(img, (w // 10, h // 2), (w // 2, h // 2), ink, 2)
    cv2.putText(img, "MH 12.5", (w // 5, h // 3), cv2.FONT_HERSHEY_SIMPLEX, 1.5, ink, 3)
    if channels == 1:
        return img
    bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return bgr


def write_png(path: Path, img: np.ndarray) -> Path:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    buf.tofile(str(path))
    return path


def write_palette_png(path: Path, idx: np.ndarray, palette: list[tuple[int, int, int]]) -> Path:
    """Minimal 8-bit palette PNG (colour type 3); OpenCV cannot write one."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    h, w = idx.shape
    raw = b"".join(b"\x00" + idx[r].astype(np.uint8).tobytes() for r in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
        + chunk(b"PLTE", b"".join(bytes(c) for c in palette))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def white_background(binary: np.ndarray) -> bool:
    return np.median(binary) == 255 and (binary == 255).mean() > 0.8


# ------------------------------------------------------------------ rules


def test_light_plan_is_not_inverted_and_keeps_the_grid():
    img = plan()
    pre = preprocess(img)
    assert pre.original is img
    assert pre.normalized.shape == img.shape
    assert pre.binary.shape == img.shape[:2] and pre.ocr_gray.shape == img.shape[:2]
    assert not pre.transforms["inverted"]
    assert np.array_equal(pre.normalized, img)
    assert white_background(pre.binary)
    assert pre.binary[img.shape[0] // 10, img.shape[1] // 2] == 0  # the wall is a dark line


def test_dark_plan_is_inverted_not_brightened():
    img = plan(dark=True)
    pre = preprocess(img)
    assert pre.transforms["inverted"]
    assert np.array_equal(pre.normalized, 255 - img)  # an exact inversion, no gain or gamma
    assert pre.normalized.shape == img.shape and pre.binary.shape == img.shape[:2]
    assert np.median(cv2.cvtColor(pre.normalized, cv2.COLOR_BGR2GRAY)) > 200
    assert white_background(pre.binary)
    assert pre.binary[img.shape[0] // 10, img.shape[1] // 2] == 0  # bright wall became a dark line


def test_rgba_is_composited_on_white(tmp_path):
    rgba = np.zeros((1600, 2000, 4), np.uint8)  # fully transparent ...
    rgba[300:310, 200:1800] = (0, 0, 0, 255)  # ... with one opaque black wall
    img = load_image(write_png(tmp_path / "rgba.png", rgba))
    assert img.shape == (1600, 2000, 3)
    assert img[0, 0].tolist() == [255, 255, 255] and img[305, 1000].tolist() == [0, 0, 0]
    pre = preprocess(rgba)  # the array path composites too
    assert not pre.transforms["inverted"]  # transparent black must not read as a dark background
    assert pre.binary.shape == rgba.shape[:2] and white_background(pre.binary)


def test_greyscale_image_and_file(tmp_path):
    gray = plan(channels=1)
    pre = preprocess(gray)
    assert pre.normalized.shape == (*gray.shape, 3) and pre.binary.shape == gray.shape
    img = load_image(write_png(tmp_path / "gray.png", gray))
    assert img.shape == (*gray.shape, 3)


def test_palette_png(tmp_path):
    idx = np.zeros((1600, 2000), np.uint8)
    idx[400:420, 100:1900] = 1
    path = write_palette_png(tmp_path / "palette.png", idx, [(255, 255, 255), (0, 0, 0)])
    pre = preprocess(load_image(path), source=str(path))
    assert pre.normalized.shape == (1600, 2000, 3) and white_background(pre.binary)
    assert pre.binary[410, 1000] == 0


def test_16bit_png_and_non_ascii_path(tmp_path):
    img16 = plan(channels=1).astype(np.uint16) * 257
    path = write_png(tmp_path / "pohjapiirros_ä_ö.png", img16)
    img = load_image(path)
    assert img.dtype == np.uint8 and img.shape == (*img16.shape, 3)


def test_small_image_is_upscaled_x2_for_ocr_only():
    img = plan(h=600, w=800)
    pre = preprocess(img)
    assert pre.transforms["ocr_upscale"] == 2
    assert pre.ocr_upscaled.shape == (1200, 1600)
    assert pre.normalized.shape == img.shape and pre.binary.shape == img.shape[:2]  # grid unchanged
    kinds = [t["kind"] for t in pre.schema_transforms()]
    assert kinds == ["upscale"]


def test_large_image_is_not_upscaled():
    pre = preprocess(plan(h=1000, w=1500))  # longest side exactly 1500: not < 1500
    assert pre.transforms["ocr_upscale"] == 1 and pre.ocr_upscaled is None
    assert pre.schema_transforms() == []


def test_transforms_json_round_trip_and_fit_the_schema():
    pre = preprocess(plan(h=600, w=800, dark=True))
    t = pre.transforms
    assert json.loads(json.dumps(t)) == t
    assert t == {"inverted": True, "ocr_upscale": 2, "scale_x": 1.0, "scale_y": 1.0, "deskew_deg": 0.0}
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    transforms_schema = {"$defs": schema["$defs"], **schema["$defs"]["drawing"]["properties"]["transforms"]}
    entries = pre.schema_transforms()
    jsonschema.Draft202012Validator(transforms_schema).validate(entries)
    assert [e["kind"] for e in entries] == ["invert", "upscale"]
    assert json.loads(json.dumps(entries)) == entries


def test_undecodable_file_raises_with_the_file_name(tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a png at all")
    with pytest.raises(RasterError, match="broken.png"):
        load_image(bad)


def test_preprocess_path_uses_the_raster_route(tmp_path):
    folder = tmp_path / "cubicasa5k"
    folder.mkdir()
    path = write_png(folder / "plan.png", plan())
    pre = preprocess_path(path, tmp_path)
    assert pre.normalized.shape[:2] == (1600, 2000)
    (folder / "plan.dxf").write_text("0\nEOF\n")  # a DXF sibling wins: no longer a raster route
    with pytest.raises(RasterError, match="not raster"):
        preprocess_path(path, tmp_path)


@needs_data
def test_real_dev_cubicasa_image():
    path = "data/cubicasa5k/high_quality-5815.png"  # pinned to the dev split
    assert path not in HOLDOUT
    pre = preprocess_path(path)
    orig = load_image(data_root() / path)
    assert pre.normalized.shape[:2] == orig.shape[:2] == pre.binary.shape[:2]
    assert white_background(pre.binary)
