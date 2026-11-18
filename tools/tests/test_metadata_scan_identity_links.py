"""After a video is identified: are its performers and studio the right rows, linked once, with nothing lost or duplicated?"""
import json

import pytest

import metadata_scan_harness as h
from telegram_archive.metadata import providers as prov

DB = h.fresh_db


def video(db, name="Studio.25.09.28.Ava.Stone.mp4", channel=None, mid=1):
    ch = channel or h.new_channel()
    with db.lock:
        db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,'SCANNED')", (ch, mid, name, name))
        db.conn.commit()
        row = db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?", (ch, mid)).fetchone()
    return ch, mid, int(row["id"])


def scene(**kw):
    base = {"source": "stashdb", "id": "scene-1", "title": "A Title", "date": "2025-09-28", "studio": "Brazzers", "studio_id": "studio-9", "site": "Brazzers",
            "performers": ["Ava Stone", "Mick Blue"],
            "performer_details": [{"name": "Ava Stone", "id": "p-1", "provider": "stashdb", "gender": "female"},
                                  {"name": "Mick Blue", "id": "p-2", "provider": "stashdb", "gender": "male"}],
            "tags": ["Blonde", "Office"]}
    base.update(kw)
    return base


def links(db, vid):
    with db.lock:
        perf = sorted(r[0] for r in db.conn.execute("SELECT p.name FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=?", (vid,)))
        stud = sorted(r[0] for r in db.conn.execute("SELECT s.name FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=?", (vid,)))
        tags = sorted(r[0] for r in db.conn.execute("SELECT t.name FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=?", (vid,)))
    return perf, stud, tags


def apply(db, ch, mid, best, **kw):
    return prov.apply_scene_match(db, ch, mid, best, **kw)


def test_a_matched_scene_links_its_cast_studio_tags_and_details():
    db = DB(); ch, mid, vid = video(db)
    assert apply(db, ch, mid, scene()) is True
    assert links(db, vid) == (["Ava Stone", "Mick Blue"], ["Brazzers"], ["Blonde", "Office"])
    row = db.conn.execute("SELECT title,release_date,metadata_source,provider_scene_id,site_name FROM media WHERE id=?", (vid,)).fetchone()
    assert (row["title"], row["release_date"], row["metadata_source"], row["provider_scene_id"], row["site_name"]) == ("A Title", "2025-09-28", "stashdb", "scene-1", "Brazzers")
    g = db.conn.execute("SELECT gender FROM performers WHERE name='Ava Stone'").fetchone()[0]
    assert str(g).lower() == "female"


def test_the_same_performer_in_two_videos_is_one_performer_row():
    db = DB()
    a = video(db); b = video(db)
    cast = {"performers": ["Zed Quill"], "performer_details": [{"name": "Zed Quill", "id": "zq-1", "provider": "stashdb"}]}
    apply(db, a[0], a[1], scene(id="s-a", **cast)); apply(db, b[0], b[1], scene(id="s-b", **cast))
    assert db.conn.execute("SELECT COUNT(*) FROM performers WHERE name='Zed Quill'").fetchone()[0] == 1
    assert links(db, a[2])[0] == ["Zed Quill"] and links(db, b[2])[0] == ["Zed Quill"]


def test_a_performer_who_already_exists_under_other_capitals_or_accents_is_reused():
    db = DB()
    from telegram_archive.engines.entity_identity import upsert_performer
    upsert_performer(db, "ÉLODIE RAYE")                       # created the way every real row is, with its lookup key
    ch, mid, vid = video(db)
    apply(db, ch, mid, scene(performers=["Elodie Raye"], performer_details=[{"name": "Elodie Raye", "id": "er-1", "provider": "stashdb"}]))
    n = db.conn.execute("SELECT COUNT(*) FROM performers WHERE lower(name) LIKE '%elodie raye%' OR lower(name) LIKE '%lodie raye%'").fetchone()[0]
    assert n == 1


def test_a_cast_that_lists_someone_twice_links_them_once():
    db = DB(); ch, mid, vid = video(db)
    d = {"name": "Twice Named", "id": "tn-1", "provider": "stashdb"}
    apply(db, ch, mid, scene(performers=["Twice Named", "Twice Named"], performer_details=[d, dict(d)]))
    assert links(db, vid)[0] == ["Twice Named"]


