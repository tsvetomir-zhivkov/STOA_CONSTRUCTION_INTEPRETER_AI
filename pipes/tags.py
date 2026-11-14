"""Step 10: text roles and tag attachment by proximity (equipment/valve ids, line numbers, pipe sizes).

  python -m pipes.tags <pipe.json> [-o out.pipe.json]      # re-tag a traced sheet in place (no re-trace)

Roles come from regexes on the normalised OCR text (O/0 left alone: only the separators are repaired, 'V.100' and
'PV=10014' read as 'V-100', 'PV-10014'). Then, after Gao et al.:
  equipment_tag  -> nearest symbol box, within 2x its size (text inside the box wins)
  line_tag, diameter -> nearest pipe by point-to-polyline distance from the text centre, within 3x the text height,
                   preferring a pipe parallel to the text baseline
  instrument code (TIC, LSH, ...) + loop number below/above it -> one tag 'TIC-203'
A line tag is then carried along its run through small in-line symbols that have exactly two pipes (valves), never
through tees, junctions or large equipment, and never over a pipe that has its own different tag. Carried tags are
marked: edge.provenance.evidence_ids = [text id, source edge id], confidence x0.8.
A size written in the line number ('4"-S1-P-10014') also gives the pipe's nominal size.
Coverage: `tags` is analysed only when every tag-like text found a home, else partial with those text ids;
`diameters` is partial with the pipes that have no size.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

EXT = "ext_tags"
MIN_TEXT_SCORE = 0.5
EQUIP_FACTOR = 2.0  # symbol: gap from the text box to the symbol box <= this x the symbol's longest side
LINE_FACTOR = 3.0  # pipe: distance from the text centre to the polyline <= this x the text height
NOT_PARALLEL_DEG = 30
SMALL_SYMBOL = (
    1.5  # a symbol is 'small' (a valve, not a pump or vessel) if its longest side <= this x the median
)
STRUCTURAL = {"tee", "junction", "elbow", "cross", "open_end"}

# nominal pipe size (inch) -> DN
NPS_DN = {0.5: 15, 0.75: 20, 1: 25, 1.25: 32, 1.5: 40, 2: 50, 2.5: 65, 3: 80, 4: 100, 5: 125, 6: 150, 8: 200,
          10: 250, 12: 300, 14: 350, 16: 400, 18: 450, 20: 500, 24: 600}  # fmt: skip

INSTRUMENT_CODES = {
    "FIC", "FI", "FT", "FE", "FV", "FC", "FCV", "FAL", "FAH", "LIC", "LI", "LT", "LC", "LV", "LSH", "LSL", "LAH",
    "LAL", "LSHH", "LSLL", "PIC", "PI", "PT", "PC", "PV", "PCV", "PSV", "PSH", "PSL", "PAH", "PAL", "PDI", "PDT",
    "TIC", "TI", "TT", "TC", "TV", "TCV", "TSH", "TSL", "TAH", "TAL", "TE", "AIC", "AI", "AT", "AE", "XV", "XA",
    "YI", "YA", "YQI", "HS", "HV", "HIC", "SIC", "SI", "ST", "WI", "WT", "QI", "QT",
}  # fmt: skip

_DIAM_IN = re.compile(r'^(\d{1,2}(?:\.\d)?)"$')
_DIAM_DN = re.compile(r"^DN(\d{2,3})$")
_LINE = re.compile(r'^(\d{1,3}(?:\.\d)?"?)((?:-[A-Z0-9]{1,6}){2,5})$')
_EQUIP = re.compile(r"^(?:\d{1,3}-)?(?:[A-Z]{1,3}-\d{1,5}|[A-Z]{1,3}\d{2,5})[A-Z]?(?:/[A-Z])?$")
_NUM = re.compile(r"^\d{2,5}$")


def normalise(raw: str) -> str:
    t = raw.strip().upper()
    for a, b in (("”", '"'), ("“", '"'), ("''", '"'), ("″", '"'), ("–", "-"), ("—", "-")):
        t = t.replace(a, b)
    t = re.sub(r"\s+", "", t)
    return re.sub(r"(?<=[A-Z])[.=](?=\d)", "-", t)  # V.100 -> V-100, PV=10014 -> PV-10014


def size_value(raw: str) -> dict:
    """Nominal size from '4"' / 'DN100' -> schema size {raw_text, dn_mm, ...}."""
    t = normalise(raw)
    m = _DIAM_IN.match(t) or re.match(r'^(\d{1,3}(?:\.\d)?)"?$', t)
    if m and not t.startswith("DN"):
        inch = float(m.group(1))
        return {"raw_text": raw, "dn_mm": NPS_DN.get(inch), "outer_mm": None, "wall_mm": None}
    m = _DIAM_DN.match(t)
    return {"raw_text": raw, "dn_mm": float(m.group(1)) if m else None, "outer_mm": None, "wall_mm": None}


def text_value(raw: str) -> dict | None:
    """Schema texts[].value for a size label: {'number': 4.0, 'unit': 'inch'} or {'number': 100.0, 'unit': 'dn'}."""
    t = normalise(raw)
    m = _DIAM_IN.match(t)
    if m:
        return {"number": float(m.group(1)), "unit": "inch"}
    m = _DIAM_DN.match(t)
    return {"number": float(m.group(1)), "unit": "dn"} if m else None


def classify(raw: str) -> dict:
    """{'role': ..., 'canon': normalised tag or None, 'size_raw': size part of a line number or None}."""
    t = normalise(raw)
    if _DIAM_IN.match(t) or _DIAM_DN.match(t):
        return {"role": "diameter", "canon": t, "size_raw": raw}
    m = _LINE.match(t)
    if m and re.search(r"[A-Z]", m.group(2)) and re.search(r"\d", m.group(2)):
        return {"role": "line_tag", "canon": t, "size_raw": m.group(1)}
    if _EQUIP.match(t):
        return {"role": "equipment_tag", "canon": t, "size_raw": None}
    if t in INSTRUMENT_CODES:
        return {"role": "equipment_tag", "canon": t, "size_raw": None, "instrument": True}
    return {"role": "unknown", "canon": None, "size_raw": None}


# ---------------------------------------------------------------- geometry


def _rect_gap(a, b) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return math.hypot(dx, dy)


def _centre(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _seg_dist(p, a, b) -> tuple[float, float]:
    """(distance from p to segment ab, segment angle in degrees mod 180)."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    ln2 = dx * dx + dy * dy
    t = 0.0 if ln2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ln2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)), math.degrees(math.atan2(dy, dx)) % 180


