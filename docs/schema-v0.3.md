# Building schema v0.3

v0.3 turns the drawing index into a building model. One document describes one IFC building, with all its storeys, or one drawing page (DXF, SVG, PDF, raster). It is a **superset of v0.2**: every v0.2 field keeps its name and meaning, so code written for v0.2 (for example `eval/probe.py`) reads v0.3 unchanged.

| | |
|---|---|
| Schema file | `blueprint-ingest/schema/building.schema.v0.3.json` (JSON Schema 2020-12, strict) |
| Python copy | `translator/schema/building.schema.v0.3.json`, written by `node blueprint-ingest/scripts/sync-schema.mjs` (the tests fail if it is stale) |
| v0.2 | `blueprint.schema.json` and `translator/schema/drawing.schema.json` are unchanged |
| Hand-off | `index/` stays v0.2 (see `CLAUDE.md`). v0.3 copies go to `index_v03/` |
| Node layer | `blueprint-ingest/src/schema/` (`v03.js`, `versions.js`, `lift.js`) |
| Python layer | `translator/schema_compat.py` (gives the same output as the Node migration; checked on the samples) |

## Three rules

1. **Null means unknown.** A value the source does not state is `null` (or an empty list), never a guess. Coverage says whether a category was looked at.
2. **Every measured value says where it came from.** Measured fields carry a record in `quantities`:

   ```json
   "area_m2": 25.25,
   "quantities": {
     "area_m2": {"value": 25.25, "unit": "m2", "status": "explicit", "method": "IfcQuantityArea",
                 "source": "Qto_SpaceBaseQuantities.NetFloorArea (SQUARE_METRE)", "raw": {"value": 25.25, "unit": "SQUARE_METRE"}}
   }
   ```

   | `status` | Meaning | Example |
   |---|---|---|
   | `explicit` | Read from the source | IFC quantity, `IfcDoor.OverallWidth`, a written area |
   | `calculated` | Derived by code from explicit data | polygon area × scale², sum of layer thicknesses, extrusion depth |
   | `inferred` | From a rule or convention | a unit the file does not declare |
   | `not_available` | The source does not say | the value is `null` |

   An explicit value always wins: code never overwrites it with a geometry-derived one (`storage/metres.js`). The relationship validator checks that each record agrees with its field.
3. **SI units.** Lengths are in m, areas m², volumes m³, mass kg, angles in degrees. `project.units` records what the file declared and the conversion factor applied (`to_si`).

## Top level

| Key | Content |
|---|---|
| `schema_version` | `"0.3"` |
| `project` | `name`, `global_id`, `schema_identifier` (IFC2X3, IFC4…), `application` (name, version, developer, originating system), `created_at`, `units` (length, area, volume, angle, optional mass: `{unit, to_si, status, source}`) |
| `site` / `building` | IfcSite (latitude and longitude in decimal degrees, elevation, address) and IfcBuilding; `null` for drawings |
| `drawing` | As in v0.2 (page frame, route, transforms, scale) |
| `sources[]` | Files read: `file`, `role`, `format`, `size_bytes`, `sha256`, `georeference` (IfcMapConversion) |
| `levels[]`, `units[]` | As in v0.2. `units` are apartments; units of measure are in `project.units` |
| `rooms[]`, `walls[]`, `openings[]`, `objects[]`, `texts[]`, `edges[]` | v0.2 entities plus the fields listed below |
| `element_types[]` | Reusable types (IfcDoorType, IfcWallType, IfcDoorStyle…): `kind`, `predefined_type`, `operation`, `tag`, `material_ids`, `material_layer_set_id`, `instance_ids`, type psets in `properties.psets` |
| `materials[]` | `name`, `category`, `description` |
| `material_layer_sets[]` | `kind` (`layer_set`, `constituent_set`, `profile_set`, `list`); `layers[]` (`material_id`, `thickness_m`, `name`, `category`, `is_ventilated`, `priority`); `total_thickness_m` (calculated: sum of layers) |
| `systems[]` | IfcSystem, IfcDistributionSystem, IfcZone, IfcGroup…: `kind`, `predefined_type`, `member_ids`, `unresolved_member_count` |
| `schedules[]` | Door and window schedules derived from the instances (see below) |
| `relationships[]` | `aggregates` (whole → part), `nests`, `connects` (wall path connections), `covers` (covering → room or element), `serves` (system → building), `other` |
| `coverage[]`, `history[]` | As in v0.2, plus the categories `ifc_classes`, `units`, `materials`, `element_types`, `systems`, `schedules`, `relationships`, `finishes` |
| `statistics[]` | Optional counts and sums (see below) |

