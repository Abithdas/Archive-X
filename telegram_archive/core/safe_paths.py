"""One place that turns "a folder plus a name that came from a request" into a path that cannot leave the folder."""
from __future__ import annotations

import os
from pathlib import Path


def inside(base: str | os.PathLike, *parts: object) -> Path:
    """``base / parts``, resolved; raises ValueError when the result is not inside ``base`` (``..``, absolute parts, links out)."""
    root = os.path.realpath(os.fspath(base))
    candidate = os.path.realpath(os.path.join(root, *[str(p) for p in parts]))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise ValueError("path escapes its folder")
    return Path(candidate)
