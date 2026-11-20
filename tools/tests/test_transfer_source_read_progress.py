"""The transfer reads the source messages again right after preparation, 100 per Telegram call.

Telegram answers a call with a FloodWait of about 30 s when the account was just used heavily, and the loop said nothing until it
finished, so the watchdog saw no activity for two minutes and force-stopped the job (log of 15 Nov 2026, 2 of 2 attempts).
Every call must now be preceded by a progress line: the Media Manager runner turns changed console text into watchdog activity.
"""
import asyncio
import contextlib
import importlib
from types import SimpleNamespace


class _Conn:
    def __init__(self, ids):
        self.ids = ids

    def execute(self, sql, params=()):
        ids = self.ids
        return SimpleNamespace(
            fetchone=lambda: {"high_water_mark": 0, "last_scan_status": "COMPLETE", "last_scan_at": ""},
            fetchall=lambda: [{"message_id": i} for i in ids],
        )


class _Db:
    def __init__(self, ids):
        self.ids = ids

    @contextlib.contextmanager
    def reading(self):
        yield _Conn(self.ids)

    def log(self, *a, **k):
        pass


class _Client:
    def __init__(self, log):
        self.log = log

    def is_connected(self):
        return True

    async def get_messages(self, source, ids=None):
        self.log.append(("call", list(ids)))
        return [SimpleNamespace(id=i, video=None) for i in ids]


def test_each_source_read_is_announced_before_the_call(tmp_path, monkeypatch):
    mod = importlib.import_module("telegram_archive.media_manager.telegram_media_manager")
    events = []
    old = (getattr(mod, "MEDIA_MANAGER_DB", None),)
    mod.configure(print_fn=lambda *a, **k: events.append(("print", " ".join(str(x) for x in a))), state_dir=tmp_path)
    mod.MEDIA_MANAGER_DB = _Db(list(range(1, 251)))   # 250 known ids -> calls of 100, 100, 50
    monkeypatch.setattr(mod, "canonical_peer_id", lambda s: "-1001")
    try:
        asyncio.run(mod.transfer_scan_candidates(_Client(events), SimpleNamespace(id=1), "VIDEO", "BOTH", state={}))
    finally:
        mod.MEDIA_MANAGER_DB = old[0]
    reads = [i for i, e in enumerate(events) if e[0] == "call"]
    assert len(reads) == 3
    for i in reads:
        before = events[i - 1]
        assert before[0] == "print" and "Reading source messages" in before[1], before
    texts = [e[1] for e in events if e[0] == "print" and "Reading source messages" in e[1]]
    assert texts[-1].endswith("250 of 250")
