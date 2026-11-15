# Blueprint Viewer

A single page that shows blueprint-ingest drawing JSON, and IFC models, as one interactive 3D scene. Plain HTML and ES modules, with no build step and no backend: files never leave the browser.

## How it works

Geometry comes from two sources, and both end up in one scene.

| Source | Geometry | Heights |
|---|---|---|
| `.ifc` | **Real 3D solids**, read in the browser with web-ifc 0.0.68 | Real: nothing vertical is assumed |
| drawing JSON (DXF, SVG, raster, or IFC without its `.ifc`) | Extruded from the polygons in the JSON | Conventions, marked *assumed* |

**The drawing JSON is always the semantic layer.** Types, confidence, flags, provenance and coverage come from it. A web-ifc mesh and a JSON element are linked by IFC GlobalId:

- The viewer matches on `provenance.source_ref`, which holds the raw GUID. It never matches on the element id, which is sanitised.
- When both are loaded, a matched element is drawn from the mesh and described from the JSON. Nothing is reconstructed from 2D that web-ifc can read in 3D.

**Frame.** Everything lives in the IFC world frame, in metres:

- A JSON's coordinates come from `polygon_px` through the inverse of `drawing.transforms[0]`, so files rendered at different pixels per metre still align. The clinic levels are 55, 45 and 51 px/m.
- A file without a transform falls back to `polygon_m`, with a warning that cross-file alignment is not guaranteed.
- web-ifc already returns **Y-up** geometry, so the IFC group is *not* rotated. A −90° rotation would lay the building on its side.
- The scene origin is fixed by the first file loaded. Georeferenced models (esplanades sits 538 km east and 6 591 km north) keep full precision, because every vertex is shifted in double precision before it becomes float32.

**Schema versions.** v0.2 and v0.3 files both load: v0.3 is a superset ([docs/schema-v0.3.md](../docs/schema-v0.3.md)).

- The GlobalId link uses `global_id` when the file has it, else `provenance.source_ref`.
- Elements without plan geometry (`polygon_px: null`, flag `no_plan_geometry`) are counted but not drawn.
- For a v0.3 file, the inspector also shows:
  - `ifc_class` and the type (name, predefined type, instance count)
  - `mark` and `operation`
  - the material layers with their thicknesses, the systems and the room finishes
  - the file's declared units
  - for every measured value, how it was obtained (explicit, calculated or inferred, with the method). An inferred value is shown in the assumption colour.

**Levels.** Each storey sits at its `elevation_m`. JSON levels and IFC storeys with the same name merge into one level, so nothing is stacked twice. A file without an elevation goes to 0 m, and the Levels panel says so.

## Setup and run

```bash
cd blueprint-viewer
npm install      # pins web-ifc 0.0.68 and copies web-ifc-api.js + web-ifc.wasm (and the shared vocabulary) into vendor/
npm start        # serves this folder; open the printed URL
```

The `.wasm` must be **served**, not bundled. `npm install` copies it to `vendor/web-ifc/`, and the page passes that folder's absolute URL to `SetWasmPath(…, true)`. That's equivalent to `SetWasmPath('/')` with the wasm in a public root, but it also works when the viewer isn't served from `/`.

A wrong path would otherwise fail silently with an empty scene. Instead, the page checks that the module really instantiated; if it didn't, it checks whether the wasm is served at all, and the error names the URL.

The single-threaded wasm is used, because the multi-threaded one needs cross-origin isolation headers. `file://` does not work, because browsers block ES modules from it. three.js r160 and three-mesh-bvh 0.7.8 load from jsDelivr through the import map.

## Use

- **Add files:** use **Open files** / **Add files** (multi-select) or drag and drop anywhere. `.json` and `.ifc` can be mixed. Loading adds to the scene and never replaces it.
- **Files panel:** lists each file with a Remove button and its geometry source. For example, "web-ifc (real 3D solids)" or "JSON polygons (extruded from 2D, heights assumed)". It also shows the link counts in both directions:
  - clinic: 6 328 of 6 587 meshes have a JSON element, and every JSON element has a mesh;
  - esplanades: the JSON rooms have no mesh, because web-ifc does not stream `IfcSpace`, so they are drawn from the JSON.
- **Levels panel:** each level has a checkbox (labelled with its name and elevation) and **Isolate**.
  - **Separate levels** spreads the levels vertically, up to 3× their real spacing.
  - **Pull apart** separates individual pieces, independently of the levels. Both can be active at once.
  - **Cut at** clips the whole stack at one height; its range follows the model.
- **Visibility:** Floors, Walls, Doors, Windows, Fixtures, Stairs, Structure (slabs, columns, beams, roofs…), Services (pipes, ducts, fittings…), Room names, Areas.
- **Fixtures legend:** lists every fixture category present, with its colour and count. Clicking a row shows only that category.
- **What is in each file:** driven by coverage.
  - "none in this drawing" means analysed with nothing found; "not extracted yet" (warning colour) means `not_analysed`.
  - Banners say what a file cannot show: "no architecture" (the clinic plumbing model has no rooms, walls or openings), approximate geometry, no scale, or IFC parts web-ifc could not mesh.
  - The console prints the same inventory for every JSON on load.
