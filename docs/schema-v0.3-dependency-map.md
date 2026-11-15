# Schema v0.3: dependency map

This map lists every piece of code that writes, transforms or reads the drawing JSON. It was written before the v0.3 change (branch `schema-v0.3`, baseline commit `a329c69`) and says, for each one, what v0.3 changes. Line numbers refer to the baseline.

There are two schema files with the same v0.2 content:

| File | Used by |
|---|---|
| `blueprint-ingest/schema/blueprint.schema.json` | Node pipeline (`config.js` `SCHEMA_PATH`), `npm run validate` |
| `translator/schema/drawing.schema.json` | Python `translator/store.py` (`read_drawing` / `write_drawing`), `eval/score.py` through `read_drawing` |

The two had drifted: the Node copy has the extra object categories from translator 0.2.1/0.2.2 (stove, fridge, washing_machine, elevator, radiator, closet). The Python copy is the hand-off contract named in `CLAUDE.md` and is **not changed** by v0.3.

## 1. Producers

| Producer | Output | What it writes | v0.3 change |
|---|---|---|---|
| `blueprint-ingest/src/extractors/ifcExtractor.js` | Bundle, one per `IfcBuilding` | storeys as levels, `IfcSpace` rooms, walls, doors/windows, a fixed list of element classes as objects, raw psets and quantities in `properties` | Reads project/site/building metadata, the declared units (length, area, volume, angle), element types, materials and layer sets, systems and zones, space boundaries, coverings, classifications and `Tag` marks. Every quantity keeps its declared unit. All element classes are emitted; the old count-only classes (slab, roof, covering, footing, plate, member) are now objects, and elements without a plan footprint are kept with `polygon_px: null`. |
| `blueprint-ingest/src/extractors/cadExtractor.js` (+ `dxfWalls.js`, `stairs.js`, `fixtures.js`) | Bundle per DXF | rooms, walls (pairs of face lines), openings, fixtures, stairs, texts | Unchanged. It goes through the same v0.3 builder; DXF has no materials, types or systems, so those are empty and the coverage says `not_analysed`. |
| `pdfExtractor.js`, `pdfCommon.js`, the SVG route in `cadExtractor.js` | Bundle per page | rooms, walls, texts, scale | Unchanged; same as DXF. |
| `blueprint-ingest/src/mappers/documentBuilder.js` | v0.2 document | every v0.2 field; `recomputeMetres` derives the metre fields | Kept as is. v0.3 is built from its output plus the Bundle's `bim` data by `src/schema/v03.js`. |
| `translator/extract/ifc.py` | v0.2 per storey to `index/` | rooms with areas, element counts | Unchanged (hand-off contract). A v0.3 copy is made by `translator/to_v03.py` through the migration. |
| `translator/extract/swiss_dxf.py` | v0.2 per floor to `index/` | rooms, units, openings, fixtures | Same as `ifc.py`. |

## 2. Transformers (Node)

| Module | Fields it touches | v0.3 change |
|---|---|---|
| `mappers/corrections.js` | `type` / `kind` / `category`, `is_outdoor`, `provenance.extractor = 'human'`, `history` | None: it runs on the v0.2 shape before the v0.3 lift, so `ref:FILE#source_ref`, `block:` and `layer:` keys work unchanged. |
| `mappers/vocabulary.js` | `rooms[].type`, `objects[].category`, `properties.raw_type` / `raw_category`, flag `unmapped_vocabulary` | None (it runs before the lift). |
| `storage/metres.js` | `polygon_m`, `holes_m`, `area_m2`, `area_computed_m2`, `volume_m3`, `length_m`, `thickness_m`, `width_m`, stair `*_m` | It keeps writing the metre fields from pixels. It is rerun on merge, so v0.3 rebuilds the `quantities` entries for these fields (`v03.js` `syncQuantities`) after every recompute. It must not overwrite a value that came from an IFC quantity: v0.3 keeps the explicit IFC value in `quantities.<field>` and puts the code-derived one next to it. |
| `mappers/merge.js` | `COLLECTIONS` = levels, units, rooms, walls, openings, objects, texts, edges; identity by IoU / name / label; `superseded` | Extended to the new v0.3 collections (`element_types`, `materials`, `material_layer_sets`, `systems`, `schedules`, `relationships`, `statistics`); identity there is the IFC GlobalId or the name. The rule "superseded, never deleted" is kept. |
| `mappers/schemaMapper.js` | build, then corrections, vocabulary, validate, merge, write | It chooses the schema by `--schema` (default 0.3). Writes pass the Ajv check **and** the relationship check; a failure throws and nothing is written. |
| `storage/jsonStore.js` | Ajv 2020 validator, atomic write | `createValidator` loads both schema files and picks one by `schema_version`. |
| `src/index.js` | CLI, `--validate-only` | Gains `--schema 0.2|0.3`; `--validate-only` accepts either version and also runs the relationship check on v0.3. |

