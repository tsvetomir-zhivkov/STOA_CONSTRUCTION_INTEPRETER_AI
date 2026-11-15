# Stoa vision translator (Junction 2026)

Turns any drawing file into schema v0.3 JSON (one per drawing page; an IFC model: one per IFC building,
with all its storeys), so an answering agent can
answer questions about it. Plan: @docs/vision_translator_plan.md
Architecture: @docs/Stoa_proposal.md

## Goal and scoring
- Counts must be exact. Areas, lengths and volumes must be within 2%.
- Dev set: questions.jsonl (7,990 questions, 320 files). Measure every
  change with python eval/score.py.
- Hold out 20% of FILES (not questions). Never tune on them.

## Priorities (do not reorder)
1. eval harness  2. router  3. Swiss DXF/PDF  4. IFC  5. CubiCasa raster on Modal
6. VLM checker  7. everything else.
Never cut: eval, router, Swiss, IFC areas.

## Repo layout
- translator/router.py: pick the best source file (sibling IFC > sibling DXF > SVG > vector PDF > raster)
- translator/extract/: swiss_dxf.py, ifc.py, vector_pdf.py, raster/
- translator/fuse.py, scale.py, coverage.py, glossary.py
- translator/modal_app.py: ALL GPU code lives here and nowhere else
- translator/schema/building.schema.v0.3.json: current contract (synced copy of
  blueprint-ingest/schema/building.schema.v0.3.json); drawing.schema.json is frozen v0.2;
  building.v0.3.schema.json is the building-model schema (translator.building), not the contract
- eval/probe.py, eval/score.py, eval/failures.jsonl
- index/<source>/<file>.json: outputs, one JSON per drawing page (IFC model: per IFC building)
- index/_raw/: raw model outputs, so the GPU is never rerun while tuning

## Hand-off contract (do not change without asking)
- Output: index/<source>/<file>.json
- API: translate(path) -> dict and python -m translator <path>
- Every write is validated with jsonschema. Fail loudly, never write invalid JSON.

## Schema rules
- Schema v0.3 (translator/schema/building.schema.v0.3.json = blueprint-ingest/schema/building.schema.v0.3.json)
  is the contract (k, 2026-11-15). Extractors build v0.2 documents; translator.schema_compat.migrate_v02_to_v03
  converts them and store.write_drawing validates both. Keep frozen v0.2 documents readable. See docs/schema-v0.3.md.
- Every entity has source/provenance.extractor, confidence, and
  provenance.source_ref (DXF handle, PDF item index or IFC GlobalId).
- Rooms: number, unit_id, parent_room_id, subtype, is_outdoor, polygon in pixels and metres.
- Top level: units[], levels[], coverage[], drawings[].route, drawings[].read_from, drawings[].transforms.
- Fixture categories: toilet, sink, shower, bathtub, sauna_bench, stair.
- Never delete a pipeline element when a better one is found. Mark it superseded.
- Written areas only CROSS-CHECK. Flag check_this if >10% off (>2% for vector sources).

## Coordinate rules (the most common source of bugs)
- Highlight coordinates are pixels of the ORIGINAL page image at a fixed dpi.
- Never crop, rotate or resize in place. If a model needs a resized input, record
  scale_x, scale_y (and any deskew angle) in drawing.transforms, and map
  outputs back to original pixels BEFORE writing JSON.
- Check alignment visually: draw polygons and text boxes over the plan image.

## Domain rules
- Router prefers a better sibling file: ifc_models/<model>_<level>.pdf is rendered
  from <model>.ifc. Same-stem .dxf beats the PDF.
- Swiss DXF layers: ROOM_OUTLINE, ROOM_TEXT, UNIT_OUTLINE, DOOR, WINDOW, FIXTURE, WALL. Units in metres.
  A window is probably several polylines: cluster them. Check against the answers.
- IFC area = NetFloorArea if present, otherwise the 2D footprint of the IfcSpace mesh.
- Open doorways without a door leaf do not count as doors (CubiCasa questions).
- CubiCasa model has no shower class. Showers go to the VLM check.
- Scale priority: vector units > scale note > dimension lines > door width (low confidence).
  Raster questions are counts, so do not overspend on scale.
- Never claim something is absent from a region unless coverage says it was analysed with good confidence.
- OCR: use a Latin-script model (fi/latin), not English. English drops ä/ö/å/æ/ø.

## Tooling
- Python 3.15, formatter/linter: ruff.
- Libraries: ezdxf, ifcopenshell, shapely, pymupdf (import pymupdf), opencv-python-headless,
  numpy, jsonschema, pandas, trimesh, PaddleOCR (fallback: rapidocr_onnxruntime).
- Modal: app stoa-translator, weights Volume cubicasa-weights. T4 is enough for CubiCasa.
  Keep scaledown_window short. Run modal setup before using the CLI. Do not guess the
  Modal API
- CubiCasa code is old. If weights fail to load on torch 2.2, use torch==1.13.1, Python 3.9.
- Quick commands: pytest -q, python eval/score.py, python -m translator.router <path>.