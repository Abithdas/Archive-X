"""name_lists_sql() must give the same four lists, in the same order, as the old four-way join."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.web.video_lists import name_lists_sql

LEGACY = (
    "SELECT m.id, GROUP_CONCAT(DISTINCT p.name) performers, GROUP_CONCAT(DISTINCT lp.name) local_performers, "
    "GROUP_CONCAT(DISTINCT s.name) studios, GROUP_CONCAT(DISTINCT t.name) tags FROM media m "
    "LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id "
    "LEFT JOIN local_video_performers lvp ON lvp.video_id=m.id LEFT JOIN performers lp ON lp.id=lvp.performer_id "
    "LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id "
    "LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id "
    "GROUP BY m.id ORDER BY m.id"
)
NEW = "SELECT m.id, " + name_lists_sql() + " FROM media m GROUP BY m.id ORDER BY m.id"


def test_same_lists_on_random_data(tmp_path):
    rnd = random.Random(7)
    db = Database(tmp_path / "t.db")
    with db.lock:
        c = db.conn
        for i in range(1, 41):
            c.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,?,'ACTIVE')", (i, "-1001", i, f"{i}.mp4", f"V{i}"))
        for table in ("performers", "studios", "tags"):
            for j in range(1, 13):
                name = f"{table[:2]}{j}"
                c.execute(f"INSERT INTO {table}(id,name) VALUES(?,?)", (j, name))
        for vid in range(1, 41):
            for table, link, col in (("performers", "video_performers", "performer_id"), ("local_video_performers", None, None),
                                     ("studios", "video_studios", "studio_id"), ("tags", "video_tags", "tag_id")):
                ids = rnd.sample(range(1, 13), rnd.randint(0, 6))
                tbl = link or "local_video_performers"
                column = col or "performer_id"
                for x in ids:
                    if tbl == "local_video_performers":
                        c.execute("INSERT INTO local_video_performers(video_id,performer_id,created_at) VALUES(?,?,'2026-01-01')", (vid, x))
                    else:
                        c.execute(f"INSERT OR IGNORE INTO {tbl}(video_id,{column}) VALUES(?,?)", (vid, x))
        c.commit()
        old = [tuple(r) for r in c.execute(LEGACY)]
        new = [tuple(r) for r in c.execute(NEW)]
    assert len(old) == 40
    assert any(r[1] for r in old) and any(r[2] for r in old) and any(r[3] for r in old) and any(r[4] for r in old)
    assert old == new
