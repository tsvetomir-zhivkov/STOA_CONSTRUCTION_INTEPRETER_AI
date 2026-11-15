"""Quality gate before detection in the scan route. Measure first; a fix runs only for the check that failed.

Checks (thresholds sit outside everything seen on the 20 test sheets, which pass all four untouched):
  small_thin  stroke width <= 1.5 px (the tracer's floor) or the longest page side < 500 px      -> 3x upscale
  background  light pixels of a 4x4 grid: darkest cell median < 200, spread (max - min) > 25,
              or per-channel tint (max - min of the paper's channel medians) > 12; or a dark page  -> invert (translator
              rule), illumination correction (per channel divide by a large blur of a line-free copy), whiten paper
  noise       tiny components (< w^2 px) above 1000 per megapixel                                    -> remove them

The page grid never changes: invert / illumination / noise act on the same pixels. The 3x upscale feeds only the
detector and OCR; their boxes are divided by the factor, so every coordinate stays an ORIGINAL page pixel.
Real-ESRGAN runs on Modal (pipes.modal_pipes.upscale); without it a Lanczos resize is used and recorded as such.
"""

from __future__ import annotations

import cv2
import numpy as np

from pipes.raster import erase
from pipes.reuse import preprocess
from translator.raster.preprocess import DARK_MEDIAN

MIN_STROKE_PX = 1.5
MIN_SIDE_PX = 500
MIN_CELL_MEDIAN = 200
MAX_CELL_SPREAD = 25
MAX_TINT = 12
MAX_TINY_PER_MP = 1000
UPSCALE = 3
GRID = 4


def _ink(bgr: np.ndarray) -> np.ndarray:
    return preprocess(bgr).binary == 0


