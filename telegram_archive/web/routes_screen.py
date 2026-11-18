"""Movies and TV (the SFW side): provider keys, running the identification, and the person's choices for uncertain matches."""
from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, HTTPException, Request

from ..engines import screen_identify as sid
from ..metadata import screen_ids as si

KEYS = {"tmdb_api_key": "tmdb", "omdb_api_key": "omdb", "opensubtitles_api_key": "opensubtitles"}


def _mask(k: str) -> str:
    return (k[:4] + "…" + k[-4:]) if len(k) > 8 else ("set" if k else "")


def register_screen_routes(router: APIRouter, *, db, require_auth, fetch=None):
    run_lock = threading.Lock()

    def start(channel: str | None) -> bool:
        if not run_lock.acquire(blocking=False):
            return False

        def work():
            try:
                sid.run(db, channel, fetch)
            except Exception as exc:
                db.log(f"Movie and show identification failed: {type(exc).__name__}: {exc}"[:300], "WARN")
            finally:
                run_lock.release()
        threading.Thread(target=work, name="screen-identify", daemon=True).start()
        return True

    @router.get("/api/screen/settings")
    async def screen_settings(request: Request):
        require_auth(request)
        out = {}
        for key in KEYS:
            v = str(db.setting(key) or "")
            out[key] = _mask(v)
            out[key.replace("_api_key", "") + "_configured"] = bool(v)
        return out

    @router.post("/api/screen/settings")
    async def screen_settings_save(request: Request):
        """Save keys (a blank field keeps what is saved). `clear` lists keys to remove."""
        require_auth(request)
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        saved = []
        for key in KEYS:
            v = str(body.get(key) or "").strip()
            if v:
                db.set_setting(key, v)
                saved.append(key)
        for key in body.get("clear") or []:
            if key in KEYS:
                db.set_setting(key, "")
        return {"ok": True, "saved": saved}

    @router.post("/api/screen/check")
    async def screen_check(request: Request):
        """Try a saved key against its provider with one harmless request."""
        require_auth(request)
        body = await request.json()
        provider = str((body or {}).get("provider") or "")
        screen = sid.make_screen(db, fetch)

        def work():
            if provider == "tmdb":
                if not screen.tmdb:
                    return {"ok": False, "error": "No TMDB key saved"}
                got = screen._t("/configuration")
                return {"ok": bool(got)} if got else {"ok": False, "error": "TMDB did not accept the key"}
            if provider == "omdb":
                if not screen.omdb:
                    return {"ok": False, "error": "No OMDb key saved"}
                got = screen.fetch(si.OMDB, {"i": "tt0111161", "apikey": screen.omdb}, {}) or {}
                return {"ok": got.get("Response") == "True"} if got.get("Response") == "True" else {"ok": False, "error": str(got.get("Error") or "OMDb did not accept the key")}
            if provider == "opensubtitles":
                if not screen.os_key:
                    return {"ok": False, "error": "No OpenSubtitles key saved"}
                screen.fetch(si.OPENSUBTITLES, {"moviehash": "0000000000000000"}, {"Api-Key": screen.os_key, "Accept": "application/json", "User-Agent": "ArchiveX v1"})
                return {"ok": True}
            raise HTTPException(400, "provider must be tmdb, omdb or opensubtitles")
        try:
            return await asyncio.to_thread(work)
        except HTTPException:
            raise
        except Exception as exc:
            db.log(f"Provider key check failed ({provider}): {type(exc).__name__}: {exc}"[:300], "WARN")
            return {"ok": False, "error": "The provider did not accept the key (or could not be reached)"}

    @router.post("/api/screen/identify")
    async def screen_identify_start(request: Request):
        """Identify every video of the SFW folders that has no answer yet (or those of one folder: `channel`)."""
        require_auth(request)
        body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
        if not await asyncio.to_thread(sid.configured, db):
            raise HTTPException(400, "Add a TMDB key first (Settings → Metadata)")
        return {"ok": True, "started": start((body or {}).get("channel"))}

    @router.get("/api/screen/status")
    async def screen_status(request: Request):
        require_auth(request)

        def read():
            with db.reading() as c:
                rows = c.execute("SELECT s.state, COUNT(*) n FROM media_screen s JOIN media m ON m.id=s.media_id GROUP BY s.state").fetchall()
            return {r["state"]: r["n"] for r in rows}
        return {"progress": sid.progress(), "counts": await asyncio.to_thread(read), "pending": len(await asyncio.to_thread(sid.pending, db)), "configured": sid.configured(db)}

    @router.get("/api/screen/review")
    async def screen_review(request: Request):
        """Videos the providers could not settle: with the candidates found, for the person to pick."""
        require_auth(request)

        def read():
            with db.reading() as c:
                rows = c.execute(
                    "SELECT m.id, m.filename, m.title, s.state, s.season, s.episode, s.candidates FROM media_screen s JOIN media m ON m.id=s.media_id "
                    "WHERE s.state IN ('REVIEW','UNMATCHED') ORDER BY s.state, m.filename LIMIT 500").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["candidates"] = json.loads(d["candidates"] or "[]")
                d["parsed"] = si.parse_name(d["filename"] or "")
                out.append(d)
            return out
        return {"items": await asyncio.to_thread(read)}

    @router.post("/api/screen/review/{media_id}")
    async def screen_choose(media_id: int, request: Request):
        """Settle one video: `kind` (movie / tv), `tmdb_id`, and for a show `season` and `episode`."""
        require_auth(request)
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        kind = str(body.get("kind") or "")
        if kind not in ("movie", "tv"):
            raise HTTPException(400, "kind must be movie or tv")
        try:
            tmdb_id = int(body.get("tmdb_id"))
            season = int(body["season"]) if body.get("season") is not None else None
            episode = int(body["episode"]) if body.get("episode") is not None else None
        except (TypeError, ValueError):
            raise HTTPException(400, "tmdb_id, season and episode must be numbers")
        try:
            return {"ok": True, **await asyncio.to_thread(sid.choose, db, int(media_id), kind, tmdb_id, season, episode, fetch)}
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:
            db.log(f"Choosing a title failed: {type(exc).__name__}: {exc}"[:300], "WARN")
            raise HTTPException(502, "The provider could not be reached")


    @router.get("/api/screen/series")
    async def series_list(request: Request):
        """The TV shows that have episodes in the library (in the current mode), with a poster and how many episodes are here."""
        require_auth(request)

        def read():
            with db.reading() as c:
                rows = c.execute(
                    "SELECT t.id,t.title,t.year,t.poster,t.overview,t.genres,COUNT(*) AS episodes,COUNT(DISTINCT s.season) AS seasons "
                    "FROM media_screen s JOIN media m ON m.id=s.media_id JOIN screen_titles t ON t.id=s.title_id "
                    "WHERE t.kind='tv' AND s.state='MATCHED' GROUP BY t.id ORDER BY LOWER(t.title)").fetchall()
            return [{"id": r["id"], "title": r["title"], "year": r["year"], "poster": si.image_url(r["poster"], "w342"), "overview": r["overview"],
                     "genres": json.loads(r["genres"] or "[]"), "episodes": r["episodes"], "seasons": r["seasons"]} for r in rows]
        return {"items": await asyncio.to_thread(read)}

    @router.get("/api/screen/series/{title_id}")
    async def series_detail(title_id: int, request: Request):
        """One show: its details and, season by season, the episodes that are in the library, in order."""
        require_auth(request)

        def read():
            with db.reading() as c:
                t = c.execute("SELECT * FROM screen_titles WHERE id=? AND kind='tv'", (int(title_id),)).fetchone()
                if not t:
                    return None
                eps = c.execute(
                    "SELECT s.season,s.episode,m.id AS media_id,m.source_channel,m.source_message_id,m.duration,m.title AS media_title,e.name,e.overview,e.air_date,e.still,e.runtime "
                    "FROM media_screen s JOIN media m ON m.id=s.media_id LEFT JOIN screen_episodes e ON e.title_id=s.title_id AND e.season=s.season AND e.episode=s.episode "
                    "WHERE s.title_id=? AND s.state='MATCHED' ORDER BY s.season,s.episode,m.id", (int(title_id),)).fetchall()
            if not eps and not t:
                return None
            known = {x["season"]: x for x in json.loads(t["seasons"] or "[]")}
            seasons: dict = {}
            for e in eps:
                key = e["season"] if e["season"] is not None else 0
                seasons.setdefault(key, []).append({
                    "episode": e["episode"], "name": e["name"] or e["media_title"], "overview": e["overview"] or "", "air_date": e["air_date"] or "", "still": si.image_url(e["still"] or "", "w300"),
                    "runtime": e["runtime"] or 0, "duration": e["duration"] or 0, "cid": e["source_channel"], "mid": e["source_message_id"]})
            return {"id": t["id"], "title": t["title"], "year": t["year"], "overview": t["overview"], "tagline": t["tagline"], "poster": si.image_url(t["poster"], "w500"),
                    "backdrop": si.image_url(t["backdrop"], "w780"), "genres": json.loads(t["genres"] or "[]"), "companies": json.loads(t["companies"] or "[]"),
                    "cast": [{"name": p["name"], "character": p.get("character", ""), "profile": si.image_url(p.get("profile", ""), "w185")} for p in json.loads(t["cast_json"] or "[]")][:12],
                    "rating": t["rating"], "status": t["status"],
                    "seasons": [{"season": k, "name": (known.get(k) or {}).get("name") or (f"Season {k}" if k else "Specials"), "total": (known.get(k) or {}).get("episodes") or 0, "episodes": v} for k, v in sorted(seasons.items())]}
        out = await asyncio.to_thread(read)
        if out is None:
            raise HTTPException(404, "No such show")
        return out
