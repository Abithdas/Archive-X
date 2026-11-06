"""Durable-friendly job priority helpers.

The UI/database remains the source of truth; this module only converts a saved
job row into an in-memory queue priority. Lower numbers run first. A caller can
set ``detail.priority`` for operator-controlled overrides without changing the
jobs schema.
"""
from __future__ import annotations

import json

DEFAULT_PRIORITY = 50
PRIORITY_BY_TYPE = {
    "CONTENT_AUTOMATION": 20,
    "UPLOAD_LOCAL": 25,
    "TELEGRAM_TRANSFER": 25,
    "MEDIA_MANAGER": 25,
    "METADATA_SYNC": 40,
    "SUGGESTION_SCAN": 45,
    "ENTITY_IMAGE_SYNC": 55,
    "THUMBNAIL_INDEXER": 60,
    "METADATA_ASSETS": 50,
    "METADATA_CHANNEL_PIPELINE": 48,
    "ASSET_PREFETCH": 70,
    "PREVIEW_PREFETCH": 78,
}


def job_priority(job: dict) -> int:
    """Return a bounded integer priority for a persisted job row."""
    persisted = job.get("priority")
    if persisted not in (None, ""):
        try:
            return max(0, min(100, int(persisted)))
        except (TypeError, ValueError):
            pass
    base = PRIORITY_BY_TYPE.get(str(job.get("job_type") or ""), DEFAULT_PRIORITY)
    raw = job.get("detail") or ""
    try:
        detail = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        detail = {}
    try:
        override = int(detail.get("priority"))
    except Exception:
        override = base
    return max(0, min(100, override))
