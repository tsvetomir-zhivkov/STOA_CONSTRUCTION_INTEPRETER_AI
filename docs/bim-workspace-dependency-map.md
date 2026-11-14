# BIM workspace dependency map

Baseline: `5fd079a`, inspected 2026-11-15. Implementation authorized after read-only exploration.

## Existing application

`blueprint-viewer/index.html` is the plain ES-module application and UI controller.
`blueprintViewer.js` contains `BlueprintScene` (source/level/picking ownership) and
`BlueprintViewer` (Three.js renderer, OrbitControls, selection, corrections).
`ifcGeometry.js` uses web-ifc 0.0.68, real indexed triangles, per-element ranges and
storey/category/material batches. Three.js 0.160.0 and three-mesh-bvh 0.7.8 are pinned.
Preserve explode, level separation, clipping, group visibility, category isolation,
hover/pick cycling, camera navigation, correction export, coverage and units inspection.
Removal currently removes an entire source; individual component removal is new work.
There is no dedicated 2D viewer, HTTP API, persistent library or browser AI interface.

## Pipeline and contracts

Python `translator.translate`/`translate_all`, `translator/extract/ifc.py`, specialized
Swiss DXF extraction and Node `blueprint-ingest/src/index.js` are the integration points.
Node also handles SVG and vector PDF. Raster interpretation depends on a configured
external vision module; never manufacture findings when it is unavailable.

Canonical contract: `translator/schema/building.v0.3.schema.json`, selected by both
runtime configs and CLAUDE.md. It has source_documents/drawings/types, geometry and
quantity arrays. A second incompatible 0.3 contract in `building.schema.v0.3.json`
uses drawing/sources/element_types and quantity objects. Explicit input adapters are
required; schema_version alone cannot identify the shape. Keep one validated semantic
model, with source files and geometry artifacts separate. Preserve original IDs and
per-source transforms; never match storeys only by their names.

`translator/store.py` and Node `storage/jsonStore.js` provide validated atomic writes.
Current Python read/write paths disagree about the 0.3 schema. Node IFC orchestration
references undefined createValidator/persistBuilding/usage; mapper validation mixes
the two schemas. AI `answering_agent/agent.py` and `tools.py` contain committed conflict
markers. Restore these boundaries before adding application consumers.

## Lifecycle reproduction

Original HTML/CSS/controller run in Chrome with only renderer/parser mocked:
first import: source=1, viewing=true; remove last file or Clear: source=0,
viewing=false; second successful import: source=1, viewing=false, stage display:none.
`ensureViewer` returns the retained instance before restoring body.viewing. Input
reset already exists. This is controller evidence, not full IFC/WebGL verification.
Source removal already disposes geometry/material/BVH and closes IFC models.
Missing: cancellation/generation guards, serialized imports, failed-model cleanup,
complete renderer teardown, and resetting stale isolation state.

## References and verification

Viewed all nine embedded images in
`C:/Users/itson/Downloads/Telegram Desktop/benchmarks.docx`: adjustable parallel view;
large neutral/dark model viewport; hierarchy/visibility and readable properties;
source-linked AI findings; real-document preview gallery.

Baseline commands/results:
- Python pytest: 63 passed, 7 failed, 13 errors, 25 skipped.
- Node ingestion tests: 25 passed, 13 failed, 1 skipped.
- Ruff: 44 issues.
- Viewer JavaScript syntax checks: passed.

Original data/ and STOA_DATA are absent. Legacy normalized samples exist in
blueprint-ingest/out/samples-v0.2, along with a canonical building.v0.3.json sample.
Existing tests generate small source fixtures; use those where originals are absent.
The viewer verification script needs external clinic/Esplanades/Swiss source files.
No viewer build/lint/typecheck scripts exist initially; start uses npx serve.

## Approved stages (one commit per stage)

- [x] 1. Inspection, dependency map, reference review, defect evidence.
- [x] 2. Lifecycle stabilization and regression tests.
- [x] 3. Canonical data integration, API, storage and existing pipeline repairs.
- [x] 4. IFC floor/category visibility.
- [x] 5. Cached per-storey 2D plans.
- [x] 6. Synchronized parallel workspace.
- [x] 7. Dedicated DXF/SVG environments.
- [x] 8. Component inspection and removal.
- [ ] 9. Reference-based UI, AI analysis and document library.
- [ ] 10. Integration tests, performance/lifecycle verification and architecture docs.

Stage 2: generation-owned serialized imports, visibility restoration, teardown, stale-state reset and parser failure cleanup; session tests pass. Full cross-format browser matrix remains a stage 10 gate.

Stage 3: canonical boundary, legacy/alternate adapter with source archive, repaired AI syntax and persistence, loopback document/extraction API. Canonical sample upload/validate/delete verified through HTTP. Runtime dependencies added: none.

Stage 5: worker-based IFC parsing/BVH generation, cached storey section/projection plans, native IFC Plan/FootPrint polylines and supported circle curves. Unsupported native curves retain mesh-section fallback. Section tests verify cut height and storey elevation.

Stage 6: real Chrome verification imported the existing two-storey IFC fixture through the API and worker, rendered 2 source records, produced per-storey plans, and selected a plan element in 3D. Canonical sample shows all four floors in parallel mode.

Stage 7: DXF uses installed ezdxf SVG rendering with original handles, layers, transforms and original-file retention; SVG keeps sanitized native vectors/groups. Chrome verified DXF import and layer extraction; uncertain classifications remain explicitly unclassified.

Stage 8: canonical category-aware inspector, SI measurements, null/zero distinction, material layers and provenance; identifiers collapsed; non-destructive component removal and restoration. Formatter regression tests pass.
