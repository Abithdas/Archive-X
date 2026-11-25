"""GET /api/channel-maintenance/duplicates for one channel still answers from what is saved when Telegram cannot be asked."""
import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
import pytest
from fastapi import APIRouter, FastAPI

from telegram_archive import config
from telegram_archive.core.database import Database
from telegram_archive.engines import channel_duplicate_audit as audit
from telegram_archive.web.routes_entities import register_entity_routes

ROWS = [{"id": 1, "source_message_id": 10, "document_id": "D1", "filename": "a.mp4", "size": 1000, "channel_id": "C"},
        {"id": 2, "source_message_id": 20, "document_id": "D1", "filename": "b.mp4", "size": 1000, "channel_id": "C"}]


def build(tmp_path, monkeypatch, *, connected, channel="-1001"):
    db = Database(tmp_path / "t.db")
    with db.lock:   # the channel is known to the saved index through one of its videos
        db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES(1,?,10,'a.mp4','A','ACTIVE')", (channel,))
        db.conn.commit()
    monkeypatch.setattr(audit, "load_channel_rows", lambda db_, ch: [dict(r, channel_id=str(ch)) for r in ROWS])
    monkeypatch.setattr(audit, "missing_hash_candidate_buckets", lambda rows: [list(rows)])   # something would need a fingerprint from Telegram
    telegram = types.SimpleNamespace(connected=connected, client=None)
    router = APIRouter()
    register_entity_routes(router, db=db, jobs=None, telegram=telegram, require_auth=lambda r: None, config=config,
                           _proxy_entity_image=None, _queue_catalogue_caption_sync=lambda ids: 0, _sync_catalogue_caption=None)
    app = FastAPI()
    app.include_router(router)
    return app


def get(app, channel):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.get("/api/channel-maintenance/duplicates", params={"channel_id": channel})
    return asyncio.run(go())


@pytest.mark.parametrize("channel", ["-1001", "local:folder-3"])
def test_scan_without_telegram_is_answered_from_the_saved_index(tmp_path, monkeypatch, channel):
    r = get(build(tmp_path, monkeypatch, connected=False, channel=channel), channel)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["channel_id"] == channel
    assert body["duplicate_group_count"] == 1
    assert any("Telegram is not connected" in w for w in body["fingerprint_warnings"])


def test_a_missing_channel_id_is_still_refused(tmp_path, monkeypatch):
    assert get(build(tmp_path, monkeypatch, connected=False), "").status_code == 400
