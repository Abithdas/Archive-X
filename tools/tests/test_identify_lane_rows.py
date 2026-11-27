"""A finished metadata scan leaves the transfer lanes soon when it went well, and stays longer when something failed."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.engines import local_identify_adult, local_scanner, screen_identify


def finished(seconds_ago, **extra):
    return {"running": False, "total": 2, "done": 2, "channel": "", "started": time.time() - seconds_ago - 5, "finished": time.time() - seconds_ago, **extra}


def rows(monkeypatch, state, module=local_identify_adult):
    monkeypatch.setattr(module, "progress", lambda: state)
    return [r["id"] for r in local_scanner._identify_rows(None)]


def test_a_clean_finished_scan_is_shown_for_a_moment_only(monkeypatch):
    monkeypatch.setattr(screen_identify, "progress", lambda: {})
    assert rows(monkeypatch, finished(5, failed=0)) == ["identify:nsfw"]
    assert rows(monkeypatch, finished(120, failed=0)) == []


def test_a_scan_with_failures_stays_until_it_has_been_seen(monkeypatch):
    monkeypatch.setattr(screen_identify, "progress", lambda: {})
    assert rows(monkeypatch, finished(60, failed=1)) == ["identify:nsfw"]
    assert rows(monkeypatch, finished(120, failed=1)) == []        # a minute and a half: long enough to see the failure, short enough not to look stuck


def test_a_running_scan_is_always_shown(monkeypatch):
    monkeypatch.setattr(screen_identify, "progress", lambda: {})
    assert rows(monkeypatch, {"running": True, "total": 5, "done": 1, "started": time.time() - 3}) == ["identify:nsfw"]
