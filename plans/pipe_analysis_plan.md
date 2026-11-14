# Pipe schematic analysis: step-by-step build plan

Owner: k. Written 2026-11-14 16:40 (UTC), step 2 changed to dataset ingest 16:55. Starts after the vision translator (`plans/vision_translator_plan.md`) is done, or in parallel by a second person.
Goal: turn a pipe drawing (DXF or scan) into one **pipe index JSON per sheet** that validates against `schema/pipe/pipe.schema.json` (v0.1): symbols as nodes, pipe segments as edges, connectivity, diameters, lengths, coverage and provenance, so the answering agent can answer "how many ball valves", "total length of DN20 cold water", "what does KV-1 connect" and highlight the evidence.

Read with: `schema/pipe/README.md` (the target format and its rules), `plans/vision_translator_plan.md` (shared preprocessing, OCR, Modal app, router), `Stoa_proposal.md`.
References: Gao et al. 2020 (nuclear P&ID, Faster R-CNN, pipes detected as objects 92% vs Hough 65%, ~400 px tiles, symbol grouping by size/shape, tag-to-pipe proximity); Jamieson et al. 2024 IJDAR (YOLO / Faster R-CNN symbol detection on construction drawings; its class list is already mapped to our `type` enum in `schema/pipe/README.md`, rule 7).

Every step below has: **Goal, Do, Inputs/Outputs, Commands, Done when, Quick test, If behind.** Implement in order; each step is testable alone.

---

## 0. What the data tells us (read first, it sets the order)

- **The preview has no pipe drawings and no pipe questions** in the scored types. The only pipe content is MEP in IFC: `ifc_bench/digital_hub_arc.ifc` has 914 heating + 504 plumbing pipe segments, and ~5 `ifc_bench` free-text questions ask about pipes (types, total length). `opening_design` has folders "from Plumbing eng" / "to HVAC engineer", but the files there are architectural sheets.
- So **we have no ground truth in the preview**. Step 2 therefore ingests the pipe dataset k found (PID_dataset, Zenodo 8028570, CC BY 4.0) and converts it to one common layout: YOLO labels, a ground-truth pipe JSON and a DXF counterpart per sheet. It is our eval set and our training set.
- Public pipe datasets (e.g. Dataset-P&ID / Digitize-PID, 500 synthetic process P&IDs with symbol, line and text annotations; PID2Graph) are mostly **process-plant** style: ISA instrument bubbles, process valves, thick main lines. Building services (plumbing, heating, Finnish LVI / Danish VVS sheets) use different symbols (radiators, WC/sink fixtures, floor drains, expansion vessels, risers), thin lines, many crossings and dense text. Expect a domain gap; step 6 lists the mitigations.
- Strategic note for the pitch: if the full dataset also has no pipe questions, pipe analysis earns points on **creativity and verifiable visualization**, not accuracy. Keep it a strong demo on 3 to 5 sheets, not a batch job over 14k files.

**Priority order that follows:** (1) schema hookup, (2) dataset ingest, (3) eval, (4) DXF route (exact, cheap, also tests the graph code), (5) router, then the scan path (6 to 11), then real sheets (12). Optional IFC MEP route (13) is cheap and is the only one that touches real dataset questions.

---

## Time budget at a glance

