# Hand-off: vision translator (updated 2026-11-14, after Step 10)

Single source of truth for a fresh session. `docs/HANDOFF_vision_translator.md` only points here.
Read first: `CLAUDE.md`, `docs/vision_translator_plan.md`, this file. Full history per step: `docs/TODO.md`.
Final numbers: `docs/EVAL_TABLE.md` (generated, see section 5).

## 1. Architecture and routes

One schema v0.2 JSON per page (`translator/schema/drawing.schema.json`, the contract with the agent teammate;
never change it without asking k). Every write is validated with jsonschema and fails loudly.

| route (router.py) | input | path to JSON |
|---|---|---|
| `ifc` | `<model>.ifc`, or `ifc_models/<model>_<level>.pdf` rendered from it | IFC extractor, one doc per storey; mapped onto the page via the SVG sibling |
| `dxf` | Swiss PDF with a same-stem `.dxf` | Swiss DXF extractor (layers ROOM_OUTLINE, ROOM_TEXT, UNIT_OUTLINE, DOOR, WINDOW, FIXTURE, WALL), affine-mapped to the PDF page |
| `vector_pdf` | PDF with words + > 50 drawings | **no extractor yet** (Step 11) |
| `raster` | PNG/JPG (CubiCasa) | see below |
| `none` | anything else | empty document, coverage not_analysed |

Vector routes (IFC, DXF) go straight to JSON. The raster route (CubiCasa) is a pipeline:
1. `translator/raster/preprocess.py`: invert dark backgrounds, CLAHE copy for OCR, adaptive-threshold copy;
   never crops/rotates/resizes in place; transforms recorded in `drawing.transforms`.
2. CubiCasa5K segmentation on Modal (`translator/modal_app.py`, GPU code lives only there) →
   `index/_raw/cubicasa/<name>.seg.json`; local post-processing `translator/extract/cubicasa.py`.
3. OCR on Modal (PaddleOCR 3.0, Latin model) → `index/_raw/ocr/<name>.ocr.json`; local naming with the glossary
   `translator/extract/ocr.py` + `translator/glossary.py`.
4. `translator/fuse.py::fuse_raster`: OCR fusion → sauna fallback → duplicates (IoU > 0.5 superseded) →
   kitchen/bathroom count priors → cached VLM answers + shower prior (`translator/vlm/run.py::apply_cached`,
   never calls the API) → scale (`scale.py`) → coverage, page + 4×4 grid (`coverage.py`).
5. Schema-valid JSON, `index/cubicasa5k/<name>.json`.

**VLM checker** (`translator/vlm/`, Step 9): `VLMClient.check(image_bytes, category, prompt, *, source, crop)`;
Gemini over REST (urllib, no SDK; key in the `x-goog-api-key` header), `FakeClient` for tests, `QwenClient`
stub. Hold-out guard (names only), cache, budget guard and key scrubbing run inside `BaseClient.check` for every
client. Runs are offline jobs (`python -m translator.vlm.run`); translation only reads the cache.

**Blueprint-or-noise gate: NOT BUILT. Owner: teammate.** Agreed interface: `is_blueprint(path) -> (bool,
reason)`, called at the start of `translate_all` for raster inputs; a rejected input returns route `"none"`
with a coverage note giving the reason.

## 2. How to run

- `translate(path) -> dict` (one document; an IFC model needs `part=`), `translate_all(path) -> list[dict]`
  (one per page / storey), both validated. Raster options: `cubicasa_params`, `ocr_params`, `fuse_params`
  (`priors`, `vlm`, `vlm_enabled`, `vlm_runs`, `shower_prior`, `grid`).
- CLI: `python -m translator <path> [...] [--write] [--source NAME] [--data ROOT]`. **Warning:** `--source`
  translates every questions.jsonl file of that source, hold-out files included (see the disclosure in 5).
- Rebuild the CubiCasa dev documents from cached raw files (dev only, no GPU, no API):
  `python -m translator.extract.cubicasa [--min-icon-px N] [--no-priors] [--index DIR]`.
- Environment:
  - `STOA_DATA` = the dataset root holding `questions.jsonl` and `data/` (not `data/` itself).
  - `STOA_VLM` = `gemini` (default) | `fake` (tests, and any run that must not call the API) | `qwen` (stub).
  - `GEMINI_API_KEY` from the environment only; never written to a file, log, cache or commit.
  - `PYTHONIOENCODING=utf-8` on Windows.
