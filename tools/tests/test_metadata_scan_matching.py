"""The "Match metadata" engine (engines/metadata_sync.run) with fake providers and a fake Telegram."""
import json
from types import SimpleNamespace

import pytest

import metadata_scan_harness as h
from telegram_archive.engines import metadata_sync
from telegram_archive.metadata import pipeline as meta_pipeline


@pytest.fixture
def keyed():
    """The provider key is a database setting every test shares: set it for one test, clear it after."""
    db = h.fresh_db()
    old = {k: db.setting(k) for k in ("stashdb_api_key", "tpdb_api_token", "tpdb_token")}
    db.set_setting("stashdb_api_key", "test-key")
    yield db
    for k, v in old.items():
        db.set_setting(k, v if v is not None else "")


def stored(db, n=4, names=None, **cols):
    ch = h.new_channel()
    with db.lock:
        for i in range(1, n + 1):
            name = (names or {}).get(i, f"scene {i}.mp4")
            db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,'SCANNED')", (ch, i, name, name))
        db.conn.commit()
    return ch


class Telegram:
    def __init__(self, missing=(), fail_ids=()):
        self.missing, self.fail_ids, self.asked = set(missing), set(fail_ids), []

    async def get_messages(self, channel, ids=None):
        self.asked.append(ids)
        many = isinstance(ids, (list, tuple))
        out = []
        for mid in (ids if many else [ids]):
            if mid in self.fail_ids:
                raise RuntimeError("Telegram hiccup")
            out.append(None if mid in self.missing else SimpleNamespace(id=mid, file=SimpleNamespace(name=f"scene {mid}.mp4", size=1000), chat_id=int(channel)))
        return out if many else out[0]


def ctx_for(db, channel, client, *, limit=0, unbounded=True, cancel_after=None, extra=None):
    ctx = h.make_ctx(db, {"source_channels": [channel], "limit": limit, "unbounded": unbounded, **(extra or {})}, cancel_after=cancel_after)
    ctx.job["source_channel"] = channel
    ctx.telegram = SimpleNamespace(client=client, connected=True)
    ctx.activity = None
    ctx.report = lambda *a, **k: None
    return ctx


class Identify:
    """A fake identify_video: `plan` maps message id -> action, or an exception to raise."""

    def __init__(self, plan=None, default="applied"):
        self.plan, self.default, self.calls = plan or {}, default, []

    async def __call__(self, db, *, channel_id, message_id, filename, oshash=None, force=False, **kw):
        self.calls.append({"id": message_id, "oshash": oshash, "force": force, "filename": filename})
        out = self.plan.get(message_id, self.default)
        if isinstance(out, BaseException):
            raise out
        return {"action": out}


def wire(monkeypatch, identify, *, hash_error=None):
    monkeypatch.setattr(meta_pipeline, "identify_video", identify)
    monkeypatch.setattr("telegram_archive.core.scan_schedule.wait_for_metadata_scan_window", h._no_wait)

    class Source:
        def __init__(self, *a, **k): pass
        def close(self): pass

    def fake_hash(source):
        if hash_error:
            raise hash_error
        return "abcdef0123456789"

    monkeypatch.setattr(metadata_sync, "DirectTelegramRangeSource", Source)
    monkeypatch.setattr(metadata_sync, "oshash_from_source", fake_hash)


def go(ctx):
    return h.run(metadata_sync.run(ctx))


def test_without_a_provider_key_the_stage_says_what_to_configure(monkeypatch):
    db = h.fresh_db()
    for k in ("stashdb_api_key", "tpdb_api_token", "tpdb_token"):
        db.set_setting(k, "")
    ch = stored(db)
    wire(monkeypatch, Identify())
    with pytest.raises(RuntimeError, match="StashDB API key or ThePornDB token"):
        go(ctx_for(db, ch, Telegram()))


def test_every_video_is_identified_with_the_fingerprint_of_its_file(keyed, monkeypatch):
    ch = stored(keyed, 3)
    ident = Identify({1: "applied", 2: "no_match", 3: "suggested"})
    wire(monkeypatch, ident)
    go(ctx_for(keyed, ch, Telegram()))
    assert sorted(c["id"] for c in ident.calls) == [1, 2, 3] and all(c["oshash"] == "abcdef0123456789" for c in ident.calls)


def test_a_video_deleted_from_telegram_is_counted_failed_and_the_rest_still_run(keyed, monkeypatch):
    ch = stored(keyed, 4)
    ident = Identify()
    wire(monkeypatch, ident)
    go(ctx_for(keyed, ch, Telegram(missing={2})))
    assert sorted(c["id"] for c in ident.calls) == [1, 3, 4]


