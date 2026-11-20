"""Performer gallery: provider pictures kept on this PC, safe downloads, removal that sticks, merges, and the face references learned from it."""
import asyncio
import io
import json
import time

import numpy as np
import pytest
from PIL import Image

import metadata_scan_harness as h
from telegram_archive import config
from telegram_archive.engines import performer_gallery as pg


@pytest.fixture(autouse=True)
def _private_gallery_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    yield


def jpeg(color=(200, 30, 30), size=(300, 300), fmt="JPEG", noise=0):
    rng = np.random.default_rng(noise)
    arr = np.full((size[1], size[0], 3), color, dtype=np.uint8)
    if noise:
        arr = np.clip(arr.astype(int) + rng.integers(-30, 30, arr.shape), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, fmt)
    return buf.getvalue()


def performer(db, name=None):
    from telegram_archive.engines.entity_identity import upsert_performer
    pid = upsert_performer(db, name or f"Gallery Person {h.new_channel()}", provider="stashdb", provider_id=h.new_channel())
    db.conn.commit()
    return pid


def cands(*urls, provider="stashdb"):
    return [{"url": u, "provider": provider, "provider_id": "p1", "width": 0, "height": 0} for u in urls]


class Net:
    """A fake download: url -> bytes (or an exception to raise)."""

    def __init__(self, files):
        self.files, self.calls = files, []

    async def __call__(self, url):
        self.calls.append(url)
        out = self.files[url]
        if isinstance(out, BaseException):
            raise out
        return out


def sync(db, pid, net, urls, **kw):
    return h.run(pg.sync_performer(db, pid, candidates=cands(*urls), download=net, **kw))


def active(db, pid):
    return db.conn.execute("SELECT COUNT(*) FROM performer_gallery WHERE performer_id=? AND status='ACTIVE'", (pid,)).fetchone()[0]


# ---------------------------------------------------------------- keeping the pictures ----------------------------------------------------------
def test_pictures_are_downloaded_and_kept_as_files_on_this_pc():
    db = h.fresh_db(); pid = performer(db)
    net = Net({f"https://x.test/{i}.jpg": jpeg((i * 40, 80, 120), noise=i) for i in range(1, 4)})
    out = sync(db, pid, net, list(net.files))
    assert out["ok"] and out["added"] == 3 and out["found"] == 3
    listing = pg.list_pictures(db, pid)
    assert len(listing["items"]) == 3 and all(i["width"] == 300 for i in listing["items"])
    for item in listing["items"]:
        assert pg.picture_file(db, pid, item["id"]).is_file()
    assert listing["last_result"] == "ok" and listing["last_synced_at"]


def test_syncing_again_downloads_nothing_it_already_has():
    db = h.fresh_db(); pid = performer(db)
    net = Net({"https://x.test/a.jpg": jpeg(noise=1), "https://x.test/b.jpg": jpeg((10, 200, 10), noise=2)})
    sync(db, pid, net, list(net.files))
    net.calls.clear()
    out = sync(db, pid, net, list(net.files))
    assert out["added"] == 0 and out["known"] == 2 and net.calls == []


def test_a_new_provider_picture_is_added_on_the_next_sync():
    db = h.fresh_db(); pid = performer(db)
    files = {"https://x.test/a.jpg": jpeg(noise=1), "https://x.test/b.jpg": jpeg((10, 200, 10), noise=2)}
    net = Net(files)
    sync(db, pid, net, ["https://x.test/a.jpg"])
    out = sync(db, pid, net, list(files))
    assert out["added"] == 1 and active(db, pid) == 2


def test_the_number_of_pictures_kept_is_capped():
    db = h.fresh_db(); pid = performer(db)
    net = Net({f"https://x.test/{i}.jpg": jpeg((i * 5 % 255, 50, 90), noise=i) for i in range(1, 30)})
    out = sync(db, pid, net, list(net.files), max_pictures=5)
    assert out["added"] == 5 and active(db, pid) == 5 and len(net.calls) == 5          # and it stops downloading at the cap


