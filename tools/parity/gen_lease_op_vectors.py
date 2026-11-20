"""Record what the Python database layer does for execution, admission, lane-slot and resource-lease operations, on a fake clock.

    python tools/parity/gen_lease_op_vectors.py          # rewrite crates/ax-db/tests/lease_ops_cases.json
    python tools/parity/gen_lease_op_vectors.py --check  # fail if the file is out of date

The clock is fake (it starts at 2031-03-04 05:06:07 and moves only when a scenario says `advance`), so every timestamp, lease expiry and
string comparison between the two time forms Python writes is reproducible and compared exactly. Random ids are replaced by the names a
scenario gives them (`as`). After each operation the recorded state is the operation's result or error and the rows of six tables.
"""
import datetime as _dt
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-db" / "tests" / "lease_ops_cases.json"

BASE = 1_930_713_967  # 2031-03-04T05:06:07Z

CAPS = {"cpu": 1, "net": 2, "disk": 3}

SCENARIOS = {
    "execution lifecycle": {"micros": 0, "ops": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "create", "name": "b", "type": "UPLOAD_LOCAL"},
        {"op": "resource_acquire", "as": "r1", "key": "playback", "job": "a", "owner": "L1", "amount": 2},
        {"op": "resource_acquire", "as": "r2", "key": "disk", "job": "b", "owner": "L1", "class": "IO", "type": "DISK", "amount": 1.5, "lease_seconds": 3},
        {"op": "advance", "seconds": 1},
        {"op": "acquire_execution", "job": "a", "as": "e1", "worker": "w1", "lease_seconds": 45},
        {"op": "acquire_execution", "job": "a", "as": "e2", "worker": "w2", "lease_seconds": 45},
        {"op": "advance", "seconds": 10},
        {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "UPLOADING", "lease_seconds": 45},
        {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "", "lease_seconds": 1},
        {"op": "heartbeat", "job": "a", "execution": "nope", "worker_state": "RUNNING", "lease_seconds": 45},
        {"op": "snapshot"},
        {"op": "advance", "seconds": 50},
        {"op": "expired"},
        {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "RUNNING", "lease_seconds": 45},
        {"op": "expired"},
        {"op": "finish", "job": "a", "execution": "e1", "status": "COMPLETED", "failure_class": ""},
        {"op": "acquire_execution", "job": "a", "as": "e3", "worker": "w1", "lease_seconds": 2},
        {"op": "finish", "job": "a", "execution": "e3", "status": "FAILED", "failure_class": "NETWORK"},
        {"op": "acquire_execution", "job": "missing", "as": "x", "worker": "w1", "lease_seconds": 45},
    ]},
    "two reserved resources on one job": {"micros": 0, "ops": [
        # Python binds every RESERVED lease of the job to the one new lease id, and the unique index on lease_id refuses the second:
        # a job with two reserved resources cannot start. Recorded so that the Rust port does not quietly behave differently.
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "resource_acquire", "as": "r1", "key": "one", "job": "a", "owner": "L1", "amount": 1},
        {"op": "resource_acquire", "as": "r2", "key": "two", "job": "a", "owner": "L1", "amount": 1},
        {"op": "acquire_execution", "job": "a", "as": "e1", "worker": "w1", "lease_seconds": 45},
        {"op": "resource_snapshot"},
    ]},
    "fractions of a second": {"micros": 500000, "ops": [
        {"op": "create", "name": "a", "type": "METADATA_SYNC"},
        {"op": "resource_acquire", "as": "r1", "key": "k", "job": "a", "owner": "L1", "amount": 1},
        {"op": "acquire_execution", "job": "a", "as": "e1", "worker": "w1", "lease_seconds": 6},
        {"op": "lane_acquire", "job": "a", "execution": "e1", "lanes": ["net"], "caps": CAPS, "lease_id": None, "lease_seconds": 6},
        {"op": "advance", "seconds": 5},
        {"op": "expired"},
        {"op": "lane_expire"},
        {"op": "resource_expire"},
        {"op": "advance", "seconds": 1},
        {"op": "expired"},
        {"op": "lane_expire"},
        {"op": "resource_expire"},
        {"op": "snapshot"},
        {"op": "resource_snapshot"},
        {"op": "lane_snapshot", "caps": CAPS},
    ]},
    "interrupted by a new launch": {"micros": 0, "ops": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "create", "name": "b", "type": "MEDIA_MANAGER"},
        {"op": "resource_acquire", "as": "r1", "key": "k1", "job": "a", "owner": "L1", "amount": 1},
        {"op": "resource_acquire", "as": "r2", "key": "k2", "job": "b", "owner": "L1", "amount": 1},
        {"op": "advance", "seconds": 1},
        {"op": "acquire_execution", "job": "a", "as": "e1", "worker": "w1", "lease_seconds": 45},
        {"op": "lane_acquire", "job": "a", "execution": "e1", "lanes": ["cpu", "net"], "caps": CAPS, "lease_id": None, "lease_seconds": 45},
        {"op": "interrupt_stale", "job": "a"},
        {"op": "interrupt_stale", "job": "a"},
        {"op": "interrupt_stale", "job": "nobody"},
        {"op": "resource_invalidate", "owner": "L2"},
        {"op": "resource_invalidate", "owner": "L2"},
        {"op": "resource_snapshot"},
        {"op": "lane_snapshot", "caps": CAPS},
    ]},
    "admission": {"micros": 0, "ops": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "admission", "job": "a", "state": "waiting", "reason": "DEPENDENCY", "operation": "Waiting for dependencies"},
        {"op": "admission", "job": "a", "state": "waiting", "reason": "DEPENDENCY", "operation": "Waiting for dependencies"},
        {"op": "advance", "seconds": 5},
        {"op": "admission", "job": "a", "state": "delayed", "reason": "RETRY_DELAY", "next_run_at": "2031-03-04T05:07:00+00:00", "priority": 10, "detail": "later"},
        {"op": "admission", "job": "a", "state": "DELAYED", "reason": "RETRY_DELAY", "next_run_at": "2031-03-04T05:07:00+00:00", "priority": 10},
        {"op": "admission", "job": "a", "state": "blocked", "reason": "", "priority": 500, "operation": "x" * 400},
        {"op": "admission", "job": "a", "state": "", "reason": ""},
        {"op": "admission", "job": "a", "state": "nonsense", "reason": ""},
        {"op": "admission", "job": "missing", "state": "ready", "reason": ""},
        {"op": "retry_policy", "job": "a", "retry_count": 2, "max_retries": 5, "failure_class": "TIMEOUT", "next_run_at": "2031-03-04T05:08:00+00:00"},
        {"op": "retry_policy", "job": "a", "retry_count": -4},
        {"op": "retry_policy", "job": "a", "max_retries": -1, "failure_class": "é" * 100},
        {"op": "retry_policy", "job": "a", "next_run_at": "2031-03-04T05:09:00+00:00"},
        {"op": "retry_policy", "job": "a"},
        {"op": "create", "name": "b", "type": "METADATA_SYNC"},
        {"op": "create", "name": "c", "type": "METADATA_SYNC"},
        {"op": "admission", "job": "b", "state": "waiting", "reason": "DEPENDENCY"},
        {"op": "admission", "job": "c", "state": "ready", "reason": ""},
        # one thing at a time changes: the journal records an admission change only when something differs
        {"op": "create", "name": "d", "type": "UPLOAD_LOCAL"},
        {"op": "admission", "job": "d", "state": "ready", "reason": ""},
        {"op": "admission", "job": "d", "state": "ready", "reason": "", "priority": 50},
        {"op": "admission", "job": "d", "state": "ready", "reason": "", "priority": 51},
        {"op": "admission", "job": "d", "state": "ready", "reason": "", "priority": 51, "next_run_at": "2031-03-04T06:00:00+00:00"},
        {"op": "admission", "job": "d", "state": "ready", "reason": "", "priority": 51, "next_run_at": "2031-03-04T06:00:00+00:00", "operation": "only the operation"},
        {"op": "admission", "job": "d", "state": "ready", "reason": "why", "priority": 51, "next_run_at": "2031-03-04T06:00:00+00:00"},
        {"op": "admission", "job": "d", "state": "waiting", "reason": "why", "priority": 51, "next_run_at": "2031-03-04T06:00:00+00:00", "detail": "explained"},
        {"op": "snapshot"},
    ]},
    "lane slots": {"micros": 0, "ops": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "create", "name": "b", "type": "UPLOAD_LOCAL"},
        {"op": "create", "name": "c", "type": "UPLOAD_LOCAL"},
        {"op": "acquire_execution", "job": "a", "as": "e1", "worker": "w", "lease_seconds": 45},
        {"op": "acquire_execution", "job": "b", "as": "e2", "worker": "w", "lease_seconds": 45},
        {"op": "acquire_execution", "job": "c", "as": "e3", "worker": "w", "lease_seconds": 45},
        {"op": "lane_acquire", "job": "a", "execution": "e1", "lanes": ["cpu", "net", "net", ""], "caps": CAPS, "lease_id": None, "lease_seconds": 45},
        {"op": "lane_snapshot", "caps": CAPS},
        {"op": "lane_acquire", "job": "b", "execution": "e2", "lanes": ["net"], "caps": CAPS, "lease_id": "custom-lease", "lease_seconds": 3},
        {"op": "advance", "seconds": 1},
        {"op": "lane_acquire", "job": "c", "execution": "e3", "lanes": ["net"], "caps": CAPS, "lease_id": None, "lease_seconds": 45},
        {"op": "lane_acquire", "job": "c", "execution": "e3", "lanes": ["cpu"], "caps": CAPS, "lease_id": None, "lease_seconds": 45},
        {"op": "lane_acquire", "job": "c", "execution": "e3", "lanes": ["disk", "unlisted"], "caps": {"disk": 0}, "lease_id": None, "lease_seconds": 45},
        {"op": "lane_snapshot", "caps": CAPS},
        {"op": "advance", "seconds": 4},
        {"op": "lane_snapshot", "caps": CAPS},
        {"op": "lane_release", "execution": "e1", "state": "RELEASED"},
        {"op": "lane_release", "job": "c", "state": "DRAINED"},
        {"op": "lane_release", "state": ""},
        {"op": "lane_snapshot", "caps": CAPS},
        # a lane nobody listed has room for one
        {"op": "lane_acquire", "job": "a", "execution": "e1", "lanes": ["solo"], "caps": {}, "lease_id": None, "lease_seconds": 45},
        {"op": "lane_acquire", "job": "b", "execution": "e2", "lanes": ["solo"], "caps": {}, "lease_id": None, "lease_seconds": 45},
        {"op": "lane_acquire", "job": "b", "execution": "e2", "lanes": ["solo", "cpu"], "caps": {"solo": 2}, "lease_id": None, "lease_seconds": 45},
        {"op": "lane_snapshot", "caps": {"solo": 2, "cpu": 1}},
    ]},
    "resource leases": {"micros": 0, "ops": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL"},
        {"op": "resource_acquire", "as": "r1", "key": "k", "job": "a", "owner": "L1", "amount": 1},
        {"op": "advance", "seconds": 1},
        {"op": "resource_acquire", "as": "r2", "key": "k", "job": None, "owner": "L2", "class": "", "type": "", "amount": 0, "lease_seconds": 1},
        {"op": "advance", "seconds": 1},
        {"op": "resource_acquire", "as": "r3", "key": "other", "job": "a", "owner": "L2", "class": "PLAYBACK", "amount": 0.25},
        {"op": "resource_snapshot"},
        {"op": "advance", "seconds": 6},
        {"op": "resource_expire"},
        {"op": "resource_expire"},
        {"op": "resource_snapshot"},
    ]},
}


