"""Choosing which videos need snapshots checks about 40 local files per video. On a big channel that took minutes in silence and the job's
watchdog stopped the job ("no_activity", 10:55 in a user's log). The loop must report progress now and then."""
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axsnap_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def test_the_local_check_reports_progress():
    from telegram_archive.core.database import Database
    from telegram_archive.engines import preview_prefetch
    db = Database()
    with db.lock:
        for i in range(1, 451):
            db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,duration) VALUES('-1009',?,?,?,60)", (i, f"v{i}.mp4", f"v{i}"))
        db.conn.commit()
    calls = []
    rows = preview_prefetch._pending_rows(db, 0, [-1009], "missing", None, False, False, False, lambda done, total: calls.append((done, total)))
    assert len(rows) == 450                       # nothing has local snapshots, so every video needs work
    assert calls == [(200, 450), (400, 450)]
    db.close()
