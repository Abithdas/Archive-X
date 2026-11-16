"""Shared fakes for the metadata channel scan tests: a real Database, a fake job context and a fake Telegram history walk."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

root = str(Path(__file__).resolve().parents[2])
os.environ.setdefault("ARCHIVE_X_DATA_DIR", tempfile.mkdtemp(prefix="axmeta_"))
os.environ.setdefault("ARCHIVE_X_AUTO_OPEN_BROWSER", "0")
os.environ.setdefault("TELEGRAM_ARCHIVE_ALLOW_REMOTE_SETUP", "1")
sys.path.insert(0, root)

from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.engines import metadata_channel_pipeline as pipe  # noqa: E402
from telegram_archive.jobs.context import JobCancelled  # noqa: E402
from telegram_archive.playback import stream_server  # noqa: E402


def video(i, **extra):
    return {"id": i, "name": f"clip {i}.mp4", "size": 1000 + i, "duration": 60.0 + i, "width": 1920, "height": 1080, **extra}


class FakeHistory:
    """What the Telegram history walk returns. `stats` is what build_channel_index reports through its progress callback."""

    def __init__(self, records=None, *, complete=True, limit_hit=False, raises=None):
        self.records = records if records is not None else {}
        self.complete, self.limit_hit, self.raises = complete, limit_hit, raises
        self.calls = []
        self.checkpoints = []

    async def __call__(self, client, channel, progress=None, min_id=0, max_videos=None, *, initial_records=None,
                       checkpoint=None, checkpoint_callback=None, **_):
        self.calls.append({"min_id": min_id, "max_videos": max_videos, "checkpoint": checkpoint})
        if self.raises:
            exc = self.raises.pop(0) if isinstance(self.raises, list) else self.raises
            if exc is not None:
                raise exc
        out = {str(k): dict(v) for k, v in (initial_records or {}).items()}
        out.update({str(k): dict(v) for k, v in self.records.items() if int(k) > int(min_id or 0)})
        if checkpoint_callback is not None:
            await checkpoint_callback({"version": 2, "base_min_id": min_id})
        if progress:
            progress({"messages": len(out), "videos": len(out), "enumeration_complete": self.complete, "complete": self.complete,
                      "full_history": bool(self.complete and not min_id), "limit_hit": self.limit_hit,
                      "phase": "complete" if self.complete else "partial"})
        return out


def make_ctx(db, detail=None, *, cancel_after=None):
    """A job context with a real job row. `cancel_after` = number of checkpoint() calls before JobCancelled."""
    detail = detail if detail is not None else {}
    jid = db.create_job("METADATA_CHANNEL_PIPELINE", "", "", False, detail=json.dumps(detail))
    state = {"checks": 0}
    reports = []

    async def checkpoint():
        state["checks"] += 1
        if cancel_after is not None and state["checks"] > cancel_after:
            raise JobCancelled()

    async def cancellable(factory, **_):
        return await factory()

    ctx = SimpleNamespace(
        db=db, job=db.get_job(jid), job_id=jid, checkpoint=checkpoint, cancellable=cancellable,
        report=lambda *a, **k: reports.append(k), reports=reports,
        telegram=SimpleNamespace(client=SimpleNamespace(get_entity=lambda cid: _value(object()))),
        governor=SimpleNamespace(channel_download_floodwaits=lambda cid: 0, note_channel_scan_floodwait=lambda cid: None),
        scan_window_enforced=True, cancel_requested=lambda: False,
    )
    return ctx


async def _value(v):
    return v


async def _no_wait(_ctx):
    return None


def install(monkeypatch, history):
    """Route the history walk to `history` and switch off the scan-window wait."""
    monkeypatch.setattr(stream_server, "build_channel_index", history)
    monkeypatch.setattr(pipe, "_scan_window_wait", _no_wait)


def count(db, channel, where=""):
    return db.conn.execute(f"SELECT COUNT(*) FROM media WHERE source_channel=? {where}", (channel,)).fetchone()[0]


_DB = None
_CH = [1000]


def new_channel():
    """A channel id no other test has used (all tests share one database file)."""
    _CH[0] += 1
    return f"-{_CH[0]}"


def fresh_db():
    global _DB
    if _DB is None:
        _DB = Database()
    return _DB


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- the three stage engines, faked ----------------------------------------------
class Engines:
    """Stands in for pipe._run_engine. Like the real engines it writes the database states the final check reads:
    metadata -> metadata_state COMPLETE; thumbnails -> snapshot_state READY (+ a card image); entity_data -> nothing (the final step
    fills in Unknown placeholders). `fail` maps an operation key to an exception; `slow` makes snapshots take that many seconds."""

    def __init__(self, db, *, fail=None, slow=0.0, link_entities=True):
        self.db, self.fail, self.slow, self.link_entities = db, dict(fail or {}), slow, link_entities
        self.calls, self.cancelled, self.finished = [], [], []

    @staticmethod
    def key(operation, detail):
        return "performers" if operation == "entity_data" and detail.get("scope") == "performers" else \
               "studios" if operation == "entity_data" else operation

    def _media_ids(self, channel):
        return [r[0] for r in self.db.conn.execute("SELECT id FROM media WHERE source_channel=? AND COALESCE(status,'')<>'SOURCE_DELETED'", (channel,))]

    async def __call__(self, ctx, operation, detail, *, report_override=None):
        key = self.key(operation, detail)
        self.calls.append((key, detail))
        try:
            if self.slow and key == "thumbnails":
                await asyncio.sleep(self.slow)
            else:
                await asyncio.sleep(0)
            exc = self.fail.get(key)
            if exc is not None:
                raise exc
            channels = [str(c) for c in (detail.get("source_channels") or detail.get("channels") or [])]
            with self.db.lock:
                if key == "metadata":
                    for ch in channels:
                        ids = self._media_ids(ch)
                        for mid in ids:
                            self.db.conn.execute("UPDATE media SET metadata_synced_at=datetime('now') WHERE id=?", (mid,))
                            self.db.conn.execute("INSERT INTO resource_state(media_id,metadata_state,updated_at) VALUES(?, 'COMPLETE', datetime('now')) "
                                                 "ON CONFLICT(media_id) DO UPDATE SET metadata_state='COMPLETE'", (mid,))
                        if self.link_entities and ids:
                            self.db.conn.execute("INSERT OR IGNORE INTO performers(name) VALUES(?)", (f"Fake Performer {ch}",))
                            self.db.conn.execute("INSERT OR IGNORE INTO studios(name) VALUES(?)", (f"Fake Studio {ch}",))
                            p = self.db.conn.execute("SELECT id FROM performers WHERE name=?", (f"Fake Performer {ch}",)).fetchone()[0]
                            s = self.db.conn.execute("SELECT id FROM studios WHERE name=?", (f"Fake Studio {ch}",)).fetchone()[0]
                            for mid in ids:
                                self.db.conn.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES(?,?)", (mid, p))
                                self.db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,1.0)", (mid, s))
                elif key == "thumbnails":
                    for ch in channels:
                        for mid, msg in self.db.conn.execute("SELECT id, source_message_id FROM media WHERE source_channel=? AND COALESCE(status,'')<>'SOURCE_DELETED'", (ch,)).fetchall():
                            self.db.conn.execute("INSERT INTO resource_state(media_id,snapshot_state,updated_at) VALUES(?, 'READY', datetime('now')) "
                                                 "ON CONFLICT(media_id) DO UPDATE SET snapshot_state='READY'", (mid,))
                            # what the real snapshot engine leaves behind, and the Library view asks for
                            self.db.conn.execute("DELETE FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=?", (ch, msg))
                            self.db.conn.execute("INSERT INTO timeline_preview_cache(source_channel,video_message_id,path,status,frame_count,version,updated_at) "
                                                 "VALUES(?,?,?,?,36,7,datetime('now'))", (ch, msg, f"/x/{msg}.jpg", "READY"))
                            self.db.conn.execute("DELETE FROM thumbnail_cache WHERE source_channel=? AND video_message_id=?", (ch, msg))
                            self.db.conn.execute("INSERT INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) "
                                                 "VALUES(?,?,'image/jpeg',x'ffd8',?,datetime('now'))", (ch, msg, "playback-snapshot"))
                            self.db.conn.execute("DELETE FROM asset_registry WHERE media_id=? AND asset_type='PLAYBACK_REPRESENTATIVE'", (mid,))
                            self.db.conn.execute("INSERT INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,state,content_revision,created_at,updated_at) "
                                                 "VALUES(?,?,?,?,?,'READY',1,datetime('now'),datetime('now'))", (mid, "telegram_video", f"{ch}:{msg}", "PLAYBACK_REPRESENTATIVE", f"/x/{msg}.jpg"))
                self.db.conn.commit()
            self.finished.append(key)
        except asyncio.CancelledError:
            self.cancelled.append(key)
            raise

    def keys(self):
        return [k for k, _ in self.calls]


def job(db, monkeypatch, *, stages=None, channels, history=None, engines=None, detail=None, cancel_after=None):
    """Run the whole Metadata & Assets job body once; return (ctx, saved job detail)."""
    body = {"channel_ids": list(channels), "limit": 0, "unbounded": True}
    if stages is not None:
        body["stages"] = stages
    body.update(detail or {})
    ctx = make_ctx(db, body, cancel_after=cancel_after)
    install(monkeypatch, history or FakeHistory())
    monkeypatch.setattr(pipe, "_run_engine", engines)
    monkeypatch.setattr(pipe, "_start_entity_repair", lambda *a, **k: None)
    ctx.leftover = []

    async def go():
        try:
            await pipe._run_metadata_assets(ctx)
        finally:
            await asyncio.sleep(0.05)      # let cancelled tasks finish before looking for strays
            ctx.leftover = [t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]

    try:
        run(go())
    finally:
        ctx.saved = json.loads(db.get_job(ctx.job_id)["detail"])
    return ctx, ctx.saved


def phases(payload, index=0):
    return payload["channel_queue"][index]["phases"]
