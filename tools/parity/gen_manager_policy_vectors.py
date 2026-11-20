"""Record the pure decisions of Python's `jobs/manager.py`: lanes, resource category, conflicts, lane capacity, coalescing, completion.

    python tools/parity/gen_manager_policy_vectors.py          # rewrite crates/ax-jobs/tests/manager_policy_cases.json
    python tools/parity/gen_manager_policy_vectors.py --check  # fail if the file is out of date

Every job type is combined with a set of detail payloads (valid, empty, not JSON, JSON that is not an object, odd types inside). For each
combination the answer of each function, or the text of the exception Python raises, is recorded; `crates/ax-jobs/tests/manager_policy.rs`
replays them on the Rust port.
"""
import itertools
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-jobs" / "tests" / "manager_policy_cases.json"

TYPES = ["UPLOAD_LOCAL", "MEDIA_MANAGER", "TELEGRAM_TRANSFER", "METADATA_CHANNEL_PIPELINE", "METADATA_ASSETS", "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC",
         "THUMBNAIL_INDEXER", "PREVIEW_PREFETCH", "ASSET_PREFETCH", "CONTENT_AUTOMATION", "BACKUP_CREATE", "", None, "something_else"]

DETAILS = [
    None, "", "{}", "not json", "[1, 2]", "7", "null", '"text"', 5, [1], {},
    '{"workflow": "content_automation"}', '{"workflow": " Content_Automation "}', '{"workflow": "other", "source": "LOCAL"}',
    '{"source": "local_files"}', '{"source": " Upload_Local "}', '{"source": "telegram"}', '{"source": "", "source_type": "local"}', '{"source": 5}', '{"source": ["local"]}',
    '{"source": null, "source_type": "UPLOAD_LOCAL"}', '{"source": "STRASSE"}', '{"source": "ß"}', '{"workflow": 12}', '{"workflow": ["CONTENT_AUTOMATION"]}',
    '{"stages": ["metadata"]}', '{"stages": ["performers"]}', '{"stages": ["channel_data", "snapshots"]}', '{"stages": ["inventory"]}', '{"stages": []}', '{"stages": "metadata"}',
    '{"stages": ["metadata", "performers", "snapshots"]}', '{"stages": [1, null, "metadata"]}', '{"stages": null}',
    '{"operations": ["metadata"]}', '{"operations": ["Metadata_Sync", "THUMBNAILS", "bogus", "thumbnails"]}', '{"operations": ["entity_images", "suggestion_scan"]}',
    '{"operations": []}', '{"operations": "metadata"}', '{"operations": 5}', '{"operations": null}', '{"operations": {"thumbnails": 1, "metadata": 2}}', '{"operations": [null, 0, "", "Metadata"]}',
    '{"operations": [" metadata ", "THUMBNAIL_INDEXER"]}', '{"operations": ["thumbnails"], "thumbnails": {"a": 1}}',
    {"operations": ["thumbnails"]}, {"stages": ["snapshots"]}, {"manual": True}, '{"manual": true}', '{"manual": 0}', '{"manual": "yes"}',
]


def jobs():
    for t, d in itertools.product(TYPES, DETAILS):
        yield {"job_type": t, "detail": d}


def attempt(fn, *a):
    try:
        return {"ok": fn(*a)}
    except Exception as exc:  # recorded as Python raises it
        return {"error": str(exc)}


