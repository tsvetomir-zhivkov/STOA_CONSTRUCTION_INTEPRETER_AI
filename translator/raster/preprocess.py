"""Step 5: raster preprocessing. One clean input per raster model, on the original pixel grid.

preprocess(img) -> Preprocessed with
  original      the input image, unchanged
  normalized    BGR; inverted when the background is dark (median grey < DARK_MEDIAN). Invert, never brighten.
  ocr_gray      greyscale of normalized with CLAHE (clipLimit 2.0, tiles 8x8)
  binary        adaptive Gaussian threshold of the greyscale of normalized (block 31, C 10):
                dark lines (0) on a white background (255)
  ocr_upscaled  ocr_gray x2 when the longest side is < UPSCALE_BELOW px, for OCR only; else None
  transforms    {inverted, ocr_upscale, scale_x, scale_y, deskew_deg}; schema_transforms() gives
                the same as valid drawing.transforms entries

Nothing is cropped, rotated or resized in place: normalized, ocr_gray and binary have the height and
width of the original. Only opencv-python-headless and numpy are used (pymupdf, already a dependency,
renders scanned PDFs).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

DARK_MEDIAN = 100  # median grey below this = dark background -> invert
CLAHE_CLIP = 2.0
CLAHE_TILES = (8, 8)
THRESH_BLOCK = 31
THRESH_C = 10
UPSCALE_BELOW = 1500  # longest side in px
OCR_UPSCALE = 2


class RasterError(ValueError):
    """An image that cannot be loaded or preprocessed. The message names the file and the reason."""


@dataclass
class Preprocessed:
    original: np.ndarray
    normalized: np.ndarray
    ocr_gray: np.ndarray
    binary: np.ndarray
    ocr_upscaled: np.ndarray | None
    transforms: dict = field(default_factory=dict)
    source: str | None = None  # file the image came from, if any

    @property
    def shape(self) -> tuple[int, ...]:
        """Shape of the normalized image (same height and width as the original)."""
        return self.normalized.shape

    def schema_transforms(self) -> list[dict]:
        """The transforms as schema v0.2 drawing.transforms entries (invert / upscale / resize / deskew)."""
        t = self.transforms
        out = []
        if t["inverted"]:
            out.append(
                {
                    "kind": "invert",
                    "from": "original",
                    "to": "normalized",
                    "note": f"dark background (median grey < {DARK_MEDIAN}) inverted",
                }
            )
        if t["ocr_upscale"] != 1:
            out.append(
                {
                    "kind": "upscale",
                    "from": "ocr_gray",
                    "to": "ocr_upscaled",
                    "scale_x": float(t["ocr_upscale"]),
                    "scale_y": float(t["ocr_upscale"]),
                    "note": "OCR input only: divide OCR pixel coordinates by this factor",
                }
            )
        if t["scale_x"] != 1.0 or t["scale_y"] != 1.0:
            out.append(
                {
                    "kind": "resize",
                    "from": "original",
                    "to": "model_input",
                    "scale_x": float(t["scale_x"]),
                    "scale_y": float(t["scale_y"]),
                }
            )
        if t["deskew_deg"] != 0.0:
            out.append(
                {
                    "kind": "deskew",
                    "from": "original",
                    "to": "model_input",
                    "angle_deg": float(t["deskew_deg"]),
                }
            )
        return out


# ------------------------------------------------------------------ loading


def to_bgr(img: np.ndarray, where: str = "<array>") -> np.ndarray:
    """Any decoded image -> 8-bit BGR: greyscale, BGRA (composited on white), 16-bit."""
    if img is None or not isinstance(img, np.ndarray) or img.size == 0:
        raise RasterError(f"{where}: empty or undecodable image")
    if img.dtype == np.uint16:
        img = (img >> 8).astype(np.uint8)
    elif img.dtype in (np.float32, np.float64):
        img = np.clip(img * (255.0 if img.max() <= 1.0 else 1.0), 0, 255).astype(np.uint8)
    elif img.dtype == bool:
        img = img.astype(np.uint8) * 255
    elif img.dtype != np.uint8:
        raise RasterError(f"{where}: unsupported pixel type {img.dtype}")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.ndim == 3 and img.shape[2] == 1:
        return cv2.cvtColor(img[:, :, 0], cv2.COLOR_GRAY2BGR)
    if img.ndim == 3 and img.shape[2] == 3:
        return img
    if img.ndim == 3 and img.shape[2] == 4:  # composite on white
        bgr = img[:, :, :3].astype(np.float32)
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        return np.round(bgr * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    raise RasterError(f"{where}: unsupported image shape {img.shape}")


def load_image(path: str | Path, page_index: int = 0, pdf_dpi: float | None = None) -> np.ndarray:
    """Decode an image file (PNG/JPG, incl. alpha, greyscale, palette, 16-bit) or render a PDF page -> BGR.

    Reads through numpy so non-ASCII Windows paths work (cv2.imread cannot open them).
    """
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        import pymupdf

        from translator.router import SCAN_DPI

        try:
            with pymupdf.open(p) as doc:
                pix = doc[page_index].get_pixmap(dpi=pdf_dpi or SCAN_DPI, alpha=False)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        except Exception as e:
            raise RasterError(f"{p}: cannot render PDF page {page_index}: {e}") from e
        arr = arr[:, :, :3][:, :, ::-1] if arr.shape[2] >= 3 else arr  # RGB -> BGR
        return to_bgr(np.ascontiguousarray(arr), str(p))
    try:
        data = np.fromfile(p, dtype=np.uint8)
    except OSError as e:
        raise RasterError(f"{p}: cannot read file: {e}") from e
    # IMREAD_UNCHANGED keeps alpha and 16-bit; palette PNGs are expanded to BGR(A) by OpenCV
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RasterError(f"{p}: OpenCV cannot decode this file")
    return to_bgr(img, str(p))


# ------------------------------------------------------------------ preprocessing


def preprocess(img_bgr: np.ndarray, source: str | None = None) -> Preprocessed:
    """Build the model inputs for one image. All outputs except ocr_upscaled keep the original grid."""
    where = source or "<array>"
    bgr = to_bgr(img_bgr, where)  # also accepts greyscale / BGRA / 16-bit arrays
    h, w = bgr.shape[:2]

    gray0 = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    inverted = bool(np.median(gray0) < DARK_MEDIAN)
    normalized = cv2.bitwise_not(bgr) if inverted else bgr.copy()
    gray = cv2.bitwise_not(gray0) if inverted else gray0

    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=CLAHE_TILES)
    ocr_gray = clahe.apply(gray)
    # dark lines -> 0, background -> 255 (pixels darker than the local Gaussian mean minus C)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, THRESH_BLOCK, THRESH_C
    )

    upscale = OCR_UPSCALE if max(h, w) < UPSCALE_BELOW else 1
    ocr_upscaled = (
        cv2.resize(ocr_gray, (w * upscale, h * upscale), interpolation=cv2.INTER_CUBIC)
        if upscale > 1
        else None
    )

    for name, a in (("normalized", normalized), ("ocr_gray", ocr_gray), ("binary", binary)):
        if a.shape[:2] != (h, w):  # the hard rule: never change the pixel grid
            raise RasterError(f"{where}: {name} is {a.shape[:2]}, original is {(h, w)}")

    transforms = {
        "inverted": inverted,
        "ocr_upscale": int(upscale),
        "scale_x": 1.0,
        "scale_y": 1.0,
        "deskew_deg": 0.0,
    }
    return Preprocessed(
        original=img_bgr,
        normalized=normalized,
        ocr_gray=ocr_gray,
        binary=binary,
        ocr_upscaled=ocr_upscaled,
        transforms=transforms,
        source=source,
    )


def preprocess_path(path: str | Path, data: str | Path | None = None) -> Preprocessed:
    """Route the file, and preprocess it when the router sends it to the raster route.

    Raises RasterError (after logging the file and the reason) when the file is not a raster route or
    cannot be decoded. The caller decides whether to skip it.
    """
    from translator import router
    from translator.config import data_root

    root = data_root(data)
    r = router.route(path, root)
    if r["route"] != "raster":
        msg = f"{path}: route is {r['route']!r}, not raster ({r.get('reason')})"
        log.warning(msg)
        raise RasterError(msg)
    src = Path(r["read_from"])
    if not src.is_absolute():
        src = root / src
    try:
        img = load_image(src)
        return preprocess(img, source=r["file"])
    except RasterError as e:
        log.warning("raster preprocessing failed: %s", e)
        raise
