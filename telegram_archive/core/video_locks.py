"""Cross-lane per-video locks shared across application and Media Manager loops.

A threading lock is intentional: Archive X has separate event loops for some
engines, so an asyncio.Lock would not be a safe global coordination primitive.
Acquisition is offloaded to a worker thread to avoid blocking either event loop.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import threading
from contextlib import asynccontextmanager
from pathlib import Path

_guard = threading.RLock()
_locks: dict[str, tuple[threading.Lock, int]] = {}
_held_keys: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar("archive_x_held_video_keys", default=frozenset())


def _key(*, identity_key: str = "", source_channel: str = "", source_message_id: int = 0,
         file_path: str | Path | None = None) -> str:
    identity = str(identity_key or "").strip().casefold()
    if identity:
        return "identity:" + identity
    channel, message = str(source_channel or "").strip(), int(source_message_id or 0)
    if channel and message > 0:
        return f"telegram:{channel}:{message}"
    if file_path:
        try:
            canonical = str(Path(file_path).expanduser().resolve(strict=False)).casefold()
        except Exception:
            canonical = str(file_path).strip().casefold()
        if canonical:
            return "path:" + hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()
    return "unspecified"


@asynccontextmanager
async def video_operation_lock(*, identity_key: str = "", source_channel: str = "",
                               source_message_id: int = 0, file_path: str | Path | None = None):
    """Serialize all lane work for one stable media identity, across event loops."""
    key = _key(identity_key=identity_key, source_channel=source_channel,
               source_message_id=source_message_id, file_path=file_path)
    # Workflow-level operations often call a snapshot helper for the same video.
    # Re-entering within the same async context is safe; independent lanes/tasks
    # do not inherit this context unless they are spawned by the owning operation.
    held = _held_keys.get()
    if key in held:
        yield key
        return
    with _guard:
        lock, refs = _locks.get(key, (threading.Lock(), 0))
        _locks[key] = (lock, refs + 1)
    acquired = False
    context_token = None
    try:
        await asyncio.to_thread(lock.acquire)
        acquired = True
        context_token = _held_keys.set(held | {key})
        yield key
    finally:
        if context_token is not None:
            _held_keys.reset(context_token)
        if acquired:
            lock.release()
        with _guard:
            current = _locks.get(key)
            if current and current[0] is lock:
                if current[1] <= 1 and not lock.locked():
                    _locks.pop(key, None)
                else:
                    _locks[key] = (lock, max(0, current[1] - 1))
