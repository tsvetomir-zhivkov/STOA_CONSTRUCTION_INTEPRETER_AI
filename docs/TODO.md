# TODO

Open items found while building, grouped by the plan step that should handle them.

## Step 4 (IFC extractor)

- **kit-fzk-haus has no sibling IFC.** `data/ifc_models/kit-fzk-haus_ac20-fzk-haus_plan-02-dachgeschoss.pdf`
  (24 questions, all in the hold-out split) has no `kit-fzk-haus*.ifc` in `ifc_models/`, so the router
  sends it to its sibling **svg** (`..._plan-02-dachgeschoss.svg`: 56 shapes, 8 texts), not to ifc.
  It is listed as a route mismatch by `python -m translator.router --all`; it is not special-cased.
  - The rendered title is `FZK-Haus (Simple Phantasy Building) — Grundriss: Dachgeschoss`.
  - Candidate model: `data/ifc_bench/ac20_arc.ifc` (an AC20 model with a `Dachgeschoss` storey).
    It is in another folder, so the router does not treat it as a sibling.
  - Decide in step 4: (a) verify that ac20_arc.ifc really is the same building (match IfcSpace
    GUIDs against the SVG's `data-guid` attributes) and add an explicit, documented cross-folder link;
    or (b) extract this plan from the SVG (IfcSpace groups carry `data-name` / `data-guid`, labels are
    `<text>`). The questions ask for walls, slabs, room areas and counts on `Dachgeschoss`, which the
    SVG alone cannot answer for volumes/lengths, so (a) is preferred if the GUIDs match.
  - **Checked 2026-11-14 (step 4): NOT the same building, do not link.** Both files are in the hold-out
    split; k approved one read-only look at the two files, comparing storey names and room labels only.
    No questions or answers of either file were read or scored, and nothing was tuned on them.
    - `ac20_arc.ifc`: project "Projekt Buerogebaeude", building "Buerogebaeude" (an office building).
      Storeys Keller, Erdgeschoss, 1. Obergeschoss, 2. Obergeschoss, Dachgeschoss (elev. 9.0).
      Dachgeschoss spaces: Dachboden-1 (402), Dachboden-2 (403), Flur 3.OG Treppe (401).
    - kit-fzk-haus plan: "FZK-Haus (Simple Phantasy Building) — Grundriss: Dachgeschoss", one room
      label "Galerie" / "7". Its footer says it was generated from `AC20-FZK-Haus.ifc`, a different KIT
      model that is not in the dataset.
    - Only the storey name "Dachgeschoss" is shared. So option (a) is out: the file keeps the svg route,
      and option (b) (an SVG extractor for IfcConvert plans) belongs to step 11.
- **ac20_arc.ifc load-only check (2026-11-14, approved by k): PASS.** Held-out file, so this was a load test
  only: `python -m translator data/ifc_bench/ac20_arc.ifc --write`. No questions were read, nothing was
  scored or compared, nothing was tuned. Result: exit 0, 5 storey documents (all schema-valid on write),
  4.43 MB in total, largest document 1.12 MB, 23 s uncached. The outputs sit in `index/ifc_bench/`
  (git-ignored); `eval/score.py` only scores them with `--split holdout` or `--split all`.

## Step 5 (raster preprocessing)

- **Done (2026-11-14), `translator/raster/preprocess.py`.** `preprocess(img)` / `preprocess_path(path)` give
  original, normalized (inverted when median grey < 100), ocr_gray (CLAHE 2.0, 8x8), binary (adaptive
  Gaussian, block 31, C 10) and ocr_upscaled (x2 when the longest side < 1500 px, OCR only), all on the
  original pixel grid. `Preprocessed.schema_transforms()` turns the transforms dict into valid
  `drawing.transforms` entries (kinds invert / upscale / resize / deskew).
- **Dev-split check (cubicasa5k, 193 images; the 48 held-out ones skipped unopened):** 193/193 processed,
  `pre.shape[:2] == orig.shape[:2]` for all, 0 failures, 0 inverted, 165 upscaled for OCR (longest side:
  min 431, median 884, max 4500 px), all 193 binaries have a white background. 10 side-by-sides
  (seed 5) in `index/debug_raster/` (git-ignored).
- For step 7 (OCR): some CubiCasa originals are **mirrored** (text reads backwards, e.g. `colorful-7513`:
  "RUOK" drawn as "KOUR"). Not touched here (no flips in step 5). OCR must try a mirrored copy and map
  boxes back (x' = width - 1 - x), recording the flip in drawing.transforms. Not yet counted how many.
- For step 7: no dev image has a dark background, so the invert path is only covered by synthetic tests.
- ~~Open: `translator/extract/raster/` stub~~ deleted in step 6 (raster code lives in `translator/raster/`).

## Step 6 (CubiCasa5K segmentation on Modal)

- **Done (2026-11-14).** `translator/modal_app.py` (app `stoa-translator`, class `Seg`, T4, scaledown 120 s)
  runs hg_furukawa_original on the step 5 normalized image with CubiCasa's 4-rotation test-time averaging;
  `translator/extract/cubicasa.py` turns the raw results into documents on the CPU.
  Commands: `python -m modal run -m translator.modal_app [--limit N] [--names a,b]` (dev images only, skips
  images with a raw file), then `python -m translator.extract.cubicasa [--min-icon-px N] [--min-room-px N]`.
- **Environment:** Python **3.11** + torch **2.2.2+cu121** load the weights (Modal's 2026.10 image builder no
  longer builds 3.10 or 3.9; agreed with k). Fallback image `STOA_CC_TORCH=2.0` = torch 2.0.1 (torch 1.13.1
  has no 3.11 wheels). CubiCasa repo pinned at c34440266665a11f4484eb06cd2e4b7d72ad76c1.
- **Dev run:** 193/193 images (the 48 held-out ones never read or sent), 0 failures, 123 s GPU time in
  `run()` (about $0.02 at the T4 list price plus start-up). 15 images above 2048 px ran at a recorded scale
  (0.51-0.88) and were mapped back. Raw files: `index/_raw/cubicasa/` (8.8 MB, git-ignored).
- **Dev score (default N = 16 px, nothing tuned):** windows 31.1%, doors 47.1% (baseline 14% each);
  toilets 76.0%, kitchens 74.6%, bedrooms 66.9%, wc_rooms 61.7%, sinks 60.9%, balconies 57.9%,
  bathrooms 50.0%, saunas 42.1%, showers+baths 9.1%, floors 0.0%.

Open issues (for steps 7-10):
- **Mirrored images** (e.g. `colorful-7513`): not handled. The schema has no `flipped` field, so nothing
  records it yet; segmentation is unaffected, OCR (step 7) is not. Proposal: OCR tries a mirrored copy and
  maps boxes back with x' = width - 1 - x, recorded as a drawing.transforms entry (kind "other").
- **Showers:** no model class; coverage says `shower` not_analysed. showers+baths counts bathtubs only (9.1%).
  Step 9 (VLM) or a label rule.
- **Floors:** no `levels[]` from segmentation, so 0%. Step 7 (storey titles) and/or building blobs.
- **Saunas / WC rooms / bathrooms:** the model has no sauna or WC room class (Bath covers both), so those
  templates need OCR labels (step 7: S/SAUNA, WC, KH/PH). A Sauna bench icon could also imply a sauna.
- **Room fragmentation:** room classes split into several components (kitchens 74.6% vs 95% baseline);
  merge per labelled room in step 7.
- Icon components are not split when merged (as agreed). N is untuned; tune it in step 10 on the dev split.
- Walls are kept in the raw files but not written to `walls[]` (coverage says not_analysed).
- Base branch: step-6-cubicasa starts from step-5-raster (main does not have step 5 yet); merge order 5, 6.

## Step 7 (OCR + glossary room naming), 2026-11-14, branch step-7-ocr

- **Base:** step-7-ocr starts from step-6-cubicasa (agreed with k: main has neither step 5 nor 6 yet).
  Merge order 5, 6, 7.
- **Modal app has two entrypoints:** `python -m modal run -m translator.modal_app::main` (segmentation) and
  `python -m modal run -m translator.modal_app::ocr_main` (OCR). Both take `--limit N`, `--names a,b`
  and `--overwrite`, and refuse held-out images.
- **Working OCR set (Modal T4, Python 3.11.18):**
  - Packages: paddlepaddle-gpu 3.0.0 (CUDA 11.8, cuDNN 8.9.7), from Paddle's index (`.../stable/cu118/`; PyPI only
    has 2.6.2 for Python 3.11), paddleocr 3.0.3 installed `--no-deps`, paddlex[ocr] 3.0.3,
    opencv-contrib-python 4.10.0.84, numpy 2.4.1. Paddle runs on `gpu:0` (Tesla T4).
  - Models: PP-OCRv5_server_det, latin_PP-OCRv3_mobile_rec (Latin script, keeps ä/ö/å) and
    PP-LCNet_x1_0_textline_ori.
  - Three install fixes: (1) paddleocr's own dependency list pulls langchain, and its current release has no
    `langchain.docstore`, so paddleocr is installed without its dependencies; (2) PaddleOCR 3.0.3 has no `fi`
    language entry, so the models are named directly; (3) the GPU build of Paddle cannot be imported without
    a GPU driver, so the model download runs on a T4 at build time.
- **Run:** 193/193 dev images, 0 failures, 259 s GPU in run() (~$0.043), both orientations per image.
  Raw output: `index/_raw/ocr/<name>.ocr.json`. No held-out image was read or sent.
- **Dev score (default: label priority on, merging off):**

  | template | step 6 | step 7 | majority baseline |
  |---|---|---|---|
  | kitchens | 74.6 | **63.8** | 95 |
  | bathrooms | 50.0 | 66.0 | 88 |
  | saunas | 42.1 | 60.2 | 55 |
  | WC rooms | 61.7 | 76.6 | 61 |
  | balconies | 57.9 | 84.2 | 57 |
  | bedrooms | 66.9 | 54.8 | 28 |
  | floors | 0.0 | 93.3 | 80 |

  CubiCasa overall 45.1 → 57.8, TOTAL 69.9 → 76.8. Swiss and IFC are unchanged.
- **Kitchen fix (task 5):**
  - Merging same-class regions within 4 px changed no kitchen count and cost bedrooms 2–4 points.
  - Counting labelled rooms instead of regions (label priority) moved kitchens from 46.9 to 63.8 and bathrooms
    from 51.1 to 66.0. So label priority is on and merging is off (it is still available: `--merge`).
  - Without OCR, building blobs alone give floors 96.9 (vs 93.3 with storey titles).
- **Mirrored:** 18 dev images are read flipped. 6 are clear (colorful-13685, colorful-7513,
  high_quality-11377, high_quality-8781, high_quality-8033, high_quality_architectural-10196). The other 12
  win by only 1–2 glossary hits, often on mirror-symmetric strings (H, OH, S), and are probably not
  mirrored. They have not been checked by eye. Also, a mirrored "OH" reads "HO" in the flipped copy.
- **Open, seen in the 20 worst kitchen failures (not tuned yet, waiting for k):**
  - TK is mapped to kitchen as agreed, but on these plans it is *tuulikaappi* (vestibule): 5 of 20.
  - Apartment-type summaries (`1H+KK+ALK+S`, `H+KK+S`, `H+TK+S`) are read as room labels: 4 of 20.
  - Unlabelled small segmentation Kitchen fragments count when no kitchen label was read: 9 of 20.
  - A second label of the same type in one region (`Kök` + `kk`), or a stray `K`, makes a second kitchen.
  - The kitchen "K" is often not detected at all; rotated labels (high_quality_architectural-4517) are misread.
  - OCR misreads like PARVERE are not fuzzy-matched.
- **Follow-up (2026-11-14, dev only):**
  - **Kitchen fallback, default ON:** when OCR reads no kitchen label, the segmentation Kitchen class counts
    as one kitchen (the largest region). Kitchens 63.8 → 72.3; no other template changes. CubiCasa overall
    58.5, TOTAL 77.1.
  - **Rotated OCR pass, default OFF:** a second reading of a copy turned 90° clockwise, merged per box.
    - It runs only where `needs_rotation()` triggers: 2+ tall boxes, or at least 40% of boxes below 0.7. That was
      128/193 dev images, 99 s extra GPU (~$0.016). `python -m modal run -m translator.modal_app::ocr_rot_main`.
    - Room labels per image went from 5.18 to 6.01 on average (4517: 1 → 6). Balconies +1.8, bathrooms +3.1,
      saunas +0.7, WC rooms +2.1.
    - But bedrooms −4.0 and kitchens −5.4, so it is not the default: the extra labels feed the known
      problems (TK, apartment summaries such as `2 H+K+S`, stray `K`/`H`), and label priority steps back
      unlabelled bedrooms when only some bedrooms are labelled.
  - **Floors:** titles never fix a wrong building count on dev. All 7 regressions have fewer distinct titles
    than buildings (a missed title, or the same title read twice). Rule 'max' (take whichever
    counts more storeys) scores 96.9 vs 93.3. It is an option only (`floors_rule="max"`), waiting for k.
- **Decisions applied (2026-11-14, agreed with k, dev only):**

  | step | kitchens | bedrooms | bathrooms | WC rooms | saunas | balconies | floors |
  |---|---|---|---|---|---|---|---|
  | before | 72.3 | 54.8 | 66.0 | 76.6 | 60.2 | 84.2 | 93.3 |
  | 1. floors rule 'max' default | 72.3 | 54.8 | 66.0 | 76.6 | 60.2 | 84.2 | 96.9 |
  | 2. TK = entry (tuulikaappi); TUPAK/TPK = kitchen | 83.1 | 54.8 | 66.0 | 76.6 | 60.2 | 84.2 | 96.9 |
  | 3. apartment summaries are unit_label | 86.9 | 55.6 | 66.0 | 76.6 | 59.4 | 84.2 | 96.9 |
  | 4. stray single-letter K outside rooms (2 images) | 87.7 | 55.6 | 66.0 | 76.6 | 59.4 | 84.2 | 96.9 |
  | 5. bedrooms: labels + uncovered Bedroom regions | 87.7 | 58.1 | 66.0 | 76.6 | 59.4 | 84.2 | 96.9 |
  | 6. + rotated pass (NOT kept) | 86.9 | 54.8 | 69.1 | 78.7 | 59.4 | 86.0 | 96.9 |

  - **Summary rule:** a '+'-joined text whose first part is a room count (H, 1H, 2 H, 1-2H) is an apartment
    description (role unit_label). A misread "JH+KK" is not caught. Saunas lose 0.8 because a summary's "S" was
    sometimes the only sauna evidence read.
  - **Rotated pass stays off:** bedrooms and kitchens drop.
  - **Result:** CubiCasa 60.2, TOTAL 78.0.
  - **16 kitchen failures left:**
    - 7 images: no kitchen evidence read at all (no Kitchen region and no K label).
    - 3 images: two real kitchens, but the fallback keeps one.
    - 3 images: a second kitchen label inside a room (`Kök` + `kk`, `k/pH`, a 2nd `K` on the entry region).
    - 2 images: a false segmentation Kitchen region.
    - 1 image: the misread summary "JH+KK".

## Icon size threshold (MIN_ICON_PX), 2026-11-14, branch step-7-ocr, dev only
- **Applied (k's decision): MIN_ICON_PX = 46.** Dev, from Step 8 with priors on: windows 31.1 → 35.8,
  doors 47.1 → 51.4, toilets 76.0 → 77.6, sinks 60.9 → 67.7, showers+baths 9.1 → 9.1. No template drops:
  the 47 px bathtub speck (hqa-7838) that cost 2 questions at 48 survives at 46. CubiCasa 61.7 → 63.5,
  TOTAL 78.9 → 79.8.

Sweep below (before the decision):
Sweep on the cached raw files (`index/_raw/cubicasa`, no GPU), scored with `eval/score.py` logic from a clean
index copy. Each image has an EN and an FI question, so 2 questions = 1 image. Default stays 16 until k decides.

| min icon area (px) | windows | doors | toilets | sinks | showers+baths |
|---|---|---|---|---|---|
| 4 | 25.4 | 39.9 | 69.8 | 52.6 | 9.1 |
| 8 | 31.1 | 42.8 | 74.0 | 58.3 | 9.1 |
| 12 | 31.1 | 43.5 | 75.0 | 60.4 | 9.1 |
| **16 (current)** | 31.1 | 47.1 | 76.0 | 60.9 | 9.1 |
| 24 | 32.6 | 47.8 | 77.6 | 62.0 | 9.1 |
| 32 | 35.8 | 47.1 | 76.6 | 64.1 | 9.1 |
| 40 | 35.8 | 49.3 | 76.6 | 67.7 | 9.1 |
| 48 | 35.2 | 51.4 | 77.6 | 67.7 | 8.6 |
| 56 | 33.7 | 50.0 | 78.1 | 68.8 | 8.6 |
| 64 | 33.7 | 50.0 | 79.7 | 69.3 | 8.6 |
| 96 | 32.6 | 47.1 | 79.7 | 67.2 | 8.6 |
| 128 | 31.1 | 42.8 | 79.2 | 65.1 | 8.6 |
| 256 | 30.6 | 23.2 | 76.6 | 58.3 | 8.6 |

- **Plateau:** 40-56 for windows, doors and sinks; toilets keep rising to 64-96. 48 is the centre.
  Official runs (`--min-icon-px`): 40 → CubiCasa 61.7, TOTAL 78.9 (+52 q, no template drops);
  48 → CubiCasa 61.9, TOTAL 78.9 (+58 q; showers+baths −2 q = 1 image).
- **Images flipped 16 → 48:** windows +16/−8, doors +11/−5, toilets +6/−3, sinks +16/−3, showers+baths 0/−1.
  - Window losses were right by luck: the dropped 17-47 px component is a speck (wall corner, column mark),
    and it made up for a missed window.
  - Door losses (10498, 13210, 13607, 1772, 9198; L 739-1516) and sink losses (11866, 12943, 4855; L ≤ 530)
    are real small icons on small images. Bathtub loss 7838 is a speck in an empty room (luck).
- **Relative thresholds** do not beat a fixed one: longest bbox side ≥ 0.2-1.6% of L, area ≥ n·(L/884)²,
  n·(L/884), and capped hybrids n·min(1, L/884). Best windows 34.7, best doors 51.4 (= fixed 48). Only sinks
  (71.4) and toilets (81.2) are slightly better, and windows lose. Shrinking the threshold for small images
  brings their specks back too.
- **Maximum size** (≤ k × per-image median area of the class): k=3 doors −32 q; k=5/8/12 ±0-4 q. Not useful:
  big components are real large windows/double doors or merged groups that should count MORE.
- **Splitting merged components (report only):**
  - Distance-transform peaks fail: thin windows have a flat ridge. Exact images would go from 60 to 5 (windows)
    and from 65 to 3 (doors).
  - Length rule (longest side ≥ 1.8× image median → round(ratio)) fixes 5/65 under-counted window images but
    breaks 13 (net 60 → 52).
  - Merging is real (2580, 10321, 12077, 1967, 7924: rows of adjacent windows in one component). It needs
    another cue, e.g. OCR window-size labels (`10*22`, `IK 15x14`, `149x139`) printed next to each window.
- **Ceiling:** even a per-image oracle threshold makes only 99/193 window and 96/138 door images exact.
  Most errors are not size: 20 worst each, checked on overlays:
  - windows: merged 6, missed 6, false positive 6, fragmented 2;
  - doors: false-positive fragments 11, missed 4, open doorway/stair edge read as door 3, scope unclear 2.

## Step 8 (fusion, scale, coverage, priors), 2026-11-14, branch step-7-ocr, dev only
- **Pipeline:** `translate_all` → raster CubiCasa plans → `translator.fuse.fuse_raster`:
  `ocr.fuse` (settings unchanged: merge=False, label_priority, kitchen_fallback, rotation=False,
  floors_rule="max") → `dedupe_document` → `apply_priors` → `scale.estimate` → `coverage.build`.
  Every document is still validated in `translate_all` and again in `store.write_drawing`.
  New `fuse_params` argument (`priors`, `grid`); CLI `python -m translator.extract.cubicasa --no-priors`.
- **Duplicates:** same category with IoU > 0.5 → the lower-confidence element becomes superseded
  (`superseded_by`, flag `duplicate`); nothing is deleted. Categories: openings by kind, objects by
  category, rooms by type, texts by folded text. On dev: 0 duplicates, because segmentation components of
  one class never overlap. It matters once vlm_check adds elements (step 9).
- **Scale:**
  - Order: vector_units kept > scale note (only with a known dpi) > door width (median door long side =
    0.85 m, confidence 0.2) > none (confidence 0). Dimension lines are not implemented.
  - Dev: 190 door_width, 3 none. Scale notes were found as text in 3 images, but CubiCasa PNGs have no dpi.
  - No areas are computed from it.
- **Coverage:**
  - rooms, doors, windows, fixtures, text: a page record plus 16 cells (4x4 grid).
  - Confidence = mean confidence of active elements. A cell is partial when its mean is below 0.5.
  - An empty cell inherits the page status and confidence (note "no elements found").
  - floors, shower, walls, kitchens and bathrooms records are kept as written: floors stays partial at 0.3
    on disagreement, shower stays not_analysed. 90 records per CubiCasa document.
- **Priors** (kitchens and bathrooms only, approved by k):
  - Dev majorities live in `fuse.PRIORS`: kitchens 1 (93.8%), bathrooms 1 (87.2%).
  - Detected 0 → assume the majority. Equal → keep.
  - Different → keep only with ≥ 2 independent strong signals: labels (not TK) in distinct segmentation
    regions; label-only rooms and a second label in the same region do not count. Otherwise fall back:
    add assumed rooms, or step the weakest back to type unknown (flag prior_stepped_back).
  - Assumed room: polygon_px null, flags ["assumed_prior"], confidence 0.2, provenance prior /
    `prior:<template>`. Coverage record 'kitchens' / 'bathrooms' is partial, note
    "prior: assumed N, not read from the drawing ...".
- **Dev scores** (clean index copy, `eval/score.py --split dev`):

  | | kitchens | bathrooms | other CubiCasa templates | CubiCasa | TOTAL |
  |---|---|---|---|---|---|
  | before step 8 | 87.7 | 66.0 | — | 60.2 | 78.0 |
  | step 8, priors off | 87.7 | 66.0 | unchanged | 60.2 | 78.0 |
  | step 8, priors on (default) | 92.3 | 87.2 | unchanged | 61.7 | 78.9 |

  - Kitchens (130 images): the prior fired on 11.
    - Zero branch: 8 images, 6 gained, 1 lost (high_quality-698, true answer 0).
    - Fallback: 3 images, 2 gained, 1 lost (hqa-12077: 2 real kitchens, `kk` + `k` in the same region).
    - Kept on strong signals: 2 images, both wrong. hqa-12617 has the misread summary "JH+KK 3S'2 ws";
      hqa-6001 has a second "K" (OCR 0.38) on the entry region.
  - Bathrooms (94 images): the prior fired on 32.
    - Fallback: 23 images, 19 gained.
    - Zero branch: 9 images, 5 gained, 4 lost (true answer 0: hqa-1886, 3660, 5640, 9405).
    - Kept on strong signals: 2 images, 1 right (14341, Badrum ×2), 1 wrong (14494: `k/pH` shared label).
  - **Strong-signal rule tightened (k's decision, applied):** a label is a strong signal only when its OCR
    confidence (of the label text, not the room) is ≥ 0.5 and its text holds no '+'.
    - **This rule was found on two dev images only** (hqa-12617, misread summary "JH+KK 3S'2 ws"; hqa-6001,
      a second "K" at OCR 0.38), so expect it to generalise less than the dev number suggests.
    - Dev, with MIN_ICON_PX 46: kitchens 92.3 → 93.8 (those 2 images fixed); every other template
      unchanged; CubiCasa 63.5 → 63.6, TOTAL 79.8 → 79.9.
    - 3 images change count: hqa-12617 and hqa-6001 kitchens 2 → 1, and colorful-11400 bathrooms 2 → 1.
      The last has no bathroom question on dev, but it is a side effect: `KHH+PH` is a real combined label
      (utility + washroom), and the '+' rule no longer counts it as strong.

## Priors (read this before quoting kitchen or bathroom numbers)
- For kitchens and bathrooms, the CubiCasa output is **mostly a prior, not a reading**. On dev the prior
  decides the count on 13/130 kitchen images and 32/94 bathroom images; elsewhere the detected count already
  equals the prior (1). Both majorities are 1, so the honest baseline is "always answer 1": 93.8% kitchens,
  87.2% bathrooms. Our dev scores (93.8 and 87.2) equal it: the pipeline adds no measurable accuracy on
  these two templates yet.
- It is flagged in the JSON. An assumed room has `flags: ["assumed_prior"]`, `polygon_px: null`,
  confidence 0.2, provenance `prior` / `prior:<template>`. A room the prior overrode becomes type
  `unknown` with flag `prior_stepped_back` (details.prior_type). The coverage record `kitchens` or
  `bathrooms` is `partial`, with a note starting "prior: assumed N, not read from the drawing". The agent
  must never highlight an assumed room as evidence. Checked on all 193 dev documents (82 fired records).
- **Expect lower hold-out accuracy than dev:** the majorities and the strong-signal rule (found on 2 dev
  images) were chosen on dev, and the hold-out answer distribution may differ.
- Report with the priors off and on side by side: `python eval/priors_report.py` (dev only, no GPU).

  | template | priors off | priors on | prior fired |
  |---|---|---|---|
  | kitchens | 87.7 | 93.8 | 13/130 images |
  | bathrooms | 66.0 | 87.2 | 32/94 images |

## Step 9 (VLM checker), 2026-11-14, branch step-7-ocr, dev only, IN PROGRESS (stage 1 of 2 done)
- **Interface** (`translator/vlm/`): `VLMClient.check(image_bytes, category, prompt, *, source, crop)`.
  - Every client inherits `BaseClient.check`, which runs in order: hold-out guard, cache, budget, call,
    and scrubs the key from any error.
  - Clients by env `STOA_VLM`: `gemini` (default, REST via urllib, no SDK; key in the `x-goog-api-key`
    header only), `fake` (tests), `qwen` (stub).
  - Only `translator/vlm/gemini.py` talks to the Gemini API. `answering_agent/` (teammate) uses the
    google-genai SDK on its own and is left alone.
- **Hold-out guard:** names only (normalised path, basename, stem; case-insensitive), checked before any
  file is read.
  - The first version also hashed the hold-out files' bytes. It ran in the two step-a smoke processes,
    in memory only, and no hash was written anywhere. It has been removed.
- **Model:** `gemini-3.8-flash`, stable (aistudio.google.com/docs/models, model page "Latest update:
  September 2026"); 2.5 Flash is deprecated. Thinking level `low` ("minimal" is not supported by this model).
- **Prices** (config.PRICES_USD_PER_MTOK, aistudio.google.com/docs/pricing, read 2026-11-14): $0.75 per 1M
  input tokens, $3.75 per 1M output tokens incl. thinking, through Dec 31 2026 (both double from Jan 1
  2027). Image token cost is not documented on the pages read; measured about 1,200–1,300 input tokens
  per image.
- **Cache:** `index/_raw/vlm/<key>.json`, key = image hash + category + prompt + model + settings. Never
  sent twice. The run is resumable.
- **Budget:** the default cap is $1.00. Hard max $1.50 without k's approval. Stage 1 ran at $1.30 (k).
- **Timeout:** 300 s, one retry with backoff, latency logged. One step-a call timed out at 120 s and may
  have been billed: recorded in run_summary.json as an estimate of $0.025.
- **VLM elements:** extractor `vlm_check`, source_ref `vlm:<category>`,
  `properties.box_quality = "approximate"`. Confidence 0.6 is a FIXED value, not calibrated.
- **Write-back:** OFF by default for every category (`config.WRITEBACK_ENABLED`). k decides per category
  after the dev scores.
- **Shower variant:** A (one downscaled whole page, one call), chosen on 3 images.
  - B (wet-room crops) stays in code, off. Both were 3/3; A was cheaper.
  - Low vs default thinking on the same 3 images: both 3/3; $0.0030 vs $0.0091 per image.
- **Step b** (5 images, 11 calls, $0.053): 7/11 right. Overlays are in `index/debug_vlm/` (not committed).
- **Stage 1 — shower gate** (183 of 193 dev images = 94.8% gate rate; 175 new calls, $0.813,
  19:11–19:50 UTC). Dev, clean index copy, write-back on for showers only:

  | template | VLM off | VLM shower write-back on |
  |---|---|---|
  | showers + baths | 9.1 (34/372) | **68.8** (256/372) |
  | every other template | — | unchanged (no drops) |
  | CubiCasa / TOTAL | 63.6 / 79.9 | 70.0 / 83.4 |

  - **Honest baseline:** "always answer 1" scores 72.0% on this template, so 68.8% is still below it.
  - **Per image** (186 with gold): right 17 → 128. Of the 58 wrong, 41 are under-counts and 17 over-counts.
    Long side < 1000 px: 80/117 right; ≥ 1000 px: 48/69. Multi-floor pages: 21/38.
  - **Not gated:** 8 images have no wet region in the segmentation, so the VLM never saw them (4 are in
    the worst 20).
  - **Causes seen on the overlays of the worst 20:**
    - missed small or faint symbols on small or noisy scans (colorful-34 507 px, hqa-5602, 4561, 9488,
      6299, 12085 upside-down, 13048);
    - shower rooms of NEIGHBOURING flats at the page edge counted (hqa-11866, 11280, 5742, 13767);
    - one wet room counted twice (hqa-7549);
    - false positives on noisy or annotated scans (hqa-10254, 10000);
    - a multi-unit page with 5 items, only 1 found (hqa-7098).
  - **Cost:** $0.0047 per gated image, $0.0044 per dev image (target < $0.002: not met). Thinking is
    956 tokens on average (max 6,578).
  - **Spend so far:** $0.943 logged + $0.025 estimated = $0.968 (run_summary.json).
- **Saved for later (not run):** step-e candidate: pages with a long side under about 1000 px are upscaled
  to 1280 px before sending (`config.UPSCALE_BELOW_PX`, off). Prompt candidates suggested by the failures:
  "count only the apartment in the centre / not cut-off neighbours". Not applied (no tuning before k decides).

### Step 9, after stage 1 (k's decisions applied, 2026-11-14)
- **Shower write-back: ON by default.** Always report it next to the always-1 baseline (72.0%).
- **Shower fallback prior** (`config.SHOWER_PRIOR`; rule from the stage-1 failure list; no API cost).
  - It fires when no shower/bathtub is active and either the VLM said 0 on a page with a bathroom (label
    or segmentation), or the page has no wet room AND no VLM answer.
  - It adds 1 object: shower / shower_or_bathtub, flags ["assumed_prior"], confidence 0.2, no geometry,
    prior / prior:shower. The coverage record 'shower' is partial, "prior: assumed 1, not read from the
    drawing".
  - The "no VLM answer" condition was added after step e. Three never-gated pages that e1 answered with 0
    (gold 0) were still getting the prior; it now trusts the VLM answer (+6 questions).
  - On dev the prior fires on 20 images: 12 right; 4 wrong where the gold is 0 (regressions versus VLM-only);
    3 wrong where the gold is 2 (wrong before as well); 1 without a dev question.
- **Step e (the one allowed variant), e1:** pages with a long side < 1000 px upscaled to 1280 px, plus the
  prompt line "Count only the main apartment; ignore neighbouring flats cut off at the page edge."
  - Paired on a fixed-seed sample (seed 0): all 32 small gated pages that were wrong (only 32 exist, not 40),
    40 that were right, and the 8 never-gated pages. 80 calls, $0.393.
  - Result: 18 fixed, 4 broken (hqa-6001 2→1, hqa-11142 1→0, hqa-2145 1→0, high_quality-11377 1→0), net +14.
    Gated pages net +9; never-gated pages 5 fixed, 3 confirmed 0.
  - Acceptance rule, set before the run: net ≥ +5, broken ≤ half of fixed, and full-dev showers improves.
    Met: VLM-only 68.8 → 76.3; with the shower prior 75.8 → 79.0.
  - Accepted: `config.DEFAULT_VARIANT = {"shower": "e1"}`, and run step_e_variant is in
    `config.OUTCOME_RUNS`.
  - Caveat: the other ~103 shower pages still have the old prompt's answers. Re-running them with e1 would
    cost about $0.48, which is over budget. The dev number mixes both prompts, and the sample was drawn
    using dev labels, so expect less on hold-out.
- **Stage 2, reduced** (7 new calls, $0.015): no-evidence windows (2) / doors (1), and floors (7).
  - Kitchen/bathroom prior-fired tasks (82) are deferred.
  - Floors: all 7 were already right (rule "max"); the VLM agreed 7/7.
  - Windows: colorful-34 0 → 14 (gold 17, still wrong); high_quality-3086 0 confirmed (gold 0).
  - Doors: 1 image, no dev door question.
  - With write-back on, windows / doors / floors scores do not move. Their write-back stays OFF (k decides).
- **Final dev** (clean index copy; `python eval/priors_report.py` shows every column side by side):

  | template | pipeline | + count priors | + VLM (shower) | + shower prior | always-majority |
  |---|---|---|---|---|---|
  | showers + baths | 9.1 | 9.1 | 76.3 | **80.6** | 72.0 |
  | kitchens | 87.7 | 93.8 | 93.8 | 93.8 | 93.8 |
  | bathrooms | 66.0 | 87.2 | 87.2 | 87.2 | 87.2 |
  | windows / doors / floors | 35.8 / 51.4 / 96.9 | same | same | same | 15.0 / 14.5 / 80.3 |
  | other CubiCasa templates | unchanged | | | | |

  CubiCasa 63.6 → 71.3, TOTAL 79.9 → 84.0 (5369/6388). Swiss 99.5 and ifc_models 100 are unchanged.
  No template drops.
- **Cost (run_summary.json):**
  - Shower gate: 183 + 8 images. Cost per gated image ≈ $0.0047, per dev image ≈ $0.0044 (target
    < $0.002: not met).
  - Every run so far: $1.351 logged + $0.025 estimated = **$1.376** of the $1.50 total cap (k's final raise).
- **Deferred:** prior-fired kitchen/bathroom VLM tasks (82); e1 on the remaining shower pages; gate 5
  (windows/doors confidence).

## Step 10 (eval round and fixes), 2026-11-14, dev only, no API spend (VLM cache only, STOA_VLM=fake)
- **Diagnosis:**
  - No scored template is below its always-majority baseline. Kitchens (93.8) and bathrooms (87.2) only
    equal it.
  - Points still available: windows 248, doors 134, sinks 124, saunas 108, bedrooms 104, toilets 86 questions.
  - opening_design (19 questions) has no template; ifc_bench (201 free-text questions) is not scored.
- **Fix 1 — sauna fallback (principled; k approved).**
  - Rule: no sauna room read and a sauna bench icon (kept at the existing MIN_ICON_PX 46; threshold
    unchanged) → one sauna per page, the bench's shape, extractor cubicasa5k, flag `inferred_from_icon`.
    Coverage 'saunas' is partial, "inferred from sauna bench icon".
  - It mirrors the kitchen fallback. Cause from the diagnosis: 44 of the 54 sauna failures had a bench but
    no S label read.
  - Dev: saunas 59.4 → **89.5** (+80 q; 43 images fixed, 3 broken). Every other template unchanged.
    CubiCasa 71.3 → 73.6, TOTAL 84.0 → 85.3. No drops.
