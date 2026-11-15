# Stoa Vision Translator

Architectural drawings, building data, and an interactive 2D/3D workspace.

## Start the application

From the repository root, with Python 3.11+ and Node 22+ installed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m workspace.install --test
npm ci --prefix blueprint-ingest
npm ci --prefix blueprint-viewer
npm start
```

On macOS/Linux, replace the Python path with `.venv/bin/python`.
After initial setup, only `npm start` is needed.

Open [localhost:3000](http://localhost:3000/) for the existing scrollable blueprint
landing page. Choose **Open workspace**, or visit
[localhost:3000/workspace/](http://localhost:3000/workspace/) directly.

Stop any previous static preview on port 3000 before starting. `npx serve` and
Live Server serve HTML but cannot import drawings: imports require the Python
document API launched by `npm start`. Use `npm start -- --port 4601` for another port.

Each computer runs its own local server and stores its uploads in the ignored
`.workspace-data/` directory. Run one app server per checkout.

See [the workspace guide](blueprint-viewer/README.md) for formats, controls, AI
configuration, and verification commands. The existing landing source is in
[`scrollcraft/builds/stoa-plan-to-evidence`](scrollcraft/builds/stoa-plan-to-evidence).
