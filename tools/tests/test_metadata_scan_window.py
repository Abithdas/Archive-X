"""The optional daily scan window: a scan waits outside the hours the user set, and must never wait by mistake or without a way out."""
import asyncio
from datetime import datetime

import pytest

import metadata_scan_harness as h
from telegram_archive.core import scan_schedule


def at(monkeypatch, hour, minute=0):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 16, hour, minute, 0)
    monkeypatch.setattr(scan_schedule, "datetime", Fixed)


def settings(db, enabled, start="01:00", end="07:00"):
    db.set_setting("metadata_scan_window_enabled", "1" if enabled else "0")
    db.set_setting("metadata_scan_window_start", start)
    db.set_setting("metadata_scan_window_end", end)


@pytest.fixture(autouse=True)
def _reset():
    yield
    settings(h.fresh_db(), False)


def waits(db, *, cancel_after=3):
    """True when the gate held the scan back (it asked to stop through the cancel), False when it let it through at once."""
    ctx = h.make_ctx(db, {}, cancel_after=cancel_after)
    real_sleep = asyncio.sleep

    async def quick(s, *a, **k):
        await real_sleep(0)

    asyncio.sleep, saved = quick, asyncio.sleep
    try:
        try:
            h.run(scan_schedule.wait_for_metadata_scan_window(ctx))
            return False
        except h.JobCancelled:
            return True
    finally:
        asyncio.sleep = saved


def test_no_window_means_no_waiting(monkeypatch):
    db = h.fresh_db()
    settings(db, False)
    at(monkeypatch, 12)
    assert waits(db) is False


def test_a_job_that_does_not_enforce_the_window_is_never_held(monkeypatch):
    db = h.fresh_db()
    settings(db, True)
    at(monkeypatch, 12)
    ctx = h.make_ctx(db, {})
    ctx.scan_window_enforced = False
    assert h.run(scan_schedule.wait_for_metadata_scan_window(ctx)) is None


@pytest.mark.parametrize("hour,minute,held", [(0, 59, True), (1, 0, False), (3, 30, False), (6, 59, False), (7, 0, True), (12, 0, True), (23, 59, True)])
def test_a_daytime_free_window_one_to_seven(monkeypatch, hour, minute, held):
    db = h.fresh_db()
    settings(db, True, "01:00", "07:00")
    at(monkeypatch, hour, minute)
    assert waits(db) is held


@pytest.mark.parametrize("hour,held", [(21, True), (22, False), (23, False), (0, False), (5, False), (6, True), (12, True)])
def test_a_window_that_runs_past_midnight(monkeypatch, hour, held):
    db = h.fresh_db()
    settings(db, True, "22:00", "06:00")
    at(monkeypatch, hour)
    assert waits(db) is held


def test_the_same_start_and_end_means_always_open(monkeypatch):
    db = h.fresh_db()
    settings(db, True, "03:00", "03:00")
    at(monkeypatch, 12)
    assert waits(db) is False


@pytest.mark.parametrize("start,end", [("25:00", "07:00"), ("1", "7"), ("ab:cd", "07:00"), ("01:00", "99:99")])
def test_an_unreadable_window_never_blocks_the_scan(monkeypatch, start, end):
    db = h.fresh_db()
    settings(db, True, start, end)
    at(monkeypatch, 12)
    assert waits(db) is False


def test_blank_times_fall_back_to_the_default_one_to_seven_window(monkeypatch):
    db = h.fresh_db()
    settings(db, True, "", "")
    at(monkeypatch, 12)
    assert waits(db) is True
    at(monkeypatch, 3)
    assert waits(db) is False