def _poly_dist(p, poly) -> tuple[float, float]:
    best = (math.inf, 0.0)
    for a, b in zip(poly, poly[1:], strict=False):
        d = _seg_dist(p, a, b)
        if d[0] < best[0]:
            best = d
    return best


def _text_height(t) -> float:
    w, h = t["bbox_px"][2] - t["bbox_px"][0], t["bbox_px"][3] - t["bbox_px"][1]
    return max(min(w, h), 4.0)


def _text_angle(t) -> float:
    return 90.0 if t.get("rotation_deg") == 90 else 0.0


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180
    return min(d, 180 - d)


# ---------------------------------------------------------------- attachment


def _reset(doc: dict) -> None:
    for n in doc["nodes"]:
        n["tag"], n["tag_text_id"] = None, None
    for e in doc["edges"]:
        e["line_tag"], e["line_tag_text_id"] = None, None
        e["provenance"]["evidence_ids"] = [x for x in e["provenance"].get("evidence_ids", []) if not x.startswith("text_")
                                           and not x.startswith("pipe_")]  # fmt: skip
        if e["diameter"]["source"] == "label":
            e["diameter"] = {"value": None, "source": "unknown", "text_id": None, "confidence": 0.0}
    for t in doc["texts"]:
        t["role"], t["value"], t["attached_to"] = "unknown", None, []
    doc["coverage"] = [c for c in doc["coverage"] if c["category"] not in ("tags", "diameters")]


def _nearest_edge(t, edges):
    c = _centre(t["bbox_px"])
    th = _text_height(t)
    best = None
    for e in edges:
        d, ang = _poly_dist(c, e["polyline_px"])
        score = d + (1.5 * th if _angle_diff(ang, _text_angle(t)) > NOT_PARALLEL_DEG else 0.0)
        if d <= LINE_FACTOR * th and (best is None or score < best[0]):
            best = (score, d, e)
    return best


