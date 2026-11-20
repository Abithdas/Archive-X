"""Record what Python's `TransferGovernor` decides, on fake clocks.

    python tools/parity/gen_governor_vectors.py          # rewrite crates/ax-core/tests/governor_cases.json
    python tools/parity/gen_governor_vectors.py --check  # fail if the file is out of date

The governor is a gate made of counters, limits and a few learned paces. Each scenario is a list of operations on one governor. `time.time`
and `time.monotonic` are fake for the governor's module only (asyncio keeps the real clock); every wait inside it is made to raise instead of
sleeping, so "would have to wait" becomes the answer `waits` without leaving threads behind. After every operation the result, the log lines
the governor wrote and the full `snapshot()` are recorded; `crates/ax-core/tests/governor.rs` replays them on the Rust port.
"""
import asyncio
import json
import sys
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-core" / "tests" / "governor_cases.json"

WALL0 = 1_930_713_967.0
MONO0 = 5_000.0
MIB = 1024 * 1024
FLOOD_LANES = [None, "", "range", "rpc", "playback", "upload", "other"]

CONFIG = {
    "TELEGRAM_BACKGROUND_RANGE_CONCURRENCY": 8, "PLAYBACK_FOREGROUND_RANGE_CONCURRENCY": 6, "PLAYBACK_FOREGROUND_RPC_CONCURRENCY": 4,
    "PLAYBACK_FOREGROUND_EMERGENCY_CONCURRENCY": 10, "PLAYBACK_FOREGROUND_RPC_INTERVAL_SECONDS": 0.02, "TRANSFER_GOVERNOR_PLAYBACK_DEADLINE_SECONDS": 3.0,
}


def adv(w, m=None):
    return {"op": "advance", "wall": w, "mono": w if m is None else m}


def flood(seconds, kind=None, request=None):
    return {"op": "floodwait", "seconds": seconds, "kind": kind, "request": request}


