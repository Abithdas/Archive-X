"""GET /api/photos/content-tags counts each tag inside the filters that are already ticked (a faceted count), against a real temporary database."""
import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
from fastapi import APIRouter, FastAPI

from telegram_archive import config
from telegram_archive.core.database import Database
from telegram_archive.engines import photo_content_tags as ct
from telegram_archive.web.routes_library import register_library_routes


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for i, ch in ((1, "-1"), (2, "-1"), (3, "-1"), (4, "-2")):
            db.conn.execute("INSERT INTO photo_library_items(id,channel_id,message_id,filename,created_at,updated_at) VALUES(?,?,?,?,?,?)", (i, ch, i, f"{i}.jpg", "t", "t"))
        db.conn.commit()
    ct.store_tags(db, 1, {"breasts": {"score": .9, "regions": 1}, "hair_long": {"score": .9, "regions": 1}, "hair_black": {"score": .9, "regions": 1}})
    ct.store_tags(db, 2, {"hair_long": {"score": .9, "regions": 1}, "hair_red": {"score": .9, "regions": 1}})
    ct.store_tags(db, 3, {"breasts": {"score": .9, "regions": 1}, "hair_black": {"score": .9, "regions": 1}})
    ct.store_tags(db, 4, {"feet": {"score": .9, "regions": 1}, "hair_long": {"score": .9, "regions": 1}})
    router = APIRouter()
    register_library_routes(router, db=db, jobs=None, telegram=types.SimpleNamespace(connected=False, client=None), config=config, require_auth=lambda r: None, _queue_catalogue_caption_sync=lambda ids: 0)
    app = FastAPI()
    app.include_router(router)
    return app


def get(app, query=""):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return (await c.get("/api/photos/content-tags" + query)).json()
    return asyncio.run(go())


def counts(body):
    return {t["tag"]: t["count"] for t in body["tags"] + body["looks"]["tags"]}


def test_without_filters_the_counts_are_the_overall_ones(tmp_path):
    body = get(make(tmp_path))
    assert body["filtered"] is False
    assert counts(body) == {"breasts": 2, "feet": 1, "hair_long": 3, "hair_black": 2, "hair_red": 1}


def test_ticking_long_hair_recounts_every_other_tag_among_long_haired_photos(tmp_path):
    body = get(make(tmp_path), "?content_tags=hair_long")
    assert body["filtered"] is True
    # long hair: photos 1, 2 and 4. Black hair is on 1; red on 2; breasts on 1; feet on 4.
    assert counts(body) == {"breasts": 1, "feet": 1, "hair_long": 3, "hair_black": 1, "hair_red": 1}


def test_two_ticked_tags_must_both_be_present(tmp_path):
    body = get(make(tmp_path), "?content_tags=hair_long,hair_black")
    # only photo 1 has both; every tag then counts that one photo or none
    assert counts(body) == {"breasts": 1, "feet": 0, "hair_long": 1, "hair_black": 1, "hair_red": 0}


def test_a_tag_with_no_photo_left_stays_listed_with_zero(tmp_path):
    body = get(make(tmp_path), "?content_tags=hair_red")
    assert counts(body)["breasts"] == 0 and "breasts" in counts(body)


def test_the_channel_filter_counts_only_that_channel(tmp_path):
    app = make(tmp_path)
    body = get(app, "?channels=-2")
    assert counts(body) == {"breasts": 0, "feet": 1, "hair_long": 1, "hair_black": 0, "hair_red": 0}
    body = get(app, "?channels=-1&content_tags=breasts")
    assert counts(body)["hair_black"] == 2 and counts(body)["hair_long"] == 1 and counts(body)["feet"] == 0


def test_overall_progress_numbers_ignore_the_filters(tmp_path):
    body = get(make(tmp_path), "?content_tags=hair_red")
    assert body["total_photos"] == 4
