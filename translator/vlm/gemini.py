"""Gemini over the REST API (urllib, no SDK). The key goes in the x-goog-api-key header, never in the URL.

The key is read from env GEMINI_API_KEY only, never stored on the client object's public state, written,
logged or cached; every error message is scrubbed of it (translator.vlm.base.scrub).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request

from translator.vlm import config
from translator.vlm.base import BaseClient, VLMError, scrub

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 300  # default thinking on 3.x can take minutes; a timed-out call may still be billed
RETRIES = 1  # one retry with backoff on a timeout, connection error, 429 or 5xx
RETRY_BACKOFF_S = 10.0
log = logging.getLogger("translator.vlm")

ANSWER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "count": {"type": "INTEGER"},
        "boxes": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "NUMBER"}}},
    },
    "required": ["count", "boxes"],
}


def _key() -> str:
    key = os.environ.get(config.API_KEY_ENV, "")
    if not key:
        raise VLMError(f"{config.API_KEY_ENV} is not set; stop and ask k")
    return key


def _retryable(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    return isinstance(e, (TimeoutError, urllib.error.URLError, ConnectionError, OSError))


def _request(
    method: str, path: str, body: dict | None = None, opener=urllib.request.urlopen, sleep=time.sleep
) -> dict:
    """One request, retried once (after RETRY_BACKOFF_S) on a timeout, connection error, 429 or 5xx.

    A latency line per attempt goes to the 'translator.vlm' logger (method, path, seconds; never the key).
    A timed-out attempt may still have been billed by the API without returning token counts.
    """
    key = _key()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    for attempt in range(1, RETRIES + 2):
        req = urllib.request.Request(f"{API_ROOT}/{path}", data=data, method=method)
        req.add_header("x-goog-api-key", key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        t0 = time.monotonic()
        try:
            with opener(req, timeout=TIMEOUT_S) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            log.info("gemini %s %s: %.1f s (attempt %d)", method, path, time.monotonic() - t0, attempt)
            return out
        except Exception as e:
            dt = time.monotonic() - t0
            if isinstance(e, urllib.error.HTTPError):
                detail = e.read().decode("utf-8", "replace")[:500] if e.fp else ""
                msg = f"HTTP {e.code} on {method} {path}: {detail}"
            else:
                msg = f"{type(e).__name__} on {method} {path}: {e}"
            msg = scrub(msg, [key])
            log.warning("gemini %s %s: failed after %.1f s (attempt %d): %s", method, path, dt, attempt, msg)
            if attempt <= RETRIES and _retryable(e):
                sleep(RETRY_BACKOFF_S * attempt)
                continue
            raise VLMError(msg) from None
    raise AssertionError("unreachable")


def list_models(opener=urllib.request.urlopen) -> list[dict]:
    out, token = [], None
    while True:
        page = _request(
            "GET", "models?pageSize=1000" + (f"&pageToken={token}" if token else ""), opener=opener
        )
        out += page.get("models", [])
        token = page.get("nextPageToken")
        if not token:
            return out


class GeminiClient(BaseClient):
    def __init__(
        self,
        model: str = config.GEMINI_MODEL,
        thinking_level: str | None = config.THINKING_LEVEL,
        opener=None,
        **kw,
    ):
        super().__init__(**kw)
        self.model_name = model
        self.settings = dict(config.GENERATION)
        if thinking_level is not None:  # None = the model's default thinking; 3.x: low | medium | high
            self.settings["thinking_level"] = thinking_level
        self._opener = opener or urllib.request.urlopen

    def _secrets(self) -> list[str]:
        return [os.environ.get(config.API_KEY_ENV, "")]

    def _call(self, image_bytes: bytes, prompt: str, category: str) -> tuple[str, dict, dict]:
        gen = {
            "responseMimeType": self.settings["response_mime_type"],
            "responseSchema": ANSWER_SCHEMA,
        }
        if "thinking_level" in self.settings:
            gen["thinkingConfig"] = {"thinkingLevel": self.settings["thinking_level"]}
        body = {
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image_bytes).decode()}},
                {"text": prompt},
            ]}],
            "generationConfig": gen,
        }  # fmt: skip
        raw = _request("POST", f"models/{self.model_name}:generateContent", body, opener=self._opener)
        try:
            parts = raw["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError) as e:
            raise VLMError(f"unexpected response shape: {json.dumps(raw)[:300]}") from e
        um = raw.get("usageMetadata", {})
        usage = {
            "input_tokens": int(um.get("promptTokenCount", 0)),
            "output_tokens": int(um.get("candidatesTokenCount", 0)),
            "thinking_tokens": int(um.get("thoughtsTokenCount", 0)),
        }
        return text, usage, {"usageMetadata": um, "modelVersion": raw.get("modelVersion"),
                             "finishReason": raw["candidates"][0].get("finishReason")}  # fmt: skip
