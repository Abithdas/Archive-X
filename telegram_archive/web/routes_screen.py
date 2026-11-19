"""Movies and TV (the SFW side): provider keys, running the identification, and the person's choices for uncertain matches."""
from __future__ import annotations

import asyncio
import json
import re
import threading

from fastapi import APIRouter, HTTPException, Request

from ..engines import screen_identify as sid
from ..metadata import screen_ids as si

KEYS = {"tmdb_api_key": "tmdb", "omdb_api_key": "omdb", "opensubtitles_api_key": "opensubtitles"}


def clean_key(field: str, raw) -> str:
    """The key itself from what was pasted: no spaces, quotes or hidden characters, and the key out of a URL or a "key: …" sentence (an e-mail
    from the provider, a copied address). Raises ValueError, in words, for text that is plainly not a key (OMDb's activation link)."""
    text = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u00a0]+", " ", str(raw or "")).strip().strip("\"'`<>").strip()
    if not text:
        return ""
    if field == "omdb_api_key":
        if "verifykey" in text.lower():
            raise ValueError("That is OMDb's activation link, not the key. Open the link once to activate the key, then paste the key itself: 8 letters and digits, from the same e-mail.")
        found = re.search(r"apikey=([A-Za-z0-9]+)", text, re.I) or re.search(r"\bkey\b\W{0,4}(?:is\W{0,3})?([A-Za-z0-9]{6,12})\b", text, re.I)
        if found:
            text = found.group(1)
        text = text.replace(" ", "")
        if not re.fullmatch(r"[A-Za-z0-9]{6,12}", text):
            raise ValueError("An OMDb key is 8 letters and digits (for example 1a2b3c4d). Copy just the key from OMDb's e-mail.")
        return text
    found = re.search(r"api[_-]?key=([A-Za-z0-9._-]+)", text, re.I)
    if found:
        text = found.group(1)
    text = re.sub(r"^(bearer|token)\s+", "", text, flags=re.I).replace(" ", "")
    return text


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
        out["tvmaze_enabled"] = sid.tvmaze_on(db)
        out["cinemeta_enabled"] = sid.cinemeta_on(db)
        return out

    @router.post("/api/screen/settings")
    async def screen_settings_save(request: Request):
        """Save keys (a blank field keeps what is saved). `clear` lists keys to remove."""
        require_auth(request)
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        saved = []
        cleaned: dict[str, str] = {}
        for key in KEYS:
            try:
                v = clean_key(key, body.get(key))
            except ValueError as exc:
                raise HTTPException(400, str(exc))   # nothing is saved when one of the keys is plainly wrong
            if v:
                cleaned[key] = v
        for key, v in cleaned.items():
            db.set_setting(key, v)
            saved.append(key)
        if "tvmaze_enabled" in body:
            db.set_setting("tvmaze_enabled", "1" if body["tvmaze_enabled"] else "0")
            saved.append("tvmaze_enabled")
        if "cinemeta_enabled" in body:
            db.set_setting("cinemeta_enabled", "1" if body["cinemeta_enabled"] else "0")
            saved.append("cinemeta_enabled")
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
        screen.cache = None   # a key test always asks the provider itself: an old answer says nothing about the key as it is now

        def work():
            if provider == "tmdb":
                if not screen.tmdb:
                    return {"ok": False, "error": "No TMDB key saved"}
                got = screen._t("/configuration")
                return {"ok": True} if got else {"ok": False, "error": "TMDB did not answer the test"}
            if provider == "omdb":
                if not screen.omdb:
                    return {"ok": False, "error": "No OMDb key saved"}
                got = screen.fetch(si.OMDB, {"i": "tt0111161", "apikey": screen.omdb}, {}) or {}
                if got.get("Response") == "True":
                    return {"ok": True}
                raise sid.ProviderError(401, str(got.get("Error") or "no answer"))
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
            return {"ok": False, "error": sid.key_trouble(provider, exc)}

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

    @router.get("/api/screen/search")
    async def screen_search(request: Request, q: str = "", kind: str = "movie"):
        """Look a title up by what was typed (a name, or an IMDb id tt…), to settle a video by hand."""
        require_auth(request)
        if kind not in ("movie", "tv"):
            raise HTTPException(400, "kind must be movie or tv")
        if len(q) > 200:
            raise HTTPException(400, "That is too long to be a title")
        try:
            return {"items": await asyncio.to_thread(sid.search_titles, db, q, kind, fetch)}
        except Exception as exc:
            db.log(f"Title search failed: {type(exc).__name__}: {exc}"[:300], "WARN")
            raise HTTPException(502, "The provider could not be reached")

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
            tmdb_id = int(body["tmdb_id"]) if body.get("tmdb_id") not in (None, "") else None
            season = int(body["season"]) if body.get("season") is not None else None
            episode = int(body["episode"]) if body.get("episode") is not None else None
        except (TypeError, ValueError):
            raise HTTPException(400, "tmdb_id, season and episode must be numbers")
        imdb = str(body.get("imdb_id") or "").strip().lower() or None
        if imdb and not (imdb.startswith("tt") and imdb[2:].isdigit()):
            raise HTTPException(400, "An IMDb id looks like tt0133093")
        if tmdb_id is None and not imdb:
            raise HTTPException(400, "Give a TMDB id, an IMDb id, or pick one of the titles")
        try:
            return {"ok": True, **await asyncio.to_thread(sid.choose, db, int(media_id), kind, tmdb_id, season, episode, fetch, imdb)}
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
            for k, info in known.items():       # a season of the show with nothing of it here still appears, as all missing
                if k and k not in seasons and (info.get("episodes") or 0) > 0:
                    seasons[k] = []
            return {"id": t["id"], "title": t["title"], "year": t["year"], "overview": t["overview"], "tagline": t["tagline"], "poster": si.image_url(t["poster"], "w500"),
                    "backdrop": si.image_url(t["backdrop"], "w780"), "genres": json.loads(t["genres"] or "[]"), "companies": json.loads(t["companies"] or "[]"),
                    "cast": [{"name": p["name"], "character": p.get("character", ""), "profile": si.image_url(p.get("profile", ""), "w185")} for p in json.loads(t["cast_json"] or "[]")][:12],
                    "rating": t["rating"], "status": t["status"],
                    "seasons": [{"season": k, "name": (known.get(k) or {}).get("name") or (f"Season {k}" if k else "Specials"), "total": (known.get(k) or {}).get("episodes") or 0, "episodes": v,
                                 "missing": si.missing_episodes((known.get(k) or {}).get("episodes") or 0, [e["episode"] for e in v if e["episode"] is not None]) if k else []} for k, v in sorted(seasons.items())]}
        out = await asyncio.to_thread(read)
        if out is None:
            raise HTTPException(404, "No such show")
        return out


    def _versions(rows, hide_cam: bool) -> list[dict]:
        """The copies of one movie, best first. A cinema recording is left out when a real copy exists (and is marked when it is all there is)."""
        copies = []
        for r in rows:
            q = si.quality_of(r["filename"] or "", r["height"] or 0)
            copies.append({"cid": r["source_channel"], "mid": r["source_message_id"], "media_id": r["media_id"], "filename": r["filename"] or "", "resolution": q["resolution"], "height": q["height"],
                           "source": q["source"], "cam": q["cam"], "codec": r["codec"] or "", "size": r["size"] or 0, "duration": r["duration"] or 0})
        copies.sort(key=lambda v: si.version_sort_key(v))
        if hide_cam and any(not v["cam"] for v in copies):
            copies = [v for v in copies if not v["cam"]]
        return copies

    @router.get("/api/screen/movies")
    async def movies_list(request: Request, cam: int = 0):
        """The movies in the library: one entry for each, with how many copies there are and the best one's picture size."""
        require_auth(request)

        def read():
            with db.reading() as c:
                rows = c.execute(
                    "SELECT t.id AS title_id,t.title,t.year,t.poster,t.overview,t.genres,t.rating,t.release_date,m.id AS media_id,m.source_channel,m.source_message_id,m.filename,m.height,m.size,m.duration,m.codec "
                    "FROM media_screen s JOIN media m ON m.id=s.media_id JOIN screen_titles t ON t.id=s.title_id WHERE t.kind='movie' AND s.state='MATCHED' ORDER BY t.id, m.id").fetchall()
            groups: dict[int, list] = {}
            for r in rows:
                groups.setdefault(r["title_id"], []).append(r)
            out = []
            for tid, rs in groups.items():
                v = _versions(rs, not cam)
                first = rs[0]
                out.append({"id": tid, "title": first["title"], "year": first["year"], "poster": si.image_url(first["poster"], "w342"), "overview": first["overview"], "genres": json.loads(first["genres"] or "[]"),
                            "rating": first["rating"] or 0, "release_date": first["release_date"] or "", "added": max(r["media_id"] for r in rs), "versions": len(v), "best": v[0]["resolution"] if v else "",
                            "cam_only": bool(v) and all(x["cam"] for x in v)})
            return sorted(out, key=lambda m: m["title"].lower())
        return {"items": await asyncio.to_thread(read)}

    @router.get("/api/screen/movies/{title_id}")
    async def movie_detail(title_id: int, request: Request, cam: int = 0):
        """One movie: its story, cast and every copy of it (best first), each one playable."""
        require_auth(request)

        def read():
            with db.reading() as c:
                t = c.execute("SELECT * FROM screen_titles WHERE id=? AND kind='movie'", (int(title_id),)).fetchone()
                if not t:
                    return None
                rows = c.execute(
                    "SELECT m.id AS media_id,m.source_channel,m.source_message_id,m.filename,m.height,m.size,m.duration,m.codec FROM media_screen s JOIN media m ON m.id=s.media_id "
                    "WHERE s.title_id=? AND s.state='MATCHED' ORDER BY m.id", (int(title_id),)).fetchall()
            v = _versions(rows, not cam)
            if not v:
                return None
            return {"id": t["id"], "title": t["title"], "year": t["year"], "overview": t["overview"], "tagline": t["tagline"], "poster": si.image_url(t["poster"], "w500"), "backdrop": si.image_url(t["backdrop"], "w780"),
                    "genres": json.loads(t["genres"] or "[]"), "companies": json.loads(t["companies"] or "[]"), "rating": t["rating"], "runtime": t["runtime"],
                    "cast": [{"name": p["name"], "character": p.get("character", ""), "profile": si.image_url(p.get("profile", ""), "w185")} for p in json.loads(t["cast_json"] or "[]")][:12], "versions": v}
        out = await asyncio.to_thread(read)
        if out is None:
            raise HTTPException(404, "No such movie")
        return out
