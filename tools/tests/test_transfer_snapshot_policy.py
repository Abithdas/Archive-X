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


def test_saved_snapshots_only_composes_the_sheet_from_the_36_saved_frames(tmp_path, monkeypatch):
    """A video in the Library has its 36 frames but never had the one-picture sheet made: "saved snapshots only" must build it from the frames
    (no Telegram download) instead of skipping the video as "needs snapshots"."""
    import random
    from PIL import Image, ImageDraw
    from telegram_archive import config
    from telegram_archive.core.database import Database
    from telegram_archive.engines import preview_prefetch as pp, thumbnail_cache as tc

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    db = Database(tmp_path / "t.db")
    cid, mid = -1001234567890, 42
    folder = pp.preview_frames_path(cid, mid)
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1)
    for i in range(1, 37):
        im = Image.new("RGB", (320, 180), (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
        d = ImageDraw.Draw(im)
        for _ in range(40):
            x, y = rng.randrange(280), rng.randrange(140)
            d.rectangle([x, y, x + rng.randrange(1, 40), y + rng.randrange(1, 40)], fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
        im.save(folder / f"frame_{i:02d}.jpg", quality=92)
    if not pp.frame_quality_sync(folder).get("ready"):
        import pytest
        pytest.skip("the generated test frames do not pass the snapshot quality check on this machine")
    assert tc.playback_snapshot_sheet_cached_sync(db, cid, mid) is None          # no sheet has been made yet
    sheet = tc.playback_snapshot_sheet_from_saved_frames_sync(db, cid, mid)      # made now, from the frames
    assert sheet and sheet[1] == "image/jpeg" and len(sheet[0]) > 512
    assert tc.playback_snapshot_sheet_cached_sync(db, cid, mid) is not None      # and kept for next time


def test_saved_snapshots_only_still_says_no_when_the_frames_are_missing(tmp_path, monkeypatch):
    from telegram_archive import config
    from telegram_archive.core.database import Database
    from telegram_archive.engines import thumbnail_cache as tc
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    assert tc.playback_snapshot_sheet_from_saved_frames_sync(Database(tmp_path / "t.db"), -1001234567890, 7) is None
