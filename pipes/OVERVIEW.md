# Pipe analysis: overview

## 1. What it does

- **Input:** a scanned or photographed pipe sheet (PNG, JPG, WEBP), or a vector DXF.
- **Output:** one piping index per sheet, schema `piping-0.1` (`pipes/schema/pipe.schema.json`), at
  `pipes/index_pipes/<source>/<stem>#<page>.pipe.json`. It holds pipe segments, fittings, equipment, connections,
  texts, coverage and history, with geometry in the original page pixels.
- **Scans:** a symbol detector and OCR run on Modal; a skeleton tracer then turns the pipes into a graph.
- **DXF:** the graph is read straight from the file, so it matches the drawing exactly. No model is involved.
- **Agent:** a Gemini agent answers questions only through four graph tools. Every answer cites the node and pipe ids
  it used, and the same ids are drawn on the sheet as an overlay.

## 2. Folder map

| Path | Contents |
|---|---|
| `pipes/__main__.py` | `python -m pipes <file>`: an image goes to the scan route, a `.dxf` to the DXF route |
| ★ `pipes/scan.py` | The scan route end to end (decode, detector, OCR, trace, publish); GPU results cached by image SHA-1 |
| ★ `pipes/raster/pipeline.py` | The tracer: prepare the ink, erase symbols and text, skeletonize, snap pipe ends, build the graph |
| `pipes/raster/` | `quality.py` (quality gate), `regions.py` (frames, title block, legend), `erase.py`, `trace_skeleton.py`, `equipment.py`, `detect.py` (merges detections), `tiles.py`, `ocr_run.py` |
| ★ `pipes/graph.py` | Graph rules: a 4-way crossing is two pipes unless a junction dot is drawn; a 3-way point is a tee; an end that reaches nothing is `unsnapped` |
| ★ `pipes/extract/dxf_pipes.py` | DXF route: lines and polylines on pipe layers, INSERT blocks as symbols, TEXT as tags, drawing units as the scale |
| `pipes/tags.py` | Text roles and tag attachment by proximity (equipment and valve ids, line numbers, sizes) |
| ★ `pipes/write.py` | `finalize()`: every route writes through it (convert, derive, validate), so no unvalidated JSON is ever written |
| ★ `pipes/schema/pipe.schema.json` | The piping schema v0.1; `convert.py`, `derive.py` and `validate.py` sit beside it; `README.md` explains them |
| ★ `pipes/tools.py` | The four query tools; code does all the graph work and arithmetic |
| `pipes/agent_tools.py` | The same four tools as JSON definitions and bound callables, plus a dispatcher |
| ★ `pipes/agent.py` | The answering agent (Gemini function calling, with a system instruction that forbids guessing) |
| `pipes/routing.py` | Which tool sets a page gets, decided by which index files exist for it |
| `pipes/demo.py` | Overlay of any tool result on the sheet image |
| ★ `pipes/modal_pipes.py` | All GPU code (Modal app `stoa-pipes`): detector training and detection, Real-ESRGAN upscaling |
| ★ `pipes/config.py` | The frozen detector settings and the measured recall that the tools quote |
| `pipes/ingest/` | PID_dataset adapter (Zenodo 8028570, CC BY 4.0), tile augmentation, GT-to-DXF writer, edge-labelling helper |
| `pipes/eval/` | `score_dets.py` (detector P/R/F1), `score_graph.py` and `metrics.py`, `agent_questions.py` (13 questions with cost), `overlay.py` |
| `pipes/tests/` | 15 test files; `fixtures/pipw_test/` holds a regression sheet with its detections and OCR |
| `pipes/cost.py`, `glossary_pipes.py`, `reuse.py` | Token prices; legend, title-block and continuation words; the translator pieces that are reused |

Why each ★ file matters:
1. `scan.py`: the scan route judges will run.
2. `raster/pipeline.py`: where pixels become a graph.
3. `graph.py`: the connectivity rules that make answers trustworthy.
4. `dxf_pipes.py`: the exact route.
5. `write.py`: the validation guarantee.
6. `pipe.schema.json`: the contract.
7. `tools.py`: what the agent can and cannot claim.
8. `agent.py`: the user-facing answer.
9. `modal_pipes.py`: the only GPU code, which keeps cost visible.
10. `config.py`: one place for the frozen detector and its measured recall.