| # | Step | Hours | MVP? |
|---|---|---|---|
| 1 | Add `pipes/` to the translator repo (reuses its setup, Modal app, preprocessing, OCR) | 0.25 | yes |
| 2 | Dataset ingest (k's dataset to YOLO labels + GT JSON + DXF) | 3 | yes |
| 3 | Pipe additions to the translator's eval harness: graph metrics, question generator | 1 | yes |
| 4 | DXF route (vector parse) | 2.5 | yes |
| 5 | Router + global pass (pipe sheet? legend, title block, to scale?) | 1 | yes |
| 6 | Detector: YOLO on 400 px tiles, symbols + pipe boxes, train on Modal | 3 (mostly GPU waiting, overlap with 7 and 8) | yes |
| 7 | Raster preprocessing, OCR, erase symbols and text, ports | 1 | yes |
| 8 | Skeleton tracer + graph builder (snap, tees, crossings, junction dots) | 3 | yes |
| 9 | Pipe-box tracer: line detector inside YOLO pipe boxes, compare with step 8 | 3 | no |
| 10 | Tags, diameters, lengths, flow direction | 2 | partly |
| 11 | Unknown symbol grouping, legend matching, coverage, writer | 1.5 | yes |
| 12 | Eval round on dataset test split + 5 to 10 real sheets, fixes | 2 | yes |
| 13 | IFC MEP route (optional) | 1.5 | no |
| 14 | Freeze, overlays for the demo, hand-off | 1 | yes |
| | **Total** | **≈ 26** | **MVP ≈ 18** |

MVP = steps 1 to 8, 10 (tags and diameters only), 11, 12, 14: a working DXF route plus a working scan route using the skeleton tracer. Step 9 (k's main tracing idea from Gao) is built after the graph builder works end-to-end, because both tracers feed the same graph builder and the skeleton version gets a full pipeline running in a few hours. Step 12 then picks the better tracer on numbers.

Hand-off contract with the agent teammate: **pipe JSON files in `index_pipes/<source>/<file>#<page>.pipe.json`**, plus `translate_pipes(path) -> list[dict]` as a function and `python -m pipes <path>` as CLI. Same pattern as the translator.

---

## Step 1. Add `pipes/` to the translator repo (0.25 h)

**Reuse, don't rebuild.** The translator's step 1 already gives us the repo, the Modal app, the eval harness (`eval/probe.py`, `eval/score.py` with the official scoring rule, per-template tables and the failures dump), and its steps 5 and 7 give raster preprocessing and the PaddleOCR wrapper. This step only adds the pipe-specific parts on top.

**Do:**
1. Add the package next to `translator/` (only new files listed):
   ```
   pipes/
     __main__.py          # python -m pipes <path>
     router.py            # step 5 (calls translator.router's vector check)
     global_pass.py       # step 5: is pipe sheet, legend/title block regions, diagram type
     schema/              # copy of schema/pipe/: pipe.schema.json, derive.py, validate.py
     ingest/__main__.py, ingest/pid_dataset.py, ingest/gt_to_dxf.py, ingest/augment.py   # step 2
     extract/dxf_pipes.py # step 4
     extract/ifc_mep.py   # step 13 (optional)
     raster/detect.py     # step 6 inference (tiles + merge)
     raster/erase.py      # step 7
     raster/trace_skeleton.py, raster/trace_boxes.py   # steps 8, 9
     graph.py             # step 8: segments + nodes -> graph (shared by all routes)
     tags.py, unknowns.py, coverage.py, write.py       # steps 10, 11
     glossary_pipes.py    # system abbreviations, symbol names, legend headings
   eval/pipes/            # step 3: graph metrics + question generator + pipe probe templates
   data_pipes/            # raw/ (downloads), ds/ (common layout), real/ (hand-labelled), yolo/ (tiles)
   index_pipes/           # outputs
   ```
2. **Modal:** add the pipe functions (`fetch_pid_dataset`, `train`, `detect`, line detector) to the translator's `modal_app.py` as extra functions with their own image (`ultralytics`, `sahi`), or as `pipes/modal_pipes.py` under the same app name. Same volume conventions, same `scaledown_window`.
3. Copy `schema/pipe/{pipe.schema.json,derive.py,validate.py}` to `pipes/schema/`. Schema changes go through `schema/pipe/scripts/build_schema.py`, then copy again.
4. `write.py` exposes `finalize(pipe, drawings={})`: `derive.derive()`, then `validate.check()`, raises with the error list. **Every route writes through `finalize`.**
5. Imports, not copies: `translator.raster.preprocess` (translator step 5), the OCR wrapper (translator step 7), the router's vector-PDF check (translator step 2).

**Extra libraries:** `networkx`, `ezdxf`, `scikit-image`, `sknw`, `ultralytics`, `sahi` (the rest is already in the translator).
**Done when:** `finalize()` runs on `schema/pipe/example_standalone_heating_schematic.json` without error, and raises on a copy with one edge end pointing at a missing node.

---

## Step 2. Dataset ingest: k's dataset to YOLO labels + ground-truth pipe JSON (3 h)

**Goal:** turn the pipe dataset k found into one common layout that every later step uses: the eval set (step 3), the detector training data (step 6) and test inputs for both routes (steps 4 and 8). Nothing after this step reads the dataset's own format.

**The dataset:** `PID_dataset`, Zenodo record 8028570 (https://zenodo.org/records/8028570, DOI 10.5281/zenodo.8028570), Mohit Gupta (Arizona State University), v1, June 2023, **CC BY 4.0** (fine for the hackathon and the demo; keep the credit). One file: `PID_Dataset.zip`, **6.7 GB**, md5 `ade6a91d6299e3751486bb1422a658e0`. Description: P&IDs "from industry and webscraping", for training ML/CV to digitise P&IDs; the authors say their code and trained weights are included "in respective folders" (probably inside the zip). The record page does **not** state the image count, annotation format, classes, whether lines/connectivity/text are annotated, or splits: step 2.1 finds out. "From industry" suggests real scanned sheets, which is good for the scan path; it is still **process P&ID** style, so the building-services gap in section 0 applies.

**Download on Modal, not in the cloud container** (it cannot reach zenodo.org), straight into the volume:
```python
@app.function(image=image, volumes={"/data": vol}, timeout=3600)
def fetch_pid_dataset():
    import subprocess, hashlib
    url = "https://zenodo.org/records/8028570/files/PID_Dataset.zip?download=1"
    subprocess.run(["wget", "-q", "-O", "/data/raw/PID_Dataset.zip", url], check=True)   # mkdir /data/raw first
    # verify md5 ade6a91d6299e3751486bb1422a658e0, then: unzip -q -d /data/raw/pid_dataset
    vol.commit()
```
(add `wget` and `unzip` to `apt_install`). Run `modal run pipes/modal_pipes.py::fetch_pid_dataset`, then `modal volume get` only the folders you need locally, or run the ingest itself as a Modal CPU function. If the zip contains the authors' trained weights, try them in step 6 before training: they may already detect valves well.

**Inputs (fill in after step 2.1):**
```
DATASET_NAME   = "pid_dataset"                # Zenodo 8028570
DATASET_ROOT   = "data_pipes/raw/pid_dataset/"     # downloaded once, never edited
IMAGE_GLOB     = "<images/*.png>"
ANNOT_FORMAT   = "<coco | voc_xml | yolo | graphml/json graph | npy | per-image csv | ...>"
HAS_LINES      = <true/false>   # pipe segments annotated (polylines or boxes)?
HAS_GRAPH      = <true/false>   # connectivity annotated (which symbols a line joins)?
HAS_TEXT       = <true/false>   # text boxes + transcriptions annotated?
LICENSE        = "<licence>"    # record it; research/hackathon use must be allowed
```
**Outputs per sheet** in `data_pipes/ds/<split>/<id>/` (same layout whatever the source):
`sheet.png` (original image, unchanged pixels), `yolo.txt` (full-page boxes in page pixels, our classes), `gt.pipe.json` (schema-valid, `extractor: human`, confidence 1), and `sheet.dxf` (written from the GT, see 2.6). Plus `data_pipes/ds/class_map.csv` and `data_pipes/ds/REPORT.md`.

**Do:**
1. **Inspect first (30 min max).** `unzip -l PID_Dataset.zip | head -100` to see the folder structure (images, annotations, code, weights), then open 3 images with their annotations and write down in `REPORT.md`: annotation format, class list with counts, image sizes, whether pipes are annotated as polylines, boxes or not at all, whether connectivity is given, and what the drawings look like (process P&ID vs building services, scan vs clean render, line widths, languages). This decides how much of 2.3 to 2.5 applies.
2. **Adapter** (`pipes/ingest/pid_dataset.py`): one class implementing a small interface, so a second dataset later is one new file:
   ```python
   class Adapter:
       def sheets(self) -> Iterator[str]: ...                 # sheet ids
       def image_path(self, sid) -> Path: ...
       def symbols(self, sid) -> list[dict]: ...              # {cls_raw, bbox_px, rotation?, text?}
       def lines(self, sid) -> list[dict]: ...                # {polyline_px, style?, cls_raw?}  ([] if HAS_LINES is false)
       def connections(self, sid) -> list[tuple] | None: ...  # (line_idx, symbol_idx | line_idx) pairs, None if HAS_GRAPH is false
       def texts(self, sid) -> list[dict]: ...                # {bbox_px, text}  ([] if HAS_TEXT is false)
   ```
   Common parsers to reuse: COCO (`pycocotools` or plain json), Pascal VOC (`xml.etree`), YOLO txt (denormalise), GraphML/JSON graphs (`networkx.read_graphml`).
3. **Class map** (`class_map.csv`: `raw_class, our_class, note`): map each dataset class to the step 6 `YOLO_CLASSES` / schema `type` where the meaning matches (gate/globe/check/ball/control valves, pumps, meters, instruments → `instrument_other`, flow arrows, junction dots, off-page connectors); everything else → `symbol_other`. Keep the raw class in `nodes[].subtype` so nothing is lost. Classes with fewer than 20 examples also go to `symbol_other` for training (but keep their raw name in GT).
4. **Pipe boxes** (Gao's pipes-as-objects idea): from line annotations, split each polyline at direction changes; each straight piece becomes a box padded by 3 px, class `pipe_h`, `pipe_v` or `pipe_d` by angle (±10°). If the dataset has **no line annotations**, skip pipe classes for training (the skeleton tracer in step 8 does not need them) and build the GT graph in 2.5 from our own trace, corrected by hand on the test split only.
5. **GT pipe JSON:** nodes from symbols (ports = where annotated lines touch the symbol box, within 3 px), edges from lines; ends `snapped` to the node given by `connections()` if HAS_GRAPH, otherwise by endpoint-in-box (radius 3 px, these are annotations so this is exact enough); line crossings without a junction node stay unconnected; texts with roles via the step 10 regexes. `sheet.route = "raster_pipeline"`, `diagram_type`/`is_to_scale` from the dataset's metadata or `null`. Then `finalize()` (step 1). Sheets that fail validation are listed in `REPORT.md`, not silently dropped.
6. **DXF counterpart** (`pipes/ingest/gt_to_dxf.py`, 30 min): write each GT JSON as a DXF (one layer per system, edges as LWPOLYLINE, nodes as BLOCK + INSERT named by `type` with a `TAG` attribute, texts as TEXT; pixels to mm via a fixed 0.1 mm/px). This is the test input for the DXF route (step 4) when the dataset has no CAD files, and it checks that both routes produce the same graph from the same content.
7. **Splits by sheet:** 80% train, 10% val, 10% test (fixed seed, stratify by source subfolder if any). The **test split is never used for tuning** until step 12.
8. **Domain-gap check:** in `REPORT.md`, list which of our building-services types (radiator, fixture, floor drain, expansion vessel, pipe_up/down, balancing valve) have zero or few examples. These are the ones legend matching and `unknown` grouping (step 11) and hand-labelled real tiles (step 12) must cover.

**Commands:**
```
python -m pipes.ingest --adapter pid_dataset --root data_pipes/raw/pid_dataset --out data_pipes/ds --seed 0
python -m pipes.ingest.gt_to_dxf data_pipes/ds/test
```
**Done when:** every sheet has the four files, ≥ 95% of GT JSONs pass `validate.py`, `REPORT.md` lists format, class map and counts, and an overlay of `yolo.txt` + GT edges on 5 random sheets lines up by eye.
**Quick test:** for one sheet, the number of `ball_valve` boxes in `yolo.txt` equals `summary.node_counts.ball_valve` in `gt.pipe.json`, and both equal the raw annotation count for that class.
**If behind:** symbols only (no pipe boxes, no DXF counterpart); build GT graphs by hand-correcting our trace on 10 test sheets.

## Step 3. Pipe additions to the eval harness (1 h)

**Reuse:** the translator's `eval/score.py` (official rule: counts exact, lengths within 2%; per-template table; `failures.jsonl`; cost per file) and its probe pattern. Do not write a second scorer. Add only what pipes need that the translator harness can't do, because there are no pipe questions in the dataset and pipe quality is about the graph, not only answers.

**Do** (in `eval/pipes/`):
1. `metrics.py`, given a predicted and a GT pipe JSON:
   - **Symbols:** match non-structural nodes (all but `tee`, `junction`, `elbow`, `cross`, `open_end`) by Hungarian assignment on centre distance ≤ 0.5 × GT box diagonal; precision, recall, per-type confusion.
   - **Pipes:** rasterise both edge sets (1 px); recall = share of GT pixels within 3 px of a predicted pixel, precision = the reverse.
   - **Graph:** after node matching (structural nodes type-agnostic, within 3 × stroke width), an edge is correct if both end nodes map to a GT edge's ends. Edge P/R. **Connectivity accuracy:** over pairs of GT terminal nodes, share where "connected or not" agrees (`networkx.has_path`). This is the number that tells you whether crossings and snapping work.
   - **Unsnapped ends:** count, and the share on a GT connection (missed snaps) vs on a GT open end (correct flags).
2. `gen_questions.py`: from each GT JSON write lines in the `questions.jsonl` format (`path, question, answer, unit, type`), so the translator's `score.py` can read them unchanged: counts per symbol type, total length per system and size (only where lengths exist), "is A connected to B" (`text` yes/no), "which equipment does line X connect". EN + FI wording.
3. `probe_pipes.py`: the pipe templates (regex → read `summary.node_counts`, `summary.length_m_by_system_and_size`, walk edges by `line_tag`), registered in the translator's probe so `score.py --index index_pipes --questions data_pipes/ds/test/questions.jsonl` just works.
4. `score_graph.py`: runs `metrics.py` over a split and prints one table per layer (symbols, pipes, graph, unsnapped), next to the QA table from `score.py`.

**Commands:** `python -m eval.pipes.score_graph --pred index_pipes/ds_test --gt data_pipes/ds/test` and `python -m eval.score --index index_pipes/ds_test --questions data_pipes/ds/test/questions.jsonl`
**Done when:** GT against itself gives 100% everywhere; an empty prediction gives 0% without crashing.
**Quick test:** delete one edge from a GT copy: edge recall drops by exactly 1/N and connectivity accuracy drops.

---

## Step 4. DXF route (2.5 h)

**Goal:** exact graph from vector pipe drawings, zero model cost. Also the first real test of `graph.py`.

**Inputs:** `.dxf` (DWG: convert first with the free ODA File Converter on a laptop, `ODAFileConverter in_dir out_dir ACAD2018 DXF 0 1`; not in the container). **Output:** pipe JSON with `route: vector_parser`.

**Do:**
1. **Pick pipe layers** in `dxf_pipes.py`: layer name matches the glossary (`PIPE`, `PIPING`, `P-`, `PLUMB`, `SAN`, `HEAT`, `VVS`, `LVI`, `KV`, `LV`, `VV`, `JV`, `RÖR`, `ROHR`, `HEIZ`, `SANIT`...), case-insensitive; else fall back to all layers with LINE/LWPOLYLINE entities that have at least one endpoint on an INSERT bbox. Record the layer on each edge (`edges[].layer`); system from the layer name via the glossary.
2. **Segments:** LINE, LWPOLYLINE, POLYLINE (explode bulges with `ezdxf.math` to short segments), ARC only if it is on a pipe layer (bends). Coordinates in drawing units, then `source_to_px`.
3. **Nodes:** each INSERT on a pipe layer or touched by a pipe → node. `type` from block name via a mapping table in `glossary_pipes.py` (exact names, then keyword match: `VALVE`, `VENTTIILI`, `VENTIL`, `PUMP`, `PUMPPU`, `PUMPE`, `RAD`, `PATTERI`...), else `unknown`. Tag from attributes (`insert.attribs`, tags `TAG`, `NAME`, `ID`). Bbox from `insert.virtual_entities()` extents. **Ports:** points where a pipe segment end lies on or within tol of the block bbox.
4. **Graph** with `graph.py` (written here, extended in step 8): endpoint coincidence within tol (0.5 mm × units), `snap: vector_exact`; a segment end on another segment's interior → split it, `tee` node; two segments crossing without a shared vertex → **not connected** (no node); a DXF CIRCLE/filled HATCH dot at the crossing → `junction` node. Ends that touch nothing → `open_end` node only if a text like `TO`, `JATKUU`, `FORTSAETTER`, `->` or a sheet ref sits within 10 mm, else unsnapped with a connectivity coverage entry.
5. **Texts:** TEXT/MTEXT (`mtext.plain_text()`), roles by regex (step 10), assigned to edges/nodes with `tags.py`.
6. **Frame and highlighting:** render the modelspace with `ezdxf.addons.drawing` (matplotlib backend) into a figure whose axes exactly span the drawing extents (`ax.set_position([0,0,1,1])`, `ax.set_xlim(xmin,xmax)`, `ax.set_ylim(ymin,ymax)`, figure size = `W/dpi, H/dpi`), so `source_to_px = [s,0,-s*xmin, 0,-s,s*ymax]` is exact. Store the PNG next to the JSON.
7. **Scale:** `$INSUNITS` gives units; `is_to_scale` from the global pass (step 5). If not to scale, only `L=` written lengths count (`derive.py` already handles this).

**Commands:** `python -m pipes data_pipes/ds/test/<id>/sheet.dxf -o index_pipes/ds_test_dxf/`
**Done when:** on the test-split DXFs written by step 2.6: symbol P/R = 100%, edge P/R ≥ 99%, connectivity accuracy 100%, all QA templates 100%. (Those DXFs are written from the GT, so they are exact; anything less is a bug in `graph.py`, which is exactly why this step comes before the scan path.)
**Quick test:** a sheet with a dotless crossing: the two crossing lines must be in different components if nothing else joins them.
**If behind:** skip arcs and the off-sheet text rule.

---

## Step 5. Router + global pass (1 h)

**Goal:** decide whether a file is a pipe sheet, which route it takes, and where the legend and title block are.

**Do:**
1. `router.py`:
   - `.dxf` → DXF route. `.dwg` → DXF route after conversion, else skip with a log line.
   - `.ifc` with `IfcPipeSegment` / `IfcFlowSegment` → IFC MEP route (step 13) if built.
   - `.pdf`: reuse the translator's vector check (`get_text()` has words, `len(get_drawings()) > 50`, no page-sized image). Vector PDF → render at 200 dpi and use the **raster route** for the hackathon (vector PDF pipe parsing is a cut item); record `read_from`.
   - `.png/.jpg` and scanned PDFs → raster route.
2. **Is it a pipe sheet?** (`global_pass.py`): yes if any of: filename/sheet id matches `^(P|M|LVI|VVS|KVV|HVAC|PLUMB)` or contains `PIPING`, `PLUMBING`, `HEATING`, `KAAVIO`, `STRANG`; OCR/text layer has ≥ 3 diameter tokens (`DN\d+`, `Ø\d+`) or system tags (`KV`, `LV`, `VV`, `PWC`); or the detector (step 6) finds ≥ 5 valve-class boxes. The translator calls this too, so a sheet can get both a drawing JSON and a pipe JSON.
3. **Regions** (raster): title block = largest rectangle touching the bottom or right border with ≥ 3 inner horizontal lines (`cv2.findContours` on the binary, filter by area and position). Legend = rectangle containing a heading from the glossary (`LEGEND`, `SELITYKSET`, `MERKINNÄT`, `SIGNATURFORKLARING`, `TEGNFORKLARING`, `LEGENDE`, `TECKENFÖRKLARING`). Write both to `sheet.regions`; tracing ignores them (coverage `not_analysed` with a note), but step 11 reads the legend.
4. **Diagram type / to scale:** title keywords: `KAAVIO`, `PERIAATE`, `STRANG`, `SCHEMA`, `PRINCIP`, `RISER`, `DIAGRAM`, `ISOMETRI` → schematic or riser_diagram, `is_to_scale: false`; `POHJA`, `PLAN`, `GRUNDRISS` + a scale note `1:\d+` → pipe_plan, `is_to_scale: true`; otherwise `null` (lengths flagged).

**Done when:** all dataset sheets route correctly (DXF → vector, PNG → raster), title block and legend boxes (where the dataset has them; else check 10 by eye) overlap GT with IoU > 0.7 on 90%, and none of the 241 CubiCasa PNGs is called a pipe sheet.
**Quick test:** `python -m pipes.router data_pipes/ds/test/<id>/sheet.png` prints `raster, riser_diagram, to_scale=false, legend=[...], title_block=[...]`.

---

## Step 6. Detector: YOLO on tiles, symbols + pipe boxes (3 h, mostly GPU waiting)

**Goal:** one detector that finds symbols, junction dots, flow arrows and pipe-segment boxes on 400 px tiles.

**Classes** (keep it to ~30, mapped 1:1 to the schema `type` enum, plus the pipe classes): `gate_valve, ball_valve, globe_valve, check_valve, control_valve, balancing_valve, safety_valve, pressure_reducing_valve, valve_other, pump, meter, backflow_preventer, strainer, expansion_vessel, tank, heat_exchanger, boiler, radiator, fixture, floor_drain, reducer, cap, pipe_up, pipe_down, off_sheet_connector, instrument_other, junction_dot, flow_arrow, symbol_other, pipe_h, pipe_v, pipe_d`. Write the list once in `glossary_pipes.py` as `YOLO_CLASSES` and a `YOLO_TO_TYPE` map.

**Data (`data_pipes/yolo/`):**
1. **PID_dataset** (Zenodo 8028570, step 2: `sheet.png` + `yolo.txt`, train and val splits only).
2. **Optional second source:** if another public set (Dataset-P&ID, PID2Graph) adds classes k's dataset lacks, add it with a second step 2 adapter; same layout, same class map. Only worth it if step 2.8 shows large gaps.
3. **Tiling:** 400 x 400 px tiles with 80 px overlap (Gao used ~400 px), keep a box in a tile if ≥ 60% of it is inside; pipe boxes are cut at the tile border and kept if ≥ 12 px long. If two sources: cap the larger one at ~60% of tiles. Drop 80% of tiles with no labels.
4. Split by **sheet**, not by tile.

**Domain gap mitigations** (in order of payoff): (a) map classes carefully and send the rest to `symbol_other` (step 2.3); (b) heavy scan augmentation, done offline in `pipes/ingest/augment.py` on the tiles: blur, JPEG 30 to 80, salt-and-pepper, uneven background, line thinning/dilation, 10% inverted; plus Ultralytics `degrees=0, fliplr=0.5, flipud=0.5, mosaic=1.0, hsv_v=0.4`. 90° rotations are fine for symbols, but swap `pipe_h`/`pipe_v` when you rotate, so do rotations offline, not in Ultralytics; (c) a catch-all `symbol_other` class so unknown symbols are still found and become `unknown` nodes (step 11) instead of breaking the trace; (d) after the first real sheets come in (step 12), hand-label 5 to 10 tiles-worth per sheet (CVAT or Label Studio locally, or Roboflow free) and fine-tune 10 epochs; (e) legend template matching (step 11) for symbols the model never saw.

**Train on Modal** (`modal_pipes.py`):
```python
import modal
app = modal.App("stoa-pipes")
vol = modal.Volume.from_name("stoa-pipes-data", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.11")
         .apt_install("libgl1", "libglib2.0-0")
         .pip_install("ultralytics==8.3.*", "sahi", "opencv-python-headless"))

@app.function(image=image, gpu="A10G", volumes={"/data": vol}, timeout=3 * 3600)
def train(epochs: int = 60, model: str = "yolo11s.pt"):
    from ultralytics import YOLO
    m = YOLO(model)
    m.train(data="/data/yolo/pipes.yaml", imgsz=416, epochs=epochs, batch=64,
            project="/data/runs", name="pipes", patience=15, flipud=0.5, fliplr=0.5, degrees=0)
    vol.commit()
```
Upload once: `modal volume put stoa-pipes-data data_pipes/yolo /yolo`. Run: `modal run pipes/modal_pipes.py::train --epochs 60`. Look at `results.csv` after ~10 epochs; if `pipe_*` mAP is near 0, check the label conversion before waiting longer.

**Inference** (`raster/detect.py`): SAHI sliced prediction with the trained weights (`AutoDetectionModel.from_pretrained(model_type="ultralytics", model_path=..., confidence_threshold=0.25)` then `get_sliced_prediction(img, model, slice_height=400, slice_width=400, overlap_height_ratio=0.2, overlap_width_ratio=0.2)`; check the `model_type` name for your SAHI version). Symbols: SAHI's merge (NMS) is fine. **Pipe boxes: do not NMS-merge**; collect raw per-tile pipe boxes and merge them in step 9 by collinearity. Run on CPU locally for a few sheets, or as a Modal `T4` function for batches. Write raw detections to `index_pipes/_raw/<id>.det.json` so post-processing never reruns the model.

**Done when:** on the dataset test split, page-level symbol recall ≥ 90% and precision ≥ 90% for the 10 most frequent classes; `junction_dot` recall ≥ 90%; `pipe_*` box mAP@0.5 reported (no target, step 12 decides whether pipe boxes beat the skeleton).
**Quick test:** overlay detections on 3 test scans next to the GT overlay.
**If behind:** drop the pipe classes; step 8 does not need them.

---

## Step 7. Raster preprocessing, OCR, erase, ports (1 h)

**Goal:** a clean binary "pipes only" image, all texts with boxes, and port positions on every symbol.

**Do:**
1. `prep.py`: call the translator preprocessing (invert dark backgrounds, CLAHE grey for OCR, adaptive threshold binary for tracing; never crop or resize the page in place, record transforms). Deskew if the global pass finds a skew > 0.3° (Hough on long lines), record the angle in `frame` and map everything back before writing.
2. **Stroke width** `w`: median of `2 × distance_transform` sampled on the skeleton of the binary. All later thresholds are multiples of `w`.
3. **OCR**: translator PaddleOCR wrapper (Latin model for ä/ö/æ/ø/å), run on the full page plus a 90° rotated copy (vertical tags along risers); keep boxes in page pixels. Write to `texts[]` with `role: unknown` for now.
4. **Ports** (`erase.py`): for each symbol box, take the skeleton of the un-erased binary, and the points where it crosses the box border, expanded by `w`. Cluster them; each cluster centre is a port (`ports_px`). A symbol with 0 ports is decoration (legend, title block) or a free-standing item; with 1 port it is a terminal (fixture, cap, gauge).
5. **Erase**: fill symbol boxes (shrunk by 1 px), text boxes, title block and legend regions with background on the binary. Keep junction dots (they are evidence for step 8; record their centres first, then erase them too so the skeleton is clean).
6. Remove connected components smaller than `(3w)²` px (noise, leftover characters).

**Done when:** on 5 dataset test sheets, the erased binary contains only pipe strokes by eye, and ≥ 95% of GT ports are within `2w` of a computed port (using GT symbol boxes).
**Quick test:** a 2-port valve on a straight pipe gets exactly 2 ports, on opposite sides.

---

## Step 8. Skeleton tracer + graph builder (3 h)

**Goal:** the full raster pipeline end-to-end: segments from the skeleton, then one graph builder that every route uses.

**Do:**
1. `trace_skeleton.py`: `skimage.morphology.skeletonize(binary)`, then `sknw.build_sknw(skel)`. Each sknw edge's `pts` → polyline (`cv2.approxPolyDP`, epsilon `w/2`). Prune spurs: edges shorter than `2w` with a degree-1 end. Output a list of segments (`polyline_px`, `dashed=False`) plus sknw degree ≥ 3 points as crossing candidates.
2. `graph.py` (same module as step 4, now with tolerances in pixels), input: segments, symbol nodes with ports, junction-dot centres, sheet border:
   1. **Stitch** collinear pieces: two segment ends with angle difference < 8° and gap < `6w` (or < 3 × median dash period if the gap pattern repeats) → one run; if ≥ 3 gaps were bridged, `line_style: dashed`. This also reconnects pipes cut by erased text.
   2. **Crossings:** at each degree-4 skeleton point, pair the four branches into two straight-through lines by opposite directions. **If a junction dot is within `2w`** (detector or pixel density: dark share in a disk of radius `2.5w` on the un-erased binary > 0.8) → split both lines, add a `junction` node. **Otherwise not connected**: keep two independent runs, no node. Bridge arcs (a small semicircle hop) are treated as crossings too.
   3. **Tees:** a run end within `r` of another run's interior, roughly perpendicular → split that run, add a `tee` node (`snap: snapped`, distance recorded). Degree-3 skeleton points become tees directly.
   4. **Corners:** two run ends meeting at ~90° with no symbol → merge into one polyline (no `elbow` node; fewer nodes, same answers).
   5. **Ends to symbols:** nearest port within `r` → `snapped` with `port_index`; else nearest symbol box border within `r` → `snapped`, port_index null.
   6. **Leftovers:** end on the sheet border, or within `5w` of an off-sheet text or connector → `open_end` / `off_sheet_connector` node. Otherwise `unsnapped` (`node_id: null`) and a `connectivity` coverage entry `partial` with a box of side `6r` around the end and `element_ids: [edge_id]`.
   7. Snap radius `r = max(8 px, 3w)`; tune on the dataset **val** split with `connectivity accuracy` as the target.
   8. Split runs at every node → edges with two `ends`; set `ends[].point_px` equal to the first/last polyline point (validator rule).
3. Wire it: `python -m pipes <scan.png>` runs prep → detect → OCR → erase → trace → graph → write (steps 10 and 11 can be stubs at first).
4. **Oracle mode:** `--oracle-symbols gt.pipe.json` uses GT symbol boxes instead of the detector. Run it once: it tells you how much error comes from tracing vs detection.

**Done when:** on the dataset test split, with the trained detector: pipe recall ≥ 95%, edge recall ≥ 85%, connectivity accuracy ≥ 90%, every output passes `validate.py`. With oracle symbols, edge recall ≥ 92%.
**Quick test:** a sheet with one dotless crossing and one dotted tee: crossing → 0 new nodes, tee → 1 `junction` node with 4 or 3 edges.
**If behind:** skip dash stitching and bridge arcs (check `REPORT.md` for how common dashed lines are first).

---

## Step 9. Pipe-box tracer: line detector inside YOLO pipe boxes (3 h)

**Goal:** k's main tracing idea (Gao et al.): use the detector's `pipe_h/v/d` boxes to decide **where** pipes are (robust to text, hatching, dimension lines and noise that a skeleton picks up), and a line detector **inside** each box for the exact polyline. Then compare with step 8 on numbers.

**Do:**
1. **Merge pipe boxes** from the raw per-tile detections: same class, overlapping or touching along the long axis, centre lines within `2w` → one box. This undoes the tile cuts.
2. **Inside each merged box** (padded by `2w`, on the erased binary): run a line segment detector, keep segments whose angle matches the class (h: |angle| < 10°, v: |angle − 90°| < 10°, d: the rest), fit one line through them (total least squares on the segment endpoints weighted by length), clip it to the extent of the dark pixels along that line. Result: one straight segment per box.
   - Line detector choice: start with OpenCV's LSD (`cv2.createLineSegmentDetector()`, CPU, free) as the baseline; then try **M-LSD** (Apache 2.0, small, fast) or **DeepLSD** (more accurate on noisy scans, GPU) as a Modal `T4` function taking crops. Spend at most 45 min getting one of them to install; keep whichever wins on pipe recall.
3. Feed the segments into the **same `graph.py`** (step 8). Crossing candidates now come from segment intersections instead of skeleton degree-4 points: two segments that cross in their interiors are a crossing unless a junction dot is within `2w`.
4. **Gaps:** pipe pixels that no box covers (missed boxes) are invisible to this tracer. Fill them from the skeleton tracer: skeleton edges whose pixels are > `3w` from every box-tracer segment are added as extra segments with confidence × 0.7. This hybrid is usually better than either alone.
5. Flag `--tracer skeleton|boxes|hybrid` in the CLI.

**Done when:** a table on the dataset test split with pipe recall/precision, edge P/R and connectivity accuracy for `skeleton`, `boxes`, `hybrid`; the default tracer is set to the best one on connectivity accuracy. Expect `boxes` to win on precision on noisy scans and `skeleton` on recall.
**Quick test:** a pipe crossed by a dimension line: `boxes` keeps the pipe and ignores the dimension line; `skeleton` may add a false tee.
**If behind:** skip entirely; the skeleton tracer is the MVP. Mention Gao's numbers in the pitch as the planned upgrade.

---

## Step 10. Tags, diameters, lengths, flow direction (2 h)

**Goal:** attach texts to the graph so size and length questions work, and give flow direction where the drawing shows it.

**Do:**
1. **Text roles** (`tags.py`, regex on OCR/DXF text, after normalising `O`↔`0`, `,`→`.`, `Ø`/`⌀`/`ø`):
   - diameter: `^(DN|dn)\s?\d{2,3}$`, `^[Ø⌀ø]\s?\d{1,3}(\.\d)?$`, `^\d{2,3}\s?[x×]\s?\d(\.\d+)?$` (copper OD × wall), `^\d{2,3}\s?mm$`;
   - length: `^L\s?=\s?\d+([.,]\d+)?\s?(m|mm)?$`;
   - line tag: `^(KV|LV|LVK|VV|VVC|PWC|PWH|PWH-C|JV|SV|LJ|[A-Z]{1,4})-?\d{0,3}$` when near a pipe and not a diameter;
   - equipment tag: `^[A-Z]{1,3}\d{1,3}[A-Z]?$` near a symbol;
   - system label and legend entries from the glossary (`glossary_pipes.py`: `KV` cold water, `LV` hot water (FI), `LVK` hot water circulation (FI), `VV` hot water (DK/SE/NO), `VVC` circulation, `PWC`/`PWH`/`PWH-C` (EN 806), heating supply/return words `MENO`/`PALUU`, `FREMLØB`/`RETUR`, `VORLAUF`/`RÜCKLAUF`, `SUPPLY`/`RETURN`; extend from real legends).
2. **Proximity mapping** (Gao): for each text with a pipe role, the nearest edge by point-to-polyline distance from the text box centre, within `3 × text height`, preferring edges parallel to the text baseline; equipment tags go to the nearest symbol within `2 ×` its box size. **Leader lines:** a traced edge with one end at a text box and the other on a pipe or symbol, shorter than 150 px, is a leader: delete it as an edge and use it as the association instead.
3. **Diameters:** `diameter.value` from the label, `source: label`; then **propagate** along each run until a `reducer`, `tee`, `junction` or a different label (`source: propagated`). Edges still without a size → `diameters` coverage `partial` with their ids.
4. **Lengths:** `length.written_m` from `L=` labels. Geometry lengths come from `derive.py` (only if `is_to_scale` and the scale is known: scale note `1:50` + render dpi, or a linked drawing's scale). Nothing else to do here.
5. **Line tags** span edges: walk from the tagged edge along the run (through valves, not through tees) and set `line_tag` on all of them.
6. **Flow direction:** for each `flow_arrow` marker, attach to the nearest edge (`on_edge_id`); the **head** (filled triangle) holds most of the dark pixels, so the arrow points from the box centre toward the dark-pixel centroid, projected on the edge direction → `start_to_end` or `end_to_start`. Propagate along the run until a branch.
7. **System per edge:** from the line tag, else from the DXF layer, else from colour on colour scans (blue → cold water, red → hot water / heating supply, green dashed → circulation is a guess: mark confidence 0.5), else `unknown`.

**Done when:** on the dataset test split (if it annotates text; else on 5 hand-checked sheets): diameter accuracy per edge ≥ 90% (label or propagated), the "total length of DN20 cold water" template ≥ 90% within 2%, flow direction correct on ≥ 90% of edges that have an arrow.
**If behind:** diameters + line tags only (skip flow and colour systems).

---

## Step 11. Unknown symbols, legend matching, coverage, writer (1.5 h)

**Goal:** never drop what we can't classify, use the sheet's own legend instead of a VLM, and write honest coverage.

**Do:**
1. **Unknown symbols** (`unknowns.py`): detections of `symbol_other` or confidence < 0.4 become nodes with `type: unknown`. Group them (Gao's size/shape grouping): crop from the binary, normalise to 32 x 32, feature = box w/h/aspect + Hu moments + the 32x32 bitmap, cluster with DBSCAN (`eps` tuned on the val split). Each node gets `attributes.group: "U1"`, `"U2"`... so the agent can say "7 symbols of the same unknown kind (group U1), shown here" and the reviewer fixes a whole group at once. No VLM call.
2. **Legend matching:** if the global pass found a legend, detect symbols inside it (same detector), OCR the text to the right of each one, and store `relations` of kind `legend_meaning` (symbol crop → text). Then template-match every unknown group's medoid against the legend symbols (`cv2.matchTemplate`, normalised 32 x 32, score > 0.7): on a hit, the group gets `subtype` = legend text and a mapped `type` if the text matches the glossary (e.g. "palloventtiili" → `ball_valve`), `provenance.evidence_ids` pointing at the legend relation.
3. **Coverage** (`coverage.py`), per category, on a 4 x 4 grid over `diagram_area` plus `page`:
   - `symbols`, `markers`, `pipes`, `text`: `analysed` where the detector/OCR ran, confidence = mean element confidence in the cell; `not_analysed` for legend/title block regions.
   - `connectivity`: `analysed` per cell, except the `partial` boxes around every unsnapped end (step 8) and around every `unknown` node.
   - `diameters`, `lengths`, `flow_direction`, `tags`: `partial` with `element_ids` of edges lacking them; `lengths` is `not_analysed` if `is_to_scale` is null and no `L=` labels exist.
   - `scale`: from the scale source.
4. **Writer** (`write.py`): assemble `sheet` (with `route`, `diagram_type`, `is_to_scale`, `sheet_id`, `level_id` from the title block OCR), `frame` (`frame_key = "<source_file>#<page>@<W>x<H>"`), `scale`, `links: []` (linking to drawing files is optional and done by the translator side if both exist on the same page: `relation: same_page`, identity transform), `extractors` (one entry per component with version and `cost_usd`: Modal GPU seconds × price; OCR on CPU = 0), ids `n_`, `e_`, `t_`, `m_`, `r_`, `cov_`; then `finalize()`.

**Done when:** every test-split sheet writes a valid pipe JSON; unknown nodes appear with groups; no unsnapped end lacks a coverage entry (the validator enforces it).
**Quick test:** remove `pump` from the detector's class map on purpose: pumps come out as one `unknown` group, and legend matching renames the group to the legend text.

---

## Step 12. Eval round on the dataset test split + real sheets, fixes (2 h)

**Do:**
1. Run `eval/score.py + eval/pipes/score_graph.py` on the dataset test split for the DXF route, the scan route (default tracer) and the oracle-symbol variant. Sort failures by (questions affected × error rate). Typical fixes in order of payoff: snap radius, junction-dot threshold, stitch gap (text cuts pipes), confidence threshold for `symbol_other`, leader-line rule.
2. **Real sheets:** from the full dataset (if released) or public sources with a clear licence, pick 5 to 10 building-services sheets: filter filenames and text layers with the step 5 pipe-sheet rule (`opening_design` MEP transfers, any `P-`/`M-`/`LVI`/`VVS` sheets). Hand-label GT for 3 to 5 of them in a quick way: draw symbol boxes in CVAT/Label Studio, then write the GT graph by correcting our own pipeline output (edit the JSON, set `extractor: human`); ~30 min per sheet. Score the same metrics. This is the honest number for the pitch for building drawings; the dataset test number shows in-domain accuracy and must be labelled as such.
3. If real-sheet symbol recall is far below the dataset test split: label 50 to 100 real tiles and fine-tune (step 6 mitigation d, ~30 min on Modal).
4. Record the final table: per route, per layer (symbols, pipes, connectivity, QA), cost per sheet.

**Done when:** the table exists for the dataset test split and ≥ 3 real sheets, and the default tracer choice is backed by it.

---

## Step 13. IFC MEP route (optional, 1.5 h)

**Goal:** the only pipe data with questions in the preview (`ifc_bench/digital_hub_arc.ifc`): build the graph straight from the model.

**Do:** `ifcopenshell.open`; elements `IfcPipeSegment`, `IfcPipeFitting`, `IfcValve`, `IfcPump`, `IfcTank`, `IfcSpaceHeater`, `IfcSanitaryTerminal`, `IfcFlowTerminal`, `IfcFlowController` (IFC2x3 uses the generic `IfcFlow*` classes with type objects). Nodes = everything except segments; edges = segments. Connectivity from ports: IFC4 `IfcRelNests` (element → `IfcDistributionPort`), IFC2x3 `IfcRelConnectsPortToElement`; port pairs from `IfcRelConnectsPorts`. System from `IfcRelAssignsToGroup` to `IfcDistributionSystem` / `IfcSystem` (name → glossary). Length: `Qto_PipeSegmentBaseQuantities.Length`, else the segment's axis or bounding-box long side; **check the unit**: the benchmark answer quotes 1,207,377 m of heating pipe, which looks like millimetres summed as metres. Report both and say so. Frame: plan projection per storey (matplotlib, like translator step 4.3). `route: vector_parser`, `file_format: ifc`, `source_ref` = GlobalId.
**Done when:** digital_hub writes a valid pipe JSON whose segment and valve counts per system match the `ifc_bench` answer (914 heating segments, 65 heating valves, 504 plumbing segments).

---

## Step 14. Freeze and hand-off (1 h)

1. `python -m pipes <path>` and `translate_pipes(path)` work for the agent teammate; README with the route table, the class list, tolerances and the eval table.
2. Demo overlays (adapt `schema/pipe/scripts/overlay.py`): one DXF sheet, one scan with the hybrid/skeleton trace, nodes coloured by type, unsnapped ends and `unknown` groups in red, plus one answer highlighted ("total DN20 cold water = 14.6 m" with its edges lit up).
3. Agent tools to agree with the teammate: `count_symbols(type, region?)`, `pipe_length(system?, size?)` (returns lower-bound flag + missing edges), `connected(a, b)` (networkx path, returns the edge ids for highlighting), `line_route(tag)`. All read only the pipe JSON and refuse to claim absence where coverage is not `analysed`.

---

## What to cut if behind (in this order)

1. Step 13 (IFC MEP).
2. Step 9 (pipe-box tracer): ship the skeleton tracer, pitch the box tracer as the next step with Gao's numbers.
3. Flow direction and colour-based systems (step 10.6, 10.7).
4. Second dataset adapter (step 6, optional second source).
5. Legend matching (step 11.2): keep unknown groups.
6. DXF counterparts in step 2.6 (test the DXF route on one hand-made DXF instead).

Never cut: dataset ingest (step 2) and eval (step 3), because without them nothing can be measured; the DXF route (step 4), because it is exact, cheap and tests `graph.py`; the "crossing without dot = not connected" and "unsnapped = flagged" rules, because they are what makes the answers trustworthy.

## Open questions for the team

- Does the full dataset contain pipe sheets and pipe questions? If not, keep this a demo feature and stop at the MVP.
- Which symbol conventions do the real sheets use (Finnish LVI, Danish VVS, US plumbing)? Compare them with the dataset's classes (step 2.8) once we see 3 real sheets.
- Linking pipe sheets to floor plans (`overlay_sheet` with a registered transform) is not in this plan; `same_page` links are cheap and included. Add registration only if a real dataset has separate pipe and architectural sheets per floor.