- Modal (client 1.6.4; not on PATH, always `python -m modal`; app `stoa-translator`, T4, scaledown 120 s,
  Volume `cubicasa-weights` at `/w`). Entrypoints must be named; all skip existing raw files (use
  `--overwrite`) and refuse hold-out images:
  - `python -m modal run -m translator.modal_app::main [--names a,b] [--limit N]` (segmentation)
  - `python -m modal run -m translator.modal_app::ocr_main [--names a,b]` (OCR as drawn + flipped)
  - `python -m modal run -m translator.modal_app::ocr_rot_main [--names a,b]` (rotated pass)
- VLM (costs money; total cap reached, see 6): `python -m translator.vlm.run [--images a,b] [--gates ...]
  [--categories ...] [--variant e1] [--dry-run] [--debug] --name RUN`; scoring copies with write-back:
  `--apply-to DIR --enable shower [--runs ...]`.
- **Scoring rule:** always score from a clean copy of the index (the default loads every JSON under `index/`,
  including `index/debug_*`), dev split only:
  ```
  $C = "$env:TEMP\stoa_idx"; foreach ($d in "cubicasa5k","swiss_dwellings","ifc_models","ifc_bench") { Copy-Item -Recurse "index\$d" "$C\$d" }
  python eval/score.py --split dev --index $C
  ```
  Full table with every configuration and the baseline: `python eval/priors_report.py [--write-md docs/EVAL_TABLE.md]`.
- Checks before every commit (with `STOA_DATA` set): `pytest -q`, `ruff check translator tests`,
  `ruff format --check translator tests`.

## 3. Decisions (with k) and why

Raster room counting (`translator/extract/ocr.py`, `translator/fuse.py`):
- **Label priority on:** rooms are counted by label; unlabelled segmentation regions of a labelled type step
  back (flag `unlabelled_region`). Fixed kitchen over-counting from fragments. Room merging off (cost bedrooms).
