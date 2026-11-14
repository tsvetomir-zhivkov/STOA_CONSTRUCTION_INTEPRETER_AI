"""Stoa vision translator: any drawing file -> one schema v0.2 JSON per page.

Hand-off contract: translate(path) -> dict, and `python -m translator <path>`.
Outputs go to index/<source>/<file>.json (see translator.store.index_path).
"""

from __future__ import annotations

__version__ = "0.2.0"


def translate(path: str) -> dict:
    """Translate one drawing file into a schema v0.2 document.

    Not implemented yet: needs the router (step 2) and extractors (steps 3+).
    """
    raise NotImplementedError("translate() needs the router (plan step 2)")
