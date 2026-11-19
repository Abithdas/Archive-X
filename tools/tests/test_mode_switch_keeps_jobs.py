"""Switching between SFW and NSFW only changes what pages show: a running job is neither cancelled nor changed, and the lanes still list it."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axmk_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from telegram_archive.core.database import Database  # noqa: E402


def _client(db):
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, H


def test_running_jobs_survive_every_mode_switch_and_stay_in_the_lanes(tmp_path):
    db = Database(tmp_path / "a.db")
    c, H = _client(db)
    jid = db.create_job("METADATA_CHANNEL_PIPELINE", "-1001", "", False, detail="{}")
    db.update_job(jid, status="RUNNING", current_operation="Scanning", progress=40)
    before = {k: db.get_job(jid)[k] for k in ("status", "current_operation", "progress")}
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    for mode in ("sfw", "nsfw", "sfw"):
        body = {"mode": mode, "pin": "2468"}
        assert c.post("/api/content-mode", json=body, headers=H).status_code == 200
        assert {k: db.get_job(jid)[k] for k in before} == before
        lanes = c.get("/api/telegram-transfer/lanes").json()
        assert any(j["id"] == jid and j["status"] == "RUNNING" for j in lanes["jobs"]), mode


def test_a_job_does_not_inherit_the_pages_content_filter():
    import asyncio
    from telegram_archive.core.content_mode import FILTER_ACTIVE
    seen = []

    async def probe():
        # what JobManager._run_job does first, in a task that began inside a filtered request
        FILTER_ACTIVE.set(False)
        seen.append(FILTER_ACTIVE.get())

    async def request():
        FILTER_ACTIVE.set(True)
        await asyncio.create_task(probe())
        return FILTER_ACTIVE.get()
    assert asyncio.run(request()) is True and seen == [False]
