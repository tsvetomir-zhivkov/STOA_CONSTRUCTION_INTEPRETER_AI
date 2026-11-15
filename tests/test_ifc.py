"""Step 4: IFC extractor. Synthetic models built with ifcopenshell.api (mm and m, IFC4 and IFC2X3),
plus the real esplanades quick test."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.api as api
import ifcopenshell.api.owner.settings as owner_settings
import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "eval"))

import probe  # noqa: E402

from translator import translate, translate_all  # noqa: E402
from translator.config import INDEX_DIR, data_root, questions_path  # noqa: E402
from translator.extract import ifc  # noqa: E402
from translator.store import validate  # noqa: E402

needs_data = pytest.mark.skipif(
    not questions_path(data_root()).exists(), reason="dataset not found; set STOA_DATA"
)


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    """Never write synthetic models into the real cache."""
    monkeypatch.setattr(ifc, "CACHE_DIR", tmp_path / "_cache")


# ------------------------------------------------------------------ synthetic model builder


def _owner(f):
    """IFC2X3 needs an owner history; give the api a user and an application for this file."""

    def user(file):
        found = file.by_type("IfcPersonAndOrganization")
        if found:
            return found[0]
        org = file.createIfcOrganization(Name="test")
        return file.createIfcPersonAndOrganization(file.createIfcPerson(), org)

    def application(file):
        found = file.by_type("IfcApplication")
        if found:
            return found[0]
        return file.createIfcApplication(user(file).TheOrganization, "1", "test", "test")

    owner_settings.get_user = user
    owner_settings.get_application = application


class Model:
    def __init__(self, schema="IFC4", unit="MILLI"):
        self.f = ifcopenshell.file(schema=schema)
        if schema == "IFC2X3":
            _owner(self.f)
        self.k = 1000.0 if unit == "MILLI" else 1.0  # SI metres -> project length units
        f = self.f
        project = api.run("root.create_entity", f, ifc_class="IfcProject", name="Test")
        units = [
            api.run("unit.add_si_unit", f, unit_type="LENGTHUNIT", prefix=unit),
            api.run("unit.add_si_unit", f, unit_type="AREAUNIT"),
            api.run("unit.add_si_unit", f, unit_type="VOLUMEUNIT"),
        ]
        api.run("unit.assign_unit", f, units=units)
        model_ctx = api.run("context.add_context", f, context_type="Model")
        self.body = api.run(
            "context.add_context",
            f,
            context_type="Model",
            context_identifier="Body",
            target_view="MODEL_VIEW",
            parent=model_ctx,
        )
        site = api.run("root.create_entity", f, ifc_class="IfcSite")
        self.building = api.run("root.create_entity", f, ifc_class="IfcBuilding")
        api.run("aggregate.assign_object", f, products=[site], relating_object=project)
        api.run("aggregate.assign_object", f, products=[self.building], relating_object=site)

    def storey(self, name, elevation_m):
        s = api.run("root.create_entity", self.f, ifc_class="IfcBuildingStorey", name=name)
        s.Elevation = elevation_m * self.k
        api.run("aggregate.assign_object", self.f, products=[s], relating_object=self.building)
        return s

    def element(self, cls, storey=None, box=None, at=(0, 0, 0), name=None, aggregate_in=None, **attrs):
        e = api.run("root.create_entity", self.f, ifc_class=cls, name=name)
        for k, v in attrs.items():
            setattr(e, k, v)
        if aggregate_in is not None:
            api.run("aggregate.assign_object", self.f, products=[e], relating_object=aggregate_in)
        elif storey is not None:
            if cls == "IfcSpace":
                api.run("aggregate.assign_object", self.f, products=[e], relating_object=storey)
            else:
                api.run("spatial.assign_container", self.f, products=[e], relating_structure=storey)
        if box is not None:  # an x * y * z metre box at `at`
            x, y, z = box
            rep = api.run(
                "geometry.add_wall_representation", self.f, context=self.body, length=x, height=z, thickness=y
            )
            api.run("geometry.assign_representation", self.f, product=e, representation=rep)
            m = np.eye(4)
            m[:3, 3] = at
            api.run("geometry.edit_object_placement", self.f, product=e, matrix=m)
        return e

    def qto(self, e, name, **values):
        q = api.run("pset.add_qto", self.f, product=e, name=name)
        api.run("pset.edit_qto", self.f, qto=q, properties=values)

    def save(self, path: Path) -> Path:
        self.f.write(str(path))
        return path


def build(tmp_path: Path, schema="IFC4", unit="MILLI", name="model") -> Path:
    """Two storeys with one of every structured element kind. Quantities in project units."""
    m = Model(schema, unit)
    k = m.k
    eg, og = m.storey("EG", 0.0), m.storey("OG", 3.0)
    # rooms: one with a NetFloorArea quantity (19.5 vs 20.0 drawn -> check_this), one without
    office = m.element("IfcSpace", eg, box=(5, 4, 3), name="101", LongName="Office")
    m.qto(office, "Qto_SpaceBaseQuantities", NetFloorArea=19.5)
    m.element("IfcSpace", eg, box=(2, 3, 2.5), at=(6, 0, 0), name="102", LongName="Balkon")
    # walls: quantities on one, geometry only on the other
    w1 = m.element("IfcWall", eg, box=(4, 0.2, 3), at=(0, 5, 0))
    m.qto(w1, "Qto_WallBaseQuantities", Length=4.0 * k, NetSideArea=12.0, NetVolume=2.4)
    pset = api.run("pset.add_pset", m.f, product=w1, name="Pset_WallCommon")
    api.run("pset.edit_pset", m.f, pset=pset, properties={"FireRating": "EI60", "LoadBearing": True})
    m.element("IfcWall", eg, box=(6, 0.25, 3), at=(0, 7, 0))
    # extra elements: kept for text questions, never counted by the structured rules
    m.element("IfcCovering", eg, name="Floor tiles")
    m.element("IfcBuildingElementProxy", eg, name="Kitchen block")
    m.element("IfcDoor", eg, OverallWidth=0.9 * k, OverallHeight=2.1 * k)
    m.element("IfcWindow", eg, OverallWidth=1.2 * k, OverallHeight=1.4 * k)
    slab = m.element("IfcSlab", eg, box=(5, 4, 0.2))
    m.qto(slab, "Qto_SlabBaseQuantities", NetVolume=3.9)
    m.element("IfcColumn", og, box=(0.3, 0.3, 3))  # volume from the mesh: 0.27
    m.element("IfcBeam", og, box=(4, 0.2, 0.4), at=(0, 0, 2.6))
    stair = m.element("IfcStair", og, name="Stair 1")
    m.element("IfcStair", aggregate_in=stair, name="flight part")  # nested part: not a stair of its own
    m.element("IfcColumn", None, box=(0.3, 0.3, 3), at=(20, 0, 0))  # in no storey
    if schema == "IFC4":
        m.element("IfcSanitaryTerminal", eg, name="WC 1", PredefinedType="WCSEAT")
        m.element("IfcFlowTerminal", eg, name="Air outlet")  # ignored: the model has IfcSanitaryTerminal
    else:
        m.element("IfcFlowTerminal", eg, name="WC")
        m.element("IfcFlowTerminal", eg, name="Floor drain")
    return m.save(tmp_path / f"{name}.ifc")


def _docs(path: Path) -> dict[str, dict]:
    data = ifc.extract_records(path)
    return {d["part"]: d for d in ifc.documents(data, path.name)}


def _one(items, **kw):
    hits = [x for x in items if all(x.get(k) == v for k, v in kw.items())]
    assert len(hits) == 1, (kw, items)
    return hits[0]


# ------------------------------------------------------------------ rules


def test_storeys_one_document_each_plus_no_storey(tmp_path):
    docs = _docs(build(tmp_path))
    assert list(docs) == ["00-EG", "01-OG", "no-storey"]
    assert docs["00-EG"]["levels"][0]["name"] == "EG"
    assert docs["01-OG"]["levels"][0]["elevation_m"] == pytest.approx(3.0)
    assert docs["no-storey"]["levels"] == []
    assert _one(docs["no-storey"]["objects"], subtype="column")["level_id"] is None


def test_space_is_found_through_aggregation_and_named(tmp_path):
    eg = _docs(build(tmp_path))["00-EG"]
    room = _one(eg["rooms"], number="101")
    assert (room["name"], room["label"]) == ("Office", "Office (101)")
    assert room["level_id"] == eg["levels"][0]["id"]


def test_area_quantity_wins_and_footprint_cross_checks(tmp_path):
    eg = _docs(build(tmp_path))["00-EG"]
    office = _one(eg["rooms"], number="101")
    assert office["area_m2"] == pytest.approx(19.5)
    assert office["area_written_m2"] == pytest.approx(19.5)
    assert office["area_computed_m2"] == pytest.approx(20.0)
    assert office["provenance"]["details"]["area_source"] == "qto:NetFloorArea"
    assert "check_this" in office["flags"]  # 2.5% off


def test_area_from_lowest_footprint_when_no_quantity(tmp_path):
    eg = _docs(build(tmp_path))["00-EG"]
    balcony = _one(eg["rooms"], number="102")
    assert balcony["area_m2"] == pytest.approx(6.0) and balcony["area_written_m2"] is None
    assert balcony["provenance"]["details"]["area_source"] == "geometry:footprint"
    assert balcony["height_m"] == pytest.approx(2.5)
    assert balcony["is_outdoor"]  # glossary: Balkon


def test_walls_quantities_then_geometry(tmp_path):
    eg = _docs(build(tmp_path))["00-EG"]
    with_q = _one(eg["walls"], length_m=pytest.approx(4.0))
    assert with_q["net_side_area_m2"] == pytest.approx(12.0) and with_q["net_volume_m3"] == pytest.approx(2.4)
    assert with_q["provenance"]["details"]["length_source"] == "qto:Length"
    geom = next(w for w in eg["walls"] if w is not with_q)
    assert geom["length_m"] == pytest.approx(6.0)
    assert geom["net_volume_m3"] == pytest.approx(6 * 0.25 * 3)
    assert geom["provenance"]["details"]["volume_source"] == "geometry:mesh"


def test_openings_and_objects(tmp_path):
    docs = _docs(build(tmp_path))
    eg, og = docs["00-EG"], docs["01-OG"]
    assert _one(eg["openings"], kind="door")["width_m"] == pytest.approx(0.9)
    assert _one(eg["openings"], kind="window")["height_m"] == pytest.approx(1.4)
    slab = _one(eg["objects"], subtype="slab")
    assert slab["category"] == "other" and slab["net_volume_m3"] == pytest.approx(3.9)
    assert _one(og["objects"], subtype="column")["net_volume_m3"] == pytest.approx(0.27)
    assert _one(og["objects"], subtype="beam")["category"] == "other"
    assert len([o for o in og["objects"] if o["category"] == "stair"]) == 1  # nested part not counted


def test_sanitary_terminal_beats_flow_terminal(tmp_path):
    eg = _docs(build(tmp_path, "IFC4"))["00-EG"]
    assert [
        o["category"]
        for o in eg["objects"]
        if o["category"] in ("toilet", "other") and o.get("subtype") in (None, "sanitary")
    ] == ["toilet"]


def test_flow_terminals_used_when_no_sanitary_terminal(tmp_path):
    eg = _docs(build(tmp_path, "IFC2X3"))["00-EG"]
    sanitary = sorted(
        (o["category"], o.get("subtype"))
        for o in eg["objects"]
        if o["category"] == "toilet" or o.get("subtype") == "sanitary"
    )
    assert sanitary == [("other", "sanitary"), ("toilet", None)]
    assert len(probe._elements([eg], "sanitary")) == 2


@pytest.mark.parametrize("schema", ["IFC4", "IFC2X3"])
def test_mm_and_m_models_give_the_same_metres(tmp_path, schema):
    def summary(path):
        docs = _docs(path)
        out = []
        for part, d in sorted(docs.items()):
            out.append((part, [lv["elevation_m"] for lv in d["levels"]]))
            out += sorted((r["number"], round(r["area_m2"], 4), round(r["height_m"], 4)) for r in d["rooms"])
            out += sorted((round(w["length_m"], 4), round(w["net_volume_m3"], 4)) for w in d["walls"])
            out += sorted(
                (o["category"], o.get("subtype"), round(o["net_volume_m3"] or 0, 4)) for o in d["objects"]
            )
            out += sorted((o["kind"], o["width_m"]) for o in d["openings"])
        return out

    mm = summary(build(tmp_path, schema, "MILLI", name="mm"))
    m = summary(build(tmp_path, schema, None, name="m"))
    assert mm == m


def test_cache_is_used_on_second_read(tmp_path, monkeypatch):
    path = build(tmp_path)
    first = ifc.extract_records(path)
    monkeypatch.setattr(ifc, "_geometry", lambda *a, **k: pytest.fail("geometry recomputed"))
    assert ifc.extract_records(path) == first


def test_translate_all_parts_and_translate_part(tmp_path):
    path = build(tmp_path)
    # schema v0.3 (building.schema.v0.3.json): one document per IFC building, with all its storeys and the
    # elements without a storey
    (d,) = translate_all(str(path), str(tmp_path))
    validate(d)
    assert d["schema_version"] == "0.3" and d["drawing"]["route"] == "ifc" and d["drawing"]["part"] is None
    assert sorted(lv["name"] for lv in d["levels"]) == ["EG", "OG"]
    assert all(o["provenance"]["source_ref"] for o in d["rooms"] + d["walls"] + d["openings"] + d["objects"])
    assert any(o.get("level_id") is None for o in d["walls"] + d["openings"] + d["objects"])  # no-storey kept
    rooms = sorted(r["id"] for r in d["rooms"])
    assert sorted(r["id"] for r in translate(str(path), str(tmp_path))["rooms"]) == rooms
    with pytest.raises(KeyError, match="no part"):
        translate(str(path), str(tmp_path), part="01-OG")


def test_probe_reads_ifc_documents(tmp_path):
    docs = [d for d in translate_all(str(build(tmp_path)), str(tmp_path))]
    ask = lambda q: probe.answer(docs, "ifc_models", q)[1]  # noqa: E731
    assert ask("What is the net floor area of the room 'Office (101)' on 'EG', in m²?") == pytest.approx(19.5)
    assert ask("What is the total net floor area of all rooms (spaces) on 'EG', in m²?") == pytest.approx(
        25.5
    )
    assert ask("What is the total net volume of all slabs on 'EG', in m³?") == pytest.approx(3.9)
    assert ask("How many columns are there in the model 'Test'?") == 2
    assert ask("How many stairs are shown on the floor plan of 'OG'?") == 1


# ------------------------------------------------------------------ properties (text questions)


def test_element_properties_carry_psets(tmp_path):
    eg = _docs(build(tmp_path))["00-EG"]
    wall = next(w for w in eg["walls"] if w["length_m"] == pytest.approx(4.0))
    props = wall["properties"]
    assert props["ifc_class"] == "IfcWall"
    assert props["psets"]["Pset_WallCommon"] == {"FireRating": "EI60", "LoadBearing": True}
    assert "Qto_WallBaseQuantities" in props["psets"]


def test_extra_elements_are_other_and_not_counted(tmp_path):
    eg = _docs(build(tmp_path))["00-EG"]
    extras = sorted(
        o["subtype"] for o in eg["objects"] if o["subtype"] in ("covering", "buildingelementproxy")
    )
    assert extras == ["buildingelementproxy", "covering"]
    assert not any(o["flags"] for o in eg["objects"] if o["subtype"] == "covering")  # no no_geometry flag
    assert len(probe._elements([eg], "sanitary")) == 1  # IFC4: the IfcSanitaryTerminal only
    assert len(probe._elements([eg], "column")) == 0


def test_level_properties_hold_project_and_units(tmp_path):
    level = _docs(build(tmp_path))["00-EG"]["levels"][0]
    props = level["properties"]
    assert props["project"]["name"] == "Test"
    assert props["units_to_si"]["LENGTHUNIT"] == pytest.approx(0.001)


def test_cap_drops_largest_psets_first():
    props = {
        "ifc_class": "IfcWall",
        "psets": {"small": {"a": 1}, "big": {f"k{i}": "x" * 50 for i in range(50)}},
    }
    capped = ifc.cap_properties(props, max_bytes=500)
    assert list(capped["psets"]) == ["small"] and capped["psets_dropped"] == ["big"]


def test_document_budget_slims_only_extra_elements():
    big = {"ifc_class": "IfcFlowSegment", "psets": {"P": {f"k{i}": "x" * 100 for i in range(30)}}}
    doc = {
        "rooms": [],
        "walls": [
            {"id": "wall-1", "properties": {"ifc_class": "IfcWall", "psets": {"P": {"k": "x" * 3000}}}}
        ],
        "openings": [],
        "objects": [{"id": f"element-{i}", "properties": json.loads(json.dumps(big))} for i in range(5)],
    }
    recs = [{"kind": "element", "global_id": str(i), "quantities": {"Length": 2.0}} for i in range(5)]
    n = ifc.apply_document_budget(doc, recs, max_bytes=12_000)
    assert n >= 1
    slim = [o for o in doc["objects"] if o["properties"].get("slim")]
    assert len(slim) == n and all(o["properties"]["quantities"] == {"Length": 2.0} for o in slim)
    assert "psets" in doc["walls"][0]["properties"]  # structured elements are never slimmed


# ------------------------------------------------------------------ real model


@needs_data
@pytest.mark.parametrize("label, expected", [("KORIDOR (119)", 63.84), ("ELUTUBA+KÖÖK (3-1)", 25.25)])
def test_real_esplanades_quick_test(label, expected, monkeypatch):
    monkeypatch.setattr(ifc, "CACHE_DIR", INDEX_DIR / "_raw" / "ifc")  # reuse the real cache
    docs = translate_all("data/ifc_models/bsc-esplanades_1807-ep-ar-v18.ifc")
    q = f"What is the net floor area of the room '{label}' on '1.korrus', in m²?"
    assert probe.answer(docs, "ifc_models", q)[1] == pytest.approx(expected, rel=0.02)
    json.dumps(docs)  # serialisable
