"""Application-wide local-first decision helpers.

This module is intentionally small. It does not perform network I/O; it gives
callers a common vocabulary for deciding whether local state is sufficient or
an external/provider operation is actually required.
"""
from __future__ import annotations

from dataclasses import dataclass


LOCAL_CACHE_HIT = "LOCAL_CACHE_HIT"
LOCAL_REFERENCE_HIT = "LOCAL_REFERENCE_HIT"
LOCAL_ASSET_HIT = "LOCAL_ASSET_HIT"
LOCAL_CATALOGUE_COMPLETE = "LOCAL_CATALOGUE_COMPLETE"
TELEGRAM_RANGE_REQUIRED = "TELEGRAM_RANGE_REQUIRED"
PROVIDER_REQUIRED = "PROVIDER_REQUIRED"
FULL_DOWNLOAD_REQUIRED = "FULL_DOWNLOAD_REQUIRED"
DEDUP_JOINED = "DEDUP_JOINED"
STALE_REFRESH_REQUIRED = "STALE_REFRESH_REQUIRED"


@dataclass(frozen=True)
class LocalFirstDecision:
    """Result of deciding between local data and an external request."""
    use_local: bool
    reason: str
    detail: str = ""


def local(reason: str, detail: str = "") -> LocalFirstDecision:
    return LocalFirstDecision(True, str(reason), str(detail or ""))


def external(reason: str, detail: str = "") -> LocalFirstDecision:
    return LocalFirstDecision(False, str(reason), str(detail or ""))


def caption_requires_remote_metadata(template: str, fields: dict) -> bool:
    """Return whether a Telegram caption needs identification/provider work.

    Filename/title/date/duration/resolution can be derived from the local
    catalogue or the Telegram message itself. Only placeholders whose values
    cannot be satisfied locally should trigger identification.
    """
    import re

    required = {str(x).lower() for x in re.findall(r"\{(\w+)\}", str(template or ""))}
    if not required:
        return False
    available = {str(k).lower() for k, value in fields.items() if value not in (None, "", [], ())}
    # Tags are intentionally excluded from Send-to-Telegram templates by policy.
    available.update({"hashtags", "performer_tags", "tag_tags", "studio_tag"})
    return bool(required - available)
