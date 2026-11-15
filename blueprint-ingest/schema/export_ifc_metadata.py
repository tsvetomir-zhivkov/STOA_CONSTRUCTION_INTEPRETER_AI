"""Export inheritance/attribute metadata for the existing JS STEP reader (no runtime Python)."""

import json
from pathlib import Path

import ifcopenshell.ifcopenshell_wrapper as wrapper

out = {}
for name in ("IFC2X3", "IFC4", "IFC4X3_ADD2"):
    schema = wrapper.schema_by_name(name)
    entities = {}
    for declaration in schema.declarations():
        if not isinstance(declaration, wrapper.entity):
            continue
        entities[declaration.name().upper()] = {
            "name": declaration.name(),
            "parent": declaration.supertype().name().upper() if declaration.supertype() else None,
            "attributes": [a.name() for a in declaration.all_attributes()],
        }
    out[name] = entities
Path(__file__).with_name("ifc-entities.json").write_text(
    json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8"
)
