"""Identify the videos of SFW folders as movies and TV episodes, and keep the answer.

For each video that is not identified yet: read its name, ask the providers (``metadata/screen_ids.py``), and either apply the match
(title, date, cast, studios or networks, genres, all marked SFW), or keep the candidates for the person to pick (REVIEW), or note that
nothing was found (UNMATCHED). A person's choice is never undone by a later run.
"""
from __future__ import annotations

import time
import json
import os
import threading

from .. import config
from ..core.resource_registry import settle_lookup
from ..core.swallowed import note as _swallowed_note
from ..metadata import screen_ids as si

_lock = threading.Lock()
_state: dict = {"running": False, "total": 0, "done": 0, "channel": "", "started": 0.0, "finished": 0.0, "reason": ""}
SUFFIX = {"performers": " (Actor)", "studios": " (Studio)", "tags": " (Genre)"}


def keys(db) -> dict:
    return {"tmdb": str(db.setting("tmdb_api_key") or ""), "omdb": str(db.setting("omdb_api_key") or ""), "opensubtitles": str(db.setting("opensubtitles_api_key") or "")}


def tvmaze_on(db) -> bool:
    """TVmaze needs no key, but it is asked about the person's file names only when they switched it on."""
    return str(db.setting("tvmaze_enabled") or "").lower() in ("1", "true", "yes")


BUILTIN_ADDONS = (
    ("cinemeta", "Cinemeta", "Popular, New and Featured movies and series, and the details of every title"),
    ("streaming", "Streaming Catalogs", "What is on Netflix, HBO Max, Disney+, Prime Video and Apple TV+"),
    ("anime", "Anime Catalogs", "MyAnimeList's top airing, top series and top movies"),
    ("subtitles", "OpenSubtitles", "Subtitles in the video player for movies and series (Stremio's own subtitle add-on)"),
)
OPENSUBTITLES_URL = "https://opensubtitles-v3.strem.io"
BUILTIN_IDS = tuple(b[0] for b in BUILTIN_ADDONS)


def removed_builtins(db) -> set[str]:
    """The built-in add-ons the person removed (Settings, Addons). Removing one takes its lists out of Discover; it can be added back."""
    raw = db.setting("removed_builtin_addons")
    return {str(x) for x in raw if str(x) in BUILTIN_IDS} if isinstance(raw, list) else set()


def set_builtin_removed(db, key: str, removed: bool) -> None:
    if key not in BUILTIN_IDS:
        raise KeyError(key)
    now = removed_builtins(db)
    (now.add if removed else now.discard)(key)
    db.set_setting("removed_builtin_addons", [k for k in BUILTIN_IDS if k in now])


def builtin_on(db, key: str) -> bool:
    """Is this built-in add-on in use? Not removed, and the catalogues are not switched off as a whole (Settings, Metadata)."""
    if key == "cinemeta":
        return cinemeta_on(db)
    return catalogues_on(db) and key not in removed_builtins(db)


def catalogues_on(db) -> bool:
    return str(db.setting("cinemeta_enabled") or "").strip().lower() in ("", "1", "true", "yes")


def cinemeta_on(db) -> bool:
    """Cinemeta (the Stremio project's open catalog) needs no key. It is on until someone switches it off: without it (and without a TMDB key) nothing
    in a movie folder is ever identified, and Movies & Shows stays empty."""
    return catalogues_on(db) and "cinemeta" not in removed_builtins(db)


def configured(db) -> bool:
    k = keys(db)
    return bool(k["tmdb"] or k["omdb"] or tvmaze_on(db) or cinemeta_on(db))


class ProviderError(RuntimeError):
    """A provider said no, and said why (its own words are kept: "Invalid API key!", "Unauthorized")."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


def _why(body: bytes, reason: str = "") -> str:
    """The provider's own words for a refusal, from its reply: "Invalid API key!", "Unauthorized", the status line when it said nothing."""
    text = (body or b"").decode("utf-8", "replace").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text[:200] or reason or ""
    if isinstance(data, dict):
        for key in ("Error", "status_message", "message", "detail", "error"):
            if data.get(key):
                return str(data[key])[:200]
    return str(data)[:200]


