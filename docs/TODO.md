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
