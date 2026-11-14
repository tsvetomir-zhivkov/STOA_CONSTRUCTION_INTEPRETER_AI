"""IfcOpenShell-specific facts, separated from the library-independent building model."""

from __future__ import annotations

import math

import ifcopenshell.util.element as ue
import ifcopenshell.util.placement as up
import ifcopenshell.util.unit as uu

CATEGORIES = {
    "IfcColumn": "column",
    "IfcBeam": "beam",
    "IfcSlab": "slab",
    "IfcRoof": "roof",
    "IfcFooting": "footing",
    "IfcPile": "pile",
    "IfcMember": "structural_member",
    "IfcPlate": "structural_member",
    "IfcBuildingElementPart": "structural_member",
    "IfcMechanicalFastener": "structural_connection",
    "IfcDiscreteAccessory": "structural_connection",
    "IfcRailing": "railing",
    "IfcFurniture": "furniture",
    "IfcFurnishingElement": "furniture",
    "IfcPipeSegment": "pipe",
    "IfcPipeFitting": "pipe",
    "IfcDuctSegment": "duct",
    "IfcDuctFitting": "duct",
    "IfcCableSegment": "cable",
    "IfcCableCarrierSegment": "cable_tray",
    "IfcElectricDistributionBoard": "distribution_board",
    "IfcLightFixture": "light_fixture",
    "IfcPump": "pump",
    "IfcValve": "valve",
    "IfcFan": "ventilation_equipment",
    "IfcAirTerminal": "ventilation_equipment",
    "IfcBoiler": "heating_equipment",
    "IfcSpaceHeater": "radiator",
    "IfcChiller": "cooling_equipment",
    "IfcFireSuppressionTerminal": "fire_protection_equipment",
    "IfcSensor": "sensor",
    "IfcElectricAppliance": "electrical_equipment",
    "IfcTransportElement": "elevator",
}
SYSTEM_TYPES = {
    "HEATING": "heating",
    "COOLING": "cooling",
    "VENTILATION": "ventilation",
    "AIRCONDITIONING": "ventilation",
    "DOMESTICCOLDWATER": "plumbing",
    "DOMESTICHOTWATER": "plumbing",
    "WATERSUPPLY": "plumbing",
    "DRAINAGE": "drainage",
    "SEWAGE": "drainage",
    "RAINWATER": "drainage",
    "ELECTRICAL": "electrical",
    "LIGHTING": "lighting",
    "FIREPROTECTION": "fire_protection",
    "COMMUNICATION": "communication",
    "DATA": "communication",
}


