# Building model dependency map (inspection before implementation)

## Existing v0.2 architecture

* Python `translator/__init__.py` routes inputs, creates page envelopes, runs Swiss DXF or IfcOpenShell extraction, aligns coordinates, and validates each page/storey. `extract/ifc.py` caches geometry and properties and emits storey fragments. `align.py` needs a page coordinate frame. `store.py` validates and persists one fragment per file/part; `__main__.py` is its CLI.
* JavaScript `blueprint-ingest/src/index.js` classifies IFC, DXF, SVG and vector PDF. `extractors/ifcExtractor.js` uses the existing STEP parser and optional web-ifc geometry; CAD and PDF extractors return bundles. `documentBuilder.js` builds page envelopes; `geminiSchemaMapper.js` enriches, validates, merges and writes each envelope. `storage/metres.js` and `mappers/merge.js` operate in a single pixel frame.
* `translator/schema/drawing.schema.json` and `blueprint-ingest/schema/blueprint.schema.json` are the existing v0.2 contracts. Preserve both. Source-specific raw properties are permissive; the normalized core is strict.
* Python raster, vector PDF, fusion, coverage and Modal modules are placeholders. JavaScript raster/OCR/VLM is an external `visionAgentAdapter.js` plugin contract. No implemented CubiCasa inference, API endpoint, database model, or frontend building-data consumer was found. The landing page references vocabulary, not live document data.
* `answering_agent/main.py` loads JSON directly; `agent.py` explicitly requires v0.2; `tools.py` queries collections, measurements, connectivity, geometry and coverage. `eval/score.py` indexes drawing.file/read_from; `eval/probe.py` filters by levels and reads page/model identity.
* Tests cover Python routing, IFC, Swiss DXF, alignment and evaluation. IFC tests already contain an IfcOpenShell model builder for IFC4 and IFC2X3. No .ifc source file is checked into this checkout; real dataset tests require external STOA_DATA. JavaScript has sample v0.2 outputs in `blueprint-ingest/out/` but no checked-in test suite.

## Upgrade boundaries

1. Add a versioned Draft 2020-12 building schema; keep both old schemas readable.
2. Central compatibility modules aggregate page fragments into building collections, namespace drawing IDs, preserve original pixel frames, and expose explicit page views for existing alignment/evaluation code. Never guess that overlapping page coordinates identify the same physical entity.
3. Preserve IFC identifiers, typed properties, original quantities, type/material/system relationships at extraction time, then normalize into the shared model. Types and schedule rows are not physical instances.
4. Validate schema and reference integrity before atomic persistence. Read old files through migration. Publish one building document, with all levels and multiple source drawings.
5. Update orchestration, storage, agent prompts/tools, evaluation loading, plugin contracts, samples and tests together. Keep unavailable extraction routes explicitly unanalysed.

User authorization for this task explicitly supersedes CLAUDE.md's old v0.2 hand-off restriction.
