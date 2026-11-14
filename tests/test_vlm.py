"""Step 9: VLM interface, guards, cache, budget, crop mapping, gates (FakeClient; no network at all)."""

from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import pytest

from translator.vlm import base, config, gate, gemini, images, shower
from translator.vlm.base import Budget, BudgetExceeded, Crop, HoldoutError, HoldoutGuard, VLMError
from translator.vlm.fake import FakeClient, QwenClient
from translator.vlm.gemini import GeminiClient

SECRET = "TEST-KEY-must-never-leak-0123456789"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network call in a test")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(gemini, "RETRY_BACKOFF_S", 0.0)
    monkeypatch.setenv("STOA_VLM", "fake")


def _png(h=40, w=60, value=255, seed=None) -> bytes:
    img = np.full((h, w, 3), value, np.uint8)
    if seed is not None:
        img[:] = np.random.default_rng(seed).integers(0, 255, img.shape, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


@pytest.fixture
def guard():
    return HoldoutGuard({"data/cubicasa5k/held.png", "data/swiss_dwellings/Plan_7.pdf"})


def _client(guard, tmp_path, cls=FakeClient, **kw):
    return cls(guard=guard, cache=tmp_path / "vlm", **kw)


# ------------------------------------------------------------------ crop -> page mapping


def test_crop_box_maps_back_to_page_pixels():
    # a crop whose top-left is (100, 50) on the page, upscaled x2 to 400 x 300 sent pixels
    crop = Crop(page_x0=100, page_y0=50, scale=2.0, sent_w=400, sent_h=300)
    # [y_min, x_min, y_max, x_max] in 0-1000 of the sent image
    assert crop.box_to_page([0, 0, 1000, 1000]) == [100.0, 50.0, 300.0, 200.0]
    assert crop.box_to_page([500, 250, 1000, 500]) == [150.0, 125.0, 200.0, 200.0]


def test_crop_image_round_trip_through_padding_and_resize():
    img = np.full((600, 800, 3), 255, np.uint8)
    room = [300.0, 200.0, 400.0, 260.0]  # x0, y0, x1, y1 on the page
    img[200:260, 300:400] = 0
    data, crop, padded = images.crop_image(img, room)
    sent = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert (sent.shape[1], sent.shape[0]) == (crop.sent_w, crop.sent_h)
    assert max(sent.shape[:2]) == images.MIN_CROP_SIDE_PX  # small crop upscaled
    # where the dark room sits in the sent image, in 0-1000, maps back to the room on the page
    ys, xs = np.where(sent[:, :, 0] < 128)
    b1000 = [ys.min() / crop.sent_h * 1000, xs.min() / crop.sent_w * 1000,
             (ys.max() + 1) / crop.sent_h * 1000, (xs.max() + 1) / crop.sent_w * 1000]  # fmt: skip
    assert crop.box_to_page(b1000) == pytest.approx(room, abs=1.0)
    assert padded[0] < room[0] and padded[3] > room[3]


def test_crop_clipped_at_the_page_edge_keeps_the_offset():
    img = np.full((300, 300, 3), 255, np.uint8)
    _, crop, padded = images.crop_image(img, [0, 0, 50, 50])
    assert padded[:2] == [0, 0] and (crop.page_x0, crop.page_y0) == (0.0, 0.0)


def test_page_image_is_downscaled_never_upscaled():
    big = np.zeros((2000, 3072, 3), np.uint8)
    _, crop = images.page_image(big, max_side=1536)
    assert crop.scale == 0.5 and (crop.sent_w, crop.sent_h) == (1536, 1000)
    assert crop.box_to_page([0, 0, 1000, 1000]) == [0.0, 0.0, 3072.0, 2000.0]
    _, small = images.page_image(np.zeros((100, 100, 3), np.uint8))
    assert small.scale == 1.0


# ------------------------------------------------------------------ hold-out guard


@pytest.mark.parametrize(
    "source",
    ["data/cubicasa5k/held.png", "held.png", "held", r"C:\x\data\cubicasa5k\HELD.PNG", "held.jpg",
     "data/swiss_dwellings/plan_7.dxf", "Plan_7"],
)  # fmt: skip
def test_holdout_refused_by_path_basename_or_stem_before_any_network(guard, tmp_path, source):
    opener_called = []
    c = _client(guard, tmp_path, GeminiClient, opener=lambda *a, **k: opener_called.append(1))
    with pytest.raises(HoldoutError):
        c.check(_png(), "shower", "p", source=source)
    assert not opener_called and not (tmp_path / "vlm").exists()


def test_guard_never_reads_holdout_files(tmp_path, monkeypatch):
    """The guard is built from names only; the loaders refuse before opening anything."""
    import translator.store  # noqa: F401  (jsonschema reads its own files on import)

    opened = []
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def spy(real):
        def f(self, *a, **k):
            if "held" in str(self):
                opened.append(str(self))
            return real(self, *a, **k)

        return f

    monkeypatch.setattr(cv2, "imread", lambda p, *a: opened.append(p))
    monkeypatch.setattr(Path, "read_text", spy(real_text))
    monkeypatch.setattr(Path, "read_bytes", spy(real_bytes))
    monkeypatch.setattr(base, "_GUARD", HoldoutGuard({"data/cubicasa5k/held.png"}))
    with pytest.raises(HoldoutError):
        images.load_page("data/cubicasa5k/held.png", tmp_path)
    with pytest.raises(HoldoutError):
        gate.load_doc("data/cubicasa5k/held.png", tmp_path)
    assert opened == []
    assert not any(hasattr(base, n) for n in ("pixel_hash", "file_hashes"))


def test_real_holdout_list_is_enforced():
    from translator.extract.cubicasa import holdout_files

    with pytest.raises(HoldoutError):
        base.guard_source(sorted(holdout_files())[0])


def test_every_input_needs_a_source(guard, tmp_path):
    with pytest.raises(HoldoutError, match="source"):
        _client(guard, tmp_path).check(_png(), "shower", "p", source="")


def test_dev_input_goes_through(guard, tmp_path):
    c = _client(
        guard, tmp_path, answers={"shower": {"count": 2, "boxes": [[0, 0, 500, 500], [500, 500, 1000, 1000]]}}
    )
    r = c.check(_png(), "shower", "p", source="data/cubicasa5k/dev.png", crop=Crop(10, 20, 1.0, 60, 40))
    assert r.count == 2 and r.boxes_px[0] == [10.0, 20.0, 40.0, 40.0] and r.model_name == "fake"


# ------------------------------------------------------------------ cache


def test_cache_hit_never_sends_twice_and_prompt_change_misses(guard, tmp_path):
    c = _client(guard, tmp_path, answers={"shower": {"count": 1, "boxes": []}})
    img = _png(seed=1)
    a = c.check(img, "shower", "prompt v1", source="data/cubicasa5k/dev.png")
    b = c.check(img, "shower", "prompt v1", source="data/cubicasa5k/dev.png")
    assert not a.cached and b.cached and len(c.calls) == 1
    # a fresh client (new process) also hits the cache file
    c2 = _client(guard, tmp_path, answers={"shower": {"count": 1, "boxes": []}})
    assert c2.check(img, "shower", "prompt v1", source="data/cubicasa5k/dev.png").cached and c2.calls == []
    c.check(img, "shower", "prompt v2", source="data/cubicasa5k/dev.png")  # changed prompt: new key
    assert len(c.calls) == 2
    keys = {p.stem for p in (tmp_path / "vlm").glob("*.json")}
    assert len(keys) == 2


def test_cache_key_covers_image_category_prompt_model_and_settings():
    k = base.cache_key(b"img", "shower", "p", "m", {"t": 1})
    assert k != base.cache_key(b"img2", "shower", "p", "m", {"t": 1})
    assert k != base.cache_key(b"img", "kitchen", "p", "m", {"t": 1})
    assert k != base.cache_key(b"img", "shower", "p2", "m", {"t": 1})
    assert k != base.cache_key(b"img", "shower", "p", "m2", {"t": 1})
    assert k != base.cache_key(b"img", "shower", "p", "m", {"t": 2})
    assert GeminiClient().settings["thinking_level"] == config.THINKING_LEVEL == "low"
    assert GeminiClient(thinking_level=None).settings != GeminiClient().settings


# ------------------------------------------------------------------ budget and cost


def test_cost_counts_thinking_tokens_as_output():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "thinking_tokens": 100_000}
    p = config.PRICES_USD_PER_MTOK[config.GEMINI_MODEL]
    assert base.cost_usd(config.GEMINI_MODEL, usage) == pytest.approx(p["input"] + 0.2 * p["output"])


