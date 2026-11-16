"""The progress report the page polls, with whatever a job's saved detail may hold (older versions, interrupted writes, odd values)."""
import json

import pytest

import metadata_scan_harness as h

P = h.pipe

BASE = {"channel_ids": ["-1"], "stages": list(P.PHASES), "current_channel_index": 0,
        "channel_queue": [{"channel_id": "-1", "state": "PROCESSING", "videos": [{"media_id": 1}], "phases": {"inventory": "COMPLETED"},
                           "phase_progress": {"inventory": {"completed": 1, "total": 1}, "metadata": {"completed": 2, "total": 5, "eta_seconds": 30}},
                           "counts": {"videos": 5}, "readiness": {"published_ready": 1}}]}


def bad(**changes):
    d = json.loads(json.dumps(BASE))
    for k, v in changes.items():
        if k.startswith("entry_"):
            d["channel_queue"][0][k[6:]] = v
        else:
            d[k] = v
    return d


@pytest.mark.parametrize("detail", [
    None, "", "not json at all", "[]", "123", "null", '{"channel_queue": "x"}', {}, [], 5,
    BASE,
    bad(entry_phase_progress=[]), bad(entry_phase_progress="x"), bad(entry_phase_progress={"metadata": "oops"}),
    bad(entry_phase_progress={"metadata": {"completed": "a", "total": "b"}}), bad(entry_phase_progress={"metadata": {"completed": None, "total": None}}),
    bad(entry_phase_progress={"metadata": {"completed": 9, "total": 3}}), bad(entry_phase_progress={"metadata": {"completed": -5, "total": -2}}),
    bad(entry_phase_progress={"metadata": {"eta_seconds": "soon", "total": 4, "completed": 1}}),
    bad(entry_counts=[]), bad(entry_counts={"videos": "many"}), bad(entry_readiness="x"), bad(entry_readiness={"published_ready": "n"}),
    bad(entry_phases=[]), bad(entry_phases={"metadata": None}), bad(entry_videos="x"), bad(entry_state=None), bad(entry_error=None),
    bad(current_channel_index="x"), bad(current_channel_index=99), bad(current_channel_index=-3), bad(current_channel_index=None),
    bad(progress_started_at="yesterday"), bad(progress_started_at=-1), bad(caption_reconciliation="x"), bad(caption_reconciliation={"total": "x"}),
    bad(stages="all"), bad(stages=[]), bad(stages=None), bad(channel_queue=[None, 3, "x", {}]), bad(channel_queue=[]),
])
def test_the_progress_report_never_crashes(detail):
    out = P.progress_snapshot(detail)
    assert isinstance(out, dict) and 0.0 <= out["overall_progress"] <= 100.0
    json.dumps(out)                                          # and it can always be sent to the page


def test_a_normal_job_reports_sensible_numbers():
    out = P.progress_snapshot(BASE)
    assert out["channel_count"] == 1 and out["current_channel_id"] == "-1" and 0 < out["overall_progress"] < 100
    row = out["channels"][0]
    assert {p["phase"]: p["state"] for p in row["phases"]}["inventory"] == "COMPLETED"
    meta = next(p for p in row["phases"] if p["phase"] == "metadata")
    assert (meta["completed"], meta["total"]) == (2, 5) and meta["progress"] == 40.0


def test_a_finished_job_reads_one_hundred_percent_and_a_failed_one_does_not():
    done = bad(entry_phases={p: "COMPLETED" for p in P.PHASES}, entry_state="COMPLETED")
    assert P.progress_snapshot(done)["overall_progress"] == 100.0
    failed = bad(entry_phases={"inventory": "COMPLETED", "metadata": "FAILED"}, entry_state="FAILED", entry_error="boom")
    out = P.progress_snapshot(failed)
    assert out["overall_progress"] < 100 and out["channels"][0]["error"] == "boom"


def test_a_channel_with_no_videos_does_not_divide_by_zero():
    out = P.progress_snapshot({"channel_ids": ["-1"], "stages": ["inventory"], "channel_queue": [{"channel_id": "-1", "state": "COMPLETED", "videos": [], "phases": {"inventory": "COMPLETED"}}]})
    assert out["overall_progress"] == 100.0 and out["channels"][0]["total_videos"] == 0
