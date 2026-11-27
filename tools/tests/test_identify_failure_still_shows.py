"""A video whose lookup could not be made (the databases are unreachable) is still shown, and is looked up again later."""
import time

from test_screen_identify import _client, Database
from telegram_archive.engines import local_scanner as ls


def _folder(tmp_path, db, c, H, rating):
    big = tmp_path / "Folder"
    big.mkdir()
    (big / "scene one.mp4").write_bytes(b"\x07" * 200_000)
    sid_ = c.post("/api/local-folders", json={"path": str(big), "content_rating": rating, "holds": "videos"}, headers=H).json()["id"]
    ch = ls.channel_id(sid_)
    for _ in range(200):
        with db.reading() as conn:
            if conn.execute("SELECT COUNT(*) FROM local_video_files WHERE channel_id=? AND snapshots<>0", (ch,)).fetchone()[0] == 1:
                break
        time.sleep(0.05)
    time.sleep(0.3)
    return ch


def _links(db, ch):
    with db.reading() as conn:
        mid = conn.execute("SELECT id FROM media WHERE source_channel=?", (ch,)).fetchone()[0]
        perf = [r[0] for r in conn.execute("SELECT p.name FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=?", (mid,))]
        studio = [r[0] for r in conn.execute("SELECT s.name FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=?", (mid,))]
        meta = conn.execute("SELECT metadata_state, entity_state FROM resource_state WHERE media_id=?", (mid,)).fetchone()
        ident = conn.execute("SELECT COALESCE(identification_state,'') FROM media WHERE id=?", (mid,)).fetchone()[0]
    return perf, studio, tuple(meta) if meta else None, ident


def test_an_adult_video_is_unknown_performer_and_studio_when_the_databases_cannot_be_reached(tmp_path, monkeypatch):
    from telegram_archive.engines import local_identify_adult as adult
    db = Database(tmp_path / "n.db")
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    ch = _folder(tmp_path, db, c, H, "nsfw")
    db.set_setting("stashdb_api_key", "k" * 20)

    async def lookup(_db, file_hash):
        raise ConnectionResetError("closed by the remote host")
    monkeypatch.setattr(adult, "_lookup", lookup)
    out = adult.run(db, ch)
    assert out["ok"] and out["failed"] == 1
    perf, studio, meta, ident = _links(db, ch)
    assert perf == ["Unknown Performer"] and studio == ["Unknown Studio"] and meta == ("COMPLETE", "COMPLETE")
    assert ident not in ("MATCHED", "VERIFIED")                              # still to be looked up
    assert [r["id"] for r in adult.pending(db, ch)]                          # and on the list for the next run


def test_a_movie_file_is_shown_unidentified_when_tmdb_cannot_be_reached_and_is_asked_again(tmp_path):
    from telegram_archive.engines import screen_identify as sid
    db = Database(tmp_path / "s.db")
    c, H = _client(db)
    ch = _folder(tmp_path, db, c, H, "sfw")
    db.set_setting("tmdb_api_key", "k" * 32)

    def down(*a, **k):
        raise ConnectionResetError("closed by the remote host")
    for _ in range(200):                                                             # a folder starts looking by itself: let that run end first
        if not sid.progress().get("running"):
            break
        time.sleep(0.05)
    out = sid.run(db, ch, fetch=down)
    assert out["ok"]
    perf, studio, meta, _ = _links(db, ch)
    assert perf == [] and studio == [] and meta == ("COMPLETE", "COMPLETE")        # shown; the Unknown records are the adult library's, not a film's
    assert len(sid.pending(db, ch)) == 1                                             # and still to be named when TMDB can be reached
