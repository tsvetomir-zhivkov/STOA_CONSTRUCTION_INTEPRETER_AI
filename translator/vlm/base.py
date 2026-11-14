"""Step 9: the swappable VLM interface, with the guards every client goes through.

VLMClient.check(image_bytes, category, prompt, *, source, crop) -> VLMResult. BaseClient.check runs, in order:
  1. hold-out guard: refuses (raises HoldoutError) when `source` is a hold-out file (by path or stem) or the
     image bytes / decoded pixels hash to a hold-out image. Runs before any cache or network code.
  2. cache: index/_raw/vlm/<key>.json, key = sha256(image bytes hash, category, prompt, model, generation
     settings). A cached answer is returned; the same key is never sent twice.
  3. budget: aborts (BudgetExceeded) when the cumulative cost of the run reached its cap.
  4. the client's _call (network for Gemini); errors are re-raised with any secret scrubbed.
Boxes come back as [y_min, x_min, y_max, x_max] normalised 0-1000 on the image SENT, and are mapped to
ORIGINAL page pixels [x0, y0, x1, y1] through the Crop (offset of the sent image on the page and its scale).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from translator.vlm import config


class HoldoutError(PermissionError):
    """A hold-out input was about to be sent to a VLM."""


class BudgetExceeded(RuntimeError):
    """The run's cumulative cost reached its cap."""


class VLMError(RuntimeError):
    """A client call failed (message scrubbed of secrets)."""


@dataclass(frozen=True)
class Crop:
    """Where the sent image sits on the original page: page = (page_x0, page_y0) + sent_px / scale.

    page_x0/page_y0 may be negative when the crop was padded past the page edge. scale = sent px per page px.
    """

    page_x0: float
    page_y0: float
    scale: float
    sent_w: int
    sent_h: int

    def box_to_page(self, box_1000: list[float]) -> list[float]:
        """[y_min, x_min, y_max, x_max] in 0-1000 of the sent image -> [x0, y0, x1, y1] page pixels."""
        y0, x0, y1, x1 = (float(v) for v in box_1000)
        fx, fy = self.sent_w / 1000.0, self.sent_h / 1000.0
        px = [self.page_x0 + x0 * fx / self.scale, self.page_y0 + y0 * fy / self.scale,
              self.page_x0 + x1 * fx / self.scale, self.page_y0 + y1 * fy / self.scale]  # fmt: skip
        return [round(v, 1) for v in px]


@dataclass
class VLMResult:
    count: int
    boxes_px: list[list[float]]
    raw: dict
    cost_usd: float
    model_name: str
    cached: bool = False
    usage: dict = field(default_factory=dict)


class VLMClient(Protocol):
    model_name: str

    def check(
        self, image_bytes: bytes, category: str, prompt: str, *, source: str, crop: Crop | None = None
    ) -> VLMResult: ...


# ------------------------------------------------------------------ hold-out guard


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pixel_hash(image_bytes: bytes) -> str | None:
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        return None
    return _sha(arr.shape.__repr__().encode() + np.ascontiguousarray(arr).tobytes())


class HoldoutGuard:
    """Refuses hold-out inputs by path/stem and by hash of the file bytes and of the decoded pixels.

    The hashes are computed once from the hold-out files' bytes (read only to hash them; never sent, scored
    or shown). A crop or downscale of a hold-out image is caught by the path check, not by the hash.
    """

    def __init__(self, holdout_paths: set[str], data_root: Path | None):
        self.paths = {p.replace("\\", "/") for p in holdout_paths}
        self.stems = {Path(p).stem for p in self.paths}
        self.file_hashes: set[str] = set()
        self.pixel_hashes: set[str] = set()
        for p in sorted(self.paths):
            f = (data_root / p) if data_root else None
            if f is not None and f.is_file():
                b = f.read_bytes()
                self.file_hashes.add(_sha(b))
                ph = pixel_hash(b)
                if ph:
                    self.pixel_hashes.add(ph)

    def check(self, source: str, image_bytes: bytes) -> None:
        s = (source or "").replace("\\", "/")
        if not s:
            raise HoldoutError("refused: every VLM input needs its source path (hold-out guard)")
        if s in self.paths or any(s.endswith("/" + p) for p in self.paths) or Path(s).stem in self.stems:
            raise HoldoutError(f"refused: {s} is a hold-out file")
        if _sha(image_bytes) in self.file_hashes or pixel_hash(image_bytes) in self.pixel_hashes:
            raise HoldoutError(f"refused: image bytes for {s} match a hold-out image")


_GUARD: HoldoutGuard | None = None


def default_guard() -> HoldoutGuard:
    global _GUARD
    if _GUARD is None:
        from translator.config import data_root
        from translator.extract.cubicasa import holdout_files

        root = data_root()
        _GUARD = HoldoutGuard(holdout_files(), root if (root / "data").is_dir() else None)
    return _GUARD


# ------------------------------------------------------------------ budget, cost, cache