def attach_tags(doc: dict) -> dict:
    """Mutates a pipe doc (before finalize) and returns counts. Idempotent."""
    _reset(doc)
    if not any(x["id"] == EXT for x in doc["extractors"]):
        doc["extractors"].append({"id": EXT, "kind": "geometry_code", "name": "tag proximity rules (pipes.tags)",
                                  "version": None, "run_at": None, "cost_usd": 0.0})  # fmt: skip
    texts = {t["id"]: t for t in doc["texts"]}
    info: dict[str, dict] = {}
    for t in doc["texts"]:
        if t["provenance"]["confidence"] < MIN_TEXT_SCORE:
            continue
        c = classify(t["raw_text"])
        if c["role"] != "unknown":
            info[t["id"]] = c
            t["role"] = c["role"]
            if c["role"] == "diameter":
                t["value"] = text_value(t["raw_text"])
    # instrument code + loop number -> one tag
    nums = [
        t
        for t in doc["texts"]
        if _NUM.match(normalise(t["raw_text"])) and t["provenance"]["confidence"] >= MIN_TEXT_SCORE
    ]
    used_nums: set[str] = set()
    for tid, c in list(info.items()):
        if not c.get("instrument"):
            continue
        t = texts[tid]
        cc, th = _centre(t["bbox_px"]), _text_height(t)
        cand = [
            (math.dist(cc, _centre(n["bbox_px"])), n) for n in nums
            if n["id"] not in used_nums and abs(_centre(n["bbox_px"])[0] - cc[0]) <= 2.5 * th * 2
            and 0 < math.dist(cc, _centre(n["bbox_px"])) <= 4 * th
        ]  # fmt: skip
        if cand:
            _, n = min(cand, key=lambda x: x[0])
            used_nums.add(n["id"])
            c["canon"] = f"{c['canon']}-{normalise(n['raw_text'])}"
            c["extra_text"] = n["id"]
            n["role"] = "equipment_tag"

    syms = [
        n
        for n in doc["nodes"]
        if n["type"] not in STRUCTURAL and n["provenance"].get("status", "active") == "active"
    ]
    edges = [e for e in doc["edges"] if e["provenance"].get("status", "active") == "active"]
    node_best: dict[str, float] = {}
    stats = {
        "equipment_tags": 0,
        "line_tags": 0,
        "diameters": 0,
        "propagated": 0,
        "conflicts": 0,
        "unattached": 0,
    }
    assoc_conf: list[float] = []

    for tid, c in info.items():
        t = texts[tid]
        if c["role"] != "equipment_tag":
            continue
        tb = t["bbox_px"]
        best = None
        for n in syms:
            nb = n["bbox_px"]
            gap = _rect_gap(tb, nb)
            lim = EQUIP_FACTOR * max(nb[2] - nb[0], nb[3] - nb[1])
            if gap <= lim:
                key = (gap, (nb[2] - nb[0]) * (nb[3] - nb[1]))
                if best is None or key < best[0]:
                    best = (key, n, lim)
        if not best:
            continue
        (gap, _), n, lim = best
        conf = round(t["provenance"]["confidence"] * max(0.5, 1 - gap / lim * 0.5), 4)
        t["attached_to"] = [n["id"]]
        if c.get("extra_text"):
            texts[c["extra_text"]]["attached_to"] = [n["id"]]
        if conf > node_best.get(n["id"], 0):
            node_best[n["id"]] = conf
            n["tag"], n["tag_text_id"] = c["canon"], tid
    stats["equipment_tags"] = len(node_best)
    assoc_conf += list(node_best.values())

    line_best: dict[str, float] = {}
    for tid, c in info.items():
        t = texts[tid]
        if c["role"] not in ("line_tag", "diameter"):
            continue
        hit = _nearest_edge(t, edges)
        if not hit:
            continue
        _, d, e = hit
        conf = round(
            t["provenance"]["confidence"] * max(0.5, 1 - d / (LINE_FACTOR * _text_height(t)) * 0.5), 4
        )
        t["attached_to"] = [e["id"]]
        if c["role"] == "line_tag":
            if e["line_tag"] and e["line_tag"] != c["canon"]:
                stats["conflicts"] += 1
                if conf <= line_best[e["id"]]:
                    continue
            line_best[e["id"]] = conf
            e["line_tag"], e["line_tag_text_id"] = c["canon"], tid
            e["provenance"]["evidence_ids"] = [*e["provenance"].get("evidence_ids", []), tid]
        size = c["size_raw"]
        if size and e["diameter"]["value"] is None:
            e["diameter"] = {"value": size_value(size), "source": "label", "text_id": tid, "confidence": conf}
            stats["diameters"] += 1
    stats["line_tags"] = len(line_best)
    assoc_conf += list(line_best.values())

    # carry line tags along the run through small two-pipe symbols (valves)
    inc: dict[str, list[dict]] = {}
    for e in edges:
        for end in e["ends"]:
            if end["node_id"]:
                inc.setdefault(end["node_id"], []).append(e)
    sizes = [max(n["bbox_px"][2] - n["bbox_px"][0], n["bbox_px"][3] - n["bbox_px"][1]) for n in syms]
    small_max = SMALL_SYMBOL * statistics.median(sizes) if sizes else 0
    small = {
        n["id"] for n in syms
        if n.get("subtype") != "equipment" and max(n["bbox_px"][2] - n["bbox_px"][0], n["bbox_px"][3] - n["bbox_px"][1]) <= small_max
    }  # fmt: skip
    queue = [e for e in edges if e["line_tag"] and e["id"] in line_best]
    while queue:
        e = queue.pop()
        for end in e["ends"]:
            n = end["node_id"]
            if n not in small or len(inc.get(n, [])) != 2:
                continue
            other = next((x for x in inc[n] if x["id"] != e["id"]), None)
            if other is None or other["line_tag"]:
                continue
            other["line_tag"], other["line_tag_text_id"] = e["line_tag"], e["line_tag_text_id"]
            other["provenance"]["evidence_ids"] = [
                *other["provenance"].get("evidence_ids", []),
                e["line_tag_text_id"],
                e["id"],
            ]
            other["provenance"]["confidence"] = round(other["provenance"]["confidence"] * 0.8, 4)
            if other["diameter"]["value"] is None and e["diameter"]["source"] == "label":
                other["diameter"] = {
                    **e["diameter"],
                    "source": "propagated",
                    "confidence": round(e["diameter"]["confidence"] * 0.8, 4),
                }
            stats["propagated"] += 1
            queue.append(other)

    tag_like = [t for t in doc["texts"] if t["role"] in ("line_tag", "equipment_tag")]
    unattached = [t["id"] for t in tag_like if not t["attached_to"]]
    stats["unattached"] = len(unattached)
    ocr_mean = (
        statistics.fmean([t["provenance"]["confidence"] for t in doc["texts"]]) if doc["texts"] else 0.0
    )
    conf = statistics.fmean(assoc_conf) if assoc_conf else ocr_mean
    cid = len(doc["coverage"])
    doc["coverage"].append({
        "id": f"cov_{cid + 1}", "region": "page", "category": "tags",
        "status": "partial" if unattached else "analysed", "confidence": round(conf, 4), "extractor_id": EXT,
        "element_ids": unattached,
        "note": (f"{stats['equipment_tags']} symbol tag(s), {stats['line_tags']} line tag(s) read directly, "
                 f"{stats['propagated']} carried along runs; {len(unattached)} tag-like text(s) not attached"
                 if tag_like else "no tag-like text found on this sheet"),
    })  # fmt: skip
    no_size = [e["id"] for e in edges if e["diameter"]["value"] is None]
    if edges:
        doc["coverage"].append({
            "id": f"cov_{cid + 2}", "region": "page", "category": "diameters",
            "status": "partial" if no_size else "analysed", "confidence": round(conf, 4), "extractor_id": EXT,
            "element_ids": no_size, "note": f"{len(edges) - len(no_size)} of {len(edges)} pipes have a size",
        })  # fmt: skip
    return stats


def main(argv: list[str] | None = None) -> int:
    from pipes.write import finalize

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m pipes.tags", description=__doc__)
    ap.add_argument("pipe_json")
    ap.add_argument("-o", "--out", help="default: overwrite the input")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.pipe_json).read_text(encoding="utf-8"))
    stats = attach_tags(doc)
    Path(a.out or a.pipe_json).write_text(
        json.dumps(finalize(doc), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