## Fields added to entities

| On | Field | Meaning |
|---|---|---|
| every entity | `global_id` | Raw IFC GlobalId (also still in `provenance.source_ref`) |
| | `ifc_class` | Source class, upper case (`IFCWALLSTANDARDCASE`) |
| | `quantities` | Records for the measured fields, plus every IFC quantity as `<QtoSet>.<Name>`, converted to SI with its raw value and unit |
| | `classifications[]` | `{system, code, name}` from IfcRelAssociatesClassification |
| rooms | `finishes.floor` / `wall` / `ceiling` | `{value, status, source, covering_ids, material_ids}` from IfcRelCoversSpaces coverings, else the FloorCovering, WallCovering and CeilingCovering psets. `not_available` when the file has neither |
| | `boundary_element_ids` | Elements bounding the space (IfcRelSpaceBoundary) |
| | `type_id` | IfcSpaceType |
| walls | `type_id`, `material_layer_set_id`, `material_ids` | |
| openings | `type_id`, `mark` (IfcElement.Tag as written), `operation` (OperationType; for IFC4 windows, PartitioningType), `material_ids` | Instance data. The type is in `element_types`, the schedule row in `schedules` |
| objects | `discipline` | `architectural`, `structural`, `mep`, `furnishing` or `other` (from `ifc_class`, else from `category`) |
| | `type_id`, `system_ids`, `material_ids`, `material_layer_set_id`, `host_id` | |

`objects[].category` keeps the v0.2 fixture enum (toilet, sink, …, other), because questions count those. What any other object is, is given by `ifc_class` and `subtype` (for example `IFCPIPESEGMENT`, `pipe_segment`).

## Instance, type and schedule entry

| Concept | Where | Example |
|---|---|---|
| Instance | `openings[]` | One per physical door: position, `width_m` (explicit, from `OverallWidth`), `mark`, `type_id` |
| Type | `element_types[]` | "Exterior Sliding Door 22" (IFCDOORSTYLE), operation `SLIDING_TO_RIGHT`, `instance_ids` |
| Schedule entry | `schedules[].entries[]` | `{mark, type_id, name, instance_ids, count, width_m, height_m}` |

A schedule groups instances by `type_id`, or by `mark` when there are no types. It is derived, so its status is `calculated`. Width and height come from the type, else from a value every instance shares, else `null`. IFC schedule objects are not read.

## Statistics

```json
{"id": "stat_17", "name": "sum.rooms.area_m2", "value": 1939.384, "unit": "m2", "status": "calculated",
 "method": "sum of rooms[].area_m2 (all inputs explicit)", "source": "rooms[].area_m2", "exact": true,
 "scope": {"collection": "rooms"}, "inputs": 82, "missing": 0}
```

(ac20_arc.ifc: 82 IfcSpace NetFloorArea quantities.)

- A **count** is `exact` when its coverage category was `analysed` and not capped.
- A **sum** is `exact` when every input is explicit and none is missing.

Names include `count.rooms`, `count.openings.door`, `count.objects.<category>`, `count.ifc_class.<CLASS>`, `sum.rooms.area_m2` (total, and per level when there are several), and `sum.walls.length_m`.

## Ids and references