def http_fetch(url: str, params: dict, headers: dict) -> dict | list:
    """The real fetch: JSON over HTTPS, {} when the provider has nothing (404), a ProviderError when it refuses and says why. One polite retry when asked to slow down.

    Standard library only: the installed app is built from requirements.txt, which does not (and need not) include `requests`.
    """
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full = url + (("&" if "?" in url else "?") + query if query else "")
    req = urllib.request.Request(full, headers={"User-Agent": "ArchiveX", "Accept": "application/json", **(headers or {})})
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read(16 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            if exc.code == 429 and attempt == 0:
                try:
                    wait = float(exc.headers.get("Retry-After", "2") or 2)
                except ValueError:
                    wait = 2.0
                time.sleep(min(wait, 10.0))
                continue
            try:
                body = exc.read(4000)
            except Exception:
                body = b""
            raise ProviderError(exc.code, _why(body, str(exc.reason or ""))) from exc
        return json.loads(raw.decode("utf-8")) if raw else {}
    return {}


def key_trouble(provider: str, exc: Exception) -> str:
    """What to tell the person when a provider refused its key, in the provider's own terms."""
    detail = str(getattr(exc, "detail", "") or exc)
    status = getattr(exc, "status", 0)
    if provider == "omdb" and ("api key" in detail.lower() or status == 401):
        return "OMDb does not recognise this key (it said: " + detail.rstrip("!.") + "). OMDb emails you an activation link when you ask for a key: open it, then test again."
    if provider == "tmdb" and status in (401, 403):
        return "TMDB did not accept the key. Use the v3 API key, or the long v4 read token, from themoviedb.org → Settings → API."
    if provider == "opensubtitles" and status in (401, 403):
        return "OpenSubtitles did not accept the key (it said: " + detail.rstrip("!.") + ")."
    if status:
        return f"The provider answered {status}: {detail}"
    return "Could not reach the provider: " + detail[:120]


class DbCache:
    """Provider answers kept in the database between runs (the table the other metadata providers use), so a second run asks nothing again."""

    def __init__(self, db):
        self.db = db
        from ..metadata import providers
        providers._ensure_cache(db)

    def get(self, provider: str, key: str):
        import time
        with self.db.lock:
            row = self.db.conn.execute("SELECT response, fetched_at FROM metadata_cache WHERE provider=? AND query_key=?", (provider, key)).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row["response"]) if row["response"] else {}
        except ValueError:
            return None
        return value if value is not None else {}, time.time() - float(row["fetched_at"])

    def put(self, provider: str, key: str, value) -> None:
        import time
        with self.db.lock:
            self.db.conn.execute("INSERT OR REPLACE INTO metadata_cache(provider,query_key,response,fetched_at) VALUES(?,?,?,?)", (provider, key, json.dumps(value), time.time()))
            self.db.conn.commit()


def make_screen(db, fetch=None) -> si.Screen:
    k = keys(db)
    return si.Screen(fetch or http_fetch, tmdb=k["tmdb"], omdb=k["omdb"], opensubtitles=k["opensubtitles"], tvmaze=tvmaze_on(db), cinemeta=cinemeta_on(db), cache=DbCache(db))


def _entity(db, table: str, name: str, tmdb_id=None, image: str = "") -> int | None:
    """An SFW person, studio or genre by name. A name already used by a non-SFW row gets a suffix instead of taking it over."""
    from .entity_identity import upsert_performer, upsert_studio
    name = " ".join(str(name).split())
    if not name:
        return None
    from .entity_identity import _find_existing
    use = name
    if table == "tags":
        with db.reading() as conn:
            row = conn.execute("SELECT id,content_rating FROM tags WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
        if row and (row["content_rating"] or "nsfw") != "sfw":
            use = name + SUFFIX[table]
    else:
        # The matcher also recognises aliases and canonical spellings, so ask it what it would pick: if that record is not already SFW it is somebody
        # (or some studio) of the adult side with the same name, and it must keep its mark. The TMDB entry gets its own, suffixed record.
        with db.lock:
            found = _find_existing(db, table, name, "tmdb", str(tmdb_id) if tmdb_id else None)
            if found and (found.get("content_rating") or "nsfw") != "sfw":
                use = name + SUFFIX[table]
                if _find_existing(db, table, use, "tmdb", str(tmdb_id) if tmdb_id else None):
                    return None   # even that is taken by an adult record: no link is better than a wrong one
    if table == "tags":
        with db.lock:
            db.conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (use,))
            eid = int(db.conn.execute("SELECT id FROM tags WHERE name=?", (use,)).fetchone()["id"])
            db.conn.execute("UPDATE tags SET content_rating='sfw' WHERE id=?", (eid,))
            db.conn.commit()
        return eid
    up = upsert_performer if table == "performers" else upsert_studio
    eid = up(db, use, provider="tmdb", provider_id=tmdb_id, image=image or None) if tmdb_id else up(db, use)
    if eid:
        with db.lock:
            db.conn.execute(f"UPDATE {table} SET content_rating='sfw'{',tmdb_id=?' if tmdb_id else ''} WHERE id=?", ([str(tmdb_id)] if tmdb_id else []) + [int(eid)])
            db.conn.commit()
    return int(eid) if eid else None


