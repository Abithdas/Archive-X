"""Bundled UI font registry for Archive X.

UI fonts are shipped with the application and are never downloaded from the
internet or resolved through operating-system font fallback.  The web layer
serves these immutable bundled assets directly. Only Outfit is bundled (SIL OFL 1.1, licence text beside it).
"""
from __future__ import annotations

from pathlib import Path

_BUNDLED_FONT_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "fonts"

_FONT_SPECS = {
    "outfit-400.woff2": ("Outfit", 400),
    "outfit-500.woff2": ("Outfit", 500),
    "outfit-600.woff2": ("Outfit", 600),
    "outfit-700.woff2": ("Outfit", 700),
    "outfit-800.woff2": ("Outfit", 800),
}


def ensure_font(filename: str) -> Path | None:
    """Return the bundled font only; no network or OS-font resolution."""
    if filename not in _FONT_SPECS:
        return None
    target = _BUNDLED_FONT_DIR / filename
    return target if target.is_file() else None


def font_specs() -> dict[str, tuple[str, int]]:
    return dict(_FONT_SPECS)
