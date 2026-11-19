"""The issue digest: logs, failed jobs and ignored errors turned into a short ranked list by rules, with no model."""
import os
import sys
import tempfile
import time

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axdg_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from telegram_archive.core import issue_digest as dg  # noqa: E402
from telegram_archive.core import swallowed  # noqa: E402
from telegram_archive.ai.ai_tools import AIToolRegistry  # noqa: E402
from telegram_archive.core.database import Database  # noqa: E402

NOW = 1_800_000_000.0


def ev(level, message, ago=60):
    return {"level": level, "message": message, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(NOW - ago))}


def digest(events, **kw):
    swallowed.reset()
    return dg.build(None, events=events, now=NOW, **kw)


def test_known_problems_are_named_with_their_usual_cause_and_where_to_look():
    d = digest([ev("WARN", "FloodWait: a wait of 33 seconds is required"), ev("ERROR", "ffmpeg not found on PATH"), ev("ERROR", "sqlite3.OperationalError: database is locked")])
    ids = [i["id"] for i in d["issues"]]
    assert ids[0] in {"ffmpeg", "db-locked"} and "telegram-flood" in ids               # the serious ones first
    flood = next(i for i in d["issues"] if i["id"] == "telegram-flood")
    assert flood["area"] == "Telegram" and flood["cause"] and flood["check"]


def test_the_same_problem_is_one_issue_with_a_count_and_two_real_lines():
    d = digest([ev("WARN", f"FloodWait: a wait of {n} seconds is required", ago=600 - n) for n in (10, 20, 30, 40)])
    assert len(d["issues"]) == 1
    i = d["issues"][0]
    assert i["count"] == 4 and len(i["evidence"]) == 2 and i["ago"].endswith("min ago")


def test_unknown_warnings_still_group_by_what_they_say_not_by_their_numbers_or_paths():
    d = digest([ev("WARN", rf"Could not read C:\Users\a\clip{n}.mp4 at byte {n * 1000}") for n in range(5)])
    assert len(d["issues"]) == 1 and d["issues"][0]["count"] == 5 and d["issues"][0]["area"] == "Log"


def test_no_secret_reaches_the_digest():
    d = digest([ev("ERROR", "Request failed with token=sk-abcdef1234567890abcdef1234567890 for api_key=ABCDEFGH12345678")])
    text = dg.as_text(d)
    assert "abcdef1234567890abcdef" not in text and "ABCDEFGH12345678" not in text


def test_a_quiet_log_says_so_and_info_lines_are_not_problems():
    d = digest([ev("INFO", "Started"), ev("INFO", "Scan finished")])
    assert d["ok"] is True and d["issues"] == []
    assert "No problems found" in dg.as_text(d)


def test_errors_the_code_ignores_show_up_with_where_in_the_code():
    swallowed.reset()
    for _ in range(30):
        try:
            raise KeyError("x")
        except KeyError:
            swallowed.note("routes_media.cover")
    d = dg.build(None, events=[], now=NOW)
    i = next(x for x in d["issues"] if x["area"] == "Ignored errors")
    assert "routes_media.cover" in i["title"] and i["count"] == 30 and i["severity"] == "medium" and "routes_media.cover" in i["check"]
    swallowed.reset()


def test_failed_jobs_are_read_from_the_database_with_the_reason_the_job_recorded(tmp_path):
    swallowed.reset()
    db = Database(tmp_path / "d.db")
    with db.lock:
        db.conn.execute("INSERT INTO jobs(id,job_type,status,error,created_at,updated_at) VALUES('j1','UPLOAD_LOCAL','FAILED','No space left on device','2026-01-01T00:00:00','2026-01-01T00:05:00')")
        db.conn.commit()
    d = dg.build(db, now=NOW)
    assert any(i["id"] == "disk-full" for i in d["issues"])


def test_it_is_short_enough_for_a_small_model_to_read_whole():
    lines = [ev("ERROR", f"Different failure number {n} in module{n} while doing thing{n}") for n in range(60)]
    text = dg.as_text(digest(lines))
    assert len(text) < 4500 and text.count("\n") < 45                                    # at most 8 issues, each a handful of lines


def test_the_assistant_has_the_digest_as_a_tool_and_cannot_record_a_fix(tmp_path):
    db = Database(tmp_path / "t.db")
    tools = AIToolRegistry(db)
    names = {d["function"]["name"] for d in tools.definitions()}
    assert "get_issue_digest" in names
    kinds = next(d for d in tools.definitions() if d["function"]["name"] == "record_finding")["function"]["parameters"]["properties"]["kind"]["enum"]
    assert "fix" not in kinds
    assert tools.execute("get_issue_digest")["ok"] in (True, False)
    from telegram_archive.ai.ai_assistant import SYSTEM_INSTRUCTIONS
    assert "DIAGNOSE, not to fix" in SYSTEM_INSTRUCTIONS and "get_issue_digest" in SYSTEM_INSTRUCTIONS
