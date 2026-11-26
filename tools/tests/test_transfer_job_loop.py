"""The transfer job's loop around the batched finish: which videos are counted, when groups are finished, what a cancel leaves, and the one-at-a-time switch."""
import asyncio
import tempfile
import os

import pytest

import transfer_harness as t
from telegram_archive.engines import telegram_sender
from telegram_archive.engines.transfer_timing import TIMER
from telegram_archive.media_manager import telegram_media_manager as tmm


@pytest.fixture(autouse=True)
def fresh_state():
    telegram_sender.CoverReuse.reset()
    TIMER.reset()
    yield
    telegram_sender.CoverReuse.reset()


class JobWorld:
    def __init__(self, monkeypatch, n):
        self.client = t.FakeClient()
        self.rec = t.install(monkeypatch)
        self.state = t.new_state()
        self.messages = [self.client.add_source_video(f"scene {i}.mp4") for i in range(1, n + 1)]
        self.logs = []
        client = self.client
        client.disconnect = lambda: asyncio.sleep(0)

        async def login(config):
            return client

        async def resolve(c, cid):
            return t.peer(int(cid))

        async def scan(c, source, source_mode, orientation, state=None):
            return list(self.messages)

        async def nothing(*a, **k):
            return None

        async def no_duplicates(c, destination):
            return {}
        monkeypatch.setattr(tmm, "STARTUP_IMPORT_ERRORS", [])
        monkeypatch.setattr(tmm, "transfer_job_state_load", lambda job_id=None, resume=False: self.state)
        monkeypatch.setattr(tmm, "transfer_job_state_save", lambda state: None)
        monkeypatch.setattr(tmm, "load_json", lambda path, default=None: default if default is not None else {})
        monkeypatch.setattr(tmm, "login_and_get_client", login)
        monkeypatch.setattr(tmm, "resolve_saved_entity", resolve)
        monkeypatch.setattr(tmm, "transfer_scan_candidates", scan)
        monkeypatch.setattr(tmm, "transfer_prefetch_metadata", nothing)
        monkeypatch.setattr(tmm, "build_transfer_destination_duplicate_index", no_duplicates)

        async def prepared(*a, **k):
            return "child-job"                                                        # the Metadata & Assets preparation is not what is tested here
        monkeypatch.setattr(tmm, "_run_metadata_child_job", prepared)

    def detail(self, **extra):
        return {"workflow": "PROCESS", "source_channels": [t.SOURCE_ID], "destination_channel": str(t.DEST_ID), "source_mode": "VIDEO",
                "destination_mode": "VIDEO", "orientation": "BOTH", "duplicate_check": {"destination": False, "database": False},
                "snapshot_policy": "existing", **extra}

    def run(self, **extra):
        return t.run(tmm.telegram_transfer_job_main(job_id="job1", job_detail=self.detail(**extra)))

    def in_source(self):
        return [m.id for m in self.messages if m.id in self.client.chats[t.SOURCE_ID].messages]


def test_a_job_moves_every_video_in_groups_and_counts_them_once(monkeypatch, capsys):
    w = JobWorld(monkeypatch, 7)
    w.run(delivery_batch_size=3)
    assert w.in_source() == []
    assert all(t.entry(w.state, m)["status"] == "COMPLETE" for m in w.messages)
    deletes = [len(c[2]) for c in w.client.calls("delete_messages", t.SOURCE_ID)]
    assert deletes == [3, 3, 1]                                                          # groups of 3, and the one left at the end of the source
    assert TIMER.items == 7 and TIMER.failed == 0 and TIMER.batches == 3
    out = capsys.readouterr().out
    assert "Completed items     : 7" in out and "Failed items        : 0" in out
    assert "groups of up to 3" in out


def test_a_group_size_of_one_is_the_old_one_at_a_time_behaviour(monkeypatch, capsys):
    w = JobWorld(monkeypatch, 4)
    w.run(delivery_batch_size=1)
    assert [len(c[2]) for c in w.client.calls("delete_messages", t.SOURCE_ID)] == [1, 1, 1, 1]
    assert TIMER.items == 4 and "one transfer item at a time" in capsys.readouterr().out


