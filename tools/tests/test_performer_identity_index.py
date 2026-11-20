"""assess_performer_identity used to read every performer (bio included) and compare the name against all of them on each call, on the event
loop: 8 to 15 second freezes in a user's log. It now works from an index kept until the performers change. These tests hold it to the old
answers: a plain re-statement of the old algorithm is compared with the new code on random names, and the index must follow edits."""
import json
import os
import random
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axpid_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def reference(db, name, provider="", provider_id=None):
    """The matching part of the old implementation, row by row over the whole table."""
    from telegram_archive.engines import entity_identity as ei
    key, norm = ei.canonical_key(ei.display_name(name)), ei.normalized_name(ei.display_name(name))
    rows = [dict(r) for r in db.conn.execute("SELECT * FROM performers ORDER BY id").fetchall()]
    canonical, alias = {}, {}
    for row in rows:
        rid = int(row["id"])
        if ((key and (ei.canonical_key(str(row.get("name") or "")) == key or str(row.get("canonical_key") or "") == key))
                or (norm and str(row.get("normalized_name") or "") == norm)):
            canonical[rid] = row
            continue
        values = ei._load_json(row.get("aliases"), [])
        if key and any(isinstance(a, str) and ei.canonical_key(a) == key for a in values):
            alias[rid] = row
    matched = list(canonical.values()) if canonical else list(alias.values())
    ids = {int(r["id"]) for r in matched}
    near = []
    if len(key) >= 7:
        for row in rows:
            if int(row["id"]) in ids:
                continue
            variants = [str(row.get("name") or "")] + [x for x in ei._load_json(row.get("aliases"), []) if isinstance(x, str)]
            best, best_variant = 0.0, ""
            for v in variants:
                vk = ei.canonical_key(v)
                if len(vk) < 7 or abs(len(key) - len(vk)) > max(2, int(max(len(key), len(vk)) * 0.12)):
                    continue
                ratio = SequenceMatcher(None, key, vk).ratio()
                if ratio > best:
                    best, best_variant = ratio, v
            if best >= 0.80:
                near.append((best, row, best_variant))
    near.sort(key=lambda t: (-t[0], int(t[1]["id"])))
    return [int(r["id"]) for r in matched], [(int(r["id"]), round(ratio, 3), v) for ratio, r, v in near[:8]]


_DB = None


def seeded_db(n=300):
    global _DB
    from telegram_archive.core.database import Database
    if _DB is None:
        _DB = Database()   # one per process: a second one on the same data folder would wait for the first one's lock
    db = _DB
    with db.lock:
        db.conn.execute("DELETE FROM performers")
        db.conn.commit()
    rng = random.Random(7)
    first = ["Anna", "Ana", "Anne", "Sarah", "Sara", "Zoë", "Zoe", "Mia", "Maya", "Lena", "Léna", "Eva", "Ava", "Isabella", "Isabela", "Charlotte"]
    last = ["Vandella", "Vandela", "Stone", "Stoné", "Blue", "Cruz", "Cruise", "Moon", "Lane", "Rose", "Rosé", "Hart", "Heart"]
    names = list(dict.fromkeys(f"{rng.choice(first)} {rng.choice(last)}" for _ in range(n * 3)))[:n]   # names are unique in the table
    with db.lock:
        for i, nm in enumerate(names):
            aliases = [f"{rng.choice(first)} {rng.choice(last)}" for _ in range(rng.randint(0, 2))]
            db.conn.execute("INSERT INTO performers(name,aliases,provider_ids,bio) VALUES(?,?,?,?)",
                            (nm, json.dumps(aliases), json.dumps({"stashdb": f"s{i}", "theporndb": [f"t{i}", f"u{i}"]}), "bio " * 50))
        db.conn.commit()
    return db, names, rng


def test_the_index_gives_the_old_answers():
    from telegram_archive.engines import entity_identity as ei
    db, names, rng = seeded_db()
    queries = names[:40] + ["Sarah  Vandella", "SARAH-VANDELLA", "Sarah Vandellaa", "Isabella Stonee", "Zoe Rose", "Nobody Atall", "Q", ""]
    for q in queries:
        got = ei.assess_performer_identity(db, q)
        want_ids, want_near = reference(db, q)
        if got["status"] in ("existing", "ambiguous", "new"):
            got_ids = [c["id"] for c in got["candidates"]] if got["status"] != "new" else []
            if got["status"] == "existing" and got.get("matched_by") != "provider_id":
                assert got_ids == want_ids, q
            if got["status"] == "ambiguous":
                assert got_ids == want_ids, q
            if got["status"] == "new":
                assert want_ids == [], q
            assert [(d["id"], d["similarity"], d["matched_variant"]) for d in got.get("possible_duplicates", [])] == want_near, q


def test_provider_ids_are_found_in_their_own_namespace_only():
    from telegram_archive.engines import entity_identity as ei
    db, names, _ = seeded_db(60)
    row = db.conn.execute("SELECT id,name FROM performers WHERE provider_ids LIKE '%\"s7\"%'").fetchone()
    hit = ei.assess_performer_identity(db, "Whoever", provider="stashdb", provider_id="s7")
    assert (hit["status"], hit["id"], hit["matched_by"]) == ("existing", row["id"], "provider_id")
    other = ei.assess_performer_identity(db, "Whoever Else", provider="theporndb", provider_id="s7")
    assert other["status"] != "existing" or other.get("matched_by") != "provider_id"
    listed = ei.assess_performer_identity(db, "Whoever", provider="theporndb", provider_id="u7")
    assert (listed["status"], listed["id"]) == ("existing", row["id"])


def test_the_index_follows_edits():
    from telegram_archive.engines import entity_identity as ei
    db, _, _ = seeded_db(40)
    assert ei.assess_performer_identity(db, "Brand New Person")["status"] == "new"
    assert getattr(db, "_performer_identity_index", None), "the index should be kept between calls (the change triggers exist)"
    with db.lock:
        db.conn.execute("INSERT INTO performers(name,aliases,provider_ids) VALUES('Brand New Person','[]','{}')")
        db.conn.commit()
    assert ei.assess_performer_identity(db, "Brand New Person")["status"] == "existing"
    with db.lock:
        db.conn.execute("UPDATE performers SET name='Renamed Person' WHERE name='Brand New Person'")
        db.conn.commit()
    assert ei.assess_performer_identity(db, "Brand New Person")["status"] == "new"
    assert ei.assess_performer_identity(db, "Renamed Person")["status"] == "existing"