def test_the_same_picture_at_two_addresses_is_kept_once():
    db = h.fresh_db(); pid = performer(db)
    data = jpeg(noise=7)
    net = Net({"https://x.test/a.jpg": data, "https://cdn.test/a-copy.jpg": data})
    out = sync(db, pid, net, list(net.files))
    assert out["added"] == 1 and out["duplicates"] == 1 and active(db, pid) == 1
    net.calls.clear()
    sync(db, pid, net, list(net.files))
    assert net.calls == []                                                             # the copy is remembered, not fetched again


@pytest.mark.parametrize("data", [b"<html>not a picture</html>" * 40, b"", jpeg(size=(60, 60)), jpeg(fmt="GIF"), jpeg()[:200], b"\x00" * 3000])
def test_things_that_are_not_usable_pictures_are_not_kept(data):
    db = h.fresh_db(); pid = performer(db)
    net = Net({"https://x.test/bad": data})
    out = sync(db, pid, net, ["https://x.test/bad"])
    assert out["added"] == 0 and active(db, pid) == 0
    net.calls.clear()
    sync(db, pid, net, ["https://x.test/bad"])
    assert net.calls == []                                                             # a bad picture is not asked for again


def test_a_failed_download_is_tried_again_next_time():
    db = h.fresh_db(); pid = performer(db)
    net = Net({"https://x.test/a.jpg": TimeoutError("slow")})
    out = sync(db, pid, net, ["https://x.test/a.jpg"])
    assert out["failed"] == 1 and out["added"] == 0
    net.files["https://x.test/a.jpg"] = jpeg(noise=3)
    assert sync(db, pid, net, ["https://x.test/a.jpg"])["added"] == 1


def test_a_removed_picture_stays_removed_and_is_never_fetched_again():
    db = h.fresh_db(); pid = performer(db)
    net = Net({"https://x.test/a.jpg": jpeg(noise=1), "https://x.test/b.jpg": jpeg((9, 99, 199), noise=2)})
    sync(db, pid, net, list(net.files))
    first = pg.list_pictures(db, pid)["items"][0]
    path = pg.picture_file(db, pid, first["id"])
    assert pg.remove_picture(db, pid, first["id"]) is True
    assert not path.exists() and pg.picture_file(db, pid, first["id"]) is None
    net.calls.clear()
    out = sync(db, pid, net, list(net.files))
    assert out["rejected"] == 1 and net.calls == [] and active(db, pid) == 1
    assert pg.list_pictures(db, pid)["removed"] == 1
    assert pg.remove_picture(db, pid, first["id"]) is False                            # already gone


def test_a_picture_of_another_performer_cannot_be_reached_through_this_one():
    db = h.fresh_db(); a, b = performer(db), performer(db)
    net = Net({"https://x.test/a.jpg": jpeg(noise=1)})
    sync(db, a, net, list(net.files))
    pic = pg.list_pictures(db, a)["items"][0]["id"]
    assert pg.picture_file(db, b, pic) is None and pg.remove_picture(db, b, pic) is False


def test_the_unknown_performer_and_a_missing_performer_are_skipped():
    db = h.fresh_db()
    from telegram_archive.core import resource_registry as rr
    rr.ensure_unknown_entity_links(db, [])
    unknown = db.conn.execute("SELECT id FROM performers WHERE name='Unknown Performer'").fetchone()
    if unknown:
        assert h.run(pg.sync_performer(db, unknown[0], candidates=cands("https://x.test/a.jpg"), download=Net({})))["reason"] == "unknown_performer"
    assert h.run(pg.sync_performer(db, 999999999, candidates=[], download=Net({})))["reason"] == "performer_not_found"


