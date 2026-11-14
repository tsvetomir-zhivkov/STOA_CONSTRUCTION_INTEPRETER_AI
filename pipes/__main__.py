"""python -m pipes <file> [--source-file NAME]

.dxf   the DXF route (exact, no model): writes and publishes the pipe index where the router looks.
other  scans/PDFs: the raster route is run per dev folder with `python -m pipes.raster.pipeline` for now.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    if Path(argv[0]).suffix.lower() == ".dxf":
        from pipes.extract import dxf_pipes

        return dxf_pipes.main(argv)
    print(
        f"pipes: no route for {argv[0]!r}; DXF is supported here, scans run through pipes.raster.pipeline",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
