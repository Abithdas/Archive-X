"""Performer gallery: every picture the metadata providers hold of a performer, kept on this PC, and the face references learned from it.

Before this, face matching had ONE picture per performer (the profile picture). Now each performer has a gallery:

* ``sync_performer`` asks the providers (StashDB, ThePornDB) for the performer's pictures through the same lookup the picture picker uses,
  which only trusts an exact name, an exact alias or a stored provider id, downloads the ones not yet kept, and stores them as files here.
* A picture you remove is remembered (REJECTED) and never fetched again; removing it also drops it as a face reference.
* ``index_faces`` reads the face in each picture. Only a picture with one clear face serves as a reference, so a photo of two people
  cannot teach the matcher the wrong face.
* ``reference_matrix`` gathers the references used for matching: the profile picture, the gallery faces, and up to a dozen varied faces you
  have confirmed for the performer (never one you took out). So the matcher improves with every confirmation, removal and refresh.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import socket
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .. import config
from ..core.swallowed import note as _swallowed_note

MAX_PICTURES = 40                    # per performer
MAX_BYTES = 8 * 1024 * 1024          # one picture
MIN_SIDE = 120                       # pixels: smaller cannot hold a usable face
MAX_PIXELS = 60_000_000
DOWNLOAD_TIMEOUT = 25
RESYNC_AFTER_DAYS = 30
CONFIRMED_EXEMPLARS = 12             # varied confirmed faces kept as references per performer
_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
_SYNCING: dict[int, asyncio.Task] = {}

_DDL = """
CREATE TABLE IF NOT EXISTS performer_gallery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    performer_id INTEGER NOT NULL,
    url_hash TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    faces INTEGER NOT NULL DEFAULT -1,
    embedding_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'provider',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(performer_id, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_performer_gallery_performer ON performer_gallery(performer_id, status);
CREATE TABLE IF NOT EXISTS performer_gallery_state (
    performer_id INTEGER PRIMARY KEY,
    last_synced_at TEXT NOT NULL DEFAULT '',
    last_result TEXT NOT NULL DEFAULT '',
    images_found INTEGER NOT NULL DEFAULT 0
);
"""


def _now() -> str:
    return config.now_iso()


def gallery_dir() -> Path:
    return Path(config.CACHE_DIR) / "performer_gallery"


def ensure_tables(db) -> None:
    """The tables are part of the normal schema; this only covers a database opened before they existed. It never uses executescript,
    which would commit a transaction the caller (a merge, say) has open."""
    with db.lock:
        if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='performer_gallery'").fetchone():
            return
        for statement in _DDL.split(";"):
            if statement.strip():
                db.conn.execute(statement)


def url_hash(url: str) -> str:
    return hashlib.sha1(str(url or "").strip().encode("utf-8", "ignore")).hexdigest()


# ---------------------------------------------------------------- safe download ---------------------------------------------------------------
def _host_is_public(host: str) -> bool:
    """A provider's picture address must not lead into this PC or its network."""
    host = (host or "").strip("[]").lower()
    if not host or host in {"localhost"} or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        literals = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            literals = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except OSError:
            return False
    return bool(literals) and all(ip.is_global for ip in literals)


def fetch_bytes(url: str, *, max_bytes: int = MAX_BYTES, timeout: float = DOWNLOAD_TIMEOUT) -> bytes:
    """Download one picture, refusing odd addresses and anything over `max_bytes`."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or not _host_is_public(parsed.hostname):
        raise ValueError("not a public picture address")
    request = urllib.request.Request(url, headers={"User-Agent": "Archive-X/gallery", "Accept": "image/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > max_bytes:
            raise ValueError("picture too large")
        kind = str(response.headers.get("Content-Type") or "").lower()
        if kind and not kind.startswith("image/") and "octet-stream" not in kind:
            raise ValueError("not an image")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("picture too large")
    return data


def inspect_image(data: bytes) -> tuple[int, int, str] | None:
    """(width, height, format) when `data` is a usable photo, else None. Nothing is decoded beyond what is needed to be sure."""
    if not data or len(data) < 120:
        return None
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(data)) as image:
            fmt = str(image.format or "").upper()
            width, height = image.size
            image.verify()
    except Exception:
        return None
    if fmt not in _EXT or min(width, height) < MIN_SIDE or width * height > MAX_PIXELS:
        return None
    return int(width), int(height), fmt


# ---------------------------------------------------------------- sync ---------------------------------------------------------------------------
async def sync_performer(db, performer_id: int, *, max_pictures: int = MAX_PICTURES, candidates: list[dict] | None = None,
                         download: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Pull the provider pictures of one performer that are not kept yet. Safe to repeat: known and removed pictures are skipped."""
    ensure_tables(db)
    pid = int(performer_id)
    with db.lock:
        row = db.conn.execute("SELECT id,name,provider_ids FROM performers WHERE id=?", (pid,)).fetchone()
    summary = {"ok": False, "performer_id": pid, "found": 0, "added": 0, "known": 0, "rejected": 0, "failed": 0, "duplicates": 0}
    if not row:
        summary["reason"] = "performer_not_found"
        return summary
    if str(row["name"] or "").strip().casefold() == "unknown performer":
        summary["reason"] = "unknown_performer"
        return summary
    if candidates is None:
        from ..metadata.providers import performer_image_candidates_from_providers
        try:
            candidates = await performer_image_candidates_from_providers(db, str(row["name"] or ""), row["provider_ids"], limit=max_pictures, offset=0, entity_id=pid)
        except Exception as exc:
            _record_state(db, pid, f"provider lookup failed: {type(exc).__name__}: {str(exc)[:160]}", 0, advance=False)
            summary["reason"] = "provider_lookup_failed"
            return summary
    fetch = download or (lambda u: asyncio.to_thread(fetch_bytes, u))
    with db.lock:
        known = {r["url_hash"]: r["status"] for r in db.conn.execute("SELECT url_hash,status FROM performer_gallery WHERE performer_id=?", (pid,)).fetchall()}
        kept_files = {r["url_hash"]: r["file_path"] for r in db.conn.execute("SELECT url_hash,file_path FROM performer_gallery WHERE performer_id=? AND status='ACTIVE'", (pid,)).fetchall()}
        sha_known = {r["sha256"] for r in db.conn.execute("SELECT sha256 FROM performer_gallery WHERE performer_id=? AND sha256<>''", (pid,)).fetchall()}
        active = int(db.conn.execute("SELECT COUNT(*) FROM performer_gallery WHERE performer_id=? AND status='ACTIVE' AND source='provider'", (pid,)).fetchone()[0])
    summary["found"] = len(candidates)
    folder = gallery_dir() / str(pid)
    for item in candidates:
        url = str((item or {}).get("url") or "").strip()
        if not url:
            continue
        key = url_hash(url)
        if key in known:
            if known[key] == "ACTIVE" and not (gallery_dir() / str(kept_files.get(key) or "-")).is_file():
                # the file is gone (a restored backup, a cleaned cache): fetch it again, keeping the row and what was learnt from it
                if await _refetch(db, pid, key, url, fetch):
                    summary["added"] += 1
                else:
                    summary["failed"] += 1
                continue
            summary["rejected" if known[key] == "REJECTED" else "known"] += 1
            continue
        if active >= max_pictures:
            break
        try:
            data = await fetch(url)
        except Exception:
            summary["failed"] += 1                    # tried again next time: nothing is recorded for a failed download
            continue
        info = inspect_image(data)
        sha = hashlib.sha256(data).hexdigest() if data else ""
        now = _now()
        if info is None:
            _insert(db, pid, key, url, item, status="UNUSABLE", sha="", path="", info=(0, 0), size=0, now=now)
            known[key] = "UNUSABLE"
            summary["failed"] += 1
            continue
        if sha in sha_known:
            _insert(db, pid, key, url, item, status="DUPLICATE", sha=sha, path="", info=info[:2], size=len(data), now=now)
            known[key] = "DUPLICATE"
            summary["duplicates"] += 1
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{sha}.{_EXT[info[2]]}"
        temp = target.with_suffix(target.suffix + ".part")
        temp.write_bytes(data)
        temp.replace(target)
        _insert(db, pid, key, url, item, status="ACTIVE", sha=sha, path=f"{pid}/{target.name}", info=info[:2], size=len(data), now=now)
        known[key] = "ACTIVE"
        sha_known.add(sha)
        active += 1
        summary["added"] += 1
    _record_state(db, pid, "ok", len(candidates), advance=True)
    summary["ok"] = True
    return summary


async def _refetch(db, pid: int, key: str, url: str, fetch) -> bool:
    try:
        data = await fetch(url)
    except Exception:
        return False
    info = inspect_image(data)
    if info is None:
        return False
    sha = hashlib.sha256(data).hexdigest()
    folder = gallery_dir() / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{sha}.{_EXT[info[2]]}"
    temp = target.with_suffix(target.suffix + ".part")
    temp.write_bytes(data)
    temp.replace(target)
    with db.lock:
        db.conn.execute("UPDATE performer_gallery SET file_path=?,sha256=?,width=?,height=?,bytes=?,updated_at=? WHERE performer_id=? AND url_hash=?",
                        (f"{pid}/{target.name}", sha, info[0], info[1], len(data), _now(), pid, key))
        db.conn.commit()
    return True


def _insert(db, pid, key, url, item, *, status, sha, path, info, size, now) -> None:
    with db.lock:
        db.conn.execute(
            "INSERT OR IGNORE INTO performer_gallery(performer_id,url_hash,url,provider,provider_id,file_path,sha256,width,height,bytes,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, key, url, str(item.get("provider") or ""), str(item.get("provider_id") or ""), path, sha, int(info[0]), int(info[1]), int(size), status, now, now))
        db.conn.commit()


def _record_state(db, pid: int, result: str, found: int, *, advance: bool) -> None:
    with db.lock:
        if advance:
            db.conn.execute("INSERT INTO performer_gallery_state(performer_id,last_synced_at,last_result,images_found) VALUES(?,?,?,?) "
                            "ON CONFLICT(performer_id) DO UPDATE SET last_synced_at=excluded.last_synced_at,last_result=excluded.last_result,images_found=excluded.images_found",
                            (pid, _now(), result, int(found)))
        else:
            db.conn.execute("INSERT INTO performer_gallery_state(performer_id,last_result) VALUES(?,?) "
                            "ON CONFLICT(performer_id) DO UPDATE SET last_result=excluded.last_result", (pid, result))
        db.conn.commit()


def performers_due(db, *, limit: int = 100, max_age_days: int = RESYNC_AFTER_DAYS) -> list[int]:
    """Named performers whose gallery was never fetched or is older than `max_age_days`, oldest first."""
    ensure_tables(db)
    with db.reading() as conn:
        rows = conn.execute(
            "SELECT p.id FROM performers p LEFT JOIN performer_gallery_state s ON s.performer_id=p.id "
            "WHERE lower(trim(p.name))<>'unknown performer' AND (s.last_synced_at IS NULL OR s.last_synced_at='' OR s.last_synced_at < datetime('now', ?)) "
            "ORDER BY COALESCE(NULLIF(s.last_synced_at,''),'0') ASC, p.id ASC LIMIT ?", (f"-{int(max_age_days)} day", int(limit))).fetchall()
    return [int(r["id"]) for r in rows]


async def sync_due(db, *, limit: int = 100, checkpoint: Callable[[], Any] | None = None, on_progress: Callable[[int, int, str], None] | None = None,
                   sync: Callable[..., Any] | None = None, budget_seconds: float = 600.0, clock: Callable[[], float] | None = None) -> dict[str, int]:
    """Sync the next `limit` performers that are due, for at most `budget_seconds` (the rest are still due next time, so a large library
    is worked through over several runs instead of holding one up). Stops promptly when `checkpoint` raises (a cancel)."""
    import time as _time
    now = clock or _time.monotonic
    started = now()
    ids = performers_due(db, limit=limit)
    totals = {"performers": 0, "added": 0, "failed": 0}
    run = sync or sync_performer
    for n, pid in enumerate(ids, start=1):
        if now() - started > budget_seconds:
            break
        if checkpoint is not None:
            await checkpoint()
        try:
            result = await run(db, pid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            db.log(f"Performer gallery sync failed for {pid}: {type(exc).__name__}: {exc}", "WARN")
            totals["failed"] += 1
            continue
        totals["performers"] += 1
        totals["added"] += int(result.get("added") or 0)
        if on_progress is not None:
            on_progress(n, len(ids), "")
    return totals


def start_background_sync(db, performer_id: int) -> bool:
    """Start (once) a sync of one performer's gallery in the background. False when one is already running."""
    pid = int(performer_id)
    running = _SYNCING.get(pid)
    if running is not None and not running.done():
        return False

    async def work():
        try:
            await sync_performer(db, pid)
        except Exception as exc:
            db.log(f"Performer gallery sync failed for {pid}: {type(exc).__name__}: {exc}", "WARN")

    _SYNCING[pid] = asyncio.get_running_loop().create_task(work())
    return True


def is_syncing(performer_id: int) -> bool:
    task = _SYNCING.get(int(performer_id))
    return task is not None and not task.done()


# ---------------------------------------------------------------- reading the gallery ------------------------------------------------------------
def list_pictures(db, performer_id: int) -> dict[str, Any]:
    ensure_tables(db)
    pid = int(performer_id)
    with db.reading() as conn:
        rows = conn.execute("SELECT id,provider,width,height,faces,embedding_json,source,created_at FROM performer_gallery "
                            "WHERE performer_id=? AND status='ACTIVE' AND file_path<>'' ORDER BY id", (pid,)).fetchall()
        state = conn.execute("SELECT last_synced_at,last_result,images_found FROM performer_gallery_state WHERE performer_id=?", (pid,)).fetchone()
        removed = int(conn.execute("SELECT COUNT(*) FROM performer_gallery WHERE performer_id=? AND status='REJECTED'", (pid,)).fetchone()[0])
    items = [{"id": int(r["id"]), "provider": r["provider"], "width": int(r["width"]), "height": int(r["height"]),
              "faces": int(r["faces"]), "reference": (r["embedding_json"] or "[]") != "[]", "source": r["source"],
              "url": f"/api/performers/{pid}/gallery/{int(r['id'])}/image"} for r in rows]
    return {"items": items, "removed": removed, "syncing": is_syncing(pid),
            "last_synced_at": state["last_synced_at"] if state else "", "last_result": state["last_result"] if state else "",
            "references": sum(1 for i in items if i["reference"])}


def picture_file(db, performer_id: int, picture_id: int) -> Path | None:
    """The stored file of an active picture, only if it really lies inside the gallery folder."""
    ensure_tables(db)
    with db.reading() as conn:
        row = conn.execute("SELECT file_path FROM performer_gallery WHERE id=? AND performer_id=? AND status='ACTIVE'", (int(picture_id), int(performer_id))).fetchone()
    if not row or not row["file_path"]:
        return None
    base = gallery_dir().resolve()
    path = (base / str(row["file_path"])).resolve()
    return path if base in path.parents and path.is_file() else None


def remove_picture(db, performer_id: int, picture_id: int) -> bool:
    """Take a picture out of the gallery for good: the file goes, it is not fetched again, and it stops being a face reference. A picture
    taken from a video keeps its face as a "not this performer" memory (engines/performer_snapshot_faces.py) and that video is not searched
    for this performer again."""
    ensure_tables(db)
    with db.lock:
        row = db.conn.execute("SELECT id,file_path,source,provider_id FROM performer_gallery WHERE id=? AND performer_id=? AND status='ACTIVE'", (int(picture_id), int(performer_id))).fetchone()
        if not row:
            return False
        if row["source"] == "snapshot":
            db.conn.execute("UPDATE performer_gallery SET status='REJECTED',file_path='',updated_at=? WHERE id=?", (_now(), int(picture_id)))
            if str(row["provider_id"] or "").isdigit():
                db.conn.execute("INSERT OR REPLACE INTO performer_video_faces_done(performer_id,video_id,own_refs,added,updated_at) VALUES (?,?,-1,0,?)",
                                (int(performer_id), int(row["provider_id"]), _now()))
        else:
            db.conn.execute("UPDATE performer_gallery SET status='REJECTED',file_path='',embedding_json='[]',updated_at=? WHERE id=?", (_now(), int(picture_id)))
        db.conn.commit()
    if row["file_path"]:
        try:
            base = gallery_dir().resolve()
            target = (base / str(row["file_path"])).resolve()
            if base in target.parents:
                target.unlink(missing_ok=True)
        except OSError:
            _swallowed_note("performer_gallery.remove_picture")
    return True


def use_as_profile_picture(db, performer_id: int, picture_id: int) -> bool:
    """Make a kept picture the performer's profile picture, the same way a hand-uploaded one is stored: it is cached, marked manual and
    locked, so a later provider sync does not replace it. False when the picture is not there."""
    path = picture_file(db, performer_id, picture_id)
    if path is None:
        return False
    try:
        blob = path.read_bytes()
    except OSError:
        return False
    mime = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
    return use_bytes_as_profile(db, performer_id, blob, mime)


def use_bytes_as_profile(db, performer_id: int, blob: bytes, mime: str = "image/jpeg") -> bool:
    """Store picture bytes as the performer's manual, locked profile picture (a photo of theirs from the library, or a gallery picture)."""
    if not blob:
        return False
    token = "manual-upload:" + hashlib.sha1(blob).hexdigest()
    with db.lock:
        db.conn.execute("UPDATE performers SET image=?,image_source='manual',image_locked=1,image_crop_version=0 WHERE id=?", (token, int(performer_id)))
        db.conn.execute("INSERT OR REPLACE INTO performer_image_cache(performer_id,url,mime,data,updated_at) VALUES(?,?,?,?,?)", (int(performer_id), token, mime, blob, _now()))
        db.conn.commit()
    return True


def move_gallery(db, keep_id: int, merge_id: int) -> int:
    """When two performers are merged: the merged one's pictures join the survivor's gallery (a picture it already has is dropped)."""
    ensure_tables(db)
    keep_dir = gallery_dir() / str(int(keep_id))
    moved = 0
    with db.lock:
        rows = db.conn.execute("SELECT id,url_hash,sha256,file_path,status FROM performer_gallery WHERE performer_id=?", (int(merge_id),)).fetchall()
        have = {r["url_hash"] for r in db.conn.execute("SELECT url_hash FROM performer_gallery WHERE performer_id=?", (int(keep_id),)).fetchall()}
        have_sha = {r["sha256"] for r in db.conn.execute("SELECT sha256 FROM performer_gallery WHERE performer_id=? AND sha256<>'' AND status='ACTIVE'", (int(keep_id),)).fetchall()}
        for r in rows:
            if r["url_hash"] in have or (r["status"] == "ACTIVE" and r["sha256"] in have_sha):
                db.conn.execute("DELETE FROM performer_gallery WHERE id=?", (int(r["id"]),))
                _unlink(r["file_path"])
                continue
            new_path = r["file_path"]
            if r["file_path"]:
                try:
                    keep_dir.mkdir(parents=True, exist_ok=True)
                    source = gallery_dir() / str(r["file_path"])
                    target = keep_dir / Path(str(r["file_path"])).name
                    if source.is_file():
                        source.replace(target)
                    new_path = f"{int(keep_id)}/{target.name}"
                except OSError:
                    new_path = ""
            db.conn.execute("UPDATE performer_gallery SET performer_id=?,file_path=?,updated_at=? WHERE id=?", (int(keep_id), new_path, _now(), int(r["id"])))
            moved += 1
        db.conn.execute("DELETE FROM performer_gallery_state WHERE performer_id=?", (int(merge_id),))
    return moved


def _unlink(relative: str) -> None:
    if not relative:
        return
    try:
        base = gallery_dir().resolve()
        target = (base / str(relative)).resolve()
        if base in target.parents:
            target.unlink(missing_ok=True)
    except OSError:
        _swallowed_note("performer_gallery._unlink")


def delete_performer_gallery(db, performer_id: int) -> None:
    """When a performer is deleted: their pictures and state go with them."""
    ensure_tables(db)
    with db.lock:
        paths = [r["file_path"] for r in db.conn.execute("SELECT file_path FROM performer_gallery WHERE performer_id=?", (int(performer_id),)).fetchall()]
        db.conn.execute("DELETE FROM performer_gallery WHERE performer_id=?", (int(performer_id),))
        db.conn.execute("DELETE FROM performer_gallery_state WHERE performer_id=?", (int(performer_id),))
        db.conn.commit()
    for path in paths:
        _unlink(path)


# ---------------------------------------------------------------- faces --------------------------------------------------------------------------
def pick_reference_face(faces: list[dict]) -> tuple[int, list[float]]:
    """(face count, embedding to use) for one picture. A picture serves as a reference only when it shows one clear face: one face, or a
    largest face at least twice the area of the next, so a photo of two people cannot teach the matcher the wrong one."""
    if not faces:
        return 0, []
    if len(faces) == 1:
        return 1, list(faces[0]["embedding"])
    areas = sorted(((float(f["bbox"][2]) * float(f["bbox"][3]), f) for f in faces), key=lambda x: -x[0])
    if areas[0][0] >= 2.0 * max(areas[1][0], 1.0):
        return len(faces), list(areas[0][1]["embedding"])
    return len(faces), []


def index_faces(db, extract: Callable[[str], list[dict]], *, limit: int = 4000) -> int:
    """Read the face in every kept picture that has not been read. `extract(path)` returns the faces found (embedding + bbox)."""
    ensure_tables(db)
    with db.reading() as conn:
        rows = conn.execute("SELECT id,file_path FROM performer_gallery WHERE status='ACTIVE' AND file_path<>'' AND faces=-1 ORDER BY id LIMIT ?", (int(limit),)).fetchall()
    done = 0
    base = gallery_dir()
    for row in rows:
        path = base / str(row["file_path"])
        try:
            faces = extract(str(path)) if path.is_file() else None
        except Exception:
            faces = None
        if faces is None:
            continue                                     # unreadable right now: try again next time
        count, embedding = pick_reference_face(faces)
        with db.lock:
            db.conn.execute("UPDATE performer_gallery SET faces=?,embedding_json=?,updated_at=? WHERE id=?",
                            (count, json.dumps(embedding) if embedding else "[]", _now(), int(row["id"])))
            db.conn.commit()
        done += 1
    return done


# ---------------------------------------------------------------- the references used for matching -----------------------------------------------
def _decode(raw: Any):
    import numpy as np
    try:
        v = np.asarray(json.loads(raw or "[]"), dtype=np.float32)
    except Exception:
        return None
    if v.shape != (128,) or not np.isfinite(v).all():
        return None
    n = float(np.linalg.norm(v))
    return v / n if n else None


def _varied(vectors: list, count: int) -> list:
    """Up to `count` of the vectors that differ most from each other (farthest-point choice), so twelve near-copies are not kept."""
    import numpy as np
    if len(vectors) <= count:
        return vectors
    matrix = np.stack(vectors)
    chosen = [0]
    best = matrix @ matrix[0]
    while len(chosen) < count:
        nxt = int(np.argmin(best))
        chosen.append(nxt)
        best = np.maximum(best, matrix @ matrix[nxt])
    return [vectors[i] for i in chosen]


def reference_matrix(conn, wanted: Iterable[int] | None = None, *, confirmed: bool = True):
    """(performer id per row, matrix of normalised reference faces). One row per reference: the profile picture, each gallery face and a
    handful of varied faces confirmed for the performer (never a face taken out of them). A performer can appear on several rows."""
    import numpy as np
    wanted_set = {int(w) for w in wanted} if wanted is not None else None
    ids: list[int] = []
    vecs: list = []

    def add(pid: int, raw: Any) -> None:
        if wanted_set is not None and pid not in wanted_set:
            return
        v = _decode(raw)
        if v is not None:
            ids.append(pid)
            vecs.append(v)

    for r in conn.execute("SELECT performer_id,embedding_json FROM performer_face_embeddings WHERE embedding_json<>'[]'").fetchall():
        add(int(r["performer_id"]), r["embedding_json"])
    try:
        for r in conn.execute("SELECT performer_id,embedding_json FROM performer_gallery WHERE status='ACTIVE' AND embedding_json<>'[]'").fetchall():
            add(int(r["performer_id"]), r["embedding_json"])
    except Exception:
        _swallowed_note("performer_gallery.reference_matrix.gallery")      # no gallery table yet
    if confirmed:
        try:
            neg = {(int(r[0]), int(r[1])) for r in conn.execute("SELECT detection_id,performer_id FROM photo_face_negatives").fetchall()}
        except Exception:
            neg = set()
        try:
            rows = conn.execute(
                "WITH ranked AS (SELECT d.id AS did, g.performer_id AS pid, d.embedding_json AS emb, "
                "ROW_NUMBER() OVER (PARTITION BY g.performer_id ORDER BY d.id DESC) AS rn "
                "FROM photo_face_detections d JOIN photo_face_groups g ON g.id=d.group_id WHERE g.performer_id IS NOT NULL AND d.embedding_json<>'[]') "
                "SELECT did,pid,emb FROM ranked WHERE rn<=150").fetchall()
        except Exception:
            rows = []
        per: dict[int, list] = {}
        for r in rows:
            pid = int(r["pid"])
            if (wanted_set is not None and pid not in wanted_set) or (int(r["did"]), pid) in neg:
                continue
            v = _decode(r["emb"])
            if v is not None:
                per.setdefault(pid, []).append(v)
        for pid, group in per.items():
            for v in _varied(group, CONFIRMED_EXEMPLARS):
                ids.append(pid)
                vecs.append(v)
    return ids, (np.stack(vecs) if vecs else None)


def best_per_performer(ids: list[int], scores, *, top: int = 1) -> tuple[list[int], list[float]]:
    """Collapse one score per reference row into one score per performer: the best row (top=1) or the mean of their best `top` rows."""
    import numpy as np
    grouped: dict[int, list[float]] = {}
    for pid, score in zip(ids, np.asarray(scores, dtype=float).tolist()):
        grouped.setdefault(int(pid), []).append(float(score))
    order = list(grouped)
    out = []
    for pid in order:
        best = sorted(grouped[pid], reverse=True)[:max(1, int(top))]
        out.append(sum(best) / len(best))
    return order, out
