"""The metadata prefetch of a transfer must not write the whole job state after every video (about 50 ms each at 15,000 videos)."""
from __future__ import annotations

import pytest

from telegram_archive.media_manager import telegram_media_manager as tmm
from tools.tests import transfer_harness as t


def test_the_saver_writes_every_n_items_and_on_flush():
    saved = []
    now = {"t": 0.0}
    saver = tmm.PrefetchSaver(lambda: saved.append(1), every_items=3, every_seconds=100.0, clock=lambda: now["t"])
    for _ in range(7):
        saver.item_done()
    assert len(saved) == 2          # after 3 and after 6
    saver.flush()
    assert len(saved) == 3          # the seventh is not lost
    saver.flush()
    assert len(saved) == 3          # nothing pending: nothing written


def test_the_saver_also_writes_when_time_has_passed():
    saved = []
    now = {"t": 0.0}
    saver = tmm.PrefetchSaver(lambda: saved.append(1), every_items=1000, every_seconds=5.0, clock=lambda: now["t"])
    saver.item_done()
    assert saved == []
    now["t"] = 6.0
    saver.item_done()
    assert len(saved) == 1


def test_prefetching_450_videos_saves_a_handful_of_times_and_keeps_every_result(monkeypatch):
    saves = []
    monkeypatch.setattr(tmm, "transfer_job_state_save", lambda state: saves.append(len(state.get("metadata_prefetch", {}))))
    monkeypatch.setattr(tmm, "transfer_control_checkpoint", lambda: _done())
    messages = [t.video_message(t.SOURCE_ID, i, name=f"v{i}.mp4") for i in range(1, 451)]
    state = {}
    t.run(tmm.transfer_prefetch_metadata(None, t.peer(t.SOURCE_ID), messages, state, db=None))
    assert len(state["metadata_prefetch"]) == 450
    assert all(v["status"] == "READY" for v in state["metadata_prefetch"].values())
    # 450 videos = 2 saves of 200 + the final flush + the summary save; it used to be 450+
    assert 2 <= len(saves) <= 6, saves
    assert saves[-1] == 450


def test_a_cancel_still_keeps_what_was_prepared(monkeypatch):
    import asyncio
    saves = []
    monkeypatch.setattr(tmm, "transfer_job_state_save", lambda state: saves.append(len(state.get("metadata_prefetch", {}))))
    calls = {"n": 0}

    async def checkpoint():
        calls["n"] += 1
        if calls["n"] > 12:
            raise asyncio.CancelledError()
    monkeypatch.setattr(tmm, "transfer_control_checkpoint", checkpoint)
    messages = [t.video_message(t.SOURCE_ID, i, name=f"v{i}.mp4") for i in range(1, 40)]
    state = {}
    with pytest.raises(asyncio.CancelledError):
        t.run(tmm.transfer_prefetch_metadata(None, t.peer(t.SOURCE_ID), messages, state, db=None))
    assert saves and saves[-1] == len(state["metadata_prefetch"]) > 0


async def _done():
    return None