def store_title(db, d: dict) -> int:
    now = config.now_iso()
    with db.lock:
        if d.get("imdb_id") and int(d["tmdb_id"]) < 0:        # a TVmaze show that TMDB already holds under the same IMDb id: one row, not two
            same = db.conn.execute("SELECT id FROM screen_titles WHERE kind=? AND imdb_id=? AND tmdb_id>0", (d["kind"], d["imdb_id"])).fetchone()
            if same:
                return int(same["id"])
        db.conn.execute(
            "INSERT INTO screen_titles(kind,tmdb_id,imdb_id,title,original_title,year,release_date,overview,tagline,poster,backdrop,genres,companies,cast_json,seasons,rating,runtime,status,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,tmdb_id) DO UPDATE SET imdb_id=excluded.imdb_id,title=excluded.title,original_title=excluded.original_title,"
            "year=excluded.year,release_date=excluded.release_date,overview=excluded.overview,tagline=excluded.tagline,poster=excluded.poster,backdrop=excluded.backdrop,genres=excluded.genres,"
            "companies=excluded.companies,cast_json=excluded.cast_json,seasons=excluded.seasons,rating=excluded.rating,runtime=excluded.runtime,status=excluded.status,updated_at=excluded.updated_at",
            (d["kind"], d["tmdb_id"], d.get("imdb_id", ""), d["title"], d.get("original_title", ""), d.get("year"), d.get("release_date", ""), d.get("overview", ""), d.get("tagline", ""),
             d.get("poster", ""), d.get("backdrop", ""), si.dumps(d.get("genres", [])), si.dumps(d.get("companies", [])), si.dumps(d.get("cast", [])), si.dumps(d.get("seasons", [])),
             d.get("rating", 0), d.get("runtime", 0), d.get("status", ""), now))
        tid = int(db.conn.execute("SELECT id FROM screen_titles WHERE kind=? AND tmdb_id=?", (d["kind"], d["tmdb_id"])).fetchone()["id"])
        db.conn.commit()
    return tid


def _title_entities(db, d: dict, ctx: dict | None) -> tuple[list, list, list]:
    """People, studios and genres of a title as rows. Made once per title in a run: the 40 episodes of a show share them."""
    key = (d["kind"], d["tmdb_id"])
    if ctx is not None and key in ctx.setdefault("entities", {}):
        return ctx["entities"][key]
    cast = [_entity(db, "performers", p["name"], p.get("tmdb_id"), si.image_url(p.get("profile", ""), "w185")) for p in d.get("cast", [])]
    made_by = d.get("company_info") or [{"name": n, "id": None, "logo": ""} for n in d.get("companies", [])]
    studios = [_entity(db, "studios", c["name"], c.get("id"), si.image_url(c.get("logo", ""), "w185")) for c in made_by[:6]]
    genres = [_entity(db, "tags", g) for g in d.get("genres", [])]
    if ctx is not None:
        ctx["entities"][key] = (cast, studios, genres)
    return cast, studios, genres