def test_budget_guard_aborts_the_run(guard, tmp_path, monkeypatch):
    monkeypatch.setitem(config.PRICES_USD_PER_MTOK, "fake", {"input": 1000.0, "output": 0.0})
    c = _client(guard, tmp_path, budget=Budget(cap_usd=1.0), tokens=(600, 0, 0))  # $0.60 per call
    c.check(_png(seed=1), "shower", "p", source="data/cubicasa5k/a.png")
    with pytest.raises(BudgetExceeded):
        c.check(_png(seed=2), "shower", "p", source="data/cubicasa5k/b.png")  # spent $1.20 > $1.00
    with pytest.raises(BudgetExceeded):
        c.check(_png(seed=3), "shower", "p", source="data/cubicasa5k/c.png")  # refused before the call
    assert len(c.calls) == 2


def test_budget_cap_above_hard_max_needs_approval():
    with pytest.raises(ValueError, match="approval"):
        Budget(cap_usd=2.0)
    assert Budget(cap_usd=2.0, approved_over_hard_max=True).cap_usd == 2.0
    assert Budget().cap_usd == config.BUDGET_CAP_USD <= config.BUDGET_HARD_MAX_USD


# ------------------------------------------------------------------ interface swap


def test_client_chosen_by_env(monkeypatch):
    assert isinstance(base.get_client(), FakeClient)
    monkeypatch.setenv("STOA_VLM", "gemini")
    assert isinstance(base.get_client(), GeminiClient)
    monkeypatch.setenv("STOA_VLM", "qwen")
    q = base.get_client()
    assert isinstance(q, QwenClient)
    with pytest.raises(VLMError, match="stub"):  # NotImplementedError, re-raised scrubbed
        q.check(_png(), "shower", "p", source="data/cubicasa5k/dev.png")


