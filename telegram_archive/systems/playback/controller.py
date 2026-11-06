"""PlaybackController: coordinates playback sessions and backends."""
from __future__ import annotations
import asyncio
from typing import Any
from .session import PlaybackSession
from .registry import PlaybackRegistry
from .adapters import build_default_backends


class PlaybackController:
    """Playback facade owning sessions and backend selection.

    Backend registration is intentionally separate from the existing streaming
    engine. Native-player availability is discovered only when requested by
    diagnostics/open, so application startup remains lightweight.
    """
    def __init__(self, engine: Any | None = None, registry: PlaybackRegistry | None = None,
                 register_defaults: bool = True):
        self.engine = engine
        self.registry = registry or PlaybackRegistry()
        self.sessions: dict[str, PlaybackSession] = {}
        self._lock = asyncio.Lock()
        if register_defaults:
            for backend in build_default_backends(engine=engine):
                self.registry.register(backend)

    def backend(self, name: str) -> Any:
        backend = self.registry.get(name)
        if backend is None:
            raise KeyError(f"Unknown playback backend: {name}")
        return backend

    async def open(self, backend_name: str, source: str) -> dict[str, Any]:
        backend = self.backend(backend_name)
        await backend.open(source)
        return self.backend_snapshot(backend_name)

    async def close_backend(self, backend_name: str) -> None:
        await self.backend(backend_name).close()

    def backend_snapshot(self, name: str) -> dict[str, Any]:
        backend = self.backend(name)
        snap = backend.snapshot() if hasattr(backend, "snapshot") else {"name": name}
        return dict(snap)

    def backend_status(self) -> dict[str, Any]:
        result = {}
        for name in self.registry.names():
            backend = self.registry.get(name)
            if hasattr(backend, "snapshot"):
                result[name] = backend.snapshot()
            else:
                result[name] = {"name": name, "available": True}
        return result

    async def session(self, media_id: str) -> PlaybackSession:
        key=str(media_id)
        async with self._lock:
            return self.sessions.setdefault(key, PlaybackSession(key))
    async def mark_seek(self, media_id: str, position: float) -> None:
        (await self.session(media_id)).mark_seek(position)
    async def new_seek(self, media_id: str, position: float) -> int:
        return (await self.session(media_id)).new_seek(position)
    async def begin_request(self, media_id: str, position: float | None = None) -> int:
        return (await self.session(media_id)).begin_request(position)
    async def end_request(self, media_id: str) -> None:
        (await self.session(media_id)).end_request()
    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            sessions={k:v.snapshot() for k,v in self.sessions.items()}
        return {"backend_names": list(self.registry.names()), "backends": self.backend_status(),
                "session_count": len(sessions), "sessions": sessions}
    async def close(self) -> None:
        for name in self.registry.names():
            try:
                await self.registry.get(name).close()
            except Exception:
                pass
        async with self._lock: self.sessions.clear()