def apply_match(db, media_id: int, d: dict, episode: dict | None, *, season=None, number=None, confidence=1.0, source="tmdb", chosen=False, ctx: dict | None = None) -> None:
    """Make a video that title: the media row, the stored title and episode, and the people, studios and genres linked to it."""
    tid = store_title(db, d)
    now = config.now_iso()
    if episode:
        with db.lock:
            db.conn.execute(
                "INSERT INTO screen_episodes(title_id,season,episode,name,overview,air_date,still,runtime) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(title_id,season,episode) DO UPDATE SET "
                "name=excluded.name,overview=excluded.overview,air_date=excluded.air_date,still=excluded.still,runtime=excluded.runtime",
                (tid, episode["season"], episode["episode"], episode["name"], episode["overview"], episode["air_date"], episode["still"], episode["runtime"]))
            db.conn.commit()
    if d["kind"] == "tv" and season is not None and number is not None:
        label = f"{d['title']} S{int(season):02d}E{int(number):02d}" + (f" · {episode['name']}" if episode and episode.get("name") else "")
        date = (episode or {}).get("air_date") or d.get("release_date", "")
    else:
        label = d["title"] + (f" ({d['year']})" if d.get("year") else "")
        date = d.get("release_date", "")
    cast, studios, genres = _title_entities(db, d, ctx)
    with db.lock:
        db.conn.execute("UPDATE media SET title=?,release_date=?,identification_state='MATCHED',updated_at=? WHERE id=?", (label, date or None, now, int(media_id)))
        for table, link, ids in (("video_performers", "performer_id", cast), ("video_studios", "studio_id", studios), ("video_tags", "tag_id", genres)):
            db.conn.execute(f"DELETE FROM {table} WHERE video_id=? AND {link} IN (SELECT id FROM {table.split('_')[1]} WHERE content_rating='sfw')", (int(media_id),))
            for i in ids:
                if i:
                    db.conn.execute(f"INSERT OR IGNORE INTO {table}(video_id,{link},confidence) VALUES(?,?,?)", (int(media_id), int(i), float(confidence)))
        db.conn.execute(
            "INSERT INTO media_screen(media_id,title_id,season,episode,state,confidence,source,chosen,candidates,parser,updated_at) VALUES(?,?,?,?,'MATCHED',?,?,?,'[]',?,?) ON CONFLICT(media_id) DO UPDATE SET "
            "title_id=excluded.title_id,season=excluded.season,episode=excluded.episode,state='MATCHED',confidence=excluded.confidence,source=excluded.source,chosen=excluded.chosen,candidates='[]',parser=excluded.parser,updated_at=excluded.updated_at",
            (int(media_id), tid, season, number, float(confidence), source, 1 if chosen else 0, si.PARSER_VERSION, now))
        db.conn.commit()


def _record(db, media_id: int, state: str, result: dict) -> None:
    with db.lock:
        db.conn.execute(
            "INSERT INTO media_screen(media_id,state,season,episode,confidence,source,chosen,candidates,parser,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?) ON CONFLICT(media_id) DO UPDATE SET "
            "state=excluded.state,season=excluded.season,episode=excluded.episode,confidence=excluded.confidence,candidates=excluded.candidates,parser=excluded.parser,updated_at=excluded.updated_at WHERE media_screen.chosen=0",
            (int(media_id), state, result.get("season"), result.get("episode"), float((result.get("candidates") or [{}])[0].get("score") or 0), "name", si.dumps(result.get("candidates", [])), si.PARSER_VERSION, config.now_iso()))
        db.conn.commit()


def identify_one(db, screen: si.Screen, media_id: int, path: str, ctx: dict | None = None) -> str:
    try:
        file_hash = si.oshash(path) if screen.os_key and os.path.isfile(path) else ""
    except OSError:
        file_hash = ""
    result = si.identify(screen, path, file_hash=file_hash, memo=None if ctx is None else ctx.setdefault("memo", {}))
    if result["state"] == "MATCHED":
        best = result["best"]
        kind = best["kind"]
        if not best.get("tmdb_id"):                                 # an OMDb-only answer: kept for review, it has no TMDB page to build from
            result["state"] = "REVIEW"
        else:
            d = screen.details(kind, best["tmdb_id"])
            if d:
                ep = screen.episode(d["tmdb_id"], result["season"], result["episode"]) if kind == "tv" and result["season"] is not None and result["episode"] is not None else None
                apply_match(db, media_id, d, ep, season=result["season"], number=result["episode"], confidence=best["score"], source=best["source"], ctx=ctx)
                settle_lookup(db, [media_id], unknown=False)
                return "MATCHED"
            result["state"] = "REVIEW"
    _record(db, media_id, result["state"], result)
    settle_lookup(db, [media_id], unknown=False)        # nothing found (or a doubtful match to check): the lookup is finished, the video is shown under Unknown Performer and Studio and can be settled by hand
    return result["state"]