- Ids are unique across the whole document and contain no whitespace.
- Every reference must resolve. The validator checks `level_id`, `room_id`, `room_ids`, `wall_id`, `type_id`, `system_ids`, `material_ids`, `material_layer_set_id`, `host_id`, `text_ids`, `boundary_element_ids`, `instance_ids`, `member_ids`, `superseded_by`, finish `covering_ids` and `material_ids`, layer `material_id`, schedule entries, relationship ends and statistic scopes.
- It runs on every write, in `blueprint-ingest/src/schema/v03.js` `validateRelationships` and in `translator/schema_compat.py` `validate_relationships`.
- A document that fails either the schema or this check is never written.

## Migration from v0.2

`migrateV02toV03` (Node) and `migrate_v02_to_v03` (Python) map fields only:

| v0.2 | v0.3 |
|---|---|
| everything | kept, same names |
| `properties.ifc_type` | `ifc_class` |
| `provenance.source_ref` of an IFC element (22-character GUID) | `global_id` (source_ref kept) |
| `levels[].properties.long_name` | `levels[].long_name` |
| object `category` (+ `ifc_class`) | `discipline` (table in `ifcClasses.js`, shared with Python through `translator/schema/ifc_disciplines.json`) |
| metre fields | `quantities` records: a written area is explicit; code-derived fields are calculated with their method; anything else is "migrated from v0.2 (origin not recorded)" |
| (absent) | `project.units` `not_available`; `materials`, `element_types`, `systems`, `schedules`, `relationships` empty, with coverage `not_analysed` |

Reading v0.3 from Python:

```python
from translator.schema_compat import load_document      # v0.2 or v0.3 file -> validated v0.3 dict
doc = load_document("index/swiss_dwellings/site1000_bldg2082_floor9385.json")
```

Writing v0.3 copies of the Python index: `python -m translator.to_v03` (index/ → index_v03/).

## Producing v0.3

```bash
cd blueprint-ingest
node src/index.js ../data/ifc_models/bsc-esplanades_1807-ep-ar-v18.ifc --out out/esplanades.json            # v0.3 (default)
node src/index.js ../data/ifc_models/bsc-esplanades_1807-ep-ar-v18.ifc --schema 0.2 --out out/esplanades.v02.json
node src/index.js --validate-only out/esplanades.json     # either version; v0.3 also checks references
```

`--schema 0.2` reproduces the previous output exactly, apart from timestamps and `translator_version` (checked on four samples). That works because v0.2 and v0.3 share one pixel frame, which also lets a v0.3 run merge into an existing v0.2 file (the file is migrated in memory first).

## What the IFC route reads, and what it does not

| Read | Not read (reported in coverage) |
|---|---|
| IfcProject, OwnerHistory → IfcApplication, the FILE_NAME header | IFC schedule and cost objects |
| IfcUnitAssignment, including prefixes and conversion-based units (ft, in, degree) | Pset values' own units: psets carry none, so they stay raw, and the viewer checks them against geometry |
| IfcSite, IfcBuilding, IfcMapConversion | Space-boundary geometry (only which element bounds which space) |
| Every element class in `src/schema/ifcClasses.js` (architectural, structural, MEP, furnishing) | IfcOpeningElement, IfcDistributionPort, IfcVirtualElement, IfcAnnotation and IfcGrid: counted, not emitted |
| IfcElementQuantity, with each quantity's own unit when it has one | Any other product class: named and counted in coverage `ifc_classes` (status `partial`) |
| IfcRelDefinesByType (types, type psets) | |
| IfcRelAssociatesMaterial: material, layer set (usage), list, constituent and profile sets; an element without its own association takes its type's | |
| IfcRelAssignsToGroup (systems, circuits, zones, groups), IfcRelServicesBuildings | |
| IfcRelSpaceBoundary, IfcRelCoversSpaces / BldgElements, IfcRelAssociatesClassification, IfcRelAggregates / Nests (between elements), IfcRelConnectsPathElements | |
