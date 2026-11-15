"""PID_dataset (Zenodo 8028570, CC BY 4.0) adapter. Reads ONLY 0__raw_data, with its own train/test split.

Format found by inspection: sheets/<split>/<n>.jpg, labels/<split>/<n>.txt in YOLO
format (class cx cy w h, normalised), ONE class ("Symbol"), no pipe lines, no connectivity, no text.
Never reads 1__processed_data, 2__Stage-1 (pretrained Darknet weights) or 3__Stage-2.

Output per sheet in data_pipes/ds/<split>/<id>/: sheet.jpg (original bytes), yolo.txt (class x0 y0 x1 y1, page
pixels), gt.pipe.json (nodes only, or with the hand-labelled edges of gt.edges.json when that file exists).
Splits: the dataset's own test set stays our test set; our val is ~10% of its train, stratified by box count.
"""

from __future__ import annotations

import json
import random
import shutil
from collections.abc import Iterator
from pathlib import Path

from PIL import Image

from pipes.write import finalize

HERE = Path(__file__).parent.parent
DS = HERE / "data_pipes" / "ds"
DEV = HERE / "data_pipes" / "dev"
DEFAULT_ROOT = Path.home() / "Downloads" / "PID_Dataset" / "PID_Dataset"
DATASET = "pid_dataset"
CLASS_NAMES = ["symbol"]  # single class: the dataset's "Symbol"
N_DEV = {"train": 40, "val": 7, "test": 5}