- **Bedrooms:** labelled bedrooms plus Bedroom regions no label covers.
- **H / huone are generic room labels, not bedroom** (Step 10; **reverses the Step-7 decision "H = bedroom
  label"**). A generic label names a region but keeps its segmentation type and is not counted as a labelled
  room; in no region it makes no room. Bedrooms 58.1 → 66.9, back to the segmentation-only score, not above it.
- **Kitchen fallback:** with no kitchen label read, the segmentation Kitchen class counts once (largest region).
- **TK = tuulikaappi (entry), not kitchen;** TUPAK / TPK / tupakeittiö = kitchen.
- **'+'-summaries are `unit_label`:** a '+'-joined text whose first part is a room count (H, 1H, 2 H, 1-2H)
  describes the apartment, not rooms. Real combined labels (OH+RT+K, KHH+PH) stay room labels.
- **Stray single-letter K** outside every room makes no second kitchen.
- **floors_rule = "max":** take whichever of storey titles and building blobs counts more (OCR misses titles
  more often than it invents them); coverage 'floors' stays partial at 0.3 when they disagree.
- **Rotated OCR pass off** (it found more labels but lowered bedrooms and kitchens); raw rotated readings cached.
- **Mirrored plans:** the reading (as drawn / flipped) with more glossary hits wins; a tie needs +0.05 mean
  score; recorded as `{"kind": "other", "note": "mirrored"}` in `drawing.transforms`.
- **Count priors (kitchens, bathrooms; dev majorities 1 and 1):** detected 0 → assume the majority; equal →
  keep; different → keep only with ≥ 2 independent strong signals, else fall back (assumed rooms added, or the
  weakest step back to type `unknown`, flag `prior_stepped_back`). Assumed element: `polygon_px` null,
  `flags: ["assumed_prior"]`, confidence 0.2, provenance `prior` / `prior:<template>`; coverage record
  `kitchens` / `bathrooms` `partial` with a note starting **"prior: assumed N, not read from the drawing"**.
- **Strong-signal rule:** a label in a distinct segmentation region (not label-only, not TK), OCR confidence
  ≥ 0.5, no '+' in the text. Found on 2 dev images (dev-fitted).
- **MIN_ICON_PX = 46** (k, from the dev sweep; plateau 40-56).
- **Sauna fallback** (Step 10, principled): no sauna room + a sauna bench icon → one sauna per page, the
  bench's shape, extractor `cubicasa5k`, flag `inferred_from_icon`; coverage `saunas` partial "inferred from
  sauna bench icon". Saunas 59.4 → 89.5.
- **Showers (VLM):** variant A (one downscaled whole page, one call); thinking level `low`; prompt variant
  **e1** (pages < 1000 px upscaled to 1280 px + "count only the main apartment; ignore neighbouring flats cut
  off at the page edge") is the default for new runs. Shower write-back **on**; every other category off.
- **Shower fallback prior:** no active shower/bathtub and either the VLM said 0 on a page with a bathroom, or the
  page has no wet room and no VLM answer → 1 assumed `shower_or_bathtub` (`assumed_prior`, confidence 0.2,
  `prior:shower`, coverage `shower` partial "prior: assumed 1, not read from the drawing").
- **VLM model:** `gemini-3.8-flash` (stable; 2.5 Flash deprecated); prices $0.75 / $3.75 per 1M tokens (thinking
  billed as output), through 2026-12-31, doubling from 2027-01-01 (`translator/vlm/config.py`).
- **Hold-out rules:** never open, score, OCR or send a file in `eval/holdout_files.txt`; dev only;
  never `--split holdout/all`. Disclosed exceptions in section 5.
- **.gitignore rebuilt** after the merge 3fb2b30 had cut it to 4 lines: union of ours and the teammate's
  (data/, questions.jsonl, .env, *.pkl, caches, index/* with `!index/.gitkeep`, .modal.toml, uv.lock,
  .python-version, root copies `/drawing.schema.json` and `/sample_drawing.json`); the teammate's ".end" was read
  as ".env"; 29 tracked .pyc files untracked. Nothing sensitive or > 5 MB was ever committed.

## 4. Flags the agent must handle

- `assumed_prior` (rooms, objects): a prior, not read from the drawing. No geometry. **Never highlight it as
  evidence**; say the count is assumed. Its coverage record is `partial` with a note "prior: assumed …".
- `prior_stepped_back`: a room the count prior overrode; type `unknown`, original type in
  `provenance.details.prior_type`.
- `inferred_from_icon`: a room inferred from an icon (sauna bench); geometry = the icon's shape.
- `vlm_check` elements (provenance.extractor `vlm_check`, source_ref `vlm:<category>`): boxes are approximate
  (`properties.box_quality: "approximate"`), confidence 0.6 is **fixed, not calibrated**; the VLM call is in
  `history[]` (`extractor: vlm_check`, `cost_usd`). Showers come as category `shower`, subtype
  `shower_or_bathtub` (the VLM does not say which). Other flags: `vlm_confirmed`, `vlm_rejected`,
  `vlm_stepped_back`, `vlm_no_box`.
- `status: "superseded"` (+ `superseded_by`): never count or highlight; it was replaced (duplicates, VLM).
- Also: `label_only`, `shares_region`, `unlabelled_region`, `kitchen_fragment`, `check_this`, `low_confidence`.
- Coverage: never claim absence unless the category/region is `analysed` with good confidence; `shower` is
  `not_analysed` where neither the VLM nor the prior ran.

## 5. Scores (dev split; from docs/EVAL_TABLE.md, `python eval/priors_report.py`)

**Hold-out accuracy is expected to be lower than these dev numbers.** Known weak spots: **windows, doors, sinks
and toilets** (segmentation limits: missed, merged and false-positive icons).

Totals: **5471/6388 = 85.6%** of all dev questions (opening_design's 19 questions have no template and count as
failures); **5471/6369 = 85.9%** of scoreable questions. Not scored: 201 ifc_bench + 4 opening_design free-text
questions. By source: CubiCasa 74.3, Swiss 99.5, ifc_models 100.0, opening_design 0.0.

| template | n | pipeline | + count priors | + VLM | final | always-majority | prior fired |
|---|---|---|---|---|---|---|---|
| cc_balconies | 114 | 84.2 | 84.2 | 84.2 | **84.2** | 57.9 | |
| cc_bathrooms | 188 | 67.0 | 87.2 | 87.2 | **87.2** | 87.2 | 31/94 |
| cc_bedrooms | 248 | 66.9 | 66.9 | 66.9 | **66.9** | 29.0 | |
| cc_doors | 276 | 51.4 | 51.4 | 51.4 | **51.4** | 14.5 | |
| cc_floors | 386 | 96.9 | 96.9 | 96.9 | **96.9** | 80.3 | |
| cc_kitchens | 260 | 89.2 | 93.8 | 93.8 | **93.8** | 93.8 | 11/130 |
| cc_saunas | 266 | 89.5 | 89.5 | 89.5 | **89.5** | 56.4 | |
| cc_showers_baths | 372 | 9.1 | 9.1 | 76.3 | **80.6** | 72.0 | 19/186 |
| cc_sinks | 384 | 67.7 | 67.7 | 67.7 | **67.7** | 55.2 | |
| cc_toilets | 384 | 77.6 | 77.6 | 77.6 | **77.6** | 62.5 | |
| cc_wc_rooms | 188 | 76.6 | 76.6 | 76.6 | **76.6** | 61.7 | |
| cc_windows | 386 | 35.8 | 35.8 | 35.8 | **35.8** | 15.0 | |
| Swiss (11 templates) | 1892 | | | | **99.5** | 1.9-43.8 | |
| ifc_models (8 templates) | 1025 | | | | **100.0** | 14.3-57.1 | |

Kitchens and bathrooms only **equal** their always-majority baseline (mostly the prior).

VLM cost: total spend **$1.376** ($1.351 logged + $0.025 estimated for a timed-out call) of the $1.50 cap;
answers applied in the index: 191 on 191 images, $0.905 → **$0.0047 per gated image, $0.0047 per dev image**
(target < $0.002: not met).

Disclosures (also in docs/EVAL_TABLE.md):
- Dev-fitted (will cost accuracy on hold-out): the count priors (dev majorities) and their strong-signal rule;
  the shower fallback prior; MIN_ICON_PX 46; the e1 sample was drawn using dev labels.
- The dev shower score mixes two prompts (80 pages e1, ~103 the original prompt; re-running was over budget).
- Principled fixes found while reading dev failures: sauna fallback; H / huone generic.
- high_quality_architectural-5006: a one-off raster test on a hold-out image (approved by k), outputs in
  `index/debug_jpg_test/`, never scored.
- Hold-out hash code: an early VLM guard read the hold-out files' bytes to hash them (in memory only, two
  smoke-test processes); no hash was written anywhere; removed; the guard now uses names only.
- **`index/` holds documents for 13 Swiss and 1 ifc_bench hold-out files**, produced by `python -m translator
  --source` in Steps 3-4 (`--source` does not filter the hold-out). They were never scored or tuned on;
  `eval/score.py` scores dev only. Found while writing this hand-off.
- Swiss: 10 dev questions fail on two floors (no written areas; a numbered room without a name is summed);
  cause known, not fixed (a rule would fit 2 floors).

## 6. Open items

- `schema-update` branch: 5 object categories (stove, fridge, washing_machine, elevator, radiator; additive,
  stays 0.2), 4 commits, **not merged** into step-7-ocr.
- Branches: `step-3-swiss` is fully contained in `step-7-ocr` (same as origin/step-3-swiss); decide which branch
  is handed off / merged (k). Local `main` is behind `origin/main`; there is also a `pipes` branch (teammate).
- Mirrored-image margin rule: 18 dev images read flipped, only 6 clearly mirrored (unverified by eye).
- Swiss two-floor area failures (cause known, not fixed).
- Walls are not written to `walls[]` for raster plans.
- Fuzzy OCR matching (e.g. PARVERE) not done; single-letter K often not detected.
- Generic vector PDF extractor (Step 11; mine, only if time); opening_design has no template (19 questions).
- Teammate: validate the DXF/IFC/PDF documents against schema v0.2 on their side.
- VLM: gate 5 (low-confidence windows/doors) and the 82 prior-fired kitchen/bathroom tasks are deferred; e1 on
  the remaining ~103 shower pages not run. **Remaining VLM budget ≈ $0.12** (cap $1.50 reached otherwise).
- Blueprint-or-noise gate (teammate, interface in section 1).
- `--source` in the CLI should skip hold-out files before the Step-12 batch run (or the batch run must decide
  explicitly that the deliverable index includes them).

## 7. Freeze checklist (Step 12)

- [ ] Batch-run the translator over every file the organisers give (decide hold-out handling first, see 6);
      cache to `index/`; VLM from cache only.
- [ ] README: the route table (section 1) and the eval table (`docs/EVAL_TABLE.md`), how to run.
- [ ] 3 overlay PNGs from the JSON for the demo: one Swiss, one IFC plan, one CubiCasa.
- [ ] Regenerate `docs/EVAL_TABLE.md`; checks pass; working tree clean.
- [ ] Tag the freeze commit (k decides the branch; do not push without k).
