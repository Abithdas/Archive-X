"""PlaybackRegistry: lookup table of playback backends."""
from __future__ import annotations

from typing import Any


class PlaybackRegistry:
    """Backend registry; browser, VLC, and MPC-HC implementations can coexist."""

    def __init__(self) -> None:
        self._backends: dict[str, Any] = {}

    def register(self, backend: Any) -> None:
        name = str(getattr(backend, "name", "")).strip().lower()
        if not name:
            raise ValueError("Playback backend must define a name")
        self._backends[name] = backend

    def get(self, name: str) -> Any | None:
        return self._backends.get(str(name).strip().lower())

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))