class Budget:
    def __init__(self, cap_usd: float = config.BUDGET_CAP_USD, approved_over_hard_max: bool = False):
        if cap_usd > config.BUDGET_HARD_MAX_USD and not approved_over_hard_max:
            raise ValueError(
                f"budget cap ${cap_usd} is above ${config.BUDGET_HARD_MAX_USD}: needs k's approval"
            )
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.calls = 0

    def before_call(self) -> None:
        if self.spent_usd >= self.cap_usd:
            raise BudgetExceeded(f"budget cap ${self.cap_usd:.2f} reached (spent ${self.spent_usd:.4f})")

    def add(self, cost: float) -> None:
        self.spent_usd += cost
        self.calls += 1
        if self.spent_usd > self.cap_usd:
            raise BudgetExceeded(f"budget cap ${self.cap_usd:.2f} exceeded (spent ${self.spent_usd:.4f})")


def cost_usd(model: str, usage: dict) -> float:
    """From token usage; thinking tokens count as output. Unknown model -> KeyError (fail loudly)."""
    price = config.PRICES_USD_PER_MTOK[model]
    out = usage.get("output_tokens", 0) + usage.get("thinking_tokens", 0)
    return round((usage.get("input_tokens", 0) * price["input"] + out * price["output"]) / 1e6, 8)


def cache_key(image_bytes: bytes, category: str, prompt: str, model: str, settings: dict) -> str:
    blob = json.dumps(
        {"image": _sha(image_bytes), "category": category, "prompt": prompt, "model": model,
         "settings": settings}, sort_keys=True, ensure_ascii=False,
    )  # fmt: skip
    return _sha(blob.encode("utf-8"))


def cache_dir() -> Path:
    from translator.config import INDEX_DIR

    return INDEX_DIR / "_raw" / "vlm"


def scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def parse_answer(text: str) -> dict:
    """The model's JSON text -> {"count": int, "boxes": [[y0, x0, y1, x1], ...]} (validated)."""
    data = json.loads(text)
    count = int(data["count"])
    boxes = [[float(v) for v in b] for b in data.get("boxes") or []]
    if count < 0 or any(len(b) != 4 for b in boxes):
        raise ValueError(f"bad VLM answer: {text[:200]}")
    return {"count": count, "boxes": boxes}


class BaseClient:
    """Guards, cache, budget and box mapping; subclasses implement _call(image_bytes, prompt, category)."""

    model_name = "base"
    settings: dict = {}

    def __init__(self, budget: Budget | None = None, guard: HoldoutGuard | None = None,
                 cache: Path | None = None):  # fmt: skip
        self.budget = budget or Budget()
        self._guard = guard
        self.cache = cache
        self.sent_keys: set[str] = set()

    @property
    def guard(self) -> HoldoutGuard:
        return self._guard or default_guard()

    def _secrets(self) -> list[str]:
        return []

    def _call(self, image_bytes: bytes, prompt: str, category: str) -> tuple[str, dict, dict]:
        """-> (answer JSON text, usage {input_tokens, output_tokens, thinking_tokens}, raw response)."""
        raise NotImplementedError

    def check(
        self, image_bytes: bytes, category: str, prompt: str, *, source: str, crop: Crop | None = None
    ) -> VLMResult:
        self.guard.check(source, image_bytes)  # 1. before anything else, every client
        key = cache_key(image_bytes, category, prompt, self.model_name, self.settings)
        folder = self.cache or cache_dir()
        path = folder / f"{key}.json"
        if path.exists():  # 2. cached: never send the same key twice
            rec = json.loads(path.read_text(encoding="utf-8"))
            cached = True
        else:
            if key in self.sent_keys:
                raise RuntimeError(f"cache key {key[:12]} was sent before but has no cache file")
            self.budget.before_call()  # 3.
            try:
                text, usage, raw = self._call(image_bytes, prompt, category)  # 4.
            except (HoldoutError, BudgetExceeded):
                raise
            except Exception as e:  # never let a secret reach a message or traceback
                raise VLMError(scrub(f"{type(e).__name__}: {e}", self._secrets())) from None
            self.sent_keys.add(key)
            cost = cost_usd(self.model_name, usage)
            rec = {"key": key, "model": self.model_name, "settings": self.settings, "category": category,
                   "source": source, "prompt": prompt, "answer_text": text, "usage": usage,
                   "cost_usd": cost, "raw": raw}  # fmt: skip
            blob = scrub(json.dumps(rec, ensure_ascii=False, indent=1), self._secrets())
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(blob, encoding="utf-8")
            self.budget.add(cost)
            cached = False
        ans = parse_answer(rec["answer_text"])
        crop = crop or Crop(0, 0, 1.0, 1000, 1000)
        boxes = [crop.box_to_page(b) for b in ans["boxes"]]
        cost = 0.0 if cached else rec["cost_usd"]  # this run's spend; the cache keeps the original cost
        return VLMResult(count=ans["count"], boxes_px=boxes, raw=rec, cost_usd=cost,
                         model_name=self.model_name, cached=cached, usage=rec["usage"])  # fmt: skip


def get_client(name: str | None = None, **kw) -> BaseClient:
    """Client by name or env STOA_VLM (default gemini)."""
    name = (name or os.environ.get(config.CLIENT_ENV) or config.DEFAULT_CLIENT).lower()
    if name == "gemini":
        from translator.vlm.gemini import GeminiClient

        return GeminiClient(**kw)
    if name == "fake":
        from translator.vlm.fake import FakeClient

        return FakeClient(**kw)
    if name == "qwen":
        from translator.vlm.fake import QwenClient

        return QwenClient(**kw)
    raise ValueError(f"unknown VLM client {name!r} (env {config.CLIENT_ENV}: gemini | fake | qwen)")
