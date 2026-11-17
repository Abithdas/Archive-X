"""The database lock records who holds it, so an event-loop freeze can be traced to the code that kept the lock."""
import threading
import time

from telegram_archive.core.db_lock import TrackedRLock, long_holds


def test_it_is_reentrant_and_free_again_after_the_last_release():
    lock = TrackedRLock()
    with lock:
        with lock:
            assert lock.held_for()[0] == threading.get_ident()
        assert lock.held_for()[0] == threading.get_ident()   # still held by the outer block
    assert lock.held_for() == (None, 0.0)


def test_a_long_hold_is_reported_with_the_holder_and_what_it_is_doing():
    lock = TrackedRLock()
    release = threading.Event()
    taken = threading.Event()

    def hold_the_database_for_a_while():
        with lock:
            taken.set()
            release.wait(5)

    t = threading.Thread(target=hold_the_database_for_a_while, name="slow-writer")
    t.start()
    assert taken.wait(5)
    try:
        time.sleep(0.15)
        text = long_holds(0.1)
        assert "slow-writer" in text and "hold_the_database_for_a_while" in text
        assert long_holds(60) == ""            # a short hold is not worth a line
    finally:
        release.set(); t.join()
    assert "slow-writer" not in long_holds(0.0)


def test_other_threads_wait_and_non_blocking_acquire_still_works():
    lock = TrackedRLock()
    assert lock.acquire(blocking=False)
    got = []
    t = threading.Thread(target=lambda: got.append(lock.acquire(blocking=False)))
    t.start(); t.join()
    assert got == [False]
    lock.release()


def test_the_watchdog_can_tell_a_frozen_application_from_a_stuck_job():
    """A job that looks late because the whole event loop froze must not be force-stopped for it."""
    from telegram_archive.core.watchdogs import EventLoopGuard
    guard = EventLoopGuard(check_interval=0.2, stall_after=0.4)
    guard.start()
    try:
        guard.beat()
        t0 = time.monotonic()
        assert not guard.stalled_since(t0)
        time.sleep(0.9)                      # nothing beats: the loop is frozen
        assert guard.stalled_since(t0)       # frozen right now
        guard.beat()                         # it recovers
        assert guard.stalled_since(t0)       # but it was frozen after t0
        assert not guard.stalled_since(time.monotonic() + 0.01)
    finally:
        guard.stop()
