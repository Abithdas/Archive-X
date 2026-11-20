"""Record what Python's `InteractiveResourceManager` decides, on fake clocks.

    python tools/parity/gen_resource_policy_vectors.py          # rewrite crates/ax-core/tests/resource_policy_cases.json
    python tools/parity/gen_resource_policy_vectors.py --check  # fail if the file is out of date

Each scenario is a list of operations on one manager (a hardware profile and a CPU count are fixed per scenario). `time.time` and
`time.monotonic` are fake and move only on `advance`. After every operation the result (or the error text) and the full `snapshot()` are
recorded; `crates/ax-core/tests/resource_policy.rs` replays the same operations on the Rust port and must reach the same answers.

`take` asks for a background slot and records whether it was granted at once (a request that would have to wait is cancelled after 50 ms,
which is what leaves Python's waiting counters to be checked). `park` leaves such a request waiting so that the counters can be seen,
`unpark` cancels them.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-core" / "tests" / "resource_policy_cases.json"

WALL0 = 1_930_713_967.0
MONO0 = 5_000.0

SMALL = {"background_capacity": 1, "media_workers": 1}
MID = {"background_capacity": 3, "media_workers": 2, "hardware_acceleration": {"encoder": "none"}}
BIG = {"background_capacity": 6, "media_workers": 4, "hardware_acceleration": {"encoder": "nvenc", "decode": True}}

SCENARIOS = {
    "hardware profiles": [
        {"hardware": {}, "cpu": 1, "ops": [{"op": "snapshot"}]},
        {"hardware": {}, "cpu": 2, "ops": [{"op": "snapshot"}]},
        {"hardware": {}, "cpu": 8, "ops": [{"op": "snapshot"}]},
        {"hardware": {}, "cpu": 64, "ops": [{"op": "snapshot"}]},
        {"hardware": {"background_capacity": 99, "media_workers": 99}, "cpu": 4, "ops": [{"op": "snapshot"}]},
        {"hardware": {"background_capacity": 0, "media_workers": 0}, "cpu": 4, "ops": [{"op": "snapshot"}]},
        {"hardware": SMALL, "cpu": 4, "ops": [{"op": "snapshot"}]},
        {"hardware": MID, "cpu": 4, "ops": [{"op": "snapshot"}]},
        {"hardware": BIG, "cpu": 16, "ops": [{"op": "snapshot"}]},
    ],
    "configure": [{"hardware": MID, "cpu": 4, "ops": [
        {"op": "configure", "mode": "Balanced", "label": "x" * 200},
        {"op": "configure", "mode": "  MANUAL  ", "limits": {"conversion": 5, "metadata": "4", "upload": 2.9, "preview": None, "bogus": 3}, "playback_gate": False, "expires_seconds": 0},
        {"op": "configure", "mode": "turbo"},
        {"op": "configure", "limits": {"thumbnail": 0}},
        {"op": "configure", "limits": {"thumbnail": 9}},
        {"op": "configure", "limits": {"conversion": 3, "upload": "many"}},
        {"op": "configure", "limits": {"conversion": 3, "preview": 7, "thumbnail": -1}},
        {"op": "configure", "limits": {}},
        {"op": "configure", "mode": "manual", "limits": {"metadata": 1, "conversion": 8}, "playback_gate": True},
        {"op": "configure", "playback_gate": False, "label": "no gate"},
        {"op": "configure", "expires_seconds": -5},
        {"op": "configure", "expires_seconds": "30", "label": "temporary"},
        {"op": "advance", "wall": 10, "mono": 10},
        {"op": "snapshot"},
        {"op": "advance", "wall": 25, "mono": 25},
        {"op": "snapshot"},
        {"op": "configure", "mode": "background", "expires_seconds": 5.7},
        {"op": "advance", "wall": 5.7, "mono": 5.7},
        {"op": "snapshot"},
        {"op": "configure", "mode": "manual", "limits": {"upload": 2}, "expires_seconds": 30, "label": "exactly"},
        {"op": "advance", "wall": 29, "mono": 29},
        {"op": "snapshot"},
        {"op": "advance", "wall": 1, "mono": 1},
        {"op": "snapshot"},
        {"op": "configure", "mode": "playback", "expires_seconds": 100},
        {"op": "configure", "mode": "automatic"},
        {"op": "advance", "wall": 200, "mono": 200},
        {"op": "snapshot"},
    ]}],
    "pause and resume": [{"hardware": MID, "cpu": 4, "ops": [
        {"op": "pause", "reason": "r" * 300},
        {"op": "take", "category": "metadata"},
        {"op": "checkpoint", "category": "generic"},
        {"op": "service", "block_playback": False},
        {"op": "pause", "reason": ""},
        {"op": "resume"},
        {"op": "take", "category": "metadata"},
        {"op": "checkpoint", "category": "generic"},
        {"op": "service", "block_playback": True},
    ]}],
    "playback gates the categories": [{"hardware": BIG, "cpu": 16, "ops": [
        {"op": "configure", "mode": "automatic"},
        {"op": "p_enter"},
        {"op": "take", "category": "conversion"},
        {"op": "take", "category": "metadata"},
        {"op": "take", "category": "upload"},
        {"op": "take", "category": "weird"},
        {"op": "checkpoint", "category": "conversion"},
        {"op": "checkpoint", "category": "preview"},
        {"op": "service", "block_playback": True},
        {"op": "service", "block_playback": False},
        {"op": "configure", "mode": "background"},
        {"op": "take", "category": "metadata"},
        {"op": "checkpoint", "category": "metadata"},
        {"op": "configure", "mode": "balanced"},
        {"op": "take", "category": "upload"},
        {"op": "configure", "mode": "manual", "limits": {}, "playback_gate": True},
        {"op": "take", "category": "preview"},
        {"op": "configure", "mode": "manual", "limits": {}, "playback_gate": False},
        {"op": "take", "category": "preview"},
        {"op": "p_exit"},
        {"op": "p_exit"},
        {"op": "take", "category": "preview"},
    ]}],
    "limits and the reserved slot": [
        {"hardware": BIG, "cpu": 16, "ops": [
            {"op": "configure", "mode": "background"},
            {"op": "take", "category": "metadata"},
            {"op": "take", "category": "metadata"},
            {"op": "take", "category": "metadata"},
            {"op": "take", "category": "metadata"},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "preview"},
            {"op": "take", "category": "preview"},
            {"op": "take", "category": "thumbnail"},
            {"op": "release", "category": "metadata"},
            {"op": "take", "category": "Upload"},
            {"op": "release", "category": "upload"},
            {"op": "release", "category": "upload"},
            {"op": "release", "category": "upload"},
            {"op": "release", "category": "upload"},
            {"op": "release", "category": "metadata"},
            {"op": "release", "category": "metadata"},
            {"op": "release", "category": "metadata"},
            {"op": "release", "category": "nothing"},
            {"op": "release", "category": "thumbnail"},
            {"op": "release", "category": "preview"},
            {"op": "release", "category": "preview"},
            {"op": "release", "category": ""},
            {"op": "take", "category": ""},
            {"op": "take", "category": "generic"},
            {"op": "take", "category": "conversion"},
            {"op": "take", "category": "conversion"},
            {"op": "take", "category": "conversion"},
        ]},
        {"hardware": SMALL, "cpu": 2, "ops": [
            {"op": "configure", "mode": "background"},
            {"op": "take", "category": "metadata"},
            {"op": "take", "category": "upload"},
            {"op": "release", "category": "metadata"},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "metadata"},
        ]},
        {"hardware": MID, "cpu": 4, "ops": [
            {"op": "configure", "mode": "manual", "limits": {"upload": 1, "metadata": 2, "conversion": 1}, "playback_gate": False},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "upload"},
            {"op": "take", "category": "metadata"},
            {"op": "take", "category": "metadata"},
            {"op": "take", "category": "conversion"},
            {"op": "take", "category": "conversion"},
            {"op": "configure", "mode": "automatic"},
            {"op": "take", "category": "metadata"},
        ]},
    ],
    "service slot": [{"hardware": MID, "cpu": 4, "ops": [
        {"op": "service", "block_playback": True},
        {"op": "service", "block_playback": True},
        {"op": "release_service"},
        {"op": "release_service"},
        {"op": "release_service"},
        {"op": "i_enter"},
        {"op": "service", "block_playback": True},
        {"op": "service", "block_playback": False},
        {"op": "i_exit"},
        {"op": "service", "block_playback": True},
    ]}],
    "playback leases": [{"hardware": MID, "cpu": 4, "ops": [
        {"op": "snapshot"},
        {"op": "p_enter"},
        {"op": "advance", "wall": 0, "mono": 3},
        {"op": "snapshot"},
        {"op": "p_enter"},
        {"op": "p_enter"},
        {"op": "note"},
        {"op": "reconcile", "expected": 3},
        {"op": "reconcile", "expected": 1},
        {"op": "reconcile", "expected": -4},
        {"op": "reconcile", "expected": 0},
        {"op": "p_exit"},
        {"op": "p_exit"},
        {"op": "p_exit"},
        {"op": "i_heartbeat"},
        {"op": "i_enter"},
        {"op": "advance", "wall": 0, "mono": 2.5},
        {"op": "i_heartbeat"},
        {"op": "p_enter"},
        {"op": "p_enter"},
        {"op": "reconcile", "expected": 1},
        {"op": "reconcile", "expected": 0},
        {"op": "p_exit"},
        {"op": "p_exit"},
        {"op": "reconcile", "expected": 0},
        {"op": "p_enter"},
        {"op": "reconcile", "expected": 0},
        {"op": "i_exit"},
        {"op": "p_exit"},
        {"op": "advance", "wall": 0, "mono": 1.25},
        {"op": "snapshot"},
    ]}],
    "media policy": [
        {"hardware": BIG, "cpu": 16, "ops": [
            {"op": "snapshot"}, {"op": "p_enter"}, {"op": "snapshot"}, {"op": "p_exit"}, {"op": "pause", "reason": "x"}, {"op": "snapshot"},
            {"op": "resume"}, {"op": "configure", "mode": "manual", "limits": {"thumbnail": 6, "preview": 3}, "playback_gate": True}, {"op": "snapshot"},
        ]},
        {"hardware": {"background_capacity": 2, "media_workers": 3}, "cpu": 4, "ops": [{"op": "snapshot"}, {"op": "i_enter"}, {"op": "snapshot"}]},
        {"hardware": {"background_capacity": 1, "media_workers": 4}, "cpu": 4, "ops": [{"op": "snapshot"}]},
        {"hardware": {"background_capacity": 5, "media_workers": 2}, "cpu": 4, "ops": [{"op": "snapshot"}]},
    ],
    "waiting counters": [{"hardware": MID, "cpu": 4, "ops": [
        {"op": "pause", "reason": "stop"},
        {"op": "park", "category": "metadata"},
        {"op": "park", "category": "Upload"},
        {"op": "park", "category": "upload"},
        {"op": "snapshot"},
        {"op": "unpark"},
        {"op": "snapshot"},
        {"op": "take", "category": "metadata"},
        {"op": "snapshot"},
    ]}],
}


def run_scenario(spec):
    import os
    clock = {"wall": WALL0, "mono": MONO0}
    import types
    from telegram_archive.core import resource_manager as module
    from telegram_archive.core.resource_manager import InteractiveResourceManager
    # only the manager's own view of time is fake: asyncio keeps the real clock, or its timeouts would never fire
    module.time = types.SimpleNamespace(time=lambda: clock["wall"], monotonic=lambda: clock["mono"])
    module.os = types.SimpleNamespace(cpu_count=lambda: spec["cpu"])

    async def go():
        rm = InteractiveResourceManager(hardware_profile=dict(spec["hardware"]))
        parked = []
        steps = []
        for op in spec["ops"]:
            name = op["op"]
            result = None
            try:
                if name == "advance":
                    clock["wall"] += op["wall"]
                    clock["mono"] += op["mono"]
                elif name == "configure":
                    kwargs = {k: op[k] for k in ("mode", "limits", "playback_gate", "expires_seconds") if k in op}
                    rm.configure(label=op.get("label", ""), **kwargs)
                elif name == "pause":
                    rm.pause_background(op["reason"])
                elif name == "resume":
                    rm.resume_background()
                elif name == "p_enter":
                    await rm.playback_enter()
                elif name == "p_exit":
                    await rm.playback_exit()
                elif name == "i_enter":
                    await rm.interactive_playback_enter()
                elif name == "i_heartbeat":
                    await rm.interactive_playback_heartbeat()
                elif name == "i_exit":
                    await rm.interactive_playback_exit()
                elif name == "reconcile":
                    result = await rm.reconcile_playback(expected_streams=op["expected"])
                elif name == "note":
                    rm.note_playback_activity()
                elif name == "checkpoint":
                    try:
                        await asyncio.wait_for(rm.cooperative_background_checkpoint(category=op["category"]), 0.05)
                        result = "passes"
                    except asyncio.TimeoutError:
                        result = "waits"
                elif name == "service":
                    try:
                        await asyncio.wait_for(rm.acquire_service(block_playback=op["block_playback"]), 0.05)
                        result = "granted"
                    except asyncio.TimeoutError:
                        result = "waits"
                elif name == "release_service":
                    await rm.release_service()
                elif name == "take":
                    try:
                        await asyncio.wait_for(rm.acquire_background(op["category"]), 0.05)
                        result = "granted"
                    except asyncio.TimeoutError:
                        result = "waits"
                elif name == "release":
                    await rm.release_background(op["category"])
                elif name == "park":
                    parked.append(asyncio.create_task(rm.acquire_background(op["category"])))
                    await asyncio.sleep(0.02)
                elif name == "unpark":
                    for t in parked:
                        t.cancel()
                    await asyncio.gather(*parked, return_exceptions=True)
                    parked.clear()
                elif name == "snapshot":
                    pass
                else:
                    raise AssertionError(name)
            except ValueError as e:
                text = str(e)
                # Python's own wording for a value that is not a number differs between versions; the port has one text
                result = {"error": "not an integer" if text.startswith("invalid literal") else text}
            step = dict(op)
            step["result"] = result
            snap = rm.snapshot()
            step["snapshot"] = snap
            steps.append(step)
        for t in parked:
            t.cancel()
        return steps

    return asyncio.run(go())


def build():
    out = {}
    for name, specs in SCENARIOS.items():
        out[name] = [{"hardware": s["hardware"], "cpu": s["cpu"], "steps": run_scenario(s)} for s in specs]
    return json.dumps(out, indent=1, sort_keys=True) + "\n"


def main():
    text = build()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit(f"{OUT} is out of date: run python tools/parity/gen_resource_policy_vectors.py")
        print("up to date")
        return
    OUT.write_text(text)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
