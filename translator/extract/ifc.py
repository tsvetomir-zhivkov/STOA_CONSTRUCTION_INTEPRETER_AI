"""IfcOpenShell extraction with SI world geometry and untruncated source properties.

The existing documents() function emits internal storey fragments for alignment compatibility.
The public translator combines all fragments per building and enriches them with IFC metadata,
types, systems, material layers, quantities and relationships before v0.3 validation/persistence.
Geometry is cached by absolute source path, size, timestamp and extractor version.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import re
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from translator import glossary
from translator.config import INDEX_DIR

EXTRACTOR = "ifc"
EXTRACTOR_VERSION = "5-building-0.3"
CACHE_DIR = INDEX_DIR / "_raw" / "ifc"
Z_TOL = 1e-3  # m, triangles within this of the lowest z form the footprint
AREA_TOL = 0.02  # written vs computed area: flag check_this above 2% (vector source)
COORD_DECIMALS = 4
PROPS_MAX_BYTES = 6000  # per element: the largest property sets are dropped beyond this
STR_MAX = 200  # characters kept of one property value
LIST_MAX = 20  # items kept of one list-valued property
DOC_PROPS_MAX_BYTES = 2_000_000  # per document: extra elements are slimmed beyond this
# IfcElement kinds that are never real building items (openings, voids, virtual boundaries)
SKIP_ELEMENT_CLASSES = ("IfcVirtualElement",)

OBJECT_KINDS = {"IfcSlab": "slab", "IfcColumn": "column", "IfcBeam": "beam"}
SANITARY_WORDS = [  # (category, words in predefined type / type name / name), first hit wins
    ("toilet", ["TOILETPAN", "WCSEAT", "TOILET", "WC", "TOILETTE", "KLOSETT"]),
    ("bathtub", ["BATH", "BATHTUB", "BADEWANNE", "BADEKAR"]),
    ("shower", ["SHOWER", "DUSCHE", "BRUSE"]),
    ("sink", ["WASHHANDBASIN", "SINK", "BASIN", "LAVATORY", "WASHBASIN", "LAVABO", "VASK", "SPUELE"]),
]


# ------------------------------------------------------------------ model helpers


def _ue():
    import ifcopenshell.util.element as ue

    return ue


def storey_of(e):
    """IfcBuildingStorey through containment, then aggregation (spaces, nested parts)."""
    ue = _ue()
    seen, x = set(), e
    while x is not None and x.id() not in seen:
        seen.add(x.id())
        if x.is_a("IfcBuildingStorey"):
            return x
        x = ue.get_container(x) or ue.get_aggregate(x)
    return None


def unit_scales(model) -> dict[str, float]:
    import ifcopenshell.util.unit as uu

    out = {}
    for kind in ("LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT"):
        try:
            unit = uu.get_project_unit(model, kind)
            out[kind] = float(uu.calculate_unit_scale(model, kind)) if unit is not None else None
        except Exception as exc:
            raise ValueError(f"Cannot resolve IFC {kind}") from exc
    if out["LENGTHUNIT"] is None:
        raise ValueError("IFC project length unit is missing; cannot normalize geometry")
    return out


def _qtos(e) -> dict[str, float]:
    """All numeric quantities of an element, flattened: {name: value} (first occurrence wins)."""
    out: dict[str, float] = {}
    for qset in _ue().get_psets(e, qtos_only=True).values():
        for k, v in qset.items():
            if k != "id" and isinstance(v, (int, float)) and k not in out:
                out[k] = float(v)
    return out


def _qto(q: dict, *names) -> float | None:
    for n in names:
        if n in q:
            return q[n]
    return None


def sanitary_category(e) -> str:
    ue = _ue()
    t = ue.get_type(e)
    words = " ".join(
        str(x or "")
        for x in (
            getattr(e, "PredefinedType", None),
            getattr(t, "PredefinedType", None) if t else None,
            t.Name if t else None,
            e.Name,
            e.ObjectType if hasattr(e, "ObjectType") else None,
        )
    ).upper()
    tokens = set(re.split(r"[^A-Z0-9ÄÖÜÆØÅ]+", words))
    for cat, keys in SANITARY_WORDS:
        if any(k in tokens for k in keys):
            return cat
    return "other"


def is_top_level_stair(e) -> bool:
    agg = _ue().get_aggregate(e)
    return not (agg is not None and agg.is_a("IfcStair"))


# ------------------------------------------------------------------ geometry


def _mesh(shape):
    g = shape.geometry
    v = np.asarray(g.verts, dtype=float).reshape(-1, 3)
    f = np.asarray(g.faces, dtype=int).reshape(-1, 3)
    return v, f


def footprint_lowest(v, f):
    """Union of the triangles lying at the lowest z (the floor face of a space)."""
    if not len(f):
        return None
    zmin = v[:, 2].min()
    bottom = f[(np.abs(v[f][:, :, 2] - zmin) < Z_TOL).all(axis=1)]
    polys = [Polygon(v[t][:, :2]) for t in bottom]
    polys = [p for p in polys if p.is_valid and p.area > 1e-9]
    return unary_union(polys) if polys else None


def footprint_projected(v, f):
    """Union of all triangles projected to XY (plan footprint of an element)."""
    if not len(f):
        return None
    polys = [Polygon(v[t][:, :2]) for t in f]
    polys = [p for p in polys if p.is_valid and p.area > 1e-9]
    return unary_union(polys) if polys else None


def mesh_volume(v, f) -> float | None:
    import trimesh

    try:
        m = trimesh.Trimesh(vertices=v, faces=f, process=True)
        return abs(float(m.volume)) if m.is_watertight else None
    except Exception:
        return None


def _outline(geom) -> list[list[float]] | None:
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type != "Polygon":
        geom = geom.convex_hull
        if geom.geom_type != "Polygon":
            return None
    return [[round(x, COORD_DECIMALS), round(y, COORD_DECIMALS)] for x, y in list(geom.exterior.coords)[:-1]]


def _long_side(geom) -> float | None:
    if geom is None or geom.is_empty:
        return None
    r = geom.minimum_rotated_rectangle
    if r.geom_type != "Polygon":
        return float(r.length)
    xy = list(r.exterior.coords)
    return max(math.dist(xy[0], xy[1]), math.dist(xy[1], xy[2]))


def _geometry(model, products) -> dict[int, tuple]:
    """{step id: (verts, faces)} in world coordinates, metres. Failures are left out."""
    import ifcopenshell.geom

    out: dict[int, tuple] = {}
    if not products:
        return out
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    it = ifcopenshell.geom.iterator(s, model, max(1, multiprocessing.cpu_count() - 1), include=products)
    ok = it.initialize()
    while ok:
        shape = it.get()
        v, f = _mesh(shape)
        if len(v) and len(f):  # empty meshes count as no geometry
            out[shape.id] = (v, f)
        ok = it.next()
    return out


# ------------------------------------------------------------------ properties (free-text questions)


def _clean_value(v):
    from translator.extract.ifc_metadata import value

    return value(v)


def _clean_psets(psets: dict) -> dict:
    out = {}
    for name, props in psets.items():
        clean = {k: _clean_value(v) for k, v in props.items() if k != "id"}
        if clean:
            out[str(name)] = clean
    return out


def _materials(e, L: float):
    try:
        m = _ue().get_material(e, should_skip_usage=True)
    except Exception:
        return None
    if m is None:
        return None
    if m.is_a("IfcMaterial"):
        return [{"name": m.Name}]
    if m.is_a("IfcMaterialLayerSet"):
        return [
            {
                "name": ly.Material.Name if ly.Material else None,
                "thickness_m": _r(ly.LayerThickness * L if ly.LayerThickness is not None else None),
            }
            for ly in m.MaterialLayers
        ]
    if m.is_a("IfcMaterialList"):
        return [{"name": x.Name} for x in m.Materials]
    if m.is_a("IfcMaterialConstituentSet"):
        return [{"name": c.Material.Name if c.Material else c.Name} for c in (m.MaterialConstituents or [])]
    if m.is_a("IfcMaterialProfileSet"):
        return [{"name": pr.Material.Name if pr.Material else pr.Name} for pr in (m.MaterialProfiles or [])]
    return [{"name": getattr(m, "Name", None)}]


def _systems(e) -> list[str] | None:
    try:
        import ifcopenshell.util.system as us

        names = [s.Name for s in us.get_element_systems(e)]
    except Exception:
        return None
    return names or None


def cap_properties(props: dict, max_bytes: int = PROPS_MAX_BYTES) -> dict:
    """Drop the largest property sets until the serialised properties fit in max_bytes."""
    size = len(json.dumps(props, ensure_ascii=False))
    psets = props.get("psets") or {}
    dropped = []
    while size > max_bytes and psets:
        name = max(psets, key=lambda n: len(json.dumps(psets[n], ensure_ascii=False)))
        del psets[name]
        dropped.append(name)
        size = len(json.dumps(props, ensure_ascii=False))
    if dropped:
        props["psets_dropped"] = dropped
    return props


def element_properties(e, L: float) -> dict:
    """Raw IFC data for text questions: class, type, materials, systems and all property sets."""
    ue = _ue()
    t = ue.get_type(e)
    props = {
        "ifc_class": e.is_a(),
        "name": e.Name,
        "description": getattr(e, "Description", None),
        "object_type": getattr(e, "ObjectType", None),
        "predefined_type": str(getattr(e, "PredefinedType", None) or "") or None,
        "tag": getattr(e, "Tag", None),
        "type_name": t.Name if t else None,
        "type_class": t.is_a() if t else None,
        "materials": _materials(e, L),
        "systems": _systems(e),
        "psets": _clean_psets(ue.get_psets(e)),  # includes inherited type psets and quantities
    }
    props = {k: (v if k in ("psets", "materials") else _clean_value(v)) for k, v in props.items()}
    return {k: v for k, v in props.items() if v not in (None, [], {})}


def model_context(model, L: float) -> dict:
    """Project / site / building information, stored on every level document."""
    ue = _ue()
    out = {}
    for key, cls in (("project", "IfcProject"), ("site", "IfcSite"), ("building", "IfcBuilding")):
        items = list(model.by_type(cls))
        if not items:
            continue
        e = items[0]
        info = {
            "name": e.Name,
            "long_name": getattr(e, "LongName", None),
            "description": e.Description,
            "object_type": getattr(e, "ObjectType", None),
            "global_id": e.GlobalId,
        }
        if cls == "IfcSite":
            info["ref_latitude"] = _clean_value(e.RefLatitude)
            info["ref_longitude"] = _clean_value(e.RefLongitude)
            info["ref_elevation_m"] = _r(e.RefElevation * L if e.RefElevation is not None else None)
        address = getattr(e, "BuildingAddress", None) or getattr(e, "SiteAddress", None)
        if address is not None:
            fields = ("AddressLines", "PostalCode", "Town", "Region", "Country")
            info["address"] = {
                k: _clean_value(getattr(address, k)) for k in fields if getattr(address, k, None)
            }
        info["psets"] = _clean_psets(ue.get_psets(e))
        out[key] = {k: v for k, v in info.items() if v not in (None, [], {})}
    return out


# ------------------------------------------------------------------ extraction to records


def _r(x, n=6):
    return None if x is None else round(float(x), n)


def _scaled(value, factor):
    return value * factor if value is not None and factor is not None else None


def extract_records(ifc_path: str | Path) -> dict:
    """Read one model into plain records (metres). Cached per model."""
    import ifcopenshell

    path = Path(ifc_path)
    st = path.stat()
    key = {
        "path": str(path.resolve()),
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "version": EXTRACTOR_VERSION,
    }
    cache = CACHE_DIR / f"{path.stem}.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("key") == key:
                return data
        except (OSError, ValueError):
            pass

    model = ifcopenshell.open(str(path))
    sc = unit_scales(model)
    L, A, V = sc["LENGTHUNIT"], sc["AREAUNIT"], sc["VOLUMEUNIT"]

    def has(cls):
        try:
            return list(model.by_type(cls))
        except RuntimeError:  # class not in this schema (e.g. IfcSanitaryTerminal in IFC2X3)
            return []

    spaces = has("IfcSpace")
    doors, windows = has("IfcDoor"), has("IfcWindow")
    walls = has("IfcWall")  # includes IfcWallStandardCase
    objs = [e for cls in OBJECT_KINDS for e in has(cls)]
    stairs = [e for e in has("IfcStair") if is_top_level_stair(e)]
    sanitary_terminals = has("IfcSanitaryTerminal")
    sanitary = sanitary_terminals or has("IfcFlowTerminal")
    sanitary_rule = (
        "IfcSanitaryTerminal" if sanitary_terminals else "IfcFlowTerminal (model has no IfcSanitaryTerminal)"
    )

    geo = _geometry(model, spaces + [e for e in has("IfcElement") if not e.is_a("IfcVirtualElement")])

    def base(e, kind):
        stv = storey_of(e)
        return {
            "kind": kind,
            "ifc_class": e.is_a(),
            "global_id": e.GlobalId,
            "name": e.Name,
            "storey": stv.GlobalId if stv else None,
            "has_geometry": e.id() in geo,
            "props": element_properties(e, L),
        }

    records = []
    for e in spaces:
        r = base(e, "space")
        q = _qtos(e)
        nfa = _qto(q, "NetFloorArea")
        r["long_name"] = e.LongName
        r["area_qto"] = _r(_scaled(nfa, A))
        if e.id() in geo:
            v, f = geo[e.id()]
            fp = footprint_lowest(v, f)
            r["area_geom"] = _r(fp.area if fp is not None else None)
            r["height"] = _r(v[:, 2].max() - v[:, 2].min())
            r["outline"] = _outline(fp)
        records.append(r)
    for e, kind in [(d, "door") for d in doors] + [(w, "window") for w in windows]:
        r = base(e, kind)
        r["width"] = _r(e.OverallWidth * L if getattr(e, "OverallWidth", None) is not None else None)
        r["height"] = _r(e.OverallHeight * L if getattr(e, "OverallHeight", None) is not None else None)
        if e.id() in geo:
            r["outline"] = _outline(footprint_projected(*geo[e.id()]))
        records.append(r)
    for e in walls:
        r = base(e, "wall")
        q = _qtos(e)
        fp = footprint_projected(*geo[e.id()]) if e.id() in geo else None
        length = _qto(q, "Length")
        r["length"] = _r(length * L) if length is not None else _r(_long_side(fp))
        r["length_source"] = (
            "qto:Length" if length is not None else ("geometry:footprint" if fp is not None else None)
        )
        side = _qto(q, "NetSideArea")
        r["net_side_area"] = _r(_scaled(side, A))
        vol = _qto(q, "NetVolume")
        r["net_volume"] = (
            _r(_scaled(vol, V))
            if vol is not None
            else (_r(mesh_volume(*geo[e.id()])) if e.id() in geo else None)
        )
        r["volume_source"] = (
            "qto:NetVolume" if vol is not None else ("geometry:mesh" if e.id() in geo else None)
        )
        width, height = _qto(q, "Width"), _qto(q, "Height")
        r["thickness"] = _r(width * L if width is not None else None)
        r["height"] = (
            _r(height * L)
            if height is not None
            else (_r(np.ptp(geo[e.id()][0][:, 2])) if e.id() in geo else None)
        )
        r["outline"] = _outline(fp)
        records.append(r)
    for e in objs + stairs + sanitary:
        if e.is_a("IfcStair"):
            kind = "stair"
        elif any(e.is_a(c) for c in OBJECT_KINDS):
            kind = OBJECT_KINDS[next(c for c in OBJECT_KINDS if e.is_a(c))]
        else:
            kind = "sanitary"
        r = base(e, kind)
        if kind == "sanitary":
            r["sanitary_category"] = sanitary_category(e)
        q = _qtos(e)
        vol = _qto(q, "NetVolume")
        r["net_volume"] = (
            _r(_scaled(vol, V))
            if vol is not None
            else (_r(mesh_volume(*geo[e.id()])) if e.id() in geo else None)
        )
        r["volume_source"] = (
            "qto:NetVolume" if vol is not None else ("geometry:mesh" if e.id() in geo else None)
        )
        if e.id() in geo:
            r["outline"] = _outline(footprint_projected(*geo[e.id()]))
        records.append(r)

    # every other building element, for text questions (no geometry, never counted by the rules above)
    handled = {e.id() for e in spaces + doors + windows + walls + objs + sanitary + stairs}
    for e in has("IfcElement"):
        if e.id() in handled or any(e.is_a(c) for c in SKIP_ELEMENT_CLASSES):
            continue
        r = base(e, "element")
        r["geometry_skipped"] = True
        r["quantities"] = _qtos(e)  # kept when the document budget slims this element
        cls = e.is_a()
        r["subtype"] = (cls[3:] if cls.startswith("Ifc") else cls).lower()
        if e.id() in geo:
            r["outline"] = _outline(footprint_projected(*geo[e.id()]))
            r["net_volume"] = _r(mesh_volume(*geo[e.id()]))
            r["volume_source"] = "geometry:watertight_mesh"
        records.append(r)

    storeys = [
        {
            "global_id": s.GlobalId,
            "name": s.Name,
            "elevation": _r(s.Elevation * L if s.Elevation is not None else None),
            "props": element_properties(s, L),
        }
        for s in sorted(has("IfcBuildingStorey"), key=lambda s: s.Elevation or 0.0)
    ]
    from translator.extract.ifc_metadata import extract as extract_metadata

    metadata = extract_metadata(model, sc, geo)
    for r in records:
        if r["ifc_class"].startswith("IfcOpening"):
            r["kind"] = "void"
        if r["global_id"] in metadata["entities"]:
            r["building"] = metadata["membership"].get(r["global_id"])
    for s in storeys:
        s["building"] = metadata["membership"].get(s["global_id"])
    data = {
        "metadata": metadata,
        "key": key,
        "schema": model.schema,
        "units": sc,
        "sanitary_rule": sanitary_rule,
        "context": model_context(model, L),
        "storeys": storeys,
        "records": records,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ------------------------------------------------------------------ records to schema documents


def _prov(r: dict, read_from: str, **details) -> dict:
    prov = {"extractor": EXTRACTOR, "source_ref": r["global_id"], "file": read_from}
    details = {k: v for k, v in details.items() if v is not None}
    if details:
        prov["details"] = details
    return prov


def _flags(r: dict) -> list[str]:
    return [] if r.get("has_geometry") or r.get("geometry_skipped") else ["no_geometry"]


def _room(r: dict, level_id: str | None, read_from: str) -> dict:
    name, number = r.get("long_name"), r.get("name")
    written, computed = r.get("area_qto"), r.get("area_geom")
    area = written if written is not None else computed
    flags = _flags(r)
    if (
        written is not None
        and computed is not None
        and abs(written - computed) > AREA_TOL * max(written, 1e-9)
    ):
        flags.append("check_this")
    label = f"{name} ({number})" if name and number else (name or number)
    rtype = glossary.room_type(name)
    return {
        "id": f"room-{r['global_id']}",
        "name": name,
        "number": number,
        "label": label,
        "type": rtype,
        "subtype": name,
        "is_outdoor": glossary.is_outdoor(name),
        "unit_id": None,
        "polygon_px": None,
        "polygon_m": r.get("outline"),
        "area_m2": area,
        "area_written_m2": written,
        "area_computed_m2": computed,
        "height_m": r.get("height"),
        "level_id": level_id,
        "confidence": 1.0 if area is not None else 0.3,
        "provenance": _prov(
            r,
            read_from,
            area_source="qto:NetFloorArea"
            if written is not None
            else ("geometry:footprint" if computed else None),
            ifc_class=r["ifc_class"],
        ),
        "status": "active",
        "flags": flags,
        "properties": r.get("props") or {"ifc_class": r["ifc_class"]},
    }


def _entity(r: dict, level_id, read_from) -> dict:
    kind = r["kind"]
    common = {
        "id": f"{kind}-{r['global_id']}",
        "level_id": level_id,
        "confidence": 1.0,
        "provenance": _prov(
            r,
            read_from,
            ifc_class=r["ifc_class"],
            volume_source=r.get("volume_source"),
            length_source=r.get("length_source"),
        ),
        "status": "active",
        "flags": _flags(r),
        "properties": r.get("props") or {"ifc_class": r["ifc_class"], "name": r.get("name")},
    }
    if kind in ("door", "window", "void"):
        return {
            **common,
            "kind": kind,
            "bbox_px": None,
            "polygon_px": None,
            "polygon_m": r.get("outline"),
            "width_m": r.get("width"),
            "height_m": r.get("height"),
        }
    if kind == "wall":
        return {
            **common,
            "polyline_px": None,
            "polygon_px": None,
            "polygon_m": r.get("outline"),
            "length_m": r.get("length"),
            "thickness_m": r.get("thickness"),
            "height_m": r.get("height"),
            "net_side_area_m2": r.get("net_side_area"),
            "net_volume_m3": r.get("net_volume"),
        }
    if kind == "stair":
        category, subtype = "stair", None
    elif kind == "element":
        category, subtype = "other", r["subtype"]
    elif kind == "sanitary":
        cat = r.get("sanitary_category", "other")
        category, subtype = (cat, None) if cat != "other" else ("other", "sanitary")
    else:
        category, subtype = "other", kind  # slab / column / beam: no schema category
    return {
        **common,
        "category": category,
        "subtype": subtype,
        "bbox_px": None,
        "polygon_px": None,
        "polygon_m": r.get("outline"),
        "net_volume_m3": r.get("net_volume"),
    }


def apply_document_budget(doc: dict, recs: list[dict], max_bytes: int = DOC_PROPS_MAX_BYTES) -> int:
    """Keep a document's properties under max_bytes by slimming the extra elements, largest first.

    Slim properties keep class, name, type, materials and systems, and replace the property sets by
    the element's numeric quantities. Elements used by the structured rules are never slimmed.
    """
    quantities = {
        f"element-{r['global_id']}": r.get("quantities") or {} for r in recs if r["kind"] == "element"
    }
    extras = [o for o in doc["objects"] if o["id"] in quantities]
    sizes = {o["id"]: len(json.dumps(o["properties"], ensure_ascii=False)) for o in extras}
    total = sum(
        len(json.dumps(e.get("properties", {}), ensure_ascii=False))
        for k in ("rooms", "walls", "openings", "objects")
        for e in doc[k]
    )
    n = 0
    for o in sorted(extras, key=lambda o: -sizes[o["id"]]):
        if total <= max_bytes:
            break
        keep = {k: v for k, v in o["properties"].items() if k not in ("psets", "psets_dropped")}
        keep.update(quantities=quantities[o["id"]], slim=True)
        total -= sizes[o["id"]] - len(json.dumps(keep, ensure_ascii=False))
        o["properties"] = keep
        n += 1
    return n


def documents(data: dict, read_from: str, level_name: str | None = None) -> list[dict]:
    """Schema parts per storey: [{part, levels, rooms, walls, openings, objects, coverage, history}].

    level_name selects one storey (a rendered plan of that level); None gives every storey plus,
    if any element has no storey, a document without a level.
    """
    storeys = data["storeys"]
    if level_name is not None:
        storeys = [s for s in storeys if s["name"] == level_name] or [
            s for s in storeys if (s["name"] or "").casefold() == level_name.casefold()
        ]
    groups: list[tuple[dict | None, str]] = [
        (s, f"{i:02d}-{s['name']}") for i, s in enumerate(data["storeys"]) if s in storeys
    ]
    if level_name is None and any(r["storey"] is None for r in data["records"]):
        groups.append((None, "no-storey"))
    out = []
    for s, part in groups:
        gid = s["global_id"] if s else None
        level_id = f"level-{gid}" if s else None
        recs = [r for r in data["records"] if r["storey"] == gid]
        doc = {
            "part": part,
            "levels": (
                [
                    {
                        "id": level_id,
                        "name": s["name"],
                        "elevation_m": s["elevation"],
                        "confidence": 1.0,
                        "provenance": {"extractor": EXTRACTOR, "source_ref": gid, "file": read_from},
                        "status": "active",
                        # storey data plus project / site / building info (no top-level slot for it)
                        "properties": {
                            "storey": s.get("props") or {},
                            **data.get("context", {}),
                            "units_to_si": data.get("units"),
                            "note": "property set values are in model units; multiply by units_to_si",
                        },
                    }
                ]
                if s
                else []
            ),
            "rooms": [_room(r, level_id, read_from) for r in recs if r["kind"] == "space"],
            "walls": [_entity(r, level_id, read_from) for r in recs if r["kind"] == "wall"],
            "openings": [
                _entity(r, level_id, read_from) for r in recs if r["kind"] in ("door", "window", "void")
            ],
            "objects": [
                _entity(r, level_id, read_from)
                for r in recs
                if r["kind"] in ("stair", "sanitary", "element", *OBJECT_KINDS.values())
            ],
        }
        slimmed = 0  # v0.3 preserves complete source property sets
        n = {k: len(doc[k]) for k in ("rooms", "walls", "openings", "objects")}
        doc["coverage"] = [
            {
                "category": c,
                "region": "page",
                "status": "analysed",
                "extractor": EXTRACTOR,
                "confidence": 1.0,
                "note": "read from the IFC model",
            }
            for c in ("rooms", "doors", "windows", "walls", "fixtures")
        ] + [
            {
                "category": "text",
                "region": "page",
                "status": "not_analysed",
                "extractor": EXTRACTOR,
                "confidence": 0.0,
                "note": "no page text read (model route)",
            }
        ]
        doc["history"] = [
            {
                "extractor": EXTRACTOR,
                "action": "extract",
                "note": f"{data['schema']}, storey {s['name'] if s else '(none)'}: {n}; "
                f"sanitary rule: {data['sanitary_rule']}; {slimmed} extra elements slimmed to fit "
                f"{DOC_PROPS_MAX_BYTES // 1_000_000} MB",
            }
        ]
        out.append(doc)
    return out
