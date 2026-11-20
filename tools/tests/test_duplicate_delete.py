"""POST /api/channel-maintenance/delete-duplicate with a real temporary database and a fake Telegram client."""
import asyncio
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
import pytest
from fastapi import APIRouter, FastAPI

from telegram_archive import config
from telegram_archive.core.database import Database
from telegram_archive.web.routes_entities import register_entity_routes


def vmsg(i):
    return types.SimpleNamespace(id=i, video=True, document=None, file=types.SimpleNamespace(size=1000, name=f"v{i}.mp4"))


class FakeClient:
    def __init__(self, fail_delete=False):
        self.deleted, self.fail_delete = [], fail_delete

    async def get_entity(self, channel_id):
        return f"entity:{channel_id}"

    async def get_messages(self, source, ids):
        return vmsg(ids)

    async def delete_messages(self, source, ids, **kw):
        if self.fail_delete:
            raise RuntimeError("flood")
        self.deleted.append(list(ids))


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for i, mid in ((1, 10), (2, 20)):
            db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,?,'ACTIVE')", (i, "-1001", mid, f"v{mid}.mp4", f"V{mid}"))
        db.conn.commit()
    rows = [{"id": 1, "source_message_id": 10, "document_id": "D1", "filename": "v10.mp4", "size": 1000},
            {"id": 2, "source_message_id": 20, "document_id": "D1", "filename": "v20.mp4", "size": 1000}]
    from telegram_archive.engines import channel_duplicate_audit as audit, safe_message_move as smm
    from telegram_archive.playback import stream_server

    async def no_companion(*a, **k):
        return 0
    monkeypatch.setattr(audit, "load_channel_rows", lambda db_, ch: list(rows))
    monkeypatch.setattr(smm, "discover_companion_thumbnail", no_companion)
    monkeypatch.setattr(stream_server, "make_record", lambda m: {"document_id": "D1"})
    state = types.SimpleNamespace(client=FakeClient(), db=db)
    telegram = types.SimpleNamespace(connected=True, client=state.client)
    router = APIRouter()
    register_entity_routes(router, db=db, jobs=None, telegram=telegram, require_auth=lambda r: None, config=config,
                           _proxy_entity_image=None, _queue_catalogue_caption_sync=lambda ids: 0, _sync_catalogue_caption=None)
    app = FastAPI()
    app.include_router(router)
    state.app, state.telegram = app, telegram
    return state


BODY = {"channel_id": -1001, "message_id": 10, "keep_message_id": 20, "confirm_delete": True}


def post(app, body):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post("/api/channel-maintenance/delete-duplicate", json=body)
    return asyncio.run(go())


def status_of(db, i):
    with db.lock:
        return db.conn.execute("SELECT status FROM media WHERE id=?", (i,)).fetchone()[0]


def test_deletes_the_selected_copy_and_keeps_the_other(env):
    r = post(env.app, BODY)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and j["deleted_message_id"] == 10 and j["kept_message_id"] == 20
    assert env.client.deleted == [[10]]
    assert status_of(env.db, 1) == "SOURCE_DELETED" and status_of(env.db, 2) == "ACTIVE"


def test_checks_the_request_before_touching_telegram(env):
    assert post(env.app, {**BODY, "confirm_delete": False}).status_code == 400
    assert post(env.app, {**BODY, "keep_message_id": 10}).status_code == 400
    assert post(env.app, {**BODY, "message_id": 99}).status_code == 404
    assert env.client.deleted == [] and status_of(env.db, 1) == "ACTIVE"


def test_a_changed_identity_stops_the_delete(env, monkeypatch):
    from telegram_archive.playback import stream_server
    docs = iter(["D1", "OTHER"])
    monkeypatch.setattr(stream_server, "make_record", lambda m: {"document_id": next(docs)})
    r = post(env.app, BODY)
    assert r.status_code == 409, r.text
    assert env.client.deleted == [] and status_of(env.db, 1) == "ACTIVE"


def test_a_telegram_failure_leaves_the_library_alone(env):
    env.client.fail_delete = True
    r = post(env.app, BODY)
    assert r.status_code == 502 and "did not confirm" in r.text
    assert status_of(env.db, 1) == "ACTIVE"


def test_the_server_keeps_answering_while_the_database_is_busy(env):
    def hold_lock():
        with env.db.lock:
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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://t") as c:
            r = await c.post("/api/channel-maintenance/delete-duplicate", json=BODY)
        stop.set()
        await tick
        t.join()
        return r, ticks

    r, ticks = asyncio.run(go())
    assert r.status_code == 200, r.text
    assert ticks >= 8, f"the event loop was blocked ({ticks} ticks in about 0.8 s)"
