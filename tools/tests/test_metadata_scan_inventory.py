"""Metadata channel scan, "Collect filenames" stage: every way the Telegram history walk can go wrong or come back strange."""
import asyncio

import pytest

import metadata_scan_harness as h

CH = "-1001"


@pytest.fixture(autouse=True)
def _own_channel():
    global CH
    CH = h.new_channel()
    yield


def scan(db, history, monkeypatch, *, full=False, limit=0, channel=None, ctx=None):
    h.install(monkeypatch, history)
    ctx = ctx or h.make_ctx(db)
    return h.run(h.pipe._discover_and_store_inventory(ctx, channel or CH, limit, full_scan=full))


def live(db, channel=None):
    return h.count(db, channel or CH, " AND COALESCE(status,'')<>'SOURCE_DELETED'")


def test_a_first_scan_stores_every_video_and_marks_the_catalogue_done(monkeypatch):
    db = h.fresh_db()
    out = scan(db, h.FakeHistory({i: h.video(i) for i in range(1, 8)}), monkeypatch, full=True)
    assert len(out) == 7 and live(db) == 7
    done = db.conn.execute("SELECT COUNT(*) FROM resource_state rs JOIN media m ON m.id=rs.media_id WHERE m.source_channel=? AND rs.catalogue_state='COMPLETE'", (CH,)).fetchone()[0]
    assert done == 7


def test_scanning_again_changes_nothing(monkeypatch):
    db = h.fresh_db()
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 6)})
    scan(db, hist, monkeypatch, full=True)
    ids = [r[0] for r in db.conn.execute("SELECT id FROM media WHERE source_channel=? ORDER BY id", (CH,))]
    scan(db, hist, monkeypatch, full=True)
    scan(db, hist, monkeypatch)
    assert [r[0] for r in db.conn.execute("SELECT id FROM media WHERE source_channel=? ORDER BY id", (CH,))] == ids


def test_an_incremental_scan_only_asks_for_newer_messages_and_never_deletes(monkeypatch):
    db = h.fresh_db()
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 6)})
    scan(db, hist, monkeypatch, full=True)
    hist.records = {i: h.video(i) for i in range(1, 9)}            # three new videos
    scan(db, hist, monkeypatch)
    assert hist.calls[-1]["min_id"] == 5 and live(db) == 8
    hist.records = {}                                              # nothing new
    scan(db, hist, monkeypatch)
    assert live(db) == 8


def test_a_full_scan_marks_videos_deleted_from_telegram_and_a_comeback_revives_them(monkeypatch):
    db = h.fresh_db()
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 6)})
    scan(db, hist, monkeypatch, full=True)
    del hist.records[2]
    scan(db, hist, monkeypatch, full=True)
    assert live(db) == 4 and h.count(db, CH, " AND status='SOURCE_DELETED'") == 1
    hist.records[2] = h.video(2)
    scan(db, hist, monkeypatch, full=True)
    assert live(db) == 5


def test_a_full_scan_that_finds_nothing_does_not_wipe_the_channel(monkeypatch):
    """Telegram can answer a history walk with nothing (access lost, a filter hiccup) and still call it complete. That must not
    remove every video of the channel from the Library."""
    db = h.fresh_db()
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 11)})
    scan(db, hist, monkeypatch, full=True)
    hist.records = {}
    scan(db, hist, monkeypatch, full=True)
    assert live(db) == 10


def test_a_scan_cut_short_by_a_limit_or_stopped_early_deletes_nothing_and_keeps_the_mark(monkeypatch):
    db = h.fresh_db()
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 6)})
    scan(db, hist, monkeypatch, full=True)
    mark = db.setting(f"inventory_highwater_v1_{CH}", None)
    for partial in (h.FakeHistory({3: h.video(3)}, complete=False), h.FakeHistory({3: h.video(3)}, complete=False, limit_hit=True)):
        scan(db, partial, monkeypatch, full=True)
        assert live(db) == 5
    hist2 = h.FakeHistory({i: h.video(i) for i in range(6, 9)}, complete=False)
    scan(db, hist2, monkeypatch)
    assert int(db.setting(f"inventory_highwater_v1_{CH}", None)) == int(mark)      # not advanced past what was never finished


def test_odd_records_do_not_break_the_scan(monkeypatch):
    db = h.fresh_db()
    recs = {1: h.video(1), 2: {"id": 2, "name": None, "size": None, "duration": None}, 3: {"id": 3},
            4: {"id": 0, "name": "no id.mp4"}, 5: h.video(5, name="x" * 600), 6: h.video(6, name="  clip   six  .mp4")}
    out = scan(db, h.FakeHistory(recs), monkeypatch, full=True)
    assert live(db) == 5                                   # the record without an id is skipped, the rest are kept
    row = db.conn.execute("SELECT filename,size,duration FROM media WHERE source_channel=? AND source_message_id=2", (CH,)).fetchone()
    assert row["filename"] and row["size"] == 0
    assert len(out) == 5


