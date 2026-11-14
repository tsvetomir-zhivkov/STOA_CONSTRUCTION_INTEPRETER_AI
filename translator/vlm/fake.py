"""FakeClient (tests, no network) and the QwenClient stub (Qwen3-VL on Modal, not built yet)."""

from __future__ import annotations

import json

from translator.vlm.base import BaseClient


class FakeClient(BaseClient):
    """Answers from a script: answers[category] = {"count": n, "boxes": [[y0, x0, y1, x1], ...]}."""

    model_name = "fake"

    def __init__(self, answers: dict | None = None, tokens: tuple[int, int, int] = (1000, 50, 0), **kw):
        super().__init__(**kw)
        self.answers = answers or {}
        self.tokens = tokens
        self.calls: list[tuple[str, int]] = []
        self.settings = {"fake": True}

    def _call(self, image_bytes: bytes, prompt: str, category: str) -> tuple[str, dict, dict]:
        ans = self.answers.get(category, {"count": 0, "boxes": []})
        self.calls.append((category, len(image_bytes)))
        i, o, t = self.tokens
        return json.dumps(ans), {"input_tokens": i, "output_tokens": o, "thinking_tokens": t}, {"fake": True}


class QwenClient(BaseClient):
    model_name = "qwen3-vl"

    def _call(self, image_bytes: bytes, prompt: str, category: str):
        raise NotImplementedError(
            "QwenClient is a stub: Qwen3-VL on Modal is not built yet (plan step 9, option 2). "
            "Use STOA_VLM=gemini or STOA_VLM=fake."
        )
