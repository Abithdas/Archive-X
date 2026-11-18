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
