"""Identify the videos of an NSFW local folder the way Telegram videos are: by the file's hash, asked of StashDB and ThePornDB.

A hash is an exact answer, so a match is applied at once (performers, studio, tags, title, date) with the same code as for Telegram videos.
Names are only clues: tags found in the file name are added, nothing else is guessed from it.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time

from ..core.swallowed import note as _swallowed_note
from ..metadata import providers
from ..metadata import screen_ids as si

_lock = threading.Lock()
_state: dict = {"running": False, "total": 0, "done": 0, "matched": 0, "unmatched": 0, "failed": 0, "channel": "", "started": 0.0, "finished": 0.0, "reason": ""}


def configured(db) -> bool:
    return bool(db.setting("stashdb_api_key") or db.setting("tpdb_api_token") or db.setting("tpdb_token"))


def pending(db, channel: str) -> list[dict]:
    with db.reading() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT m.id, m.source_message_id AS mid, m.filename, l.path FROM media m JOIN local_video_files l ON l.channel_id=m.source_channel AND l.message_id=m.source_message_id "
            "WHERE m.source_channel=? AND COALESCE(m.identification_state,'') NOT IN ('MATCHED','VERIFIED') ORDER BY m.id", (channel,))]


async def _lookup(db, file_hash: str) -> dict | None:
    stash = str(db.setting("stashdb_api_key") or "")
    tpdb = str(db.setting("tpdb_api_token") or db.setting("tpdb_token") or "")
    if stash:
        found = await providers.stashdb_by_oshash(db, stash, file_hash)
        if found:
            return found[0]
    if tpdb:
        found = await providers.tpdb_by_hash(db, tpdb, file_hash)
        if found:
            return found[0]
    return None


def run(db, channel: str) -> dict:
    """Look up every video of the folder that is not identified yet. One run at a time."""
    if not configured(db):
        return {"ok": False, "error": "Add a StashDB or ThePornDB key (Settings → Metadata) to identify these videos."}
    if not _lock.acquire(blocking=False):
        return dict(_state)
    try:
        todo = pending(db, channel)
        _state.update(running=True, total=len(todo), done=0, matched=0, unmatched=0, failed=0, channel=str(channel), started=time.time(), finished=0.0, reason="")
        for row in todo:
            try:
                path = row["path"] or ""
                file_hash = si.oshash(path) if path and os.path.isfile(path) else ""
                if not file_hash:                    # smaller than 128 KiB: the hash needs more than the file has
                    _state["unmatched"] += 1
                else:
                    with db.lock:
                        db.conn.execute("UPDATE media SET oshash=? WHERE id=?", (file_hash, int(row["id"])))
                        db.conn.commit()
                    best = asyncio.run(_lookup(db, file_hash))
                    if best and providers.apply_scene_match(db, int(channel), int(row["mid"]), best, oshash=file_hash):
                        _state["matched"] += 1
                    else:
                        _state["unmatched"] += 1
            except Exception as exc:
                _swallowed_note("local_identify_adult.run")
                from ..core.net_errors import explain
                if not _state["reason"]:
                    _state["reason"] = explain(exc, "StashDB / ThePornDB") or f"{type(exc).__name__}: {exc}"[:160]
                    db.log(f"Identify failed for {row.get('filename')}: {_state['reason']}"[:300], "WARN")
                _state["failed"] += 1
            _state["done"] += 1
        return {"ok": True, **_state}
    finally:
        _state.update(running=False, finished=time.time())
        _lock.release()


def progress() -> dict:
    return dict(_state)
