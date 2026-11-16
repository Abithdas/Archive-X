""""Only videos shorter than N minutes" for both Telegram Transfer Manager sources (local files and Telegram channels)."""
from __future__ import annotations

from typing import Any

MAX_MINUTES_LIMIT = 24 * 60


def normalize_max_minutes(value: Any = None) -> float | None:
    """The limit in minutes, or None for no limit. Empty, missing or 0 mean no limit; anything else must be a positive number."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        minutes = float(text)
    except ValueError as exc:
        raise ValueError("Enter the maximum duration in minutes as a number, or leave it empty for no limit") from exc
    if minutes != minutes or minutes in (float("inf"), float("-inf")) or minutes < 0:
        raise ValueError("The maximum duration must be a positive number of minutes")
    if minutes == 0:
        return None
    if minutes > MAX_MINUTES_LIMIT:
        raise ValueError(f"The maximum duration cannot be more than {MAX_MINUTES_LIMIT} minutes")
    return minutes


def within_limit(duration_seconds: Any, max_minutes: float | None) -> bool:
    """True when the video is strictly shorter than the limit (10 means under 10:00). No limit keeps everything; a video whose
    duration is unknown is not kept, rather than guessed."""
    if max_minutes is None:
        return True
    try:
        seconds = float(duration_seconds or 0)
    except (TypeError, ValueError):
        return False
    return 0 < seconds < float(max_minutes) * 60.0


def known(duration_seconds: Any) -> bool:
    """Whether a duration is a real, positive number of seconds."""
    try:
        return float(duration_seconds or 0) > 0
    except (TypeError, ValueError):
        return False


def describe(max_minutes: float | None) -> str:
    if max_minutes is None:
        return "any length"
    shown = int(max_minutes) if float(max_minutes).is_integer() else round(float(max_minutes), 2)
    return f"under {shown} min"


def message_duration_seconds(message: Any) -> float:
    """Duration of a Telegram video message in seconds (0 when Telegram does not say)."""
    candidates = [getattr(getattr(message, "file", None), "duration", None), getattr(getattr(message, "video", None), "duration", None)]
    document = getattr(getattr(message, "media", None), "document", None)
    for attr in getattr(document, "attributes", None) or []:
        candidates.append(getattr(attr, "duration", None))
    for value in candidates:
        if known(value):
            return float(value)
    return 0.0
