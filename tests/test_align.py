"""Step 3 phase B: model metres -> PDF page pixels (affine on room-number anchors)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pymupdf
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from test_swiss_dxf import Plan  # noqa: E402

from translator import align, translate  # noqa: E402
from translator.config import data_root, questions_path  # noqa: E402

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)

# page points = TRUE @ [x_m, y_m, 1]: 1:100 on A4-ish, y flipped (PDF y grows downwards)
TRUE_PT = np.array([[28.35, 0.0, 60.0], [0.0, -28.35, 700.0]])


def test_fit_affine_recovers_transform():
    src = np.array([[0, 0], [10, 0], [0, 5], [7, 3]], dtype=float)
    M_true = np.array([[2.0, 0.1, 5.0], [-0.1, -2.0, 300.0]])
    dst = src @ M_true[:, :2].T + M_true[:, 2]
    M, res = align.fit_affine(src, dst)
    assert np.allclose(M, M_true) and res.max() < 1e-9


def _pair(tmp_path: Path, numbers: dict[str, tuple[float, float]]) -> Path:
    """DXF rooms with these numbers, and a same-stem PDF with the numbers at TRUE_PT positions."""
    p = Plan()
    for number, (x, y) in numbers.items():
        p.room(x - 1, y - 1, x + 1, y + 1, name="Zimmer", number=number, area=4.0)
    p.save(tmp_path / "floor.dxf")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for number, (x, y) in numbers.items():
        # place each number centred on the model position of its DXF text
        # (Plan.room stacks name / number / area, so the number sits 0.25 m above the centre)
        u, v = TRUE_PT @ np.array([x, y + 0.25, 1.0])
        w = pymupdf.get_text_length(number, fontsize=8)
        page.insert_text((u - w / 2, v + 3), number, fontsize=8)
    doc.save(tmp_path / "floor.pdf")
    return tmp_path / "floor.pdf"


def test_translate_maps_rooms_onto_the_pdf_page(tmp_path):
    numbers = {"1.01": (2, 2), "1.02": (8, 2), "1.03": (2, 9), "1.04": (12, 14)}
    pdf = _pair(tmp_path, numbers)
    doc = translate(str(pdf), str(tmp_path))
    aff = [t for t in doc["drawings"][0]["transforms"] if t["kind"] == "affine"]
    assert len(aff) == 1 and aff[0]["residual_px"] < align.MAX_RESIDUAL_PX
    k = doc["drawings"][0]["image"]["dpi"] / 72
    assert doc["drawings"][0]["scale"]["m_per_px"] == pytest.approx(1 / (28.35 * k), rel=0.01)
    room = next(r for r in doc["rooms"] if r["number"] == "1.04")
    # the room's corner (11, 13) m must land where the true transform puts it
    expected = (TRUE_PT @ np.array([11, 13, 1.0])) * k
    got = min(room["polygon_px"], key=lambda p: np.hypot(p[0] - expected[0], p[1] - expected[1]))
    assert np.hypot(got[0] - expected[0], got[1] - expected[1]) < 3.0
    numbers_px = [t for t in doc["texts"] if t["role"] == "room_number"]
    assert all(t["bbox_px"] for t in numbers_px)


def test_too_few_anchors_leaves_pixels_empty(tmp_path):
    pdf = _pair(tmp_path, {"1.01": (2, 2), "1.02": (8, 2)})
    doc = translate(str(pdf), str(tmp_path))
    assert all(r["polygon_px"] is None for r in doc["rooms"])
    assert not doc["drawings"][0]["transforms"]
    assert "fewer than 3" in doc["history"][-1]["note"]


@needs_data
def test_real_floor_alignment_residual():
    doc = translate("data/swiss_dwellings/site1000_bldg2082_floor9385.pdf")
    aff = next(t for t in doc["drawings"][0]["transforms"] if t["kind"] == "affine")
    assert aff["residual_px"] < align.MAX_RESIDUAL_PX
    assert all(r["polygon_px"] for r in doc["rooms"])
    assert all(o["bbox_px"] for o in doc["openings"] + doc["objects"])
    # 1:200 at 150 dpi = 29.53 px per metre
    assert 1 / doc["drawings"][0]["scale"]["m_per_px"] == pytest.approx(150 / 0.0254 / 200, rel=0.005)
