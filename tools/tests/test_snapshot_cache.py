import threading
import time

import pytest

from telegram_archive.core.snapshot_cache import SnapshotCache


def test_one_answer_is_shared_until_it_expires():
    cache, calls = SnapshotCache(0.2), []
    build = lambda: calls.append(1) or len(calls)
    assert cache.get(build) == 1 and cache.get(build) == 1
    time.sleep(0.25)
    assert cache.get(build) == 2


def test_callers_that_arrive_while_it_is_built_wait_for_that_answer():
    cache, calls, out = SnapshotCache(5), [], []

    def slow():
        calls.append(1); time.sleep(0.2); return "answer"
    threads = [threading.Thread(target=lambda: out.append(cache.get(slow))) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert out == ["answer"] * 6 and len(calls) == 1


def test_a_failure_is_not_kept_and_clear_forces_a_new_answer():
    cache = SnapshotCache(60)
    def boom(): raise RuntimeError("no")
    with pytest.raises(RuntimeError):
        cache.get(boom)
    assert cache.get(lambda: 1) == 1
    cache.clear()
    assert cache.get(lambda: 2) == 2
