"""The metadata-match stage ends with ctx.report(detail="fingerprints 0/21 ok ..."). It runs inside the channel pipeline's job and shares its row,
so that sentence replaced the job's saved settings; recovering the job then failed with "Select at least one source channel"."""
import json
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axjob_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def _job(db, job_type, detail):
    jid = db.create_job(job_type, "", "", False, detail=detail)
    db.update_job(jid, status="RUNNING")
    return jid


def test_a_progress_sentence_does_not_replace_a_pipeline_jobs_settings():
    from telegram_archive.core.database import Database
    from telegram_archive.jobs.context import JobContext
    db = Database()
    settings = json.dumps({"channel_ids": ["-1001453865657"], "unbounded": False})
    jid = _job(db, "METADATA_CHANNEL_PIPELINE", settings)
    ctx = JobContext(jid, db.get_job(jid), db, None, None, None)
    ctx.report(completed=21, total=21, operation="Finished", detail="fingerprints 0/21 ok · 21 fallback", force=True)
    row = db.get_job(jid)
    assert json.loads(row["detail"]) == json.loads(settings)
    assert row["current_operation"] == "Finished"            # the rest of the report still lands
    ctx.report(detail=json.dumps({"channel_ids": ["-1001453865657"], "progress": {"done": 1}}), force=True)
    assert json.loads(db.get_job(jid)["detail"])["progress"] == {"done": 1}   # a settings update (JSON) is still stored
    db.close()


def test_other_jobs_keep_showing_their_progress_line():
    from telegram_archive.core.database import Database
    from telegram_archive.jobs.context import JobContext
    db = Database()
    jid = _job(db, "SOME_OTHER_JOB", json.dumps({"a": 1}))
    ctx = JobContext(jid, db.get_job(jid), db, None, None, None)
    ctx.report(detail="Polling RSS feed", force=True)
    assert db.get_job(jid)["detail"] == "Polling RSS feed"
    db.close()
