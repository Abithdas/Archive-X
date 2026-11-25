"""A metadata scan's bar counts stages, so it does not rescale when a stage starts with a different number of items."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.engines import metadata_channel_pipeline as mcp


def payload(phases, progress=None, channels=1, **extra):
    entry = lambda: {"phases": dict(phases), "phase_progress": {k: dict(v) for k, v in (progress or {}).items()}}
    return {"stages": ["inventory", "metadata", "performers", "channel_data", "snapshots"], "channel_queue": [entry() for _ in range(channels)], **extra}


def test_the_total_is_the_number_of_stages_and_does_not_depend_on_items():
    one = mcp._stage_progress(payload({}))
    assert (one["stages_total"], one["stages_done"], one["stage_progress"]) == (5, 0, 0.0)
    assert mcp._stage_progress(payload({}, channels=3))["stages_total"] == 15


def test_a_finished_stage_counts_whole_and_the_running_one_by_its_items():
    p = payload({"inventory": "COMPLETED", "metadata": "PROCESSING"}, {"metadata": {"completed": 5, "total": 10}})
    out = mcp._stage_progress(p)
    assert (out["stages_done"], out["stages_total"], out["stage_progress"]) == (1, 5, 30.0)     # (1 + 0.5) / 5


def test_a_stage_that_learns_it_has_more_items_does_not_move_the_bar_back():
    p = payload({"inventory": "COMPLETED", "metadata": "PROCESSING"}, {"metadata": {"completed": 8, "total": 10}})
    first = mcp._stage_progress(p)["stage_progress"]                                           # (1 + 0.8) / 5 = 36
    p["channel_queue"][0]["phase_progress"]["metadata"] = {"completed": 8, "total": 80}        # the same eight items, out of eighty now
    again = mcp._stage_progress(p)["stage_progress"]
    assert first == 36.0 and again == 36.0                                                       # kept at the highest value reached
    p["channel_queue"][0]["phase_progress"]["metadata"] = {"completed": 60, "total": 80}
    assert mcp._stage_progress(p)["stage_progress"] == 36.0                                      # (1 + 0.75) / 5 = 35 is still below what was reached
    p["channel_queue"][0]["phase_progress"]["metadata"] = {"completed": 76, "total": 80}
    assert mcp._stage_progress(p)["stage_progress"] == 39.0                                      # (1 + 0.95) / 5: it moves on once it passes it


def test_it_reaches_the_end_when_every_stage_is_done():
    out = mcp._stage_progress(payload({s: "COMPLETED" for s in ("inventory", "metadata", "performers", "channel_data", "snapshots")}))
    assert (out["stages_done"], out["stage_progress"]) == (5, 100.0)


def test_the_snapshot_carries_it_and_the_view_reads_it():
    p = payload({"inventory": "COMPLETED"})
    snap = mcp._work_snapshot(p)
    assert snap["stages_total"] == 5 and snap["stages_done"] == 1
    assert mcp.stage_view("METADATA_CHANNEL_PIPELINE", {"progress": snap}) == {"completed": 1, "total": 5, "progress": 20.0}
    assert mcp.stage_view("TELEGRAM_TRANSFER", {"progress": snap}) is None                      # other jobs keep their own counters
    assert mcp.stage_view("METADATA_CHANNEL_PIPELINE", {}) is None                              # nothing counted yet: its own counters


def test_the_status_and_the_transfer_lanes_show_the_scan_by_stages(tmp_path):
    import json
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    from telegram_archive.core.database import Database
    db = Database(tmp_path / "s.db")
    ensure_web_credentials(db)
    H, PW = {"Origin": "https://testserver"}, "correct-horse-battery"
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    c.post("/api/auth/setup", json={"username": "admin", "password": PW, "confirmation": PW}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": PW}, headers=H).status_code == 200
    detail = payload({"inventory": "COMPLETED", "metadata": "PROCESSING"}, {"metadata": {"completed": 3, "total": 30}})
    detail["progress"] = mcp._work_snapshot(detail)
    jid = db.create_job("METADATA_CHANNEL_PIPELINE", "-1001", "", False, detail=json.dumps(detail))
    db.update_job(jid, status="RUNNING", completed=3, total=30, progress=10.0, current_operation="Caption sync 3/30")
    job = next(j for j in c.get("/api/status").json()["jobs"] if j["id"] == jid)
    assert (job["completed"], job["total"], job["progress"], job["stage"]) == (1, 5, detail["progress"]["stage_progress"], {"completed": 3, "total": 30})
    lane = next(r for r in c.get("/api/telegram-transfer/lanes").json()["jobs"] if r["id"] == jid)
    assert (lane["completed"], lane["total"], lane["stage"]) == (1, 5, {"completed": 3, "total": 30}) and lane["progress"] == round(job["progress"], 1)
    other = db.create_job("TELEGRAM_TRANSFER", "-1001", "-1002", False, detail="{}")
    db.update_job(other, status="RUNNING", completed=2, total=8, progress=25.0)
    import time
    time.sleep(1.2)                                                                              # the status is cached for a second
    row = next(j for j in c.get("/api/status").json()["jobs"] if j["id"] == other)
    assert (row["completed"], row["total"], row["stage"]) == (2, 8, None)                        # any other job keeps its own counters
