> Historical v0.2 implementation plan. The current contract is documented in [building-v0.3.md](building-v0.3.md); page/storey persistence below is superseded.

# Vision translator: step-by-step build plan

Owner: k. Written 2026-11-14 00:00 (UTC), about 40 hours left.
Goal: turn any drawing file into one schema JSON per page (rooms, walls, openings, fixtures, texts, scale, coverage, provenance), accurate enough that the answering agent can answer the dataset questions within the scoring rules (counts exact, area/length/volume within 2%).

Read with: `Stoa_proposal.md` (architecture), `schema/README.md` (schema v0.1), `pipeline_run/molio_kaelder/README.md` (a real run on a vector PDF).

---

## 0. What the data tells us (read this first, it sets the order)

The preview now ships `questions.jsonl` with answers: **7,990 questions over 320 files**. That is our dev set: we can measure every extractor against real answers from hour 2.

| Source | Questions | Type | Best input to read | Route |
|---|---|---|---|---|
| cubicasa5k | 4,270 (53%) | all **counts**: windows, doors, WC seats, sinks, showers+baths, floors, bedrooms, kitchens, bathrooms, WC rooms, saunas, balconies (EN + FI) | PNG only | raster models |
| swiss_dwellings | 2,358 (30%) | counts of labelled rooms, apartments, doors, windows, WCs, washbasins, baths, showers; room/apartment/total net areas (EN, DE, FR, IT) | **DXF sibling** (layered), PDF has written areas | vector |
| ifc_models | 1,049 (13%) | room net areas (EN, DA, ET), counts, a few volume/length | **IFC model** (PDFs are rendered from it) | IFC |
| ifc_bench | 249 (3%) | text questions about IFC models | IFC | IFC (agent-heavy) |
| opening_design | 64 | mixed | PDF sets | vector PDF |
| europeana, finna, habs, wikimedia | **0** | none in preview | JPG | VLM fallback, lowest priority |

Things checked by hand on 2026-11-13:
- **Swiss DXF is perfect input.** Layers `ROOM_OUTLINE`, `ROOM_TEXT`, `UNIT_OUTLINE`, `DOOR`, `WINDOW`, `FIXTURE`, `WALL`; units in metres (`$INSUNITS=6`). The PDF text layer has room name, number and written area (e.g. `Zimmer / 2.10 / 16.78 m²`), and the written area **equals the answer** (16.78). Apartment labels `Whg. A..D`.
- **IFC areas = footprint of `IfcSpace` geometry.** The esplanades model has no area quantity sets, but the 2D footprint of the space mesh gave 63.84 and 25.25 m², exactly the answers.
- The dataset README says "files with the same name and a different extension are the same drawing", and `ifc_models/<model>_<level>.pdf` is rendered from `<model>.ifc`. So the router should **look for a better sibling file first**.
- CubiCasa "always guess the most common answer" accuracy: kitchens 95%, bathrooms 88%, floors 80%, showers+baths 73%, WC seats 63%, WC rooms 61%, balconies 57%, sinks 56%, saunas 55%, bedrooms 28%, **windows 14%, doors 14%**. These are the numbers to beat; windows and doors are where the model work pays off.

**Priority order that follows:** (1) eval harness, (2) router, (3) Swiss DXF/PDF, (4) IFC, (5) CubiCasa raster pipeline on Modal, (6) VLM checker, (7) everything else. Steps 3 and 4 are cheap, exact and cover ~43% of questions; step 5 is the biggest block (53%) and the hardest.

---

## Time budget at a glance

| # | Step | Hours | Cumulative |
|---|---|---|---|
| 1 | Repo, schema v0.2 fields, eval harness | 2.5 | 2.5 |
| 2 | Router (sibling formats + vector check) | 1.5 | 4 |
| 3 | Swiss: DXF + PDF text extractor | 4 | 8 |
| 4 | IFC extractor (+ link rendered PDFs) | 4 | 12 |
| 5 | Raster preprocessing | 1.5 | 13.5 |
| 6 | CubiCasa5K segmentation on Modal | 5 | 18.5 |
| 7 | OCR + glossary room naming | 3 | 21.5 |
| 8 | Fusion: counts, scale, coverage, JSON | 3 | 24.5 |
| 9 | VLM checker for low-confidence items (Modal or Gemini) | 4 | 28.5 |
| 10 | Eval round + fixes on worst templates | 3 | 31.5 |
| 11 | Generic vector PDF + historical fallback | 2.5 | 34 |
| 12 | Freeze, batch-run everything, hand-off | 2 | 36 |
| | Buffer (sleep is not in here, plan it) | 4 | 40 |

