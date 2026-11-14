"""CLI: hold-out files are refused by default (translator/vlm filename guard); --allow-holdout overrides."""

from __future__ import annotations

import json

import pytest

import translator.__main__ as cli
from translator.vlm import base
from translator.vlm.base import HoldoutGuard

HELD = "data/swiss_dwellings/held_floor.pdf"
DEV = "data/swiss_dwellings/dev_floor.pdf"


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(base, "_GUARD", HoldoutGuard({HELD}))
    monkeypatch.setattr(cli, "translate_all", lambda p, data=None, allow_holdout=False: seen.append(p) or [])
    return seen


def test_holdout_path_is_refused_before_anything_is_read(calls, capsys):
    assert cli.main([HELD]) == 2
    assert calls == [] and "refused" in capsys.readouterr().err
    assert cli.main(["data/swiss_dwellings/held_floor.dxf"]) == 2  # same stem, other extension
    assert calls == []


def test_allow_holdout_overrides_with_a_warning(calls, capsys):
    assert cli.main([HELD, "--allow-holdout"]) == 0
    assert calls == [HELD] and "WARNING" in capsys.readouterr().err


def test_dev_path_goes_through(calls):
    assert cli.main([DEV]) == 0 and calls == [DEV]


def test_source_batch_skips_holdout_files(calls, tmp_path, monkeypatch, capsys):
    (tmp_path / "questions.jsonl").write_text(
        "\n".join(json.dumps({"path": p}) for p in (HELD, DEV, "data/cubicasa5k/x.png")), encoding="utf-8"
    )
    monkeypatch.setattr("translator.store.write_drawing", lambda doc: "x")
    assert cli.main(["--source", "swiss_dwellings", "--data", str(tmp_path)]) == 0
    assert calls == [DEV] and "skipped 1 hold-out" in capsys.readouterr().err
    calls.clear()
    assert cli.main(["--source", "swiss_dwellings", "--data", str(tmp_path), "--allow-holdout"]) == 0
    assert sorted(calls) == sorted([HELD, DEV])


# ---------------------------------------------------- the library guard (translate / translate_all)


def test_translate_all_refuses_holdout_before_reading(monkeypatch):
    import translator
    from translator import router

    monkeypatch.setattr(base, "_GUARD", HoldoutGuard({HELD}))
    touched = []
    monkeypatch.setattr(router, "route", lambda *a, **k: touched.append(a) or pytest.fail("router reached"))
    with pytest.raises(base.HoldoutError):
        translator.translate_all(HELD)
    with pytest.raises(base.HoldoutError):
        translator.translate("data/swiss_dwellings/held_floor.dxf")
    assert touched == []


def test_translate_all_allow_holdout_overrides_with_a_warning(monkeypatch):
    import translator
    from translator import router

    monkeypatch.setattr(base, "_GUARD", HoldoutGuard({HELD}))

    class Reached(Exception):
        pass

    def fake_route(*a, **k):
        raise Reached

    monkeypatch.setattr(router, "route", fake_route)
    with pytest.warns(UserWarning, match="hold-out"), pytest.raises(Reached):
        translator.translate_all(HELD, allow_holdout=True)