SCENARIOS = {
    "fresh": [{"op": "snapshot"}],
    "playback and pause": [
        {"op": "clear_wait"}, {"op": "p_enter"}, {"op": "clear_wait"}, {"op": "snapshot"}, {"op": "policy", "blocks": False}, {"op": "clear_wait"}, {"op": "snapshot"},
        {"op": "p_enter"}, {"op": "p_exit"}, {"op": "p_exit"}, {"op": "p_exit"}, {"op": "policy", "blocks": True}, {"op": "clear_wait"},
        {"op": "pause", "paused": True}, {"op": "file_take", "size": 5, "dc": 2, "allow": False}, {"op": "range_permit", "playback": False},
        {"op": "range_permit", "playback": True}, {"op": "pause", "paused": False}, {"op": "snapshot"},
    ],
    "file slots": [
        {"op": "limits", "small": 2, "large": 1},
        {"op": "file_take", "size": 5, "dc": 2, "allow": False}, {"op": "file_take", "size": 5, "dc": 2, "allow": False}, {"op": "file_take", "size": 5, "dc": 2, "allow": False},
        {"op": "file_take", "size": 5, "dc": 4, "allow": False}, {"op": "file_take", "size": 25 * MIB, "dc": 2, "allow": False}, {"op": "file_take", "size": 25 * MIB, "dc": 2, "allow": False},
        {"op": "file_take", "size": None, "dc": None, "allow": False}, {"op": "file_take", "size": 20 * MIB - 1, "dc": 9, "allow": False}, {"op": "file_take", "size": 20 * MIB, "dc": 9, "allow": False},
        {"op": "snapshot"}, {"op": "p_enter"}, {"op": "file_take", "size": 5, "dc": 7, "allow": False}, {"op": "file_take", "size": 5, "dc": 7, "allow": True},
        {"op": "file_release", "dc": 2, "kind": "small"}, {"op": "file_release", "dc": 2, "kind": "small"}, {"op": "file_release", "dc": 2, "kind": "small"},
        {"op": "file_release", "dc": 2, "kind": "large"}, {"op": "file_release", "dc": 5, "kind": "large"}, {"op": "snapshot"},
        {"op": "limits", "small": "7", "large": "x"}, {"op": "limits", "small": None, "large": 999}, {"op": "limits", "small": 0, "large": -3}, {"op": "limits", "small": 2.9, "large": True},
        {"op": "snapshot"},
    ],
    "download floodwaits": [
        {"op": "dl_flood", "seconds": 30, "lane": "range", "request": "GetFileRequest", "channel": -100123, "message": 7},
        {"op": "snapshot"}, {"op": "lane_wait", "lane": "range"}, {"op": "lane_wait", "lane": "playback"}, {"op": "lane_wait", "lane": "bogus"},
        {"op": "dl_flood", "seconds": 5, "lane": "RANGE", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 0, "lane": "playback", "request": "", "channel": 55, "message": 1},
        {"op": "dl_flood", "seconds": 1e9, "lane": "fingerprint", "request": "Hash", "channel": 55, "message": 2},
        {"op": "dl_flood", "seconds": 2.5, "lane": "", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 3.5, "lane": None, "request": None, "channel": 0, "message": 0},
        {"op": "snapshot"}, {"op": "global_take", "size": 5, "playback": False, "allow": False},
        adv(100), {"op": "snapshot"}, adv(250), {"op": "snapshot"}, adv(100000), {"op": "snapshot"},
        {"op": "scan_flood", "channel": 9}, {"op": "scan_flood", "channel": 9}, {"op": "scan_flood", "channel": 0}, {"op": "channel_count", "channel": 9}, {"op": "channel_count", "channel": 0},
        {"op": "ref_refresh", "channel": 12, "message": 3}, {"op": "ref_refresh", "channel": 0, "message": 0}, {"op": "snapshot"},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "dl_flood", "seconds": 4, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "snapshot"}, adv(299), {"op": "snapshot"}, adv(2), {"op": "snapshot"},
    ],
    "global range slots": [
        {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 25 * MIB, "playback": False, "allow": False},
        {"op": "global_take", "size": 0, "playback": False, "allow": False}, {"op": "global_take", "size": None, "playback": False, "allow": False},
        {"op": "global_take", "size": 5, "playback": True, "allow": False}, {"op": "snapshot"},
        {"op": "gw_register"}, {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": True},
        {"op": "global_take", "size": 5, "playback": True, "allow": False}, {"op": "gw_release"}, {"op": "gw_release"}, {"op": "gw_release"},
        {"op": "p_enter"}, {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": True},
        {"op": "policy", "blocks": False}, {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "snapshot"},
        {"op": "global_release", "cls": "small"}, {"op": "global_release", "cls": "large"}, {"op": "global_release", "cls": "playback"}, {"op": "global_release", "cls": "small"},
        {"op": "global_release", "cls": "small"}, {"op": "global_release", "cls": "small"}, {"op": "snapshot"},
        {"op": "limits", "small": 3, "large": 3},
        {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": False},
        {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": False},
        {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "global_take", "size": 5, "playback": False, "allow": False},
        {"op": "snapshot"},
        {"op": "dl_flood", "seconds": 60, "lane": "range", "request": None, "channel": 0, "message": 0},
        {"op": "global_release", "cls": "small"}, {"op": "global_release", "cls": "small"}, {"op": "global_release", "cls": "small"},
        {"op": "global_take", "size": 5, "playback": False, "allow": False}, {"op": "snapshot"},
    ],
    "playback lanes": [
        {"op": "snapshot"}, {"op": "lanes", "normal": 12, "emergency": None}, {"op": "snapshot"}, {"op": "emergency", "on": True}, {"op": "snapshot"}, {"op": "emergency", "on": False}, {"op": "snapshot"},
        {"op": "lanes", "normal": 0, "emergency": 3}, {"op": "snapshot"}, {"op": "lanes", "normal": 5, "emergency": 2}, {"op": "emergency", "on": True}, {"op": "snapshot"},
        flood(10, "range"), {"op": "emergency", "on": True}, {"op": "lanes", "normal": 9, "emergency": 11}, {"op": "snapshot"},
        adv(11), {"op": "range_ok", "amount": 1000, "elapsed": 1}, {"op": "snapshot"}, {"op": "range_ok", "amount": 1000, "elapsed": 1}, {"op": "range_ok", "amount": 1000, "elapsed": 1},
        {"op": "snapshot"},
    ],
    "playback range slots": [
        {"op": "lanes", "normal": 2, "emergency": 4},
        {"op": "pr_enter", "deadline": None}, {"op": "snapshot"}, {"op": "pr_enter", "deadline": 0.5}, {"op": "pr_enter", "deadline": None}, {"op": "snapshot"},
        {"op": "pr_exit"}, {"op": "snapshot"}, {"op": "pr_enter", "deadline": None}, {"op": "pr_exit"}, {"op": "pr_exit"}, {"op": "pr_exit"}, {"op": "snapshot"},
        {"op": "dl_flood", "seconds": 60, "lane": "playback", "request": None, "channel": 0, "message": 0},
        {"op": "pr_enter", "deadline": None}, {"op": "snapshot"}, {"op": "pr_enter", "deadline": None}, {"op": "snapshot"},
        flood(10, "playback"), {"op": "pr_enter", "deadline": None}, {"op": "snapshot"}, adv(20), {"op": "pr_enter", "deadline": None}, {"op": "snapshot"},
    ],
    "floodwait learning": [
        {"op": "snapshot"}, flood(30, "range", "GetFileRequest"), {"op": "snapshot"}, flood(30, "range", "GetFileRequest"), {"op": "snapshot"}, adv(31), flood(30, "range"), {"op": "snapshot"},
        adv(31), flood(2, "rpc", "messages.GetHistory"), {"op": "snapshot"}, flood(2, "rpc"), {"op": "snapshot"}, adv(5), flood(2, "rpc"), {"op": "snapshot"},
        flood(2.5, "playback"), flood(3.5, "upload", "SaveFilePartRequest"), flood(1, None), flood(1, ""), flood(1, "other", "X"), {"op": "snapshot"},
        adv(100, 700), {"op": "snapshot"}, adv(1000, 1000), {"op": "snapshot"}, adv(100000, 100000), {"op": "snapshot"},
        flood(4, "range"), {"op": "snapshot"},
    ] + [flood(1, "range") if i % 2 else adv(3) for i in range(60)] + [{"op": "snapshot"}],
    "network and outcomes": [
        {"op": "neterr", "message": "x" * 500}, {"op": "snapshot"}, {"op": "neterr", "message": "again"}, {"op": "snapshot"}, adv(6),
        {"op": "range_fail"}, {"op": "range_fail"}, {"op": "snapshot"},
        {"op": "range_ok", "amount": 0, "elapsed": 1}, {"op": "range_ok", "amount": 5, "elapsed": 0}, {"op": "range_ok", "amount": -5, "elapsed": 1}, {"op": "snapshot"},
    ] + [{"op": "range_ok", "amount": 1_000_000 + i * 70_000, "elapsed": 0.5 + (i % 3) * 0.25} for i in range(14)] + [{"op": "snapshot"}] +
    [{"op": "rpc_ok", "elapsed": 0.05 + (i % 5) * 0.03} for i in range(30)] + [{"op": "snapshot"}, {"op": "rpc_ok", "elapsed": -1}, {"op": "snapshot"}],
    "permits": [
        {"op": "rpc_permit"}, {"op": "rpc_permit"}, {"op": "snapshot"}, {"op": "rpc_release"}, {"op": "rpc_permit"}, {"op": "rpc_release"}, {"op": "rpc_release"}, {"op": "rpc_release"},
        {"op": "snapshot"}, {"op": "prpc_take"}, {"op": "prpc_take"}, {"op": "prpc_take"}, {"op": "prpc_take"}, {"op": "prpc_take"}, {"op": "snapshot"}, {"op": "prpc_release"},
        {"op": "range_permit", "playback": False}, {"op": "range_permit", "playback": False}, {"op": "range_permit", "playback": True}, {"op": "snapshot"},
        {"op": "range_release"}, {"op": "range_release"}, {"op": "range_release"}, {"op": "range_release"},
        {"op": "limits", "small": 2, "large": 2}, flood(5, "rpc"), {"op": "rpc_permit"}, {"op": "rpc_permit"}, {"op": "snapshot"},
    ],
    "uploads": [
        {"op": "snapshot"}, {"op": "up_take"}, {"op": "snapshot"}, adv(31), {"op": "snapshot"}, adv(31), {"op": "up_take"}, {"op": "snapshot"}, adv(31), {"op": "snapshot"},
        adv(31), {"op": "snapshot"}, adv(31), {"op": "snapshot"}, adv(31), {"op": "snapshot"}, adv(31), {"op": "snapshot"},
        {"op": "up_flood", "seconds": 20, "request": "SaveBigFilePartRequest"}, {"op": "snapshot"}, {"op": "up_take"}, {"op": "up_flood", "seconds": 5, "request": None}, {"op": "snapshot"},
        adv(21), {"op": "up_take"}, {"op": "snapshot"}, {"op": "up_flood", "seconds": 0, "request": ""}, {"op": "snapshot"}, adv(2), {"op": "up_flood", "seconds": 1, "request": ""},
        {"op": "up_flood", "seconds": 3, "request": "a"}, {"op": "snapshot"}, adv(10), {"op": "up_flood", "seconds": 3, "request": "a"}, {"op": "snapshot"},
        adv(10), {"op": "up_flood", "seconds": 3, "request": "a"}, {"op": "snapshot"}, adv(10), {"op": "up_flood", "seconds": 3, "request": "a"}, {"op": "snapshot"},
        {"op": "up_conn", "queues": 2, "error": "e" * 400}, {"op": "snapshot"}, {"op": "up_conn", "queues": 0, "error": ""}, {"op": "up_conn", "queues": 99, "error": ""}, {"op": "snapshot"},
        {"op": "up_part"}, {"op": "p_enter"}, {"op": "snapshot"}, {"op": "up_take"}, {"op": "up_take"}, {"op": "up_take"}, {"op": "snapshot"},
        {"op": "up_release"}, {"op": "up_release"}, {"op": "up_release"}, {"op": "up_release"}, {"op": "up_release"}, {"op": "snapshot"},
    ],
    "learning blocks end exactly": [
        flood(30, "range"), adv(30), flood(30, "range"), {"op": "snapshot"}, adv(29.5), flood(30, "range"), {"op": "snapshot"},
        adv(30), flood(30, "rpc"), adv(30), flood(30, "rpc"), {"op": "snapshot"},
    ],
    "pace recovers to the floor": [flood(2, "rpc"), adv(3), {"op": "snapshot"}] + [{"op": "rpc_ok", "elapsed": 0.05} for _ in range(120)] + [{"op": "snapshot"}] +
    [flood(2, "range"), adv(3)] + [{"op": "range_ok", "amount": 1_000_000, "elapsed": 1} for _ in range(120)] + [{"op": "snapshot"}] +
    [{"op": "range_ok", "amount": 100_000, "elapsed": 1} for _ in range(6)] + [{"op": "snapshot"}] + [{"op": "range_ok", "amount": 5_000_000, "elapsed": 1} for _ in range(6)] + [{"op": "snapshot"}],
    "range window against the average": [{"op": "range_ok", "amount": 500_000, "elapsed": 1} for _ in range(17)] + [{"op": "snapshot"}, {"op": "range_ok", "amount": 3_000_000, "elapsed": 1}, {"op": "snapshot"}],
    "upload ramp timing": [
        {"op": "up_take"}, adv(30), {"op": "snapshot"}, adv(30), {"op": "snapshot"}, adv(29.5), {"op": "snapshot"}, adv(0.5), {"op": "snapshot"},
        {"op": "up_conn", "queues": 4, "error": ""}, adv(30), {"op": "snapshot"}, adv(0.5), {"op": "snapshot"},
    ],
    "upload from the bottom": [
        {"op": "up_flood", "seconds": 5, "request": None}, {"op": "snapshot"}, adv(6), {"op": "up_take"}, {"op": "snapshot"}, adv(31), {"op": "snapshot"}, adv(31), {"op": "snapshot"},
        {"op": "up_release"}, {"op": "up_flood", "seconds": 5, "request": None}, {"op": "snapshot"},
    ],
    "background range permits": [{"op": "range_permit", "playback": False} for _ in range(9)] + [{"op": "snapshot"}] + [{"op": "range_permit", "playback": True} for _ in range(3)] + [{"op": "snapshot"}],
    "upload ramp from an odd pair": [
        {"op": "up_take"}, adv(31), {"op": "up_take"}, adv(31), {"op": "up_take"}, adv(31), {"op": "up_take"}, adv(31), {"op": "up_take"}, adv(31), {"op": "up_take"}, adv(31), {"op": "up_take"},
        {"op": "snapshot"}, {"op": "up_flood", "seconds": 1, "request": ""}, adv(2), {"op": "up_flood", "seconds": 1, "request": ""}, adv(2), {"op": "snapshot"},
        adv(31), {"op": "up_take"}, {"op": "snapshot"}, {"op": "up_conn", "queues": 1, "error": ""}, adv(31), {"op": "snapshot"}, {"op": "up_take"}, {"op": "snapshot"},
    ],
}

FLOOD_LANE_CASES = [(n, p) for n in [None, "", "GetFileRequest", "GetCdnFileRequest", "GetFile", "SaveFilePartRequest", "SaveBigFilePartRequest", "messages.GetHistory", "x"] for p in (False, True)]


class Blocked(Exception):
    pass


class NoWait(threading.Condition):
    def wait(self, timeout=None):
        raise Blocked


class FakeDb:
    def __init__(self):
        self.lines = []

    def log(self, message, level="INFO"):
        self.lines.append([level, message])


def run_scenario(ops):
    from telegram_archive import config
    for k, v in CONFIG.items():
        assert getattr(config, k) == v, (k, getattr(config, k))
    from telegram_archive.core import governor as module
    clock = {"wall": WALL0, "mono": MONO0}

    def sleep(_):
        raise Blocked
    module.time = types.SimpleNamespace(time=lambda: clock["wall"], monotonic=lambda: clock["mono"], sleep=sleep)
    db = FakeDb()
    g = module.TransferGovernor(db)
    g._rpc_cond = NoWait(threading.RLock())
    g._playback_rpc_lock = NoWait(threading.RLock())
    g._range_cond = NoWait(threading.Lock())
    g._global_range_cond = NoWait(threading.RLock())
    g._upload_cond = NoWait(threading.RLock())
    g._playback_cond = NoWait(threading.RLock())

    async def go():
        held = []
        steps = []
        for op in ops:
            name = op["op"]
            result = None

            async def attempt(coro, timeout=0.15):
                """(finished, value): False when it would have to wait."""
                try:
                    return True, await asyncio.wait_for(coro, timeout)
                except (asyncio.TimeoutError, Blocked):
                    return False, None

            def sync_ok(fn, *a, **k):
                try:
                    fn(*a, **k)
                    return True
                except Blocked:
                    return False

            if name == "advance":
                clock["wall"] += op["wall"]
                clock["mono"] += op["mono"]
            elif name == "pause":
                g.set_background_paused(op["paused"])
            elif name == "policy":
                g.set_playback_policy(op["blocks"])
            elif name == "p_enter":
                g.playback_enter()
            elif name == "p_exit":
                g.playback_exit()
            elif name == "clear_wait":
                result = "passes" if sync_ok(g.wait_for_playback_clear) else "waits"
            elif name == "limits":
                g.set_telegram_download_limits(op["small"], op["large"])
            elif name == "file_take":
                ok, key = await attempt(g._acquire_file_operation_slot(file_size=op["size"], dc_id=op["dc"], allow_during_playback=op["allow"]))
                result = ["granted", list(key)] if ok else "waits"
            elif name == "file_release":
                g._release_file_operation_slot((op["dc"], op["kind"]))
            elif name == "dl_flood":
                g.note_download_floodwait(op["seconds"], lane=op["lane"], request=op["request"], channel_id=op["channel"], message_id=op["message"])
            elif name == "lane_wait":
                try:
                    await asyncio.wait_for(g.wait_download_lane_async(op["lane"]), 0.15)
                    result = "passes"
                except asyncio.TimeoutError:
                    result = "waits"
            elif name == "scan_flood":
                g.note_channel_scan_floodwait(op["channel"])
            elif name == "channel_count":
                result = g.channel_download_floodwaits(op["channel"])
            elif name == "ref_refresh":
                g.note_file_reference_refresh(channel_id=op["channel"], message_id=op["message"])
            elif name == "global_take":
                ok, cls = await attempt(g._acquire_global_range_slot(file_size=op["size"], playback=op["playback"], allow_during_playback=op["allow"]))
                result = ["granted", cls] if ok else "waits"
            elif name == "global_release":
                g._release_global_range_slot(op["cls"])
            elif name == "gw_register":
                g._register_global_playback_waiter()
            elif name == "gw_release":
                g._release_global_playback_waiter()
            elif name == "lanes":
                g.configure_playback_lanes(op["normal"], op["emergency"])
            elif name == "emergency":
                g.set_playback_emergency(op["on"])
            elif name == "floodwait":
                g.note_floodwait(op["seconds"], op["kind"], op["request"])
            elif name == "neterr":
                g.note_network_error(op["message"])
            elif name == "range_ok":
                g.note_range_success(op["amount"], op["elapsed"])
            elif name == "range_fail":
                g.note_range_failure()
            elif name == "rpc_ok":
                g.note_rpc_success(op["elapsed"])
            elif name == "rpc_permit":
                result = "granted" if sync_ok(g._acquire_rpc_permit) else "waits"
            elif name == "rpc_release":
                g._release_rpc_permit()
            elif name == "prpc_take":
                ok, _ = await attempt(g._acquire_playback_rpc_permit_async())
                result = "granted" if ok else "waits"
            elif name == "prpc_release":
                g._release_playback_rpc_permit()
            elif name == "range_permit":
                result = "granted" if sync_ok(g._acquire_range_permit, playback=op["playback"]) else "waits"
            elif name == "range_release":
                g._release_range_permit()
            elif name == "pr_enter":
                cm = g.telegram_playback_range_slot(deadline_seconds=op["deadline"])
                try:
                    await asyncio.wait_for(cm.__aenter__(), 0.4)
                    held.append(cm)
                    result = "granted"
                except asyncio.TimeoutError:
                    result = "waits"
            elif name == "pr_exit":
                if held:
                    await held.pop().__aexit__(None, None, None)
                    result = "exited"
                else:
                    result = "none held"
            elif name == "up_take":
                result = "granted" if sync_ok(g._acquire_upload_permit) else "waits"
            elif name == "up_release":
                g._release_upload_permit()
            elif name == "up_conn":
                g.note_upload_connection_failure(op["queues"], op["error"])
            elif name == "up_part":
                g.note_upload_part_success()
            elif name == "up_flood":
                g.note_upload_floodwait(op["seconds"], op["request"])
            elif name == "snapshot":
                pass
            else:
                raise AssertionError(name)
            step = dict(op)
            step["result"] = result
            step["logs"], db.lines[:] = list(db.lines), []
            step["snapshot"] = g.snapshot()
            steps.append(step)
        return steps
    return asyncio.run(go())


def build():
    out = {"config": CONFIG, "scenarios": {}, "flood_lane_for": []}
    for name, ops in SCENARIOS.items():
        out["scenarios"][name] = run_scenario(ops)
    from telegram_archive.core.governor import TransferGovernor
    for n, p in FLOOD_LANE_CASES:
        out["flood_lane_for"].append({"request": n, "playback": p, "lane": TransferGovernor.flood_lane_for(n, p)})
    return json.dumps(out, indent=1, sort_keys=True) + "\n"


def main():
    text = build()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit(f"{OUT} is out of date: run python tools/parity/gen_governor_vectors.py")
        print("up to date")
        return
    OUT.write_text(text)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
