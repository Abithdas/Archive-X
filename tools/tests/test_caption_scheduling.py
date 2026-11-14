"""Caption windows are served in the viewer's order: their own window first, a window they left is dropped or abandoned midway, and a
request that goes away never leaves the service stuck. (The older tests in test_media_tracks.py cover caching and single flight.)"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.playback import captions


class Engine:
    """Takes `work` seconds per window in 50 ms steps and checks `cancelled` between steps, like faster-whisper between segments."""
    name = "fake"

    def __init__(self, work=0.3):
        self.work, self.started, self.finished = work, [], []
        self._current = 0

    def transcribe_wav(self, wav, task="translate", cancelled=None):
        start = float(wav.decode())
        self.started.append(start)
        for _ in range(int(self.work / 0.05)):
            if cancelled is not None and cancelled():
                raise captions.TranscribeCancelled()
            time.sleep(0.05)
        self.finished.append(start)
        return {"language": "en", "cues": [{"start": 0.0, "end": 1.0, "text": f"w{int(start)}"}]}


async def wav(s, d):
    return str(s).encode()


def run(coro):
    return asyncio.run(coro)


def test_the_viewers_own_window_goes_before_the_lookahead(tmp_path):
    eng = Engine()
    svc = captions.CaptionService(tmp_path, eng)

    async def go():
        busy = asyncio.create_task(svc.window("other", 500, None, wav, at=500))        # another video keeps the engine busy; nobody leaves it
        await asyncio.sleep(0.05)
        ahead = asyncio.create_task(svc.window("k", 150, None, wav, at=120))           # the lookahead is asked first ...
        await asyncio.sleep(0.02)
        now = asyncio.create_task(svc.window("k", 120, None, wav, at=120))             # ... then the window the viewer is in
        return await asyncio.gather(busy, ahead, now)
    busy, ahead, now = run(go())
    assert eng.started == [480.0, 120.0, 150.0]                                         # 120 before 150 although 150 asked first
    assert now["start"] == 120.0 and ahead["start"] == 150.0


def test_a_running_window_is_abandoned_when_the_viewer_seeks_away(tmp_path):
    eng = Engine(work=2.0)
    svc = captions.CaptionService(tmp_path, eng)

    async def go():
        t0 = time.monotonic()
        first = asyncio.create_task(svc.window("k", 0, None, wav, at=0))
        await asyncio.sleep(0.1)
        second = asyncio.create_task(svc.window("k", 900, None, wav, at=900))
        r1 = await asyncio.gather(first, return_exceptions=True)
        stopped_after = time.monotonic() - t0
        r2 = await second
        return r1[0], stopped_after, r2
    r1, stopped_after, r2 = run(go())
    assert isinstance(r1, captions.CaptionsStale)
    assert stopped_after < 1.0                                                          # not after its full two seconds
    assert r2["start"] == 900.0
    assert not (tmp_path / "captions" / "k" / "en_a_0.json").exists()                  # a half-finished window is never cached


def test_a_waiting_window_behind_the_viewer_is_dropped(tmp_path):
    eng = Engine()
    svc = captions.CaptionService(tmp_path, eng)

    async def go():
        busy = asyncio.create_task(svc.window("other", 900, None, wav, at=900))        # another video keeps the engine busy; nobody leaves it
        await asyncio.sleep(0.05)
        behind = asyncio.create_task(svc.window("k", 30, None, wav, at=30))
        await asyncio.sleep(0.02)
        here = asyncio.create_task(svc.window("k", 90, None, wav, at=90))               # the viewer skips ahead past it before its turn
        return await asyncio.gather(busy, behind, here, return_exceptions=True)
    busy, behind, here = run(go())
    assert isinstance(behind, captions.CaptionsStale)
    assert isinstance(here, dict) and here["start"] == 90.0
    assert 30.0 not in eng.started


def test_the_next_window_is_kept_while_the_viewer_is_in_this_one(tmp_path):
    eng = Engine(work=0.1)
    svc = captions.CaptionService(tmp_path, eng)

    async def go():
        a = asyncio.create_task(svc.window("k", 60, None, wav, at=65))
        await asyncio.sleep(0.01)
        b = asyncio.create_task(svc.window("k", 90, None, wav, at=65))                  # lookahead, one window ahead: wanted
        return await asyncio.gather(a, b)
    a, b = run(go())
    assert a["start"] == 60.0 and b["start"] == 90.0
    assert eng.finished == [60.0, 90.0]


def test_a_request_cancelled_while_waiting_does_not_block_the_next(tmp_path):
    eng = Engine(work=0.2)
    svc = captions.CaptionService(tmp_path, eng)

    async def go():
        busy = asyncio.create_task(svc.window("k", 0, None, wav, at=0))
        await asyncio.sleep(0.05)
        waiting = asyncio.create_task(svc.window("k", 30, None, wav, at=0))
        await asyncio.sleep(0.02)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await busy
        return await asyncio.wait_for(svc.window("k", 30, None, wav, at=0), 3)
    assert run(go())["start"] == 30.0


def test_a_disconnected_caller_gives_the_turn_back(tmp_path):
    eng = Engine(work=0.1)
    svc = captions.CaptionService(tmp_path, eng)
    state = {"gone": True}

    async def gone():
        return state["gone"]

    async def go():
        with pytest.raises(captions.CaptionsStale):
            await svc.window("k", 0, None, wav, is_gone=gone, at=0)
        state["gone"] = False
        return await asyncio.wait_for(svc.window("k", 0, None, wav, is_gone=gone, at=0), 3)
    assert run(go())["start"] == 0.0 and eng.started == [0.0]


def test_an_older_player_without_at_still_gets_its_newest_window(tmp_path):
    eng = Engine(work=0.1)
    svc = captions.CaptionService(tmp_path, eng)

    async def go():
        a = asyncio.create_task(svc.window("k", 0, None, wav))
        await asyncio.sleep(0.02)
        b = asyncio.create_task(svc.window("k", 30, None, wav))
        c = asyncio.create_task(svc.window("k", 500, None, wav))                        # far away: the one between is skipped, as before
        return await asyncio.gather(a, b, c, return_exceptions=True)
    a, b, c = run(go())
    assert isinstance(c, dict) and c["start"] == 480.0
    assert isinstance(b, captions.CaptionsStale)                                        # (the running window 0 is abandoned too, or finishes)


def test_a_live_request_that_joins_an_abandoned_window_still_gets_it(tmp_path):
    """The viewer skips away (the player aborts its request) and back (it asks again) while the first request still waits in the queue:
    the window must not be thrown away because the first request is gone."""
    eng = Engine(work=0.2)
    svc = captions.CaptionService(tmp_path, eng)

    async def dead():
        return True

    async def go():
        busy = asyncio.create_task(svc.window("other", 900, None, wav, at=900))        # keeps the engine busy meanwhile
        await asyncio.sleep(0.05)
        first = asyncio.create_task(svc.window("k", 120, None, wav, is_gone=dead, at=120))   # its player already gave up on this request
        await asyncio.sleep(0.02)
        again = asyncio.create_task(svc.window("k", 120, None, wav, at=120))           # the same window, asked again by a live request
        await busy
        return await asyncio.gather(first, again)
    first, again = run(go())
    assert again["start"] == 120.0 and first["start"] == 120.0                         # both are served by the one run
    assert eng.started.count(120.0) == 1


def test_a_window_everyone_gave_up_on_is_still_skipped(tmp_path):
    eng = Engine(work=0.2)
    svc = captions.CaptionService(tmp_path, eng)

    async def dead():
        return True

    async def go():
        busy = asyncio.create_task(svc.window("other", 900, None, wav, at=900))
        await asyncio.sleep(0.05)
        a = asyncio.create_task(svc.window("k", 120, None, wav, is_gone=dead, at=120))
        await asyncio.sleep(0.02)
        b = asyncio.create_task(svc.window("k", 120, None, wav, is_gone=dead, at=120))
        await busy
        return await asyncio.gather(a, b, return_exceptions=True)
    a, b = run(go())
    assert isinstance(a, captions.CaptionsStale) and isinstance(b, captions.CaptionsStale)
    assert 120.0 not in eng.started


def test_the_viewers_own_window_preempts_a_running_lookahead(tmp_path):
    eng = Engine(work=2.0)
    svc = captions.CaptionService(tmp_path, eng)

    async def go():
        t0 = time.monotonic()
        ahead = asyncio.create_task(svc.window("k", 150, None, wav, at=120))           # the lookahead is running (it is not stale: one window ahead)
        await asyncio.sleep(0.15)
        now = asyncio.create_task(svc.window("k", 120, None, wav, at=120))             # the viewer's own window is asked for now
        r_now = await now
        return r_now, time.monotonic() - t0, (await asyncio.gather(ahead, return_exceptions=True))[0]
    r_now, took, r_ahead = run(go())
    assert r_now["start"] == 120.0
    assert took < 3.0                                                                    # not 2 s (the lookahead) + 2 s (its own)
    assert isinstance(r_ahead, captions.CaptionsStale)                                   # the lookahead gave way
    assert eng.finished == [120.0]