Hand-off contract with the agent teammate: **schema JSON files in `index/<source>/<file>.json`** plus `translate(path) -> json` as a function and CLI. Agree on this in step 1, then you can work independently.

---

## Step 1. Repo, schema v0.2 fields, eval harness (2.5 h)

**Goal:** one repo layout, the schema fields the data needs, and a scorer that tells you per question template how good the JSON is, without waiting for the agent.

**Do:**
1. Layout:
   ```
   translator/
     router.py            # step 2
     extract/swiss_dxf.py, ifc.py, vector_pdf.py, raster/  # one module per extractor
     fuse.py, scale.py, coverage.py, glossary.py
     modal_app.py         # all GPU code
     schema/drawing.schema.json
   eval/
     probe.py             # template -> JSON lookup (see below)
     score.py
   index/                 # outputs, one JSON per page
   ```
2. Schema v0.2: add only what the questions need (from the Molio report; confirm with the agent teammate since they read it):
   - `rooms[].number` (e.g. `2.10`, `K08`), `rooms[].unit_id` (apartment), `rooms[].parent_room_id`, `rooms[].holes_px`, `rooms[].subtype` (free text), `rooms[].is_outdoor` (balcony, loggia, terrace).
   - top-level `units[]` (apartments: id, label, polygon) and `levels[]` (storey name, elevation) for multi-floor PNGs and IFC.
   - `provenance.source_ref` (DXF handle, PDF item index, IFC GlobalId).
   - fixture categories in `objects[]`: `toilet`, `sink`, `shower`, `bathtub`, `sauna_bench`, `stair`.
   - `*_m` volume fields stay empty unless IFC provides heights.
3. `eval/probe.py`: a **deterministic** answerer, one regex per question template (there are ~30 templates × 4 languages), that reads the JSON and returns a number. Example: "How many windows" → `count(openings where kind=window)`; "net floor area of room 2.10" → `rooms[number=2.10].area`. This is not the real agent; it isolates translator errors from agent errors.
4. `eval/score.py`: run the probe over `questions.jsonl`, score with the official rule (exact for counts, ±2% for measures), print accuracy **per source × template**, and dump failures to `eval/failures.jsonl` with the file path, expected, got.

**Libraries:** `jsonschema`, `pandas` (tables), `rich` (optional).
**Done when:** `python eval/score.py` runs on an empty index and prints 0% for every template without crashing.
**Quick test:** hand-write one Swiss JSON with 2 rooms, check that the two matching questions score as correct.

---

## Step 2. Router (1.5 h)

**Goal:** for any input path, pick the best available representation and the extractor.

**Order of preference** (stop at the first hit):
1. Sibling or parent **IFC** (`<model>.ifc` for `ifc_models/<model>_<level>.pdf`; same stem `.ifc`).
2. Sibling **DXF** (same stem).
3. **SVG** (same stem or input) with real paths and `<text>`.
4. **PDF with vector content:** `page.get_text()` returns words AND `len(page.get_drawings()) > 50` AND no image covers > 50% of the page. (Molio rule, worked.)
5. Otherwise **raster** (PNG/JPG, or scanned PDF rendered at 300 dpi).

The JSON always refers to the file the question asked about (`drawing.file`), and records `drawing.route` and `drawing.read_from` (the sibling actually parsed). Coordinates for highlighting stay in that page's pixel space (see the "map back" notes in steps 3 and 4).

Also classify the raster drawing type here with a cheap rule first (`cubicasa5k/` folder name, image is colour/greyscale, dark background check), and only call a VLM for unknown sources (step 11).

**Libraries:** `pymupdf` (`import pymupdf`), `pathlib`.
**Done when:** `router.py` prints a route for all 320 preview files and the counts look like: cubicasa → raster, swiss → dxf, ifc_models → ifc, opening_design → vector_pdf.
**Quick test:** `python -m translator.router data/ifc_models/bsc-molio_molio-with-uris_plan-02-klder.pdf` → `ifc (bsc-molio_molio-with-uris.ifc), level 'Kælder'`.

