# Hand-off for a fresh session (vision translator, written 2026-11-14)

Read first: CLAUDE.md, docs/vision_translator_plan.md, docs/TODO.md (full history per step).

## 1. Git state
- Branch `step-7-ocr`. The last commit before this file is `5b5d99a` ("TODO: step 7 decisions applied…").
- Branch chain: `step-5-raster` (from main) → `step-6-cubicasa` (7 commits) → `step-7-ocr`
  (24 commits before this file). Merge order: 5, then 6, then 7.
- `schema-update` (4 commits, from origin/main) adds 5 object categories to the schema (additive, stays 0.2).
  It is not merged and not in step-7-ocr. `translator.__version__` stays 0.2.0 (k's decision).
- Local `main` is 3 commits behind `origin/main` (teammate: agent model, .gitignore). Not touched.
- Staged on purpose, never commit it unless k says so: `docs/overlay_9385_crop.png`.
  Commit only with explicit paths (`git commit -- <paths>`).
- `index/` is git-ignored: outputs, raw GPU files (`index/_raw/cubicasa`, `index/_raw/ocr`), debug overlays.

## 2. Done: steps 1-7, latest dev scores (split dev, `eval/score.py`)
Step 1 eval harness; 2 router; 3 Swiss DXF; 4 IFC; 5 raster preprocessing; 6 CubiCasa5K segmentation on
Modal; 7 OCR + glossary room naming. Steps 8-12 are not started.

| CubiCasa template | dev % | majority baseline | | CubiCasa template | dev % | baseline |
|---|---|---|---|---|---|---|
| floors | 96.9 | 80 | | toilets | 76.0 | 63 |
| kitchens | 87.7 | 95 | | sinks | 60.9 | 56 |
| balconies | 84.2 | 57 | | doors | 47.1 | 14 |
| WC rooms | 76.6 | 61 | | windows | 31.1 | 14 |
| bathrooms | 66.0 | 88 | | showers + baths | 9.1 | 73 |
| saunas | 59.4 | 55 | | | | |
| bedrooms | 58.1 | 28 | | | | |

- **Totals:** CubiCasa all 60.2% (2078/3452). Swiss 99.5% (1882/1892). ifc_models 100% (1025/1025).
  ~TOTAL 78.0% (4985/6388).
- **Not scored:** opening_design has no template (19 questions); ifc_bench is 201 free-text questions.
- **Dev OCR run:** 193 images, 0 failures, 259 s GPU; the rotated pass added 99 s on 128 images. Segmentation:
  193 images, 123 s GPU.

## 3. Decisions already made (with k), and why
- **Count rooms by label (label_priority on):** unlabelled segmentation regions step back when a labelled room
  of the same type exists. It fixed kitchen over-counting from fragments (kitchens 46.9 → 63.8).
- **Room merging off (`merge=False`):** merging same-class regions within 4 px changed no kitchen count and
  cost bedrooms 2-4 points.
- **Kitchen fallback on:** with no kitchen label read, the segmentation Kitchen class counts once (the largest
  region). Kitchens 63.8 → 72.3.
- **TK = entry (tuulikaappi), not kitchen;** TUPAK/TPK/tupakeittiö = kitchen. On dev plans TK is the
  vestibule. Kitchens 72.3 → 83.1.
- **Apartment summaries are not rooms:** a '+'-joined text whose first part is a room count (H, 1H, 2 H,
  1-2H) gets role unit_label. Kitchens → 86.9, saunas −0.8. Real combined labels (OH+RT+K, KHH+PH) stay.
- **Stray single-letter K:** outside every room, it makes no second kitchen (2 dev images).
- **Bedrooms:** labelled bedrooms plus Bedroom regions that no label covers (55.6 → 58.1).
- **floors_rule="max":** take whichever of storey titles and building blobs counts more storeys, because OCR
  misses titles far more often than it invents them (93.3 → 96.9). Coverage 'floors' stays partial,
  confidence 0.3, when the two disagree.
- **Rotated OCR pass off by default** (`rotation=False`): it finds about 16% more labels, but bedrooms −3.3
  and kitchens −0.8. The raw rotated readings are already cached for the 128 triggered images.
- **H = huone = bedroom label;** trailing numbers are stripped only after 2+ letters (H39, K115 are not rooms).
- **Mirrored plans:** the reading with more glossary hits wins (flipped also needs +0.05 mean score on a tie).
  Recorded in drawing.transforms as {"kind": "other", "note": "mirrored"}.
- **Schema `is_void` was dropped** in step 4: use room type "shaft".

## 4. Open items, in priority order
1. **Kitchens 87.7 < 95 (16 images left):**
   - 7 have no kitchen evidence at all (no Kitchen region, single-letter "K" not detected).
   - 3 have two real kitchens, and the fallback keeps one.
   - 3 have a second kitchen label inside a room (`Kök` + `kk`, `k/pH`, a 2nd `K` in the entry).
   - 2 have false Kitchen regions; 1 has the misread summary "JH+KK".
2. **Bathrooms 66.0 < 88:** not analysed yet.
3. **Doors and windows:** are untuned (icon size N=16).
4. **Bedrooms regression:** 66.9 in step 6 → 58.1 now. Cause not analysed yet. Likely contributors: H/MH
   labels on non-bedroom regions, label-only rooms.
5. **Mirrored-image margin rule:** 18 dev images are read flipped. Only 6 are clear (colorful-13685,
   colorful-7513, high_quality-11377, high_quality-8781, high_quality-8033,
   high_quality_architectural-10196). The other 12 win by 1-2 hits on mirror-symmetric strings (H, OH, S)
   and are probably not mirrored (unverified, not checked by eye). A mirrored "OH" reads "HO".
6. **Rotated pass:** it may stop hurting once items 1 and 3 are fixed. Re-score with `rotation=True`.
7. **Showers + baths 9.1 vs 73:** the model has no shower class. Coverage says shower not_analysed.
   Planned for the step 9 VLM check.
8. **Smaller items:**
  - Fuzzy OCR matching (PARVERE) is not done.
   - The kitchen "K" is often not detected (single letters).
   - Saunas lost 0.8 to the summary rule.
   
   - Walls are not written to walls[].

## 5. Environment
- **Local:** Python 3.15.2 (no Paddle wheel exists for it, so OCR runs on Modal); Modal client 1.6.4; ruff.
  `modal` is not on PATH: always use `python -m modal ...`.
- **Data:** `STOA_DATA` = the dataset root that holds `questions.jsonl` and `data/` (not `data/` itself;
  pointing at `data/` makes the dataset tests skip).
- **Modal:** app `stoa-translator`, T4, scaledown_window 120 s, Volume `cubicasa-weights` mounted at `/w`
  (`/w/model_best_val_loss_var.pkl`). There are three entrypoints; each must be named:
  - `::main`: CubiCasa5K segmentation → `index/_raw/cubicasa/<name>.seg.json`.
  - `::ocr_main`: OCR as drawn and flipped → `index/_raw/ocr/<name>.ocr.json`.
  - `::ocr_rot_main`: rotated pass for triggered images (adds `orientations.rotated` to the OCR raw file).

  All of them skip images that already have a raw file (use `--overwrite`) and refuse hold-out images.
- **Segmentation image:**
  - Python 3.11 with torch 2.2.2+cu121, torchvision 0.17.2, numpy 1.26.4, opencv-python-headless 4.10.0.84,
    scipy 1.13.1, shapely 2.0.6.
  - CubiCasa5k repo at commit c34440266665a11f4484eb06cd2e4b7d72ad76c1.
  - Modal's builder rejects Python 3.9/3.10; the fallback variant is `STOA_CC_TORCH=2.0`.
- **OCR image:**
  - Python 3.11.18 with paddlepaddle-gpu 3.0.0 (CUDA 11.8, cuDNN 8.9.7) from
    https://www.paddlepaddle.org.cn/packages/stable/cu118/ (PyPI has only 2.6.2 for Python 3.11).
  - paddleocr 3.0.3 installed `--no-deps`, plus paddlex[ocr] 3.0.3, opencv-contrib-python 4.10.0.84,
    numpy 2.4.1.
  - Models: PP-OCRv5_server_det, latin_PP-OCRv3_mobile_rec (keeps ä/ö/å), PP-LCNet_x1_0_textline_ori.
  - Gotchas: paddleocr's own dependency list pulls langchain without `docstore`; `lang="fi"` is not in
    PaddleOCR 3.0.3's list; the model download must run on a GPU at build time (libcuda).
- **Settings, in code:**
  - `translator.extract.ocr.fuse(merge=False, label_priority=True, kitchen_fallback=True, rotation=False,
    floors_rule="max")`.
  - `cubicasa.MIN_ICON_PX=16`, `MIN_ROOM_PX=200`.

## 6. Rules
- **Schema:** v0.2 (`translator/schema/drawing.schema.json`) is the contract with the agent teammate.
  Never change it without asking k. Every write is validated with jsonschema and fails loudly.
- **Hold-out:** never open, score, compare, OCR or send to the GPU any file in `eval/holdout_files.txt`.
  Dev split only; never run `eval/score.py --split holdout` or `--split all`. Tune on dev only.
  - One approved exception: high_quality_architectural-5006 (one-off raster test). Its outputs sit in
    `index/debug_jpg_test/` and are never scored.
- **Scoring:** the default `eval/score.py` loads every JSON under `index/`, including `index/debug_*`.
  Score from a clean index directory (see below).
- **Git:** do not merge or push unless k asks. Small commits, explicit paths, branch per step.
- **Checks before every commit:** `pytest -q`, `ruff check translator tests`,
  `ruff format --check translator tests`. The answering_agent ruff errors are known and not ours to fix.
- **Process:** show k failures before tuning. Report drops, report every drop, and keep a change only if the net gain is clear and each drop has an explained cause.
- **Next steps:** do not start step 8 or 9 without k's go-ahead.

## 7. Reproduce
Dev score (rewrite CubiCasa documents from the cached raw files, then score from a clean copy), PowerShell:
```
$env:STOA_DATA = "<dataset root>"; $env:PYTHONIOENCODING = "utf-8"
python -m translator.extract.cubicasa          # 193 dev documents -> index/cubicasa5k/
$C = "$env:TEMP\stoa_idx"; Remove-Item -Recurse -Force $C -ErrorAction SilentlyContinue; New-Item -ItemType Directory $C | Out-Null
foreach ($d in "cubicasa5k","swiss_dwellings","ifc_models","ifc_bench") { Copy-Item -Recurse "index\$d" "$C\$d" }
python eval/score.py --split dev --index $C     # writes eval/failures.jsonl
```
Swiss and IFC documents come from `python -m translator <paths> --write`; they are already in `index/`.

Last OCR smoke test (Paddle on the T4, high_quality-5815; needs `--overwrite` because the raw file exists):
```
python -m modal run -m translator.modal_app::ocr_main --names high_quality-5815 --overwrite
```
Expected: "PaddleOCR loaded: … 'place': 'gpu:0', 'gpu': 'Tesla T4'", 6 texts read as drawn (OH, VH, KH, ET,
H, H). The segmentation smoke test is the same with `::main`.
