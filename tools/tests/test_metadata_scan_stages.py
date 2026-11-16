"""Metadata channel scan, the stage runner: each stage alone and combined, a failure in each stage, cancels at every point, resuming."""
import asyncio
import json

import pytest

import metadata_scan_harness as h

ALL = ["inventory", "metadata", "performers", "channel_data", "snapshots"]


def channel_with_videos(n=4):
    return h.new_channel(), h.FakeHistory({i: h.video(i) for i in range(1, n + 1)})


def test_every_stage_together_completes_and_the_videos_reach_the_library(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos()
    eng = h.Engines(db)
    ctx, payload = h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=eng)
    assert payload["result_status"] == "COMPLETED", payload["channel_queue"][0].get("error")
    assert set(h.phases(payload).values()) == {"COMPLETED"}
    assert set(eng.keys()) == {"metadata", "thumbnails", "performers", "studios"}
    assert eng.keys().index("metadata") < eng.keys().index("performers") < eng.keys().index("studios")      # entities follow metadata
    assert payload["overall_in_library"] == 4 and payload["overall_not_in_library"] == 0


@pytest.mark.parametrize("stage,expected_engine_calls", [
    ("inventory", []),
    ("metadata", ["metadata"]),
    ("performers", None),          # runs, whatever is linked (the Unknown placeholders count; the real engine skips them)
    ("channel_data", None),
    ("snapshots", ["thumbnails"]),
])
def test_each_stage_on_its_own_runs_cleanly(monkeypatch, stage, expected_engine_calls):
    db = h.fresh_db()
    ch, hist = channel_with_videos()
    eng = h.Engines(db)
    # the channel is already inventoried, so stages that need videos have them
    h.job(db, monkeypatch, stages=["inventory"], channels=[ch], history=hist, engines=eng)
    eng.calls.clear()
    ctx, payload = h.job(db, monkeypatch, stages=[stage], channels=[ch], history=hist, engines=eng)
    if expected_engine_calls is not None:
        assert eng.keys() == expected_engine_calls
    assert h.phases(payload).get(stage) == "COMPLETED", payload["channel_queue"][0]
    assert payload["channel_queue"][0].get("error", "") == ""


