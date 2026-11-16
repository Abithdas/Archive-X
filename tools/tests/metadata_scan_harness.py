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