def test_a_provider_that_fails_is_reported_and_does_not_mark_the_performer_as_done(monkeypatch):
    db = h.fresh_db(); pid = performer(db)
    from telegram_archive.metadata import providers

    async def boom(*a, **k):
        raise RuntimeError("StashDB is down")

    monkeypatch.setattr(providers, "performer_image_candidates_from_providers", boom)
    out = h.run(pg.sync_performer(db, pid))
    assert out["ok"] is False and out["reason"] == "provider_lookup_failed"
    assert pid in pg.performers_due(db, limit=100000)                                   # still due, so the next run tries again


def test_the_providers_are_asked_through_the_picture_pickers_lookup(monkeypatch):
    db = h.fresh_db(); pid = performer(db, "Ava Stone Gallery")
    from telegram_archive.metadata import providers
    seen = {}

    async def fake(db_, name, provider_ids=None, limit=10, entity_id=None, offset=0):
        seen.update(name=name, limit=limit, entity=entity_id, offset=offset)
        return cands("https://x.test/a.jpg") + cands("https://x.test/t.jpg", provider="theporndb")

    monkeypatch.setattr(providers, "performer_image_candidates_from_providers", fake)
    out = h.run(pg.sync_performer(db, pid, download=Net({"https://x.test/a.jpg": jpeg(noise=1), "https://x.test/t.jpg": jpeg((1, 2, 3), noise=2)})))
    assert out["added"] == 2 and seen == {"name": "Ava Stone Gallery", "limit": pg.MAX_PICTURES, "entity": pid, "offset": 0}
    assert {i["provider"] for i in pg.list_pictures(db, pid)["items"]} == {"stashdb", "theporndb"}


# ---------------------------------------------------------------- which performers are due -------------------------------------------------------
def test_performers_are_due_until_synced_and_again_after_a_month():
    db = h.fresh_db(); pid = performer(db)
    assert pid in pg.performers_due(db, limit=100000)
    sync(db, pid, Net({}), [])
    assert pid not in pg.performers_due(db, limit=100000)
    with db.lock:
        db.conn.execute("UPDATE performer_gallery_state SET last_synced_at=datetime('now','-40 day') WHERE performer_id=?", (pid,))
        db.conn.commit()
    assert pid in pg.performers_due(db, limit=100000)


def test_sync_due_works_through_performers_and_stops_on_a_cancel():
    db = h.fresh_db()
    ids = [performer(db) for _ in range(4)]
    calls = []

    async def fake_sync(db_, pid):
        calls.append(pid)
        pg._record_state(db_, pid, "ok", 0, advance=True)
        return {"added": 2}

    done = h.run(pg.sync_due(db, limit=3, sync=fake_sync))
    assert done["performers"] == 3 and done["added"] == 6 and len(calls) == 3

    async def stop():
        raise h.JobCancelled()

    with pytest.raises(h.JobCancelled):
        h.run(pg.sync_due(db, limit=5, checkpoint=stop, sync=fake_sync))
    assert len(calls) == 3                                                              # nothing more was started


def test_sync_due_stops_when_its_time_budget_is_used_and_the_rest_stay_due():
    db = h.fresh_db()
    for pid in db.conn.execute("SELECT id FROM performers").fetchall():
        pg._record_state(db, pid[0], "ok", 0, advance=True)             # everyone already done: only the new ones are due
    ids = [performer(db) for _ in range(5)]
    ticks = iter(range(0, 1000, 100))

    async def fake_sync(db_, pid):
        pg._record_state(db_, pid, "ok", 0, advance=True)
        return {"added": 1}

    done = h.run(pg.sync_due(db, limit=5, sync=fake_sync, budget_seconds=250, clock=lambda: next(ticks)))
    assert 1 <= done["performers"] < 5
    assert len(pg.performers_due(db, limit=100)) == 5 - done["performers"]


