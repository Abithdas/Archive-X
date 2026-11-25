"""Four older checks are scripts (`python tools/tests/<name>.py`) that run when imported and read `sys.argv`; `pytest` must not import them.
They are run as separate programs by `test_script_checks.py`, and CI and the installer build still run them directly."""
collect_ignore = [
    "test_desktop_mode.py",
    "test_image_derivatives.py",
    "test_photo_derivatives.py",
    "test_duplicate_delete_thumbnail.py",
]

# Tests that build a Database on the shared data folder would clash with the others (a second connection waits for the first one's lock, and
# rows left behind change the next schema check). They run in their own process: test_isolated_checks.py starts one with AX_ISOLATED=1.
import os as _os
ISOLATED = ["test_inventory_off_loop.py", "test_performer_identity_index.py", "test_job_detail_kept.py", "test_snapshot_scan_heartbeat.py"]
if _os.environ.get("AX_ISOLATED") != "1":
    collect_ignore += ISOLATED


# A folder scan runs in a background thread (scan, probe, snapshots, identify). One that outlives its test would still be using that test's
# database and stubs while the next test runs, so every test waits for them to finish.
import threading as _threading
import time as _time

import pytest as _pytest


def wait_for_scans(timeout: float = 90.0) -> None:
    deadline = _time.time() + timeout
    while _time.time() < deadline and any(t.name.startswith("local-scan-") and t.is_alive() for t in _threading.enumerate()):
        _time.sleep(0.05)


@_pytest.fixture(autouse=True)
def _no_scan_outlives_its_test():
    yield
    wait_for_scans()


# The two metadata scans (movies and shows, scene databases) keep how their last run went in a module-level dict, so the transfer lanes can show it for a
# moment after it finishes. That dict outlives a test: the next one then saw a "finished" lane that was not its own (about one full run in three).
import sys as _sys

_SCAN_STATE_AT_REST = {"running": False, "total": 0, "done": 0, "failed": 0, "matched": 0, "review": 0, "unmatched": 0, "channel": "", "started": 0.0, "finished": 0.0, "reason": ""}


@_pytest.fixture(autouse=True)
def _scan_state_does_not_leak_between_tests():
    def reset():
        for name in ("telegram_archive.engines.local_identify_adult", "telegram_archive.engines.screen_identify"):
            module = _sys.modules.get(name)
            if module is not None and isinstance(getattr(module, "_state", None), dict):
                module._state.update(_SCAN_STATE_AT_REST)
    reset()
    yield
    wait_for_scans()
    reset()
