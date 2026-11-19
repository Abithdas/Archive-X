"""Movies and TV (the SFW side): provider keys, running the identification, and the person's choices for uncertain matches."""
from __future__ import annotations

import asyncio
import json
import re
import threading

from fastapi import APIRouter, HTTPException, Query, Request, Response

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


def _ics_text(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    out, data = [], line.encode("utf-8")
    while len(data) > 75:
        cut = 75
        while cut > 0 and (data[cut] & 0xC0) == 0x80:      # never split a multi-byte character
            cut -= 1
        out.append(data[:cut].decode("utf-8"))
        data = b" " + data[cut:]
    out.append(data.decode("utf-8"))
    return "\r\n".join(out)


def ics_calendar(events: list[dict]) -> str:
    """An iCalendar file of all-day events: `uid`, `date` (YYYY-MM-DD), `title`, `note`."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Archive X//Airing//EN", "CALSCALE:GREGORIAN", "X-WR-CALNAME:Archive X: airing"]
    for e in events:
        day = str(e["date"]).replace("-", "")[:8]
        if len(day) != 8 or not day.isdigit():
            continue
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{day}T000000Z", f"DTSTART;VALUE=DATE:{day}", f"SUMMARY:{_ics_text(e['title'])}"]
        if e.get("note"):
            lines.append(f"DESCRIPTION:{_ics_text(e['note'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(x) for x in lines) + "\r\n"


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


    # ------------------------------------------------------------------
    # Home and Discover (SFW): what to continue, what airs this week, what was added, and what to find in Cinemeta's open catalogues.
    # ------------------------------------------------------------------
    import time as _time
    discover_cache: dict[str, tuple[float, object]] = {}
    GENRES = {"Action", "Adventure", "Animation", "Biography", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy", "History", "Horror", "Mystery", "Romance", "Sci-Fi", "Sport", "Thriller", "War", "Western"}

    # Cinemeta's own lists: Popular (top), New (year), Featured (imdbRating). The streaming lists come from TMDB ("where to watch"), so they need its key.
    CATALOGS = {"popular": "top", "new": "year", "featured": "imdbRating"}
    SERVICES = {"netflix": 8, "hbomax": 1899, "disney": 337, "prime": 119, "apple": 350}            # TMDB's "where to watch" numbers (the backup source)
    STREAM_LISTS = {"netflix": "nfx", "hbomax": "hbm", "disney": "dnp", "prime": "amp", "apple": "atp"}   # the same services in the Streaming Catalogs add-on
    ANIME_LISTS = {"anime-airing": "myanimelist_top-airing", "anime-series": "myanimelist_top-series", "anime-movies": "myanimelist_top-movies"}
    PAGE = 50
    TMDB_GENRES = {
        "movie": {"Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35, "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751, "Fantasy": 14, "History": 36, "Horror": 27, "Mystery": 9648,
                  "Romance": 10749, "Sci-Fi": 878, "Thriller": 53, "War": 10752, "Western": 37},
        "tv": {"Action": 10759, "Adventure": 10759, "Animation": 16, "Comedy": 35, "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751, "Fantasy": 10765, "Mystery": 9648, "Sci-Fi": 10765,
               "War": 10768, "Western": 37},
    }

    def _catalog_get(path: str, base: str = ""):
        """One catalogue answer (Cinemeta unless another add-on's address is given), kept ten minutes: the catalogues change slowly, and the page asks for the same few again and again."""
        key = (base or si.CINEMETA) + path
        hit = discover_cache.get(key)
        if hit and _time.monotonic() - hit[0] < 600:
            return hit[1]
        got = (fetch or sid.http_fetch)(key, {}, {})
        if len(discover_cache) > 200:
            discover_cache.clear()
        discover_cache[key] = (_time.monotonic(), got)
        return got

    def _in_library(imdb_ids: list[str]) -> dict[str, dict]:
        """Which of these IMDb ids are already in the library (visible in the current mode): imdb -> {kind, id}."""
        ids = [i for i in dict.fromkeys(imdb_ids) if re.fullmatch(r"tt\d{5,10}", i or "")]
        if not ids:
            return {}
        with db.reading() as c:
            rows = c.execute(
                f"SELECT DISTINCT t.id,t.kind,t.imdb_id FROM screen_titles t JOIN media_screen s ON s.title_id=t.id AND s.state='MATCHED' JOIN media m ON m.id=s.media_id "
                f"WHERE t.imdb_id IN ({','.join('?' for _ in ids)})", ids).fetchall()
        return {r["imdb_id"]: {"kind": r["kind"], "id": r["id"]} for r in rows}

    def _in_library_tmdb(media: str, ids: list[int]) -> dict[int, dict]:
        """Which of these TMDB ids (movies or shows) are already in the library: tmdb id -> {kind, id}."""
        ids = [int(i) for i in dict.fromkeys(ids) if i]
        if not ids:
            return {}
        with db.reading() as c:
            rows = c.execute(
                f"SELECT DISTINCT t.id,t.kind,t.tmdb_id FROM screen_titles t JOIN media_screen s ON s.title_id=t.id AND s.state='MATCHED' JOIN media m ON m.id=s.media_id "
                f"WHERE t.kind=? AND t.tmdb_id IN ({','.join('?' for _ in ids)})", [media, *ids]).fetchall()
        return {int(r["tmdb_id"]): {"kind": r["kind"], "id": r["id"]} for r in rows}

    def _tmdb_card(r: dict, media: str, mine: dict[int, dict]) -> dict:
        tid = int(r.get("id") or 0)
        date = str(r.get("release_date") or r.get("first_air_date") or "")[:4]
        vote = float(r.get("vote_average") or 0)
        return {"imdb": f"tmdb-{tid}", "kind": "tv" if media == "tv" else "movie", "title": r.get("title") or r.get("name") or "", "year": int(date) if date.isdigit() else None,
                "poster": si.image_url(r.get("poster_path") or "", "w500"), "background": si.image_url(r.get("backdrop_path") or "", "w780"), "rating": f"{vote:.1f}" if vote else "",
                "overview": r.get("overview") or "", "genres": [], "library": mine.get(tid)}

    def _card(m: dict, mine: dict[str, dict]) -> dict:
        imdb = str(m.get("imdb_id") or m.get("id") or "")
        year = str(m.get("year") or m.get("releaseInfo") or "")[:4]
        return {"imdb": imdb, "kind": "tv" if m.get("type") == "series" else "movie", "title": m.get("name") or "", "year": int(year) if year.isdigit() else None,
                "poster": m.get("poster") or "", "background": m.get("background") or "", "rating": str(m.get("imdbRating") or ""),
                "overview": m.get("description") or "", "genres": [g for g in (m.get("genres") or []) if isinstance(g, str)][:4], "library": mine.get(imdb)}

    # ------------------------------------------------------------------
    # Add-ons the person adds (Settings, Addons): extra Stremio catalogue add-ons whose lists show in Discover.
    # ------------------------------------------------------------------
    import hashlib as _hashlib
    from ..metadata import addon_client as ac
    BUILTIN_ADDONS = [
        {"name": "Cinemeta", "what": "Popular, New and Featured movies and series, and the details of every title"},
        {"name": "Streaming Catalogs", "what": "What is on Netflix, HBO Max, Disney+, Prime Video and Apple TV+"},
        {"name": "Anime Catalogs", "what": "MyAnimeList's top airing, top series and top movies"},
    ]
    MAX_ADDONS = 12

    def _saved_addons() -> list[dict]:
        raw = db.setting("catalog_addons")
        out = []
        for a in raw if isinstance(raw, list) else []:
            if isinstance(a, dict) and isinstance(a.get("url"), str) and re.fullmatch(r"[0-9a-f]{10}", str(a.get("id") or "")):
                out.append({"id": a["id"], "url": a["url"], "name": str(a.get("name") or "")[:60]})
        return out

    def _sync_local() -> bool:
        on = str(db.setting("addons_allow_local") or "") in {"1", "true", "True"}
        ac.set_allow_local(on)
        return on

    def _addon_json(url: str):
        """An add-on's answer, kept ten minutes like the other catalogues."""
        _sync_local()
        hit = discover_cache.get(url)
        if hit and _time.monotonic() - hit[0] < 600:
            return hit[1]
        got = ac.get_json(url)
        if len(discover_cache) > 200:
            discover_cache.clear()
        discover_cache[url] = (_time.monotonic(), got)
        return got

    def _addons_view() -> dict:
        items = []
        _sync_local()
        for a in _saved_addons():
            try:
                items.append({"id": a["id"], "url": a["url"], **ac.parse_manifest(_addon_json(a["url"] + "/manifest.json"))})
            except Exception as exc:
                items.append({"id": a["id"], "url": a["url"], "name": a["name"] or a["url"].split("/")[2], "description": "", "version": "", "lists": [], "error": str(exc)[:160] or "It could not be reached."})
        return {"builtin": BUILTIN_ADDONS, "items": items, "allow_local": _sync_local()}

    @router.get("/api/screen/addons")
    async def addons_list(request: Request):
        require_auth(request)
        return await asyncio.to_thread(_addons_view)

    @router.post("/api/screen/addons/settings")
    async def addons_settings(request: Request):
        """The switch for add-ons on this PC or network."""
        require_auth(request)
        body = await request.json()
        await asyncio.to_thread(db.set_setting, "addons_allow_local", "1" if (body or {}).get("allow_local") else "0")
        return await asyncio.to_thread(_addons_view)

    @router.post("/api/screen/addons")
    async def addons_add(request: Request):
        """Add a catalogue add-on by its address (a manifest link, or a stremio:// link): it must answer as an add-on with lists to browse."""
        require_auth(request)
        body = await request.json()
        saved = _saved_addons()
        if len(saved) >= MAX_ADDONS:
            raise HTTPException(400, f"At most {MAX_ADDONS} add-ons.")

        def work():
            _sync_local()
            base = ac.base_of(str((body or {}).get("url") or ""))
            manifest = ac.parse_manifest(ac.get_json(base + "/manifest.json"))
            return base, manifest
        try:
            base, manifest = await asyncio.to_thread(work)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else "That address did not answer as an add-on.") from exc
        aid = _hashlib.sha1(base.encode()).hexdigest()[:10]
        if any(a["id"] == aid for a in saved):
            raise HTTPException(400, "That add-on is already added.")
        await asyncio.to_thread(db.set_setting, "catalog_addons", [*saved, {"id": aid, "url": base, "name": manifest["name"]}])
        return await asyncio.to_thread(_addons_view)

    @router.delete("/api/screen/addons/{addon_id}")
    async def addons_remove(addon_id: str, request: Request):
        require_auth(request)
        saved = _saved_addons()
        await asyncio.to_thread(db.set_setting, "catalog_addons", [a for a in saved if a["id"] != addon_id])
        return await asyncio.to_thread(_addons_view)

    @router.get("/api/screen/discover")
    async def discover(request: Request, kind: str = Query("movie", alias="type"), genre: str = "", skip: int = 0, search: str = "", catalog: str = "popular"):
        """A list of movies or series to find (Popular, New, Featured from Cinemeta; Netflix, HBO Max, Disney+, Prime Video, Apple TV+ from TMDB), by genre, or a search: what you could add, with the ones you already have marked."""
        require_auth(request)
        if kind not in ("movie", "series"):
            raise HTTPException(400, "type must be movie or series")
        if genre and genre not in GENRES:
            raise HTTPException(400, "Unknown genre")
        if catalog not in CATALOGS and catalog not in SERVICES and catalog not in ANIME_LISTS and not re.fullmatch(r"addon:[0-9a-f]{10}:(movie|series|anime):[\w.\-%]{1,80}", catalog):
            raise HTTPException(400, "Unknown list")
        text = re.sub(r"\s+", " ", str(search or "")).strip()[:80]
        skip_n = max(0, int(skip or 0))
        if catalog.startswith("addon:") and not text:
            if not await asyncio.to_thread(sid.cinemeta_on, db):
                return {"ok": True, "disabled": True, "items": []}
            _, aid, atype, cid = catalog.split(":", 3)
            addon = next((a for a in _saved_addons() if a["id"] == aid), None)
            if addon is None:
                raise HTTPException(404, "That add-on is not added")

            def addon_work():
                try:
                    listed = next((l for l in ac.parse_manifest(_addon_json(addon["url"] + "/manifest.json"))["lists"] if l["type"] == atype and l["id"] == cid), None)
                    if listed is None:
                        return None
                    extras = ([f"genre={si.quote(genre, safe='')}"] if genre and listed["genre"] else []) + ([f"skip={min(skip_n, 2000)}"] if skip_n and listed["skip"] else [])
                    got = _addon_json(addon["url"] + f"/catalog/{atype}/{si.quote(cid, safe='')}" + (("/" + "&".join(extras)) if extras else "") + ".json")
                except Exception as exc:
                    db.log(f"Discover could not reach the add-on {addon['name']}: {type(exc).__name__}: {exc}"[:300], "WARN")
                    return None
                metas = [m for m in ((got or {}).get("metas") or []) if isinstance(m, dict) and m.get("id")] if isinstance(got, dict) else []
                if genre and not listed["genre"]:
                    metas = [m for m in metas if genre in (m.get("genres") or [])]
                if listed["skip"]:
                    page, more, nxt = metas, len(metas) >= 40, min(skip_n, 2000) + len(metas)
                else:
                    start = skip_n // PAGE * PAGE
                    page, more, nxt = metas[start:start + PAGE], len(metas) > start + PAGE, start + PAGE
                mine = _in_library([str(m.get("imdb_id") or m.get("id")) for m in page])
                return [{**_card(m, mine), "kind": "movie" if atype == "movie" else "tv"} for m in page], more, nxt
            made_addon = await asyncio.to_thread(addon_work)
            if made_addon is None:
                raise HTTPException(502, "The list could not be reached")
            return {"ok": True, "disabled": False, "items": made_addon[0], "more": made_addon[1], "next": made_addon[2]}
        if catalog in ANIME_LISTS and not text:
            if not await asyncio.to_thread(sid.cinemeta_on, db):
                return {"ok": True, "disabled": True, "items": []}
            extras = ([f"genre={genre}"] if genre else []) + ([f"skip={min(skip_n, 2000)}"] if skip_n else [])

            def anime_work():
                try:
                    got = _catalog_get(f"/catalog/anime/{ANIME_LISTS[catalog]}" + (("/" + "&".join(extras)) if extras else "") + ".json", si.ANIME_ADDON)
                except Exception as exc:
                    db.log(f"Discover could not reach the anime list: {type(exc).__name__}: {exc}"[:300], "WARN")
                    return None
                metas = [m for m in ((got or {}).get("metas") or []) if isinstance(m, dict) and m.get("id")] if isinstance(got, dict) else []
                return [{**_card(m, {}), "kind": "movie" if catalog == "anime-movies" else "tv"} for m in metas]
            anime = await asyncio.to_thread(anime_work)
            if anime is None:
                raise HTTPException(502, "The list could not be reached")
            return {"ok": True, "disabled": False, "items": anime, "more": len(anime) >= 90, "next": min(skip_n, 2000) + len(anime)}
        if catalog in SERVICES and not text:
            if not await asyncio.to_thread(sid.cinemeta_on, db):
                return {"ok": True, "disabled": True, "items": []}

            def stream_work():
                """The service's list from the Streaming Catalogs add-on: about a hundred whole titles with IMDb numbers, so the genre and the pages are cut here."""
                try:
                    got = _catalog_get(f"/catalog/{kind}/{STREAM_LISTS[catalog]}.json", si.STREAMING_ADDON)
                except Exception as exc:
                    db.log(f"Discover could not reach the {catalog} list: {type(exc).__name__}: {exc}"[:300], "WARN")
                    return None
                metas = [m for m in ((got or {}).get("metas") or []) if isinstance(m, dict) and re.fullmatch(r"tt\d{5,10}", str(m.get("imdb_id") or m.get("id") or ""))] if isinstance(got, dict) else []
                if genre:
                    metas = [m for m in metas if genre in (m.get("genres") or [])]
                page = metas[skip_n // PAGE * PAGE:][:PAGE]
                mine = _in_library([str(m.get("imdb_id") or m.get("id")) for m in page])
                return [_card(m, mine) for m in page], len(metas) > skip_n // PAGE * PAGE + PAGE, bool(metas)
            made = await asyncio.to_thread(stream_work)
            if made is not None and (made[2] or genre):
                return {"ok": True, "disabled": False, "items": made[0], "more": made[1], "next": skip_n // PAGE * PAGE + PAGE}
            # the add-on could not be reached (or had nothing): TMDB's "where to watch" is the backup, when its key is there
            media = "tv" if kind == "series" else "movie"
            scr = await asyncio.to_thread(sid.make_screen, db, fetch)
            if not scr.tmdb:
                if made is None:
                    raise HTTPException(502, "The list could not be reached")
                return {"ok": True, "disabled": False, "needs_key": True, "items": [], "more": False}
            ids = TMDB_GENRES[media].get(genre) if genre else None
            if genre and not ids:
                return {"ok": True, "disabled": False, "items": [], "more": False}   # TMDB has no such genre for this kind
            page = max(0, min(int(skip or 0), 40)) + 1
            region = str(db.setting("watch_region") or "US").upper()[:2]
            params = {"with_watch_providers": SERVICES[catalog], "watch_region": region, "with_watch_monetization_types": "flatrate", "sort_by": "popularity.desc", "page": page}
            if ids:
                params["with_genres"] = ids

            def tmdb_work():
                try:
                    got = scr._t(f"/discover/{media}", **params)
                except Exception as exc:
                    db.log(f"Discover could not reach TMDB: {type(exc).__name__}: {exc}"[:300], "WARN")
                    return None
                rows = [r for r in ((got or {}).get("results") or []) if isinstance(r, dict) and r.get("id")]
                mine = _in_library_tmdb(media, [int(r["id"]) for r in rows])
                return [_tmdb_card(r, media, mine) for r in rows], int((got or {}).get("total_pages") or 1)
            made = await asyncio.to_thread(tmdb_work)
            if made is None:
                raise HTTPException(502, "The list could not be reached")
            items, pages = made
            return {"ok": True, "disabled": False, "items": items, "more": page < min(pages, 41), "next": page}
        if not await asyncio.to_thread(sid.cinemeta_on, db):
            return {"ok": True, "disabled": True, "items": []}
        parts = []
        if genre:
            parts.append(f"genre={genre}")
        if text:
            parts.append("search=" + si.quote(text, safe=""))
        skip = max(0, min(int(skip or 0), 950)) // 50 * 50
        if skip:
            parts.append(f"skip={skip}")
        path = f"/catalog/{kind}/{'top' if text else CATALOGS.get(catalog, 'top')}" + (("/" + "&".join(parts)) if parts else "") + ".json"

        def work():
            try:
                got = _catalog_get(path)
            except Exception as exc:
                db.log(f"Discover could not reach the catalogue: {type(exc).__name__}: {exc}"[:300], "WARN")
                return None
            metas = [m for m in ((got or {}).get("metas") or []) if isinstance(m, dict)] if isinstance(got, dict) else []
            mine = _in_library([str(m.get("imdb_id") or m.get("id") or "") for m in metas])
            return [_card(m, mine) for m in metas]
        items = await asyncio.to_thread(work)
        if items is None:
            raise HTTPException(502, "The catalogue could not be reached")
        return {"ok": True, "disabled": False, "items": items, "more": len(items) >= 50, "next": skip + 50}

    WATCH_GROUPS = (("flatrate", "Stream"), ("free", "Free"), ("ads", "Free with ads"), ("rent", "Rent"), ("buy", "Buy"))

    @router.get("/api/screen/where/{kind}/{ident}")
    async def where_to_watch(kind: str, ident: str, request: Request, region: str = "US"):
        """Where a title can be watched legitimately: the services that offer it in a country (TMDB's "where to watch" data, which comes from JustWatch), and the address of TMDB's page about it.
        Nothing is played from here: the person opens the service. A title the library already has is not special: this is for the ones it has not."""
        require_auth(request)
        if kind not in ("movie", "series") or not re.fullmatch(r"tt\d{5,10}|tmdb-\d{1,9}", ident) or not re.fullmatch(r"[A-Za-z]{2}", region):
            raise HTTPException(400, "Unknown title")
        region = region.upper()
        media = "tv" if kind == "series" else "movie"

        def work():
            scr = sid.make_screen(db, fetch)
            if not scr.tmdb:
                return {"ok": True, "needs_key": True}
            if ident.startswith("tmdb-"):
                tid = int(ident[5:])
            else:
                found = [r for r in scr.find_imdb(ident) if r["kind"] == media]
                if not found:
                    return {"ok": True, "region": region, "groups": [], "link": ""}
                tid = int(found[0]["tmdb_id"])
            row = ((scr._t(f"/{media}/{tid}/watch/providers") or {}).get("results") or {}).get(region) or {}
            groups = []
            for key, label in WATCH_GROUPS:
                rows = [p for p in (row.get(key) or []) if isinstance(p, dict) and p.get("provider_name")]
                if rows:
                    groups.append({"key": key, "label": label, "providers": [{"name": str(p["provider_name"])[:40], "logo": si.image_url(p.get("logo_path") or "", "w92")} for p in rows[:12]]})
            link = str(row.get("link") or "")
            if not link.startswith("https://www.themoviedb.org/"):
                link = f"https://www.themoviedb.org/{media}/{tid}/watch?locale={region}"
            return {"ok": True, "region": region, "groups": groups, "link": link}
        return await asyncio.to_thread(work)

    @router.get("/api/screen/discover/{kind}/{imdb}")
    async def discover_one(kind: str, imdb: str, request: Request):
        """One title from Cinemeta in full (story, cast, genres, rating, trailer), and whether the library has it."""
        require_auth(request)
        if kind not in ("movie", "series") or not re.fullmatch(r"tt\d{5,10}|tmdb-\d{1,9}", imdb):
            raise HTTPException(400, "Unknown title")
        if imdb.startswith("tmdb-"):
            # A title from a streaming list: TMDB knows it by its own number; its IMDb number (for the full details) comes from TMDB too.
            media = "tv" if kind == "series" else "movie"
            tid = int(imdb[5:])

            def resolve():
                scr = sid.make_screen(db, fetch)
                if not scr.tmdb:
                    return None, {}
                ext = scr._t(f"/{media}/{tid}/external_ids")
                return str(ext.get("imdb_id") or ""), scr._t(f"/{media}/{tid}")
            found, basic = await asyncio.to_thread(resolve)
            if found is None:
                raise HTTPException(409, "Add a TMDB key in Settings, Metadata")
            if not re.fullmatch(r"tt\d{5,10}", found or ""):
                mine = await asyncio.to_thread(_in_library_tmdb, media, [tid])
                card = _tmdb_card({**basic, "id": tid}, media, mine)
                card.update({"genres": [g.get("name") for g in (basic.get("genres") or []) if isinstance(g, dict) and g.get("name")][:4], "runtime": f"{basic.get('runtime')} min" if basic.get("runtime") else "",
                             "cast": [], "director": [], "trailer": "", "seasons": int(basic.get("number_of_seasons") or 0)})
                return card
            imdb = found
        if not await asyncio.to_thread(sid.cinemeta_on, db):
            raise HTTPException(409, "Cinemeta is switched off in Settings, Metadata")

        def work():
            got = _catalog_get(f"/meta/{kind}/{imdb}.json")
            meta = (got or {}).get("meta") if isinstance(got, dict) else None
            if not isinstance(meta, dict):
                return None
            card = _card(meta, _in_library([imdb]))
            trailers = [t.get("source") for t in (meta.get("trailers") or []) if isinstance(t, dict) and re.fullmatch(r"[\w-]{6,16}", str(t.get("source") or ""))]
            card.update({"runtime": str(meta.get("runtime") or ""), "cast": [c for c in (meta.get("cast") or []) if isinstance(c, str)][:12],
                         "director": [c for c in (meta.get("director") or []) if isinstance(c, str)][:3], "trailer": trailers[0] if trailers else "",
                         "seasons": len({v.get("season") for v in (meta.get("videos") or []) if isinstance(v, dict) and (v.get("season") or 0) > 0}) if kind == "series" else 0})
            return card
        card = await asyncio.to_thread(work)
        if card is None:
            raise HTTPException(404, "That title is not in the catalogue")
        return card

    @router.get("/api/screen/home")
    async def screen_home(request: Request):
        """What the Home page shows from your own library: videos to carry on with, episodes airing this week, what was added last, and a few titles for the billboard."""
        require_auth(request)

        def read():
            out: dict = {}
            with db.reading() as c:
                rows = c.execute(
                    "SELECT wh.position,wh.duration,m.source_channel AS cid,m.source_message_id AS mid,m.title AS media_title,s.season,s.episode,t.kind,t.title AS ttitle,t.poster,t.year,e.name AS ename,e.still "
                    "FROM watch_history wh JOIN media m ON m.id=wh.media_id LEFT JOIN media_screen s ON s.media_id=m.id AND s.state='MATCHED' LEFT JOIN screen_titles t ON t.id=s.title_id "
                    "LEFT JOIN screen_episodes e ON e.title_id=s.title_id AND e.season=s.season AND e.episode=s.episode "
                    "WHERE wh.completed=0 AND wh.position>=20 AND wh.duration>0 AND wh.position<wh.duration*0.95 ORDER BY wh.last_played_at DESC LIMIT 12").fetchall()
                out["continue"] = [{
                    "cid": r["cid"], "mid": r["mid"], "title": r["ttitle"] or r["media_title"] or "Video",
                    "sub": (f"S{r['season']} · E{r['episode']}" + (f" · {r['ename']}" if r["ename"] else "")) if r["kind"] == "tv" and r["season"] is not None and r["episode"] is not None else (str(r["year"]) if r["year"] else ""),
                    "image": si.image_url(r["still"] or r["poster"] or "", "w500"), "pct": round(100 * r["position"] / r["duration"]), "left": max(1, round((r["duration"] - r["position"]) / 60))} for r in rows]
                rows = c.execute(
                    "SELECT t.id,t.title,t.poster,e.season,e.episode,e.name,e.air_date FROM screen_episodes e JOIN screen_titles t ON t.id=e.title_id AND t.kind='tv' "
                    "WHERE e.air_date>=date('now') AND e.air_date<=date('now','+7 day') AND e.title_id IN (SELECT s.title_id FROM media_screen s JOIN media m ON m.id=s.media_id WHERE s.state='MATCHED') "
                    "ORDER BY e.air_date,t.title LIMIT 24").fetchall()
                out["airing"] = [{"id": r["id"], "title": r["title"], "poster": si.image_url(r["poster"], "w342"), "season": r["season"], "episode": r["episode"], "name": r["name"], "date": r["air_date"]} for r in rows]
                rows = c.execute(
                    "SELECT t.id,t.kind,t.title,t.year,t.poster,t.backdrop,t.overview,t.genres,t.rating,MAX(m.created_at) AS added FROM media_screen s JOIN media m ON m.id=s.media_id "
                    "JOIN screen_titles t ON t.id=s.title_id WHERE s.state='MATCHED' GROUP BY t.id ORDER BY added DESC LIMIT 18").fetchall()
            out["recent"] = [{"id": r["id"], "kind": r["kind"], "title": r["title"], "year": r["year"], "poster": si.image_url(r["poster"], "w342")} for r in rows]
            out["billboard"] = [{"id": r["id"], "kind": r["kind"], "title": r["title"], "year": r["year"], "overview": (r["overview"] or "")[:320], "backdrop": si.image_url(r["backdrop"], "w1280"),
                                 "poster": si.image_url(r["poster"], "w500"), "genres": json.loads(r["genres"] or "[]")[:3], "rating": r["rating"]} for r in rows if r["backdrop"]][:5]
            return out
        return await asyncio.to_thread(read)

    @router.get("/api/screen/calendar.ics")
    async def screen_calendar(request: Request, days: int = 90):
        """Episodes of the shows in your library that air in the next `days` days, as an iCalendar file (all-day events) that any calendar app can import."""
        require_auth(request)
        days = max(1, min(int(days), 365))

        def read():
            with db.reading() as c:
                return c.execute(
                    "SELECT t.id,t.title,e.season,e.episode,e.name,e.air_date FROM screen_episodes e JOIN screen_titles t ON t.id=e.title_id AND t.kind='tv' "
                    "WHERE e.air_date>=date('now') AND e.air_date<=date('now',?) AND e.title_id IN (SELECT s.title_id FROM media_screen s JOIN media m ON m.id=s.media_id WHERE s.state='MATCHED') "
                    "ORDER BY e.air_date,t.title LIMIT 2000", (f"+{days} day",)).fetchall()
        rows = await asyncio.to_thread(read)
        body = ics_calendar([{"uid": f"ep-{r['id']}-{r['season']}-{r['episode']}@archive-x", "date": r["air_date"], "title": f"{r['title']} S{r['season']}E{r['episode']}", "note": r["name"] or ""} for r in rows])
        return Response(body, media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="archive-x-airing.ics"'})

    @router.get("/api/screen/of-media")
    async def titles_of_media(request: Request, ids: str = ""):
        """The movies and shows that a set of videos belong to (for a cast member's or a studio's page): one entry for each title, never one for each
        episode or copy, and the videos that have no title yet (`loose`)."""
        require_auth(request)
        media_ids = list(dict.fromkeys(int(x) for x in ids.split(",") if x.strip().isdigit()))[:5000]
        if not media_ids:
            return {"titles": [], "loose": []}

        def read():
            out: dict[int, dict] = {}
            matched: set[int] = set()
            with db.reading() as c:
                for i in range(0, len(media_ids), 400):
                    part = media_ids[i:i + 400]
                    rows = c.execute(
                        f"SELECT s.media_id,t.id,t.kind,t.title,t.year,t.poster FROM media_screen s JOIN screen_titles t ON t.id=s.title_id "
                        f"WHERE s.state='MATCHED' AND s.media_id IN ({','.join('?' for _ in part)})", part).fetchall()
                    for r in rows:
                        matched.add(int(r["media_id"]))
                        e = out.setdefault(int(r["id"]), {"id": int(r["id"]), "kind": r["kind"], "title": r["title"], "year": r["year"], "poster": si.image_url(r["poster"], "w342"), "videos": 0})
                        e["videos"] += 1
            titles = sorted(out.values(), key=lambda e: str(e["title"]).lower())
            return {"titles": titles, "loose": [m for m in media_ids if m not in matched]}
        return await asyncio.to_thread(read)

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