def value(v):
    """Preserve nested typed IFC values without stringification or size truncation."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {str(k): value(x) for k, x in v.items() if k != "id"}
    if isinstance(v, (list, tuple)):
        return [value(x) for x in v]
    if hasattr(v, "wrappedValue"):
        return value(v.wrappedValue)
    if hasattr(v, "is_a"):
        return {"ifc_class": v.is_a(), "step_id": v.id(), "global_id": getattr(v, "GlobalId", None)}
    raise TypeError(f"Unsupported IFC property value: {type(v).__name__}")


def ancestor(e, cls):
    seen = set()
    while e is not None and e.id() not in seen:
        if e.is_a(cls):
            return e
        seen.add(e.id())
        e = ue.get_container(e, should_get_direct=True) or ue.get_aggregate(e)
    return None


def guid(e):
    return getattr(e, "GlobalId", None) if e is not None else None


def material_data(e, length):
    mat = ue.get_material(e, should_skip_usage=True)
    if mat is None:
        return [], []

    def one(m):
        return {
            "name": getattr(m, "Name", None),
            "category": getattr(m, "Category", None),
            "description": getattr(m, "Description", None),
        }

    if mat.is_a("IfcMaterialLayerSet"):
        layers = []
        for layer in mat.MaterialLayers or []:
            layers.append(
                {
                    "material": one(layer.Material),
                    "thickness_m": layer.LayerThickness * length
                    if layer.LayerThickness is not None
                    else None,
                    "function": getattr(layer, "Category", None),
                    "is_structural": None,
                    "is_insulation": None,
                    "is_ventilated": layer.IsVentilated if isinstance(layer.IsVentilated, bool) else None,
                    "properties": {
                        "step_id": layer.id(),
                        "name": getattr(layer, "Name", None),
                        "description": getattr(layer, "Description", None),
                        "original_thickness": layer.LayerThickness,
                    },
                }
            )
        return [one(layer.Material) for layer in mat.MaterialLayers or []], layers
    if mat.is_a("IfcMaterialList"):
        return [one(m) for m in mat.Materials], []
    for attr in ("MaterialConstituents", "MaterialProfiles"):
        members = getattr(mat, attr, None)
        if members is not None:
            return [
                {**one(member.Material), "properties": value(member.get_info())} for member in members
            ], []
    return [one(mat)], []


def quantities(e, model, scales):
    result = []
    definitions = [
        r.RelatingPropertyDefinition
        for r in getattr(e, "IsDefinedBy", ())
        if r.is_a("IfcRelDefinesByProperties")
    ]
    definitions += list(getattr(ue.get_type(e), "HasPropertySets", ()) or ())
    kinds = {
        "IfcQuantityLength": ("m", "LENGTHUNIT", "LengthValue"),
        "IfcQuantityArea": ("m2", "AREAUNIT", "AreaValue"),
        "IfcQuantityVolume": ("m3", "VOLUMEUNIT", "VolumeValue"),
        "IfcQuantityCount": ("count", None, "CountValue"),
    }
    for qset in definitions:
        if not qset.is_a("IfcElementQuantity"):
            continue
        for q in qset.Quantities:
            if q.is_a() not in kinds:
                continue
            unit, unit_type, attr = kinds[q.is_a()]
            original = getattr(q, attr)
            explicit = getattr(q, "Unit", None)
            factor = scales[unit_type] if unit_type else 1
            if explicit is not None and unit_type:
                # convert_unit handles SI prefixes, area/volume powers and conversion-based units.
                target = model.create_entity(
                    "IfcSIUnit",
                    UnitType=unit_type,
                    Name={"m": "METRE", "m2": "SQUARE_METRE", "m3": "CUBIC_METRE"}[unit],
                )
                converted = uu.convert_unit(original, explicit, target)
                model.remove(target)
            else:
                converted = original * factor if original is not None and factor is not None else None
            result.append(
                {
                    "name": q.Name,
                    "value": converted,
                    "unit": unit,
                    "origin": "source",
                    "method": f"{qset.Name}.{q.Name}",
                    "source_refs": [],
                    "original_value": original,
                    "original_unit": str(explicit) if explicit else unit_type,
                }
            )
    return result


def facts(e, model, scales, mesh=None):
    t = ue.get_type(e)
    psets = value(ue.get_psets(e))
    flat = {key: v for p in psets.values() if isinstance(p, dict) for key, v in p.items()}
    materials, layers = material_data(e, scales["LENGTHUNIT"])
    data = {
        "ifc_global_id": guid(e),
        "ifc_class": e.is_a(),
        "description": getattr(e, "Description", None),
        "predefined_type": getattr(e, "PredefinedType", None),
        "type_name": getattr(t, "Name", None),
        "type_mark": getattr(t, "Tag", None),
        "type_id": f"ifc:{guid(t)}" if guid(t) else None,
        "materials": materials,
        "material_layers": layers,
        "fire_rating": flat.get("FireRating"),
        "acoustic_rating": flat.get("AcousticRating"),
        "is_load_bearing": flat.get("LoadBearing") if isinstance(flat.get("LoadBearing"), bool) else None,
        "quantities": quantities(e, model, scales),
        "classifications": [],
        "properties": {
            "psets": psets,
            "instance_psets": value(ue.get_psets(e, should_inherit=False)),
            "type_psets": value(ue.get_psets(t)) if t else {},
            "object_type": getattr(e, "ObjectType", None),
            "original_name": getattr(e, "Name", None),
            "original_parent_global_id": guid(
                ue.get_container(e, should_get_direct=True) or ue.get_aggregate(e)
            ),
        },
    }
    for rel in getattr(e, "HasAssociations", ()):
        if rel.is_a("IfcRelAssociatesClassification"):
            c = rel.RelatingClassification
            system = getattr(c, "ReferencedSource", None)
            data["classifications"].append(
                {
                    "system": getattr(system, "Name", None),
                    "name": getattr(c, "Name", None),
                    "code": getattr(c, "Identification", getattr(c, "ItemReference", None)),
                    "location": getattr(c, "Location", None),
                }
            )
    if e.is_a("IfcWall"):
        data["is_external"] = flat.get("IsExternal") if isinstance(flat.get("IsExternal"), bool) else None
    if e.is_a("IfcDoor") or e.is_a("IfcWindow"):
        data["operation"] = getattr(t, "OperationType", None) or flat.get("OperationType")
        data["hardware_group"] = flat.get("HardwareGroup")
        data["is_entrance"] = flat.get("IsExternal") if isinstance(flat.get("IsExternal"), bool) else None
        if flat.get("FrameMaterial") is not None:
            data["frame_material"] = {"name": str(flat["FrameMaterial"])}
        if flat.get("GlazingType") is not None:
            data["glazing"] = {"description": str(flat["GlazingType"])}
        accessible = flat.get("HandicapAccessible")
        if isinstance(accessible, bool):
            data["accessibility"] = {"is_accessible": accessible}
    if e.is_a("IfcSpace"):
        data["finishes"] = [
            {"surface": surface, "description": flat[key]}
            for surface, key in [
                ("floor", "FloorCovering"),
                ("wall", "WallCovering"),
                ("ceiling", "CeilingCovering"),
            ]
            if isinstance(flat.get(key), str)
        ]
    for cls, category in CATEGORIES.items():
        try:
            matches = e.is_a(cls)
        except RuntimeError:
            matches = False
        if matches:
            data["category"] = category
            break
    placement = getattr(e, "ObjectPlacement", None)
    geometry = {"coordinate_system": "world", "unit": "m", "bbox": None, "footprints": [], "placement": None}
    if placement is not None:
        matrix = up.get_local_placement(placement)
        matrix[:3, 3] *= scales["LENGTHUNIT"]
        geometry["placement"] = matrix.tolist()
    if mesh is not None:
        from shapely.geometry import MultiPolygon

        from translator.extract.ifc import footprint_lowest, footprint_projected

        verts, faces = mesh
        geometry["bbox"] = verts.min(axis=0).tolist() + verts.max(axis=0).tolist()
        footprint = (
            footprint_lowest(verts, faces) if e.is_a("IfcSpace") else footprint_projected(verts, faces)
        )
        if footprint is not None:
            polygons = list(footprint.geoms) if isinstance(footprint, MultiPolygon) else [footprint]
            geometry["footprints"] = [
                {
                    "exterior": [list(p) for p in poly.exterior.coords],
                    "holes": [[list(p) for p in ring.coords] for ring in poly.interiors],
                }
                for poly in polygons
                if poly.geom_type == "Polygon"
            ]
    data["geometry"] = geometry
    return data


def extract(model, scales, meshes):
    elements = (
        list(model.by_type("IfcElement"))
        + list(model.by_type("IfcSpace"))
        + list(model.by_type("IfcBuildingStorey"))
    )
    meta = {e.GlobalId: facts(e, model, scales, meshes.get(e.id())) for e in elements}
    buildings = list(model.by_type("IfcBuilding"))
    membership = {e.GlobalId: guid(ancestor(e, "IfcBuilding")) for e in elements}
    types = []
    for t in model.by_type("IfcTypeObject"):
        item = facts(t, model, scales)
        item.update(
            name=t.Name,
            entity_kind="door"
            if t.is_a().startswith("IfcDoor")
            else "window"
            if t.is_a().startswith("IfcWindow")
            else "wall"
            if t.is_a().startswith("IfcWall")
            else "other",
        )
        item.pop("category", None)
        types.append(item)
    systems = []
    for system in model.by_type("IfcSystem"):
        item = facts(system, model, scales)
        item.update(
            name=system.Name,
            system_type=SYSTEM_TYPES.get(getattr(system, "PredefinedType", None), "unknown"),
            component_ids=[
                f"ifc:{e.GlobalId}"
                for rel in system.IsGroupedBy
                for e in rel.RelatedObjects
                if hasattr(e, "GlobalId")
            ],
        )
        systems.append(item)
    relations = []
    tables = []
    for table in model.by_type("IfcTable"):
        rows = list(table.Rows or [])
        columns = getattr(table, "Columns", None) or []
        headers = [column.Name or column.Identifier or "" for column in columns]
        if not headers and rows and rows[0].IsHeading:
            headers = [str(value(cell)) for cell in rows.pop(0).RowCells or []]
        tables.append(
            {
                "step_id": table.id(),
                "name": table.Name,
                "headers": headers,
                "rows": [[value(cell) for cell in row.RowCells or []] for row in rows],
            }
        )

    def add(rel, kind, a, b):
        if guid(a) and guid(b):
            relations.append(
                {
                    "id": f"ifc-rel:{rel.GlobalId}:{guid(a)}:{guid(b)}",
                    "kind": kind,
                    "from_id": f"ifc:{guid(a)}",
                    "to_id": f"ifc:{guid(b)}",
                    "source_refs": [],
                }
            )

    for rel in model.by_type("IfcRelationship"):
        if rel.is_a("IfcRelAggregates"):
            for child in rel.RelatedObjects:
                add(rel, "aggregates", rel.RelatingObject, child)
        elif rel.is_a("IfcRelContainedInSpatialStructure"):
            for child in rel.RelatedElements:
                add(rel, "contains", rel.RelatingStructure, child)
        elif rel.is_a("IfcRelVoidsElement"):
            add(rel, "voids", rel.RelatedOpeningElement, rel.RelatingBuildingElement)
        elif rel.is_a("IfcRelFillsElement"):
            add(rel, "fills", rel.RelatedBuildingElement, rel.RelatingOpeningElement)
        elif rel.is_a("IfcRelSpaceBoundary"):
            add(rel, "bounds", rel.RelatingSpace, rel.RelatedBuildingElement)
        elif rel.is_a("IfcRelConnectsElements"):
            add(rel, "connects", rel.RelatingElement, rel.RelatedElement)
        elif rel.is_a("IfcRelConnectsPorts"):
            # Preserve raw ports as well as connections resolvable to physical components.
            def owner(port):
                rels = getattr(port, "ContainedIn", ())
                if rels:
                    return rels[0].RelatedElement
                nests = getattr(port, "Nests", ())
                return nests[0].RelatingObject if nests else None

            add(rel, "connects", owner(rel.RelatingPort), owner(rel.RelatedPort))
    return {
        "entities": meta,
        "tables": tables,
        "membership": membership,
        "types": types,
        "systems": systems,
        "relationships": relations,
        "raw_relationships": [value(r.get_info()) for r in model.by_type("IfcRelationship")],
        "buildings": [
            {
                "global_id": b.GlobalId,
                "name": b.Name,
                "description": b.Description,
                "object_type": getattr(b, "ObjectType", None),
                "address": value(b.BuildingAddress.get_info()) if b.BuildingAddress else None,
                "psets": value(ue.get_psets(b)),
            }
            for b in buildings
        ],
        "applications": [value(a.get_info()) for a in model.by_type("IfcApplication")],
        "units": [
            value(u.get_info()) for assignment in model.by_type("IfcUnitAssignment") for u in assignment.Units
        ],
    }


def enrich_building(doc, data, building_guid=None):
    """Resolve extracted GUID relationships after source fragments have been assembled."""
    from translator.building import COLLECTIONS

    meta = data["metadata"]
    source = next(s for s in doc["source_documents"] if s["format"] == "ifc")
    source["ifc_schema"] = data["schema"]
    source["units"] = {"to_si": data["units"], "original": meta["units"]}
    source["properties"] = {"relationships": meta["raw_relationships"], "applications": meta["applications"]}
    ref = {
        "source_document_id": source["id"],
        "drawing_id": None,
        "source_ref": source["file"],
        "extractor": "ifc",
    }
    context = data.get("context", {})
    project, site = context.get("project", {}), context.get("site", {})
    building = next((b for b in meta["buildings"] if b["global_id"] == building_guid), {})
    context = {**context, 'building': building}
    doc["project"] = {
        "name": project.get("name"),
        "description": project.get("description"),
        "building_name": building.get("name"),
        "building_type": building.get("object_type"),
        "site_name": site.get("name"),
        "ifc_schema": data["schema"],
        "ifc_project_global_id": project.get("global_id"),
        "ifc_building_global_id": building_guid,
        "ifc_site_global_id": site.get("global_id"),
        "source_refs": [ref],
        "properties": context,
        "authoring_software": ", ".join(a.get("ApplicationFullName") or "" for a in meta["applications"])
        or None,
    }
    address = building.get("address") or site.get("address")
    if address:
        doc["project"]["address"] = {
            "street": ", ".join(address.get("AddressLines") or []) or None,
            "postal_code": address.get("PostalCode"),
            "city": address.get("Town"),
            "region": address.get("Region"),
            "country": address.get("Country"),
        }
    for c in COLLECTIONS[:8]:
        for e in doc[c]:
            details = meta["entities"].get(e.get("ifc_global_id"))
            if not details:
                continue
            details = dict(details)
            if c != "objects":
                details.pop("category", None)
            e["properties"] = {**e.get("properties", {}), **details.pop("properties")}
            old_quantities = e.pop("quantities", [])
            e.update(details)
            e["quantities"].extend(
                q for q in old_quantities if q["name"] not in {x["name"] for x in e["quantities"]}
            )
            for q in e["quantities"]:
                q["source_refs"] = e["source_refs"]
            for layer in e["material_layers"]:
                layer["source_refs"] = e["source_refs"]
            for material in e["materials"]:
                material["source_refs"] = e["source_refs"]
            field_names = {
                "NetFloorArea": "area_m2",
                "GrossSideArea": "gross_area_m2",
                "NetSideArea": "net_side_area_m2",
                "NetVolume": "volume_m3" if c == "rooms" else "net_volume_m3",
                "GrossVolume": "gross_volume_m3",
                "Length": "length_m",
                "Height": "height_m",
                "Width": "thickness_m" if c == "walls" else "width_m",
            }
            allowed = {
                "rooms": {"area_m2", "volume_m3", "height_m"},
                "walls": {
                    "gross_area_m2",
                    "net_side_area_m2",
                    "net_volume_m3",
                    "gross_volume_m3",
                    "length_m",
                    "height_m",
                    "thickness_m",
                },
                "objects": {"net_volume_m3", "length_m", "height_m", "width_m"},
                "openings": {"width_m", "height_m"},
            }.get(c, set())
            for q in e["quantities"]:
                field = field_names.get(q["name"])
                if field in allowed and q["origin"] == "source" and q["value"] is not None:
                    e[field] = q["value"]
                    if field == "area_m2":
                        e["area_written_m2"] = q["value"]
            for quantity in e["quantities"]:
                field = quantity["name"]
                if field not in e or not field.endswith(("_m", "_m2", "_m3")):
                    continue
                quantity["value"] = e[field]
                source_quantity = next(
                    (
                        q
                        for q in e["quantities"]
                        if field_names.get(q["name"]) == field
                        and q["origin"] == "source"
                        and q["value"] is not None
                    ),
                    None,
                )
                if source_quantity:
                    quantity.update(origin="source", method=source_quantity["method"])
                elif c == "openings" and field in ("width_m", "height_m"):
                    quantity.update(
                        origin="source",
                        method="IFC OverallWidth" if field == "width_m" else "IFC OverallHeight",
                    )
                elif c == "levels" and field == "elevation_m":
                    quantity.update(origin="source", method="IfcBuildingStorey.Elevation")
                elif quantity["origin"] == "unknown" and (e.get("geometry") or {}).get("bbox"):
                    quantity.update(
                        origin="calculated", method="world-coordinate geometry; see provenance.details"
                    )
            if c == "rooms" and e["type"] == "unknown":
                e["is_outdoor"] = None
    physical = {e["id"]: e for c in COLLECTIONS[:8] for e in doc[c]}
    used_types = {e.get("type_id") for e in physical.values()}

    def base(item):
        return {
            **item,
            "id": f"ifc:{item['ifc_global_id']}",
            "confidence": 1.0,
            "provenance": {"extractor": "ifc", "source_ref": item["ifc_global_id"], "file": source["file"]},
            "source_refs": [{**ref, "source_ref": item["ifc_global_id"]}],
            "status": "active",
        }

    doc["types"] = [
        {
            **base(t),
            "instance_ids": [
                e["id"] for e in physical.values() if e.get("type_id") == f"ifc:{t['ifc_global_id']}"
            ],
        }
        for t in meta["types"]
        if f"ifc:{t['ifc_global_id']}" in used_types or len(meta["buildings"]) <= 1
    ]
    doc["systems"] = []
    for s in meta["systems"]:
        component_ids = [i for i in s["component_ids"] if i in physical]
        if component_ids or len(meta["buildings"]) <= 1:
            item = {
                **base(s),
                "component_ids": component_ids,
                "level_ids": sorted(
                    {physical[i]["level_id"] for i in component_ids if physical[i].get("level_id")}
                ),
            }
            doc["systems"].append(item)
            for i in component_ids:
                physical[i].setdefault("system_ids", []).append(item["id"])
    known = (
        set(physical)
        | {doc["building_id"]}
        | {t["id"] for t in doc["types"]}
        | {s["id"] for s in doc["systems"]}
    )
    doc["relationships"] = [
        {**r, "source_refs": [ref]}
        for r in meta["relationships"]
        if r["from_id"] in known and r["to_id"] in known
    ]
    for r in doc["relationships"]:
        a, b = physical.get(r["from_id"]), physical.get(r["to_id"])
        if r["kind"] in ("contains", "aggregates") and b is not None:
            b["parent_id"] = r["from_id"]
        if r["kind"] == "voids" and a is not None and b is not None:
            a["wall_id"] = b["id"] if b in doc["walls"] else None
            a["level_id"] = a.get("level_id") or b.get("level_id")
        if r["kind"] == "bounds" and a is not None and b is not None:
            a.setdefault("boundary_ids", []).append(b["id"])
            b.setdefault("room_ids", []).append(a["id"])
    for r in doc["relationships"]:
        if r["kind"] == "fills":
            a, b = physical.get(r["from_id"]), physical.get(r["to_id"])
            if a is not None and b is not None:
                a["wall_id"] = b.get("wall_id")
    for table in meta.get("tables", []):
        sid = f"{source['id']}:table:{table['step_id']}"
        table_ref = {**ref, "source_ref": f"#{table['step_id']}"}
        name = (table["name"] or "").lower()
        kind = next((kind for kind in ("door", "window", "material", "equipment") if kind in name), "other")
        doc["schedules"].append(
            {
                "id": sid,
                "name": table["name"],
                "schedule_type": kind,
                "source_document_id": source["id"],
                "source_sheet_id": None,
                "headers": table["headers"],
                "rows": [
                    {"id": f"{sid}:row:{i}", "cells": cells, "entity_ids": [], "source_refs": [table_ref]}
                    for i, cells in enumerate(table["rows"])
                ],
                "confidence": 1.0,
                "provenance": {"extractor": "ifc", "source_ref": table_ref["source_ref"]},
                "source_refs": [table_ref],
            }
        )
    from translator.statistics import calculate_statistics

    doc["statistics"] = calculate_statistics(doc)
    return doc
