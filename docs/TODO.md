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