def test_performer_and_studio_stages_refresh_the_linked_entities_when_there_are_some(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos()
    eng = h.Engines(db)
    h.job(db, monkeypatch, stages=["inventory", "metadata"], channels=[ch], history=hist, engines=eng)
    eng.calls.clear()
    _, payload = h.job(db, monkeypatch, stages=["performers"], channels=[ch], engines=eng)
    assert eng.keys() == ["performers"] and h.phases(payload)["performers"] == "COMPLETED"
    eng.calls.clear()
    _, payload = h.job(db, monkeypatch, stages=["channel_data"], channels=[ch], engines=eng)
    assert eng.keys() == ["studios"] and h.phases(payload)["channel_data"] == "COMPLETED"


def test_a_snapshots_only_run_audits_every_stored_video_and_does_not_touch_entity_state(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(3)
    eng = h.Engines(db)
    h.install(monkeypatch, hist)
    h.run(h.pipe._discover_and_store_inventory(h.make_ctx(db), ch, 0, full_scan=True))      # stored videos, nothing else done to them yet
    _, payload = h.job(db, monkeypatch, stages=["snapshots"], channels=[ch], engines=eng)
    (key, detail), = eng.calls
    assert key == "thumbnails" and detail["scope"] == "missing" and detail["operator_channel_scan"] is True
    assert len(detail["message_ids"]) == 0 and detail["unbounded"] is True
    pending = db.conn.execute("SELECT COUNT(*) FROM resource_state rs JOIN media m ON m.id=rs.media_id WHERE m.source_channel=? AND rs.entity_state='COMPLETE'", (ch,)).fetchone()[0]
    assert pending == 0                                         # an audit must not finish entity work as a side effect


@pytest.mark.parametrize("stages", [["metadata", "performers"], ["inventory", "snapshots"], ["metadata", "snapshots"],
                                    ["performers", "channel_data"], ["inventory", "metadata", "channel_data"]])
def test_stage_combinations_run_cleanly(monkeypatch, stages):
    db = h.fresh_db()
    ch, hist = channel_with_videos()
    eng = h.Engines(db)
    h.job(db, monkeypatch, stages=["inventory"], channels=[ch], history=hist, engines=eng)
    _, payload = h.job(db, monkeypatch, stages=stages, channels=[ch], history=hist, engines=eng)
    assert payload["channel_queue"][0].get("error", "") == ""
    assert all(h.phases(payload).get(s) == "COMPLETED" for s in stages), h.phases(payload)


# ---------------------------------------------------------------- a failure in each stage -----------------------------------------------------
@pytest.mark.parametrize("failing", ["metadata", "performers", "studios", "thumbnails"])
def test_a_failing_stage_fails_that_channel_cleanly_and_leaves_no_task_running(monkeypatch, failing):
    db = h.fresh_db()
    ch, hist = channel_with_videos()
    eng = h.Engines(db, fail={failing: RuntimeError(f"{failing} broke")}, slow=0.2)
    ctx, payload = h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=eng)
    entry = payload["channel_queue"][0]
    assert entry["state"] == "FAILED" and "broke" in entry["error"]
    assert payload["result_status"] == "COMPLETED_WITH_ERRORS" and payload["overall_failed"] == 1
    if failing != "thumbnails":
        assert "thumbnails" in eng.cancelled or "thumbnails" in eng.finished     # the parallel snapshot work is not left running


def test_when_the_inventory_fails_nothing_else_runs_and_the_next_channel_still_does(monkeypatch):
    db = h.fresh_db()
    bad, good = h.new_channel(), h.new_channel()
    eng = h.Engines(db)

    class Hist(h.FakeHistory):
        async def __call__(self, client, channel, *a, **k):
            if str(getattr(channel, "marker", "")) == "bad" or self._bad:
                self._bad = False
                raise RuntimeError("cannot read this channel")
            return await super().__call__(client, channel, *a, **k)
    hist = Hist({i: h.video(i) for i in range(1, 4)})
    hist._bad = True
    ctx, payload = h.job(db, monkeypatch, stages=ALL, channels=[bad, good], history=hist, engines=eng)
    q = payload["channel_queue"]
    assert q[0]["state"] == "FAILED" and q[0]["phases"]["inventory"] == "FAILED"
    assert q[1]["state"] in {"COMPLETED", "INCOMPLETE"} and q[1]["phases"]["inventory"] == "COMPLETED"
    assert payload["overall_failed"] == 1 and payload["result_status"] == "COMPLETED_WITH_ERRORS"
    seen = [d.get("source_channels") or d.get("channels") for _, d in eng.calls if d.get("source_channels") or d.get("channels")]
    assert seen and all(c in ([good], [int(good)]) for c in seen)                      # nothing ran for the channel that failed


def test_a_telegram_scan_that_did_not_finish_makes_the_inventory_stage_incomplete_not_done(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos()
    eng = h.Engines(db)
    h.job(db, monkeypatch, stages=["inventory"], channels=[ch], history=hist, engines=eng)
    broken = h.FakeHistory(raises=RuntimeError("connection lost"))
    _, payload = h.job(db, monkeypatch, stages=["inventory"], channels=[ch], history=broken, engines=eng, detail={"full_inventory": True})
    assert h.phases(payload)["inventory"] == "INCOMPLETE" and payload["result_status"] == "COMPLETED_WITH_ERRORS"
    assert "did not finish" in payload["channel_queue"][0]["inventory_warning"]


def test_a_complete_scan_that_found_nothing_is_flagged_and_removes_nothing(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(6)
    eng = h.Engines(db)
    h.job(db, monkeypatch, stages=["inventory"], channels=[ch], history=hist, engines=eng)
    empty = h.FakeHistory({})
    _, payload = h.job(db, monkeypatch, stages=["inventory"], channels=[ch], history=empty, engines=eng, detail={"full_inventory": True})
    assert h.count(db, ch, " AND COALESCE(status,'')<>'SOURCE_DELETED'") == 6
    assert h.phases(payload)["inventory"] == "INCOMPLETE" and "nothing was removed" in payload["channel_queue"][0]["inventory_warning"]


# ---------------------------------------------------------------- empty and odd jobs ----------------------------------------------------------
def test_a_channel_with_no_videos_completes_with_nothing_to_do(monkeypatch):
    db = h.fresh_db()
    eng = h.Engines(db)
    _, payload = h.job(db, monkeypatch, stages=ALL, channels=[h.new_channel()], history=h.FakeHistory({}), engines=eng)
    assert payload["channel_queue"][0]["state"] == "COMPLETED" and eng.calls == [] and payload["result_status"] == "COMPLETED"


def test_a_job_with_no_channel_says_so(monkeypatch):
    db = h.fresh_db()
    with pytest.raises(RuntimeError, match="at least one source channel"):
        h.job(db, monkeypatch, stages=ALL, channels=[], engines=h.Engines(db))


def test_duplicate_and_blank_channels_are_collapsed(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(2)
    _, payload = h.job(db, monkeypatch, stages=["inventory"], channels=[ch, ch, "", "  ", ch], history=hist, engines=h.Engines(db))
    assert payload["channel_ids"] == [ch] and len(payload["channel_queue"]) == 1


def test_a_job_whose_saved_stages_are_all_unknown_is_refused_not_reported_as_done(monkeypatch):
    """An older or hand-made job can carry no valid stage. It used to finish as COMPLETED having done nothing."""
    db = h.fresh_db()
    ch, hist = channel_with_videos(2)
    for bad in ([], ["bogus"], ["", None]):
        with pytest.raises(RuntimeError, match="stage"):
            h.job(db, monkeypatch, stages=bad, channels=[ch], history=hist, engines=h.Engines(db))


def test_a_saved_queue_of_the_wrong_length_is_rebuilt(monkeypatch):
    db = h.fresh_db()
    a, b = h.new_channel(), h.new_channel()
    hist = h.FakeHistory({1: h.video(1)})
    eng = h.Engines(db)
    stale = {"channel_queue": [{"channel_id": "old", "state": "COMPLETED", "videos": [], "phases": {p: "COMPLETED" for p in ALL}}]}
    _, payload = h.job(db, monkeypatch, stages=["inventory"], channels=[a, b], history=hist, engines=eng, detail=stale)
    assert [e["channel_id"] for e in payload["channel_queue"]] == [a, b] and h.phases(payload, 0)["inventory"] == "COMPLETED"


def test_the_limit_bounds_a_bounded_run(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(10)
    eng = h.Engines(db)
    _, payload = h.job(db, monkeypatch, stages=["inventory", "snapshots"], channels=[ch], history=hist, engines=eng,
                       detail={"unbounded": False, "limit": 3})
    (key, detail), = eng.calls
    assert key == "thumbnails" and len(detail["message_ids"]) <= 3


@pytest.mark.parametrize("raw,expected", [(None, 50), ("x", 50), (0, 50), (-5, 50), (7, 7), (999999, 5000), ("12", 12)])
def test_limit_values(raw, expected):
    assert h.pipe._selected_limit({} if raw is None else {"limit": raw}) == expected
    assert h.pipe._selected_limit({"limit": raw, "unbounded": True}) == 0


def test_stage_names_and_aliases():
    sel = h.pipe._selected_stages
    assert sel({}) == list(h.pipe.PHASES) and sel({"stages": "everything"}) == list(h.pipe.PHASES)
    assert sel({"stages": ["Metadata_Match", "performer_data", "related_entities", "playback_snapshots", "metadata"]}) == ["metadata", "performers", "channel_data", "snapshots"]
    assert sel({"stages": ["bogus", "", None, 7]}) == []


# ---------------------------------------------------------------- cancel at every point, then resume --------------------------------------------
def _checkpoints_in_a_full_run(db, monkeypatch):
    ch, hist = channel_with_videos(3)
    ctx, _ = h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=h.Engines(db, slow=0.02))
    return ctx  # noqa: F841


@pytest.mark.parametrize("cancel_after", list(range(0, 14)))
def test_cancelling_anywhere_leaves_nothing_running_and_a_new_run_finishes_the_job(monkeypatch, cancel_after):
    db = h.fresh_db()
    ch, hist = channel_with_videos(3)
    first = h.Engines(db, slow=0.02)
    cancelled = False
    try:
        ctx, saved = h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=first, cancel_after=cancel_after)
    except h.JobCancelled:
        cancelled = True
        saved = json.loads(db.conn.execute("SELECT detail FROM jobs ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    if cancelled:
        # a cancelled job never claims a stage is done unless its engine really finished
        done_before = {p for p, st in (saved.get("channel_queue") or [{}])[0].get("phases", {}).items() if st == "COMPLETED"}
        keys = {"metadata": "metadata", "performers": "performers", "channel_data": "studios", "snapshots": "thumbnails"}
        for phase in done_before & set(keys):
            assert keys[phase] in first.finished or phase in ("performers", "channel_data"), (phase, first.finished)
    # the next run: same saved settings and progress
    resumed = h.Engines(db, slow=0.0)
    ctx2, payload = h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=resumed, detail=saved if cancelled else None)
    assert ctx2.leftover == []
    assert payload["result_status"] == "COMPLETED", payload["channel_queue"][0]
    assert set(h.phases(payload).values()) == {"COMPLETED"}
    if cancelled:
        for phase, key in (("metadata", "metadata"), ("snapshots", "thumbnails")):
            if key in first.finished and phase in done_before:
                assert key not in resumed.keys(), f"{phase} was done before the cancel but ran again"


def test_a_cancel_while_the_snapshot_work_runs_stops_it(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(2)
    eng = h.Engines(db, slow=5.0)             # snapshots would take 5 s; the cancel must not wait for them
    import time
    t = time.time()
    with pytest.raises(h.JobCancelled):
        h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=eng, cancel_after=6)
    assert time.time() - t < 3 and "thumbnails" in eng.cancelled


def test_a_resumed_job_does_not_repeat_finished_stages(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(3)
    eng = h.Engines(db)
    _, saved = h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=eng)
    again = h.Engines(db)
    _, payload = h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=again, detail=saved)
    assert again.calls == [] and payload["result_status"] == "COMPLETED"


# ---------------------------------------------------------------- the pending-only scan and caption sync --------------------------------------
def test_a_pending_scan_never_asks_telegram_and_only_works_on_videos_not_in_the_library(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(5)
    done = h.Engines(db)
    h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=done)       # everything is in the Library now
    assert h.count(db, ch) == 5
    # two videos fall out of the Library again (a snapshot set was lost)
    with db.lock:
        db.conn.execute("UPDATE resource_state SET snapshot_state='MISSING' WHERE media_id IN (SELECT id FROM media WHERE source_channel=? AND source_message_id IN (2,4))", (ch,))
        db.conn.commit()
    asked = h.FakeHistory({i: h.video(i) for i in range(1, 6)})
    eng = h.Engines(db)
    _, payload = h.job(db, monkeypatch, stages=["metadata", "performers", "channel_data", "snapshots"], channels=[ch], history=asked, engines=eng,
                       detail={"pending_only": True, "unbounded": False, "limit": 10})
    assert asked.calls == []                                                            # no Telegram history walk
    thumbs = [d for k, d in eng.calls if k == "thumbnails"]
    assert len(thumbs) == 1 and sorted(thumbs[0]["message_ids"]) == [2, 4]              # exactly the two that fell out
    assert payload["result_status"] == "COMPLETED"


def test_a_pending_scan_of_a_channel_that_is_all_in_the_library_has_nothing_to_do(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(3)
    h.job(db, monkeypatch, stages=ALL, channels=[ch], history=hist, engines=h.Engines(db))
    eng = h.Engines(db)
    _, payload = h.job(db, monkeypatch, stages=["metadata", "snapshots"], channels=[ch], engines=eng, detail={"pending_only": True})
    assert eng.calls == [] and payload["channel_queue"][0]["state"] == "COMPLETED"


def test_videos_deleted_from_telegram_are_not_treated_as_pending(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(3)
    h.install(monkeypatch, hist)
    h.run(h.pipe._discover_and_store_inventory(h.make_ctx(db), ch, 0, full_scan=True))
    with db.lock:
        db.conn.execute("UPDATE media SET status='SOURCE_DELETED' WHERE source_channel=? AND source_message_id=2", (ch,))
        db.conn.commit()
    ids = [v["message_id"] for v in h.pipe._local_inventory(h.make_ctx(db), ch, 0, pending_only=True)]
    assert sorted(ids) == [1, 3]
    assert sorted(v["message_id"] for v in h.pipe._local_inventory(h.make_ctx(db), ch, 0)) == [1, 3]
    assert len(h.pipe._local_inventory(h.make_ctx(db), ch, 1, pending_only=True)) == 1             # a limit is honoured


def test_a_caption_sync_that_fails_does_not_fail_the_stage(monkeypatch):
    db = h.fresh_db()
    ch, hist = channel_with_videos(3)

    async def boom(*a, **k):
        raise RuntimeError("Telegram would not edit the caption")

    monkeypatch.setattr(h.pipe, "_sync_changed_captions", boom)
    monkeypatch.setattr(h.pipe, "_caption_signatures", lambda db_, ch_, videos: {int(v["media_id"]): "changed-%s" % (id(object()),) for v in videos})
    eng = h.Engines(db)
    _, payload = h.job(db, monkeypatch, stages=["inventory", "metadata"], channels=[ch], history=hist, engines=eng)
    assert h.phases(payload)["metadata"] == "COMPLETED" and payload["channel_queue"][0].get("error", "") == ""
