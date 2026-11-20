"""POST /api/photos/{channel}/{message}/delete against a real temporary database and a fake Telegram client."""
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
    def __init__(self, exists=True, refuses=False):
        self.exists, self.refuses, self.deleted = exists, refuses, []

    async def get_entity(self, channel_id):
        return f"entity:{channel_id}"

    async def get_messages(self, entity, ids):
        return types.SimpleNamespace(id=ids, photo=object(), document=None, file=None) if self.exists else None

    async def delete_messages(self, entity, ids, revoke=False):
        self.deleted.append((list(ids), revoke))
        if not self.refuses:
            self.exists = False


def make(tmp_path, client, connected=True):
    db = Database(tmp_path / "t.db")
    with db.lock:
        db.conn.execute("INSERT INTO photo_library_items(channel_id,message_id,filename,created_at,updated_at) VALUES('-1001',7,'a.jpg','2025-01-01','2025-01-01')")
        db.conn.commit()
    telegram = types.SimpleNamespace(connected=connected, client=client)
    router = APIRouter()
    register_library_routes(router, db=db, jobs=None, telegram=telegram, config=config, require_auth=lambda r: None, _queue_catalogue_caption_sync=lambda ids: 0)
    app = FastAPI()
    app.include_router(router)
    return db, app


def post(app, body, path="/api/photos/-1001/7/delete"):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post(path, json=body)
    return asyncio.run(go())


def count(db):
    with db.lock:
        return db.conn.execute("SELECT COUNT(*) FROM photo_library_items WHERE message_id=7").fetchone()[0]


def test_archive_only_removes_the_record_and_leaves_telegram_alone(tmp_path):
    client = FakeClient()
    db, app = make(tmp_path, client)
    r = post(app, {"scope": "archive_only"})
    assert r.status_code == 200, r.text
    assert r.json()["telegram_deleted"] is False and count(db) == 0 and client.deleted == []


def test_archive_and_telegram_deletes_with_revoke_and_verifies(tmp_path):
    client = FakeClient()
    db, app = make(tmp_path, client)
    r = post(app, {"scope": "archive_and_telegram"})
    assert r.status_code == 200, r.text
    assert r.json()["telegram_deleted"] is True and client.deleted == [([7], True)] and count(db) == 0


def test_a_message_telegram_already_removed_still_clears_the_local_entry(tmp_path):
    db, app = make(tmp_path, FakeClient(exists=False))
    r = post(app, {"scope": "archive_and_telegram"})
    assert r.status_code == 200 and r.json()["telegram_was_already_missing"] is True and count(db) == 0


def test_when_telegram_does_not_confirm_the_photo_is_kept(tmp_path):
    db, app = make(tmp_path, FakeClient(refuses=True))
    r = post(app, {"scope": "archive_and_telegram"})
    assert r.status_code == 502 and "did not confirm" in r.text and count(db) == 1


def test_validation_and_disconnected_telegram(tmp_path):
    db, app = make(tmp_path, FakeClient(), connected=False)
    assert post(app, {"scope": "nonsense"}).status_code == 400
    assert post(app, {"scope": "archive_and_telegram"}).status_code == 503 and count(db) == 1
    assert post(app, {"scope": "archive_only"}, path="/api/photos/-1001/99/delete").status_code == 404


def test_the_server_keeps_answering_while_the_database_is_busy(tmp_path):
    db, app = make(tmp_path, FakeClient())

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
        time.sleep(0.05)
        tick = asyncio.create_task(ticker())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/photos/-1001/7/delete", json={"scope": "archive_only"})
        stop.set()
        await tick
        t.join()
        return r, ticks

    r, ticks = asyncio.run(go())
    assert r.status_code == 200
    assert ticks >= 8, f"the event loop was blocked ({ticks} ticks in about 0.8 s)"
