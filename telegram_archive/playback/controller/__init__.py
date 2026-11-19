"""Playback backend system: sessions, registry, controller and backend adapters."""
from .registry import PlaybackRegistry
from .session import PlaybackSession
from .controller import PlaybackController
from .adapters import BrowserBackend, VLCBackend, MPCBackend, build_default_backends

__all__ = [
    "PlaybackRegistry", "PlaybackSession", "PlaybackController",
    "BrowserBackend", "VLCBackend", "MPCBackend", "build_default_backends",
]
