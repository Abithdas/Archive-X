"""The SFW / NSFW content switch, its PIN, and the marks on Telegram channels."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ..core import content_mode as cm

RATINGS = cm.MODES
HOLDS = ("videos", "photos", "both")


def register_content_routes(router: APIRouter, *, db, require_auth):
    @router.get("/api/content-mode")
    async def content_mode_state(request: Request):
        require_auth(request)
        return {"ok": True, "mode": cm.get_mode(db), "pin_set": cm.pin_is_set(db), "locked_for": round(cm.locked_out())}

    @router.post("/api/content-mode")
    async def content_mode_set(request: Request):
        """Switch the mode. Switching into NSFW needs the PIN; the first time (no PIN yet) it also sets one (`new_pin`)."""
        require_auth(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Send an object with a mode")
        mode = str(body.get("mode") or "").strip().lower()
        if mode not in cm.MODES:
            raise HTTPException(400, "The mode must be sfw or nsfw")
        if mode == "nsfw" and cm.get_mode(db) != "nsfw":
            wait = cm.locked_out()
            if wait:
                raise HTTPException(429, f"Too many wrong PINs. Try again in {int(wait) + 1} seconds.")
            if cm.pin_is_set(db):
                if not await asyncio.to_thread(cm.verify_pin, db, str(body.get("pin") or "")):
                    raise HTTPException(403, "Wrong PIN")
            elif body.get("new_pin"):   # the PIN is optional: one can be chosen now, but none is needed
                try:
                    await asyncio.to_thread(cm.set_pin, db, str(body.get("new_pin")))
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
        cm.set_mode(db, mode)
        db.log(f"Content mode set to {mode.upper()}")
        return {"ok": True, "mode": mode, "pin_set": cm.pin_is_set(db)}

    @router.post("/api/content-mode/pin")
    async def content_mode_pin(request: Request):
        """Change the PIN (the current one is needed when there is one)."""
        require_auth(request)
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        wait = cm.locked_out()
        if wait:
            raise HTTPException(429, f"Too many wrong PINs. Try again in {int(wait) + 1} seconds.")
        if cm.pin_is_set(db) and not await asyncio.to_thread(cm.verify_pin, db, str(body.get("current") or "")):
            raise HTTPException(403, "Wrong PIN")
        if body.get("remove") is True:   # back to no PIN at all
            await asyncio.to_thread(cm.clear_pin, db)
            db.log("NSFW PIN removed")
            return {"ok": True, "pin_set": False}
        try:
            await asyncio.to_thread(cm.set_pin, db, str(body.get("new") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        db.log("NSFW PIN changed")
        return {"ok": True, "pin_set": True}

    @router.get("/api/telegram/channel-marks")
    async def channel_marks(request: Request):
        """Every channel the account knows, with its library and content marks (unfiltered: this is where the marks are made)."""
        require_auth(request)

        def rows():
            with db.reading() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT c.telegram_id, c.title, c.username, COALESCE(c.in_library,1) AS in_library, COALESCE(c.content_rating,'nsfw') AS content_rating, "
                    "COALESCE(c.holds,'both') AS holds, CASE WHEN c.role='local' THEN 'local' ELSE 'telegram' END AS kind, "
                    "(SELECT COUNT(*) FROM media m WHERE m.source_channel=c.telegram_id) AS videos, "
                    "(SELECT COUNT(*) FROM photo_library_items p WHERE p.channel_id=c.telegram_id) AS photos "
                    "FROM channels c ORDER BY LOWER(c.title)")]
        mode = cm.get_mode(db)
        found = await asyncio.to_thread(rows)
        for r in found:
            r["unnamed"] = str(r["title"] or "").strip().lower() in ("", "unknown channel", "unknown")
        # In SFW the names of channels marked NSFW are not shown here either (the names alone would say what they are), in the library or not: a channel
        # that is not sorted yet counts as NSFW. They are managed from NSFW mode.
        hidden = [r for r in found if mode == "sfw" and r["content_rating"] == "nsfw"]
        shown = [r for r in found if r not in hidden] if hidden else found
        return {"ok": True, "mode": mode, "channels": shown, "hidden": len(hidden)}

    @router.post("/api/telegram/channel-marks")
    async def channel_marks_set(request: Request):
        """Mark one or more channels: `ids` plus any of `in_library` (bool), `content_rating` (sfw / nsfw), `holds` (videos / photos / both)."""
        require_auth(request)
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        ids = [str(i) for i in (body.get("ids") or []) if str(i).strip()]
        if not ids or len(ids) > 2000:
            raise HTTPException(400, "Choose between 1 and 2000 channels")
        sets, args = [], []
        if "in_library" in body:
            sets.append("in_library=?"); args.append(1 if body["in_library"] else 0)
        if "content_rating" in body:
            rating = str(body["content_rating"]).lower()
            if rating not in RATINGS:
                raise HTTPException(400, "content_rating must be sfw or nsfw")
            sets.append("content_rating=?"); args.append(rating)
        if "holds" in body:
            holds = str(body["holds"]).lower()
            if holds not in HOLDS:
                raise HTTPException(400, "holds must be videos, photos or both")
            sets.append("holds=?"); args.append(holds)
        if not sets:
            raise HTTPException(400, "Nothing to change")

        def write():
            marks = ",".join("?" for _ in ids)
            with db.lock:
                cur = db.conn.execute(f"UPDATE channels SET {','.join(sets)} WHERE telegram_id IN ({marks})", [*args, *ids])
                db.conn.commit()
                return cur.rowcount
        changed = await asyncio.to_thread(write)
        db.log(f"Channel marks changed for {changed} channel(s): " + ", ".join(sets))
        return {"ok": True, "changed": changed}