def build():
    import telegram_archive.config as config
    from telegram_archive.jobs import manager as m
    from telegram_archive.jobs.manager import JobManager

    out = {"capacities": config.EXECUTION_LANE_CAPACITIES, "jobs": [], "conflicts": [], "capacity": [], "merge": [], "completion": []}
    for job in jobs():
        row = {"job": job}
        row["category"] = attempt(m.job_resource_category, job)
        row["keys"] = attempt(m._execution_keys, job)
        stub = types.SimpleNamespace()
        row["lane_keys"] = attempt(lambda j: JobManager.execution_lane_keys(stub, j), job)
        row["manual"] = attempt(m._job_is_manual, job)
        row["operations"] = attempt(lambda: list(m.metadata_assets.unified_detail(str(job.get("job_type") or ""), job.get("detail"))["operations"]))
        completion = types.SimpleNamespace(db=types.SimpleNamespace(get_job=lambda jid, j=job: j))
        row["completion"] = attempt(lambda: list(JobManager._metadata_pipeline_completion_status(completion, "x")))
        out["jobs"].append(row)
    # conflicts over a spread of jobs
    sample = [j for j in jobs() if j["detail"] in (None, "{}", '{"source": "local"}', '{"workflow": "content_automation"}', '{"stages": ["metadata"]}', '{"stages": ["snapshots"]}',
                                                    '{"operations": ["thumbnails"]}', '{"operations": ["metadata", "thumbnails"]}', "[1, 2]")]
    for a, b in itertools.product(sample[::5], sample[::7]):
        out["conflicts"].append({"a": a, "b": b, "result": attempt(m.execution_lanes_conflict, a, b)})
    # lane capacity: every configured lane and one unknown, with and without drain and each pressure
    lanes = list(config.EXECUTION_LANE_CAPACITIES) + ["UNKNOWN_LANE"]
    for lane, drain, pressure in itertools.product(lanes, (False, True), (None, "NORMAL", "pressured", "PRESSURED", "constrained", "CRITICAL", "", "other")):
        resources = types.SimpleNamespace(snapshot=lambda p=pressure: ({} if p is None else {"pressure_state": p}))
        stub = types.SimpleNamespace(_lane_drain=drain, resources=resources)
        out["capacity"].append({"lane": lane, "drain": drain, "pressure": pressure, "result": attempt(lambda: JobManager.execution_lane_capacity(stub, lane))})
    # pressure under the other key, and a snapshot that raises
    for key in ("pressure",):
        stub = types.SimpleNamespace(_lane_drain=False, resources=types.SimpleNamespace(snapshot=lambda: {key: "critical"}))
        out["capacity"].append({"lane": "METADATA", "drain": False, "pressure": "critical", "pressure_key": key, "result": attempt(lambda: JobManager.execution_lane_capacity(stub, "METADATA"))})
    # coalescing
    def j(detail, source_channel=None):
        return {"detail": detail, "source_channel": source_channel}
    merges = [
        (j('{"limit": 10, "source_channels": ["a", "b"]}'), []),
        (j('{"limit": 10, "source_channels": ["a", "b"]}', "c"), [j('{"limit": 30, "source_channels": ["b", "d"], "force": true}', "a")]),
        (j("{}"), [j('{"unbounded": true}'), j('{"limit": 99}')]),
        (j('{"unbounded": true}'), [j('{"limit": 99, "force": 1}')]),
        (j(None), [j(None)]),
        (j('{"limit": 0}'), [j('{"limit": "7"}')]),
        (j('{"limit": "x"}'), []),
        (j('{"limit": 2.9}'), [j('{"limit": null}')]),
        (j("not json", "é"), [j('{"source_channels": ["日本", "é", ""]}', 5)]),
        (j('{"source_channels": 5}'), []),
        (j('{"source_channels": "ab"}'), []),
        (j("[1]"), []),
        (j("{}"), [j("[1]")]),
        (j('{"limit": 3}'), [j('{"limit": 4, "unbounded": 0, "force": ""}'), j('{"limit": 2}')]),
        (j('{"source_channels": [1, 2, null, false, "x"]}', 2), []),
        (j('{"limit": true}'), []),
        (j('{"limit": [1]}'), []),
        (j('{"source_channels": ["a\\"b", "c\\\\d", "tab\\t", "😀"]}'), []),
    ]
    for keep, others in merges:
        out["merge"].append({"keep": keep, "others": others, "result": attempt(m.merge_metadata_detail, keep, others)})
    # completion of the metadata pipeline
    def pipe(detail):
        return {"job_type": "METADATA_CHANNEL_PIPELINE", "detail": detail}
    for detail in [
        '{"channel_queue": []}', '{"channel_queue": [{"channel_id": 5, "state": "completed", "phases": {"a": "completed"}}]}',
        '{"channel_queue": [{"channel_id": 5, "state": "FAILED"}, {"channel_id": 6, "state": "incomplete", "phases": {"x": "failed", "y": "ok", "z": null}}, "junk", 7]}',
        '{"channel_queue": [{"channel_id": null, "phases": {"x": "COMPLETED_WITH_ERRORS"}}]}', '{"channel_queue": "no"}', '{"channel_queue": [{"channel_id": 1, "phases": []}]}',
        "{}", "", None, "not json", "[1]", {"channel_queue": [{"channel_id": 9, "state": "failed"}]}, [1], 5,
    ]:
        out["completion"].append({"job": pipe(detail), "result": attempt(lambda d=detail: list(JobManager._metadata_pipeline_completion_status(types.SimpleNamespace(db=types.SimpleNamespace(get_job=lambda jid: pipe(d))), "x")))})
    out["completion"].append({"job": None, "result": attempt(lambda: list(JobManager._metadata_pipeline_completion_status(types.SimpleNamespace(db=types.SimpleNamespace(get_job=lambda jid: None)), "x")))})
    out["completion"].append({"job": {"job_type": "UPLOAD_LOCAL", "detail": "{}"}, "result": attempt(lambda: list(JobManager._metadata_pipeline_completion_status(types.SimpleNamespace(db=types.SimpleNamespace(get_job=lambda jid: {"job_type": "UPLOAD_LOCAL"})), "x")))})
    return json.dumps(out, ensure_ascii=False, indent=1) + "\n"


def main():
    text = build()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.relative_to(ROOT)} is out of date: run python tools/parity/gen_manager_policy_vectors.py")
        print("up to date")
        return
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), f"({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
