"""The Collection: movies and shows to keep, found and downloaded automatically (Archive X's small, simple take on Sonarr and Radarr).

Sonarr and Radarr watch a list of titles, look for the releases of whatever is missing, hand the best one to a download client and file the result into the library. This does the
same with the pieces Archive X already has, and none of the setup:

* the list is "Keep this" on a movie or show (a show: every episode, or only new ones from now on);
* what is missing is what the title's catalogue says has aired and the library does not have (the same match Archive X uses to show a show's missing episodes);
* the releases come from the person's own Stremio add-ons (Torrentio and the like), the torrents they already see on a title page;
* the download is done by Stremio's streaming server (the one Archive X can set up by itself), which joins the swarm and serves the file: it is saved into the Collection folder as
  `Movies/Title (Year)/Title (Year).mkv` and `Series/Title (Year)/Season 01/Title - S01E02.mkv`;
* the Collection folder is a local folder of the library, so a finished file is scanned, named and shown like any other.

It is off until the person turns it on, and every step is visible in the Downloads window (Kept and Downloads): what is wanted, what is downloading, what failed and why.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import config
from . import collection_rules as rules

MOVIE_WAIT_DAYS = 14          # a film is looked for this long after its release date: earlier copies are cam recordings
RECHECK_HOURS = 6             # how often a title's catalogue is read again for new episodes
SEARCHES_PER_ROUND = 6
STALL_SECONDS = 240           # a download that gets no bytes for this long is given up and another release tried
BACKOFF_HOURS = (1, 3, 6, 12, 24)
GB = rules.GB
DEFAULTS = {"collection_on": "0", "collection_quality": str(rules.DEFAULT_QUALITY), "collection_max_gb_movie": "12", "collection_max_gb_episode": "4"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, imdb TEXT NOT NULL UNIQUE, title TEXT NOT NULL, year INTEGER, poster TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'all', monitored INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL, checked_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS collection_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title_id INTEGER NOT NULL REFERENCES collection_titles(id) ON DELETE CASCADE, season INTEGER NOT NULL DEFAULT 0, episode INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '', air_date TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'WANTED', attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL DEFAULT '',
    release TEXT NOT NULL DEFAULT '', info_hash TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', file_idx INTEGER, trackers TEXT NOT NULL DEFAULT '[]', avoid TEXT NOT NULL DEFAULT '[]',
    got INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, path TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(title_id, season, episode));
"""

def ensure(db) -> None:
    if getattr(db, "_collection_ready", False):
        return
    with db.lock:
        db.conn.executescript(_SCHEMA)
        if "source_url" not in {r["name"] for r in db.conn.execute("PRAGMA table_info(collection_items)").fetchall()}:     # a download that is a plain link (debrid, a direct file), not a torrent
            db.conn.execute("ALTER TABLE collection_items ADD COLUMN source_url TEXT NOT NULL DEFAULT ''")
        db.conn.commit()
    db._collection_ready = True      # per database object: another database (a test, a restored copy) has its own tables


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


# ---- settings -----------------------------------------------------------------------------------------------------------------------------------
def default_dir() -> str:
    return str(Path.home() / "Videos" / "Archive X Collection")


def settings(db) -> dict:
    def get(key):
        v = db.setting(key)
        return str(v if v not in (None, "") else DEFAULTS[key])
    quality = int(get("collection_quality")) if get("collection_quality").isdigit() else rules.DEFAULT_QUALITY
    return {"on": get("collection_on") in ("1", "true", "True"), "dir": str(db.setting("collection_dir") or default_dir()),
            "quality": quality if quality in rules.QUALITIES else rules.DEFAULT_QUALITY,
            "max_gb_movie": _num(get("collection_max_gb_movie"), 12), "max_gb_episode": _num(get("collection_max_gb_episode"), 4),
            "parallel": _parallel(db.setting("collection_parallel"))}


def _parallel(v) -> int:
    """How many downloads run at once (1 to 4)."""
    try:
        return max(1, min(4, int(float(v))))
    except (TypeError, ValueError):
        return 1


def _num(v, d) -> float:
    try:
        return max(0.5, min(200.0, float(v)))
    except (TypeError, ValueError):
        return float(d)


