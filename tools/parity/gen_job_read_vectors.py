"""Record what the Python database layer answers for the job queries and checkpoints, for crates/ax-db/src/job_reads.rs to repeat.

    python tools/parity/gen_job_read_vectors.py          # rewrite crates/ax-db/tests/job_read_ops_cases.json
    python tools/parity/gen_job_read_vectors.py --check  # fail if the file is out of date

Same method as gen_lease_op_vectors.py (fake clock, random ids replaced by the names a scenario gives them). Activity events and transfer
telemetry are put in by plain SQL (the app's own writers buffer and throttle in process memory), then each query's answer is recorded.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
OUT = ROOT / "crates" / "ax-db" / "tests" / "job_read_ops_cases.json"

SCENARIOS = {
    "listing and dashboard": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "advance", "seconds": 1},
        {"op": "create", "name": "b", "type": "METADATA_SYNC"},
        {"op": "advance", "seconds": 1},
        {"op": "create", "name": "c", "type": "UPLOAD_LOCAL"},
        {"op": "advance", "seconds": 1},
        {"op": "create", "name": "d", "type": "BACKUP_CREATE"},
        {"op": "jobs", "limit": 50},
        {"op": "jobs", "limit": 2},
        {"op": "active"},
        {"op": "active", "type": "UPLOAD_LOCAL"},
        {"op": "active", "type": "NOTHING"},
        {"op": "advance", "seconds": 1},
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {"completed": 1, "total": 4, "error": ""}},
        {"op": "advance", "seconds": 1},
        {"op": "transition", "job": "a", "status": "COMPLETED", "fields": {"completed": 4, "total": 4}},
        {"op": "advance", "seconds": 1},
        {"op": "transition", "job": "b", "status": "RUNNING", "fields": {}},
        {"op": "transition", "job": "b", "status": "FAILED", "fields": {"error": "boom"}},
        {"op": "advance", "seconds": 1},
        {"op": "transition", "job": "c", "status": "CANCELLED", "fields": {}},
        {"op": "dashboard"},
        {"op": "active"},
        {"op": "jobs", "limit": 3},
    ],
    "one job in full": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL", "args": {"resource_requirements": {"playback_compatible": True}}},
        {"op": "create", "name": "b", "type": "METADATA_SYNC", "args": {"depends_on": ["a"]}},
        {"op": "advance", "seconds": 1},
        {"op": "create", "name": "c", "type": "METADATA_SYNC", "args": {"depends_on": ["b"]}},
        {"op": "acquire", "job": "a", "as": "e1", "worker": "w", "lease_seconds": 45},
        {"op": "finish", "job": "a", "execution": "e1"},
        {"op": "acquire", "job": "a", "as": "e2", "worker": "w", "lease_seconds": 45},
        {"op": "activity", "job": "a", "level": "INFO", "event": "STARTED", "operation": "op", "message": "first", "details": "{\"k\": 1}"},
        {"op": "activity", "job": "b", "level": "INFO", "event": "STARTED", "operation": "op", "message": "other job", "details": "{}"},
        {"op": "activity", "job": "a", "level": "WARN", "event": "SLOW", "operation": "op", "message": "second", "details": "not json at all"},
        {"op": "activity", "job": "a", "level": "INFO", "event": "X", "operation": "", "message": "third", "details": ""},
        {"op": "activity", "job": "a", "level": "INFO", "event": "X", "operation": "", "message": "fourth", "details": "[1, 2]"},
        {"op": "get_job", "job": "a"},
        {"op": "get_job", "job": "c"},
        {"op": "get_job", "job": "missing"},
        {"op": "latest_activity", "job": "a"},
        {"op": "latest_activity", "job": "b"},
        {"op": "latest_activity", "job": ""},
        {"op": "latest_activity", "job": "missing"},
    ],
    "the activity timeline": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "create", "name": "b", "type": "UPLOAD_LOCAL"},
        *[{"op": "activity", "job": "a" if i % 3 else "b", "level": "INFO", "event": "E%d" % i, "operation": "o", "message": "m%d" % i, "details": json.dumps({"i": i})} for i in range(12)],
        {"op": "recent", "jobs": ["a"], "limit": 200},
        {"op": "recent", "jobs": ["a", "b"], "limit": 5},
        {"op": "recent", "jobs": ["a", "b"], "limit": 5, "before": 9},
        {"op": "recent", "jobs": ["a", "b"], "limit": 0},
        {"op": "recent", "jobs": ["a", "b"], "limit": -3},
        {"op": "recent", "jobs": ["a", "b"], "limit": 100000},
        {"op": "recent", "jobs": ["a"], "limit": 5, "before": 0},
        {"op": "recent", "jobs": ["a"], "limit": 5, "before": -4},
        {"op": "recent", "jobs": ["a", "a", "", "-", "None", " "], "limit": 3},
        {"op": "recent", "jobs": [], "limit": 3},
        {"op": "recent", "jobs": ["nobody"], "limit": 3},
    ],
    "transfer telemetry": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "create", "name": "b", "type": "UPLOAD_LOCAL"},
        {"op": "create", "name": "c", "type": "UPLOAD_LOCAL"},
        {"op": "telemetry", "job": "a", "telemetry": "{\"stage\": \"upload\", \"bytes_done\": 5}", "updated_at": "2031-01-01T00:00:00+00:00"},
        {"op": "telemetry", "job": "b", "telemetry": "{\"stage\": \"hash\"}", "updated_at": "2031-01-02T00:00:00+00:00"},
        {"op": "telemetry", "job": "c", "telemetry": "not json", "updated_at": "x"},
        {"op": "telemetry_get", "jobs": ["a"]},
        {"op": "telemetry_get", "jobs": ["a", "b", "c", "missing"]},
        {"op": "telemetry_get", "jobs": ["c"]},
        {"op": "telemetry_get", "jobs": ["", "-", "None"]},
        {"op": "telemetry_get", "jobs": []},
    ],
    "checkpoints": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "checkpoint_get", "job": "a"},
        {"op": "advance", "seconds": 1},
        {"op": "checkpoint_save", "job": "a", "args": {"payload": {"done": [1, 2], "é": "ü"}, "stage": "scan", "item_id": 12345, "item_index": 3, "total_items": 10, "current_operation": "Scanning", "last_completed_operation": "Listing", "input_fingerprint": "in", "output_fingerprint": "out", "retry_count": 2}},
        {"op": "checkpoint_get", "job": "a"},
        {"op": "advance", "seconds": 1},
        {"op": "checkpoint_save", "job": "a", "args": {"item_id": "x" * 400, "current_operation": "y" * 300, "last_completed_operation": "z" * 300}},
        {"op": "checkpoint_get", "job": "a"},
        {"op": "advance", "seconds": 1},
        {"op": "checkpoint_save", "job": "a", "args": {"payload": "plain text", "update_fields": {"status": "running", "current_operation": "Go", "progress": 0.5}}},
        {"op": "checkpoint_get", "job": "a"},
        {"op": "checkpoint_save", "job": "a", "args": {"update_fields": {"status": "RUNNING", "detail": "same status again"}}},
        {"op": "checkpoint_save", "job": "a", "args": {"update_fields": {"status": "FAILED", "error": "boom", "updated_at": "ignored"}}},
        {"op": "checkpoint_save", "job": "a", "args": {"update_fields": {"status": "COMPLETED"}}},
        {"op": "checkpoint_save", "job": "a", "args": {"update_fields": {"status": "BOGUS"}}},
        {"op": "checkpoint_save", "job": "a", "args": {"update_fields": {"status": None, "current_operation": "null status is no status"}}},
        {"op": "checkpoint_save", "job": "a", "args": {"update_fields": {"status": ""}}},
        {"op": "checkpoint_save", "job": "a", "args": {"update_fields": {"progress": 1.0}}},
        {"op": "checkpoint_save", "job": "missing", "args": {}},
        {"op": "checkpoint_get", "job": "missing"},
        {"op": "checkpoint_get", "job": "a"},
    ],
}


def run(ops):
    from gen_lease_op_vectors import BASE, install_clock
    from telegram_archive.core.database import Database
    clock = install_clock()
    db = Database(Path(tempfile.mkdtemp()) / "a.db")
    jobs, others = {}, {}
    hex32 = re.compile(r"\b[0-9a-f]{32}\b")

    def jid(name):
        return next((k for k, v in jobs.items() if v == name), name)

    def clean(v):
        if isinstance(v, str):
            for k, n in jobs.items():
                v = v.replace(k, "<job:%s>" % n)
            for k, n in others.items():
                v = v.replace(k, n)
            return hex32.sub("<id>", v)
        if isinstance(v, dict):
            return {clean(k): clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, tuple):
            return [clean(x) for x in v]
        return v

    steps = []
    for op in ops:
        error, result = None, None
        kind = op["op"]
        try:
            if kind == "advance":
                clock["secs"] += op["seconds"]
            elif kind == "create":
                args = dict(op.get("args", {}))
                depends = [jid(n) for n in args.pop("depends_on", [])] or None
                made = db.create_job(op["type"], "", "", False, "", depends_on=depends, **args)
                jobs[made] = op["name"]
            elif kind == "transition":
                db.transition_job(jid(op["job"]), op["status"], **op["fields"])
            elif kind == "acquire":
                got = db.acquire_job_execution(jid(op["job"]), worker_id=op["worker"], lease_seconds=op["lease_seconds"])
                others[got["execution_id"]] = "<exec:%s>" % op["as"]
                others[got["lease_id"]] = "<lease:%s>" % op["as"]
            elif kind == "finish":
                ex = next(k for k, v in others.items() if v == "<exec:%s>" % op["execution"])
                db.finish_job_execution(jid(op["job"]), ex, status="COMPLETED", failure_class="")
            elif kind == "activity":
                with db.lock:
                    db.conn.execute("INSERT INTO job_activity_events(job_id,level,event,operation,message,details,created_at) VALUES(?,?,?,?,?,?,?)",
                                    (jid(op["job"]), op["level"], op["event"], op["operation"], op["message"], op["details"], db_now()))
                    db.conn.commit()
            elif kind == "telemetry":
                with db.lock:
                    db.conn.execute("INSERT INTO job_transfer_telemetry(job_id,telemetry,updated_at) VALUES(?,?,?)", (jid(op["job"]), op["telemetry"], op["updated_at"]))
                    db.conn.commit()
            elif kind == "jobs":
                result = clean(db.jobs(op["limit"]))
            elif kind == "active":
                result = clean(db.active_jobs(op.get("type")))
            elif kind == "dashboard":
                active, last = db.job_dashboard_snapshot()
                result = clean([active, last])
            elif kind == "get_job":
                result = clean(db.get_job(jid(op["job"])))
            elif kind == "latest_activity":
                result = db.latest_job_activity_id(jid(op["job"]) if op["job"] else op["job"])
            elif kind == "recent":
                kw = {"limit": op["limit"]}
                if "before" in op:
                    kw["before_id"] = op["before"]
                result = clean(db.recent_job_activity([jid(n) for n in op["jobs"]], **kw))
            elif kind == "telemetry_get":
                result = clean(db.get_job_transfer_telemetry([jid(n) for n in op["jobs"]]))
            elif kind == "checkpoint_get":
                result = clean(db.get_job_checkpoint(jid(op["job"])))
            elif kind == "checkpoint_save":
                db.save_job_checkpoint(jid(op["job"]), **op["args"])
            else:
                raise AssertionError(kind)
        except Exception as exc:
            error = clean(str(exc))
            try:
                db.conn.rollback()
            except Exception:
                pass
        steps.append({"op": op, "clock": {"secs": clock["secs"], "micros": 0}, "error": error, "result": result, "state": state(db, clean)})
    return {"base": BASE, "steps": steps}


def db_now():
    import telegram_archive.config as config
    return config.now_iso()


def state(db, clean):
    out = {}
    for table, key in (("jobs", lambda d: d["id"]), ("job_journal", lambda d: (d["job_id"], d["sequence"])), ("job_checkpoints", lambda d: d["job_id"]),
                       ("job_activity_events", lambda d: d["id"])):
        rows = [clean(dict(r)) for r in db.conn.execute(f"SELECT * FROM {table}").fetchall()]
        if table == "job_journal":
            for r in rows:
                r.pop("id", None)
                r["event_id"] = "<E>"
        rows.sort(key=key)
        out[table] = rows
    return out


def build():
    return {name: run(ops) for name, ops in SCENARIOS.items()}


def main():
    # keys sorted: dicts keyed by job id come back in the order of random ids
    text = json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.relative_to(ROOT)} is out of date: run python tools/parity/gen_job_read_vectors.py")
        print("job read vectors are up to date")
        return
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), f"({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