def test_qwen_stub_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="Qwen3-VL"):
        QwenClient()._call(b"", "p", "shower")


def test_only_translator_vlm_talks_to_the_gemini_api():
    """In our code (translator/, eval/, tests/) only translator/vlm/gemini.py names the Gemini API or SDK.

    answering_agent/ (the teammate's agent) uses the google-genai SDK on its own; it is not translator code.
    """
    root = Path(__file__).resolve().parent.parent
    files = [p for d in ("translator", "eval", "tests") for p in (root / d).rglob("*.py")]
    needles = ("generativelanguage", "google.genai", "from google import")
    hits = {p for p in files if any(n in p.read_text(encoding="utf-8", errors="ignore") for n in needles)}
    assert hits <= {root / "translator" / "vlm" / "gemini.py", Path(__file__).resolve()}


# ------------------------------------------------------------------ the key never leaks


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_key_never_reaches_cache_logs_or_errors(guard, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(config.API_KEY_ENV, SECRET)
    seen = {}

    def ok_opener(req, timeout):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        body = {
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps({"count": 1, "boxes": []})}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 3},
            "echo": SECRET,
        }  # a response that (absurdly) echoes the key must not get it into the cache
        return _Resp(json.dumps(body).encode())  # fmt: skip

    c = _client(guard, tmp_path, GeminiClient, opener=ok_opener)
    r = c.check(_png(), "shower", "p", source="data/cubicasa5k/dev.png")
    assert r.count == 1 and SECRET not in seen["url"] and seen["headers"]["X-goog-api-key"] == SECRET
    assert r.usage == {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 3}

    def bad_opener(req, timeout):
        raise OSError(f"connection reset for {req.full_url}?key={SECRET}")

    c2 = _client(guard, tmp_path, GeminiClient, opener=bad_opener)
    with pytest.raises(VLMError) as e:
        c2.check(_png(seed=5), "shower", "p", source="data/cubicasa5k/dev.png")
    assert SECRET not in str(e.value) and "***" in str(e.value)
    assert (
        e.value.__cause__ is None and e.value.__context__ is None or SECRET not in repr(e.value.__context__)
    )
    files = list((tmp_path / "vlm").rglob("*"))
    assert files and all(SECRET not in f.read_text(encoding="utf-8") for f in files if f.is_file())
    assert SECRET not in caplog.text