def install_clock():
    import telegram_archive.config as config
    clock = {"secs": BASE, "micros": 0}

    class FakeDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock["secs"], tz).replace(microsecond=clock["micros"])

    _dt.datetime = FakeDT
    config.datetime = FakeDT
    return clock


HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


def run(scenario):
    from telegram_archive.core.database import Database
    clock = install_clock()
    clock["micros"] = scenario["micros"]
    db = Database(Path(tempfile.mkdtemp()) / "a.db")
    jobs = {}   # random job id -> name
    other = {}  # execution / lease ids -> name

    def jid(name):
        return next((k for k, v in jobs.items() if v == name), name)

    def clean(text):
        if text is None or not isinstance(text, str):
            return text
        for k, v in jobs.items():
            text = text.replace(k, "<job:%s>" % v)
        for k, v in other.items():
            text = text.replace(k, v)
        return HEX32.sub("<slot>", text)

    def clean_value(v):
        if isinstance(v, str):
            return clean(v)
        if isinstance(v, dict):
            return {k: clean_value(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean_value(x) for x in v]
        return v

    def state():
        out = {}
        specs = {
            "jobs": ("id", lambda d: str(d["id"])),
            "job_journal": ("sequence", lambda d: (str(d["job_id"]), d["sequence"])),
            "job_executions": ("attempt", lambda d: (str(d["job_id"]), d["attempt"])),
            "resource_leases": ("resource_key", lambda d: str(d["resource_key"])),
            "execution_lane_slots": ("lane_id", lambda d: (str(d["lane_id"]), str(d["job_id"]), str(d["acquired_at"]), str(d["state"]), str(d["lease_id"]))),
        }
        for table, (_, sort_key) in specs.items():
            rows = [dict(r) for r in db.conn.execute(f"SELECT * FROM {table}").fetchall()]
            rows = [{k: clean_value(v) for k, v in r.items()} for r in rows]
            if table == "job_journal":
                for r in rows:
                    r.pop("id", None)
                    r["event_id"] = "<E>"
            if table == "execution_lane_slots":
                for r in rows:
                    r["slot_id"] = "<slot>"
            rows.sort(key=sort_key)
            out[table] = rows
        return out

    steps = []
    for op in scenario["ops"]:
        error, result = None, None
        kind = op["op"]
        try:
            if kind == "advance":
                clock["secs"] += op["seconds"]
            elif kind == "create":
                made = db.create_job(op["type"], "", "", False, "")
                jobs[made] = op["name"]
            elif kind == "acquire_execution":
                try:
                    got = db.acquire_job_execution(jid(op["job"]), worker_id=op["worker"], lease_seconds=op["lease_seconds"])
                    other[got["execution_id"]] = "<exec:%s>" % op["as"]
                    other[got["lease_id"]] = "<lease:%s>" % op["as"]
                    result = clean_value(got)
                finally:
                    pass
            elif kind == "heartbeat":
                ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), op["execution"])
                result = db.heartbeat_job_execution(jid(op["job"]), ex, worker_state=op["worker_state"], lease_seconds=op["lease_seconds"])
            elif kind == "finish":
                ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), op["execution"])
                db.finish_job_execution(jid(op["job"]), ex, status=op["status"], failure_class=op["failure_class"])
            elif kind == "interrupt_stale":
                result = clean_value(db.interrupt_stale_job_execution(jid(op["job"])))
            elif kind == "expired":
                result = clean_value([dict(r) for r in db.expired_job_executions()])
            elif kind == "retry_policy":
                kw = {k: op[k] for k in ("retry_count", "max_retries", "failure_class", "next_run_at") if k in op}
                db.set_job_retry_policy(jid(op["job"]), **kw)
            elif kind == "admission":
                kw = {k: op[k] for k in ("reason", "next_run_at", "priority", "operation", "detail") if k in op}
                db.set_job_admission(jid(op["job"]), op["state"], **kw)
            elif kind == "snapshot":
                result = clean_value(db.job_orchestration_snapshot())
            elif kind == "lane_acquire":
                ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), op["execution"])
                result = db.acquire_execution_lane_slots(jid(op["job"]), ex, op["lanes"], capacities=op["caps"], lease_id=op["lease_id"], lease_seconds=op["lease_seconds"])
            elif kind == "lane_release":
                ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), None) if op.get("execution") else None
                result = db.release_execution_lane_slots(execution_id=ex, job_id=jid(op["job"]) if op.get("job") else None, state=op["state"])
            elif kind == "lane_expire":
                result = db.expire_execution_lane_slots()
            elif kind == "lane_snapshot":
                result = clean_value(db.execution_lane_snapshot(op["caps"]))
            elif kind == "resource_acquire":
                kw = {"owner_launch_id": op["owner"], "reserved_amount": op["amount"]}
                if "class" in op:
                    kw["resource_class"] = op["class"]
                if "type" in op:
                    kw["resource_type"] = op["type"]
                if "lease_seconds" in op:
                    kw["lease_seconds"] = op["lease_seconds"]
                got = db.resource_lease_acquire(op["key"], jid(op["job"]), **kw) if False else db.acquire_resource_lease(op["key"], jid(op["job"]) if op["job"] else None, **kw)
                other[got["lease_id"]] = "<lease:%s>" % op["as"]
                result = clean_value(got)
            elif kind == "resource_invalidate":
                result = db.invalidate_stale_resource_leases(op["owner"])
            elif kind == "resource_expire":
                result = clean_value([dict(r) for r in db.expire_resource_leases()])
            elif kind == "resource_snapshot":
                result = clean_value(db.resource_lease_snapshot())
            else:
                raise AssertionError(kind)
        except Exception as exc:
            error = clean(str(exc))
            try:
                db.conn.rollback()
            except Exception:
                pass
        steps.append({"op": op, "clock": {"secs": clock["secs"], "micros": clock["micros"]}, "error": error, "result": result, "state": state()})
    return {"micros": scenario["micros"], "base": BASE, "steps": steps}


def build():
    return {name: run(sc) for name, sc in SCENARIOS.items()}


def main():
    text = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.relative_to(ROOT)} is out of date: run python tools/parity/gen_lease_op_vectors.py")
        print("lease operation vectors are up to date")
        return
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), f"({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
