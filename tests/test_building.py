"""Building contract and real parser round trips, reusing the repository's IFC model builder."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import ifcopenshell.api as api
import pytest
from test_ifc import Model

from translator import translate, translate_all
from translator.building import merge_buildings, migrate, page_views
from translator.extract import ifc
from translator.store import SchemaError, read_drawing, validate, write_drawing

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def legacy():
    return json.loads((ROOT / "blueprint-ingest/out/plan.level-1.json").read_text(encoding="utf-8"))


@pytest.fixture
def rich_model(tmp_path, monkeypatch):
    monkeypatch.setattr(ifc, "CACHE_DIR", tmp_path / "_cache")
    m = Model()
    m.building.Name = "Test building"
    ground, upper = m.storey("Ground", 0), m.storey("Upper", 3)
    room = m.element("IfcSpace", ground, box=(5, 4, 3), at=(10, 20, 0), name="101", LongName="Office")
    m.qto(room, "Qto_SpaceBaseQuantities", NetFloorArea=19.5)
    wall = m.element("IfcWall", ground, box=(4, 0.2, 3), name="Wall W1")
    m.qto(wall, "Qto_WallBaseQuantities", Length=4000, NetSideArea=12, NetVolume=2.4)
    pset = api.run("pset.add_pset", m.f, product=wall, name="Pset_WallCommon")
    api.run(
        "pset.edit_pset",
        m.f,
        pset=pset,
        properties={"FireRating": "EI60", "LoadBearing": True, "IsExternal": True},
    )
    concrete = m.f.create_entity("IfcMaterial", Name="Concrete", Category="Concrete")
    insulation = m.f.create_entity("IfcMaterial", Name="Insulation")
    layers = m.f.create_entity(
        "IfcMaterialLayerSet",
        MaterialLayers=[
            m.f.create_entity(
                "IfcMaterialLayer", Material=concrete, LayerThickness=150.0, IsVentilated=False
            ),
            m.f.create_entity(
                "IfcMaterialLayer", Material=insulation, LayerThickness=50.0, IsVentilated=False
            ),
        ],
        LayerSetName="W1",
    )
    api.run("material.assign_material", m.f, products=[wall], material=layers)
    opening = m.element("IfcOpeningElement", None, name="Void")
    api.run("feature.add_feature", m.f, feature=opening, element=wall)
    door = m.element("IfcDoor", ground, name="D1", OverallWidth=900.0, OverallHeight=2100.0)
    api.run("feature.add_filling", m.f, opening=opening, element=door)
    door_type = api.run("root.create_entity", m.f, ifc_class="IfcDoorType", name="D-900")
    door_type.Tag = "D01"
    api.run("type.assign_type", m.f, related_objects=[door], relating_type=door_type)
    m.element("IfcWindow", ground, name="Window", OverallWidth=1200.0, OverallHeight=1400.0)
    m.element("IfcColumn", upper, box=(0.3, 0.3, 3), name="Column")
    pipe = m.element("IfcPipeSegment", upper, name="Pipe P1")
    pump = m.element("IfcPump", upper, name="Pump P1")
    system = api.run("system.add_system", m.f, ifc_class="IfcDistributionSystem")
    system.Name = "Heating circuit"
    system.PredefinedType = "HEATING"
    api.run("system.assign_system", m.f, products=[pipe, pump], system=system)
    api.run("geometry.connect_element", m.f, relating_element=pipe, related_element=pump)
    pipe_port = api.run("system.add_port", m.f, element=pipe)
    pump_port = api.run("system.add_port", m.f, element=pump)
    api.run("system.connect_port", m.f, port1=pipe_port, port2=pump_port)
    api.run("root.create_entity", m.f, ifc_class="IfcRelSpaceBoundary", name="Room boundary")
    boundary = m.f.by_type("IfcRelSpaceBoundary")[0]
    boundary.RelatingSpace = room
    boundary.RelatedBuildingElement = wall
    boundary.PhysicalOrVirtualBoundary = "PHYSICAL"
    boundary.InternalOrExternalBoundary = "INTERNAL"
    m.f.create_entity(
        "IfcTable",
        Name="Door schedule",
        Rows=[
            m.f.create_entity(
                "IfcTableRow",
                RowCells=[m.f.create_entity("IfcLabel", "Mark"), m.f.create_entity("IfcLabel", "Width (mm)")],
                IsHeading=True,
            ),
            m.f.create_entity(
                "IfcTableRow",
                RowCells=[m.f.create_entity("IfcLabel", "D01"), m.f.create_entity("IfcReal", 900.0)],
                IsHeading=False,
            ),
        ],
    )
    path = m.save(tmp_path / "building.ifc")
    return path, {
        "room": room.GlobalId,
        "wall": wall.GlobalId,
        "door": door.GlobalId,
        "void": opening.GlobalId,
        "pipe": pipe.GlobalId,
        "pump": pump.GlobalId,
        "type": door_type.GlobalId,
        "building": m.building.GlobalId,
    }


def assert_rich(doc, ids):
    validate(doc)
    assert doc["schema_version"] == "0.3" and "drawing" not in doc
    assert len(doc["levels"]) == 2
    assert doc["project"]["building_name"] == "Test building"
    assert doc["project"]["ifc_building_global_id"] == ids["building"]
    assert doc["project"]["ifc_schema"] == "IFC4"
    wall = next(w for w in doc["walls"] if w["ifc_global_id"] == ids["wall"])
    assert wall["length_m"] == pytest.approx(4)
    assert wall["fire_rating"] == "EI60" and wall["is_load_bearing"] is True
    assert [layer["material"]["name"] for layer in wall["material_layers"]] == ["Concrete", "Insulation"]
    assert [layer["thickness_m"] for layer in wall["material_layers"]] == pytest.approx([0.15, 0.05])
    assert wall["properties"]["psets"]["Pset_WallCommon"]["LoadBearing"] is True
    assert next(q for q in wall["quantities"] if q["name"] == "Length")["original_value"] == 4000
    door = next(o for o in doc["openings"] if o["kind"] == "door")
    assert door["width_m"] == pytest.approx(0.9)
    assert door["type_id"] == f"ifc:{ids['type']}"
    assert door["wall_id"] == wall["id"]
    assert len(doc["types"]) == 1 and doc["types"][0]["instance_ids"] == [door["id"]]
    void = next(o for o in doc["openings"] if o["kind"] == "void")
    assert void["is_physical"] is False
    assert len(doc["systems"]) == 1
    assert set(doc["systems"][0]["component_ids"]) == {f"ifc:{ids['pipe']}", f"ifc:{ids['pump']}"}
    assert doc["systems"][0]["system_type"] == "heating"
    assert len([r for r in doc["relationships"] if r["kind"] == "connects"]) >= 2
    pipe = next(o for o in doc["objects"] if o["ifc_global_id"] == ids["pipe"])
    assert pipe["category"] == "pipe" and pipe["net_volume_m3"] is None
    assert pipe["fire_rating"] is None
    room = doc["rooms"][0]
    assert room["area_m2"] == pytest.approx(19.5)
    assert room["area_computed_m2"] == pytest.approx(20)
    assert min(p[0] for p in room["polygon_m"]) == pytest.approx(10)
    assert room["boundary_ids"] == [wall["id"]]
    assert len(doc["schedules"]) == 1
    assert doc["schedules"][0]["rows"][0]["cells"] == ["D01", 900.0]
    assert doc["schedules"][0]["rows"][0]["entity_ids"] == []


def test_python_ifc_end_to_end(rich_model, tmp_path):
    path, ids = rich_model
    docs = translate_all(str(path), str(tmp_path))
    assert len(docs) == 1
    assert_rich(docs[0], ids)
    saved = write_drawing(docs[0], tmp_path / "index")
    assert len(list((tmp_path / "index").rglob("*.json"))) == 1
    assert_rich(read_drawing(saved), ids)


def test_javascript_ifc_end_to_end(rich_model, tmp_path):
    if not shutil.which("node") or not (ROOT / "blueprint-ingest/node_modules").exists():
        pytest.skip("Install blueprint-ingest dependencies to test JavaScript ingestion")
    path, ids = rich_model
    target = tmp_path / "javascript.json"
    run = subprocess.run(
        [
            "node",
            str(ROOT / "blueprint-ingest/src/index.js"),
            str(path),
            "--out",
            str(target),
            "--mode",
            "create",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GEMINI_API_KEY": ""},
    )
    assert run.returncode == 0, run.stderr
    assert_rich(read_drawing(target), ids)


def test_migration_and_multiple_sources(legacy):
    before = copy.deepcopy(legacy)
    a = migrate(legacy)
    other = copy.deepcopy(legacy)
    other["drawing"]["file"] = "data/other/another.pdf"
    other["drawing"]["read_from"] = other["drawing"]["file"]
    other["drawing"]["route"] = "vector_pdf"
    b = migrate(other)
    result = merge_buildings([a, b])
    validate(result)
    assert len(result["source_documents"]) >= 2
    assert len(result["drawings"]) == 2
    assert legacy == before
    assert len(page_views(result)) == 2
    assert migrate(result) == result


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(typo=1),
        lambda d: d["rooms"][0].update(area=1),
        lambda d: d["rooms"][0].update(level_id="missing"),
        lambda d: d["rooms"][0].update(unit_id="missing"),
        lambda d: d["rooms"][0].pop("source_refs"),
        lambda d: d["rooms"][0]["source_refs"][0].update(source_document_id="missing"),
        lambda d: d["rooms"][0].update(material_layers=[{"material": {"name": None}, "thickness_m": -1}]),
        lambda d: d["levels"].append(copy.deepcopy(d["levels"][0])),
    ],
)
def test_strict_schema_and_foreign_keys(legacy, mutation):
    doc = migrate(legacy)
    mutation(doc)
    with pytest.raises(SchemaError):
        validate(doc)


def test_nullable_material_schedule_and_system(legacy):
    doc = migrate(legacy)
    entity = doc["rooms"][0]
    entity["material_layers"] = [{"material": {"name": None}, "thickness_m": None}]
    refs = entity["source_refs"]
    common = {"confidence": 1, "provenance": entity["provenance"], "source_refs": refs}
    doc["systems"] = [
        {**common, "id": "system-test", "system_type": "unknown", "component_ids": [entity["id"]]}
    ]
    doc["schedules"] = [
        {
            **common,
            "id": "schedule-test",
            "schedule_type": "door",
            "source_document_id": refs[0]["source_document_id"],
            "headers": ["Mark", "Width (mm)"],
            "rows": [
                {
                    "id": "row1",
                    "type_mark": "D01",
                    "dimensions": {"width_m": 0.9},
                    "quantity": 4,
                    "cells": ["D01", "900"],
                    "entity_ids": [],
                    "source_refs": refs,
                }
            ],
        }
    ]
    validate(doc)
    assert len(doc["openings"]) == len(migrate(legacy)["openings"])
    doc["systems"][0]["component_ids"] = ["missing"]
    with pytest.raises(SchemaError, match="reference"):
        validate(doc)


def test_two_buildings_do_not_mix_levels(tmp_path, monkeypatch):
    monkeypatch.setattr(ifc, "CACHE_DIR", tmp_path / "_cache")
    m = Model()
    a = m.storey("A", 0)
    m.element("IfcSpace", a, name="A room")
    m.building = api.run("root.create_entity", m.f, ifc_class="IfcBuilding", name="Second")
    b = m.storey("B", 3)
    m.element("IfcSpace", b, name="B room")
    docs = translate_all(str(m.save(tmp_path / "two.ifc")), str(tmp_path))
    assert len(docs) == 2
    assert sorted([d["levels"][0]["name"] for d in docs]) == ["A", "B"]
    assert all(len(d["rooms"]) == 1 for d in docs)


def test_v02_is_validated_before_migration(legacy, tmp_path):
    target = tmp_path / "legacy.json"
    target.write_text(json.dumps(legacy), encoding="utf-8")
    assert read_drawing(target)["schema_version"] == "0.3"
    legacy["rooms"][0]["unknown_core_field"] = 3
    target.write_text(json.dumps(legacy), encoding="utf-8")
    with pytest.raises(SchemaError):
        read_drawing(target)


def test_statistics_require_complete_measurements(rich_model):
    from translator.statistics import calculate_statistics

    doc = translate(str(rich_model[0]))
    stats = {q["name"]: q for q in doc["statistics"]["quantities"]}
    assert stats["number_of_doors"]["value"] == 1
    assert stats["number_of_rooms"]["quality"] == "exact"
    assert stats["total_room_area"]["value"] == 19.5
    assert stats["total_room_area"]["entity_ids"] == [doc["rooms"][0]["id"]]
    doc["rooms"][0]["area_m2"] = None
    assert "total_room_area" not in {q["name"] for q in calculate_statistics(doc)["quantities"]}


@pytest.mark.parametrize("schema", ["IFC4", "IFC2X3"])
def test_javascript_existing_ifc_fixtures_in_si(tmp_path, schema):
    from test_ifc import build

    for unit in ("MILLI", None):
        source = build(tmp_path, schema=schema, unit=unit, name=f"model-{unit}")
        output = tmp_path / f"output-{unit}.json"
        run = subprocess.run(
            ["node", str(ROOT / "blueprint-ingest/src/index.js"), str(source), "--out", str(output)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "GEMINI_API_KEY": ""},
        )
        assert run.returncode == 0, run.stderr
        doc = read_drawing(output)
        assert len(doc["levels"]) == 2
        assert next(o for o in doc["openings"] if o["kind"] == "door")["width_m"] == pytest.approx(0.9)
        assert next(r for r in doc["rooms"] if r["number"] == "101")["area_m2"] == pytest.approx(19.5)
        assert len([o for o in doc["objects"] if o["category"] == "stair"]) == 1


def test_source_quantity_unit_override_and_nested_properties(tmp_path, monkeypatch):
    monkeypatch.setattr(ifc, "CACHE_DIR", tmp_path / "_cache")
    m = Model()
    ground = m.storey("Ground", 0)
    wall = m.element("IfcWall", ground, name="Unmeshed")
    area_unit = api.run("unit.add_si_unit", m.f, unit_type="AREAUNIT", prefix="MILLI")
    q = m.f.create_entity("IfcQuantityArea", Name="NetSideArea", Unit=area_unit, AreaValue=12000000.0)
    qset = m.f.create_entity(
        "IfcElementQuantity", GlobalId=ifc._ue().ifcopenshell.guid.new(), Name="Custom", Quantities=[q]
    )
    m.f.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifc._ue().ifcopenshell.guid.new(),
        RelatedObjects=[wall],
        RelatingPropertyDefinition=qset,
    )
    pset = api.run("pset.add_pset", m.f, product=wall, name="LargePset")
    api.run("pset.edit_pset", m.f, pset=pset, properties={"LongValue": "preserved" * 1000})
    source = m.save(tmp_path / "units.ifc")
    doc = translate(str(source))
    assert doc["walls"][0]["net_side_area_m2"] == pytest.approx(12)
    assert len(doc["walls"][0]["properties"]["psets"]["LargePset"]["LongValue"]) == 9000
    output = tmp_path / "js-units.json"
    run = subprocess.run(
        ["node", str(ROOT / "blueprint-ingest/src/index.js"), str(source), "--out", str(output)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GEMINI_API_KEY": ""},
    )
    assert run.returncode == 0, run.stderr
    assert read_drawing(output)["walls"][0]["net_side_area_m2"] == pytest.approx(12)


@pytest.mark.parametrize("format", ["dxf", "pdf"])
def test_javascript_actual_vector_parsers(tmp_path, format):
    import pymupdf
    from test_swiss_dxf import Plan

    source = tmp_path / f"plan.{format}"
    if format == "dxf":
        plan = Plan()
        plan.room(0, 0, 4, 4, name="Office", number="101", area=16)
        plan.save(source)
    else:
        pdf = pymupdf.open()
        for page_number in range(2):
            page = pdf.new_page()
            page.insert_text(
                (50, 50),
                f"Architectural floor plan level {page_number + 1}. Office room 101. "
                "Source drawing information and dimensions.",
            )
            for i in range(60):
                page.draw_line((50, 100 + i * 3), (300, 100 + i * 3))
        pdf.save(source)
    output = tmp_path / "vector-building.json"
    run = subprocess.run(
        ["node", str(ROOT / "blueprint-ingest/src/index.js"), str(source), "--out", str(output)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GEMINI_API_KEY": ""},
    )
    assert run.returncode == 0, run.stderr
    doc = read_drawing(output)
    assert len(doc["drawings"]) == (2 if format == "pdf" else 1)
    assert all(d["route"] == ("vector_pdf" if format == "pdf" else "dxf") for d in doc["drawings"])


def test_query_geometry_rejects_unregistered_source_frames(legacy):
    from answering_agent.tools import geometry_operation

    doc = migrate(legacy)
    a, b = doc["rooms"][:2]
    a["source_drawing_id"], b["source_drawing_id"] = "frame-a", "frame-b"
    a["polygon_m"] = b["polygon_m"] = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert "error" in geometry_operation(doc, a["id"], b["id"], "distance")


def test_partial_source_coverage_cannot_certify_building_absence(legacy):
    from answering_agent.tools import building_summary, check_coverage, query_entities

    doc = migrate(legacy)
    doc["drawings"].append({**doc["drawings"][0], "id": "another-unanalysed-frame"})
    assert check_coverage(doc, "rooms")["absence_verified"] is False
    assert query_entities(doc, "rooms")["coverage"]["absence_verified"] is False
    assert building_summary(doc)["building_id"] == doc["building_id"]
