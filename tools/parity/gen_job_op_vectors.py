"""Record what the Python database layer does for sequences of job operations, for the Rust port in crates/ax-db/src/jobs.rs to repeat.

    python tools/parity/gen_job_op_vectors.py          # rewrite crates/ax-db/tests/job_ops_cases.json
    python tools/parity/gen_job_op_vectors.py --check  # fail if the file is out of date

Each scenario is a list of operations (`create`, `transition`, `update`, `journal`, `depends`) on a fresh database. Jobs are named by the
scenario ("a", "b"), not by their random ids. After every operation the recorded state is the error text (if Python raised) and the rows of
`jobs`, `job_journal` and `job_dependencies` with random ids replaced by the names and times by "<T>", so Rust's replay can be compared exactly.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-db" / "tests" / "job_ops_cases.json"

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")

SCENARIOS = {
    "create and read": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL", "args": {"source": "src", "destination": "dst", "detail": "{\"x\": 1}", "dry_run": True, "destructive": True, "priority": 30, "max_retries": 5, "resource_requirements": {"playback_compatible": True, "é": "ü"}}},
        {"op": "create", "name": "b", "type": "METADATA_SYNC", "args": {}},
        {"op": "create", "name": "c", "type": "BACKUP_CREATE", "args": {"priority": 500, "max_retries": -2, "deadline_at": "2030-01-01T00:00:00+00:00", "resource_requirements": {}}},
        {"op": "create", "name": "d", "type": "MEDIA_MANAGER", "args": {"priority": -9, "detail": None}},
    ],
    "lifecycle": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL", "args": {}},
        {"op": "transition", "job": "a", "status": "running", "fields": {"current_operation": "Starting", "progress": 0.5, "completed": 2, "total": 10}},
        {"op": "transition", "job": "a", "status": "PAUSING", "fields": {}},
        {"op": "transition", "job": "a", "status": "PAUSED", "fields": {"detail": "paused by operator"}},
        {"op": "transition", "job": "a", "status": "COMPLETED", "fields": {}},
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
        {"op": "transition", "job": "a", "status": "QUEUED", "fields": {}},
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
        {"op": "transition", "job": "a", "status": "FAILED", "fields": {"error": "boom", "current_operation": "Failed"}},
        {"op": "transition", "job": "a", "status": "QUEUED", "fields": {"error": ""}},
        {"op": "transition", "job": "a", "status": "CANCELLED", "fields": {}},
    ],
    "refused transitions": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL", "args": {}},
        {"op": "transition", "job": "a", "status": "COMPLETED", "fields": {}},
        {"op": "transition", "job": "a", "status": "NONSENSE", "fields": {}},
        {"op": "transition", "job": "a", "status": "", "fields": {}},
        {"op": "transition", "job": "missing", "status": "RUNNING", "fields": {}},
        {"op": "transition", "job": "a", "status": "COMPLETED", "fields": {"error": 7, "detail": "d", "current_operation": None}},
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
    ],
    "plain updates": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL", "args": {}},
        {"op": "update", "job": "a", "fields": {"progress": 0.25, "current_item": "file.mp4", "completed": 1, "total": 4}},
        {"op": "update", "job": "a", "fields": {"status": "RUNNING", "current_operation": "go"}},
        {"op": "update", "job": "a", "fields": {"status": "running"}},
        {"op": "update", "job": "a", "fields": {"status": "COMPLETED", "error": ""}},
        {"op": "update", "job": "a", "fields": {"status": "QUEUED"}},
        {"op": "update", "job": "a", "fields": {"error": None, "next_run_at": "2030-05-05T05:05:05+00:00"}},
    ],
    "journal": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL", "args": {}},
        {"op": "journal", "job": "a", "event": "CUSTOM", "kwargs": {"status": "RUNNING", "from_status": "QUEUED", "checkpoint": {"n": 1, "list": [1, 2]}, "operation": "op", "detail": "d", "payload": {"é": "ü", "a": 1.5}, "error_type": "ValueError", "retry_count": 2, "launch_id": "L1", "created_at": "2030-01-01T00:00:00+00:00"}},
        {"op": "journal", "job": "a", "event": "BARE", "kwargs": {}},
        {"op": "journal", "job": "a", "event": "UNICODE", "kwargs": {"payload": {"k": "line\nbreak \"quoted\" \u001f"}}},
    ],
    "dependencies": [
        {"op": "create", "name": "a", "type": "UPLOAD_LOCAL", "args": {}},
        {"op": "create", "name": "b", "type": "METADATA_SYNC", "args": {"depends_on": ["a"]}},
        {"op": "create", "name": "c", "type": "METADATA_SYNC", "args": {"depends_on": ["a", "b", "a"]}},
        {"op": "depends", "job": "a", "on": ["c"], "type": "success"},
        {"op": "depends", "job": "a", "on": ["a"], "type": "SUCCESS"},
        {"op": "depends", "job": "a", "on": [], "type": "SUCCESS"},
        {"op": "depends", "job": "b", "on": ["nobody"], "type": "SUCCESS"},
        {"op": "create", "name": "d", "type": "MEDIA_MANAGER", "args": {"depends_on": ["ghost"]}},
        {"op": "depends", "job": "c", "on": ["a"], "type": "ANY"},
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
    ],
}


def normalise(db, names):
    """The rows of the three tables with ids and times replaced; `names` maps random job ids to the scenario's names."""
    def clean(value, key=None):
        if isinstance(value, str):
            if value in names:
                return "<job:%s>" % names[value]
            if ISO.match(value) and key not in {"deadline_at", "next_run_at"}:
                return "<T>"
            return value
        return value

    def clean_json(text):
        if text is None:
            return None
        out = text
        for job_id, name in names.items():
            out = out.replace(job_id, "<job:%s>" % name)
        return out

    state = {}
    for table, order in (("jobs", "created_at, id"), ("job_journal", "job_id, sequence"), ("job_dependencies", "job_id, depends_on_job_id")):
        rows = db.conn.execute(f"SELECT * FROM {table}").fetchall()
        cooked = []
        for r in rows:
            d = {k: clean(r[k], k) for k in r.keys()}
            if table == "job_journal":
                d.pop("id", None)
                d["event_id"] = "<E>"
                d["payload"] = clean_json(d["payload"])
            cooked.append(d)
        if table == "jobs":
            cooked.sort(key=lambda d: str(d["id"]))
        elif table == "job_journal":
            cooked.sort(key=lambda d: (str(d["job_id"]), d["sequence"]))
        else:
            cooked.sort(key=lambda d: (str(d["job_id"]), str(d["depends_on_job_id"])))
        state[table] = cooked
    return state


