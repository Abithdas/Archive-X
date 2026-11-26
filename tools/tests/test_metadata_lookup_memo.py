"""A video the providers found nothing for is asked about again only when what it was searched with has changed (filename, file hash, providers
asked, visual match) or after 30 days; `force` still asks about everything."""
import pytest

import metadata_scan_harness as h
from test_metadata_scan_matching import Identify, Telegram, ctx_for, go, stored, wire
from telegram_archive.metadata import lookup_memo


@pytest.fixture
def keyed():
    db = h.fresh_db()
    old = {k: db.setting(k) for k in ("stashdb_api_key", "tpdb_api_token", "tpdb_token")}
    db.set_setting("stashdb_api_key", "test-key")
    db.set_setting("tpdb_api_token", "")
    db.set_setting("tpdb_token", "")
    yield db
    for k, v in old.items():
        db.set_setting(k, v if v is not None else "")


class Stamping(Identify):
    """Like the real pipeline: a no-match stamps the video as checked (metadata_synced_at), a match is recorded as such."""

    async def __call__(self, db, *, channel_id, message_id, filename, oshash=None, force=False, **kw):
        out = await super().__call__(db, channel_id=channel_id, message_id=message_id, filename=filename, oshash=oshash, force=force, **kw)
        state = {"no_match": "none", "applied": "matched"}.get(out["action"])
        with db.lock:
            db.conn.execute("UPDATE media SET metadata_source=?, metadata_synced_at=datetime('now'), identification_state=? "
                            "WHERE source_channel=? AND source_message_id=?",
                            (state, "MATCHED" if out["action"] == "applied" else None, str(channel_id), int(message_id)))
            db.conn.commit()
        return out


def asked(ident):
    return sorted({c["id"] for c in ident.calls})


def one_run(db, ch, monkeypatch, ident, **extra):
    wire(monkeypatch, ident)
    go(ctx_for(db, ch, Telegram(), extra=extra))


def test_a_second_run_does_not_ask_again_about_videos_with_no_match(keyed, monkeypatch):
    ch = stored(keyed, 3)
    first = Stamping(default="no_match")
    one_run(keyed, ch, monkeypatch, first)
    assert asked(first) == [1, 2, 3]
    second = Stamping(default="no_match")
    one_run(keyed, ch, monkeypatch, second)
    assert second.calls == []


def test_a_renamed_video_is_asked_again_and_only_that_one(keyed, monkeypatch):
    ch = stored(keyed, 3)
    one_run(keyed, ch, monkeypatch, Stamping(default="no_match"))
    with keyed.lock:
        keyed.conn.execute("UPDATE media SET filename='Studio - Real Title.mp4' WHERE source_channel=? AND source_message_id=2", (ch,))
        keyed.conn.commit()
    again = Stamping(default="no_match")
    one_run(keyed, ch, monkeypatch, again)
    assert asked(again) == [2]


def test_a_file_hash_that_appeared_later_asks_again(keyed, monkeypatch):
    ch = stored(keyed, 2)
    one_run(keyed, ch, monkeypatch, Stamping(default="no_match"))
    with keyed.lock:
        keyed.conn.execute("UPDATE media SET oshash='0123456789abcdef' WHERE source_channel=? AND source_message_id=1", (ch,))
        keyed.conn.commit()
    again = Stamping(default="no_match")
    one_run(keyed, ch, monkeypatch, again)
    assert asked(again) == [1]


def test_a_provider_added_since_asks_again_about_everything(keyed, monkeypatch):
    ch = stored(keyed, 2)
    one_run(keyed, ch, monkeypatch, Stamping(default="no_match"))
    old = keyed.setting("tpdb_api_token")
    keyed.set_setting("tpdb_api_token", "another-provider")
    try:
        again = Stamping(default="no_match")
        one_run(keyed, ch, monkeypatch, again)
        assert asked(again) == [1, 2]
    finally:
        keyed.set_setting("tpdb_api_token", old or "")


def test_switching_the_visual_match_on_asks_again(keyed, monkeypatch):
    ch = stored(keyed, 2)
    one_run(keyed, ch, monkeypatch, Stamping(default="no_match"), visual_match=False)
    same = Stamping(default="no_match")
    one_run(keyed, ch, monkeypatch, same, visual_match=False)
    assert same.calls == []                                                   # same evidence: left alone
    on = Stamping(default="no_match")
    one_run(keyed, ch, monkeypatch, on, visual_match=True)
    assert asked(on) == [1, 2]                                                # a visual match could find what names could not


def test_force_still_asks_about_everything(keyed, monkeypatch):
    ch = stored(keyed, 2)
    one_run(keyed, ch, monkeypatch, Stamping(default="no_match"))
    forced = Stamping(default="no_match")
    one_run(keyed, ch, monkeypatch, forced, force=True)
    assert asked(forced) == [1, 2]


def test_a_video_that_is_matched_later_loses_its_memo(keyed, monkeypatch):
    ch = stored(keyed, 2)
    one_run(keyed, ch, monkeypatch, Stamping(default="no_match"))
    ids = [r[0] for r in keyed.conn.execute("SELECT id FROM media WHERE source_channel=? ORDER BY source_message_id", (ch,))]
    assert keyed.conn.execute(f"SELECT COUNT(*) FROM {lookup_memo.TABLE} WHERE media_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0] == 2
    one_run(keyed, ch, monkeypatch, Stamping(default="applied"), force=True)
    assert keyed.conn.execute(f"SELECT COUNT(*) FROM {lookup_memo.TABLE} WHERE media_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0] == 0


def test_the_memo_never_holds_a_key(keyed, monkeypatch):
    ch = stored(keyed, 1)
    one_run(keyed, ch, monkeypatch, Stamping(default="no_match"))
    rows = keyed.conn.execute(f"SELECT * FROM {lookup_memo.TABLE}").fetchall()
    assert rows and not any("test-key" in str(tuple(r)) for r in rows)


def test_the_signature_is_stable_and_changes_with_what_was_asked(keyed):
    a = lookup_memo.providers_signature(keyed, True)
    assert a == lookup_memo.providers_signature(keyed, True)
    assert a != lookup_memo.providers_signature(keyed, False)
