"""Reproducibly extend the frozen v0.2 vocabulary into the v0.3 building contract."""

import json
from pathlib import Path

HERE = Path(__file__).parent
s = json.loads((HERE / "drawing.schema.json").read_text(encoding="utf-8"))
d = s["$defs"]


def ref(name):
    return {"$ref": f"#/$defs/{name}"}


def arr(item):
    return {"type": "array", "items": item}


def obj(properties, required=()):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


string = {"type": ["string", "null"]}
number = {"type": ["number", "null"]}
boolean = {"type": ["boolean", "null"]}
ids = arr({"type": "string"})
raw = {"type": "object", "additionalProperties": True}
d["source_reference"] = obj(
    {
        "source_document_id": {"type": "string"},
        "drawing_id": string,
        "source_ref": {"type": "string"},
        "extractor": string,
        "details": raw,
    },
    ("source_document_id", "drawing_id", "source_ref"),
)
refs = arr(ref("source_reference"))
d["quantity"] = obj(
    {
        "name": {"type": "string"},
        "value": number,
        "unit": {"enum": ["m", "m2", "m3", "deg", "count", "1"]},
        "origin": {"enum": ["source", "calculated", "estimated", "unknown"]},
        "quality": {"enum": ["exact", "calculated", "estimated", "unknown"]},
        "method": string,
        "source_refs": refs,
        "entity_ids": ids,
        "original_value": number,
        "original_unit": string,
    },
    ("name", "value", "unit", "origin", "method", "source_refs"),
)
d["dimensions"] = obj({k: number for k in ("width_m", "depth_m", "height_m", "length_m", "diameter_m")})
d["geometry"] = obj(
    {
        "coordinate_system": {"enum": ["world", "source", "unknown"]},
        "unit": {"enum": ["m", "px"]},
        "bbox": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 6, "maxItems": 6},
        "footprints": arr(
            obj({"exterior": ref("polygon"), "holes": arr(ref("polygon"))}, ("exterior", "holes"))
        ),
        "placement": {
            "type": ["array", "null"],
            "items": arr({"type": "number"}),
            "minItems": 4,
            "maxItems": 4,
        },
        "source_refs": refs,
    },
    ("coordinate_system", "unit"),
)
# The old schema names its 2D polygon definition polygon; keep all original coordinate fields.
if "polygon" not in d:
    d["polygon"] = {"type": ["array", "null"], "items": ref("point2"), "minItems": 3}
d["material"] = obj(
    {"name": string, "category": string, "description": string, "source_refs": refs, "properties": raw}
)
d["material_layer"] = obj(
    {
        "material": ref("material"),
        "thickness_m": {"type": ["number", "null"], "minimum": 0},
        "function": string,
        "is_structural": boolean,
        "is_insulation": boolean,
        "is_ventilated": boolean,
        "source_refs": refs,
        "properties": raw,
    },
    ("material", "thickness_m"),
)
d["finish"] = obj(
    {
        "surface": {"enum": ["floor", "wall", "ceiling", "other"]},
        "code": string,
        "description": string,
        "material": ref("material"),
        "area_m2": number,
        "source_refs": refs,
    },
    ("surface",),
)
d["classification"] = obj({"system": string, "code": string, "name": string, "location": string})
common = d["entity"]["properties"]
pixel_fields = {
    "polygon_px": ref("polygon"),
    "polyline_px": ref("polygon"),
    "bbox_px": ref("bbox"),
    "holes_px": ref("polygons"),
}
d["observation"] = obj({"drawing_id": {"type": "string"}, **pixel_fields}, ("drawing_id",))
common.update(
    {
        "ifc_global_id": string,
        "ifc_class": string,
        "name": string,
        "description": string,
        "predefined_type": string,
        "type_id": string,
        "type_name": string,
        "type_mark": string,
        "parent_id": string,
        "source_refs": {**refs, "minItems": 1},
        "source_drawing_id": string,
        "room_ids": ids,
        "system_ids": ids,
        "schedule_ids": ids,
        "materials": arr(ref("material")),
        "material_layers": arr(ref("material_layer")),
        "quantities": arr(ref("quantity")),
        "geometry": {"anyOf": [ref("geometry"), {"type": "null"}]},
        "dimensions": ref("dimensions"),
        "classifications": arr(ref("classification")),
        "fire_rating": string,
        "acoustic_rating": string,
        "is_load_bearing": boolean,
        "observations": arr(ref("observation")),
    }
)
d["entity"]["required"].append("source_refs")
d["provenance"]["additionalProperties"] = False
d["level"]["properties"].update({"description": string, "storey_identifier": string})
d["level"]["properties"]["name"] = string
d["room"]["properties"].update({"finishes": arr(ref("finish")), "boundary_ids": ids})
d["room"]["properties"]["is_outdoor"] = boolean
d["room"]["properties"]["type"]["enum"] = sorted(
    set(d["room"]["properties"]["type"]["enum"])
    | {"dining", "closet", "utility", "technical", "garage", "staircase", "terrace", "shaft", "other"}
)
d["wall"]["properties"].update({k: number for k in ("gross_area_m2", "net_area_m2", "gross_volume_m3")})
d["wall"]["properties"].update({"wall_type": string, "structural_properties": raw})
d["opening"]["properties"].update(
    {
        "area_m2": number,
        "is_physical": boolean,
        "frame_material": {"anyOf": [ref("material"), {"type": "null"}]},
        "glazing": obj(
            {
                "description": string,
                "layers": {"type": ["integer", "null"], "minimum": 1},
                "u_value_w_m2k": number,
            }
        ),
        "operation": string,
        "hardware_group": string,
        "accessibility": obj(
            {"is_accessible": boolean, "clear_width_m": number, "threshold_height_m": number}
        ),
    }
)
d["opening"]["properties"]["kind"]["enum"] = ["door", "window", "doorway", "void", "other"]
d["object"]["properties"].update(
    {k: number for k in ("width_m", "depth_m", "height_m", "length_m", "area_m2", "volume_m3")}
)
d["object"]["properties"]["category"]["enum"] = sorted(
    set(d["object"]["properties"]["category"]["enum"])
    | set(
        (
            "kitchen_equipment stove fridge washing_machine furniture closet radiator elevator column beam "
            "slab roof foundation footing pile railing structural_member structural_connection "
            "pipe duct cable cable_tray "
            "electrical_equipment light_fixture distribution_board pump valve ventilation_equipment "
            "heating_equipment cooling_equipment fire_protection_equipment sensor"
        ).split()
    )
)


