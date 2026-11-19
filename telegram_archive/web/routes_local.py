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


def register_local_routes(router: APIRouter, *, db, require_auth, config, jobs=None):
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

    def identify(channel: str) -> dict:
        """Name the folder's videos with the providers that fit its mark: movies and shows (TMDB) for SFW, scene databases (by file hash) for NSFW."""
        from ..engines import local_identify_adult, screen_identify
        with db.reading() as conn:
            row = conn.execute("SELECT COALESCE(content_rating,'nsfw') AS r FROM channels WHERE telegram_id=?", (channel,)).fetchone()
        if row and row["r"] == "sfw":
            return screen_identify.run(db, channel) if screen_identify.configured(db) else {"ok": False, "error": "Add a TMDB key (Settings → Metadata) to identify movies and shows."}
        return local_identify_adult.run(db, channel) if local_identify_adult.configured(db) else {"ok": False, "error": "Add a StashDB or ThePornDB key (Settings → Metadata) to identify these videos."}

    def start_scan(sid: int) -> bool:
        t = running.get(sid)
        if t and t.is_alive():
            return False

        def work():
            try:
                ls.scan_source(db, sid)
                from ..engines import local_videos
                local_videos.process(db, ls.channel_id(sid))   # probe the new videos and build their snapshots
                identify(ls.channel_id(sid))
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
                "FROM local_sources s LEFT JOIN channels c ON c.telegram_id=CAST(? - s.id AS TEXT) ORDER BY LOWER(COALESCE(c.title,s.path))", (ls.LOCAL_BASE,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["excluded"] = json.loads(d.get("excluded") or "[]")
            except ValueError:
                d["excluded"] = []
            d["channel_id"] = ls.channel_id(d["id"])
            d["scan"] = ls.progress(d["id"])
            from ..engines import local_videos
            d["video_work"] = local_videos.progress(ls.channel_id(d["id"]))
            d["exists"] = os.path.isdir(d["path"])
            out.append(d)
        return out

    @router.get("/api/local-folders")
    async def local_folders(request: Request):
        require_auth(request)
        mode = cm.get_mode(db)
        found = await asyncio.to_thread(listing)
        # Only the folders of the chosen mode are listed: their names and paths would say what they hold.
        hidden = [f for f in found if f["content_rating"] != mode]
        return {"ok": True, "mode": mode, "folders": [f for f in found if f["content_rating"] == mode], "hidden": len(hidden)}

    @router.get("/api/local-folders/settings")
    async def local_settings(request: Request):
        require_auth(request)
        from ..engines import local_videos
        return {"ok": True, "workers": await asyncio.to_thread(local_videos.workers_for, db)}

    @router.post("/api/local-folders/settings")
    async def local_settings_save(request: Request):
        """How many videos are prepared at once (1 to 4)."""
        require_auth(request)
        body = await request.json()
        try:
            n = int((body or {}).get("workers"))
        except (TypeError, ValueError):
            raise HTTPException(400, "workers must be a number from 1 to 4")
        if not 1 <= n <= 4:
            raise HTTPException(400, "workers must be a number from 1 to 4")
        await asyncio.to_thread(db.set_setting, "local_prepare_workers", n)
        return {"ok": True, "workers": n}

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

    @router.post("/api/local-folders/{source_id}/identify")
    async def identify_folder(source_id: int, request: Request):
        """Metadata scan of one folder: reads its videos' names and hashes and asks the providers that fit its mark. Runs in the background."""
        require_auth(request)
        ch = ls.channel_id(int(source_id))
        exists = await asyncio.to_thread(lambda: any(f["id"] == int(source_id) for f in listing()))
        if not exists:
            raise HTTPException(404, "No such folder")
        from ..engines import local_identify_adult, screen_identify
        with db.reading() as conn:
            row = conn.execute("SELECT COALESCE(content_rating,'nsfw') AS r FROM channels WHERE telegram_id=?", (ch,)).fetchone()
        sfw = bool(row and row["r"] == "sfw")
        if not (screen_identify.configured(db) if sfw else local_identify_adult.configured(db)):
            raise HTTPException(400, "Add a TMDB key first (Settings → Metadata)" if sfw else "Add a StashDB or ThePornDB key first (Settings → Metadata)")

        def work():
            try:
                identify(ch)
            except Exception as exc:
                db.log(f"Folder identification failed: {type(exc).__name__}: {exc}"[:300], "WARN")
        threading.Thread(target=work, name=f"local-identify-{source_id}", daemon=True).start()
        return {"ok": True, "kind": "movies and shows" if sfw else "scene databases"}

    @router.post("/api/play/make-snapshots/{channel_id}/{message_id}")
    async def make_snapshots(channel_id: int, message_id: int, request: Request):
        """Build the 36 snapshots of one video that lives in a folder on this PC (a review or a card with 'No cached snapshot' offers this)."""
        require_auth(request)
        from ..engines import local_videos
        if not ls.is_local_channel(channel_id):
            raise HTTPException(400, "Only videos from folders on this PC are made here. Telegram videos get theirs from Metadata & Assets, Snapshots.")
        path = await asyncio.to_thread(local_videos.file_of, db, channel_id, message_id)
        if not path or not os.path.isfile(path):
            raise HTTPException(404, "The file is not on this PC any more")

        def work() -> bool:
            with db.reading() as conn:
                row = conn.execute("SELECT duration FROM media WHERE source_channel=? AND source_message_id=?", (str(channel_id), int(message_id))).fetchone()
            duration = float((row["duration"] if row else 0) or 0)
            if duration <= 0:
                info = local_videos.probe(path)
                duration = float(info.get("duration") or 0) if info else 0.0
                if duration > 0:
                    with db.lock:
                        db.conn.execute("UPDATE media SET duration=? WHERE source_channel=? AND source_message_id=?", (duration, str(channel_id), int(message_id)))
                        db.conn.commit()
            ok = duration > 0 and local_videos.build_snapshots(db, str(channel_id), int(message_id), path, duration)
            with db.lock:
                db.conn.execute("UPDATE local_video_files SET snapshots=? WHERE channel_id=? AND message_id=?", (1 if ok else 2, str(channel_id), int(message_id)))
                db.conn.commit()
            return ok
        if not await asyncio.to_thread(work):
            raise HTTPException(502, "Snapshots could not be made from this file (ffmpeg could not read it)")
        return {"ok": True}

    @router.post("/api/local-folders/{source_id}/upload")
    async def upload_folder(source_id: int, request: Request):
        """Upload a folder's videos to a Telegram channel (`destination`), by the same upload job as Upload Local Files.

        Their snapshots are recorded where that job looks for them first, so it builds none, and what is known about each video is put on its copy
        in the channel afterwards. The files stay on this PC unless `keep_files` is false.
        """
        require_auth(request)
        if jobs is None:
            raise HTTPException(503, "Uploading is not available here")
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        destination = str(body.get("destination") or "").strip()
        keep_files = body.get("keep_files", True) is not False
        if not destination.lstrip("-").isdigit():
            raise HTTPException(400, "Choose the Telegram channel to upload to")
        ch = ls.channel_id(int(source_id))

        def prepare():
            with db.reading() as conn:
                src = conn.execute("SELECT path,recursive,excluded FROM local_sources WHERE id=?", (int(source_id),)).fetchone()
                mark = conn.execute("SELECT COALESCE(content_rating,'nsfw') AS r FROM channels WHERE telegram_id=?", (ch,)).fetchone()
                dest = conn.execute("SELECT title,role,COALESCE(content_rating,'nsfw') AS r FROM channels WHERE telegram_id=?", (destination,)).fetchone()
                rows = conn.execute("SELECT message_id FROM local_video_files WHERE channel_id=? AND snapshots=1 ORDER BY message_id", (ch,)).fetchall()
            if not src:
                raise HTTPException(404, "No such folder")
            if not dest or dest["role"] == "local":
                raise HTTPException(404, "That is not a Telegram channel this app knows")
            if dest["r"] != (mark["r"] if mark else "nsfw"):
                raise HTTPException(409, f"“{dest['title']}” is marked {dest['r'].upper()} and this folder is {(mark['r'] if mark else 'nsfw').upper()}. A channel holds one kind only: mark the channel in Settings → Telegram, or choose another.")
            try:
                if json.loads(src["excluded"] or "[]"):
                    raise HTTPException(409, "This folder leaves out some subfolders, and the upload cannot honour that yet. Remove the left-out subfolders from the list, or add the wanted subfolders as folders of their own.")
            except ValueError:
                pass
            from ..core.workspace_guard import validate_local_upload_folder
            try:
                validate_local_upload_folder(db, src["path"])
            except ValueError as exc:
                raise HTTPException(409, str(exc))
            if jobs.is_active("UPLOAD_LOCAL"):
                raise HTTPException(409, "An upload is already running: see Operations")
            from ..engines import local_upload_bridge as bridge
            tally: dict[str, int] = {"seeded": 0, "rebuilt": 0, "skipped": 0}
            why: list[str] = []
            for r in rows:
                out = bridge.seed(db, ch, int(r["message_id"]))
                key = out.split(":", 1)[0]
                tally[key] = tally.get(key, 0) + 1
                if key == "skipped" and len(why) < 5:
                    why.append(out)
            detail = json.dumps({"folder": src["path"], "include_subfolders": bool(src["recursive"]), "delete_local_after_delivery": not keep_files, "origin": "local_folder", "local_folder_id": int(source_id)})
            jid = db.create_job("UPLOAD_LOCAL", "", destination, False, detail=detail)
            jobs.submit(jid)
            return {"ok": True, "job_id": jid, "destination": dest["title"], "keep_files": keep_files, **tally, "why": why}
        return await asyncio.to_thread(prepare)

    @router.delete("/api/local-folders/{source_id}")
    async def remove_folder(source_id: int, request: Request):
        """Stop using a folder: its index goes, the files are never touched."""
        require_auth(request)
        ch = ls.channel_id(int(source_id))

        def write():
            from ..engines import local_videos
            with db.reading() as conn:
                gone = [int(r["message_id"]) for r in conn.execute("SELECT message_id FROM local_video_files WHERE channel_id=?", (ch,))]
            with db.lock:
                row = db.conn.execute("SELECT 1 FROM local_sources WHERE id=?", (int(source_id),)).fetchone()
                if not row:
                    raise HTTPException(404, "No such folder")
                for sql in ("DELETE FROM photo_library_items WHERE channel_id=?", "DELETE FROM photo_library_scan_state WHERE channel_id=?",
                            "DELETE FROM media WHERE source_channel=?", "DELETE FROM local_video_files WHERE channel_id=?",
                            "DELETE FROM timeline_preview_cache WHERE source_channel=?", "DELETE FROM channels WHERE telegram_id=?"):
                    db.conn.execute(sql, (ch,))
                db.conn.execute("DELETE FROM local_sources WHERE id=?", (int(source_id),))
                db.conn.commit()
            for mid in gone:      # their snapshots go with them (the frames are real disk space)
                local_videos.drop_snapshots(db, ch, mid)
        await asyncio.to_thread(write)
        await asyncio.to_thread(refresh_roots)
        db.log(f"Local folder removed: {ch}")
        return {"ok": True}