def save_settings(db, body: dict) -> dict:
    if "on" in body:
        db.set_setting("collection_on", "1" if body["on"] else "0")
    if "quality" in body:
        q = int(body["quality"])
        if q not in rules.QUALITIES:
            raise ValueError("Choose 720, 1080 or 2160.")
        db.set_setting("collection_quality", str(q))
    for key in ("max_gb_movie", "max_gb_episode"):
        if key in body:
            db.set_setting(f"collection_{key}", str(_num(body[key], 4)))
    if "parallel" in body:
        db.set_setting("collection_parallel", str(_parallel(body["parallel"])))
    if "dir" in body:
        path = os.path.realpath(os.path.expanduser(str(body["dir"] or "").strip())) if str(body["dir"] or "").strip() else ""
        if not path:
            raise ValueError("Choose a folder.")
        data = os.path.realpath(str(config.USER_DATA_DIR))
        if path == data or path.startswith(data + os.sep):
            raise ValueError("That folder holds Archive X's own data; choose another.")
        db.set_setting("collection_dir", path)
    return settings(db)


# ---- the list ------------------------------------------------------------------------------------------------------------------------------------
def add_title(db, kind: str, imdb: str, title: str, year: int | None, poster: str = "", scope: str = "all") -> dict:
    ensure(db)
    kind = "movie" if kind == "movie" else "series"
    scope = "new" if (kind == "series" and scope == "new") else "all"
    now = _iso(now_utc())
    with db.lock:
        row = db.conn.execute("SELECT id FROM collection_titles WHERE imdb=?", (imdb,)).fetchone()
        if row:
            db.conn.execute("UPDATE collection_titles SET monitored=1, scope=?, checked_at='' WHERE id=?", (scope, int(row["id"])))
            tid = int(row["id"])
        else:
            tid = int(db.conn.execute("INSERT INTO collection_titles(kind,imdb,title,year,poster,scope,added_at) VALUES(?,?,?,?,?,?,?)",
                                      (kind, imdb, str(title)[:200], year, str(poster)[:500], scope, now)).lastrowid)
        db.conn.commit()
    return {"id": tid, "kind": kind, "imdb": imdb, "title": title}


def remove_title(db, title_id: int) -> bool:
    ensure(db)
    with db.lock:
        items = db.conn.execute("SELECT id, state, path FROM collection_items WHERE title_id=? AND state='DOWNLOADING'", (int(title_id),)).fetchall()
        for it in items:
            _drop_part(db, int(it["id"]))
        db.conn.execute("PRAGMA foreign_keys=ON")
        cur = db.conn.execute("DELETE FROM collection_titles WHERE id=?", (int(title_id),))
        db.conn.execute("DELETE FROM collection_items WHERE title_id=?", (int(title_id),))
        db.conn.commit()
        return bool(cur.rowcount)


def enqueue_stream(db, kind: str, imdb: str, title: str, year: int | None, poster: str, season: int, episode: int, *, release: str, info_hash: str = "", file_idx=None, trackers: list[str] | None = None, name: str = "", url: str = "") -> dict:
    """Download one chosen stream (the person pressed Download on it): the item goes straight to DOWNLOADING with that release, and nothing else is looked for.

    `url` is a stream that is a plain link (a debrid or direct file) instead of a torrent: it is fetched as it is, under the same rules that decide what may be played.
    A title that is not already kept is added unmonitored: it is only a home for this download, and no other episode of it is searched for. Raises ValueError with a plain
    reason when this one is already downloading or saved.
    """
    ensure(db)
    kind = "movie" if kind == "movie" else "series"
    season, episode = (0, 0) if kind == "movie" else (int(season), int(episode))
    now = _iso(now_utc())
    with db.lock:
        row = db.conn.execute("SELECT id FROM collection_titles WHERE imdb=?", (imdb,)).fetchone()
        tid = int(row["id"]) if row else int(db.conn.execute(
            "INSERT INTO collection_titles(kind,imdb,title,year,poster,scope,monitored,added_at) VALUES(?,?,?,?,?,'all',0,?)", (kind, imdb, str(title)[:200], year, str(poster)[:500], now)).lastrowid)
        have = db.conn.execute("SELECT id, state FROM collection_items WHERE title_id=? AND season=? AND episode=?", (tid, season, episode)).fetchone()
        if have and have["state"] == "DOWNLOADING":
            raise ValueError("That is already downloading.")
        if have and have["state"] == "DONE":
            raise ValueError("That is already saved on this computer.")
        values = (str(release)[:240], info_hash, str(url)[:2000], file_idx if isinstance(file_idx, int) else None, json.dumps(list(trackers or [])[:30]), now)
        if have:
            iid = int(have["id"])
            db.conn.execute("UPDATE collection_items SET state='DOWNLOADING', release=?, info_hash=?, source_url=?, file_idx=?, trackers=?, got=0, total=0, note='', next_at='', updated_at=? WHERE id=?", (*values, iid))
        else:
            iid = int(db.conn.execute(
                "INSERT INTO collection_items(title_id,season,episode,name,state,attempts,release,info_hash,source_url,file_idx,trackers,updated_at) VALUES(?,?,?,?,'DOWNLOADING',1,?,?,?,?,?,?)",
                (tid, season, episode, str(name)[:160], *values)).lastrowid)
        db.conn.commit()
    return {"title_id": tid, "item_id": iid}


