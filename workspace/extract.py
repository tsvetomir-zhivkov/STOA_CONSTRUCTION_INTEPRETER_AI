"""Isolated, cancellable extraction process; stdout contains only result filenames."""
import json
import subprocess
import sys
from pathlib import Path

from translator import translate_all
from translator.store import validate

ROOT = Path(__file__).resolve().parents[1]


def extract(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    from workspace.source_view import make_preview
    make_preview(source, destination.parent)
    if suffix == ".ifc":
        documents = translate_all(str(source))
        for i, document in enumerate(documents):
            validate(document)
            (destination / f"building-{i}.json").write_text(json.dumps(document), encoding="utf-8")
    else:
        script = "scripts/normalize-document.mjs" if suffix == ".json" else "src/index.js"
        command = ["node", str(ROOT / "blueprint-ingest" / script), str(source)]
        if suffix != ".json":
            command.append("--out")
        command.append(str(destination / "building-0.json"))
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:])
        if not list(destination.glob("*.json")):
            # Unsupported vision extraction is an explicit outcome, never an empty success.
            raise RuntimeError("No normalized building was produced; this format may need a configured vision extractor")
    return [p.name for p in sorted(destination.glob("*.json"))]


if __name__ == "__main__":
    print(json.dumps(extract(Path(sys.argv[1]), Path(sys.argv[2]))))
