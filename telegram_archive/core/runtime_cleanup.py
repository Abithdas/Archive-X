"""Safe cleanup of Archive X disposable runtime artifacts.

This module intentionally has a narrow allow-list. Persistent media, metadata,
database, authentication, job state, and user-created files are never scanned
for deletion here.
"""
from __future__ import annotations
import shutil
import tempfile
import time
from pathlib import Path

from .. import config

# TemporaryDirectory prefixes used by Archive X operations. These are safe to
# remove only when stale because an active operation may still own one.
TEMP_PREFIXES = (
    "archive_x_", "archive-x-", "tg_upload_", "tg_upload_recover_",
    "tg_media_mover_", "tg_vertical_mobile_", "telegram_channel_organizer_",
    "telegram_media_manager_dup_", "cleanup_",
)


def _remove_stale_dirs(root: Path, older_than: float) -> tuple[int, int]:
    removed = 0
    bytes_removed = 0
    if not root.exists():
        return removed, bytes_removed
    cutoff = time.time() - older_than
    try:
        for p in root.iterdir():
            if not p.is_dir() or not p.name.startswith(TEMP_PREFIXES):
                continue
            try:
                if p.stat().st_mtime >= cutoff:
                    continue
                size = sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                shutil.rmtree(p, ignore_errors=False)
                removed += 1
                bytes_removed += size
            except (OSError, PermissionError):
                # Active/locked directories are left alone and retried later.
                pass
    except OSError:
        pass
    return removed, bytes_removed


def cleanup_runtime_artifacts(*, stale_temp_hours: float = 24.0) -> dict:
    """Remove only stale, disposable runtime leftovers.

    Normal operations already use TemporaryDirectory, so this is primarily a
    crash-recovery scavenger for processes that were killed before their finally
    blocks ran. It is safe to run at startup and periodically.
    """
    result = {"temp_dirs_removed": 0, "temp_bytes_removed": 0,
              "tmp_files_removed": 0, "tmp_bytes_removed": 0}
    stale = max(1.0, float(stale_temp_hours)) * 3600.0
    roots = {Path(tempfile.gettempdir())}
    # Some older builds used a package-local temp/cache workspace. Only inspect
    # explicitly named disposable locations, never the whole application data tree.
    roots.add(config.PLAYER_CACHE_DIR)
    roots.add(config.CACHE_DIR / "tmp")

    for root in roots:
        n, b = _remove_stale_dirs(root, stale)
        result["temp_dirs_removed"] += n
        result["temp_bytes_removed"] += b

    # Safe atomic-write leftovers in the disposable player cache only.
    cutoff = time.time() - 3600.0
    for root in (config.PLAYER_CACHE_DIR,):
        if not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or not p.name.endswith((".tmp", ".tmp.jpg", ".part", ".partial")):
                    continue
                try:
                    if p.stat().st_mtime < cutoff:
                        size = p.stat().st_size
                        p.unlink(missing_ok=True)
                        result["tmp_files_removed"] += 1
                        result["tmp_bytes_removed"] += size
                except OSError:
                    pass
        except OSError:
            pass
    return result