def pause_item(db, item_id: int) -> bool:
    """Stop a download for now: it keeps what it has fetched and carries on from there when resumed."""
    ensure(db)
    with db.lock:
        cur = db.conn.execute("UPDATE collection_items SET state='PAUSED', updated_at=? WHERE id=? AND state='DOWNLOADING'", (_iso(now_utc()), int(item_id)))
        db.conn.commit()
        return bool(cur.rowcount)


def resume_item(db, item_id: int) -> bool:
    ensure(db)
    with db.lock:
        cur = db.conn.execute("UPDATE collection_items SET state='DOWNLOADING', note='', updated_at=? WHERE id=? AND state='PAUSED'", (_iso(now_utc()), int(item_id)))
        db.conn.commit()
        return bool(cur.rowcount)


def delete_item(db, item_id: int) -> bool:
    """Take one item out of the list (a download in progress is stopped and its partial file removed; a finished file stays where it is, in the library). A title that was
    only a home for a manual download is removed with its last item."""
    ensure(db)
    with db.lock:
        it = db.conn.execute("SELECT i.id, i.title_id, t.monitored FROM collection_items i JOIN collection_titles t ON t.id=i.title_id WHERE i.id=?", (int(item_id),)).fetchone()
        if not it:
            return False
        db.conn.execute("DELETE FROM collection_items WHERE id=?", (int(item_id),))
        left = db.conn.execute("SELECT COUNT(*) AS n FROM collection_items WHERE title_id=?", (int(it["title_id"]),)).fetchone()["n"]
        if not left and not it["monitored"]:
            db.conn.execute("DELETE FROM collection_titles WHERE id=?", (int(it["title_id"]),))
        db.conn.commit()
    _drop_part(db, int(item_id))
    return True


def has_downloads(db) -> bool:
    ensure(db)
    with db.reading() as conn:
        return conn.execute("SELECT 1 FROM collection_items WHERE state='DOWNLOADING' LIMIT 1").fetchone() is not None


def _part_path(db, item_id: int) -> Path:
    return Path(settings(db)["dir"]) / ".downloading" / f"{int(item_id)}.part"


def _drop_part(db, item_id: int) -> None:
    try:
        _part_path(db, item_id).unlink(missing_ok=True)
    except OSError:
        pass


def library_has(db, kind: str, imdb: str) -> set[tuple[int, int]]:
    """What the library already has of a title, as (season, episode) pairs ((0, 0) for a movie): files named and matched to it (the same match the show page uses for missing episodes)."""
    with db.reading() as conn:
        rows = conn.execute(
            "SELECT s.season, s.episode FROM media_screen s JOIN screen_titles t ON t.id=s.title_id JOIN media m ON m.id=s.media_id "
            "WHERE t.imdb_id=? AND s.state='MATCHED' AND COALESCE(m.status,'')<>'SOURCE_DELETED'", (imdb,)).fetchall()
    return {(int(r["season"] or 0), int(r["episode"] or 0)) for r in rows} if kind == "series" else ({(0, 0)} if rows else set())


# ---- what is wanted -----------------------------------------------------------------------------------------------------------------------------
def episodes_of(meta: dict) -> list[dict]:
    """The aired-or-not episodes of a catalogue entry (specials, season 0, are left out)."""
    out = []
    for v in (meta.get("videos") or [])[:4000]:
        if not isinstance(v, dict):
            continue
        s, e = v.get("season"), v.get("episode", v.get("number"))
        if isinstance(s, int) and isinstance(e, int) and s > 0 and e > 0:
            out.append({"season": s, "episode": e, "name": str(v.get("title") or v.get("name") or "")[:160], "released": str(v.get("released") or "")[:10]})
    return out