- **Property-set units:** see below.
- **Selecting:** hover highlights and click selects.
  - The nearest visible hit wins. If a *smaller* element lies within 4 px of the pointer, it is preferred, so small fixtures can be picked.
  - **Clicking the same spot again** cycles through elements drawn on top of each other, then through everything further along the ray. That's how you reach a fitting enclosed by a pipe.
  - Hidden groups and levels are never selectable, and a drag (6 px or more) is never a click.
- **Inspector:** shows id, kind, every numeric field the element actually has, confidence (as a bar), flags in plain words, and provenance. For IFC elements it adds `ifc_type`, GlobalId, expressID and the size measured from the real geometry.
  - Values that are conventions are labelled *(assumed)* and shown in the assumption colour.
  - A value the file does not contain is never shown.
- **Status line:** shows frames per second, draw calls and triangles.
- **Console:** `window.viewer` gives access to the viewer from the browser console.

## What is measured and what is assumed

| Piece (JSON geometry) | Assumed height |
|---|---|
| Wall | 2.70 m (a wall's own `height_m` when the file has one) |
| Door head / window head | 2.10 m |
| Window sill | 0.90 m |
| Fixture, by category | toilet 0.78, sink 0.85, bathtub 0.55, shower 2.0 (50 mm tray plus a glass box), stove 0.90, fridge 1.80, washing machine 0.85, closet 2.0, radiator 0.60, elevator 2.20, sauna bench 0.45, other 0.78 |
| Stair riser | level height (or 2.70 m) ÷ total treads; flights and landings from `properties.flights` / `landings` (a U stair rises along one flight, over the landing, and back along the other) |
| Floor slab | 80 mm, 20 mm below the wall base and inset 10 mm |

Room outlines and wall faces come from different layers and are not guaranteed to coincide. The 20 mm drop and 10 mm inset make the mismatch read as an edge, not a gap.

A window is the glass pane, and the bands below the sill and above the head are **wall**, with a "part of" link. A door is a 40 mm leaf at 35° plus a threshold. The swing angle and hinge side are conventions, chosen so leaves don't run through walls.

**web-ifc geometry assumes nothing vertical.** The only viewer choice is colour: where the file has no style, web-ifc returns pure white, and the viewer uses a colour per group. The clinic model has no styles at all.

## Property-set units

IFC psets are copied raw, and their units are not consistent. In the clinic model, `Dimensions.Length` is in millimetres while `Dimensions.Outer Diameter` is in **feet**, in the same pset.

The viewer therefore checks units against geometry. For pipes lying along a plan axis, it compares the measured length with `Length` and the measured width with `Outer Diameter`, using the most common property of that name. A median ratio pset ÷ measured near 1 000 means mm, near 3.28 means ft, and near 1 means m. The unit is accepted only if the median is within 10 % of a unit and at least 60 % of the pipes agree.

On the clinic model the check finds:

| Property | Ratio | Pipes | Unit |
|---|---|---|---|
| `Length` | 1000.0 | 1 588 | mm |
| `Outer Diameter` | 3.332 | 1 588 | ft (the tessellated mesh is about 1.5 % narrower than the true round pipe) |

It reaches the same result from the JSON footprints alone. A verified pset value is shown converted to metres, next to its file value. Every other pset number is labelled **raw**.

## Correcting a classification

With a room, opening or object selected (one that has a JSON record), the inspector offers **Correct**. **Applies to** picks the key:

- `block:NAME` when the element has `properties.source_block`
- `room:LABEL` for rooms
- otherwise `ref:FILE#ID`, which means *this element of this drawing only*, or `layer:NAME`, which means every element on its layer

**Download corrections.json** saves the corrections in the shape blueprint-ingest reads. Put the file at the repo root and re-run the pipeline. It is a lookup table, and nothing is learned.

## Checks

```bash
node scripts/verify.mjs ../blueprint-ingest/out/samples ../data
```

This runs the Phase 5 checks on the viewer's own scene and picking code (`BlueprintScene`, which has no renderer) with web-ifc's Node build: levels, alignment, real pipes, match rates, unit check, selectability, the wall under a window, raster/unscaled mode, the single-file regression, WASM memory and console errors. Frame rate and look need a browser; the status line shows fps live.

## Limits

- web-ifc 0.0.68 cannot mesh some representation types: 26 in the clinic model, 12 372 in molio, 28 299 in hitos. They are counted and shown in a banner, and those parts are missing from the 3D view. The per-line web-ifc messages are kept out of the console.
- web-ifc does not stream `IfcSpace`, so rooms in IFC files are drawn from the JSON polygons.
- A drawing JSON loaded without its `.ifc` is drawn from 2D footprints (a banner says so). Load both for real geometry.
- No raster route exists in this repository yet. Unscaled mode (a nominal 1 px = 1 cm, with a no-scale banner) and the vocabulary mapping are tested with a synthetic CubiCasa-shaped file.
