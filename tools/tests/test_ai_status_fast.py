import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.ai.local_ai_runtime import LocalAIRuntime


def test_status_does_not_ask_the_model_server_again_within_a_few_seconds_and_fresh_does(tmp_path, monkeypatch):
    rt = LocalAIRuntime(Database(tmp_path / "t.db"))
    asked = []
    def fake(method, path, payload=None, timeout=8.0):
        asked.append(timeout)
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(rt, "_request", fake)
    first = rt.status()
    assert first["server"]["reachable"] is False and asked == [1.0]       # a first look does not wait the full time
    rt.status(); rt.status()
    assert asked == [1.0]                                                  # the page asking again soon costs nothing
    rt.status(fresh=True)
    assert asked == [1.0, 2.5]                                             # starting, stopping and configuring ask afresh
    rt.stop()
    assert len(asked) == 3 or asked[-1] == 2.5
    monkeypatch.setattr(time, "monotonic", lambda t=time.monotonic: t() + 6.0)   # after the cache has expired it asks again
    rt.status()
    assert asked[-1] == 1.0 and len(asked) >= 4
