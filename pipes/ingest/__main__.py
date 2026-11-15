"""python -m pipes.ingest [--root PID_Dataset/PID_Dataset] [--seed 0]"""

from __future__ import annotations

import argparse
import json

from pipes.ingest import pid_dataset


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m pipes.ingest")
    ap.add_argument("--root", default=str(pid_dataset.DEFAULT_ROOT))
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    print(json.dumps(pid_dataset.build_all(a.root, a.seed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
