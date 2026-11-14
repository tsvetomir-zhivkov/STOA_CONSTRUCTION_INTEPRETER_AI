"""Map model coordinates (metres) onto the pixels of the page the question asks about.

Anchors are labels that appear both in the model and in the PDF text layer (room numbers are
unique per floor). An affine transform model -> page pixels is fitted by least squares; its
residual must stay under MAX_RESIDUAL_PX or no pixel geometry is written.

Pixels are pixels of the ORIGINAL page rendered at drawing.image.dpi (router.PDF_DPI).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

MIN_ANCHORS = 3
MAX_RESIDUAL_PX = 2.0
TEXT_MATCH_PX = 6.0  # a PDF text line this close to the predicted position is the same label


def fit_affine(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares 2x3 affine with dst = M @ [x, y, 1]. Returns (M, per-anchor residuals)."""
    X = np.column_stack([src, np.ones(len(src))])
    sol, *_ = np.linalg.lstsq(X, dst, rcond=None)
    M = sol.T
    residuals = np.linalg.norm(X @ sol - dst, axis=1)
    return M, residuals


def apply(M: np.ndarray, pts) -> list[list[float]]:
    if pts is None:
        return None
    a = np.asarray(pts, dtype=float)[:, :2]
    out = a @ M[:, :2].T + M[:, 2]
    return [[round(float(x), 2), round(float(y), 2)] for x, y in out]


def _bbox(poly_px) -> list[float] | None:
    if not poly_px:
        return None
    xs, ys = [p[0] for p in poly_px], [p[1] for p in poly_px]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def pdf_lines(pdf: Path, page_index: int, dpi: float) -> list[dict]:
    """Text lines of one page: {text, bbox_px, centre_px, baseline_px}, pixels at dpi.

    baseline_px is the y of the first span's baseline: the point SVG <text y> refers to.
    """
    import pymupdf

    k = dpi / 72.0
    out = []
    with pymupdf.open(pdf) as doc:
        page = doc[page_index]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = _norm("".join(span["text"] for span in line["spans"]))
                if not text:
                    continue
                x0, y0, x1, y1 = (v * k for v in line["bbox"])
                out.append(
                    {
                        "text": text,
                        "bbox_px": [x0, y0, x1, y1],
                        "centre_px": ((x0 + x1) / 2, (y0 + y1) / 2),
                        "baseline_px": line["spans"][0]["origin"][1] * k,
                    }
                )
    return out


def svg_anchor_on_page(line: dict, anchor: str) -> tuple[float, float]:
    """The page point that corresponds to an SVG <text x y>: text-anchor x on the baseline y."""
    x0, _, x1, _ = line["bbox_px"]
    x = {"start": x0, "end": x1}.get(anchor, (x0 + x1) / 2)
    return x, line["baseline_px"]


def fit_from_labels(model_labels: list[tuple[str, float, float]], lines: list[dict]):
    """Fit model -> page from labels unique on both sides. Returns (M, residuals, anchor texts) or None."""
    model_count: dict[str, int] = {}
    for t, _, _ in model_labels:
        model_count[t] = model_count.get(t, 0) + 1
    page_by_text: dict[str, list[dict]] = {}
    for ln in lines:
        page_by_text.setdefault(ln["text"], []).append(ln)
    src, dst, names = [], [], []
    for t, x, y in model_labels:
        if model_count[t] == 1 and len(page_by_text.get(t, [])) == 1:
            src.append((x, y))
            dst.append(page_by_text[t][0]["centre_px"])
            names.append(t)
    if len(src) < MIN_ANCHORS:
        return None
    M, res = fit_affine(np.array(src), np.array(dst))
    return M, res, names


RANSAC_MAX_TRIPLES = 3000


def fit_robust(src: np.ndarray, dst: np.ndarray, max_residual: float = MAX_RESIDUAL_PX):
    """Affine fit that ignores anchors drawn somewhere else on one of the two renderings.

    For anchors matched by label text, where a few equal strings sit in different places (scale bar,
    footer). RANSAC: exact affines from anchor triples (all triples when few, a fixed-seed sample
    otherwise), keep the one most anchors agree with within max_residual, refit on those inliers.
    Returns (M, residuals of the kept anchors, kept index array).
    """
    from itertools import combinations
    from math import comb

    n = len(src)
    X = np.column_stack([src, np.ones(n)])
    if comb(n, 3) <= RANSAC_MAX_TRIPLES:
        triples = list(combinations(range(n), 3))
    else:
        rng = np.random.default_rng(0)
        triples = [tuple(rng.choice(n, 3, replace=False)) for _ in range(RANSAC_MAX_TRIPLES)]
    best = np.arange(n)
    best_count = -1
    for t in triples:
        A = X[list(t)]
        if abs(np.linalg.det(A)) < 1e-9:  # collinear anchors
            continue
        sol = np.linalg.solve(A, dst[list(t)])
        inliers = np.flatnonzero(np.linalg.norm(X @ sol - dst, axis=1) <= max_residual)
        if len(inliers) > best_count:
            best, best_count = inliers, len(inliers)
    if best_count < MIN_ANCHORS:
        best = np.arange(n)
    M, res = fit_affine(src[best], dst[best])
    return M, res, best


