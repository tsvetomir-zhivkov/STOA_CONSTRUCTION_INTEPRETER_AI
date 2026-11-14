"""Hold out 20% of FILES (not questions), per source, stable across runs.

Files of the same drawing (same stem, or ifc_models <model>_<level>.pdf with <model>.ifc)
form one group and always land on the same side. The split is frozen in
eval/holdout_files.txt once written: delete that file only on purpose.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path, PurePosixPath

HOLDOUT_FILE = Path(__file__).resolve().parent / "holdout_files.txt"
FRACTION = 0.2
SEED = "stoa-junction-2026"

# Files the build plan uses as quick tests: always dev, so tuning on them is allowed.
PINNED_DEV = (
    "site1000_bldg2082_floor9385",
    "bsc-esplanades_1807-ep-ar-v18",
    "bsc-molio_molio-with-uris",
    "high_quality-5815",
)


def group_key(path: str, ifc_stems: set[str]) -> str:
    p = PurePosixPath(path)
    source, stem = p.parent.name, p.stem
    for model in sorted(ifc_stems, key=len, reverse=True):
        if stem == model or stem.startswith(model + "_"):
            return f"{source}/{model}"
    return f"{source}/{stem}"


def _hash(s: str) -> str:
    return hashlib.sha256(f"{SEED}:{s}".encode()).hexdigest()


def make_split(paths: list[str]) -> set[str]:
    """Return the set of held-out file paths."""
    paths = sorted(set(paths))
    ifc_stems = {PurePosixPath(p).stem for p in paths if p.endswith(".ifc")}
    groups: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        groups[group_key(p, ifc_stems)].append(p)
    by_source: dict[str, list[str]] = defaultdict(list)
    for key in groups:
        by_source[key.split("/", 1)[0]].append(key)
    held: set[str] = set()
    for keys in by_source.values():
        candidates = sorted((k for k in keys if k.split("/", 1)[1] not in PINNED_DEV), key=_hash)
        n = round(FRACTION * len(keys))
        if n == 0 and len(keys) >= 2:
            n = 1
        for k in candidates[:n]:
            held.update(groups[k])
    return held


def load_or_make(paths: list[str]) -> set[str]:
    if HOLDOUT_FILE.exists():
        lines = HOLDOUT_FILE.read_text(encoding="utf-8").splitlines()
        return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}
    held = make_split(paths)
    header = "# Held-out files (20% per source, grouped by drawing). Never tune on these.\n"
    HOLDOUT_FILE.write_text(header + "\n".join(sorted(held)) + "\n", encoding="utf-8")
    return held