def test_nameless_or_odd_cast_entries_do_not_break_the_match():
    db = DB(); ch, mid, vid = video(db)
    details = [{"name": ""}, {"name": None}, {"name": "   "}, {"name": "Real Person", "id": "rp-1", "provider": "stashdb"}, {"name": "Ünï Çödé 日本語", "id": "u-1", "provider": "stashdb"}, {"name": "N" * 300, "id": "long", "provider": "stashdb"}]
    assert apply(db, ch, mid, scene(performers=[d["name"] for d in details if d["name"]], performer_details=details)) is True
    got = links(db, vid)[0]
    assert "Real Person" in got and any("日本語" in n for n in got)


def test_the_unknown_placeholders_are_replaced_by_the_real_cast_and_studio():
    db = DB(); ch, mid, vid = video(db)
    from telegram_archive.core import resource_registry as rr
    rr.ensure_unknown_entity_links(db, [vid])
    assert links(db, vid)[:2] == (["Unknown Performer"], ["Unknown Studio"])
    apply(db, ch, mid, scene())
    perf, stud, _ = links(db, vid)
    assert "Unknown Performer" not in perf and "Unknown Studio" not in stud and stud == ["Brazzers"]


def test_a_correction_replaces_the_cast_of_an_earlier_wrong_match():
    db = DB(); ch, mid, vid = video(db)
    apply(db, ch, mid, scene(id="wrong", performers=["Wrong Person"], performer_details=[{"name": "Wrong Person", "id": "w-1", "provider": "stashdb"}], studio="Wrong Studio", site="Wrong Studio"))
    apply(db, ch, mid, scene())
    assert links(db, vid)[:2] == (["Ava Stone", "Mick Blue"], ["Brazzers"])


def test_an_operator_mapping_survives_a_provider_match():
    db = DB(); ch, mid, vid = video(db)
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO performers(name) VALUES('Operator Choice')")
        pid = db.conn.execute("SELECT id FROM performers WHERE name='Operator Choice'").fetchone()[0]
        db.conn.execute("INSERT INTO local_video_performers(video_id,performer_id,created_at) VALUES(?,?,datetime('now'))", (vid, pid)); db.conn.commit()
    apply(db, ch, mid, scene())
    with db.lock:
        kept = db.conn.execute("SELECT COUNT(*) FROM local_video_performers WHERE video_id=?", (vid,)).fetchone()[0]
    assert kept == 1


def test_locked_fields_are_left_alone():
    db = DB(); ch, mid, vid = video(db)
    apply(db, ch, mid, scene(id="first"))
    with db.lock:
        for field in ("performer", "site", "title"):
            db.conn.execute("INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,updated_at) VALUES(?,?,1,datetime('now'))", (vid, field))
        db.conn.execute("UPDATE media SET title='My Title' WHERE id=?", (vid,)); db.conn.commit()
    apply(db, ch, mid, scene(id="second", title="Provider Title", studio="Other Studio", site="Other Studio",
                              performers=["Someone Else"], performer_details=[{"name": "Someone Else", "id": "se-1", "provider": "stashdb"}]))
    perf, stud, _ = links(db, vid)
    assert perf == ["Ava Stone", "Mick Blue"] and stud == ["Brazzers"]
    assert db.conn.execute("SELECT title FROM media WHERE id=?", (vid,)).fetchone()[0] == "My Title"


def test_the_studio_is_taken_from_the_site_when_there_is_no_studio_name():
    db = DB(); ch, mid, vid = video(db)
    apply(db, ch, mid, scene(studio=None, studio_id=None, site="Only Site"))
    assert links(db, vid)[1] == ["Only Site"]


def test_a_scene_with_no_studio_keeps_the_studio_the_video_already_has():
    db = DB(); ch, mid, vid = video(db)
    apply(db, ch, mid, scene(id="first"))
    apply(db, ch, mid, scene(id="second", studio=None, studio_id=None, site=None))
    assert links(db, vid)[1] == ["Brazzers"]
    ch2, mid2, vid2 = video(db)
    apply(db, ch2, mid2, scene(studio=None, studio_id=None, site=None))
    assert links(db, vid2)[1] == []                                    # and nothing is invented for a video that had none


