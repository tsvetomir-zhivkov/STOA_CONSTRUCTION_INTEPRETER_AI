"""python -m pipes <file> [--source-file NAME]

  .png .jpg .jpeg .webp   the scan route (detector + OCR on Modal, skeleton tracer): see pipes/scan.py
  .dxf                    the DXF route (exact, no model): see pipes/extract/dxf_pipes.py
Both write the pipe index where the router and the agent look.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    suffix = Path(argv[0]).suffix.lower()
    if suffix == ".dxf":
        from pipes.extract import dxf_pipes

        return dxf_pipes.main(argv)
    from pipes import scan

    if suffix in scan.IMAGE_SUFFIXES:
        return scan.main(argv)
    print(
        f"pipes: no route for {argv[0]!r} (supported: .dxf and {', '.join(sorted(scan.IMAGE_SUFFIXES))})",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
