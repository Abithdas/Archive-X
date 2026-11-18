"""Local folders as sources: add, mark, scan and remove them, and browse the PC's folders to choose one."""
from __future__ import annotations

import asyncio
import json
import os
import string
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..core import content_mode as cm
from ..engines import local_scanner as ls
from ..engines import photo_cache

HOLDS = ("videos", "photos", "both")
_WATCHER = False


def register_local_routes(router: APIRouter, *, db, require_auth, config):
    def roots() -> list[str]:
        with db.reading() as conn:
            return [r["path"] for r in conn.execute("SELECT path FROM local_sources")]

    def refresh_roots() -> None:
        photo_cache.set_local_roots(roots())

    refresh_roots()   # folders added before this start are readable at once
    running: dict[int, threading.Thread] = {}

    def keep_in_step() -> None:
        """Every 15 minutes (and shortly after start) look at each folder again: cheap, since unchanged files are skipped."""
        import time
        time.sleep(30)
        while True:
            try:
                for f in listing():
                    start_scan(f["id"])
            except Exception as exc:
                db.log(f"Local folder re-check failed: {type(exc).__name__}: {exc}", "WARN")
            time.sleep(900)
    global _WATCHER
    if not _WATCHER and os.environ.get("ARCHIVE_X_LOCAL_RESCAN", "1") != "0":
        _WATCHER = True
        threading.Thread(target=keep_in_step, name="local-folders-rescan", daemon=True).start()

    def start_scan(sid: int) -> bool:
        t = running.get(sid)
        if t and t.is_alive():
            return False

        def work():
            try:
                ls.scan_source(db, sid)
            except Exception as exc:   # the failure is stored on the source and shown in Settings
                db.log(f"Local folder scan failed: {type(exc).__name__}: {exc}", "WARN")
        t = threading.Thread(target=work, name=f"local-scan-{sid}", daemon=True)
        running[sid] = t
        t.start()
        return True

    def listing() -> list[dict]:
        with db.reading() as conn:
            rows = conn.execute(
                "SELECT s.*, c.title, COALESCE(c.in_library,1) AS in_library, COALESCE(c.content_rating,'nsfw') AS content_rating, COALESCE(c.holds,'both') AS holds "
                "FROM local_sources s LEFT JOIN channels c ON c.telegram_id='local:'||s.id ORDER BY LOWER(COALESCE(c.title,s.path))").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["excluded"] = json.loads(d.get("excluded") or "[]")
            except ValueError:
                d["excluded"] = []
            d["channel_id"] = ls.channel_id(d["id"])
            d["scan"] = ls.progress(d["id"])
            d["exists"] = os.path.isdir(d["path"])
            out.append(d)
        return out

    @router.get("/api/local-folders")
    async def local_folders(request: Request):
        require_auth(request)
        return {"ok": True, "mode": cm.get_mode(db), "folders": await asyncio.to_thread(listing)}

    @router.get("/api/local-folders/browse")
    async def browse(request: Request, path: str = ""):
        """The folders inside `path` (the drives when empty), to choose a folder without typing its path."""
        require_auth(request)

        def work():
            if not path.strip():
                if os.name == "nt":
                    return {"path": "", "parent": None, "folders": [{"name": f"{d}:\\", "path": f"{d}:\\"} for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]}
                home = str(Path.home())
                return {"path": "", "parent": None, "folders": [{"name": "Home", "path": home}, {"name": "/", "path": os.sep}]}
            p = os.path.realpath(os.path.expanduser(path))
            if not os.path.isdir(p):
                raise HTTPException(404, "That folder does not exist")
            parent = os.path.dirname(p.rstrip("\\/")) or None
            kids = []
            try:
                for e in sorted(os.scandir(p), key=lambda x: x.name.lower()):
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "$")):
                        kids.append({"name": e.name, "path": e.path})
            except OSError as exc:
                raise HTTPException(403, "That folder cannot be opened") from exc
            return {"path": p, "parent": parent if parent != p else "", "folders": kids[:2000]}
        return await asyncio.to_thread(work)

    @router.post("/api/local-folders")
    async def add_folder(request: Request):
        """Add a folder: `path`, `content_rating` (sfw / nsfw), optional `holds`, `recursive`. It is scanned at once."""
        require_auth(request)
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        rating = str(body.get("content_rating") or cm.get_mode(db)).lower()
        holds = str(body.get("holds") or "both").lower()
        if rating not in cm.MODES or holds not in HOLDS:
            raise HTTPException(400, "content_rating must be sfw or nsfw, and holds videos, photos or both")
        raw = str(body.get("path") or "").strip()
        path = os.path.realpath(os.path.expanduser(raw)) if raw else ""
        if not path or not os.path.isdir(path):
            raise HTTPException(400, "Choose a folder that exists on this PC")
        data_dir = os.path.realpath(str(config.USER_DATA_DIR))
        if path == data_dir or path.startswith(data_dir + os.sep) or data_dir.startswith(path + os.sep):
            raise HTTPException(400, "That folder holds Archive X's own data; choose another")
        for other in await asyncio.to_thread(roots):
            o = os.path.realpath(other)
            if path == o or path.startswith(o + os.sep) or o.startswith(path + os.sep):
                raise HTTPException(409, f"This folder overlaps one already added: {other}")

        def write():
            now = config.now_iso()
            with db.lock:
                cur = db.conn.execute("INSERT INTO local_sources(path,recursive,created_at) VALUES(?,?,?)", (path, 1 if body.get("recursive", True) else 0, now))
                sid = int(cur.lastrowid)
                db.conn.execute(
                    "INSERT INTO channels(telegram_id,title,username,role,in_library,content_rating,holds,updated_at) VALUES(?,?,?,?,1,?,?,?)",
                    (ls.channel_id(sid), os.path.basename(path.rstrip("\\/")) or path, None, "local", rating, holds, now))
                db.conn.commit()
            return sid
        sid = await asyncio.to_thread(write)
        await asyncio.to_thread(refresh_roots)
        start_scan(sid)
        db.log(f"Local folder added: {path} ({rating.upper()})")
        return {"ok": True, "id": sid, "channel_id": ls.channel_id(sid)}

    @router.post("/api/local-folders/{source_id}")
    async def edit_folder(source_id: int, request: Request):
        """Change what a folder does: `excluded` (subfolders left out, as paths inside it), `recursive`, and `title`."""
        require_auth(request)
        body = await request.json()
        body = body if isinstance(body, dict) else {}

        def write():
            with db.lock:
                row = db.conn.execute("SELECT path FROM local_sources WHERE id=?", (int(source_id),)).fetchone()
                if not row:
                    raise HTTPException(404, "No such folder")
                base = os.path.realpath(row["path"])
                if "excluded" in body:
                    ex = []
                    for e in body["excluded"] or []:
                        p = os.path.realpath(str(e))
                        if p.startswith(base + os.sep):
                            ex.append(os.path.relpath(p, base))
                    db.conn.execute("UPDATE local_sources SET excluded=? WHERE id=?", (json.dumps(sorted(set(ex))), int(source_id)))
                if "recursive" in body:
                    db.conn.execute("UPDATE local_sources SET recursive=? WHERE id=?", (1 if body["recursive"] else 0, int(source_id)))
                if str(body.get("title") or "").strip():
                    db.conn.execute("UPDATE channels SET title=?,title_locked=1,updated_at=? WHERE telegram_id=?", (str(body["title"]).strip()[:120], config.now_iso(), ls.channel_id(source_id)))
                db.conn.commit()
        await asyncio.to_thread(write)
        start_scan(int(source_id))
        return {"ok": True}

    @router.post("/api/local-folders/{source_id}/scan")
    async def rescan(source_id: int, request: Request):
        require_auth(request)
        exists = await asyncio.to_thread(lambda: any(f["id"] == int(source_id) for f in listing()))
        if not exists:
            raise HTTPException(404, "No such folder")
        return {"ok": True, "started": start_scan(int(source_id))}

    @router.delete("/api/local-folders/{source_id}")
    async def remove_folder(source_id: int, request: Request):
        """Stop using a folder: its index goes, the files are never touched."""
        require_auth(request)
        ch = ls.channel_id(int(source_id))

        def write():
            with db.lock:
                row = db.conn.execute("SELECT 1 FROM local_sources WHERE id=?", (int(source_id),)).fetchone()
                if not row:
                    raise HTTPException(404, "No such folder")
                for sql in ("DELETE FROM photo_library_items WHERE channel_id=?", "DELETE FROM photo_library_scan_state WHERE channel_id=?",
                            "DELETE FROM channels WHERE telegram_id=?"):
                    db.conn.execute(sql, (ch,))
                db.conn.execute("DELETE FROM local_sources WHERE id=?", (int(source_id),))
                db.conn.commit()
        await asyncio.to_thread(write)
        await asyncio.to_thread(refresh_roots)
        db.log(f"Local folder removed: {ch}")
        return {"ok": True}