def test_a_picture_whose_file_has_gone_is_fetched_again_and_keeps_its_row():
    db = h.fresh_db(); pid = performer(db)
    net = Net({"https://x.test/a.jpg": jpeg(noise=1)})
    sync(db, pid, net, list(net.files))
    gid = pg.list_pictures(db, pid)["items"][0]["id"]
    pg.index_faces(db, lambda path: [face(0, 0, 90, 90, unit(8))])
    pg.picture_file(db, pid, gid).unlink()                               # a restored backup brings the database but not the files
    assert pg.picture_file(db, pid, gid) is None
    out = sync(db, pid, net, list(net.files))
    assert out["added"] == 1 and pg.picture_file(db, pid, gid).is_file()
    assert pg.list_pictures(db, pid)["references"] == 1                  # what was learnt from the picture is kept


def test_only_one_background_sync_runs_per_performer(monkeypatch):
    db = h.fresh_db(); pid = performer(db)
    started = []

    async def slow(db_, performer_id, **kw):
        started.append(performer_id)
        await asyncio.sleep(0.2)
        return {}

    monkeypatch.setattr(pg, "sync_performer", slow)

    async def go():
        first = pg.start_background_sync(db, pid)
        second = pg.start_background_sync(db, pid)
        syncing = pg.is_syncing(pid)
        await asyncio.sleep(0.4)
        return first, second, syncing, pg.is_syncing(pid)

    assert h.run(go()) == (True, False, True, False) and started == [pid]


# ---------------------------------------------------------------- merges and deletes -------------------------------------------------------------
def test_merging_two_performers_brings_their_pictures_together():
    db = h.fresh_db(); keep, gone = performer(db), performer(db)
    shared = jpeg(noise=11)
    sync(db, keep, Net({"https://x.test/k.jpg": jpeg(noise=1), "https://x.test/s.jpg": shared}), ["https://x.test/k.jpg", "https://x.test/s.jpg"])
    sync(db, gone, Net({"https://x.test/g.jpg": jpeg((7, 7, 200), noise=5), "https://y.test/s-copy.jpg": shared}), ["https://x.test/g.jpg", "https://y.test/s-copy.jpg"])
    from telegram_archive.engines.entity_identity import merge_performers
    merge_performers(db, keep, gone)
    assert db.conn.execute("SELECT COUNT(*) FROM performer_gallery WHERE performer_id=?", (gone,)).fetchone()[0] == 0
    items = pg.list_pictures(db, keep)["items"]
    assert len(items) == 3                                                                # k, s, g (the copy of s was dropped)
    assert all(pg.picture_file(db, keep, i["id"]).is_file() for i in items)


def test_deleting_a_performer_removes_their_pictures_from_disk():
    db = h.fresh_db(); pid = performer(db)
    sync(db, pid, Net({"https://x.test/a.jpg": jpeg(noise=1)}), ["https://x.test/a.jpg"])
    path = pg.picture_file(db, pid, pg.list_pictures(db, pid)["items"][0]["id"])
    pg.delete_performer_gallery(db, pid)
    assert not path.exists() and pg.list_pictures(db, pid)["items"] == []


def test_a_stored_path_that_points_outside_the_gallery_is_never_served():
    db = h.fresh_db(); pid = performer(db)
    sync(db, pid, Net({"https://x.test/a.jpg": jpeg(noise=1)}), ["https://x.test/a.jpg"])
    gid = pg.list_pictures(db, pid)["items"][0]["id"]
    with db.lock:
        db.conn.execute("UPDATE performer_gallery SET file_path='../../outside.jpg' WHERE id=?", (gid,)); db.conn.commit()
    assert pg.picture_file(db, pid, gid) is None


# ---------------------------------------------------------------- safe addresses and images ------------------------------------------------------
@pytest.mark.parametrize("host,public", [("localhost", False), ("127.0.0.1", False), ("10.0.0.5", False), ("192.168.1.2", False), ("172.16.0.9", False),
                                         ("169.254.169.254", False), ("0.0.0.0", False), ("::1", False), ("printer.local", False),
                                         ("metadata.google.internal", False), ("8.8.8.8", True), ("", False)])
