"""Step 9 gates: which (image, category) cases go to the VLM, in priority order.

  1 shower         images with a wet room region (bathroom, WC, sauna; labelled or segmented)
  2 prior          kitchens / bathrooms where a count prior fired (coverage note "prior: ...")
  3 no_evidence    a category with no detected element: kitchen, bathroom, doors, windows, rooms
  4 floors         storey titles and building blobs disagree (coverage 'floors' partial)
  5 win_door       mean door or window confidence < WIN_DOOR_CONF (last, only if time remains)
One task per (image, category): an image passing several gates for the same category is sent once.
"""

from __future__ import annotations

WET_TYPES = {"bathroom", "wc", "sauna"}
WIN_DOOR_CONF = 0.7
GATES = ("shower", "prior", "no_evidence", "floors", "win_door")


def _active(items):
    return [x for x in items if x.get("status", "active") == "active"]


def _read(rooms):
    return [r for r in _active(rooms) if r["provenance"]["extractor"] != "prior"]


def _page_cov(doc, cat):
    return next((c for c in doc["coverage"] if c["category"] == cat and c["region"] == "page"), None)


def wet_regions(doc: dict) -> list[dict]:
    """Segmentation regions of wet rooms (with a real polygon), by label or by segmentation class."""
    out = []
    for r in _read(doc["rooms"]):
        flags = set(r.get("flags", []))
        if not r.get("polygon_px") or flags & {"label_only", "shares_region"}:
            continue  # a label's own text box is not a room region
        det = r["provenance"].get("details", {})
        types = {r["type"], det.get("seg_type"), det.get("prior_type")}
        if types & WET_TYPES or r.get("subtype") == "Bath":
            out.append(r)
    return out


def gates(doc: dict) -> dict[str, list[str]]:
    """gate -> categories it sends for this document."""
    out: dict[str, list[str]] = {g: [] for g in GATES}
    if wet_regions(doc):
        out["shower"].append("shower")
    for cat, cov_cat in (("kitchen", "kitchens"), ("bathroom", "bathrooms")):
        c = _page_cov(doc, cov_cat)
        if c and c.get("note", "").startswith("prior:"):
            out["prior"].append(cat)
    rooms, ops = _read(doc["rooms"]), _active(doc["openings"])
    present = {
        "kitchen": any(r["type"] == "kitchen" for r in rooms),
        "bathroom": any(r["type"] == "bathroom" for r in rooms),
        "doors": any(o["kind"] == "door" for o in ops),
        "windows": any(o["kind"] == "window" for o in ops),
        "rooms": bool(rooms),
    }
    out["no_evidence"] = [c for c, ok in present.items() if not ok]
    fl = _page_cov(doc, "floors")
    if fl and fl["status"] == "partial":
        out["floors"].append("floors")
    for cat in ("doors", "windows"):
        c = _page_cov(doc, cat)
        if c and c["status"] != "not_analysed" and c["confidence"] < WIN_DOOR_CONF:
            out["win_door"].append(cat)
    return out


def tasks(doc: dict, enabled_gates=GATES) -> list[tuple[str, str]]:
    """Unique (category, first gate) pairs for one document, in gate priority order."""
    seen, out = set(), []
    g = gates(doc)
    for gate in GATES:
        if gate not in enabled_gates:
            continue
        for cat in g[gate]:
            if cat not in seen:
                seen.add(cat)
                out.append((cat, gate))
    return out