def test_a_known_name_is_not_replaced_by_a_blank_one_and_a_rename_is_followed(monkeypatch):
    db = h.fresh_db()
    hist = h.FakeHistory({1: h.video(1, name="First.mp4")})
    scan(db, hist, monkeypatch, full=True)
    hist.records = {1: h.video(1, name="Renamed.mp4", size=0, duration=0)}
    scan(db, hist, monkeypatch, full=True)
    row = db.conn.execute("SELECT filename,size,duration FROM media WHERE source_channel=? AND source_message_id=1", (CH,)).fetchone()
    assert row["filename"] == "Renamed.mp4"
    assert row["size"] == 1001 and row["duration"] == 61.0     # zero from Telegram does not erase what is known


def test_a_telegram_failure_is_reported_and_leaves_the_stored_videos_alone(monkeypatch):
    db = h.fresh_db()
    scan(db, h.FakeHistory({i: h.video(i) for i in range(1, 4)}), monkeypatch, full=True)
    with pytest.raises(RuntimeError):
        scan(db, h.FakeHistory(raises=RuntimeError("connection lost")), monkeypatch, full=True)
    assert live(db) == 3


def test_a_missing_telegram_connection_is_a_clear_error(monkeypatch):
    db = h.fresh_db()
    ctx = h.make_ctx(db)
    ctx.telegram.client = None
    with pytest.raises(RuntimeError, match="not connected"):
        scan(db, h.FakeHistory(), monkeypatch, ctx=ctx)


def test_flood_waits_are_waited_out_then_the_scan_succeeds(monkeypatch):
    db = h.fresh_db()
    class FloodWait(Exception):
        seconds = 7
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 4)}, raises=[FloodWait("wait"), FloodWait("wait"), None])
    slept = []
    real_sleep = asyncio.sleep
    async def fast_sleep(s, *a, **k):
        slept.append(s)
        await real_sleep(0)
    monkeypatch.setattr(h.pipe.asyncio, "sleep", fast_sleep)
    clock = {"t": 0.0}
    monkeypatch.setattr(h.pipe.time, "monotonic", lambda: clock.__setitem__("t", clock["t"] + 5.0) or clock["t"])
    h.install(monkeypatch, hist)
    ctx = h.make_ctx(db)
    out = h.run(h.pipe._inventory(ctx, CH, 0))
    assert len(out) == 3 and len(hist.calls) == 3 and slept


def test_endless_flood_waits_give_up_after_five_tries_and_fall_back_to_the_stored_videos(monkeypatch):
    db = h.fresh_db()
    scan(db, h.FakeHistory({i: h.video(i) for i in range(1, 4)}), monkeypatch, full=True)
    class FloodWait(Exception):
        seconds = 3
    hist = h.FakeHistory(raises=[FloodWait("wait")] * 50)
    real_sleep = asyncio.sleep
    async def fast_sleep(s, *a, **k):
        await real_sleep(0)
    monkeypatch.setattr(h.pipe.asyncio, "sleep", fast_sleep)
    clock = {"t": 0.0}
    monkeypatch.setattr(h.pipe.time, "monotonic", lambda: clock.__setitem__("t", clock["t"] + 50.0) or clock["t"])
    h.install(monkeypatch, hist)
    ctx = h.make_ctx(db)
    out = h.run(h.pipe._inventory(ctx, CH, 0))
    assert len(out) == 3 and len(hist.calls) == 6                              # first try + 5 retries
    assert any("did not finish" in v for v in h.pipe._INVENTORY_WARNINGS.values())


def test_a_cancel_during_the_walk_adds_nothing_and_a_rerun_finishes(monkeypatch):
    db = h.fresh_db()
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 6)}, raises=[h.JobCancelled()])
    with pytest.raises(h.JobCancelled):
        scan(db, hist, monkeypatch, full=True)
    assert live(db) == 0
    scan(db, hist, monkeypatch, full=True)
    assert live(db) == 5


def test_channels_do_not_mix(monkeypatch):
    db = h.fresh_db()
    a, b = h.new_channel(), h.new_channel()
    scan(db, h.FakeHistory({1: h.video(1), 2: h.video(2)}), monkeypatch, full=True, channel=a)
    scan(db, h.FakeHistory({1: h.video(1)}), monkeypatch, full=True, channel=b)
    assert live(db, a) == 2 and live(db, b) == 1
    scan(db, h.FakeHistory({1: h.video(1)}), monkeypatch, full=True, channel=a)
    assert live(db, a) == 1 and live(db, b) == 1
