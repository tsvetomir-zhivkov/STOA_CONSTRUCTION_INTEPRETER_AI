# Stoa building model v0.3

The public extraction and persistence contract is **one JSON document per building**, including all levels. Page/storey fragments remain an internal adapter for the established drawing alignment code. They are no longer public persisted outputs.

## A. Schema and compatibility

The canonical Draft 2020-12 schema is `translator/schema/building.v0.3.schema.json`. Both original schemas remain unchanged: `translator/schema/drawing.schema.json` and `blueprint-ingest/schema/blueprint.schema.json`. `translator/schema/build_schema.py` reproducibly extends the frozen vocabulary, including its strict `allOf`/`unevaluatedProperties` entity definitions.

Required top-level fields are `schema_version`, `building_id`, `project`, `source_documents`, `drawings`, `levels`, `units`, `rooms`, `walls`, `openings`, `objects`, `texts`, `edges`, `types`, `systems`, `schedules`, `relationships`, `coverage`, and `history`. `statistics` is optional. There is no ambiguous top-level `drawing`.

Reusable definitions cover source references, observations, geometry, dimensions, quantities, materials, ordered material layers, finishes, classifications, schedule rows, relationships and addresses. Core fields are typed; only source-specific properties/details are extensible. Unknown measurements remain null. Metric lengths, areas and volumes use SI; original IFC values and units remain available.

Entities retain the familiar measurement fields for query compatibility, with `quantities[]` recording source/calculated/estimated/unknown origin and method. IFC entities use `ifc:<GlobalId>` identities. Drawing-derived identities are namespaced by their original drawing frame. Each entity has `source_refs`, and pixel coordinates identify their `source_drawing_id`. Multiple observations of one IFC entity retain separate pixel observations. No spatial deduplication occurs between unrelated sheets.

Physical doors/windows, unfilled voids/doorways, type definitions and schedule rows occupy distinct representations. A schedule quantity is not an instance count. Unknown source classes remain objects with their original IFC class and structured properties. Source relationships that cannot be resolved within a building remain in the source's raw relationship data; normalized relationships only use valid IDs.

`translator.building` and `src/storage/building.js` own version compatibility. Readers accept validated v0.2 data and migrate it; writers publish validated v0.3 data. `page_views()` exposes explicitly non-persistable views for existing evaluation/display code. `translate(path, part=...)` remains a deprecated view adapter; use `translate(path)` for a whole building or `translate_all(path)` for multi-building IFC files.

To migrate explicitly associated old files without replacing the originals:

```powershell
python -m translator.migrate page1.json page2.json --out building.json
```

The caller must establish that separate input files belong to the same building. Migration cannot recover information that an older extractor discarded. Conflicting non-null facts fail with an entity/field-specific error; they are not silently overwritten. The established same-frame v0.2 supersession/IoU merger remains available through the central compatibility dispatcher. Building merges do not apply pixel IoU across sheets or automatically reconcile conflicting revisions.

`blueprint-ingest/out/building.v0.3.json` demonstrates migration of the four existing storey examples. The original `plan*.json` files remain v0.2 compatibility fixtures.

## B. File map

