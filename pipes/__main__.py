"""python -m pipes <path>  (routes arrive in steps 4, 5 and 8)."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    print("pipes: no route is implemented yet (DXF route is step 4, scan route step 8)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
