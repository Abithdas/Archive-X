"""Long holds of the database lock are counted by the code that held them."""
from __future__ import annotations

import time

from telegram_archive.core import db_lock
from telegram_archive.core.db_lock import TrackedRLock


def _slow_holder(lock):
    with lock:
        time.sleep(db_lock.SLOW_HOLD_SECONDS + 0.05)


def test_a_long_hold_is_recorded_against_the_code_that_held_it():
    db_lock._SLOW.clear()
    _slow_holder(TrackedRLock())
    rows = db_lock.slow_summary()
    assert rows and "_slow_holder" in rows[0]["where"]
    assert rows[0]["times"] == 1 and rows[0]["longest_seconds"] >= db_lock.SLOW_HOLD_SECONDS


def test_short_holds_and_nested_takes_cost_nothing_and_are_not_recorded():
    db_lock._SLOW.clear()
    lock = TrackedRLock()
    for _ in range(1000):
        with lock:
            with lock:
                pass
    assert db_lock.slow_summary() == []