**Git-ignored** (see `.gitignore`):
- `pipes/data_pipes/raw/`, `ds/`, `yolo/` and `dev/yolo/` (the dataset, tiles and YOLO sets);
- `pipes/index_pipes/*` (outputs, raw caches, overlays);
- `*.pt` (weights).

The YOLO sets, training runs and weights live in the Modal volume **`stoa-pipes-data`** (weights under
`/data/runs/<run>/weights/best.pt`). The dev split and its ground truth (`data_pipes/dev/split.json`,
`data_pipes/dev/*/<id>/gt.pipe.json`) stay tracked.

## 3. Pipeline

**Scan route** (`pipes/scan.py`):
1. **Quality gate** (`raster/quality.py`). It measures first and fixes only the check that failed: a 3× upscale for
   thin strokes or a small page (Real-ESRGAN on Modal, Lanczos as fallback), invert, illumination correction or noise
   removal. The page grid never changes.
2. **Detector** (`modal_pipes.py`, `raster/detect.py`, `config.py`). YOLO, single class, trained on 65 sheets with
   offline augmentation. It runs on 640 px tiles plus a whole-sheet pass at 1280, and the two are merged with NMS.
   Boxes at confidence 0.5 or above are reported; boxes at 0.25 or above are erased before tracing.
3. **OCR** (`raster/ocr_run.py`): the translator's PaddleOCR on Modal, in two passes (as drawn, and rotated 90°).
4. **Erase** (`raster/erase.py`, `raster/regions.py`): symbols, text, frames, title block and legend are removed. The
   title block and legend are reported as `not_analysed`.
5. **Skeleton trace** (`raster/trace_skeleton.py`): the remaining ink is skeletonized and split into polylines.
6. **Snapping** (`raster/pipeline.py`): each pipe end joins the nearest symbol port, then the nearest symbol box,
   within a radius. Otherwise it becomes an `open_end` (near the page edge, or next to "continues" text) or stays
   `unsnapped`.
7. **Equipment** (`raster/equipment.py`): large closed outlines with curves or diagonals that pipes attach to become
   equipment nodes.
8. **Tags** (`pipes/tags.py`): equipment tags, line numbers and sizes attach to the nearest symbol or pipe.
9. **Finalize** (`pipes/write.py`): convert, derive (lengths only when there is a scale) and validate, then publish
   to `index_pipes/<source>/` with an overlay in `index_pipes/overlays/scan/`.

A page whose strokes are under 3 px gets a warning and a partial `connectivity` coverage entry.

**DXF route** (`pipes/extract/dxf_pipes.py`):
- It takes LINE, LWPOLYLINE and POLYLINE on pipe layers (found by layer name), INSERT blocks as symbols, TEXT
  through the same tag rules, and the drawing units as the scale.
- Every end snaps exactly (`vector_exact`). A crossing is a connection only if a small CIRCLE (junction dot) sits on
  it.
- It writes through the same `finalize()`.

## 4. Schema

- **Location:** `pipes/schema/pipe.schema.json` (`schema_version: "piping-0.1"`).
- **Main entities:** `pipe_segments` (centerline, tag, nominal diameter, service, length), `pipe_fittings` (valves,
  tees, elbows, open ends, unknown), `pipe_equipment` (pump, tank, boiler, terminal, …), `pipe_connections` (fitting
  or equipment ↔ segment end), plus `texts`, `pipe_systems`, `pipe_types`, `coverage`, `history` and `statistics`.
  It shares the building schema's `project`, `drawing` and `sources` blocks.
- **Workarounds where the schema has no field:**
  - extractor registry and sheet facts go in `history` (actions `extractor` and `sheet_facts`);
  - coverage lists become `cov:<category>` flags on the entities;
  - an unconnected end is the flag `end0_unsnapped` / `end1_unsnapped`;
  - anything else travels in the entity's `properties.graph`.
- **Round trip:** `convert.view()` turns a piping file back into the working graph that the tools, demo and eval
  read.