| File | Responsibility and change |
|---|---|
| `translator/schema/building.v0.3.schema.json` | New strict building contract shared by Python and JavaScript. |
| `translator/schema/build_schema.py` | Reproducible schema extension without overwriting v0.2. |
| `translator/building.py` | Central migration, source-scoped identities, aggregation, page views and foreign-key checks. |
| `translator/migrate.py` | Validated, atomic CLI migration of explicitly associated files. |
| `translator/statistics.py` | Optional counts/sums with method, quality and contributing entities; incomplete sums are omitted. |
| `translator/config.py` | Current schema path plus retained legacy schema path. |
| `translator/store.py` | Version-aware validation, reference integrity, migrated reads and atomic building writes. |
| `translator/__init__.py` | Public building orchestration, all-storey aggregation, multi-building separation and source-frame compatibility. |
| `translator/extract/__init__.py` | Documents the new public normalization boundary. |
| `translator/extract/ifc.py` | Retains IfcOpenShell geometry/cache and fragment extraction; preserves full properties, openings and previously omitted components; checks units and watertightness. |
| `translator/extract/ifc_metadata.py` | Typed IFC metadata, explicit quantity units, full property/type sets, materials, systems, spatial/connectivity relationships and IFC tables. |
| `blueprint-ingest/src/config.js` | Uses the same canonical v0.3 schema. |
| `blueprint-ingest/src/storage/building.js` | JavaScript compatibility/identity/merge/reference boundary, matching Python migration conventions. |
| `blueprint-ingest/src/storage/jsonStore.js` | Ajv plus reference validation; validates before atomic writes. |
| `blueprint-ingest/src/extractors/ifc/building.js` | Building normalization from the existing STEP model, preserving entities without geometry and separating types, systems and schedules. |
| `blueprint-ingest/src/extractors/ifcExtractor.js` | Adds complete building extraction through the existing parser/geometry engine; improves transformed extrusion footprints, holes and approximation labeling. |
| `blueprint-ingest/schema/export_ifc_metadata.py` | Reproducibly exports IFC inheritance/attribute metadata from IfcOpenShell for the JS reader. |
| `blueprint-ingest/schema/ifc-entities.json` | Generated IFC2X3, IFC4 and IFC4X3_ADD2 class metadata; avoids a hardcoded list that drops unknown physical classes. |
| `blueprint-ingest/src/extractors/schedules.js` | Conservative aligned printed-table recognition and typed schedule normalization, without creating instances. |
| `blueprint-ingest/src/extractors/pdfExtractor.js` | Extends optional semantic PDF requests to preserve printed schedule headers/rows. |
| `blueprint-ingest/src/mappers/documentBuilder.js` | Keeps the established internal page builder and identifies the updated translator version. |
| `blueprint-ingest/src/mappers/geminiSchemaMapper.js` | Aggregates all CAD/PDF bundles before validation and persistence; retains bounded schema repair. |
| `blueprint-ingest/src/mappers/merge.js` | Dispatches through the central compatibility boundary while retaining the established same-frame merger. |
| `blueprint-ingest/src/agents/visionAgentAdapter.js` | v0.3 plugin contract, legacy migration, staging and validation before publishing external output. |
| `blueprint-ingest/src/index.js` | One output per IFC building; all other vector routes use aggregated mapping; validate-only supports migration. |
| `blueprint-ingest/out/building.v0.3.json` | Validated multi-level example migrated from existing repository data. |
| `answering_agent/main.py` | Takes a building path on the CLI and uses the validated/migrating reader. |
| `answering_agent/agent.py` | v0.3 boundary validation and instructions for levels, types, schedules, provenance and coordinate frames. |
| `answering_agent/tools.py` | Project/source/statistics overview, new collections/filters, source-scoped absence checks and protection against cross-level or unregistered source-frame geometry queries. |
| `eval/probe.py` | Recognizes normalized structural categories and deduplicates repeated IFC identities. |
| `eval/score.py` | Reads buildings through explicit source-frame evaluation views. |
| `tests/test_building.py` | Cross-runtime schema, migration, IFC, SI units, preservation, schedules, statistics, multi-building and actual CAD/PDF end-to-end checks. |
| `blueprint-ingest/test/building.test.js` | JavaScript schema/reference, SVG, multi-page, schedule and staged vision-contract tests. |
| `tests/test_ifc.py` | Public contract now asserts one building while retaining internal fragment/geometry regression tests. |
| `tests/test_align.py` | Reads the source frame from `drawings[]`; retains coordinate assertions. |
| `tests/test_align_ifc.py` | Selects explicit rendered-sheet views for dataset alignment tests. |
| `tests/test_router.py` | Checks routing metadata in the building's drawing frame. |
| `tests/test_swiss_dxf.py` | Checks v0.3 frame metadata while retaining extraction measurements. |
| `pyproject.toml` | Version 0.3 and declarations for actual extraction/evaluation/test dependencies. |
| `blueprint-ingest/package.json`, `blueprint-ingest/package-lock.json` | Consistent package version 0.3; dependency versions remain lockfile-pinned. |
| `.gitignore` | Excludes the local test virtual environment. |
| `CLAUDE.md` | Updates the repository hand-off contract authorized by this task. |
| `docs/schema-v0.3-dependencies.md` | Dependency map recorded before implementation. |
| `docs/vision_translator_plan.md` | Marks the original page-based plan as historical. |
| `blueprint-ingest/README.md`, `docs/building-v0.3.md` | Current usage, contract, verification evidence and limitations. |

## C. IFC flow