def test_a_provider_that_raises_for_one_video_does_not_stop_the_others(keyed, monkeypatch):
    ch = stored(keyed, 4)
    ident = Identify({2: RuntimeError("provider timeout"), 3: "provider_error"})
    wire(monkeypatch, ident)
    go(ctx_for(keyed, ch, Telegram()))
    assert sorted(c["id"] for c in ident.calls) == [1, 2, 3, 4]


def test_a_failed_batch_lookup_falls_back_to_one_message_at_a_time(keyed, monkeypatch):
    ch = stored(keyed, 3)
    ident = Identify()
    wire(monkeypatch, ident)
    tg = Telegram(fail_ids={2})                      # the batched call raises, the single lookups work for 1 and 3
    go(ctx_for(keyed, ch, tg))
    assert sorted(c["id"] for c in ident.calls) == [1, 3]


def test_an_unreadable_file_fingerprint_falls_back_to_the_filename(keyed, monkeypatch):
    ch = stored(keyed, 2)
    ident = Identify()
    wire(monkeypatch, ident, hash_error=RuntimeError("range read failed"))
    go(ctx_for(keyed, ch, Telegram()))
    assert len(ident.calls) == 2 and not any(c["oshash"] for c in ident.calls)


def test_a_known_fingerprint_is_not_computed_again(keyed, monkeypatch):
    ch = stored(keyed, 2)
    with keyed.lock:
        keyed.conn.execute("UPDATE media SET oshash='feedfacefeedface' WHERE source_channel=?", (ch,))
        keyed.conn.commit()
    ident = Identify()
    wire(monkeypatch, ident, hash_error=RuntimeError("must not be called"))
    go(ctx_for(keyed, ch, Telegram()))
    assert [c["oshash"] for c in ident.calls] == ["feedfacefeedface"] * 2


def test_a_cancel_stops_the_stage_and_is_not_swallowed(keyed, monkeypatch):
    ch = stored(keyed, 6)
    wire(monkeypatch, Identify())
    with pytest.raises(h.JobCancelled):
        go(ctx_for(keyed, ch, Telegram(), cancel_after=2))


def test_a_channel_bigger_than_one_batch_is_matched_completely(keyed, monkeypatch):
    ch = stored(keyed, 130)
    ident = Identify()
    wire(monkeypatch, ident)
    tg = Telegram()
    go(ctx_for(keyed, ch, tg))
    assert len(ident.calls) == 130 and len({c["id"] for c in ident.calls}) == 130
    assert [len(x) for x in tg.asked if isinstance(x, list)] == [50, 50, 30]          # fetched in batches, one request each


def test_limits_and_already_finished_videos(keyed, monkeypatch):
    ch = stored(keyed, 8)
    with keyed.lock:
        keyed.conn.execute("UPDATE media SET identification_state='MATCHED' WHERE source_channel=? AND source_message_id IN (1,2)", (ch,))
        keyed.conn.execute("UPDATE media SET status='SOURCE_DELETED' WHERE source_channel=? AND source_message_id=3", (ch,))
        keyed.conn.commit()
    ident = Identify()
    wire(monkeypatch, ident)
    go(ctx_for(keyed, ch, Telegram(), limit=2, unbounded=False))
    assert len(ident.calls) == 2 and all(c["id"] not in (1, 2, 3) for c in ident.calls)
    ident.calls.clear()
    go(ctx_for(keyed, ch, Telegram(), unbounded=True))
    assert sorted(c["id"] for c in ident.calls) == [4, 5, 6, 7, 8]
    ident.calls.clear()
    go(ctx_for(keyed, ch, Telegram(), unbounded=True, extra={"force": True}))
    assert sorted(c["id"] for c in ident.calls) == [1, 2, 4, 5, 6, 7, 8] and all(c["force"] for c in ident.calls)


def test_a_file_name_with_odd_characters_does_not_break_the_stage(keyed, monkeypatch):
    names = {1: "naïve — “quotes” 日本語 [x].mp4", 2: "a" * 400 + ".mp4", 3: "semi;colon'apostrophe\".mp4", 4: ""}
    ch = stored(keyed, 4, names=names)
    ident = Identify()
    wire(monkeypatch, ident)
    go(ctx_for(keyed, ch, Telegram()))
    assert len(ident.calls) == 4


def test_a_token_saved_under_the_older_name_is_used_by_the_lookups_too():
    """The settings page and the scan's "is a key configured" check accept `tpdb_token`; the provider lookups read `tpdb_api_token`."""
    db = h.fresh_db()
    old = {k: db.setting(k) for k in ("tpdb_api_token", "tpdb_token")}
    try:
        db.set_setting("tpdb_api_token", "")
        db.set_setting("tpdb_token", "legacy-token")
        assert db.setting("tpdb_api_token") == "legacy-token"
        db.set_setting("tpdb_api_token", "new-token")
        assert db.setting("tpdb_api_token") == "new-token"            # the newer name wins when both exist
    finally:
        for k, v in old.items():
            db.set_setting(k, v if v is not None else "")
