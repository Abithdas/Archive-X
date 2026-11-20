"""POST /api/library/delete against a real temporary database and a fake Telegram client."""
import asyncio
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
from fastapi import APIRouter, FastAPI

from telegram_archive import config
from telegram_archive.core.database import Database
from telegram_archive.web.routes_library import register_library_routes


class FakeClient:
    def __init__(self, fail_delete=False, thumb_is_image=True):
        self.deleted = []
        self.fail_delete = fail_delete
        self.thumb_is_image = thumb_is_image

    async def get_entity(self, channel_id):
        return f"entity:{channel_id}"

    async def get_messages(self, source, ids):
        return types.SimpleNamespace(photo=object() if self.thumb_is_image else None, document=None)

    async def delete_messages(self, source, ids):
        if self.fail_delete:
            raise RuntimeError("flood")
        self.deleted.append(list(ids))


def make(tmp_path, client):
    db = Database(tmp_path / "t.db")
    with db.lock:
        db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status) VALUES('-1001',50,'v.mp4','V','ACTIVE')")
        db.conn.commit()
    telegram = types.SimpleNamespace(connected=True, client=client)
    router = APIRouter()
    register_library_routes(router, db=db, jobs=None, telegram=telegram, config=config, require_auth=lambda r: None, _queue_catalogue_caption_sync=lambda ids: 0)
    app = FastAPI()
    app.include_router(router)
    return db, app


def post(app, body):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post("/api/library/delete", json=body)
    return asyncio.run(go())


def status(db):
    with db.lock:
        return db.conn.execute("SELECT status FROM media WHERE source_message_id=50").fetchone()[0]


def test_deletes_the_video_and_hides_the_library_row(tmp_path):
    client = FakeClient()
    db, app = make(tmp_path, client)
    r = post(app, {"channel_id": -1001, "message_id": 50, "confirm_delete": True})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["deleted"] and r.json()["thumbnail_deleted"] is False
    assert client.deleted == [[50]]
    assert status(db) == "SOURCE_DELETED"


def test_needs_explicit_confirmation_and_an_active_video(tmp_path):
    client = FakeClient()
    db, app = make(tmp_path, client)
    assert post(app, {"channel_id": -1001, "message_id": 50}).status_code == 400
    assert post(app, {"channel_id": -1001, "message_id": 999, "confirm_delete": True}).status_code == 404
    assert client.deleted == [] and status(db) == "ACTIVE"


def test_a_telegram_failure_leaves_the_library_row_alone(tmp_path):
    client = FakeClient(fail_delete=True)
    db, app = make(tmp_path, client)
    r = post(app, {"channel_id": -1001, "message_id": 50, "confirm_delete": True})
    assert r.status_code == 502 and "could not confirm deletion" in r.text
    assert status(db) == "ACTIVE"


def test_the_server_keeps_answering_while_the_database_is_busy(tmp_path):
    """A busy database must not freeze the event loop (every other request, and any running upload, would stall)."""
    client = FakeClient()
    db, app = make(tmp_path, client)

    def hold_lock():
        with db.lock:
            time.sleep(0.8)

    async def go():
        ticks = 0
        stop = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not stop.is_set():
                await asyncio.sleep(0.05)
                ticks += 1

        t = threading.Thread(target=hold_lock)
        t.start()
        time.sleep(0.05)                       # the lock is now held
        tick = asyncio.create_task(ticker())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/library/delete", json={"channel_id": -1001, "message_id": 50, "confirm_delete": True})
        stop.set()
        await tick
        t.join()
        return r, ticks

    r, ticks = asyncio.run(go())
    assert r.status_code == 200
    assert ticks >= 8, f"the event loop was blocked ({ticks} ticks in about 0.8 s)"
