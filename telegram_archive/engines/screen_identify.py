"""Identify the videos of SFW folders as movies and TV episodes, and keep the answer.

For each video that is not identified yet: read its name, ask the providers (``metadata/screen_ids.py``), and either apply the match
(title, date, cast, studios or networks, genres, all marked SFW), or keep the candidates for the person to pick (REVIEW), or note that
nothing was found (UNMATCHED). A person's choice is never undone by a later run.
"""
from __future__ import annotations

import json
import os
import threading

from .. import config
from ..core.swallowed import note as _swallowed_note
from ..metadata import screen_ids as si

_lock = threading.Lock()
_state: dict = {"running": False, "total": 0, "done": 0}
SUFFIX = {"performers": " (Actor)", "studios": " (Studio)", "tags": " (Genre)"}


def keys(db) -> dict:
    return {"tmdb": str(db.setting("tmdb_api_key") or ""), "omdb": str(db.setting("omdb_api_key") or ""), "opensubtitles": str(db.setting("opensubtitles_api_key") or "")}


def tvmaze_on(db) -> bool:
    """TVmaze needs no key, but it is asked about the person's file names only when they switched it on."""
    return str(db.setting("tvmaze_enabled") or "").lower() in ("1", "true", "yes")


def configured(db) -> bool:
    k = keys(db)
    return bool(k["tmdb"] or k["omdb"] or tvmaze_on(db))


def http_fetch(url: str, params: dict, headers: dict) -> dict | list:
    """The real fetch: JSON over HTTPS, {} when the provider has nothing (404), an error otherwise. One polite retry when asked to slow down."""
    import time
    import requests
    for attempt in (0, 1):
        r = requests.get(url, params=params, headers=headers, timeout=20)
        if r.status_code == 404:
            return {}
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(float(r.headers.get("Retry-After", "2") or 2), 10.0))
            continue
        r.raise_for_status()
        return r.json()
    return {}


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
    return si.Screen(fetch or http_fetch, tmdb=k["tmdb"], omdb=k["omdb"], opensubtitles=k["opensubtitles"], tvmaze=tvmaze_on(db), cache=DbCache(db))


def _entity(db, table: str, name: str, tmdb_id=None, image: str = "") -> int | None:
    """An SFW person, studio or genre by name. A name already used by a non-SFW row gets a suffix instead of taking it over."""
    from .entity_identity import upsert_performer, upsert_studio
    name = " ".join(str(name).split())
    if not name:
        return None
    with db.reading() as conn:
        row = conn.execute(f"SELECT id,content_rating,{'provider_ids' if table != 'tags' else chr(39) + chr(39)} AS provider_ids FROM {table} WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
    use = name
    mine = bool(tmdb_id) and f'"{tmdb_id}"' in str(row["provider_ids"] if row else "")
    if row and (row["content_rating"] or "nsfw") != "sfw" and not mine:
        use = name + SUFFIX[table]
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
    studios = [_entity(db, "studios", n) for n in d.get("companies", [])[:6]]
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
                return "MATCHED"
            result["state"] = "REVIEW"
    _record(db, media_id, result["state"], result)
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
        _state.update(running=True, total=len(todo), done=0, failed=0, matched=0, review=0, unmatched=0)
        screen = make_screen(db, fetch)
        ctx: dict = {"memo": {}, "entities": {}}   # one answer per show for the whole run
        for row in todo:
            try:
                out = identify_one(db, screen, int(row["id"]), row["path"] or row["filename"] or "", ctx)
                _state[{"MATCHED": "matched", "REVIEW": "review"}.get(out, "unmatched")] += 1
            except Exception as exc:
                _swallowed_note("screen_identify.run")
                db.log(f"Identify failed for media {row['id']}: {type(exc).__name__}: {exc}"[:300], "WARN")
                _state["failed"] += 1
            _state["done"] += 1
        _state["requests"] = screen.calls
        return {"ok": True, **_state}
    finally:
        _state["running"] = False
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
