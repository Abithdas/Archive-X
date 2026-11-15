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