## 3. Consumers

### Viewer (`blueprint-viewer/`)

| Code | Fields read | v0.3 change |
|---|---|---|
| `docFrame` / `sceneDoc` | `drawing.transforms[0].matrix`, `drawing.scale.m_per_px`, `polygon_px`, `polygon_m`, `levels[].elevation_m`, `level_id` | None needed (same fields). Elements with `polygon_px: null` (new in v0.3) must be skipped when drawing. |
| level merge | `levels[].name`, `elevation_m`, `height_m` | None. |
| GlobalId link | `provenance.source_ref` | Prefers `global_id` when present, falls back to `source_ref`. |
| `describe`, `numericRows`, `provenanceRows`, `psetRows` | `label`, `name`, `number`, `type`, `kind`, `subtype`, `category`, `properties.*`, `provenance`, `flags`, `confidence` | Also shows `ifc_class`, the type (`type_id` resolved into `element_types[]`), the material layers, the systems, and each quantity's status (explicit / calculated / inferred / not available). |
| `psetUnits.js` | `properties.psets` numbers (`Length`, `Outer Diameter`) | None in the check itself. The inspector also shows the project's declared units; pset values stay **raw** unless the geometry check verifies them, because psets carry no unit even when the project declares one. |
| corrections UI | `properties.source_block`, `provenance.source_ref`, `layer` | None. |
| `scripts/verify.mjs` | sample files | Runs on the v0.3 samples; a v0.2 file still loads. |

### Python

| Code | Fields read | v0.3 change |
|---|---|---|
| `translator/store.py` `read_drawing` | validates against v0.2 | Validates against the schema of the document's own `schema_version` (v0.2 or v0.3). |
| `eval/score.py` `load_index` | `drawing.file`, whole document | None beyond `read_drawing`; it reads `index/`, which stays v0.2. |
| `eval/probe.py` | `rooms` (`type`, `name`, `number`, `label`, `area_m2`, `is_outdoor`, `unit_id`, `level_id`), `openings[].kind`, `objects` (`category`, `subtype`), `walls`, `levels[].name`, `units` | None: v0.3 keeps these fields with the same meaning. A v0.3 document can be passed in directly. |
| `answering_agent/agent.py` | rejects `schema_version != "0.2"` | Accepts 0.2 and 0.3. |
| `answering_agent/tools.py` | collections levels, units, rooms, walls, openings, objects, texts, edges | The new collections are added to `query_entities`. |

## 4. Tests and samples

| Item | Depends on | v0.3 change |
|---|---|---|
| `blueprint-ingest/test/e2e.test.js` | v0.2 output of the generated fixtures | Asserts v0.3 output (default) and keeps one v0.2 run (`--schema 0.2`). |
| `test/mapper.test.js` | create / merge / refuse | Runs on v0.3. |
| `test/classification.test.js` | extractor internals | None. |
| `test/helpers/fixtures.js` | generated IFC / DXF / SVG / PDF | The IFC fixture gains units in mm, a layer set, a door type, a system, quantities and a slab. |
| `blueprint-ingest/out/samples/*.json` | regenerated, not committed | Regenerated as v0.3. |
| `eval/fixtures/` | Python v0.2 examples | Unchanged. |

## 5. Not present

- No REST API, server, database or prompt template reads the JSON; persistence is JSON files only.
- No CI configuration, no lint or type-check script.
- No Python test dependencies are installed (no pytest, ifcopenshell, ezdxf, shapely or pymupdf). Only `jsonschema` and `numpy` are available, so the Python changes are checked with `py_compile` and a jsonschema script.
