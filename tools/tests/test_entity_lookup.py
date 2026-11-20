import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.web.performer_catalogue import lookup_entities_sync


def test_lookup_returns_only_the_named_rows_case_insensitively_and_keeps_trans(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for n, g in (("Ava Stone", "female"), ("Mia Rivera", "transgender_female"), ("Zoe Ray", "male")):
            db.conn.execute("INSERT INTO performers(name,gender) VALUES(?,?)", (n, g))
        for n in ("Northlight", "Aurora"):
            db.conn.execute("INSERT INTO studios(name) VALUES(?)", (n,))
        db.conn.commit()
    rows = lookup_entities_sync(db, "performers", ["ava stone", "MIA RIVERA", "Nobody"])
    assert sorted(r["name"] for r in rows) == ["Ava Stone", "Mia Rivera"]
    assert {r["name"]: r["gender"] for r in rows} == {"Ava Stone": "female", "Mia Rivera": "trans"}      # trans survives
    assert [r["name"] for r in lookup_entities_sync(db, "studios", ["northlight"])] == ["Northlight"]
    assert lookup_entities_sync(db, "studios", []) == [] and lookup_entities_sync(db, "other", ["x"]) == []
