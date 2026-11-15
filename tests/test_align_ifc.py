"""Step 4 phase C: IFC storey -> rendered plan page, via the plan's SVG sibling."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pymupdf
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from translator import align, translate  # noqa: E402
from translator.config import INDEX_DIR, data_root, questions_path  # noqa: E402
from translator.extract import ifc  # noqa: E402

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)

# model metres -> svg units (mm on the page): 1:100, rotated 30 degrees, y flipped, shifted
_A = np.radians(30)
MODEL_TO_SVG = np.array(
    [[10 * np.cos(_A), -10 * np.sin(_A), 120.0], [-10 * np.sin(_A), -10 * np.cos(_A), 150.0]]
)
PT_PER_SVG = 72 / 25.4  # svg canvas in mm, pdf page in points


def test_fit_robust_drops_a_misplaced_anchor():
    src = np.array([[0, 0], [10, 0], [0, 10], [10, 10], [5, 5]], dtype=float)
    dst = src * 2 + 3
    dst[4] += [40, -25]  # one label matched by text but drawn elsewhere
    M, res, kept = align.fit_robust(src, dst)
    assert list(kept) == [0, 1, 2, 3] and res.max() < 1e-9
    assert np.allclose(M, [[2, 0, 3], [0, 2, 3]])


def _plan(tmp_path: Path) -> tuple[dict, Path, Path]:
    """A one-storey doc with 4 rooms, and an SVG + PDF rendering of it through MODEL_TO_SVG."""
    rooms, groups, texts = [], [], []
    for i, (x, y) in enumerate([(0, 0), (6, 0), (0, 5), (6, 5)]):
        poly = [[x, y], [x + 5, y], [x + 5, y + 4], [x, y + 4]]
        gid = f"GUID{i}"
        rooms.append({"provenance": {"source_ref": gid}, "polygon_m": poly, "polygon_px": None})
        svg_pts = [MODEL_TO_SVG @ [px, py, 1] for px, py in poly]
        d = "M" + " L".join(f"{u:.4f},{v:.4f}" for u, v in svg_pts) + " Z"
        groups.append(f'<g class="IfcSpace" data-guid="{gid}"><path d="{d}"/></g>')
        cx, cy = MODEL_TO_SVG @ [x + 2.5, y + 2, 1]
        texts.append((f"Room {i}", cx, cy))
    texts.append(("Plan title", 20.0, 280.0))
    svg_body = "".join(groups) + "".join(
        f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="middle" font-size="3">{t}</text>' for t, x, y in texts
    )
    svg = tmp_path / "m_plan-01-eg.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="300mm" height="300mm" viewBox="0 0 300 300">'
        f"{svg_body}</svg>",
        encoding="utf-8",
    )
    pdf = tmp_path / "m_plan-01-eg.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300 * PT_PER_SVG, height=300 * PT_PER_SVG)
    for t, x, y in texts + [("Decoy", 40.0, 40.0)]:
        if t == "Plan title":
            x, y = 200.0, 30.0  # the same string, drawn somewhere else: must be dropped
        w = pymupdf.get_text_length(t, fontsize=3 * PT_PER_SVG)
        page.insert_text((x * PT_PER_SVG - w / 2, y * PT_PER_SVG), t, fontsize=3 * PT_PER_SVG)
    doc.save(pdf)
    with pymupdf.open(pdf) as d:
        k = 150 / 72
        irect = (d[0].rect * pymupdf.Matrix(k, k)).irect
    model_doc = {
        "drawing": {
            "image": {"width_px": irect.width, "height_px": irect.height, "dpi": 150},
            "transforms": [],
        },
        "rooms": rooms,
        "walls": [],
        "openings": [],
        "objects": [],
    }
    # the svg path labels carry no room text of their own, so add the room labels to the svg once more
    return model_doc, pdf, svg


def test_map_ifc_document_recovers_the_transform(tmp_path):
    doc, pdf, svg = _plan(tmp_path)
    summary = align.map_ifc_document(doc, pdf, svg)
    assert summary["ok"], summary
    assert summary["model_to_svg_max_px"] < 0.01
    assert summary["label_anchors"] == "4/5"  # the misplaced title is dropped
    # a room corner lands where the true chain model -> svg -> page pixels puts it
    expected = (MODEL_TO_SVG @ [5, 4, 1]) * PT_PER_SVG * 150 / 72
    corner = min(
        doc["rooms"][0]["polygon_px"], key=lambda p: np.hypot(p[0] - expected[0], p[1] - expected[1])
    )
    assert np.hypot(corner[0] - expected[0], corner[1] - expected[1]) < 2.0
    assert 1 / doc["drawing"]["scale"]["m_per_px"] == pytest.approx(10 * PT_PER_SVG * 150 / 72, rel=0.01)


def test_map_ifc_document_needs_guid_matches(tmp_path):
    doc, pdf, svg = _plan(tmp_path)
    for r in doc["rooms"]:
        r["provenance"]["source_ref"] = "unknown"
    summary = align.map_ifc_document(doc, pdf, svg)
    assert not summary["ok"] and "GlobalId" in summary["reason"]
    assert all(r["polygon_px"] is None for r in doc["rooms"])


@needs_data
@pytest.mark.parametrize(
    "path",
    [
        "data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-04-4-korrus.pdf",
        "data/ifc_models/bsc-molio_molio-with-uris_plan-02-klder.pdf",
    ],
)
def test_real_rendered_plans_align(path, monkeypatch):
    monkeypatch.setattr(ifc, "CACHE_DIR", INDEX_DIR / "_raw" / "ifc")  # reuse the real cache
    from translator.building import page_views
    doc = next(v for v in page_views(translate(path)) if v["drawing"]["file"] == path)
    aff = next(t for t in doc["drawing"]["transforms"] if t["kind"] == "affine")
    assert aff["residual_px"] < align.MAX_RESIDUAL_PX
    assert all(r["polygon_px"] for r in doc["rooms"] if r["polygon_m"])
    assert 1 / doc["drawing"]["scale"]["m_per_px"] == pytest.approx(150 / 0.0254 / 100, rel=0.005)  # 1:100
