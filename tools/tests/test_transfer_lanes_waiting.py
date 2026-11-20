from datetime import datetime, timedelta, timezone

from telegram_archive.web.transfer_lanes import transfer_lane_metrics

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _tele(age, state="active"):
    return {"updated_at": (NOW - timedelta(seconds=age)).isoformat(), "transfer_state": state}


def test_fresh_active_lane_has_no_waiting_reason():
    m = transfer_lane_metrics({"status": "RUNNING"}, _tele(2), now=NOW)
    assert "waiting_reason" not in m


def test_flood_wait_reports_the_operation_text():
    job = {"status": "RUNNING", "current_operation": "FloodWait 240s left"}
    assert transfer_lane_metrics(job, _tele(2, "paused"), now=NOW)["waiting_reason"] == "FloodWait 240s left"


def test_silent_lane_says_how_long():
    m = transfer_lane_metrics({"status": "RUNNING"}, _tele(95), now=NOW)
    assert m["state"] == "stale" and m["waiting_reason"] == "No progress update for 95 s"


def test_user_pause_and_queued_jobs():
    assert transfer_lane_metrics({"status": "PAUSED"}, _tele(2), now=NOW)["waiting_reason"] == "Paused"
    assert "waiting_reason" not in transfer_lane_metrics({"status": "QUEUED"}, None, now=NOW)