Python opens the source once with IfcOpenShell, resolves project units, and extracts world-coordinate meshes once through its existing iterator. Geometry is cached. Source quantities take precedence; explicitly overridden quantity units are converted separately. Volumes from non-watertight meshes remain unavailable. Raw instance/type properties are preserved without the old size/truncation budgets.

Storey fragments are aligned where a rendered source sheet is available, then grouped by actual IfcBuilding ancestry. All storeys enter shared collections. A multi-building model creates distinct building documents; unresolved membership is retained separately rather than assigned to an arbitrary building. GUIDs resolve type, containment, void/fill, room-boundary, component connectivity and system membership. IFC tables become schedules. Schema validation and foreign-key checks precede atomic output.

JavaScript continues to use the existing streaming STEP parser and optional web-ifc fallback. Generated IFC inheritance metadata identifies physical classes beyond the old allowlist. The new normalization path retains entities without footprints, applies declared length/area/volume units (including explicit quantity overrides), and emits buildings directly. It no longer guesses storeys from elevation or fabricates thin door rectangles in the public building path. Extrusion holes and mapped/world transformations are preserved for supported analytic shapes; approximate footprints are identified as estimates.

## D. Other routes

The working Python Swiss DXF extractor and alignment algorithm retain their internal contract; public output passes through central building normalization. JavaScript DXF, SVG and vector PDF parsers retain their format logic, with all page bundles assembled into one building document. Drawing frames, scales, transforms and page-local coordinates remain distinct.

Schedule extraction requires a recognized printed title and aligned headers/rows, or a structured optional VLM response. The external vision adapter publishes only validated building output. Existing v0.2 plugin output is migrated. The repository does **not** contain a working Python CubiCasa/OCR/raster inference pipeline; its placeholder routes stay explicitly unanalysed. No API/database/frontend data model existed to refactor.

## E. Verification

Final local results: **83 Python tests passed, 25 skipped; 7 JavaScript tests passed; zero failures**. The skipped checks require the external dataset. Python compilation, Ruff checks for the translator/new tests/evaluation modules, JavaScript syntax checks, and `git diff --check` passed. The migrated example passed both Python validation and the JavaScript validate-only CLI.

Manual inspection of that example confirmed four drawing frames/levels (T/FDN, Level 1, Level 2, Roof), 17 rooms, 57 walls, 38 openings and 67 objects in one document, with two retained source-document records.

Run from the repository root:

```powershell
.\.venv\Scripts\python -m pytest -q --disable-warnings
cd blueprint-ingest
npm.cmd test
node src/index.js --validate-only out/building.v0.3.json
```

The integration suite builds IFC files with the repository's existing IfcOpenShell test-model helper. No IFC source file is checked into this checkout. Both parsers are exercised against IFC4 and IFC2X3 with metre and millimetre length units, plus explicit area-unit overrides. The richer model verifies two floors, project identity, positioned room geometry, structural quantities, ordered materials, door/type/void/host relationships, heating-system components and connectivity, IFC schedule rows, unknown values, and preserved long property text. Both full pipelines save JSON that passes the same Python schema/reference validator.

Actual DXF, SVG and two-page vector PDF routes are exercised without external AI calls. The vision plugin test uses a local fake module to verify hand-off, migration and invalid-output isolation. It does not claim to test OCR or image interpretation. Schema tests reject unknown core fields, invalid material thickness, dangling references and duplicate IDs, and accept nullable values and multiple source documents. Statistics tests check that missing measurements suppress totals.

## F. Limits and remaining external verification

* Real-dataset accuracy/alignment checks require the external `STOA_DATA` dataset; those tests skip when it is absent.
* Live Gemini/VLM calls and a real external vision agent are untested. No new OCR or CubiCasa inference implementation was invented.
* JS analytic geometry does not implement a general solid modeller. Complex boolean/profile/mesh footprints may be approximate or unavailable; exact mesh-volume calculation remains in the Python IfcOpenShell path. IFC4X3 metadata is available but the integration fixtures exercise IFC4 and IFC2X3.
* Printed-table recognition is deliberately narrow. Irregular tables may require the optional vision route; schedule-to-instance links remain empty without reliable source evidence.
* Migration preserves what v0.2 retained; recovering old discarded IFC properties requires re-extraction. Automatic revision reconciliation, global CAD sheet registration and deduplication across independent drawings are not inferred from overlapping coordinates or similar labels.
* Optional statistics describe the indexed/analysed source scope. They are not certified survey measurements or proof that an incomplete input model describes every physical feature.