---

## Step 3. Swiss dwellings: DXF + PDF text (4 h, ~2,358 questions)

**Goal:** exact rooms, apartments, doors, windows, fixtures and areas for every Swiss floor.

**Do:**
1. `ezdxf` read modelspace. Group by layer:
   - `ROOM_OUTLINE` LWPOLYLINE → room polygons (metres). `ROOM_TEXT` TEXT → name, number, written area. Assign texts to rooms by point-in-polygon (Shapely).
   - `UNIT_OUTLINE` → `units[]`; assign rooms to units by containment; unit label from `Whg. A` / `Appt. A` / `App. A` texts.
   - `DOOR` (LWPOLYLINE leaf + ARC swing) → one door per leaf; check against the ARC count. `WINDOW` → one window per polyline, but **verify grouping**: 84 window polylines vs answer 28 windows on `site1000_bldg2082_floor9385`, so a window is probably 3 polylines. Cluster touching/overlapping polylines into one window.
   - `FIXTURE` LWPOLYLINE + CIRCLE → classify by size/shape: toilet (small polyline + circle/ellipse bowl), washbasin, bathtub (~1.7 × 0.75 m rectangle), shower (~0.9 × 0.9 m square, often with diagonal). Fit thresholds on 5 files against the answers, then check on the rest.
2. Areas: **use the written area from the PDF/DXF text** as the answer value (it matched exactly); compute the polygon area as a cross-check and set `check_this` if they differ > 2%. Room type for "rooms labelled X" questions is just the exact label string; keep the raw label.
3. Indoor vs outdoor: label list for balcony/loggia/terrace in DE/FR/IT/EN (`Balkon, Loggia, Terrasse, Balcon, Balcone, Terrazza...`) into `glossary.py`, sets `is_outdoor`. "Numbered indoor rooms" = rooms with a number and not outdoor.
4. Map back to the PDF page for highlighting: find the same room numbers in the PDF text layer (`page.get_text("words")`), fit an affine transform DXF metres → PDF points with least squares on those anchor points (3+ points), store pixels in the page render at a fixed dpi (say 150). Check residual < 2 px.

**Libraries:** `ezdxf`, `shapely`, `pymupdf`, `numpy`.
**Done when:** all 65 Swiss floors produce valid JSON; score ≥ 98% on Swiss area questions and ≥ 95% on Swiss count questions.
**Quick test:** `site1000_bldg2082_floor9385`: 4 apartments, 12 × Zimmer, 36 doors, 28 windows, 4 WC, 4 washbasins, 4 bathtubs, Zimmer total 184.93 m², all numbered indoor rooms 341.45 m².
**If behind:** skip the PDF affine mapping; highlight in a DXF render instead (`ezdxf.addons.drawing` to PNG) and store that PNG's pixels.

---

## Step 4. IFC extractor (4 h, ~1,300 questions incl. ifc_bench)

**Goal:** read the model directly: spaces with exact areas, storeys, doors, windows, walls, slabs.

**Do:**
1. `ifcopenshell.open`, then per `IfcBuildingStorey` (use `ifcopenshell.util.element.get_container`):
   - `IfcSpace` → room: `Name` (number, e.g. `3-1`) + `LongName` (e.g. `ELUTUBA+KÖÖK`). Questions quote them as `'LongName (Name)'`; store both and the combined string.
   - Area: first `get_psets(space, qtos_only=True)` `NetFloorArea`; if missing, footprint of the geometry: `ifcopenshell.geom` with `USE_WORLD_COORDS`, take triangles at the lowest z, `unary_union` in Shapely, `.area`. This gave exact answers on esplanades (63.84, 25.25).
   - Height: max z − min z of the space mesh → `height_m` (helps volume questions).
   - `IfcDoor`, `IfcWindow`, `IfcWall`/`IfcWallStandardCase`, `IfcSlab`, `IfcStair`: counts per storey; wall length from the axis representation or the longest side of the footprint; slab volume from `NetVolume` quantity or mesh volume (`trimesh`).