def measure(bgr: np.ndarray) -> dict:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    dark = bool(np.median(gray) < DARK_MEDIAN)
    if dark:  # judge the paper, not the ink: same polarity rule as the translator
        bgr, gray = cv2.bitwise_not(bgr), cv2.bitwise_not(gray)
    ink = _ink(bgr)
    sw = erase.stroke_width(ink)
    meds = []
    for i in range(GRID):
        for j in range(GRID):
            c = gray[i * h // GRID : (i + 1) * h // GRID, j * w // GRID : (j + 1) * w // GRID]
            light = c >= np.percentile(c, 50)
            if light.sum() >= 50:
                meds.append(float(np.median(c[light])))
    light = gray >= np.percentile(gray, 50)
    chan = [float(np.median(bgr[..., k][light])) for k in range(3)]
    n, _, st, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    tiny = int((st[1:, cv2.CC_STAT_AREA] < sw * sw).sum())
    return {
        "size_px": [w, h],
        "stroke_px": round(float(sw), 2),
        "dark_page": dark,
        "bg_median": round(float(np.median(meds)), 1),
        "bg_darkest_cell": round(min(meds), 1),
        "bg_spread": round(max(meds) - min(meds), 1),
        "tint": round(max(chan) - min(chan), 1),
        "tiny_per_mp": round(tiny / (w * h / 1e6)),
    }


def failed_checks(m: dict) -> list[str]:
    out = []
    if m["stroke_px"] <= MIN_STROKE_PX or max(m["size_px"]) < MIN_SIDE_PX:
        out.append("small_thin")
    if (
        m["dark_page"]
        or m["bg_darkest_cell"] < MIN_CELL_MEDIAN
        or m["bg_spread"] > MAX_CELL_SPREAD
        or m["tint"] > MAX_TINT
    ):
        out.append("background")
    if m["tiny_per_mp"] > MAX_TINY_PER_MP:
        out.append("noise")
    return out


def fix_background(bgr: np.ndarray) -> np.ndarray:
    """Invert a dark page (the translator's rule), then divide each channel by a large blur of itself with the lines
    removed (so uneven light and a tint both vanish) and whiten the paper."""
    if np.median(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)) < DARK_MEDIAN:
        bgr = cv2.bitwise_not(bgr)
    h, w = bgr.shape[:2]
    k = max(31, (max(h, w) // 8) | 1)
    line = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (15, 15)
    )  # wider than a stroke: closing drops the lines
    out = np.empty_like(bgr)
    for c in range(3):
        paper = cv2.dilate(bgr[..., c], line)
        paper = cv2.GaussianBlur(paper, (k, k), 0).astype(np.float32)
        out[..., c] = np.clip(bgr[..., c].astype(np.float32) / np.maximum(paper, 1.0) * 255.0, 0, 255)
    # whiten: everything the correction left near white is paper
    return np.where(out >= 235, 255, out).astype(np.uint8)


def remove_noise(bgr: np.ndarray) -> np.ndarray:
    """Paint components smaller than w^2 px white (w = stroke width of the page)."""
    ink = _ink(bgr)
    w = erase.stroke_width(ink)
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    tiny = np.zeros(n, bool)
    tiny[1:] = st[1:, cv2.CC_STAT_AREA] < w * w
    out = bgr.copy()
    out[tiny[lab]] = 255
    return out


def lanczos_upscale(bgr: np.ndarray, factor: int = UPSCALE) -> np.ndarray:
    return cv2.resize(bgr, None, fx=factor, fy=factor, interpolation=cv2.INTER_LANCZOS4)


def upscale_image(img: np.ndarray, upscaler=None) -> tuple[np.ndarray, str]:
    """-> (UPSCALE x larger image, fix name). upscaler = Real-ESRGAN (Modal); on failure or None: Lanczos, said so."""
    note_ = ""
    if upscaler is not None:
        try:
            return upscaler(img), f"upscale_x{UPSCALE}_realesrgan"
        except Exception as e:
            note_ = f"realesrgan_failed({type(e).__name__}) -> "
    return lanczos_upscale(img), f"{note_}upscale_x{UPSCALE}_lanczos"


def gate(bgr: np.ndarray) -> dict:
    """-> {measures, failed, fixes, image (same grid; the input object itself when nothing failed), factor}."""
    m = measure(bgr)
    failed = failed_checks(m)
    fixes: list[str] = []
    img = bgr
    if "background" in failed:
        if m["dark_page"]:
            fixes.append("invert")
        img = fix_background(img)
        fixes += ["illumination_correction", "whiten_paper"]
    if "noise" in failed:
        img = remove_noise(img)
        fixes.append("remove_small_components")
    factor = UPSCALE if "small_thin" in failed else 1  # the upscale itself runs only when a GPU call needs it
    return {"measures": m, "failed": failed, "fixes": fixes, "image": img, "factor": factor}


def note(g: dict) -> str:
    """One line for the extractor name and the demo caption."""
    if not g["failed"]:
        return "quality gate: all checks passed"
    m = g["measures"]
    return (
        f"quality gate: failed {'+'.join(g['failed'])}; fixes {', '.join(g['fixes']) or 'none'} "
        f"(stroke {m['stroke_px']} px, {m['size_px'][0]}x{m['size_px'][1]}, bg spread {m['bg_spread']}, "
        f"tint {m['tint']}, noise {m['tiny_per_mp']}/MP)"
    )


def transforms(g: dict) -> list[dict]:
    """The gate's fixes as drawing.transforms entries (piping schema); the page grid itself never changes."""
    out = []
    for f in g["fixes"]:
        if f == "invert":
            out.append({"kind": "invert", "note": "dark background inverted (translator rule)"})
        elif f.startswith("upscale") or "upscale" in f:
            out.append({"kind": "upscale", "from": "page", "to": "detector_input", "scale_x": float(UPSCALE),
                        "scale_y": float(UPSCALE), "note": f"{f}: detection and OCR only, boxes mapped back"})  # fmt: skip
        elif f in ("illumination_correction", "whiten_paper", "remove_small_components"):
            out.append({"kind": "other", "note": f"{f}: same pixel grid"})
    return out


def scale_detections(det: dict, factor: int, size: tuple[int, int]) -> dict:
    """Boxes found on the upscaled page, back to original page pixels."""
    if factor == 1:
        return det
    out = {**det, "width": size[0], "height": size[1]}
    out["dets"] = [{**d, "bbox_px": [v / factor for v in d["bbox_px"]]} for d in det["dets"]]
    return out


def scale_texts(items: list[dict], factor: int) -> list[dict]:
    if factor == 1:
        return items
    return [{**t, "poly": [[x / factor, y / factor] for x, y in t["poly"]]} for t in items]