def _apply_geometry(doc: dict, M: np.ndarray, px_per_m: float) -> None:
    scale = doc["drawing"].get("scale") or {"source": "vector_units", "confidence": 1.0}
    scale["m_per_px"] = round(1.0 / px_per_m, 6)
    doc["drawing"]["scale"] = scale
    for key in ("rooms", "units", "walls"):
        for e in doc.get(key, []):
            e["polygon_px"] = apply(M, e.get("polygon_m"))
    for key in ("openings", "objects"):
        for e in doc.get(key, []):
            e["polygon_px"] = apply(M, e.get("polygon_m"))
            e["bbox_px"] = _bbox(e["polygon_px"])


# ------------------------------------------------------------------ IFC plans rendered to SVG + PDF

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def svg_spaces_and_texts(svg: Path) -> tuple[dict, list[tuple[str, float, float, str]]]:
    """IfcSpace outlines by GlobalId (group data-guid) and every <text> as (text, x, y, text-anchor)."""
    import xml.etree.ElementTree as ET

    spaces: dict[str, list[tuple[float, float]]] = {}
    texts: list[tuple[str, float, float, str]] = []
    for el in ET.parse(svg).iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "g" and el.get("class") == "IfcSpace" and el.get("data-guid"):
            for sub in el.iter():
                if sub.tag.rsplit("}", 1)[-1] == "path" and "M" in (sub.get("d") or ""):
                    first = sub.get("d").split("M")[1]  # outer ring = first subpath
                    nums = [float(x) for x in _NUM.findall(first)]
                    pts = list(zip(nums[0::2], nums[1::2], strict=False))
                    if len(pts) >= 3:
                        spaces[el.get("data-guid")] = pts
                    break
        elif tag == "text":
            t = _norm("".join(el.itertext()))
            if t and el.get("x") and el.get("y"):
                texts.append((t, float(el.get("x")), float(el.get("y")), el.get("text-anchor") or "start"))
    return spaces, texts


def map_ifc_document(doc: dict, pdf: Path, svg: Path, page_index: int = 0) -> dict:
    """Model metres -> pixels of a plan rendered from the model, via its SVG sibling.

    model -> svg: centroid of each room footprint vs centroid of the SVG outline with the same
    GlobalId (exact pairs). svg -> page: the text labels both renderings share (robust fit).
    """
    from shapely.geometry import Polygon

    dpi = (doc["drawing"].get("image") or {}).get("dpi")
    if not dpi:
        return {"ok": False, "reason": "page has no fixed dpi"}
    spaces, svg_texts = svg_spaces_and_texts(svg)
    src, dst = [], []
    for r in doc["rooms"]:
        gid = r["provenance"]["source_ref"]
        if r.get("polygon_m") and gid in spaces:
            src.append(Polygon(r["polygon_m"]).centroid.coords[0])
            dst.append(Polygon(spaces[gid]).centroid.coords[0])
    if len(src) < MIN_ANCHORS:
        return {"ok": False, "reason": f"fewer than {MIN_ANCHORS} rooms matched by GlobalId in {svg.name}"}
    M1, res1 = fit_affine(np.array(src), np.array(dst))

    lines = pdf_lines(pdf, page_index, dpi)
    count: dict[str, int] = {}
    for t, *_ in svg_texts:
        count[t] = count.get(t, 0) + 1
    by_text: dict[str, list[dict]] = {}
    for ln in lines:
        by_text.setdefault(ln["text"], []).append(ln)
    pairs = [
        ((x, y), svg_anchor_on_page(by_text[t][0], anchor))
        for t, x, y, anchor in svg_texts
        if count[t] == 1 and len(by_text.get(t, [])) == 1
    ]
    if len(pairs) < MIN_ANCHORS:
        return {
            "ok": False,
            "reason": f"fewer than {MIN_ANCHORS} shared labels between {svg.name} and the page",
        }
    M2, res2, kept = fit_robust(np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs]))

    H1 = np.vstack([M1, [0, 0, 1]])
    M = M2 @ H1
    px_per_svg = float(np.sqrt(abs(np.linalg.det(M2[:, :2]))))
    res1_px = float(res1.max()) * px_per_svg
    total = res1_px + float(res2.max())
    summary = {
        "ok": bool(total <= MAX_RESIDUAL_PX),
        "guid_anchors": len(src),
        "label_anchors": f"{len(kept)}/{len(pairs)}",
        "model_to_svg_max_px": round(res1_px, 3),
        "svg_to_page_max_px": round(float(res2.max()), 3),
        "max_residual_px": round(total, 3),
        "px_per_m": round(float(np.sqrt(abs(np.linalg.det(M[:, :2])))), 4),
    }
    doc["drawing"]["transforms"].append(
        {
            "kind": "affine",
            "from": "model_m",
            "to": f"page_px@{dpi}dpi",
            "matrix": [[round(float(v), 6) for v in row] for row in M[:2]],
            "residual_px": summary["max_residual_px"],
            "note": f"via {svg.name}: {len(src)} GlobalId anchors model->svg, "
            f"{len(kept)}/{len(pairs)} label anchors svg->page",
        }
    )
    if not summary["ok"]:
        summary["reason"] = f"max residual {summary['max_residual_px']} px > {MAX_RESIDUAL_PX}"
        return summary
    _apply_geometry(doc, M[:2], summary["px_per_m"])
    return summary


