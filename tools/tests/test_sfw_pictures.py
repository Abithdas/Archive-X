"""SFW studios and actors get their pictures from TMDB (by the key in Settings, Metadata); the adult providers are never asked about them."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.engines import performer_image_sync as pis
from telegram_archive.engines import screen_identify as sid
from telegram_archive.engines import studio_image_sync as sis
from telegram_archive.metadata import screen_ids as si


def tmdb(url, params, headers):
    if url.endswith("/search/company"):
        return {"results": [{"name": "Other Pictures", "logo_path": "/x.png"}, {"name": "Netflix", "logo_path": "/netflix.png"}]}
    if url.endswith("/search/person"):
        return {"results": [{"name": "Aaron Paul", "profile_path": "/small.jpg", "popularity": 3}, {"name": "Aaron Paul", "profile_path": "/known.jpg", "popularity": 40}, {"name": "Aaron Pauly", "profile_path": "/no.jpg", "popularity": 99}]}
    return {}


def _no_adult_provider(*a, **k):
    raise AssertionError("an adult provider was asked about an SFW name")


def test_a_studio_and_an_actor_of_movies_get_their_picture_from_tmdb(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", tmdb)
    cached: list[str] = []

    async def fake_cache(db, kind, entity_id, url, **kw):
        cached.append(url)
        return True
    monkeypatch.setattr(sis, "prefetch_entity_image", fake_cache)
    monkeypatch.setattr(pis, "prefetch_entity_image", fake_cache)
    monkeypatch.setattr(sis, "studio_image_from_providers", _no_adult_provider)
    monkeypatch.setattr(pis, "performer_image_from_providers", _no_adult_provider)
    db = Database(tmp_path / "p.db")
    db.set_setting("tmdb_api_key", "k" * 32)
    studio = sid._entity(db, "studios", "Netflix")
    actor = sid._entity(db, "performers", "Aaron Paul")
    assert asyncio.run(sis.refresh_studio_image(db, studio))["ok"] is True
    assert asyncio.run(pis.refresh_performer_image(db, actor, force_api=False))["ok"] is True
    assert cached == [f"{si.IMAGE}/w185/netflix.png", f"{si.IMAGE}/w185/known.jpg"]       # the exact name, the best-known person
    with db.reading() as c:
        assert c.execute("SELECT image FROM studios WHERE id=?", (studio,)).fetchone()[0] == f"{si.IMAGE}/w185/netflix.png"


def test_a_title_identified_with_company_logos_gives_its_studios_their_pictures(tmp_path):
    db = Database(tmp_path / "t.db")
    d = {"kind": "tv", "tmdb_id": 1396, "title": "Breaking Bad", "companies": ["AMC"], "company_info": [{"name": "AMC", "id": 174, "logo": "/amc.png"}], "cast": [], "genres": []}
    cast, studios, genres = sid._title_entities(db, d, None)
    with db.reading() as c:
        row = c.execute("SELECT image,content_rating FROM studios WHERE id=?", (studios[0],)).fetchone()
    assert row["image"] == f"{si.IMAGE}/w185/amc.png" and row["content_rating"] == "sfw"
