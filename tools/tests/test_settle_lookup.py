"""A video that was looked up, matched or not, finishes its metadata and entity steps; nobody found means Unknown Performer and Unknown Studio."""
from telegram_archive.core import resource_registry as rr
from telegram_archive.core.database import Database


def media(db, n):
    with db.lock:
        db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,status) VALUES(?,?,?,?,'SCANNED')", (n, "-1001", n, f"v{n}.mp4"))
        db.conn.commit()
    rr.ensure_resource_tables(db)
    return n


def states(db, n):
    r = db.conn.execute("SELECT metadata_state, entity_state FROM resource_state WHERE media_id=?", (n,)).fetchone()
    perf = [x[0] for x in db.conn.execute("SELECT p.name FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=?", (n,))]
    studio = [x[0] for x in db.conn.execute("SELECT s.name FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=?", (n,))]
    return (r["metadata_state"], r["entity_state"]), perf, studio


def test_a_video_with_no_match_ends_under_unknown_performer_and_studio(tmp_path):
    db = Database(tmp_path / "a.db")
    n = media(db, 1)
    assert states(db, n)[0] != ("COMPLETE", "COMPLETE")
    rr.settle_lookup(db, [n])
    assert states(db, n) == (("COMPLETE", "COMPLETE"), ["Unknown Performer"], ["Unknown Studio"])


def test_a_performer_that_was_found_is_kept_and_no_unknown_is_added(tmp_path):
    db = Database(tmp_path / "b.db")
    n = media(db, 2)
    with db.lock:
        db.conn.execute("INSERT INTO performers(id,name) VALUES(77,'Real Person')")
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(?,77)", (n,))
        db.conn.commit()
    rr.settle_lookup(db, [n])
    state, perf, studio = states(db, n)
    assert state == ("COMPLETE", "COMPLETE") and perf == ["Real Person"] and studio == ["Unknown Studio"]


def test_nothing_is_settled_for_an_empty_list_or_for_other_videos(tmp_path):
    db = Database(tmp_path / "c.db")
    a, b = media(db, 3), media(db, 4)
    assert rr.settle_lookup(db, []) == 0
    rr.settle_lookup(db, [a])
    assert states(db, b)[0] != ("COMPLETE", "COMPLETE") and states(db, b)[1] == []


def test_an_sfw_lookup_that_finds_nothing_is_finished_without_unknown_records_and_one_that_fails_is_not(tmp_path):
    from telegram_archive.engines import screen_identify as sid
    from telegram_archive.metadata import screen_ids as si
    db = Database(tmp_path / "d.db")
    n = media(db, 5)
    empty = si.Screen(lambda url, params, headers: {"results": []}, tmdb="k" * 32)
    assert sid.identify_one(db, empty, n, "Some.Unknown.Home.Video.2019.mp4") == "UNMATCHED"
    assert states(db, n) == (("COMPLETE", "COMPLETE"), [], [])       # finished; movies and shows keep the adult library's Unknown records out
    m = media(db, 6)

    def blocked(url, params, headers):
        raise ConnectionError("closed")
    try:
        sid.identify_one(db, si.Screen(blocked, tmdb="k" * 32), m, "Some.Other.Video.2019.mp4")
    except ConnectionError:
        pass
    assert states(db, m)[0] != ("COMPLETE", "COMPLETE") and states(db, m)[1] == []   # the provider could not answer: it is tried again, not settled
