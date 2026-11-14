"""Step 3: Swiss dwellings DXF extractor (units in metres, $INSUNITS=6).

Layers and the rules used (each one checked against the drawing conventions, not per file):
  ROOM_OUTLINE  closed polylines = room polygons; pairs of 2-point segments crossing inside a
                polygon (an X) mark a shaft/void.
  ROOM_TEXT     stacked text blocks [unit label] / name / number / "16.78 m²". A room number owns
                the nearest name just above it and the nearest area just below it.
  UNIT_OUTLINE  closed polylines = units. A unit is residential when its label is an apartment
                label (Whg. A, App. B, ...). Unlabelled outlines touching a unit are parts of it.
  DOOR          a closed leaf ring = one door; ARCs and 2-point lines are its swing(s).
  WINDOW        a closed frame = one window; open polylines inside are glazing lines.
  FIXTURE       anchored on primitives: a rounded-rectangle curve = bathtub, an elliptic bowl
                with a drain inside = washbasin, without a drain = toilet, a drain outside any
                curve = shower tray. Hob circles = kitchen counter, a rect with tread lines = stair.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from translator import glossary

EXTRACTOR = "swiss_dxf"
REQUIRED_LAYERS = {"ROOM_OUTLINE", "ROOM_TEXT"}

AREA_TOL = 0.02  # written vs computed area: flag check_this above 2% (vector source)
CURVE_MIN_VERTICES = 12  # a smooth fixture outline (bowl, tub) has many vertices
TUB_MIN_FILL = 0.9  # curve area / its min. rectangle: ellipse = pi/4 = 0.785, rounded rect ~ 1.0
DRAIN_MAX_R = 0.05  # m
HOB_MAX_R = 0.12  # m
STAIR_MIN_LINES = 3
TEXT_BLOCK_DX = 1.0  # m, max horizontal offset between lines of one text block
TEXT_BLOCK_DY = 2.5  # line heights, max vertical gap between lines of one text block
TEXT_SNAP = 0.5  # m, a text outside every room polygon snaps to one this close
TOUCH = 0.05  # m

_AREA_RE = re.compile(r"^\s*([0-9][0-9' ]*(?:[.,][0-9]+)?)\s*m(?:²|2|\^2)\s*$")
_NUMBER_RE = re.compile(r"^[A-Z]{0,2}\d+(?:\.\d+)+$")


# ------------------------------------------------------------------ geometry helpers


def _pts(e) -> list[tuple[float, float]]:
    return [(float(p[0]), float(p[1])) for p in e.get_points()]


def _is_ring(e, pts) -> bool:
    if len(pts) < 3:
        return False
    return bool(e.closed) or math.dist(pts[0], pts[-1]) < 1e-6


def _ring(pts) -> list[tuple[float, float]]:
    if math.dist(pts[0], pts[-1]) < 1e-6:
        pts = pts[:-1]
    return pts


def _poly(pts) -> Polygon:
    p = Polygon(_ring(pts))
    return p if p.is_valid else p.buffer(0)


def _coords(geom) -> list[list[float]]:
    if geom.geom_type != "Polygon":
        geom = geom.convex_hull
    return [[round(x, 4), round(y, 4)] for x, y in list(geom.exterior.coords)[:-1]]


def _sides(geom) -> tuple[float, float]:
    r = geom.minimum_rotated_rectangle
    if r.geom_type != "Polygon":
        return 0.0, geom.length
    xy = list(r.exterior.coords)
    a, b = math.dist(xy[0], xy[1]), math.dist(xy[1], xy[2])
    return min(a, b), max(a, b)


def _text_point(e) -> Point:
    """Insertion point, or the alignment point when the text is aligned (centre etc.)."""
    if (e.dxf.get("halign", 0) or e.dxf.get("valign", 0)) and e.dxf.hasattr("align_point"):
        p = e.dxf.align_point
    else:
        p = e.dxf.insert
    return Point(float(p[0]), float(p[1]))


def _prov(handle: str, layer: str, read_from: str, **details) -> dict:
    prov = {"extractor": EXTRACTOR, "source_ref": str(handle), "file": read_from, "layer": layer}
    if details:
        prov["details"] = details
    return prov


def parse_area(text: str) -> float | None:
    m = _AREA_RE.match(text)
    if not m:
        return None
    return float(m.group(1).replace("'", "").replace(" ", "").replace(",", "."))


# ------------------------------------------------------------------ texts


def _read_texts(msp) -> list[dict]:
    out = []
    for e in msp.query("TEXT MTEXT"):
        raw = e.dxf.text if e.dxftype() == "TEXT" else e.plain_text()
        text = raw.strip()
        if not text:
            continue
        kind = "area" if parse_area(text) is not None else "number" if _NUMBER_RE.match(text) else "word"
        out.append(
            {
                "handle": e.dxf.handle,
                "layer": e.dxf.layer,
                "text": text,
                "kind": kind,
                "pt": _text_point(e),
                "h": float(e.dxf.get("height", 0.25) or 0.25),
            }
        )
    return out


def _above(t: dict, ref: dict) -> float | None:
    """Vertical distance if t sits just above ref in the same block, else None."""
    dy = t["pt"].y - ref["pt"].y
    if abs(t["pt"].x - ref["pt"].x) <= TEXT_BLOCK_DX and 0 < dy <= TEXT_BLOCK_DY * max(ref["h"], t["h"]):
        return dy
    return None


def _blocks(texts: list[dict]) -> list[dict]:
    """One block per room number: number, name above it, area below it, unit label above the name."""
    room_texts = [t for t in texts if t["layer"] == "ROOM_TEXT"]
    words = [t for t in room_texts if t["kind"] == "word"]
    areas = [t for t in room_texts if t["kind"] == "area"]
    used: set[str] = set()
    blocks = []
    for num in sorted((t for t in room_texts if t["kind"] == "number"), key=lambda t: -t["pt"].y):
        name = min(
            (w for w in words if w["handle"] not in used and _above(w, num) is not None),
            key=lambda w: _above(w, num),
            default=None,
        )
        area = min(
            (a for a in areas if a["handle"] not in used and _above(num, a) is not None),
            key=lambda a: _above(num, a),
            default=None,
        )
        unit = None
        if name is not None:
            unit = min(
                (
                    w
                    for w in words
                    if w["handle"] not in used and w is not name and _above(w, name) is not None
                ),
                key=lambda w: _above(w, name),
                default=None,
            )
            # a lone apartment label directly above the number is a unit label, not a room name
            if unit is None and glossary.residential_unit_letter(name["text"]):
                unit, name = name, None
        for t in (num, name, area, unit):
            if t is not None:
                used.add(t["handle"])
        blocks.append({"number": num, "name": name, "area": area, "unit": unit})
    # words not in a numbered block: a name over an area (unnumbered room) or a lone unit label
    for w in sorted((w for w in words if w["handle"] not in used), key=lambda t: -t["pt"].y):
        if w["handle"] in used:
            continue
        if glossary.residential_unit_letter(w["text"]):
            blocks.append({"number": None, "name": None, "area": None, "unit": w})
            used.add(w["handle"])
            continue
        area = min(
            (a for a in areas if a["handle"] not in used and _above(w, a) is not None),
            key=lambda a: _above(w, a),
            default=None,
        )
        unit = min(
            (u for u in words if u["handle"] not in used and u is not w and _above(u, w) is not None),
            key=lambda u: _above(u, w),
            default=None,
        )
        for t in (w, area, unit):
            if t is not None:
                used.add(t["handle"])
        blocks.append({"number": None, "name": w, "area": area, "unit": unit})
    return blocks


def _anchor(block: dict) -> Point:
    for k in ("number", "name", "area", "unit"):
        if block[k] is not None:
            return block[k]["pt"]
    raise ValueError("empty block")


# ------------------------------------------------------------------ rooms


def _room_outlines(msp):
    polys, segments = [], []
    for e in msp.query('LWPOLYLINE[layer=="ROOM_OUTLINE"]'):
        pts = _pts(e)
        if _is_ring(e, pts) and len(_ring(pts)) >= 3:
            polys.append((e.dxf.handle, _poly(pts)))
        elif len(pts) >= 2:
            segments.append(LineString(pts))
    return polys, segments


def _void_handles(polys, segments) -> set[str]:
    """Polygons with two crossing segments inside them (an X marker) are shafts/voids."""
    out = set()
    for handle, poly in polys:
        inside = [s for s in segments if poly.buffer(TOUCH).contains(s)]
        if any(a.crosses(b) for i, a in enumerate(inside) for b in inside[i + 1 :]):
            out.add(handle)
    return out


def _owner(pt: Point, polys) -> tuple[str, Polygon] | None:
    """Smallest polygon containing pt, else the nearest within TEXT_SNAP."""
    inside = [(h, p) for h, p in polys if p.contains(pt)]
    if inside:
        return min(inside, key=lambda hp: hp[1].area)
    near = [(p.distance(pt), h, p) for h, p in polys]
    near = [x for x in near if x[0] <= TEXT_SNAP]
    if near:
        d, h, p = min(near, key=lambda x: x[0])
        return h, p
    return None


# ------------------------------------------------------------------ units


def _units(msp, blocks, read_from):
    outlines = [
        (e.dxf.handle, _poly(_pts(e)))
        for e in msp.query('LWPOLYLINE[layer=="UNIT_OUTLINE"]')
        if _is_ring(e, _pts(e))
    ]
    labels = defaultdict(list)  # outline handle -> unit-label texts
    for b in blocks:
        if b["unit"] is not None:
            own = _owner(b["unit"]["pt"], outlines)
            if own:
                labels[own[0]].append(b["unit"])
    units, parts = [], {}  # parts: outline handle -> unit id
    for h, poly in outlines:
        if not labels.get(h):
            continue
        t = labels[h][0]
        letter = glossary.residential_unit_letter(t["text"])
        uid = f"unit-{h}"
        flags = [] if letter else ["non_residential"]
        if len(labels[h]) > 1:
            flags.append("check_this")
        units.append(
            {
                "id": uid,
                "label": letter or t["text"],
                "label_raw": t["text"],
                "polygon_px": None,
                "polygon_m": _coords(poly),
                "area_m2": None,
                "confidence": 1.0,
                "provenance": _prov(h, "UNIT_OUTLINE", read_from, label_handle=t["handle"]),
                "status": "active",
                "flags": flags,
                "properties": {"outline_handles": [h]},
            }
        )
        parts[h] = uid
    # unlabelled outlines are their own unit, flagged (shared/other space, not part of an apartment)
    by_id = {u["id"]: u for u in units}
    geoms = dict(outlines)
    for h, poly in outlines:
        if h in parts:
            continue
        uid = f"unit-{h}"
        u = {
            "id": uid,
            "label": "",
            "label_raw": None,
            "polygon_px": None,
            "polygon_m": _coords(poly),
            "area_m2": None,
            "confidence": 0.5,
            "provenance": _prov(h, "UNIT_OUTLINE", read_from),
            "status": "active",
            "flags": ["unlabelled"],
            "properties": {"outline_handles": [h]},
        }
        units.append(u)
        by_id[uid] = u
        parts[h] = uid
    unit_geom = {uid: unary_union([geoms[h] for h, u in parts.items() if u == uid]) for uid in by_id}
    return units, unit_geom


# ------------------------------------------------------------------ doors and windows


def _openings(msp, read_from):
    out = []
    # doors: leaf rings; arcs and 2-point lines are swings
    leaves, swings = [], []
    for e in msp.query('LWPOLYLINE[layer=="DOOR"]'):
        pts = _pts(e)
        if _is_ring(e, pts) and len(_ring(pts)) >= 3:
            leaves.append((e.dxf.handle, _poly(pts)))
        else:
            swings.append((e.dxf.handle, LineString(pts) if len(pts) >= 2 else Point(pts[0])))
    for e in msp.query('ARC[layer=="DOOR"]'):
        c = e.dxf.center
        swings.append((e.dxf.handle, Point(float(c[0]), float(c[1]))))
    attached = defaultdict(list)
    for h, g in swings:
        if leaves:
            d, lh = min((lg.distance(g), lh) for lh, lg in leaves)
            if d <= TOUCH:
                attached[lh].append(h)
    arc_handles = {e.dxf.handle for e in msp.query('ARC[layer=="DOOR"]')}
    for h, poly in leaves:
        _, long_side = _sides(poly)
        n_arcs = sum(s in arc_handles for s in attached[h])
        out.append(
            {
                "id": f"door-{h}",
                "kind": "door",
                "bbox_px": None,
                "polygon_px": None,
                "polygon_m": _coords(poly),
                "width_m": round(long_side, 3),
                "confidence": 1.0,
                "provenance": _prov(h, "DOOR", read_from, swing_handles=attached[h], arcs=n_arcs),
                "status": "active",
                "properties": {"swings": n_arcs},
            }
        )
    # windows: closed frames; open polylines are glazing lines
    frames, glazing = [], []
    for e in msp.query('LWPOLYLINE[layer=="WINDOW"]'):
        pts = _pts(e)
        if _is_ring(e, pts) and len(_ring(pts)) >= 3:
            frames.append((e.dxf.handle, _poly(pts)))
        elif len(pts) >= 2:
            glazing.append((e.dxf.handle, LineString(pts)))
    lines_of = defaultdict(list)
    for h, g in glazing:
        if frames:
            d, fh = min((f.distance(g), fh) for fh, f in frames)
            if d <= TOUCH:
                lines_of[fh].append(h)
    for h, poly in frames:
        _, long_side = _sides(poly)
        out.append(
            {
                "id": f"window-{h}",
                "kind": "window",
                "bbox_px": None,
                "polygon_px": None,
                "polygon_m": _coords(poly),
                "width_m": round(long_side, 3),
                "confidence": 1.0,
                "provenance": _prov(h, "WINDOW", read_from, glazing_handles=lines_of[h]),
                "status": "active",
            }
        )
    return out


# ------------------------------------------------------------------ fixtures


def _fixtures(msp, read_from):
    curves, rects, lines, drains, hobs = [], [], [], [], []
    for e in msp.query('*[layer=="FIXTURE"]'):
        h = e.dxf.handle
        if e.dxftype() == "CIRCLE":
            c = Point(float(e.dxf.center[0]), float(e.dxf.center[1]))
            r = float(e.dxf.radius)
            (drains if r < DRAIN_MAX_R else hobs if r < HOB_MAX_R else rects).append(
                (h, c.buffer(r) if r >= HOB_MAX_R else c)
            )
        elif e.dxftype() == "LWPOLYLINE":
            pts = _pts(e)
            if _is_ring(e, pts) and len(_ring(pts)) >= CURVE_MIN_VERTICES:
                curves.append((h, _poly(pts)))
            elif _is_ring(e, pts) and len(_ring(pts)) >= 3:
                rects.append((h, _poly(pts)))
            elif len(pts) >= 2:
                lines.append((h, LineString(pts)))

    out, used = [], set()

    def obj(cat, h, geom, parts, subtype=None, conf=1.0):
        used.update(parts)
        out.append(
            {
                "id": f"fixture-{h}",
                "category": cat,
                "subtype": subtype,
                "bbox_px": None,
                "polygon_px": None,
                "polygon_m": _coords(geom),
                "confidence": conf,
                "provenance": _prov(h, "FIXTURE", read_from, parts=sorted(parts)),
                "status": "active",
            }
        )

    def body(geom):
        """The fixture's outer rect: the smallest FIXTURE rect containing or touching geom."""
        cands = [(r.area, rh, r) for rh, r in rects if r.buffer(TOUCH).contains(geom)]
        return min(cands, default=(None, None, None))[1:]

    for h, c in curves:
        fill = c.area / max(c.minimum_rotated_rectangle.area, 1e-9)
        rh, rect = body(c)
        parts = [h] + ([rh] if rh else [])
        geom = rect if rect is not None else c
        inner = [dh for dh, d in drains if c.contains(d)]  # a drain inside a curve is never a shower
        if fill >= TUB_MIN_FILL:
            obj("bathtub", h, geom, parts + inner)
            continue
        if inner:
            obj("sink", h, geom, parts + inner, subtype="washbasin")
        else:
            near_lines = [lh for lh, ln in lines if ln.distance(c) <= TOUCH]
            obj("toilet", h, geom, parts + near_lines)
    for dh, d in drains:
        if dh in used:
            continue
        # the tray: every unused rect around the drain (outer edge + inner basin); outermost is the shape
        tray = sorted(
            ((r.area, rh, r) for rh, r in rects if rh not in used and r.contains(d)), key=lambda x: x[0]
        )
        rect = tray[-1][2] if tray else d.buffer(0.4)
        obj("shower", dh, rect, [dh] + [rh for _, rh, _ in tray])
    # the rest: kitchen counters (hob circles), stairs (rect with tread lines), other
    rest = [(h, g) for h, g in rects + lines + hobs if h not in used]
    if rest:
        merged = unary_union([g.buffer(0.005) for _, g in rest])
        comps = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        for comp in comps:
            members = [(h, g) for h, g in rest if comp.intersects(g)]
            hs = [h for h, _ in members]
            n_hob = sum(h in {x for x, _ in hobs} for h in hs)
            n_lines = sum(h in {x for x, _ in lines} for h in hs)
            if n_hob:
                obj("other", hs[0], comp.minimum_rotated_rectangle, hs, subtype="kitchen_counter")
            elif n_lines >= STAIR_MIN_LINES:
                obj("stair", hs[0], comp.minimum_rotated_rectangle, hs)
            else:
                obj("other", hs[0], comp.minimum_rotated_rectangle, hs, conf=0.5)
    return out


