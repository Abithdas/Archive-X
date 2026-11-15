"""The channel inventory writes thousands of rows. It must do that on a worker thread: on the event loop it froze the whole app for minutes
(a 202 s stall in a user's log: event-loop-stalls.log, telegram_registry.register_known_message)."""
import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axinv_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def test_the_inventory_rows_are_written_off_the_event_loop(monkeypatch):
    from telegram_archive.core.database import Database
    from telegram_archive.engines import metadata_channel_pipeline as pipe
    from telegram_archive.core import telegram_registry
    from telegram_archive.playback import stream_server

    db = Database()
    records = {str(i): {"id": i, "name": f"video {i}.mp4", "size": 100 + i, "duration": 5.0} for i in range(1, 6)}
    monkeypatch.setattr(stream_server, "build_channel_index", lambda *a, **k: _async_value(records))
    seen = []
    real = telegram_registry.register_known_message
    monkeypatch.setattr(telegram_registry, "register_known_message", lambda *a, **k: (seen.append(threading.current_thread()), real(*a, **k))[1])

    async def cancellable(factory, **_):
        return await factory()

    async def no_wait(_ctx):
        return None

    monkeypatch.setattr(pipe, "_scan_window_wait", no_wait)
    ctx = SimpleNamespace(
        db=db, cancellable=cancellable,
        telegram=SimpleNamespace(client=SimpleNamespace(get_entity=lambda cid: _async_value(object()))),
        governor=SimpleNamespace(channel_download_floodwaits=lambda cid: 0),
    )

    async def run():
        loop_thread = threading.current_thread()
        result = await pipe._discover_and_store_inventory(ctx, "-1001", 0, full_scan=True)
        return loop_thread, result

    loop_thread, result = asyncio.run(run())
    count = db.conn.execute("SELECT COUNT(*) FROM media WHERE source_channel='-1001'").fetchone()[0]
    assert count == 5
    assert len(result) == 5
    assert len(seen) == 5 and all(t is not loop_thread for t in seen)


async def _async_value(value):
    return value


def test_a_range_source_can_be_closed():
    from telegram_archive.core.telegram_range_transport import DirectTelegramRangeSource
    source = DirectTelegramRangeSource(client=object(), message=object(), loop=asyncio.new_event_loop())
    source._cache[0] = b"x"
    source.close()
    assert source._cache == {}