def test_only_public_hosts_are_allowed(host, public):
    assert pg._host_is_public(host) is public


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.jpg", "https://localhost/a.jpg", "ftp://example.com/a.jpg", "file:///etc/passwd",
                                 "https://user:pw@8.8.8.8/a.jpg", "javascript:alert(1)", "", "//8.8.8.8/a.jpg"])
def test_unsafe_addresses_are_refused_before_any_request(url):
    with pytest.raises(ValueError):
        pg.fetch_bytes(url)


@pytest.mark.parametrize("data,ok", [(jpeg(), True), (jpeg(fmt="PNG"), True), (jpeg(fmt="WEBP"), True), (jpeg(size=(119, 400)), False),
                                     (jpeg(fmt="GIF"), False), (jpeg(fmt="BMP"), False), (b"x" * 5000, False), (jpeg()[:-100], None)])
def test_what_counts_as_a_usable_picture(data, ok):
    info = pg.inspect_image(data)
    if ok is None:
        assert info is None or info[0] == 300           # a slightly truncated file may still decode; it must not crash
    else:
        assert (info is not None) is ok


# ---------------------------------------------------------------- faces and references ------------------------------------------------------------
def face(x, y, w, h_, emb):
    return {"bbox": [x, y, w, h_], "embedding": list(emb)}


def unit(seed, dim=128):
    v = np.random.default_rng(seed).normal(size=dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def test_only_a_picture_with_one_clear_face_serves_as_a_reference():
    assert pg.pick_reference_face([]) == (0, [])
    assert pg.pick_reference_face([face(0, 0, 80, 80, unit(1))])[1] == unit(1)
    count, emb = pg.pick_reference_face([face(0, 0, 80, 80, unit(1)), face(100, 0, 78, 79, unit(2))])
    assert count == 2 and emb == []                                  # two people of similar size: not a safe reference
    count, emb = pg.pick_reference_face([face(0, 0, 200, 200, unit(1)), face(300, 0, 60, 60, unit(2))])
    assert count == 2 and emb == unit(1)                             # one clearly the subject


def test_faces_are_read_once_and_an_unreadable_picture_is_retried():
    db = h.fresh_db(); pid = performer(db)
    sync(db, pid, Net({"https://x.test/a.jpg": jpeg(noise=1), "https://x.test/b.jpg": jpeg((5, 50, 150), noise=2)}), ["https://x.test/a.jpg", "https://x.test/b.jpg"])
    seen = []

    def extract(path):
        seen.append(path)
        if len(seen) == 1:
            raise RuntimeError("detector hiccup")
        return [face(0, 0, 90, 90, unit(3))]

    assert pg.index_faces(db, extract) == 1                           # one read, one failed and left for next time
    assert pg.index_faces(db, extract) == 1
    assert pg.index_faces(db, extract) == 0                           # all read
    assert pg.list_pictures(db, pid)["references"] == 2


def vec_row(db, pid, seed, table="performer_face_embeddings"):
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO performer_face_embeddings(performer_id,image_key,embedding_json,updated_at) VALUES(?,?,?,?)",
                        (pid, "k", json.dumps(unit(seed)), "t"))
        db.conn.commit()