def movie_available(meta: dict, today: date) -> bool:
    released = str(meta.get("released") or "")[:10]
    try:
        return date.fromisoformat(released) <= today - timedelta(days=MOVIE_WAIT_DAYS)
    except ValueError:
        year = str(meta.get("releaseInfo") or meta.get("year") or "")[:4]
        return year.isdigit() and int(year) < today.year


def wanted_for(title: dict, meta: dict, have: set[tuple[int, int]], today: date) -> list[dict]:
    """The items to look for, given a title, its catalogue entry and what the library has: every aired episode not there (for a show kept from now on, only those aired since it was added)."""
    if title["kind"] == "movie":
        return [] if (0, 0) in have or not movie_available(meta, today) else [{"season": 0, "episode": 0, "name": "", "air_date": ""}]
    since = str(title.get("added_at") or "")[:10] if title.get("scope") == "new" else ""
    out = []
    for ep in episodes_of(meta):
        aired = ep["released"]
        if not aired or aired > today.isoformat() or (since and aired < since) or (ep["season"], ep["episode"]) in have:
            continue
        out.append({"season": ep["season"], "episode": ep["episode"], "name": ep["name"], "air_date": aired})
    return out


def refresh(db, meta_of, *, today: date | None = None, force: bool = False) -> int:
    """Read each kept title's catalogue entry (at most every few hours) and make an item of whatever is missing; items the library has meanwhile are marked done. Returns the new items."""
    ensure(db)
    today = today or now_utc().date()
    new = 0
    with db.reading() as conn:
        titles = [dict(r) for r in conn.execute("SELECT * FROM collection_titles WHERE monitored=1").fetchall()]
    cutoff = _iso(now_utc() - timedelta(hours=RECHECK_HOURS))
    for t in titles:
        if not force and t["checked_at"] and t["checked_at"] > cutoff:
            continue
        try:
            meta = meta_of(t["kind"], t["imdb"]) or {}
        except Exception:
            continue
        if not meta:
            continue
        have = library_has(db, t["kind"], t["imdb"])
        now = _iso(now_utc())
        with db.lock:
            if meta.get("poster") and not t["poster"]:
                db.conn.execute("UPDATE collection_titles SET poster=? WHERE id=?", (str(meta["poster"])[:500], t["id"]))
            for w in wanted_for(t, meta, have, today):
                cur = db.conn.execute("INSERT OR IGNORE INTO collection_items(title_id,season,episode,name,air_date,state,updated_at) VALUES(?,?,?,?,?,'WANTED',?)",
                                      (t["id"], w["season"], w["episode"], w["name"], w["air_date"], now))
                new += int(cur.rowcount or 0)
            for s, e in have:     # the library has it now (a file added by hand, or one that finished)
                db.conn.execute("UPDATE collection_items SET state='DONE', note='In your library' WHERE title_id=? AND season=? AND episode=? AND state IN ('WANTED','DOWNLOADING')", (t["id"], s, e))
            db.conn.execute("UPDATE collection_titles SET checked_at=? WHERE id=?", (now, t["id"]))
            db.conn.commit()
    return new


# ---- finding a release --------------------------------------------------------------------------------------------------------------------------
def backoff_hours(attempts: int) -> int:
    return BACKOFF_HOURS[min(max(attempts, 1), len(BACKOFF_HOURS)) - 1]


def search_item(db, item: dict, finder, cfg: dict | None = None, now: datetime | None = None) -> bool:
    """Look for a release of one wanted item and, when there is an acceptable one, choose it (state DOWNLOADING). True when one was chosen."""
    cfg = cfg or settings(db)
    now = now or now_utc()
    with db.reading() as conn:
        t = dict(conn.execute("SELECT * FROM collection_titles WHERE id=?", (item["title_id"],)).fetchone())
    episode = item["season"] > 0
    try:
        candidates = finder(t["kind"], t["imdb"], item["season"] if episode else None, item["episode"] if episode else None)
    except Exception as exc:
        candidates, error = [], f"The add-ons could not be asked: {type(exc).__name__}"
    else:
        error = ""
    cap = int((cfg["max_gb_episode"] if episode else cfg["max_gb_movie"]) * GB)
    pick = rules.pick_release(candidates, quality=cfg["quality"], max_bytes=cap, avoid=set(json.loads(item["avoid"] or "[]")), episode=episode)
    attempts = int(item["attempts"]) + 1
    with db.lock:
        if pick:
            db.conn.execute(
                "UPDATE collection_items SET state='DOWNLOADING', attempts=?, release=?, info_hash=?, file_idx=?, trackers=?, got=0, total=0, note='', updated_at=? WHERE id=? AND state='WANTED'",
                (attempts, str(pick.get("name") or "")[:240], pick["infoHash"], pick.get("fileIdx") if isinstance(pick.get("fileIdx"), int) else None, json.dumps(pick.get("trackers") or []), _iso(now), item["id"]))
        else:
            why = error or ("No release found yet" if not candidates else "No acceptable release yet (none at the right quality and size with people sharing it)")
            db.conn.execute("UPDATE collection_items SET attempts=?, next_at=?, note=?, updated_at=? WHERE id=? AND state='WANTED'",
                            (attempts, _iso(now + timedelta(hours=backoff_hours(attempts))), why, _iso(now), item["id"]))
        db.conn.commit()
    return bool(pick)


