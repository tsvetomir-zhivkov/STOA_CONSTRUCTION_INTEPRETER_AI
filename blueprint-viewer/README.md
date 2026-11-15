# Stoa building workspace

The existing viewer now runs with a local document service. It displays real IFC meshes,
per-storey plans, native DXF/SVG drawings, normalized component information, a stored-document
gallery, and the existing analysis agent. Plain HTML/ES modules; no frontend compilation step.

## Run from another checkout

Use Python 3.11+ and Node 22+. Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m workspace.install --test
npm ci --prefix blueprint-ingest
npm ci --prefix blueprint-viewer
npm start
```

Open **http://localhost:3000/** for the scrollable landing page, then choose **Open workspace**.
The drawing workspace is at **http://localhost:3000/workspace/**. On macOS/Linux, use `.venv/bin/python` for the Python command.
`npm start` selects the checkout's virtual environment, or `PYTHON` if set.
Use `npm start -- --port 4601` to choose another port. `npm start --prefix blueprint-viewer` launches the same service.
The Python installation helper reads dependencies directly from `pyproject.toml`.

The service binds to loopback. Each computer runs its own copy. Original uploads, previews,
validated JSON and analysis history live in `.workspace-data/`, which is ignored by Git.
A clone includes source code and committed samples, not another computer's uploaded library.
All Three.js, BVH and web-ifc assets are copied locally by the viewer's install script.
Opening `index.html` directly or serving it with `npx serve` does not provide the document API.
If a static preview is already using port 3000, stop it with Ctrl+C before running `npm start`.
A missing API is reported visibly in the workspace with recovery instructions.

AI questions use the existing `answering_agent` integration. Configure `GEMINI_API_KEY` and,
optionally, `GEMINI_MODEL` in the repository's `.env`. Ordinary IFC/CAD extraction does not
need this key. PDF/image vision extraction requires the existing vision adapter configuration;
when unavailable, the workspace retains the source and reports the extraction error.

## Working with drawings

- Add IFC, JSON, DXF, SVG, PDF or image files. IFC imports include its validated building data.
- IFC geometry loads independently of building-data extraction. A visible status shows loading,
  then moves aside once 3D is ready. Facts attach in the background without resetting the camera
  or selected view mode. **Cancel import** clears the pending import and stops its extraction.
- Choose **2D**, **3D** or **Parallel**. Pick a floor from the toolbar or **Plan** in the hierarchy.
- Use floor/category checkboxes, show/hide all, isolation, pull apart, level separation and clipping.
- Select a component to inspect available facts, measurement provenance and ordered material layers.
  Source identifiers are collapsed. **Remove component** hides it; **Restore removed** restores it.
- In native DXF/SVG views, CAD layers/groups remain distinct from building storeys. Unlinked paths
  are explicitly unclassified. Wheel zoom, drag to pan, **Fit**, and Home work in 2D.
- **Drawings** opens the persistent gallery. Removing a file from the workspace preserves its stored
  document; **Delete** in the gallery deletes its stored source and derived files.
- **AI review** shows extracted facts/coverage and questions about the selected building. Citations
  locate components when a reliable identifier-to-geometry mapping exists.
- Existing classification corrections remain downloadable as `corrections.json` for the pipeline.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check .
npm test --prefix blueprint-ingest
npm test --prefix blueprint-viewer
npm run check --prefix blueprint-viewer
.\.venv\Scripts\python.exe blueprint-viewer/scripts/workspace-fixtures.py
npm start --prefix blueprint-viewer -- --port 4601
# In another terminal, with Chrome installed:
npm run test:browser --prefix blueprint-viewer -- http://127.0.0.1:4601
```

Browser verification uses Playwright Core and installed Chrome. It reuses repository fixture
builders and the committed Esplanades normalized sample. Results/screenshots go to
`.workspace-data/qa/`; test-created documents are deleted after the run.
The older `scripts/verify.mjs` additionally requires the external clinic/Esplanades/Swiss datasets.
Those original source files are not included in this checkout.

See [architecture, evidence and limitations](../docs/bim-workspace-architecture.md) and
[the staged dependency map](../docs/bim-workspace-dependency-map.md).