def confirmed_faces(db, pid, seeds):
    """Faces in a group named after `pid`; returns the detection ids."""
    now = "2026-01-01T00:00:00+00:00"
    with db.lock:
        gid = db.conn.execute("INSERT INTO photo_face_groups(performer_id,created_at,updated_at) VALUES(?,?,?)", (pid, now, now)).lastrowid
        out = []
        for s in seeds:
            photo = db.conn.execute("INSERT INTO photo_library_items(channel_id,message_id,created_at,updated_at) VALUES('-1',?,?,?)", (abs(hash((pid, s))) % 10**9, now, now)).lastrowid
            out.append(db.conn.execute("INSERT INTO photo_face_detections(photo_id,face_index,group_id,embedding_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                                       (photo, 0, gid, json.dumps(unit(s)), now, now)).lastrowid)
        db.conn.commit()
    return out


def test_references_combine_the_profile_picture_the_gallery_and_confirmed_faces():
    db = h.fresh_db(); pid = performer(db)
    vec_row(db, pid, 1)
    sync(db, pid, Net({"https://x.test/a.jpg": jpeg(noise=1)}), ["https://x.test/a.jpg"])
    pg.index_faces(db, lambda path: [face(0, 0, 90, 90, unit(2))])
    confirmed_faces(db, pid, [10, 11, 12])
    with db.reading() as conn:
        ids, matrix = pg.reference_matrix(conn, wanted=[pid])
    assert ids.count(pid) == 5 and matrix.shape == (5, 128)           # profile + 1 gallery + 3 confirmed
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-4)


def test_confirmed_faces_kept_as_references_are_a_varied_dozen_not_every_face():
    db = h.fresh_db(); pid = performer(db)
    confirmed_faces(db, pid, range(100, 140))
    with db.reading() as conn:
        ids, matrix = pg.reference_matrix(conn, wanted=[pid])
    assert len(ids) == pg.CONFIRMED_EXEMPLARS


def test_a_face_taken_out_of_a_performer_is_not_a_reference_for_them():
    db = h.fresh_db(); pid = performer(db)
    detections = confirmed_faces(db, pid, [20, 21])
    from telegram_archive.engines.photo_face_clustering import ensure_negatives_table
    with db.lock:
        ensure_negatives_table(db.conn)
        db.conn.execute("INSERT INTO photo_face_negatives(detection_id,performer_id,created_at) VALUES(?,?,'t')", (detections[0], pid)); db.conn.commit()
    with db.reading() as conn:
        ids, matrix = pg.reference_matrix(conn, wanted=[pid])
    assert len(ids) == 1 and np.allclose(matrix[0], unit(21), atol=1e-5)


def test_a_removed_gallery_picture_stops_being_a_reference():
    db = h.fresh_db(); pid = performer(db)
    sync(db, pid, Net({"https://x.test/a.jpg": jpeg(noise=1)}), ["https://x.test/a.jpg"])
    pg.index_faces(db, lambda path: [face(0, 0, 90, 90, unit(2))])
    with db.reading() as conn:
        assert len(pg.reference_matrix(conn, wanted=[pid])[0]) == 1
    pg.remove_picture(db, pid, pg.list_pictures(db, pid)["items"][0]["id"])
    with db.reading() as conn:
        assert pg.reference_matrix(conn, wanted=[pid])[0] == []


def test_a_performer_score_is_the_best_row_or_the_mean_of_the_best_two():
    ids = [1, 1, 1, 2]
    order, best = pg.best_per_performer(ids, [0.2, 0.9, 0.5, 0.6], top=1)
    assert dict(zip(order, best)) == {1: pytest.approx(0.9), 2: pytest.approx(0.6)}
    order, best = pg.best_per_performer(ids, [0.2, 0.9, 0.5, 0.6], top=2)
    assert dict(zip(order, best)) == {1: pytest.approx(0.7), 2: pytest.approx(0.6)}


# ---------------------------------------------------------------- the matcher uses all of it ------------------------------------------------------
def make_group(db, seeds, noise=0.05, base=None):
    """An unnamed group whose faces are `seeds` (or noisy copies of vector `base`), each in a completed photo."""
    now = "2026-01-01T00:00:00+00:00"
    rng = np.random.default_rng(99)
    with db.lock:
        gid = db.conn.execute("INSERT INTO photo_face_groups(performer_id,created_at,updated_at) VALUES(NULL,?,?)", (now, now)).lastrowid
        for s in seeds:
            v = np.asarray(base if base is not None else unit(s), dtype=np.float32) + rng.normal(scale=noise, size=128).astype(np.float32) * (1 if base is not None else 0)
            v = (v / np.linalg.norm(v)).tolist()
            photo = db.conn.execute("INSERT INTO photo_library_items(channel_id,message_id,created_at,updated_at) VALUES('-2',?,?,?)", (abs(hash((gid, s))) % 10**9, now, now)).lastrowid
            db.conn.execute("INSERT INTO photo_face_analysis(photo_id,status,face_count,updated_at) VALUES(?,'COMPLETE',1,?)", (photo, now))
            db.conn.execute("INSERT INTO photo_face_detections(photo_id,face_index,group_id,embedding_json,created_at,updated_at) VALUES(?,?,?,?,?,?)", (photo, 0, gid, json.dumps(v), now, now))
        db.conn.commit()
    return gid


def clean_face_tables(db):
    with db.lock:
        for t in ("photo_face_detections", "photo_face_groups", "photo_face_analysis", "photo_face_suggestion_feedback", "performer_face_embeddings", "performer_gallery"):
            db.conn.execute(f"DELETE FROM {t}")
        try:
            db.conn.execute("DELETE FROM photo_face_negatives")
        except Exception:
            pass
        db.conn.commit()


def test_a_group_is_named_from_gallery_pictures_when_the_profile_picture_alone_would_not_match():
    from telegram_archive.engines import photo_face_clustering as pfc
    db = h.fresh_db(); clean_face_tables(db)
    ava, other = performer(db), performer(db)
    target = unit(500)
    vec_row(db, ava, 1)                                              # an unlike profile picture: one reference cannot match
    vec_row(db, other, 2)
    gid = make_group(db, range(4), base=target, noise=0.02)
    assert pfc._auto_match_people(db)["matched"] == 0                # profile picture only: no match, as before
    sync(db, ava, Net({f"https://x.test/{i}.jpg": jpeg((i * 30, 9, 9), noise=i) for i in range(2)}), [f"https://x.test/{i}.jpg" for i in range(2)])
    pg.index_faces(db, lambda path: [face(0, 0, 90, 90, target)])    # her gallery pictures show the same face as the group
    assert pfc._auto_match_people(db)["matched"] == 1
    assert db.conn.execute("SELECT performer_id FROM photo_face_groups WHERE id=?", (gid,)).fetchone()[0] == ava


def test_two_performers_whose_references_both_match_leave_the_group_for_you_to_decide():
    """A wrong picture in one gallery (a provider mislabel) must not name a group on its own: the margin over the runner-up guards it."""
    from telegram_archive.engines import photo_face_clustering as pfc
    db = h.fresh_db(); clean_face_tables(db)
    ava, bea = performer(db), performer(db)
    target = unit(600)
    gid = make_group(db, range(4), base=target, noise=0.02)
    for who, name in ((ava, "a"), (bea, "b")):
        sync(db, who, Net({f"https://x.test/{name}.jpg": jpeg(noise=1 if name == "a" else 2)}), [f"https://x.test/{name}.jpg"])
    pg.index_faces(db, lambda path: [face(0, 0, 90, 90, target)])    # both galleries hold a picture with this very face
    assert pfc._auto_match_people(db)["matched"] == 0
    assert db.conn.execute("SELECT performer_id FROM photo_face_groups WHERE id=?", (gid,)).fetchone()[0] is None


def test_confirming_faces_teaches_the_matcher_the_next_group():
    from telegram_archive.engines import photo_face_clustering as pfc
    db = h.fresh_db(); clean_face_tables(db)
    ava = performer(db)
    target = unit(700)
    vec_row(db, ava, 4)                                              # profile picture unlike her real face
    gid = make_group(db, range(4), base=target, noise=0.02)
    assert pfc._auto_match_people(db)["matched"] == 0
    known = make_group(db, range(4), base=target, noise=0.02)        # you confirm one group of her faces ...
    with db.lock:
        db.conn.execute("UPDATE photo_face_groups SET performer_id=? WHERE id=?", (ava, known)); db.conn.commit()
    assert pfc._auto_match_people(db)["matched"] == 1                # ... and the other group is recognised
    assert db.conn.execute("SELECT performer_id FROM photo_face_groups WHERE id=?", (gid,)).fetchone()[0] == ava