def test_missing_key_stops(monkeypatch, guard, tmp_path):
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    c = _client(guard, tmp_path, GeminiClient, opener=lambda *a, **k: pytest.fail("network"))
    with pytest.raises(VLMError, match="not set"):
        c.check(_png(), "shower", "p", source="data/cubicasa5k/dev.png")


def test_one_retry_with_backoff_then_success_and_latency_logged(guard, tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv(config.API_KEY_ENV, SECRET)
    attempts = []

    def flaky(req, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise TimeoutError("The read operation timed out")
        body = {"candidates": [{"content": {"parts": [{"text": '{"count": 0, "boxes": []}'}]}}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}}  # fmt: skip
        return _Resp(json.dumps(body).encode())

    caplog.set_level(logging.INFO, logger="translator.vlm")
    c = _client(guard, tmp_path, GeminiClient, opener=flaky)
    r = c.check(_png(), "shower", "p", source="data/x/dev.png")
    assert r.count == 0 and len(attempts) == 2 and attempts[0] == gemini.TIMEOUT_S == 300
    assert "failed after" in caplog.text and "(attempt 2)" in caplog.text and SECRET not in caplog.text


def test_gives_up_after_one_retry(guard, tmp_path, monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV, SECRET)
    attempts = []

    def down(req, timeout):
        attempts.append(1)
        raise TimeoutError("timed out")

    c = _client(guard, tmp_path, GeminiClient, opener=down)
    with pytest.raises(VLMError, match="TimeoutError"):
        c.check(_png(), "shower", "p", source="data/x/dev.png")
    assert len(attempts) == 2


# ------------------------------------------------------------------ gates


def _room(rid, rtype, box, flags=(), extractor="cubicasa5k", seg_type=None, subtype=None):
    x0, y0, x1, y1 = box
    return {"id": rid, "type": rtype, "subtype": subtype, "flags": list(flags), "status": "active",
            "polygon_px": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "confidence": 0.9,
            "provenance": {"extractor": extractor, "source_ref": rid,
                           "details": {"seg_type": seg_type} if seg_type else {}}}  # fmt: skip


def _gdoc(rooms=(), openings=(), cov=None):
    cov = cov or {}
    return {"rooms": list(rooms), "openings": list(openings),
            "coverage": [{"category": k, "region": "page", "status": v[0], "confidence": v[1], "note": v[2],
                          "extractor": "x"} for k, v in cov.items()]}  # fmt: skip


def _door(kind):
    return {"id": kind, "kind": kind, "status": "active", "confidence": 0.9}


def test_gates_select_and_merge_per_category():
    doc = _gdoc(
        rooms=[_room("b", "bathroom", (0, 0, 10, 10)), _room("k", "kitchen", (20, 0, 40, 10))],
        openings=[_door("door"), _door("window")],
        cov={"bathrooms": ("partial", 0.2, "prior: assumed 1, not read"),
             "kitchens": ("analysed", 0.9, "detected 1"),
             "floors": ("partial", 0.3, "2 titles, 1 blob"), "doors": ("analysed", 0.5, ""),
             "windows": ("analysed", 0.9, "")},
    )  # fmt: skip
    g = gate.gates(doc)
    assert g == {"shower": ["shower"], "prior": ["bathroom"], "no_evidence": [], "floors": ["floors"],
                 "win_door": ["doors"]}  # fmt: skip
    assert gate.tasks(doc) == [("shower", "shower"), ("bathroom", "prior"), ("floors", "floors"),
                               ("doors", "win_door")]  # fmt: skip


def test_no_evidence_and_prior_for_the_same_category_send_one_task():
    prior_bath = {**_room("p", "bathroom", (0, 0, 1, 1), flags=["assumed_prior"], extractor="prior"),
                  "polygon_px": None}  # fmt: skip
    doc = _gdoc(rooms=[prior_bath], cov={"bathrooms": ("partial", 0.2, "prior: assumed 1")})
    g = gate.gates(doc)
    assert "bathroom" in g["prior"] and "bathroom" in g["no_evidence"] and g["shower"] == []
    cats = [c for c, _ in gate.tasks(doc)]
    assert cats.count("bathroom") == 1 and set(cats) == {"bathroom", "kitchen", "doors", "windows", "rooms"}


def test_wet_regions_by_label_or_segmentation_not_label_boxes():
    rooms = [
        _room("wc", "wc", (0, 0, 10, 10)),
        _room("seg", "unknown", (20, 0, 30, 10), seg_type="bathroom", flags=["unlabelled_region"]),
        _room("bath", "unknown", (40, 0, 50, 10), subtype="Bath"),
        _room("lbl", "sauna", (60, 0, 62, 2), flags=["label_only"]),  # text box, not a region
        _room("bed", "bedroom", (80, 0, 90, 10)),
    ]
    assert [r["id"] for r in gate.wet_regions(_gdoc(rooms=rooms))] == ["wc", "seg", "bath"]


def test_overlapping_wet_crops_are_merged():
    rooms = [_room("a", "bathroom", (100, 100, 200, 200)), _room("b", "wc", (205, 100, 260, 200)),
             _room("c", "sauna", (600, 600, 700, 700))]  # fmt: skip
    boxes = shower.crop_boxes(_gdoc(rooms=rooms), 1000, 1000)
    assert sorted(len(b["rooms"]) for b in boxes) == [1, 2]


def test_crop_variant_sums_counts_and_drops_cross_crop_duplicates(guard, tmp_path):
    img = np.full((1000, 1000, 3), 255, np.uint8)
    rooms = [_room("a", "bathroom", (100, 100, 200, 200)), _room("c", "sauna", (600, 600, 700, 700))]
    c = _client(guard, tmp_path, answers={"shower": {"count": 1, "boxes": [[400, 400, 600, 600]]}})
    res = shower.count_crops(c, img, _gdoc(rooms=rooms), "data/cubicasa5k/dev.png")
    assert res.count == 2 and len(res.calls) == 2 and len(res.boxes_px) == 2


def test_ledger_totals_include_estimates(tmp_path, monkeypatch):
    from translator.vlm import ledger

    monkeypatch.setattr(ledger, "cache_dir", lambda: tmp_path)
    ledger.append_run("a", calls=10, cost_usd=0.05, images_sent=5)
    ledger.add_estimate("timed-out call, possibly billed", 0.025)
    data = ledger.load()
    assert data["totals"] == {"logged_usd": 0.05, "estimated_unlogged_usd": 0.025, "total_usd": 0.075}
    assert data["runs"][0]["avg_cost_per_gated_image"] == 0.01
    assert data["runs"][0]["avg_cost_per_dev_image"] == round(0.05 / 193, 6)