2. Write one JSON per storey (`levels[]`), with `source_ref = GlobalId` on every element.
3. Map to the rendered PDF/SVG of that storey (`<model>_<level>.pdf/.svg`): match space labels in the SVG/PDF text to IFC spaces, fit model XY → page affine as in step 3. Where there is no rendering, highlight on a plan you render yourself from the footprints (matplotlib, 1 h max).
4. Store `raw IFC property sets` you used in `provenance` so text questions (ifc_bench) can be answered by the agent from the same JSON; dump all psets of each element into an `objects[].properties` dict.

**Libraries:** `ifcopenshell` (pip wheel works), `shapely`, `numpy`, `trimesh`.
**Modal:** not needed, CPU only. Geometry for big models can take minutes: cache per model.
**Done when:** all 9 IFC files load; score ≥ 98% on ifc_models area questions.
**Quick test:** esplanades `KORIDOR (119)` = 63.84, `ELUTUBA+KÖÖK (3-1)` = 25.25.
**If behind:** skip walls/slabs (only 50 length/volume questions); skip the PDF mapping.

---

## Step 5. Raster preprocessing (1.5 h)

**Goal:** one clean input for each raster model, same pixel grid as the original.

**Do** (k's decision, keep as agreed):
1. Detect dark background: median grey < 100 → **invert** (not brighten).
2. Greyscale copy for OCR: CLAHE (`cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))`).
3. Thresholded copy for segmentation/contours: adaptive threshold (`cv2.adaptiveThreshold`, Gaussian, block 31, C 10).
4. Never crop, rotate or resize in place. If a model needs a resized input, record `scale_x, scale_y` (and any deskew angle) in `drawing.transforms` and map outputs back to original pixels before writing JSON.
5. Upscale small images (longest side < 1,500 px) ×2 for OCR only, record the factor.

**Libraries:** `opencv-python-headless`, `numpy`.
**Done when:** for 10 random CubiCasa PNGs, the three images align exactly (overlay check) and dark images come out as dark-lines-on-white.
**Quick test:** `assert pre.shape[:2] == orig.shape[:2]` for all 241.

---

## Step 6. CubiCasa5K segmentation on Modal (5 h, the core of 4,270 questions)

**Goal:** rooms, walls, openings and fixture icons for every CubiCasa PNG, as polygons with confidence.

**What the model gives:** room classes (Background, Outdoor, Wall, Kitchen, Living room, Bedroom, Bath, Entry, Railing, Storage, Garage, Undefined) and icon classes (Window, Door, Closet, Electrical appliance, Toilet, Sink, Sauna bench, Fireplace, Bathtub, Chimney), plus wall junction heatmaps. **There is no shower class**, so showers need the VLM (step 9) or a label rule.

**Do:**
1. Get the code and weights on your laptop (Google Drive link in the CubiCasa5k GitHub README, `model_best_val_loss_var.pkl`), then `modal volume put cubicasa-weights model_best_val_loss_var.pkl /`.
2. Modal app (sketch):
   ```python
   import modal

   app = modal.App("stoa-translator")
   weights = modal.Volume.from_name("cubicasa-weights")
   image = (
       modal.Image.debian_slim(python_version="3.10")
       .apt_install("git", "libgl1", "libglib2.0-0")
       .pip_install("torch==2.2.*", "torchvision", "opencv-python-headless", "numpy", "scipy", "shapely")
       .run_commands("git clone https://github.com/CubiCasa/CubiCasa5k /cc5k")
   )


   @app.cls(image=image, gpu="T4", volumes={"/w": weights}, scaledown_window=120)
   class Seg:
       @modal.enter()
       def load(
           self,
       ): ...  # build hg_furukawa_original(51 classes), load /w/model_best_val_loss_var.pkl, .eval().cuda()
       @modal.method()
       def run(self, png_bytes: bytes) -> dict:
           ...  # preprocess, multi-rotation test-time averaging (their eval does 4 rotations), return
           # room/icon class maps as RLE or polygons + per-pixel max prob
   ```
   Call with `Seg().run.map(list_of_bytes)` from a local `@app.local_entrypoint()` to batch all PNGs in parallel. Return polygons, not big arrays, to keep transfer small.
3. Post-process (local, CPU):
   - Icons: connected components per icon class → instances; drop components < N px (tune N); split merged ones with distance-transform peaks only if counts come out low.
   - Rooms: connected components per room class → room polygons (`cv2.findContours` + `approxPolyDP`); keep mean class probability as confidence.
   - Doors/windows → `openings[]`; toilets/sinks/bathtubs/sauna benches → `objects[]`.
4. Write the per-image raw output to `index/_raw/cubicasa/<name>.seg.json` so you never rerun the GPU while tuning post-processing.

**Modal notes:** T4 is plenty (the model is small). The whole preview (241 images) should be minutes, a few cents of GPU. Keep `scaledown_window` short so it does not sit idle. Pin library versions; their code is old (PyTorch 1.x era), so if `torch 2.2` breaks loading, use `torch==1.13.1` with `python_version="3.9"`.

**Done when:** every CubiCasa PNG has a raw seg file and a schema JSON; window count accuracy clearly beats 14% and door count beats 14% on the dev set.
**Quick test:** overlay icons on `high_quality-5815.png` and compare with `schema/example_overlay_high_quality-5815.png`.
**If behind:** keep the model only for windows, doors, toilets, sinks, bathtubs; get room counts from OCR labels (step 7), which answers bedrooms/kitchens/saunas/WC rooms/balconies.

---

## Step 7. OCR + glossary room naming (3 h)

**Goal:** read every label, map Finnish (and Swedish) abbreviations to room types, and attach labels to room polygons.

**Do:**
1. OCR: PaddleOCR with a Latin-script recognition model (`lang="fi"` or `"latin"`, PP-OCRv4/v5), not the English one: the English model dropped æ/ø/å on Molio, and Finnish needs ä/ö. Run on the CLAHE copy (step 5). Locally on CPU is fine for 241 images; if slow, put it in the same Modal app with `gpu="T4"` and `paddlepaddle-gpu`. Fallback that installs anywhere: `rapidocr_onnxruntime` (worked in the container, English only).
2. `glossary.py`: abbreviation → type, at least: `MH` bedroom, `OH` living, `K`/`KK`/`KEITTIÖ`/`TK` kitchen (incl. kitchenette `KK`, `AVOK`), `KH`/`KPH`/`PH`/`PESUH`/`SH` bathroom, `WC` WC room, `S`/`SAUNA` sauna, `ET`/`ETEINEN` entry, `VH` walk-in closet, `VAR` storage, `PARV`/`LASIT. PARV`/`PARVEKE` balcony, `TERASSI` terrace, `TUPA` living, `RT` dining. Also the Swedish forms (`SOV`, `KÖK`, `BAD`, `BALK`). Normalise OCR noise (`0`→`O`, missing dots).
3. Attach each label to the room polygon that contains its centre (step 6 polygons). If a label lands in no polygon, still create a room from the label point with low confidence: counts by label alone are often right even when the polygon is wrong.
4. Room type = glossary type of the label; if no label, the segmentation class with its confidence.
5. Floors: detect storey titles (`1. KRS`, `2. KERROS`, `KELLARI`, `ULLAKKO`, `1st floor`) and also count large disconnected building blobs in the wall mask; write `levels[]`. If the two disagree, mark low confidence for step 9.

**Done when:** bedroom, kitchen, bathroom, WC room, sauna, balcony templates all beat their majority baselines (28/95/88/61/55/57%).
**Quick test:** `high_quality-5815.png` gives the 7 Finnish labels in the v0.1 example.

---

## Step 8. Fusion, scale, coverage, write JSON (3 h)

**Goal:** one schema-valid JSON per page from whichever extractors ran, with confidence and coverage.

**Do:**
1. `fuse.py` merges extractor outputs into the schema. Duplicate detection by IoU > 0.5 (same category) keeps the higher-confidence one and marks the other `superseded`.
2. `scale.py`, in this priority order (as decided): vector units (DXF/IFC) > scale note (`1:50`, `1:100` + known dpi/page size) > dimension lines (OCR number + the line's pixel length) > door-width estimate (0.8–0.9 m), low confidence. Written areas only cross-check (`check_this` if > 10% off; 2% for vector sources). CubiCasa questions are all counts, so scale is not on the critical path for raster in this dataset: do not overspend here.
3. `coverage.py`: per category (`rooms, doors, windows, fixtures, text`) and region (whole page grid of 4×4 is enough), `analysed` + confidence = mean element confidence, or `partial` where the model's max-prob map is low. Mark `shower` as `not_analysed` for raster unless step 9 ran.
4. Validate with `jsonschema` on every write; fail loudly.

**Done when:** all 320 preview files produce valid JSON, and `eval/score.py` gives a full per-template table.

---

## Step 9. VLM checker for low-confidence items (4 h)

**Goal:** fix the counts the cheap pipeline is unsure about, without sending every image to a big model.

**When to call it** (gate, so cost stays low):
- per image, per count category, when the segmentation instance count and the OCR/label count disagree, or mean confidence < threshold, or the category is `shower` (no model class), or floors detection disagreed;
- send **crops**, not the whole page, when the question is local (a bathroom crop for showers/baths/WC seats); the whole downscaled page only for floors and balconies.

**Two options, pick by what you have credits for:**
- **Gemini Flash via API**: fastest to wire up, cheapest per call, no GPU. Ask for JSON: `{"count": int, "boxes": [[x0,y0,x1,y1], ...]}` with boxes in crop pixels; map boxes back to page pixels.
- **Qwen3-VL on Modal** (e.g. the 8B Instruct model with vLLM on an `L4`/`A10G`, or `A100` for larger): open weights, good for the "not obsolete" story. Put weights in a Modal Volume once; use `@modal.concurrent` / vLLM batching; set `scaledown_window` short.

**Write-back:** every VLM finding becomes a new element with `provenance.extractor = "vlm_check"`, its box, and a `history[]` entry with cost. Never delete pipeline elements; mark them `superseded`.

**Done when:** showers+baths, WC seats and sinks beat their baselines (73/63/56%) and the eval prints cost per image (target: < $0.002 average).
**Quick test:** run on the 20 worst CubiCasa images from `eval/failures.jsonl`, count how many flip to correct.
**If behind:** only use the VLM for showers and floors.

---

## Step 10. Eval round and fixes (3 h)

Run `eval/score.py` on everything. Sort templates by (questions affected × error rate). Typical fixes in order of payoff: icon component size threshold (windows/doors), window grouping (Swiss), label normalisation (Finnish OCR), outdoor rooms excluded from totals, doors with no leaf excluded (CubiCasa question says open doorways do not count). Record the final table: it goes straight into the pitch (accuracy per source and cost per question).

**Hold out** 20% of files (by file, not by question) and only look at them at the end, so the pitch number is honest.

---

## Step 11. Generic vector PDF and historical fallback (2.5 h, low priority)

1. **Vector PDF** (opening_design, any PDF without siblings): generalise `pipeline_run/molio_kaelder/scripts/vector_parse.py` (fill/stroke style classes, text layer, scale note). 64 questions in the preview, so 1.5 h max.
2. **Historical JPGs** (europeana, finna, habs, wikimedia): no questions in the preview. Do only a VLM full-page pass that returns rooms/labels with boxes into the schema, flagged `confidence: low`, so the demo can show one. 1 h max.

---

## Step 12. Freeze and hand off (2 h)

1. Batch-run the translator over every file the organisers give (full dataset if released), cache to `index/`.
2. `translate(path)` and `python -m translator <path>` work for the agent teammate; README with the route table and the eval table.
3. Make 3 overlay PNGs for the demo (one Swiss, one IFC plan, one CubiCasa) from the JSON, to show the highlighting.

---

## What to cut if behind (in this order)

1. Step 11 entirely (historical, generic vector PDF).
2. Wall length and slab volume from IFC (≈50 questions).
3. PDF affine mapping for Swiss/IFC (highlight on your own render instead).
4. Self-hosted Qwen3-VL: use Gemini Flash only.
5. Scale recovery beyond vector units and scale notes (raster questions are all counts).
6. CubiCasa room polygons: keep label-based room counts and icon counts only.

Never cut: the eval harness (step 1), the router (step 2), Swiss (step 3), IFC areas (step 4). They are cheap and nearly exact.

## Open questions for the team

- Schema v0.2 fields in step 1: agree with the agent teammate before hour 3.
- Will the organisers release the full dataset (14k files, ~120k questions) or only this preview? If full, budget a batch run on Modal CPU containers (`.map` over files) in step 12.
- Do we get API credits (Gemini or other)? Decides step 9's option.
