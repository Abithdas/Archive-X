"""Playback backends: the browser and external player processes (VLC, MPC)."""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from ...playback import vlc_launcher


@dataclass
class BackendState:
    """Current state of one playback backend."""
    opened: bool = False
    playing: bool = False
    source: str | None = None
    pid: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BrowserBackend:
    """Adapter for Archive X's in-browser playback surface.

    The browser owns play/pause/seek through HTMLMediaElement. The server-side
    adapter therefore records lifecycle intent but deliberately does not try to
    drive a browser tab from Python.
    """

    name = "browser"
    capabilities = frozenset({"open", "play", "pause", "seek", "close", "local_http"})

    def __init__(self, engine: Any | None = None):
        self.engine = engine
        self.state = BackendState()

    async def open(self, source: str) -> None:
        self.state = BackendState(opened=True, playing=False, source=str(source))

    async def play(self) -> None:
        self.state.playing = True

    async def pause(self) -> None:
        self.state.playing = False

    async def seek(self, seconds: float) -> None:
        self.state.metadata["seek_seconds"] = max(0.0, float(seconds))

    async def close(self) -> None:
        self.state = BackendState()

    def available(self) -> bool:
        return self.engine is not None

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available(), "capabilities": sorted(self.capabilities),
                "opened": self.state.opened, "playing": self.state.playing, "source": self.state.source}


class ExternalProcessBackend:
    """Common adapter for native Windows/Linux player processes."""

    name = "external"
    player_name = ""
    capabilities = frozenset({"open", "close", "native_process"})

    def __init__(self, launcher=vlc_launcher):
        self.launcher = launcher
        self.state = BackendState()
        self._availability: bool | None = None
        self._availability_at = 0.0

    def _find(self) -> str | None:
        finder = getattr(self.launcher, f"find_{self.player_name.replace('-', '_')}", None)
        return finder() if finder else None

    def available(self) -> bool:
        now = time.monotonic()
        if self._availability is not None and now - self._availability_at < 5.0:
            return self._availability
        self._availability = bool(self._find())
        self._availability_at = now
        return self._availability

    async def open(self, source: str) -> None:
        if not self.available():
            raise FileNotFoundError(f"{self.player_name} was not found on this PC")
        result = await __import__("asyncio").to_thread(
            self.launcher.launch, str(source), self.player_name, "Archive X"
        )
        self._availability = True
        self._availability_at = time.monotonic()
        self.state = BackendState(
            opened=True,
            playing=True,
            source=str(source),
            pid=int(result.get("pid") or 0) or None,
            metadata=dict(result),
        )

    async def play(self) -> None:
        raise NotImplementedError(f"{self.player_name} play control is owned by the native player")

    async def pause(self) -> None:
        raise NotImplementedError(f"{self.player_name} pause control is owned by the native player")

    async def seek(self, seconds: float) -> None:
        raise NotImplementedError(f"{self.player_name} seek control is owned by the native player")

    async def close(self) -> None:
        pid = self.state.pid
        self.state = BackendState()
        if pid:
            await __import__("asyncio").to_thread(self.launcher.stop, pid)

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.name, "player": self.player_name, "available": self.available(),
                "capabilities": sorted(self.capabilities), "opened": self.state.opened,
                "playing": self.state.playing, "source": self.state.source, "pid": self.state.pid}


class VLCBackend(ExternalProcessBackend):
    """External VLC player backend."""
    name = "vlc"
    player_name = "vlc"


class MPCBackend(ExternalProcessBackend):
    """External MPC-HC player backend."""
    name = "mpc-hc"
    player_name = "mpc-hc"


def build_default_backends(engine: Any | None = None, launcher=vlc_launcher) -> tuple[Any, ...]:
    """Construct adapters without starting any player or probing hardware eagerly."""
    return (
        BrowserBackend(engine=engine),
        VLCBackend(launcher=launcher),
        MPCBackend(launcher=launcher),
    )
