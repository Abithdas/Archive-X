"""Record what Python's `JobOrchestrator` decides and writes, on a fake clock, against a real `Database`.

    python tools/parity/gen_orchestrator_vectors.py          # rewrite crates/ax-jobs/tests/orchestrator_cases.json
    python tools/parity/gen_orchestrator_vectors.py --check  # fail if the file is out of date

Each scenario is a list of operations. The manager the orchestrator asks is a small fake whose answers (running, paused and cancelled jobs, the
lanes and resource category of a job type) the scenario sets; the resource manager and the governor are the real classes the Rust port also
has, driven by the same recorded operations. Time is fake for the orchestrator, the database, the resource manager and the governor alike.
After every operation the result (or error text) and the rows of six tables are recorded; `crates/ax-jobs/tests/orchestrator.rs` replays them.
"""
import asyncio
import datetime as _dt
import json
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-jobs" / "tests" / "orchestrator_cases.json"

BASE = 1_930_713_967  # 2031-03-08T05:26:07Z
MONO0 = 5_000.0
MID = {"background_capacity": 3, "media_workers": 2}

LANES_BY_TYPE = {"UPLOAD_LOCAL": ["net"], "TELEGRAM_TRANSFER": ["net", "disk"], "METADATA_SYNC": ["cpu"], "THUMBNAIL_INDEXER": ["cpu"], "PREVIEW_PREFETCH": ["disk"], "NO_LANES": []}
CATEGORY_BY_TYPE = {"UPLOAD_LOCAL": "upload", "TELEGRAM_TRANSFER": "upload", "METADATA_SYNC": "metadata", "THUMBNAIL_INDEXER": "thumbnail", "PREVIEW_PREFETCH": "preview", "NO_LANES": "generic"}
CAPS = {"net": 1, "disk": 2, "cpu": 2}


def j(name, type_="UPLOAD_LOCAL"):
    return {"op": "create", "name": name, "type": type_}


def mgr(**kw):
    return {"op": "mgr", **kw}


def prep(name):
    return {"op": "prepare", "job": name}