def map_document(doc: dict, pdf: Path, page_index: int = 0) -> dict:
    """Fill polygon_px / bbox_px from polygon_m using room-number anchors. Returns a summary dict."""
    dpi = (doc["drawing"].get("image") or {}).get("dpi")
    if not dpi:
        return {"ok": False, "reason": "page has no fixed dpi"}
    lines = pdf_lines(pdf, page_index, dpi)
    labels = [
        (_norm(t["text"]), t["properties"]["x_m"], t["properties"]["y_m"])
        for t in doc["texts"]
        if t.get("role") == "room_number" and "x_m" in t.get("properties", {})
    ]
    fit = fit_from_labels(labels, lines)
    if fit is None:
        return {"ok": False, "reason": f"fewer than {MIN_ANCHORS} unique room numbers found on the page"}
    M, res, names = fit
    summary = {
        "ok": bool(res.max() <= MAX_RESIDUAL_PX),
        "anchors": len(names),
        "max_residual_px": round(float(res.max()), 3),
        "rms_residual_px": round(float(np.sqrt((res**2).mean())), 3),
        "px_per_m": round(float(np.sqrt(abs(np.linalg.det(M[:, :2])))), 4),
    }
    doc["drawing"]["transforms"].append(
        {
            "kind": "affine",
            "from": "model_m",
            "to": f"page_px@{dpi}dpi",
            "matrix": [[round(float(v), 6) for v in row] for row in M],
            "residual_px": summary["max_residual_px"],
            "note": f"{len(names)} room-number anchors, rms {summary['rms_residual_px']} px",
        }
    )
    if not summary["ok"]:
        summary["reason"] = f"max residual {summary['max_residual_px']} px > {MAX_RESIDUAL_PX}"
        return summary

    _apply_geometry(doc, M, summary["px_per_m"])

    by_text: dict[str, list[dict]] = {}
    for ln in lines:
        by_text.setdefault(ln["text"], []).append(ln)
    for t in doc["texts"]:
        props = t.get("properties", {})
        if "x_m" not in props:
            continue
        ((px, py),) = apply(M, [[props["x_m"], props["y_m"]]])
        cands = [
            (np.hypot(ln["centre_px"][0] - px, ln["centre_px"][1] - py), ln)
            for ln in by_text.get(_norm(t["text"]), [])
        ]
        best = min(cands, key=lambda c: c[0], default=None)
        if best is not None and best[0] <= TEXT_MATCH_PX:
            t["bbox_px"] = [round(v, 2) for v in best[1]["bbox_px"]]
        else:  # not found on the page: estimate the box from the model text height
            h = props.get("height_m", 0.25) * summary["px_per_m"]
            w = 0.6 * h * len(t["text"])
            t["bbox_px"] = [
                round(px - w / 2, 2),
                round(py - h / 2, 2),
                round(px + w / 2, 2),
                round(py + h / 2, 2),
            ]
            t["flags"] = sorted(set(t.get("flags", [])) | {"bbox_estimated"})
    return summary