def entity(properties, required=()):
    return {
        "type": "object",
        "allOf": [ref("entity")],
        "unevaluatedProperties": False,
        "properties": properties,
        "required": list(required),
    }


d["system"] = entity(
    {
        "system_type": {
            "enum": (
                "heating cooling ventilation plumbing drainage electrical lighting "
                "fire_protection communication other unknown"
            ).split()
        },
        "component_ids": ids,
        "level_ids": ids,
    },
    ("system_type", "component_ids"),
)
d["type_definition"] = entity(
    {"entity_kind": {"enum": ["door", "window", "wall", "object", "room", "other"]}, "instance_ids": ids},
    ("entity_kind", "instance_ids"),
)
d["schedule_row"] = obj(
    {
        "id": {"type": "string"},
        "type_mark": string,
        "type_id": string,
        "dimensions": ref("dimensions"),
        "materials": arr(ref("material")),
        "quantity": {"type": ["number", "null"], "minimum": 0},
        "description": string,
        "notes": arr({"type": "string"}),
        "entity_ids": ids,
        "source_refs": refs,
        "cells": arr({"type": ["string", "number", "boolean", "null"]}),
    },
    ("id", "cells", "entity_ids", "source_refs"),
)
d["schedule"] = entity(
    {
        "schedule_type": {
            "enum": ["door", "window", "room_finish", "material", "equipment", "sanitary_fixture", "other"]
        },
        "source_document_id": {"type": "string"},
        "source_sheet_id": string,
        "headers": arr({"type": "string"}),
        "rows": arr(ref("schedule_row")),
    },
    ("schedule_type", "source_document_id", "headers", "rows"),
)
d["relationship"] = obj(
    {
        "id": {"type": "string"},
        "kind": {
            "enum": [
                "contains",
                "aggregates",
                "voids",
                "fills",
                "bounds",
                "connects",
                "typed_by",
                "assigned_to",
            ]
        },
        "from_id": {"type": "string"},
        "to_id": {"type": "string"},
        "source_refs": refs,
        "properties": raw,
    },
    ("id", "kind", "from_id", "to_id", "source_refs"),
)
d["address"] = obj({k: string for k in ("street", "postal_code", "city", "region", "country")})
d["project"] = obj(
    {
        **{
            k: string
            for k in (
                "name",
                "description",
                "building_name",
                "building_type",
                "function",
                "site_name",
                "client",
                "author",
                "architect",
                "engineering_company",
                "authoring_software",
                "ifc_schema",
                "project_identifier",
                "ifc_project_global_id",
                "ifc_building_global_id",
                "ifc_site_global_id",
            )
        },
        "address": {"anyOf": [ref("address"), {"type": "null"}]},
        "location": obj({"latitude_deg": number, "longitude_deg": number, "elevation_m": number}),
        "source_refs": refs,
        "properties": raw,
    }
)
d["source_document"] = obj(
    {
        "id": {"type": "string"},
        "file": {"type": "string"},
        "format": {"type": "string"},
        "source": string,
        "ifc_schema": string,
        "units": raw,
        "properties": raw,
    },
    ("id", "file", "format"),
)
d["drawing"]["properties"].update(
    {"id": {"type": "string"}, "source_document_id": {"type": "string"}, "level_ids": ids}
)
d["drawing"]["required"] += ["id", "source_document_id", "level_ids"]
d["coverage"]["properties"]["drawing_id"] = {"type": "string"}
d["coverage"]["required"].append("drawing_id")
s["$id"] = "https://stoa.local/building.v0.3.schema.json"
s["title"] = "Stoa building model v0.3"
s["description"] = (
    "One building; source frames and observations retain their original coordinates. Metric core is SI."
)
s["properties"].pop("drawing")
s["properties"].update(
    {
        "schema_version": {"const": "0.3"},
        "building_id": {"type": "string", "minLength": 1},
        "project": ref("project"),
        "source_documents": arr(ref("source_document")),
        "drawings": arr(ref("drawing")),
        "systems": arr(ref("system")),
        "types": arr(ref("type_definition")),
        "schedules": arr(ref("schedule")),
        "relationships": arr(ref("relationship")),
        "statistics": obj({"quantities": arr(ref("quantity"))}, ("quantities",)),
    }
)
s["required"] = [k for k in s["properties"] if k != "statistics"]
(HERE / "building.v0.3.schema.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