def pending(db, channel: str | None = None) -> list[dict]:
    """Videos of SFW channels that have no answer yet (a channel for one folder, or every one)."""
    sql = ("SELECT m.id, m.source_channel, m.source_message_id, m.filename, l.path FROM media m "
           "JOIN channels c ON c.telegram_id=m.source_channel AND COALESCE(c.content_rating,'nsfw')='sfw' "
           "LEFT JOIN local_video_files l ON l.channel_id=m.source_channel AND l.message_id=m.source_message_id "
           "LEFT JOIN media_screen s ON s.media_id=m.id "
           # no answer yet, or an unsettled one made with an older way of reading names (once per improvement; a person's choice is never reopened)
           "WHERE (s.media_id IS NULL OR (s.state IN ('UNMATCHED','REVIEW') AND s.chosen=0 AND COALESCE(s.parser,0)<?))")
    args: tuple = (si.PARSER_VERSION,)
    if channel:
        sql += " AND m.source_channel=?"
        args = (si.PARSER_VERSION, channel)
    with db.reading() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY m.id", args)]


def run(db, channel: str | None = None, fetch=None) -> dict:
    """Identify what is pending. One run at a time; a failure on one video does not stop the rest."""
    if not configured(db):
        return {"ok": False, "error": "Add a TMDB key (Settings → Metadata) to identify movies and shows."}
    if not _lock.acquire(blocking=False):
        return dict(_state)
    try:
        todo = pending(db, channel)
        _state.update(running=True, total=len(todo), done=0, failed=0, matched=0, review=0, unmatched=0, channel=str(channel or ""), started=time.time(), finished=0.0, reason="")
        screen = make_screen(db, fetch)
        ctx: dict = {"memo": {}, "entities": {}}   # one answer per show for the whole run
        for row in todo:
            try:
                out = identify_one(db, screen, int(row["id"]), row["path"] or row["filename"] or "", ctx)
                _state[{"MATCHED": "matched", "REVIEW": "review"}.get(out, "unmatched")] += 1
            except Exception as exc:
                _swallowed_note("screen_identify.run")
                from ..core.net_errors import explain
                if not _state["reason"]:          # said once, at the first failure, in plain words; the rest of the run is not a log of the same line
                    _state["reason"] = explain(exc, "TMDB") or f"{type(exc).__name__}: {exc}"[:160]
                    db.log(f"Identify failed for media {row['id']}: {_state['reason']}"[:300], "WARN")
                _state["failed"] += 1
            _state["done"] += 1
        _state["requests"] = screen.calls
        return {"ok": True, **_state}
    finally:
        _state.update(running=False, finished=time.time())
        _lock.release()


def progress() -> dict:
    return dict(_state)


def search_titles(db, text: str, kind: str, fetch=None) -> list[dict]:
    """Titles matching what the person typed (for the review list): a name, or an IMDb id (tt…), or a TMDB id."""
    screen = make_screen(db, fetch)
    text = " ".join(str(text).split())
    if not text:
        return []
    if text.lower().startswith("tt") and text[2:].isdigit():
        return screen.find_imdb(text.lower())
    parsed = {"kind": "episode" if kind == "tv" else "movie", "title": text, "year": None, "season": None, "episode": None, "ids": {"tmdb": None, "imdb": None}}
    found = screen.search(parsed)
    if not found and screen.tmdb:
        return []
    return found[:8]


def choose(db, media_id: int, kind: str, tmdb_id: int | None, season=None, episode=None, fetch=None, imdb_id: str | None = None) -> dict:
    """The person picked the title for a video. This answer is final: later runs leave it alone."""
    from ..core.content_mode import FILTER_ACTIVE
    token = FILTER_ACTIVE.set(False)        # matching names against every row, whatever mode the page is in
    try:
        screen = make_screen(db, fetch)
        if not tmdb_id and imdb_id:                       # an IMDb id (tt…) is turned into the TMDB title it names
            hit = next((c for c in screen.find_imdb(imdb_id) if c["kind"] == kind), None)
            if not hit:
                raise LookupError("No title has that IMDb id")
            tmdb_id = hit["tmdb_id"]
        if not tmdb_id:
            raise LookupError("Give a title to use")
        d = screen.details(kind, int(tmdb_id))
        if not d:
            raise LookupError("The provider has no such title")
        ep = screen.episode(d["tmdb_id"], int(season), int(episode)) if kind == "tv" and season is not None and episode is not None else None
        apply_match(db, int(media_id), d, ep, season=season, number=episode, confidence=1.0, source="chosen", chosen=True)
        return {"title": d["title"], "year": d.get("year")}
    finally:
        FILTER_ACTIVE.reset(token)
