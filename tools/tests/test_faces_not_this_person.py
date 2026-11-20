"""POST /api/photos/faces/learning/not-this-person: turning down suggested look-alikes records a 'not this person' note and moves nothing."""
import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
from fastapi import APIRouter, FastAPI

from telegram_archive import config
from telegram_archive.core.database import Database
from telegram_archive.web.routes_library import register_library_routes


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        db.conn.execute("INSERT INTO performers(id,name) VALUES(1,'Ava')")
        db.conn.execute("INSERT INTO photo_face_groups(id,label,performer_id,created_at,updated_at) VALUES(1,'g1',1,'t','t'),(2,'g2',NULL,'t','t')")
        db.conn.execute("INSERT INTO photo_library_items(id,channel_id,message_id,filename,created_at,updated_at) VALUES(1,'-1',1,'a.jpg','t','t')")
        for i, g in ((10, 1), (11, 2), (12, 2)):
            db.conn.execute("INSERT INTO photo_face_detections(id,photo_id,face_index,group_id,embedding_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (i, 1, i, g, "[]", "t", "t"))
        db.conn.commit()
    router = APIRouter()
    register_library_routes(router, db=db, jobs=None, telegram=types.SimpleNamespace(connected=False, client=None), config=config, require_auth=lambda r: None, _queue_catalogue_caption_sync=lambda ids: 0)
    app = FastAPI()
    app.include_router(router)
    return db, app


def post(app, body):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post("/api/photos/faces/learning/not-this-person", json=body)
    return asyncio.run(go())


def notes(db):
    return sorted((r["detection_id"], r["performer_id"]) for r in db.conn.execute("SELECT detection_id,performer_id FROM photo_face_negatives").fetchall())


def test_turned_down_faces_become_not_this_person_notes_and_stay_where_they_are(tmp_path):
    db, app = make(tmp_path)
    r = post(app, {"performer_id": 1, "detection_ids": [11, 12]})
    assert r.status_code == 200 and r.json() == {"ok": True, "marked": 2, "skipped": 0}
    assert notes(db) == [(11, 1), (12, 1)]
    groups = {r["id"]: r["group_id"] for r in db.conn.execute("SELECT id,group_id FROM photo_face_detections").fetchall()}
    assert groups == {10: 1, 11: 2, 12: 2}          # nothing was moved


def test_saying_it_twice_is_harmless_and_a_face_already_in_her_group_is_skipped(tmp_path):
    db, app = make(tmp_path)
    post(app, {"performer_id": 1, "detection_ids": [11]})
    r = post(app, {"performer_id": 1, "detection_ids": [11, 10]})
    assert r.json()["skipped"] == 1 and notes(db) == [(11, 1)]


def test_bad_requests_are_refused(tmp_path):
    _, app = make(tmp_path)
    assert post(app, {"performer_id": 99, "detection_ids": [11]}).status_code == 404
    assert post(app, {"performer_id": 1, "detection_ids": [999]}).status_code == 404
    assert post(app, {"performer_id": 1, "detection_ids": []}).status_code == 400
    assert post(app, {"performer_id": "x", "detection_ids": [11]}).status_code == 400
    assert post(app, {"detection_ids": [11]}).status_code == 400
