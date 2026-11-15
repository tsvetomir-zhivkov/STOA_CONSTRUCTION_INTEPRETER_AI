# BIM workspace task: repository inspection and integration map

Inspection baseline: `main`, commit `e3e0141` (`JSON schema modification`). The working tree was clean before this inspection.

## Existing implementation

| Area requested | Located implementation | Integration boundary |
|---|---|---|
| Frontend/application shell | `scrollcraft/builds/stoa-plan-to-evidence/index.html`, `site.js`, `site.css` | Static promotional scroll experience. It does not import user buildings or implement a BIM workspace. |
| Backend/API | No application HTTP API found | `translator/__main__.py` and `blueprint-ingest/src/index.js` are CLI/programmatic extraction entry points, not upload endpoints. |
| IFC parsing | `translator/extract/ifc.py`, `ifc_metadata.py` | IfcOpenShell extraction, cached world geometry, spatial hierarchy, source metadata and normalized building facts. |
| JavaScript IFC parsing | `blueprint-ingest/src/extractors/ifcExtractor.js`, `ifc/building.js`, `ifc/stepParser.js` | Existing STEP reader, analytic geometry and optional web-ifc fallback; normalized entity IDs retain IFC GlobalIds. |
| DXF parsing | `translator/extract/swiss_dxf.py`; `blueprint-ingest/src/extractors/cadExtractor.js`, `cad/dxfParser.js` | Drawing entities, layers, handles, dimensions, text and extracted geometry. No interactive CAD viewport found. |
| SVG parsing | `blueprint-ingest/src/extractors/cadExtractor.js`, `cad/xml.js` | Vector extraction and normalization. No interactive SVG document viewer found. |
| PDF/raster routing | `translator/router.py`; `blueprint-ingest/src/classifier/blueprintClassifier.js`, `extractors/pdfExtractor.js` | PDF extraction/routing; external vision integration through `agents/visionAgentAdapter.js`. Python generic PDF/raster inference modules remain placeholders. |
| Normalized data | `translator/schema/building.v0.3.schema.json`; `translator/building.py`; `blueprint-ingest/src/storage/building.js` | Shared building collections, levels, types, systems, schedules, source references, identity mappings and migration. |
| Persistence | `translator/store.py`; `blueprint-ingest/src/storage/jsonStore.js` | Validated filesystem JSON and atomic writes. No existing browser document/session store found. |
| AI/query functionality | `answering_agent/agent.py`, `tools.py`, `main.py`; JavaScript Gemini mapper/client | Python CLI answering agent and deterministic building tools; semantic extraction in JavaScript. No existing chat UI/API found. |
| 3D renderer/navigation | No BIM scene renderer found | Installed/declarative IFC parsing dependencies are not evidence of a viewer. Scrollcraft's transforms and animation loop belong to the landing page. |
| Explode/component removal | No user-building implementation found | No current scene graph, explode controls, rendered-object selection, or component-removal implementation to preserve or diagnose. |
| File import/remove lifecycle | No browser workflow found | Gemini remote-file upload/delete and extraction-plugin staging are different workflows from the reported upload-button freeze. |
| Tests/examples | `tests/`, `blueprint-ingest/test/building.test.js`, `blueprint-ingest/out/` | Extraction, migration, validation, alignment and query tests; normalized sample documents. Existing IFC test helpers generate parser fixtures. |

## Dependency flow available for integration

```text
Source files
  -> Python translator / JavaScript blueprint-ingest
  -> format-specific extraction and semantic mapping
  -> v0.3 building normalization
  -> schema and reference validation
  -> filesystem JSON
  -> deterministic query tools / command-line answering agent
```

A viewer should consume this existing validated model and maintain separate render resources and session/UI state. The model already supplies level IDs, component categories, source drawing frames and IFC GlobalIds for mapping. It does not currently expose a running scene/session implementation whose cleanup behavior can be inspected.

## Searches and missing prerequisites

* Enumerated repository source files and package manifests, including hidden project files while excluding dependencies, caches and Git internals.
* Searched for scene renderers, orbit controls, frontend framework entry points, application servers, file inputs, drop handlers, scene disposal, model removal, explode/disassembly, request cancellation and object-URL lifetimes. Relevant browser matches belong to the Scrollcraft landing page/engine; upload matches belong to Gemini's file API.
* Inspected locally available branches and the `origin/MINE` and `origin/master` source trees. These also contain extraction/evaluation code rather than the described BIM application.
* Searched the repository and available attachment directories for `benchmarks.docx` and other `.docx` files. None was found. The supplied attachment contains only the master task text.
* Checked connected document sessions; none was available for inspecting the benchmark document.

Required to continue the requested integration: the path or branch containing the existing interactive application, and the actual `benchmarks.docx` file or its accessible local path.

## Lifecycle bug status

The reported import -> remove -> import freeze has **not** been reproduced or diagnosed. This checkout contains no corresponding UI, lifecycle state, scene initialization/disposal, or upload endpoint. Attributing it to stale state or resource cleanup without that source would be speculation.

No application functionality has been changed for this task, no replacement demo was created, and no implementation/test completion is claimed. This map records the completed inspection so work can continue at the real application's integration points once its location is provided.
