"""Completion markers for the Windows launcher-owned shutdown supervisor.

The launcher is the Python process's parent, so its final process-tree cleanup
cannot accidentally terminate the supervisor itself. A separate request marker
starts the supervisor's long grace timer; the completion marker is written only
after the ASGI lifecycle has attempted service cleanup and closed the database.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _atomic_json(path: Path, payload: dict) -> bool:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, path)
        return True
    except Exception:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def prepare_shutdown_completion_marker(app: Any) -> bool:
    """Register marker paths and persist the confirmed in-app Exit request."""
    if os.name != "nt":
        return False
    raw_complete = str(os.environ.get("ARCHIVE_X_SHUTDOWN_MARKER") or "").strip()
    raw_request = str(os.environ.get("ARCHIVE_X_SHUTDOWN_REQUEST_MARKER") or "").strip()
    if not raw_complete or not raw_request:
        # Direct run.py launches still use the regular graceful ASGI shutdown;
        # launcher supervision is available on the supported .bat path.
        return False
    complete_marker = Path(raw_complete)
    request_marker = Path(raw_request)
    try:
        complete_marker.parent.mkdir(parents=True, exist_ok=True)
        complete_marker.unlink(missing_ok=True)
        request_marker.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    payload = {
        "pid": os.getpid(),
        "launch_id": str(os.environ.get("ARCHIVE_X_LAUNCH_ID") or ""),
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state": "user_exit_requested",
    }
    if not _atomic_json(request_marker, payload):
        return False
    app.state.shutdown_completion_marker = str(complete_marker)
    return True


def write_shutdown_completion_marker(app: Any) -> bool:
    """Atomically mark completion after app services and the DB are closed."""
    marker_value = getattr(getattr(app, "state", None), "shutdown_completion_marker", None)
    if not marker_value:
        return False
    marker = Path(str(marker_value))
    payload = {
        "pid": os.getpid(),
        "launch_id": str(os.environ.get("ARCHIVE_X_LAUNCH_ID") or ""),
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state": "application_lifespan_complete",
    }
    return _atomic_json(marker, payload)
