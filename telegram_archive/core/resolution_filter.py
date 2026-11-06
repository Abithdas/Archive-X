"""Resolution-tier helpers shared by both Telegram Transfer Manager sources."""
from __future__ import annotations

from typing import Any


RESOLUTION_FILTERS = ("ANY", "480P", "720P", "1080P", "1440P", "2160P", "4320P")

# Bands use the shorter video dimension, so portrait and landscape videos of
# the same tier are treated consistently.
_RESOLUTION_BANDS = (
    ("4320P", 4320, None),
    ("2160P", 2160, 4320),
    ("1440P", 1440, 2160),
    ("1080P", 1080, 1440),
    ("720P", 720, 1080),
    ("480P", 1, 720),
)


def normalize_resolution_filter(value: Any = None) -> str:
    """Validate a source-resolution selection; missing values preserve old behavior."""
    normalized = str(value or "ANY").strip().upper().replace(" ", "")
    if normalized.endswith("P") and normalized[:-1].isdigit():
        normalized = f"{normalized[:-1]}P"
    elif normalized.isdigit():
        normalized = f"{normalized}P"
    if normalized not in RESOLUTION_FILTERS:
        raise ValueError("Choose Any resolution or a supported resolution tier")
    return normalized


def resolution_tier_for_dimensions(width: Any, height: Any) -> str | None:
    """Return the tier for dimensions, or None when dimensions are unavailable."""
    try:
        short_edge = min(int(width or 0), int(height or 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if short_edge <= 0:
        return None
    for tier, lower_bound, upper_bound in _RESOLUTION_BANDS:
        if short_edge >= lower_bound and (upper_bound is None or short_edge < upper_bound):
            return tier
    return None


def matches_resolution_filter(width: Any, height: Any, selection: Any = None) -> bool:
    """Match a video to a requested source-resolution tier.

    Any includes videos whose dimensions are unknown. A specific tier does not:
    unknown dimensions are excluded rather than guessed.
    """
    normalized = normalize_resolution_filter(selection)
    if normalized == "ANY":
        return True
    return resolution_tier_for_dimensions(width, height) == normalized
