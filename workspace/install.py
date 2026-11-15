"""Install this checkout's declared Python dependencies without packaging its data folders."""
import subprocess
import sys
from pathlib import Path

import tomllib

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = project["dependencies"]
    if "--test" in sys.argv:
        dependencies += project["optional-dependencies"]["test"]
    subprocess.check_call([sys.executable, "-m", "pip", "install", *dependencies])