def run(ops):
    from telegram_archive.core.database import Database
    db = Database(Path(tempfile.mkdtemp()) / "a.db")
    names = {}  # random id -> name
    steps = []
    for op in ops:
        error = None
        try:
            if op["op"] == "create":
                args = dict(op["args"])
                depends = [next((k for k, v in names.items() if v == n), n) for n in args.pop("depends_on", []) or []] if "depends_on" in op["args"] else None
                try:
                    jid = db.create_job(op["type"], args.pop("source", ""), args.pop("destination", ""), args.pop("destructive", False), args.pop("detail", ""), args.pop("dry_run", False), depends_on=depends, **args)
                finally:
                    # Python commits the job before it looks at the dependencies, so a refused dependency leaves the job behind
                    for (row,) in db.conn.execute("SELECT id FROM jobs").fetchall():
                        if row not in names:
                            names[row] = op["name"]
            else:
                ref = next((k for k, v in names.items() if v == op["job"]), op["job"])
                if op["op"] == "transition":
                    db.transition_job(ref, op["status"], **op["fields"])
                elif op["op"] == "update":
                    db.update_job(ref, **op["fields"])
                elif op["op"] == "journal":
                    db.append_job_journal(ref, op["event"], **op["kwargs"])
                elif op["op"] == "depends":
                    on = [next((k for k, v in names.items() if v == n), n) for n in op["on"]]
                    db.add_job_dependencies(ref, on, op["type"])
        except Exception as exc:  # recorded, not hidden: the Rust port has to refuse the same things with the same words
            error = str(exc)
            for job_id, name in names.items():
                error = error.replace(job_id, "<job:%s>" % name)
            error = re.sub(r"\b[0-9a-f]{12}\b", "<job:?>", error)
            try:
                db.conn.rollback()
            except Exception:
                pass
        steps.append({"op": op, "error": error, "state": normalise(db, names)})
    return {"ops": ops, "steps": steps, "names": sorted(names.values())}


def build():
    return {name: run(ops) for name, ops in SCENARIOS.items()}


def main():
    text = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if "--check" in sys.argv:
        # job ids and times are replaced, so the file is stable between runs
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.relative_to(ROOT)} is out of date: run python tools/parity/gen_job_op_vectors.py")
        print("job operation vectors are up to date")
        return
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), f"({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