# ------------------------------------------------------------------ main


def is_swiss_dxf(path: Path) -> bool:
    try:
        doc = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError):
        return False
    return REQUIRED_LAYERS <= {layer.dxf.name for layer in doc.layers}


def extract(dxf_path: str | Path, read_from: str | None = None) -> dict:
    """Return the schema parts {levels, units, rooms, openings, objects, texts, coverage, history, scale}."""
    path = Path(dxf_path)
    read_from = read_from or path.name
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    insunits = doc.header.get("$INSUNITS", 0)

    texts = _read_texts(msp)
    blocks = _blocks(texts)
    polys, segments = _room_outlines(msp)
    voids = _void_handles(polys, segments)
    units, unit_geom = _units(msp, blocks, read_from)

    def unit_of(geom) -> str | None:
        pt = geom.representative_point()
        hits = [(g.area, uid) for uid, g in unit_geom.items() if g.buffer(TOUCH).contains(pt)]
        return min(hits)[1] if hits else None  # nested outlines: the smallest (most specific) unit

    rooms, owned = [], defaultdict(list)
    for b in blocks:
        if b["number"] is None and b["name"] is None:
            continue
        own = _owner(_anchor(b), polys)
        if own:
            owned[own[0]].append(b)
    text_role = {}
    for h, poly in polys:
        bs = owned.get(h, [])
        computed = round(poly.area, 4)
        if not bs:
            is_void = h in voids
            rooms.append(
                {
                    "id": f"room-{h}",
                    "name": None,
                    "number": None,
                    "label": None,
                    "type": "shaft" if is_void else "unknown",
                    "is_outdoor": False,
                    "unit_id": unit_of(poly),
                    "polygon_px": None,
                    "polygon_m": _coords(poly),
                    "area_m2": computed,
                    "area_written_m2": None,
                    "area_computed_m2": computed,
                    "confidence": 1.0 if is_void else 0.6,
                    "provenance": _prov(h, "ROOM_OUTLINE", read_from),
                    "status": "active",
                    "flags": [],
                }
            )
            continue
        for i, b in enumerate(bs):
            name = b["name"]["text"] if b["name"] else None
            number = b["number"]["text"] if b["number"] else None
            written = parse_area(b["area"]["text"]) if b["area"] else None
            flags = []
            if len(bs) > 1:
                flags.append("shared_outline")
            if (
                written is not None
                and len(bs) == 1
                and abs(written - computed) > AREA_TOL * max(written, 1e-9)
            ):
                flags.append("check_this")
            rtype = glossary.room_type(name)
            if rtype == "unknown" and h in voids:
                rtype = "shaft"
            tids = [b[k]["handle"] for k in ("name", "number", "area") if b[k]]
            for k in ("name", "number", "area"):
                if b[k]:
                    text_role[b[k]["handle"]] = (
                        {"name": "room_label", "number": "room_number", "area": "area"}[k],
                        f"room-{h}" + (f"-{i}" if i else ""),
                    )
            rooms.append(
                {
                    "id": f"room-{h}" + (f"-{i}" if i else ""),
                    "name": name,
                    "number": number,
                    "label": f"{name} ({number})" if name and number else (name or number),
                    "type": rtype,
                    "subtype": name,
                    "is_outdoor": glossary.is_outdoor(name),
                    "unit_id": unit_of(poly),
                    "polygon_px": None,
                    "polygon_m": _coords(poly),
                    "area_m2": written if written is not None else computed,
                    "area_written_m2": written,
                    "area_computed_m2": computed,
                    "confidence": 1.0,
                    "provenance": _prov(h, "ROOM_OUTLINE", read_from, text_handles=tids),
                    "status": "active",
                    "flags": flags,
                    "text_ids": [f"text-{t}" for t in tids],
                }
            )
    # apartment areas: sum of labelled, indoor, non-shaft rooms
    for u in units:
        u["area_m2"] = (
            round(
                sum(
                    r["area_m2"]
                    for r in rooms
                    if r["unit_id"] == u["id"]
                    and (r["name"] or r["number"])
                    and not r["is_outdoor"]
                    and r["type"] != "shaft"
                ),
                4,
            )
            or None
        )
    for b in blocks:
        if b["unit"] is not None:
            text_role[b["unit"]["handle"]] = ("unit_label", None)

    # level: the largest annotation text is the plan title (e.g. "Grundriss 2. OG")
    annotations = [t for t in texts if t["layer"] == "ANNOTATION"]
    title = max(annotations, key=lambda t: t["h"], default=None)
    levels = []
    if title is not None:
        text_role[title["handle"]] = ("level_title", None)
        levels.append(
            {
                "id": "level-1",
                "name": title["text"],
                "confidence": 0.9,
                "provenance": _prov(title["handle"], "ANNOTATION", read_from),
                "status": "active",
            }
        )

    out_texts = []
    for t in texts:
        role, room_id = text_role.get(t["handle"], ("other", None))
        out_texts.append(
            {
                "id": f"text-{t['handle']}",
                "text": t["text"],
                "bbox_px": None,
                "role": role,
                "room_id": room_id,
                "confidence": 1.0,
                "provenance": _prov(t["handle"], t["layer"], read_from),
                "status": "active",
                "properties": {"x_m": round(t["pt"].x, 4), "y_m": round(t["pt"].y, 4), "height_m": t["h"]},
            }
        )

    openings = _openings(msp, read_from)
    objects = _fixtures(msp, read_from)

    cov = []
    for cat, n in (
        ("rooms", len(rooms)),
        ("doors", sum(o["kind"] == "door" for o in openings)),
        ("windows", sum(o["kind"] == "window" for o in openings)),
        ("fixtures", len(objects)),
        ("text", len(out_texts)),
    ):
        cov.append(
            {
                "category": cat,
                "region": "page",
                "status": "analysed",
                "extractor": EXTRACTOR,
                "confidence": 1.0,
                "note": f"{n} from DXF layers",
            }
        )
    cov.append(
        {
            "category": "walls",
            "region": "page",
            "status": "not_analysed",
            "extractor": EXTRACTOR,
            "confidence": 0.0,
            "note": "WALL layer not extracted (no Swiss wall questions)",
        }
    )

    return {
        "scale": {
            "m_per_px": None,
            "source": "vector_units",
            "confidence": 1.0,
            "note": f"DXF units in metres ($INSUNITS={insunits})",
        },
        "levels": levels,
        "units": units,
        "rooms": rooms,
        "openings": openings,
        "objects": objects,
        "texts": out_texts,
        "coverage": cov,
        "history": [
            {
                "extractor": EXTRACTOR,
                "action": "extract",
                "note": f"{len(rooms)} rooms, {len(units)} units, {len(openings)} openings, "
                f"{len(objects)} fixtures",
            }
        ],
    }
