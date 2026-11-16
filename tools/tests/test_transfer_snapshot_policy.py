"""Telegram -> Telegram transfer: "Videos without saved snapshots" (snapshot_policy).
existing (default): a video with no saved 36-frame set is skipped as NEEDS_SNAPSHOTS and stays in the source; nothing is made from Telegram,
and the preparation does not run its snapshots stage. generate: the previous behaviour."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axpol_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def _router():
    from telegram_archive.media_manager import runner  # noqa: F401  (loads the exec()'d media manager parts)
    from telegram_archive.media_manager import telegram_media_manager as tmm
    return tmm


def test_saved_snapshots_only_skips_a_video_without_a_set_in_one_look(monkeypatch):
    tmm = _router()
    ns = tmm.__dict__
    calls = []

    async def none_saved(*a, **k):
        calls.append(k.get("generate_fallback"))
        return None, None, None, None

    monkeypatch.setitem(ns, "_select_manager_thumbnail", none_saved)
    retry = ns["_select_manager_thumbnail_with_retry"]
    try:
        asyncio.run(retry(None, None, None, "x", generate_fallback=False))
    except ns["ContactSheetUnavailable"] as exc:
        assert "no saved 36-frame snapshot set" in str(exc) and "skip" in str(exc)
    else:
        raise AssertionError("a video without saved snapshots must raise ContactSheetUnavailable")
    assert calls == [False]                      # one look, nothing generated, no second attempt


def test_generate_keeps_the_two_attempts(monkeypatch):
    tmm = _router()
    ns = tmm.__dict__
    calls = []

    async def fails(*a, **k):
        calls.append(k.get("generate_fallback"))
        raise RuntimeError("boom")

    monkeypatch.setitem(ns, "_select_manager_thumbnail", fails)
    try:
        asyncio.run(ns["_select_manager_thumbnail_with_retry"](None, None, None, "x", generate_fallback=True))
    except ns["ContactSheetUnavailable"] as exc:
        assert "failed twice" in str(exc)
    assert calls == [True, True]


def test_the_start_route_validates_and_stores_the_policy():
    import inspect
    from telegram_archive.web import routes_jobs
    src = inspect.getsource(routes_jobs)
    assert '"snapshot_policy": snapshot_policy' in src
    assert 'snapshot_policy not in {"existing", "generate"}' in src