SCENARIOS = {
    "plain gates": {"resources": False, "governor": False, "ops": [
        j("a"), prep("a"), prep("missing"),
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}}, prep("a"),
        j("b"), mgr(running=["b"]), prep("b"), mgr(running=[]),
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57+00:00"}, prep("b"),
        {"op": "advance", "seconds": 49, "mono": 0}, prep("b"), {"op": "advance", "seconds": 1, "mono": 0}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57.000001+00:00"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "garbage"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T07:27:00+02:00"}, prep("b"), {"op": "next_run", "job": "b", "value": "2031-03-08 05:27:00Z"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:27:00"}, prep("b"), {"op": "next_run", "job": "b", "value": "2031-03-05"}, prep("b"),
        {"op": "next_run", "job": "b", "value": ""}, prep("b"), {"op": "next_run", "job": "b", "value": None},
        {"op": "advance", "seconds": 0, "mono": 0, "micros": 500000},
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:27:00.500000+00:00"}, prep("b"),
        {"op": "advance", "seconds": 0, "mono": 0, "micros": 0},
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57+00:00"}, prep("b"),
        {"op": "advance", "seconds": 0, "mono": 0, "micros": 500000},
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57.500000+00:00"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57.500001+00:00"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57.499999+00:00"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57.5+00:00"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T03:26:58-02:00"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T03:26:57-02:00"}, prep("b"),
        {"op": "next_run", "job": "b", "value": "2031-03-08T05:26:57.500000Z"}, prep("b"),
        {"op": "advance", "seconds": 0, "mono": 0, "micros": 0}, {"op": "next_run", "job": "b", "value": None},
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-08T05:26:57+00:00"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-08T05:26:58Z"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-08T05:26:58"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-08 05:26:57.000001"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-08T07:26:57+02:00"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-08T07:26:58+02:00"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-08"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2031-03-05"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2030-02-30T00:00:00"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "1900-02-29T00:00:00"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": "2000-02-29T00:00:00"}}, prep("b"),
        {"op": "set_fields", "job": "b", "fields": {"deadline_at": None}},
        mgr(cancelled=["b"]), prep("b"), mgr(cancelled=[], paused_jobs=["b"]), prep("b"), mgr(paused_jobs=[], paused=True), prep("b"), mgr(paused=False), prep("b"),
    ]},
    "dependencies": {"resources": False, "governor": False, "ops": [
        j("a"), j("b"), j("c"), j("d"), {"op": "depend", "job": "d", "on": ["a", "b"], "type": "SUCCESS"},
        {"op": "advance", "seconds": 1, "mono": 0}, {"op": "depend", "job": "d", "on": ["c"], "type": "success"}, prep("d"),
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}}, {"op": "transition", "job": "a", "status": "COMPLETED", "fields": {}}, prep("d"),
        {"op": "transition", "job": "b", "status": "RUNNING", "fields": {}}, {"op": "transition", "job": "b", "status": "FAILED", "fields": {}}, prep("d"),
        j("e"), {"op": "depend", "job": "e", "on": ["b"], "type": "COMPLETION"}, prep("e"),
        j("f"), {"op": "depend", "job": "f", "on": ["a"], "type": "SUCCESS"}, prep("f"),
        {"op": "transition", "job": "c", "status": "RUNNING", "fields": {}}, {"op": "transition", "job": "c", "status": "CANCELLED", "fields": {}}, prep("d"),
        j("g"), j("h"), {"op": "depend", "job": "g", "on": ["h"], "type": "SUCCESS"}, {"op": "transition", "job": "h", "status": "RUNNING", "fields": {}},
        {"op": "transition", "job": "h", "status": "COMPLETED_WITH_ERRORS", "fields": {}}, prep("g"),
    ]},
    "lanes": {"resources": False, "governor": False, "ops": [
        j("a", "UPLOAD_LOCAL"), j("b", "UPLOAD_LOCAL"), j("c", "TELEGRAM_TRANSFER"), j("d", "NO_LANES"), j("m", "METADATA_SYNC"),
        prep("a"), {"op": "start", "job": "a", "as": "e1"}, {"op": "lane_acquire", "job": "a", "execution": "e1", "lanes": ["net"]},
        prep("b"), prep("c"), prep("d"),
        {"op": "set_fields", "job": "b", "fields": {"created_at": "2031-03-08T04:00:00+00:00"}}, prep("b"), {"op": "eff", "job": "b"}, {"op": "eff", "job": "c"},
        {"op": "set_fields", "job": "b", "fields": {"created_at": "2031-03-08T05:26:07+00:00"}}, {"op": "eff", "job": "b"},
        {"op": "set_fields", "job": "b", "fields": {"created_at": "2031-03-08T05:26:08+00:00"}}, {"op": "eff", "job": "b"},
        {"op": "set_fields", "job": "b", "fields": {"created_at": "garbage"}}, {"op": "eff", "job": "b"},
        {"op": "set_fields", "job": "b", "fields": {"created_at": "2031-03-07T05:26:07+00:00"}}, {"op": "eff", "job": "b"},
        {"op": "set_fields", "job": "b", "fields": {"priority": 3, "created_at": "2031-03-07T05:26:07+00:00"}}, {"op": "eff", "job": "b"},
        {"op": "set_fields", "job": "b", "fields": {"priority": 90, "created_at": "2031-03-08T05:00:00+00:00"}}, {"op": "eff", "job": "b"}, prep("b"),
        mgr(lane_drain=True), prep("b"), prep("d"), mgr(lane_drain=False),
        {"op": "lane_acquire", "job": "m", "execution": "e1", "lanes": ["cpu"]}, prep("m"),
        {"op": "advance", "seconds": 1, "mono": 0}, {"op": "lane_release", "execution": "e1"}, prep("b"), prep("c"),
    ]},
    "resources": {"resources": True, "governor": False, "ops": [
        j("up", "UPLOAD_LOCAL"), j("md", "METADATA_SYNC"), j("th", "THUMBNAIL_INDEXER"), j("pv", "PREVIEW_PREFETCH"), j("ge", "NO_LANES"),
        prep("up"), prep("md"), prep("th"),
        {"op": "res", "call": "pause", "reason": "stop"}, prep("up"), {"op": "res", "call": "resume"},
        {"op": "res", "call": "p_enter"}, prep("up"), prep("th"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "{\"playback_compatible\": true}"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "{\"playback_compatible\": false}"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "{\"other\": 1}"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "[1, 2]"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "7"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "not json"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "null"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "{\"playback_compatible\": 1}"}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "\"text\""}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "\"[1, 2]\""}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "\"{\\\"playback_compatible\\\": true}\""}}, prep("up"),
        {"op": "set_fields", "job": "up", "fields": {"resource_requirements": "{}"}}, prep("up"),
        {"op": "res", "call": "p_exit"}, prep("up"),
        {"op": "res", "call": "configure", "mode": "balanced"}, {"op": "res", "call": "p_enter"}, prep("up"), {"op": "res", "call": "p_exit"},
        {"op": "res", "call": "take", "category": "upload"}, {"op": "res", "call": "take", "category": "upload"}, prep("up"),
        {"op": "res", "call": "take", "category": "upload"}, prep("up"), prep("md"), prep("ge"),
        {"op": "res", "call": "release", "category": "upload"}, {"op": "res", "call": "release", "category": "upload"}, {"op": "res", "call": "release", "category": "upload"},
        {"op": "res", "call": "take", "category": "metadata"}, prep("md"), prep("up"), prep("pv"),
        {"op": "res", "call": "take", "category": "preview"}, prep("pv"),
        {"op": "res", "call": "configure", "mode": "manual", "limits": {"thumbnail": 1}, "playback_gate": False}, {"op": "res", "call": "take", "category": "thumbnail"}, prep("th"),
        {"op": "res", "call": "release", "category": "thumbnail"}, {"op": "res", "call": "release", "category": "preview"}, {"op": "res", "call": "release", "category": "metadata"},
        {"op": "res", "call": "configure", "mode": "manual", "limits": {"upload": 1, "metadata": 1}, "playback_gate": False},
        {"op": "res", "call": "take", "category": "upload"}, {"op": "res", "call": "snapshot"}, prep("up"), {"op": "res", "call": "release", "category": "upload"},
        {"op": "res", "call": "take", "category": "metadata"}, prep("md"), prep("up"),
    ]},
    "governor": {"resources": False, "governor": True, "ops": [
        j("up", "UPLOAD_LOCAL"), j("tr", "TELEGRAM_TRANSFER"), j("md", "METADATA_SYNC"), j("mm", "MEDIA_MANAGER"),
        prep("up"), {"op": "gov", "call": "floodwait", "seconds": 30, "kind": "rpc"}, prep("up"), prep("md"), prep("mm"), {"op": "advance", "seconds": 31, "mono": 31}, prep("up"),
        {"op": "gov", "call": "neterr", "message": "net down"}, prep("tr"), {"op": "advance", "seconds": 6, "mono": 6}, prep("tr"),
        {"op": "gov", "call": "transfer", "active": 3}, prep("up"), prep("md"), prep("mm"), {"op": "gov", "call": "transfer", "active": 2}, prep("up"),
        {"op": "gov", "call": "floodwait", "seconds": 0.4, "kind": "rpc"}, prep("up"),
    ]},
    "both": {"resources": True, "governor": True, "ops": [
        j("up", "UPLOAD_LOCAL"), j("md", "METADATA_SYNC"), {"op": "gov", "call": "floodwait", "seconds": 30, "kind": "range"}, {"op": "res", "call": "pause", "reason": "r"}, prep("up"),
        {"op": "res", "call": "resume"}, prep("up"), prep("md"), {"op": "advance", "seconds": 31, "mono": 31}, prep("up"),
    ]},
    "waiting and heartbeats": {"resources": False, "governor": False, "ops": [
        j("a"), {"op": "waiting", "job": "a", "reason": "SOMETHING", "operation": "Waiting", "retry_after": None},
        {"op": "waiting", "job": "a", "reason": "LATER", "operation": "Waiting", "retry_after": 2.5},
        {"op": "waiting", "job": "a", "reason": "SOON", "operation": "Waiting", "retry_after": 0.01},
        {"op": "waiting", "job": "a", "reason": "ODD", "operation": "Waiting", "retry_after": 0.0000005},
        {"op": "ready", "job": "a"}, {"op": "ready", "job": "a"},
        {"op": "start", "job": "a", "as": "e1"}, {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "RUNNING"},
        {"op": "advance", "seconds": 1, "mono": 1}, {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "UPLOADING"},
        {"op": "advance", "seconds": 1, "mono": 1}, {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "UPLOADING"},
        {"op": "advance", "seconds": 0, "mono": 0.25}, {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "RUNNING"},
        {"op": "advance", "seconds": 3, "mono": 3}, {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "RUNNING"},
        {"op": "heartbeat", "job": "a", "execution": "nope", "worker_state": "RUNNING"},
        {"op": "finish", "job": "a", "execution": "e1", "status": "COMPLETED", "failure_class": ""}, {"op": "heartbeat", "job": "a", "execution": "e1", "worker_state": "RUNNING"},
        {"op": "snapshot"},
    ]},
    "failures": {"resources": False, "governor": False, "ops": [
        j("a"), {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
        {"op": "fail", "job": "a", "cls": "OSError", "msg": "x" * 1500, "jitter": 13},
        {"op": "advance", "seconds": 600, "mono": 0}, {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
        {"op": "fail", "job": "a", "cls": "TimeoutError", "msg": "slow", "jitter": 7}, {"op": "snapshot"},
        {"op": "advance", "seconds": 600, "mono": 0}, {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
        {"op": "fail", "job": "a", "cls": "ConnectionError", "msg": "connection reset", "jitter": 19},
        {"op": "advance", "seconds": 600, "mono": 0}, {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}},
        {"op": "fail", "job": "a", "cls": "FloodWaitError", "msg": "A wait of 5 seconds is required", "jitter": 0},
        j("b"), {"op": "transition", "job": "b", "status": "RUNNING", "fields": {}},
        {"op": "fail", "job": "b", "cls": "ValueError", "msg": "bad input", "jitter": 3}, {"op": "fail", "job": "b", "cls": "KeyError", "msg": "k", "jitter": 3},
        {"op": "fail", "job": "b", "cls": "TypeError", "msg": "t", "jitter": 3}, {"op": "fail", "job": "b", "cls": "RuntimeError", "msg": "no capacity left", "jitter": 3},
        {"op": "fail", "job": "b", "cls": "RuntimeError", "msg": "boom", "jitter": 3}, {"op": "fail", "job": "b", "cls": "ReadTimeout", "msg": "", "jitter": 3},
        {"op": "fail", "job": "b", "cls": "RuntimeError", "msg": "operation timed out", "jitter": 3},
        {"op": "fail", "job": "missing", "cls": "TimeoutError", "msg": "x", "jitter": 3},
        j("c"), {"op": "set_fields", "job": "c", "fields": {"max_retries": 0}}, {"op": "transition", "job": "c", "status": "RUNNING", "fields": {}},
        {"op": "fail", "job": "c", "cls": "TimeoutError", "msg": "x", "jitter": 3},
        j("d"), {"op": "set_fields", "job": "d", "fields": {"max_retries": 12, "retry_count": 9}}, {"op": "transition", "job": "d", "status": "RUNNING", "fields": {}},
        {"op": "fail", "job": "d", "cls": "TimeoutError", "msg": "x", "jitter": 9},
    ]},
    "recovery": {"resources": False, "governor": False, "ops": [
        j("a"), j("b"), j("c"), {"op": "start", "job": "a", "as": "e1"}, {"op": "start", "job": "b", "as": "e2"}, {"op": "start", "job": "c", "as": "e3"},
        {"op": "lane_acquire", "job": "a", "execution": "e1", "lanes": ["net"]},
        {"op": "transition", "job": "a", "status": "RUNNING", "fields": {}}, {"op": "transition", "job": "b", "status": "RUNNING", "fields": {}},
        {"op": "snapshot"}, {"op": "recover"}, {"op": "advance", "seconds": 40, "mono": 0},
        {"op": "heartbeat", "job": "b", "execution": "e2", "worker_state": "RUNNING"}, {"op": "advance", "seconds": 10, "mono": 0}, {"op": "snapshot"}, {"op": "recover"}, {"op": "snapshot"},
        {"op": "recover"}, {"op": "advance", "seconds": 100, "mono": 0}, {"op": "recover"}, {"op": "snapshot"},
    ]},
}


class FakeDb:
    def __init__(self):
        self.lines = []

    def log(self, message, level="INFO"):
        self.lines.append([level, message])


class Task:
    def done(self):
        return False


HEX32 = re.compile(r"\b[0-9a-f]{32}\b")


def run(scenario):
    import asyncio as _asyncio
    import telegram_archive.config as config
    clock = {"secs": BASE, "micros": 0, "mono": MONO0}

    class FakeDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock["secs"], tz).replace(microsecond=clock["micros"])

    _dt.datetime = FakeDT
    config.datetime = FakeDT
    from telegram_archive.core.database import Database
    from telegram_archive.core import governor as gov_module, resource_manager as res_module
    from telegram_archive.jobs import orchestrator as module
    module.datetime = FakeDT
    fake_time = types.SimpleNamespace(time=lambda: clock["secs"] + clock["micros"] / 1e6, monotonic=lambda: clock["mono"], sleep=lambda _: (_ for _ in ()).throw(RuntimeError("sleep")))
    module.time = fake_time
    gov_module.time = fake_time
    res_module.time = fake_time
    res_module.os = types.SimpleNamespace(cpu_count=lambda: 4)
    db = Database(Path(tempfile.mkdtemp()) / "a.db")
    jobs, other = {}, {}

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
        if isinstance(v, (list, tuple)):
            return [clean_value(x) for x in v]
        return v

    def state():
        out = {}
        specs = {
            "jobs": lambda d: str(d["id"]),
            "job_journal": lambda d: (str(d["job_id"]), d["sequence"]),
            "job_executions": lambda d: (str(d["job_id"]), d["attempt"]),
            "resource_leases": lambda d: str(d["resource_key"]),
            "execution_lane_slots": lambda d: (str(d["lane_id"]), str(d["job_id"]), str(d["acquired_at"]), str(d["state"]), str(d["lease_id"])),
        }
        for table, sort_key in specs.items():
            rows = [{k: clean_value(v) for k, v in dict(r).items()} for r in db.conn.execute(f"SELECT * FROM {table}").fetchall()]
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

    class Manager:
        worker_id = "archive-x-worker"

        def __init__(self):
            self.running, self._cancelled, self._paused_jobs, self.paused, self._lane_drain = {}, set(), set(), False, False
            if scenario["resources"]:
                self.resources = res_module.InteractiveResourceManager(hardware_profile=dict(MID))
            if scenario["governor"]:
                self.governor = gov_module.TransferGovernor(FakeDb())

        def execution_lane_keys(self, job):
            return list(LANES_BY_TYPE.get(job["job_type"], []))

        def execution_lane_capacities(self, lanes):
            return {l: CAPS.get(l, 1) for l in lanes}

        def job_resource_category(self, job):
            return CATEGORY_BY_TYPE.get(job["job_type"], "generic")

    manager = Manager()
    orch = module.JobOrchestrator(db, manager)

    async def go():
        steps = []
        previous = {}
        for op in scenario["ops"]:
            error, result = None, None
            kind = op["op"]
            op = dict(op)
            try:
                if kind == "advance":
                    clock["secs"] += op["seconds"]
                    clock["mono"] += op["mono"]
                    if "micros" in op:
                        clock["micros"] = op["micros"]
                elif kind == "create":
                    made = db.create_job(op["type"], "", "", False, "")
                    jobs[made] = op["name"]
                elif kind == "set_fields":
                    db.update_job(jid(op["job"]), **op["fields"])
                elif kind == "next_run":
                    db.set_job_retry_policy(jid(op["job"]), next_run_at=op["value"])
                elif kind == "depend":
                    db.add_job_dependencies(jid(op["job"]), [jid(n) for n in op["on"]], dependency_type=op["type"])
                elif kind == "transition":
                    db.transition_job(jid(op["job"]), op["status"], **op["fields"])
                elif kind == "mgr":
                    if "running" in op:
                        manager.running = {jid(n): Task() for n in op["running"]}
                    if "cancelled" in op:
                        manager._cancelled = {jid(n) for n in op["cancelled"]}
                    if "paused_jobs" in op:
                        manager._paused_jobs = {jid(n) for n in op["paused_jobs"]}
                    if "paused" in op:
                        manager.paused = op["paused"]
                    if "lane_drain" in op:
                        manager._lane_drain = op["lane_drain"]
                elif kind == "prepare":
                    result = list(orch.prepare(jid(op["job"])))
                elif kind == "waiting":
                    orch.waiting(jid(op["job"]), op["reason"], operation=op["operation"], retry_after=op["retry_after"])
                elif kind == "ready":
                    orch.ready(jid(op["job"]))
                elif kind == "start":
                    got = orch.start_execution(jid(op["job"]))
                    other[got["execution_id"]] = "<exec:%s>" % op["as"]
                    other[got["lease_id"]] = "<lease:%s>" % op["as"]
                    result = clean_value(got)
                elif kind == "lane_acquire":
                    ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), op["execution"])
                    result = db.acquire_execution_lane_slots(jid(op["job"]), ex, op["lanes"], capacities=CAPS, lease_id=None, lease_seconds=45)
                elif kind == "lane_release":
                    ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), None)
                    result = db.release_execution_lane_slots(execution_id=ex, job_id=None, state="RELEASED")
                elif kind == "heartbeat":
                    ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), op["execution"])
                    result = orch.heartbeat_execution(jid(op["job"]), ex, worker_state=op["worker_state"])
                elif kind == "finish":
                    ex = next((k for k, v in other.items() if v == "<exec:%s>" % op["execution"]), op["execution"])
                    orch.finish_execution(jid(op["job"]), ex, status=op["status"], failure_class=op["failure_class"])
                elif kind == "fail":
                    classes = {"TimeoutError": TimeoutError, "ConnectionError": ConnectionError, "OSError": OSError, "ValueError": ValueError, "KeyError": KeyError,
                               "TypeError": TypeError, "RuntimeError": RuntimeError}
                    exc = classes.get(op["cls"]) or type(op["cls"], (Exception,), {})
                    exc = exc(op["msg"])
                    op["type_name"] = type(exc).__name__
                    op["text"] = str(exc)
                    op["is_timeout"] = isinstance(exc, (TimeoutError, _asyncio.TimeoutError))
                    op["is_os_error"] = isinstance(exc, (ConnectionError, OSError))
                    op["is_input_error"] = isinstance(exc, (ValueError, KeyError, TypeError))
                    module.hash = lambda s, jitter=op["jitter"]: jitter
                    result = list(orch.schedule_failure(jid(op["job"]), exc))
                elif kind == "recover":
                    result = clean_value(orch.recover_expired_leases())
                elif kind == "snapshot":
                    result = clean_value(orch.snapshot())
                elif kind == "eff":
                    result = orch.effective_priority(db.get_job(jid(op["job"])))
                elif kind == "res":
                    r = manager.resources
                    call = op["call"]
                    if call == "pause":
                        r.pause_background(op["reason"])
                    elif call == "resume":
                        r.resume_background()
                    elif call == "p_enter":
                        await r.playback_enter()
                    elif call == "p_exit":
                        await r.playback_exit()
                    elif call == "configure":
                        r.configure(mode=op.get("mode"), limits=op.get("limits"), playback_gate=op.get("playback_gate"), label="")
                    elif call == "take":
                        await asyncio.wait_for(r.acquire_background(op["category"]), 0.5)
                    elif call == "release":
                        await r.release_background(op["category"])
                    elif call == "snapshot":
                        result = r.snapshot()
                    else:
                        raise AssertionError(call)
                elif kind == "gov":
                    g = manager.governor
                    call = op["call"]
                    if call == "floodwait":
                        g.note_floodwait(op["seconds"], op["kind"], None)
                    elif call == "neterr":
                        g.note_network_error(op["message"])
                    elif call == "transfer":
                        g._active_transfer_slots = op["active"]
                    else:
                        raise AssertionError(call)
                else:
                    raise AssertionError(kind)
            except Exception as exc:
                error = clean(str(exc))
                try:
                    db.conn.rollback()
                except Exception:
                    pass
            now_state = state()
            # only the tables that changed since the step before are written (the file would be several MB otherwise)
            changed = {t: rows for t, rows in now_state.items() if previous.get(t) != rows}
            previous = now_state
            steps.append({"op": op, "clock": {"secs": clock["secs"], "micros": clock["micros"], "mono": clock["mono"]}, "error": error, "result": result, "state_changes": changed})
        return steps
    return {"resources": scenario["resources"], "governor": scenario["governor"], "steps": asyncio.run(go())}


def build():
    return {"lanes_by_type": LANES_BY_TYPE, "category_by_type": CATEGORY_BY_TYPE, "caps": CAPS, "hardware": MID, "scenarios": {name: run(sc) for name, sc in SCENARIOS.items()}}


def main():
    text = json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.relative_to(ROOT)} is out of date: run python tools/parity/gen_orchestrator_vectors.py")
        print("orchestrator vectors are up to date")
        return
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), f"({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
