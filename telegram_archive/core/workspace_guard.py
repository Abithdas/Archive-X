"""Filesystem ownership guards shared by background media modules.

Content Automation owns its configured download tree. Local Upload must not
scan, modify, or delete files from that tree because both modules can otherwise
operate on the same video at the same time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config

ACTIVE_JOB_STATUSES = {
    "QUEUED", "RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED",
}


def normalize_path(value: str | Path) -> Path:
    """Normalize a potentially missing filesystem path without requiring it to exist."""
    return Path(value).expanduser().resolve(strict=False)


def paths_overlap(left: str | Path, right: str | Path) -> bool:
    """Return True when either path contains the other or they are identical."""
    a = normalize_path(left)
    b = normalize_path(right)
    try:
        return a == b or a.is_relative_to(b) or b.is_relative_to(a)
    except AttributeError:  # Python < 3.9 compatibility fallback.
        a_s = str(a).casefold().rstrip("\\/")
        b_s = str(b).casefold().rstrip("\\/")
        return a_s == b_s or a_s.startswith(b_s + "\\") or b_s.startswith(a_s + "\\")


def content_automation_root(db) -> Path:
    """Return the currently configured Content Automation download root."""
    # Local import avoids a module cycle during application startup.
    from ..operations import automation
    cfg = automation.get_config(db, masked=False)
    return normalize_path(cfg.get("download_path") or config.AUTOMATION_DOWNLOAD_DIR)


def upload_job_folder(job: dict[str, Any]) -> Path | None:
    """Extract a Local Upload source folder from its persisted job detail."""
    try:
        detail = json.loads(job.get("detail") or "{}")
    except Exception:
        detail = {}
    folder = str(detail.get("folder") or "").strip()
    return normalize_path(folder) if folder else None


def active_upload_folders(db) -> list[tuple[str, Path]]:
    """Return active Local Upload job folders as (job_id, folder)."""
    rows = db.active_jobs("UPLOAD_LOCAL")
    found: list[tuple[str, Path]] = []
    for job in rows:
        folder = upload_job_folder(job)
        if folder is not None:
            found.append((str(job.get("id") or ""), folder))
    return found


def validate_local_upload_folder(db, folder: str | Path) -> None:
    """Reject Local Upload folders that overlap Content Automation storage."""
    target = normalize_path(folder)
    automation_root = content_automation_root(db)
    if paths_overlap(target, automation_root):
        raise ValueError(
            "This folder overlaps the Content Automation download folder. "
            "Choose a different Local Upload folder so the two modules cannot "
            "modify or delete the same files."
        )


def validate_automation_root(db, root: str | Path, *, ignore_job_id: str | None = None) -> None:
    """Reject an Automation root that overlaps any active Local Upload folder."""
    target = normalize_path(root)
    for job_id, folder in active_upload_folders(db):
        if ignore_job_id and job_id == ignore_job_id:
            continue
        if paths_overlap(target, folder):
            raise ValueError(
                f"Content Automation download folder overlaps active Local Upload job {job_id}. "
                "Stop that upload job or choose a different Automation download folder."
            )