def test_one_video_that_fails_is_counted_failed_and_the_others_complete(monkeypatch, capsys):
    w = JobWorld(monkeypatch, 6)
    bad = w.messages[2].id
    original = w.client.send_file
    # the third video's picture never shows up in the destination
    posts = {"n": 0}

    async def drop_third_picture(entity, file, **kw):
        out = await original(entity, file, **kw)
        if out.video is None:
            posts["n"] += 1
            if posts["n"] == 3:
                w.client.chats[t.DEST_ID].messages.pop(out.id)
        return out
    w.client.send_file = drop_third_picture
    w.run(delivery_batch_size=4)
    assert w.in_source() == [bad]                                                       # only that one stays
    out = capsys.readouterr().out
    assert "Completed items     : 5" in out and "Failed items        : 1" in out
    assert TIMER.items == 5 and TIMER.failed == 1


def test_a_video_that_cannot_be_posted_is_counted_failed_and_does_not_stop_the_group(monkeypatch, capsys):
    w = JobWorld(monkeypatch, 5)
    async def no_sleep(s):
        pass
    monkeypatch.setattr(tmm.asyncio, "sleep", no_sleep)
    w.client.fail["send_file"] = [None, None] + [RuntimeError("CHAT_WRITE_FORBIDDEN")] * 3     # video 1 and its picture go; video 2's copy fails all 3 attempts
    w.run(delivery_batch_size=5)
    out = capsys.readouterr().out
    assert "Failed items        : 1" in out and "Completed items     : 4" in out
    assert len(w.in_source()) == 1


def test_a_cancel_leaves_posted_copies_and_untouched_sources_and_the_next_run_finishes_them(monkeypatch):
    w = JobWorld(monkeypatch, 5)
    original = w.client.send_file
    posted_videos = {"n": 0}

    async def cancel_on_fourth(entity, file, **kw):
        out = await original(entity, file, **kw)
        if out.video is not None:
            posted_videos["n"] += 1
        return out

    async def cancel_during_fourth_checkpoint():
        calls = {"n": 0}
        real = tmm.transfer_control_checkpoint

        async def checkpoint():
            calls["n"] += 1
            if calls["n"] == 4:
                raise asyncio.CancelledError()
            await real()
        monkeypatch.setattr(tmm, "transfer_control_checkpoint", checkpoint)
        await tmm.telegram_transfer_job_main(job_id="job1", job_detail=w.detail(delivery_batch_size=10))
    w.client.send_file = cancel_on_fourth
    before = {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("tg_media_mover_")}
    with pytest.raises(asyncio.CancelledError):
        t.run(cancel_during_fourth_checkpoint())
    posted = posted_videos["n"]
    assert 0 < posted < 5 and len(w.in_source()) == 5                                    # some were posted, none was deleted
    assert sum(1 for e in w.state["items"].values() if e.get("status") == "PAIR_COPIED") == posted
    assert {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("tg_media_mover_")} == before      # and no working folders left behind
    monkeypatch.undo()
    w2_state = w.state
    mp = pytest.MonkeyPatch()                                                            # the next run: the saved state, a fresh job
    t.install(mp)
    mp.setattr(tmm, "STARTUP_IMPORT_ERRORS", [])
    mp.setattr(tmm, "transfer_job_state_load", lambda job_id=None, resume=False: w2_state)
    mp.setattr(tmm, "transfer_job_state_save", lambda state: None)
    mp.setattr(tmm, "load_json", lambda path, default=None: default if default is not None else {})

    async def login(config):
        return w.client

    async def resolve(c, cid):
        return t.peer(int(cid))

    async def scan(c, source, source_mode, orientation, state=None):
        return list(w.messages)

    async def nothing(*a, **k):
        return None

    async def no_dups(c, d):
        return {}
    async def prepared(*a, **k):
        return "child-job"
    for name, fn in (("login_and_get_client", login), ("resolve_saved_entity", resolve), ("transfer_scan_candidates", scan),
                     ("transfer_prefetch_metadata", nothing), ("build_transfer_destination_duplicate_index", no_dups), ("_run_metadata_child_job", prepared)):
        mp.setattr(tmm, name, fn)
    try:
        t.run(tmm.telegram_transfer_job_main(job_id="job1", job_detail=w.detail(delivery_batch_size=10)))
    finally:
        mp.undo()
    assert w.in_source() == []
    assert len([p for p in w.client.posts if p[2] == "video"]) == 5                       # the ones already posted were not posted again
