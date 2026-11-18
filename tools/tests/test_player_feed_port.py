"""The player feed (for VLC, MPC-HC and ffmpeg helpers) moves to the next free port when its own is busy, instead of crashing."""
import ast
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axpf_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from telegram_archive.playback import stream_server as ss  # noqa: E402


def test_every_call_to_the_stream_log_has_a_message_and_at_most_a_level():
    """It once was called with a second argument that the function did not take, which crashed the feed whenever its port was busy."""
    bad = []
    for path in (ROOT / "telegram_archive").rglob("*.py"):
        for n in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_stream_log" and not (len(n.args) in (1, 2) and not n.keywords):
                bad.append(f"{path.name}:{n.lineno}")
    assert not bad


def test_a_busy_port_is_skipped_and_the_chosen_one_is_recorded():
    if not ss.AIOHTTP_AVAILABLE:
        pytest.skip("aiohttp is not installed")
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    state: dict = {}

    class Tokens:
        def check(self, *a):
            return False

    async def run():
        ss._FEED_RUNNING = False
        task = asyncio.create_task(ss.serve_local_player_feed(state, Tokens(), port, "127.0.0.1"))
        for _ in range(100):
            if state.get("player_port"):
                break
            if task.done():
                task.result()
            await asyncio.sleep(0.05)
        chosen = state.get("player_port")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return chosen
    try:
        chosen = asyncio.run(run())
    finally:
        busy.close()
        ss._FEED_RUNNING = False
    assert chosen and chosen != port and port < chosen <= port + 20