def test_a_scene_listing_no_cast_does_not_erase_the_cast_already_known():
    """A provider record can simply be missing its cast. Wiping the performers the video already had (from its filename) turned them
    into "Unknown Performer"."""
    db = DB(); ch, mid, vid = video(db)
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO performers(name) VALUES('Known From Filename')")
        pid = db.conn.execute("SELECT id FROM performers WHERE name='Known From Filename'").fetchone()[0]
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(?,?)", (vid, pid)); db.conn.commit()
    apply(db, ch, mid, scene(performers=[], performer_details=[]))
    assert links(db, vid)[0] == ["Known From Filename"]


def test_approving_a_suggestion_keeps_known_mappings_and_only_fills_the_gaps():
    db = DB(); ch, mid, vid = video(db)
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO performers(name) VALUES('Ava Stone')")
        pid = db.conn.execute("SELECT id FROM performers WHERE name='Ava Stone'").fetchone()[0]
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(?,?)", (vid, pid)); db.conn.commit()
    apply(db, ch, mid, scene(performers=["Ava Stone", "Mick Blue"]), preserve_existing_mappings=True)
    assert links(db, vid)[0] == ["Ava Stone"] or links(db, vid)[0] == ["Ava Stone", "Mick Blue"]
    assert db.conn.execute("SELECT COUNT(*) FROM performers WHERE name='Ava Stone'").fetchone()[0] == 1


def test_a_match_for_a_video_that_is_gone_changes_nothing():
    db = DB()
    assert apply(db, "-999999999999", 5, scene()) is False


def test_the_performer_stage_finds_the_cast_a_match_linked():
    db = DB(); ch, mid, vid = video(db)
    apply(db, ch, mid, scene())
    performers, studios = h.pipe._entity_ids(h.make_ctx(db), ch, [vid])
    assert len(performers) == 2 and len(studios) == 1


# ---------------------------------------------------------------- the providers' raw replies -------------------------------------------------
STASH = {"id": "st-1", "title": "T", "release_date": "2025-09-28", "studio": {"id": "s1", "name": "Brazzers", "parent": {"name": "MindGeek"}},
         "performers": [{"as": "Ava S.", "performer": {"id": "p1", "name": "Ava Stone", "gender": "FEMALE"}}, {"performer": {"id": "p2", "name": "Mick Blue", "gender": "MALE"}}],
         "tags": [{"name": "Blonde"}, {"name": "Office"}]}
TPDB = {"id": "tp-1", "title": "T", "date": "2025-09-28", "site": {"id": 3, "name": "Brazzers", "parent": {"name": "MindGeek"}},
        "performers": [{"id": "1", "name": "Ava Stone", "extras": {"gender": "Female"}}, {"id": "2", "name": "Mick Blue"}], "tags": [{"name": "Blonde"}]}


def test_a_stashdb_scene_reads_into_cast_studio_network_and_tags():
    s = prov._norm_stashdb(STASH)
    assert s["performers"] == ["Ava Stone", "Mick Blue"] and s["studio"] == "Brazzers" and s["parent_network"] == "MindGeek" and s["tags"] == ["Blonde", "Office"]
    assert [d["gender"] for d in s["performer_details"]] == ["FEMALE", "MALE"] and s["date"] == "2025-09-28"


def test_a_theporndb_scene_reads_into_cast_studio_and_tags():
    s = prov._norm_tpdb(TPDB)
    assert s["performers"] == ["Ava Stone", "Mick Blue"] and s["studio"] == "Brazzers" and s["tags"] == ["Blonde"]
    assert [d["gender"] for d in s["performer_details"]] == ["Female", None]        # kept when ThePornDB gives it


@pytest.mark.parametrize("raw", [
    {}, {"performers": None}, {"performers": []}, {"performers": [None, 3, "x", {}]}, {"performers": [{"performer": None}]},
    {"studio": None}, {"studio": "Brazzers"}, {"studio": {"parent": None}}, {"tags": None}, {"tags": [None, "x", {"name": None}, 5]},
    {"performers": "Ava Stone"}, {"images": None, "urls": None},
])
def test_odd_provider_replies_never_crash_the_reader(raw):
    for fn in (prov._norm_stashdb, prov._norm_tpdb):
        out = fn(dict(raw))
        assert isinstance(out["performers"], list) and isinstance(out["tags"], list)
        json.dumps(out)
