"""Which tool sets does a page get? Decided from which index files exist for it.

  python -m pipes.routing route <drawing file>       e.g. pid_dataset/0__raw_data/sheets/test/157.jpg
  python -m pipes.routing publish <pipe.json> ...    copy traced sheets to the hand-off layout

Hand-off layouts (same naming rule as translator.store.index_path, source = parent folder name):
  floor plan  index/<source>/<stem>.json  (or <stem>__<part>.json)             schema v0.2
  pipes       pipes/index_pipes/<source>/<stem>#<page>.pipe.json               pipe schema v0.1
Rules: both exist -> both tool sets (a page can be a floor plan and carry pipe work); only a pipe index -> pipe tools
only; only a floor-plan index -> floor-plan tools only; neither -> no tools and a reason that names the command to run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

from pipes.write import finalize

HERE = Path(__file__).parent
PIPES_INDEX = HERE / "index_pipes"


def pipe_index_path(drawing_file: str, page: int = 0, pipes_dir: Path = PIPES_INDEX) -> Path:
    p = PurePosixPath(drawing_file.replace("\\", "/"))
    return pipes_dir / p.parent.name / f"{p.stem}#{page}.pipe.json"


def floor_index_paths(drawing_file: str, index_dir: Path | None = None) -> list[Path]:
    """index/<source>/<stem>.json and any <stem>__<part>.json (multi-page and multi-level files)."""
    if index_dir is None:
        from translator.config import INDEX_DIR as index_dir  # imported, never edited
    p = PurePosixPath(drawing_file.replace("\\", "/"))
    d = Path(index_dir) / p.parent.name
    if not d.is_dir():
        return []
    return sorted([*d.glob(f"{p.stem}.json"), *d.glob(f"{p.stem}__*.json")])


def route_page(
    drawing_file: str, page: int = 0, *, index_dir: Path | None = None, pipes_dir: Path = PIPES_INDEX
) -> dict:
    floor = floor_index_paths(drawing_file, index_dir)
    pipe = pipe_index_path(drawing_file, page, pipes_dir)
    pipe_path = pipe if pipe.exists() else None
    sets = [*(["floor_plan"] if floor else []), *(["pipe"] if pipe_path else [])]
    if len(sets) == 2:
        reason = (
            "both indexes exist: use pipe tools for pipes, valves, tags, lines and connectivity; floor-plan tools "
            "for rooms, walls, doors, windows and areas"
        )
    elif sets == ["pipe"]:
        reason = "only a pipe index exists: answer with the pipe tools; there are no rooms or walls to query"
    elif sets == ["floor_plan"]:
        reason = "only a floor-plan index exists: pipes, valves and line questions cannot be answered from it"
    else:
        reason = (
            f"no index for {drawing_file} page {page}: run `python -m translator <file> --write` (floor plan) or "
            "`python -m pipes <file>` (pipes) first"
        )
    return {
        "file": drawing_file,
        "page": page,
        "tool_sets": sets,
        "floor_plan": [str(x) for x in floor],
        "pipe": str(pipe_path) if pipe_path else None,
        "reason": reason,
    }


def publish(pipe_json: str | Path, pipes_dir: Path = PIPES_INDEX) -> Path:
    """Validate a traced sheet and write it to the hand-off layout from its own sheet.source_file / page_index."""
    doc = json.loads(Path(pipe_json).read_text(encoding="utf-8"))
    doc = finalize(doc)
    out = pipe_index_path(doc["sheet"]["source_file"], doc["sheet"]["page_index"], pipes_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m pipes.routing", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("route")
    r.add_argument("file")
    r.add_argument("--page", type=int, default=0)
    sub.add_parser("publish").add_argument("pipe_json", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "route":
        print(json.dumps(route_page(a.file, a.page), indent=1))
    else:
        for p in a.pipe_json:
            print(publish(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
