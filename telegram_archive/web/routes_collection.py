"""The Collection (Archive X's small Sonarr and Radarr): what to keep, its settings, and what is wanted, downloading or done. SFW only, like the add-ons it asks."""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Request

from ..core import content_mode as cm
from ..engines import collection as col
from ..engines import collection_worker as cw
from ..metadata import streaming_server as ss
from ..metadata import stremio_runtime as rt


def register_collection_routes(router: APIRouter, *, db, require_auth) -> None:
    def sfw_only() -> None:
        if cm.get_mode(db) != "sfw":
            raise HTTPException(409, "The Collection is available in SFW mode only.")

    @router.get("/api/collection")
    async def collection_state(request: Request):
        require_auth(request)
        sfw_only()

        def work():
            out = col.overview(db)
            base = ss.configured(db)
            # the server is probed only when something is kept: an empty Collection page must not wait on a program nobody asked for
            out["server"] = {"up": ss.reachable(base) if out["titles"] else True, "ready": rt.ready(), "installed": bool(ss.installed_program())}
            out["running"] = cw.worker_for(db).running
            return out
        return await asyncio.to_thread(work)

    @router.post("/api/collection/settings")
    async def collection_settings(request: Request):
        require_auth(request)
        sfw_only()
        body = await request.json()
        try:
            cfg = await asyncio.to_thread(col.save_settings, db, body if isinstance(body, dict) else {})
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc) or "Those settings are not valid.")
        if cfg["on"]:
            cw.start_for(db)
            cw.worker_for(db).kick()
        return {"ok": True, "settings": cfg}

    @router.post("/api/collection/titles")
    async def collection_add(request: Request):
        """Keep a movie or show: `kind` (movie or series), `imdb`, and for a show `scope` ("all" episodes or only "new" ones). Its name, year and poster come from the request or the catalogue."""
        require_auth(request)
        sfw_only()
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        kind, imdb = str(body.get("kind") or ""), str(body.get("imdb") or "")
        if kind not in ("movie", "series") or not re.fullmatch(r"tt\d{5,10}", imdb):
            raise HTTPException(400, "A movie or show with an IMDb number is required.")
        title, year, poster = str(body.get("title") or "").strip(), body.get("year"), str(body.get("poster") or "")
        if not title:
            meta = await asyncio.to_thread(lambda: cw.meta_of_factory(db)(kind, imdb))
            title = str(meta.get("name") or "").strip()
            poster = poster or str(meta.get("poster") or "")
            digits = re.match(r"\d{4}", str(meta.get("releaseInfo") or meta.get("year") or ""))
            year = int(digits.group(0)) if digits else None
        if not title:
            raise HTTPException(502, "The catalogue could not name that title. Try again in a moment.")
        out = await asyncio.to_thread(col.add_title, db, kind, imdb, title, int(year) if isinstance(year, int) or str(year or "").isdigit() else None, poster, str(body.get("scope") or "all"))
        if col.settings(db)["on"]:
            cw.worker_for(db).kick()
        return {"ok": True, **out}

    @router.delete("/api/collection/titles/{title_id}")
    async def collection_remove(title_id: int, request: Request):
        require_auth(request)
        sfw_only()
        if not await asyncio.to_thread(col.remove_title, db, title_id):
            raise HTTPException(404, "That title is not in the Collection.")
        return {"ok": True}

    def set_state(item_id: int, state: str, note: str, reset: bool) -> bool:
        col.ensure(db)
        with db.lock:
            failed = db.conn.execute("SELECT 1 FROM collection_items WHERE id=? AND state='FAILED' AND info_hash<>''", (int(item_id),)).fetchone()
            if failed and state == "WANTED":          # a download chosen by hand that failed: try that same release again
                db.conn.execute("UPDATE collection_items SET state='DOWNLOADING', note='', got=0, total=0 WHERE id=?", (int(item_id),))
                db.conn.commit()
                return True
            cur = db.conn.execute(
                "UPDATE collection_items SET state=?, note=?, next_at=''" + (", attempts=0, avoid='[]'" if reset else "") + " WHERE id=? AND state<>'DONE'", (state, note, int(item_id)))
            db.conn.commit()
            return bool(cur.rowcount)

    @router.post("/api/collection/items/{item_id}/{action}")
    async def collection_item(item_id: int, action: str, request: Request):
        """`retry` looks for this item again now (forgetting the releases that failed); `skip` stops looking for it."""
        require_auth(request)
        sfw_only()
        if action not in ("retry", "skip"):
            raise HTTPException(404, "Unknown action.")
        ok = await asyncio.to_thread(set_state, item_id, "WANTED" if action == "retry" else "SKIPPED", "" if action == "retry" else "Skipped", action == "retry")
        if not ok:
            raise HTTPException(404, "That item is finished or does not exist.")
        if action == "retry":
            cw.start_for(db)
            cw.worker_for(db).kick()
        return {"ok": True}

    @router.delete("/api/collection/items/{item_id}")
    async def collection_item_remove(item_id: int, request: Request):
        """Take one download out of the list (a running one is stopped and its partial file removed; a finished file stays in the library)."""
        require_auth(request)
        sfw_only()
        if not await asyncio.to_thread(col.delete_item, db, item_id):
            raise HTTPException(404, "That item does not exist.")
        return {"ok": True}

    @router.post("/api/collection/run")
    async def collection_run(request: Request):
        """Do a round now (it also runs by itself every ten minutes while automatic downloads are on)."""
        require_auth(request)
        sfw_only()
        if not col.settings(db)["on"]:
            raise HTTPException(409, "Turn on automatic downloads first.")
        cw.start_for(db)
        cw.worker_for(db).kick()
        return {"ok": True}
