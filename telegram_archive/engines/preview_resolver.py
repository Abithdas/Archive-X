"""Shared preview resolution and bounded transient-failure retries.

Archive X keeps preview endpoints cache-only. This module centralizes bounded
retries for short-lived network failures instead of marking the first timeout as a permanent asset failure.
"""
from __future__ import annotations

import asyncio
import errno
from typing import Any, Callable


_RETRYABLE_ERROR_NAMES = (
    "timeout", "connectionreset", "connectionaborted", "connectionerror",
    "servererror", "rpc_call_fail", "temporarilyunavailable", "networkerror",
    "incompleteread", "brokenpipe", "clientconnector", "clientoserror",
    "serverdisconnected", "connecterror", "hostunreachable",
)
_RETRYABLE_ERRNOS = {errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
                     errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ECONNREFUSED,
                     errno.EPIPE}


def is_transient_preview_error(exc: BaseException) -> bool:
    """Return true only for failures that plausibly recover on a short retry."""
    name = type(exc).__name__.casefold()
    text = str(exc).casefold()
    # Honor Telegram's explicit cooldown before any generic "timeout" text
    # matching; a FloodWait must never be retried by this short backoff policy.
    if "floodwait" in name or "flood wait" in text:
        return False
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in _RETRYABLE_ERRNOS:
        return True
    if any(token.casefold() in name for token in _RETRYABLE_ERROR_NAMES):
        return True
    if any(token in text for token in ("connection reset", "temporarily unavailable", "timed out", "server disconnected")):
        return True
    return False


async def retry_async(
    operation: Callable[[], Any], *, attempts: int = 3,
    base_delay: float = 0.25, max_delay: float = 2.0,
    retryable: Callable[[BaseException], bool] = is_transient_preview_error,
    on_retry: Callable[[BaseException, int, float], Any] | None = None,
) -> Any:
    """Await a factory with bounded exponential backoff for transient failures."""
    attempts = max(1, min(5, int(attempts or 1)))
    base_delay = max(0.0, float(base_delay))
    max_delay = max(base_delay, float(max_delay))
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if hasattr(result, "__await__"):
                result = await result
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt >= attempts or not retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            if on_retry is not None:
                try:
                    on_retry(exc, attempt, delay)
                except Exception:
                    pass
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable retry state")


def resolve_cached_thumbnail(db, channel_id: int, message_id: int) -> dict[str, Any] | None:
    """Resolve the canonical Archive X-owned representative snapshot only.

    Never returns a Telegram-native thumbnail as a substitute for the Archive X
    snapshot. If the current snapshot cache row is missing/corrupt, the normal
    background asset worker remains responsible for repairing it.
    """
    from .thumbnail_cache import playback_snapshot_cached_info_sync

    try:
        # The card poster is the 3rd of the 36 snapshots. Videos scanned before
        # that rule keep a different stored still, so serve frame 3 straight from
        # the validated local frame set when it exists (no rebuild, no Telegram).
        try:
            from .preview_prefetch import representative_frame_path, MIN_FRAME_BYTES
            from PIL import Image
            frame = representative_frame_path(int(channel_id), int(message_id))
            if frame.is_file() and frame.stat().st_size >= MIN_FRAME_BYTES:
                frame_bytes = frame.read_bytes()
                import io
                with Image.open(io.BytesIO(frame_bytes)) as frame_img:
                    frame_img.verify()
                return {"data": frame_bytes, "mime": "image/jpeg", "source": "playback-snapshot",
                        "width": 0, "height": 0, "visual_hash": ""}
        except Exception:
            pass
        info = playback_snapshot_cached_info_sync(db, int(channel_id), int(message_id))
        if not info or not info.get("data"):
            return None
        data = bytes(info["data"])
        # Validate the bytes before the web route gives them a long-lived cache
        # header. Pillow is already a required application dependency.
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as image_file:
            image_file.verify()
        return {**info, "data": data, "source": "playback-snapshot"}
    except Exception:
        return None

