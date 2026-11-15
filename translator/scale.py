"""Step 8: scale recovery (vector units > scale note > dimension lines > door width).

estimate(doc) -> drawing.scale for a raster plan:
  vector_units     kept as is when an extractor already set it (DXF, IFC)
  scale_note       '1:50' / 'M 1:100' read by OCR, only when the image dpi is known (scanned PDF pages);
                   CubiCasa PNGs carry no dpi, so this path does not fire for them
  dimension_lines  not implemented (CubiCasa questions are counts; scale is not on their critical path)
  door_width       median long side of the detected doors = DOOR_WIDTH_M, low confidence
  none             otherwise, confidence 0
Written areas are never used to set the scale; they only cross-check.
"""

from __future__ import annotations

import re
import statistics

DOOR_WIDTH_M = 0.85  # typical door leaf 0.8-0.9 m
DOOR_CONF = 0.2
MIN_DOORS = 3
SCALE_NOTE_CONF = 0.7
_NOTE = re.compile(r"(?<![\d.,])1\s*:\s*(\d{1,4})(?![\d.,])")
_DENOMINATORS = {10, 20, 25, 50, 100, 200, 250, 500, 1000}


def scale_note(texts: list[dict]) -> tuple[int, dict] | None:
    """(denominator, text) of the first plausible scale note, e.g. '1:50'."""
    for t in texts:
        if t.get("status", "active") != "active":
            continue
        m = _NOTE.search(t["text"])
        if m and int(m.group(1)) in _DENOMINATORS:
            return int(m.group(1)), t
    return None


def door_width(doc: dict) -> tuple[float, int] | None:
    """(median door long side in px, number of doors), from active door openings with a bbox."""
    sides = []
    for o in doc["openings"]:
        if o.get("status", "active") == "active" and o["kind"] == "door" and o.get("bbox_px"):
            x0, y0, x1, y1 = o["bbox_px"]
            sides.append(max(x1 - x0, y1 - y0))
    if len(sides) < MIN_DOORS:
        return None
    return float(statistics.median(sides)), len(sides)


def estimate(doc: dict) -> dict:
    current = doc["drawing"].get("scale")
    if current and current.get("source") == "vector_units":
        return current
    dpi = (doc["drawing"].get("image") or {}).get("dpi")
    note = scale_note(doc.get("texts", []))
    if note and note[1].get("role") == "other":
        note[1]["role"] = "scale_note"
    if note and dpi:
        denom, t = note
        return {"m_per_px": 0.0254 / dpi * denom, "source": "scale_note", "confidence": SCALE_NOTE_CONF,
                "note": f"'{t['text']}' ({t['id']}) at {dpi} dpi"}  # fmt: skip
    dw = door_width(doc)
    if dw:
        px, n = dw
        if px > 0:
            extra = f"; scale note '{note[1]['text']}' seen but the image has no dpi" if note else ""
            return {"m_per_px": round(DOOR_WIDTH_M / px, 6), "source": "door_width", "confidence": DOOR_CONF,
                    "note": f"median door {px:.0f} px over {n} doors = {DOOR_WIDTH_M} m{extra}"}  # fmt: skip
    return {"m_per_px": None, "source": "none", "confidence": 0.0,
            "note": "no vector units, no scale note with a known dpi, fewer than "
                    f"{MIN_DOORS} doors"}  # fmt: skip