## 5. Agent

**The four tools** (`pipes/tools.py`):
- `count_symbols(region, type, tag)`
- `connected_to(node)`
- `path_between(a, b)`
- `pipe_length(edge | tag)`

Every result has the same envelope: `answer`, `node_ids` and `edge_ids` (which the agent must cite), `highlights` in
original page pixels, `coverage`, `reliable` and `caveats`. Where coverage is missing, the tools answer "unknown"
rather than "not connected".

**Router** (`pipes/routing.py`): a page with a pipe index gets the pipe tools, a page with a floor-plan index gets
the floor-plan tools, a page with both gets both, and a page with neither gets a reason naming the command to run.

**Calling the tools:**
- with the agent: `python -m pipes.agent <file> "<question>"` (needs `GEMINI_API_KEY`; only the question and compact
  tool results are sent);
- without the agent: `python -m pipes.tools <pipe.json> <tool> [args]`.

**Demo overlay** (`pipes/demo.py`): it draws any tool result (translucent boxes and polylines, id labels, a caption
band) on the original sheet image without resizing it.

## 6. How to run

```
python -m pipes path/to/sheet.png --source-file NAME            # scan route (Modal: detector + OCR)
python -m pipes path/to/plan.dxf --source-file hypothetical/LVI-heating.dxf   # DXF route, no model
python -m pytest -q pipes/tests                                  # tests
python -m pipes.eval.score_dets --runs full                      # detector P/R/F1 on the 20 test sheets
python -m pipes.eval.agent_questions [--set dev|dxf|all] [--tools-only]   # agent eval with cost
python -m pipes.agent "pid_dataset/0__raw_data/sheets/test/157.jpg" "Is node_8 connected to node_22?"
python -m pipes.demo <pipe.json> path_between node_3 node_9 -o out.png
```

## 7. Results

- **Detector** (20 test sheets, 1,180 symbols; a match is a centre within 0.5 × the GT box diagonal, Hungarian
  assignment). The frozen setting (tiles plus whole sheet, confidence 0.5) scores **precision 90.1%, recall 86.8%,
  F1 88.4%**.
- **Agent, 10 questions on dev sheets 157, 151 and 176:**
  - tool answers correct 8/10; agent answers correct **8/10**; cited ids real **10/10**;
  - both misses are detector misses: q01 (the index has 37 symbols, 46 expected) and q02 (26 against 32 in a
    region);
  - the 3 DXF questions score 3/3.
- **Cost per question:** $0.0325 for all 13 questions, about **$0.0025 per question** with `gemini-3.8-flash` at the
  introductory price ($0.005 from 2027-01-01). Prices are in `pipes/cost.py`.
- **Tests:** 147 passed, 1 skipped, 2 expected failures (`pipes/tests`, 15 files).
- **Training:** the frozen `full` model took 27.7 min on a Modal T4 and cost **$0.27**. Earlier runs: the dev model
  ($0.04) and a `full` run that had trained on the wrong tiles ($0.07).

## 8. Known limits and next steps

- **Dashed lines vanish.** Each dash is below the noise limit. Bridging the gaps added about 660 edges and 700
  unsnapped ends on the 20 test sheets, so it was reverted (strict xfail in `test_creation.py`).
- **Rectangular equipment** reads as a pipe loop or frame. Equipment needs curves or diagonals; accepting rectangles
  would also turn the cells of pipe grids into equipment (strict xfail in `test_creation.py`).
- **Small junction dots** (under about 5 line widths across) read as crossings, so a real connection can be missed.
- **Single-class symbols:** the dataset labels only "Symbol", so the detector cannot tell valves, pumps or radiators
  apart, and the tools say symbols are untyped. Next: typed classes or legend matching.
- **Low resolution:** strokes under 3 px get a warning and partial connectivity coverage. The quality gate's
  upscale feeds only the detector and OCR, not the tracer.
- **Domain gap:** the detector learned process P&ID symbols, while building-services sheets (LVI/VVS) use other
  symbols, thin lines and dense text. Next: hand-labelled real tiles.
- **No legend matching yet:** legends are found and erased, but their symbols are not used to name detected
  symbols.