class Adapter:
    """The step 2 interface. lines/connections/texts are empty: this dataset does not annotate them."""

    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root) / "0__raw_data"
        if not self.root.is_dir():
            raise FileNotFoundError(f"{self.root} not found")

    def sheets(self, split: str) -> Iterator[str]:
        for p in sorted((self.root / "sheets" / split).glob("*.jpg"), key=lambda p: int(p.stem)):
            yield p.stem

    def image_path(self, split: str, sid: str) -> Path:
        return self.root / "sheets" / split / f"{sid}.jpg"

    def symbols(self, split: str, sid: str) -> list[dict]:
        w, h = Image.open(self.image_path(split, sid)).size
        out = []
        for line in (self.root / "labels" / split / f"{sid}.txt").read_text().splitlines():
            p = line.split()
            if len(p) != 5:
                continue
            _, cx, cy, bw, bh = (float(v) for v in p)
            box = [(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h]
            box = [round(min(max(v, 0), w if i % 2 == 0 else h), 2) for i, v in enumerate(box)]
            if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
                out.append({"cls_raw": "Symbol", "bbox_px": box})
        return out

    def lines(self, split, sid):
        return []

    def connections(self, split, sid):
        return None

    def texts(self, split, sid):
        return []


def splits(adapter: Adapter, seed: int = 0) -> dict[str, list[str]]:
    """{'train','val','test'} sheet ids. val: every k-th train sheet by box count (keeps the density mix)."""
    train = sorted(adapter.sheets("train"), key=lambda s: len(adapter.symbols("train", s)))
    n_val = max(1, round(len(train) * 0.1))
    rng = random.Random(seed)
    step = len(train) / n_val
    val = {train[min(int(i * step + rng.random() * step), len(train) - 1)] for i in range(n_val)}
    return {
        "train": [s for s in train if s not in val],
        "val": sorted(val, key=int),
        "test": list(adapter.sheets("test")),
    }


def gt_document(
    sid: str, split: str, w: int, h: int, symbols: list[dict], edges: list[dict] | None = None
) -> dict:
    src = f"pid_dataset/0__raw_data/sheets/{'test' if split == 'test' else 'train'}/{sid}.jpg"
    prov = {"extractor_id": "ext_human", "confidence": 1.0, "source_ref": None}
    nodes = []
    for i, s in enumerate(symbols):
        x0, y0, x1, y1 = s["bbox_px"]
        nodes.append(
            {
                "id": f"node_{i + 1}",
                "type": "unknown",
                "subtype": "unknown",
                "tag": None,
                "tag_text_id": None,
                "label": None,
                "bbox_px": [x0, y0, x1, y1],
                "center_px": [round((x0 + x1) / 2, 2), round((y0 + y1) / 2, 2)],
                "rotation_deg": 0,
                "ports_px": [],
                "nominal_size": None,
                "system": None,
                "attributes": {"cls_raw": s["cls_raw"]},
                "in_room_id": None,
                "in_room_link_id": None,
                "provenance": {**prov, "source_ref": f"{sid}:{i}"},
            }
        )
    edges = edges or []
    cov = [
        {"id": "cov_1", "region": "page", "category": "symbols", "status": "analysed", "confidence": 1.0,
         "extractor_id": "ext_human", "element_ids": [], "note": "dataset annotation (single class Symbol)"},
        {"id": "cov_2", "region": "page", "category": "pipes",
         "status": "analysed" if edges else "not_analysed", "confidence": 1.0 if edges else 0.0,
         "extractor_id": "ext_human", "element_ids": [],
         "note": "hand-labelled connections" if edges else "the dataset annotates no pipe lines"},
    ]  # fmt: skip
    return {
        "schema_version": "0.1",
        "kind": "pipe_index",
        "sheet": {
            "id": f"{DATASET}/{split}/{sid}#0",
            "source_file": src,
            "source_dataset": DATASET,
            "file_format": "jpg",
            "page_index": 0,
            "dxf_layout": None,
            "diagram_type": "schematic",
            "is_to_scale": False,
            "route": "raster_pipeline",
            "sheet_id": None,
            "level_id": None,
            "title": None,
            "systems_shown": [],
            "languages": [],
            "regions": [],
            "provenance": prov,
        },
        "frame": {
            "frame_key": f"{src}#0@{w}x{h}",
            "width_px": w,
            "height_px": h,
            "render_dpi": None,
            "source_units": "px",
            "source_to_px": [1, 0, 0, 0, 1, 0],
        },
        "scale": {
            "metres_per_px": None,
            "source": "not_to_scale",
            "confidence": 0.9,
            "scale_text": None,
            "evidence_ids": [],
            "revision": 1,
            "provenance": prov,
        },
        "metres_scale_revision": None,
        "links": [],
        "extractors": [
            {
                "id": "ext_human",
                "kind": "human",
                "name": "PID_dataset annotation + hand-labelled edges",
                "version": None,
                "run_at": None,
                "cost_usd": 0.0,
            }
        ],  # fmt: skip
        "nodes": nodes,
        "edges": edges,
        "markers": [],
        "texts": [],
        "relations": [],
        "coverage": cov,
    }


def build_sheet(adapter: Adapter, split: str, sid: str, out_split: str, out_root: Path = DS) -> Path:
    d = out_root / out_split / sid
    d.mkdir(parents=True, exist_ok=True)
    src = adapter.image_path(split, sid)
    shutil.copyfile(src, d / "sheet.jpg")  # original pixels, untouched
    w, h = Image.open(src).size
    syms = adapter.symbols(split, sid)
    (d / "yolo.txt").write_text(
        "".join(f"0 {b[0]} {b[1]} {b[2]} {b[3]}\n" for b in (s["bbox_px"] for s in syms)), encoding="utf-8"
    )
    edges = None
    ef = d / "gt.edges.json"
    if ef.exists():
        edges = json.loads(ef.read_text(encoding="utf-8"))
    doc = finalize(gt_document(sid, out_split, w, h, syms, edges))
    (d / "gt.pipe.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def build_all(root: Path = DEFAULT_ROOT, seed: int = 0) -> dict:
    ad = Adapter(root)
    sp = splits(ad, seed)
    for name, ids in sp.items():
        src_split = "test" if name == "test" else "train"
        for sid in ids:
            build_sheet(ad, src_split, sid, name)
    rng = random.Random(seed)
    dev = {k: sorted(rng.sample(sp[k], min(n, len(sp[k]))), key=int) for k, n in N_DEV.items()}
    for name, ids in dev.items():
        for sid in ids:
            d = DEV / name / sid
            d.mkdir(parents=True, exist_ok=True)
            for f in ("sheet.jpg", "yolo.txt", "gt.pipe.json"):
                shutil.copyfile(DS / name / sid / f, d / f)
    (DEV / "split.json").write_text(json.dumps({"full": sp, "dev": dev}, indent=1), encoding="utf-8")
    return {"full": {k: len(v) for k, v in sp.items()}, "dev": {k: len(v) for k, v in dev.items()}}
