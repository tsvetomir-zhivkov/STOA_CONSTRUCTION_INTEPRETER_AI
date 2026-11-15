"""Optional, conservative building statistics. Missing inputs never become zero."""

from translator.building import COLLECTIONS


def calculate_statistics(doc):
    active = {c: [e for e in doc[c] if e.get("status", "active") == "active"] for c in COLLECTIONS}
    result = []

    def covered(category):
        drawings = {d["id"] for d in doc["drawings"]}
        records = [c for c in doc["coverage"] if c["category"] == category and c["region"] == "page"]
        return (
            bool(drawings)
            and {c["drawing_id"] for c in records} == drawings
            and all(c["status"] == "analysed" and c["confidence"] >= 0.8 for c in records)
        )

    def append(name, items, unit, field=None):
        if field and (not items or any(e.get(field) is None for e in items)):
            return
        result.append(
            {
                "name": name,
                "value": sum(e[field] for e in items) if field else len(items),
                "unit": unit,
                "origin": "calculated",
                "quality": "calculated" if field else "exact",
                "method": f"sum active indexed {field}" if field else "count active indexed instances",
                "entity_ids": [e["id"] for e in items],
                "source_refs": [r for e in items for r in e["source_refs"]],
            }
        )

    if covered("rooms"):
        append("number_of_rooms", active["rooms"], "count")
        append("total_room_area", active["rooms"], "m2", "area_m2")
    if active["levels"]:
        append("number_of_levels", active["levels"], "count")
    if active["units"]:
        append("number_of_units", active["units"], "count")
    for category, kind in [("doors", "door"), ("windows", "window")]:
        if covered(category):
            append(f"number_of_{category}", [o for o in active["openings"] if o["kind"] == kind], "count")
    if covered("walls"):
        append("total_wall_length", active["walls"], "m", "length_m")
        append("total_wall_volume", active["walls"], "m3", "net_volume_m3")
    if covered("fixtures"):
        append(
            "total_slab_volume",
            [o for o in active["objects"] if o["category"] == "slab"],
            "m3",
            "net_volume_m3",
        )
    return {"quantities": result}