# ---- downloading ----------------------------------------------------------------------------------------------------------------------------------
class Stopped(Exception):
    """The worker is shutting down or the item was removed: the partial file is kept, the item stays as it is."""


def final_path(db, item: dict, title: dict, ext: str) -> Path:
    root = Path(settings(db)["dir"])
    parts = (rules.movie_path(str(root), title["title"], title["year"], ext) if item["season"] == 0
             else rules.episode_path(str(root), title["title"], title["year"], item["season"], item["episode"], ext))
    return root.joinpath(*parts)


def download_item(db, item: dict, server_base: str, *, open_torrent=None, open_direct=None, should_stop=lambda: False, stall: float = STALL_SECONDS, now=time.monotonic) -> str:
    """Fetch the chosen release through the streaming server into a partial file (resuming where an earlier run stopped) and move it into place. Returns the final path.
    Raises Stopped when told to stop (nothing is lost), RuntimeError with a plain reason on a failure."""
    from ..metadata import streaming_server as ss
    open_torrent = open_torrent or ss.open_torrent
    with db.reading() as conn:
        t = dict(conn.execute("SELECT * FROM collection_titles WHERE id=?", (item["title_id"],)).fetchone())
    direct = str(item.get("source_url") or "")
    url = direct or ss.torrent_url(server_base, item["info_hash"], item["file_idx"], json.loads(item["trackers"] or "[]"))
    part = _part_path(db, item["id"])
    part.parent.mkdir(parents=True, exist_ok=True)
    offset = part.stat().st_size if part.exists() else 0
    try:
        if direct:
            from ..metadata import addon_client as ac
            resp = (open_direct or ac.open_stream)(direct, f"bytes={offset}-" if offset else "")
        else:
            resp = open_torrent(server_base, url, f"bytes={offset}-" if offset else "")
    except Exception as exc:
        raise RuntimeError(("The link could not be opened: " if direct else "The streaming server could not open the torrent: ") + f"{getattr(exc, 'reason', None) or exc}") from exc
    with resp:
        status = getattr(resp, "status", 200) or 200
        if offset and status != 206:
            offset = 0                                             # the server ignored the range: start again
        length = int(resp.headers.get("Content-Length") or 0)
        crange = resp.headers.get("Content-Range") or ""
        total = int(crange.rsplit("/", 1)[-1]) if "/" in crange and crange.rsplit("/", 1)[-1].isdigit() else (offset + length if length else 0)
        ext = rules.extension_for(resp.headers.get("Content-Type") or "")
        got, last_data, last_save = offset, now(), 0.0
        with open(part, "ab" if offset else "wb") as out:
            while True:
                if should_stop():
                    raise Stopped()
                try:
                    chunk = resp.read(1024 * 1024)
                except Exception as exc:
                    raise RuntimeError(f"The download broke off: {exc}") from exc
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                last_data = now()
                if last_data - last_save >= 2:
                    last_save = last_data
                    _progress(db, item["id"], got, total)
                    if not _downloading(db, item["id"]):
                        raise Stopped()                            # the person removed or paused it meanwhile
                if now() - last_data > stall:
                    raise RuntimeError("The download stalled.")
    if total and got < total:
        raise RuntimeError("The download ended early.")
    if got < 1024 * 1024:
        raise RuntimeError("The streaming server sent almost nothing; the torrent probably has no seeders.")
    dest = final_path(db, item, t, ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(dest.stem + f" ({int(item['id'])})" + dest.suffix)
    os.replace(part, dest)
    _progress(db, item["id"], got, got)
    return str(dest)


def _state(db, item_id: int) -> str | None:
    with db.reading() as conn:
        row = conn.execute("SELECT state FROM collection_items WHERE id=?", (int(item_id),)).fetchone()
    return str(row["state"]) if row else None


def _downloading(db, item_id: int) -> bool:
    with db.reading() as conn:
        return conn.execute("SELECT 1 FROM collection_items WHERE id=? AND state='DOWNLOADING'", (int(item_id),)).fetchone() is not None


def _progress(db, item_id: int, got: int, total: int) -> None:
    with db.lock:
        db.conn.execute("UPDATE collection_items SET got=?, total=?, updated_at=? WHERE id=?", (int(got), int(total), _iso(now_utc()), int(item_id)))
        db.conn.commit()


def finish_item(db, item_id: int, path: str) -> None:
    with db.lock:
        db.conn.execute("UPDATE collection_items SET state='DONE', path=?, note='', got=total, updated_at=? WHERE id=?", (path, _iso(now_utc()), int(item_id)))
        db.conn.commit()


def fail_item(db, item: dict, reason: str, now: datetime | None = None) -> None:
    """A release that did not work is not tried again for this item: the next search takes the next best one."""
    now = now or now_utc()
    avoid = json.loads(item["avoid"] or "[]") + [item["info_hash"]]
    _drop_part(db, item["id"])
    with db.reading() as conn:
        t = conn.execute("SELECT monitored FROM collection_titles WHERE id=?", (item["title_id"],)).fetchone()
    if t is not None and not t["monitored"]:
        # a download the person chose by hand is not searched for again: it stays in the list as failed, with the release kept so that Try again can retry it
        with db.lock:
            db.conn.execute("UPDATE collection_items SET state='FAILED', got=0, total=0, note=?, updated_at=? WHERE id=?", (reason[:240], _iso(now), item["id"]))
            db.conn.commit()
        return
    with db.lock:
        db.conn.execute("UPDATE collection_items SET state='WANTED', avoid=?, release='', info_hash='', file_idx=NULL, got=0, total=0, next_at=?, note=?, updated_at=? WHERE id=?",
                        (json.dumps(avoid[-12:]), _iso(now + timedelta(hours=backoff_hours(int(item["attempts"])))), reason[:240], _iso(now), item["id"]))
        db.conn.commit()


# ---- the folder ----------------------------------------------------------------------------------------------------------------------------------
def ensure_source(db, root: str) -> int | None:
    """The Collection folder as a local folder of the library (SFW, videos): found if it is already covered by one, created otherwise. None when it cannot be (it overlaps another folder)."""
    from . import local_scanner as ls
    real = os.path.realpath(root)
    os.makedirs(real, exist_ok=True)
    with db.reading() as conn:
        rows = [(int(r["id"]), os.path.realpath(r["path"])) for r in conn.execute("SELECT id, path FROM local_sources").fetchall()]
    for sid, path in rows:
        if real == path or real.startswith(path + os.sep):
            return sid
        if path.startswith(real + os.sep):
            return None
    now = config.now_iso()
    with db.lock:
        cur = db.conn.execute("INSERT INTO local_sources(path,recursive,created_at) VALUES(?,?,?)", (real, 1, now))
        sid = int(cur.lastrowid)
        db.conn.execute("INSERT INTO channels(telegram_id,title,username,role,in_library,content_rating,holds,updated_at) VALUES(?,?,?,?,1,'sfw','videos',?)",
                        (ls.channel_id(sid), os.path.basename(real.rstrip("\\/")) or real, None, "local", now))
        db.conn.commit()
    return sid


def import_new(db, root: str) -> None:
    """Scan the Collection folder so finished files join the library, then name them (the same steps a folder scan takes)."""
    from . import local_scanner as ls, local_videos, screen_identify
    sid = ensure_source(db, root)
    if sid is None:
        db.log("Collection: the folder overlaps another library folder, so new files are not scanned by themselves.", "WARN")
        return
    ls.scan_source(db, sid)
    local_videos.process(db, ls.channel_id(sid))
    if screen_identify.configured(db):
        screen_identify.run(db, ls.channel_id(sid))


# ---- one round -----------------------------------------------------------------------------------------------------------------------------------
def overview(db) -> dict:
    ensure(db)
    cfg = settings(db)
    with db.reading() as conn:
        titles = [dict(r) for r in conn.execute("SELECT * FROM collection_titles ORDER BY LOWER(title)").fetchall()]
        items = [dict(r) for r in conn.execute("SELECT * FROM collection_items ORDER BY season, episode").fetchall()]
    by: dict[int, list[dict]] = {}
    for it in items:
        by.setdefault(it["title_id"], []).append(it)
    out = []
    for t in titles:
        mine = by.get(t["id"], [])
        counts = {s: sum(1 for i in mine if i["state"] == s) for s in ("WANTED", "DOWNLOADING", "DONE", "SKIPPED", "FAILED", "PAUSED")}
        out.append({"id": t["id"], "kind": t["kind"], "imdb": t["imdb"], "title": t["title"], "year": t["year"], "poster": t["poster"], "scope": t["scope"], "monitored": bool(t["monitored"]), "manual": not t["monitored"],
                    "counts": counts, "items": [{"id": i["id"], "season": i["season"], "episode": i["episode"], "name": i["name"], "label": rules.release_label(i["season"], i["episode"], i["name"]),
                                                 "air_date": i["air_date"], "state": i["state"], "release": i["release"], "note": i["note"], "got": i["got"], "total": i["total"], "attempts": i["attempts"]} for i in mine]})
    return {"settings": cfg, "titles": out}


def tick(db, *, meta_of, finder, server_base: str, ensure_server, stop: threading.Event | None = None, download=download_item, import_files=import_new, today: date | None = None) -> dict:
    """One round: refresh what is wanted, look for releases, download what was chosen one after another, and bring finished files into the library. Never raises."""
    ensure(db)
    cfg = settings(db)
    stop = stop or threading.Event()
    done = new = searched = 0
    try:
        new = refresh(db, meta_of, today=today) if cfg["on"] else 0           # what is missing is looked for only while automatic downloads are on
        now = _iso(now_utc())
        with db.reading() as conn:
            due = [] if not cfg["on"] else [dict(r) for r in conn.execute(
                "SELECT i.* FROM collection_items i JOIN collection_titles t ON t.id=i.title_id WHERE t.monitored=1 AND i.state='WANTED' AND (i.next_at='' OR i.next_at<=?) ORDER BY i.air_date DESC, i.id LIMIT ?",
                (now, SEARCHES_PER_ROUND)).fetchall()]
        for it in due:
            if stop.is_set():
                break
            search_item(db, it, finder, cfg)
            searched += 1
        with db.reading() as conn:
            queue = [dict(r) for r in conn.execute("SELECT * FROM collection_items WHERE state='DOWNLOADING' ORDER BY id").fetchall()]
        if queue and not stop.is_set():
            ok, why = ensure_server()
            if not ok:
                with db.lock:
                    db.conn.execute("UPDATE collection_items SET note=? WHERE state='DOWNLOADING'", (why,))
                    db.conn.commit()
                queue = []
        def run_one(it: dict) -> str:
            """One download: "done", "failed", "skip" (removed or paused meanwhile, or not downloading any more) or "halt" (the worker is stopping)."""
            if stop.is_set():
                return "halt"
            if not _downloading(db, it["id"]):
                return "skip"
            try:
                path = download(db, it, server_base, should_stop=stop.is_set)
            except Stopped:
                state = _state(db, it["id"])
                if state == "DOWNLOADING":
                    return "halt"                                  # the worker is stopping: the partial file is kept for next time
                if state is None:
                    _drop_part(db, it["id"])                       # the person removed it
                return "skip"                                      # (paused: the partial file is kept)
            except Exception as exc:
                fail_item(db, it, str(exc) or type(exc).__name__)
                db.log(f"Collection: {it['release'] or it['id']} failed: {exc}", "WARN")
                return "failed"
            finish_item(db, it["id"], path)
            return "done"
        results: list[str] = []
        if cfg["parallel"] > 1 and len(queue) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=cfg["parallel"], thread_name_prefix="collection-download") as pool:
                results = list(pool.map(run_one, queue))
        else:
            for it in queue:
                results.append(run_one(it))
                if results[-1] == "halt":
                    break
        done = results.count("done")
        if done:
            import_files(db, cfg["dir"])
    except Exception as exc:
        db.log(f"Collection round failed: {type(exc).__name__}: {exc}", "WARN")
    return {"new": new, "searched": searched, "downloaded": done}
