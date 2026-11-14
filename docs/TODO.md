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
- Open: `translator/extract/raster/` (empty stub from step 1) now sits next to `translator/raster/`.
  Decide where segmentation post-processing goes in step 6 and delete the other.
