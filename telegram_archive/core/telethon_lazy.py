"""Access to the Telegram library (Telethon) without importing it when the application starts.

Importing Telethon takes about 0.4 s, and it is only needed once Telegram is used (a connection, a login, a download, an error to catch). Modules
that used to import it at the top ask for what they need here, at the moment they need it; the first call loads the library and later calls are free.
When it is not installed (diagnostics, minimal environments) `functions()` and `types()` give None and the error classes are stand-ins that never match,
which is what the old import fallbacks did.
"""
from __future__ import annotations

import importlib.util


class _Unavailable(Exception):
    """Stands in for a Telethon error class when the library is not installed: nothing is ever raised as it, so no `except` clause matches."""


def available() -> bool:
    """Whether the library is installed (asked without importing it)."""
    return importlib.util.find_spec("telethon") is not None


def functions():
    try:
        from telethon import functions as module
        return module
    except ImportError:
        return None


def types():
    try:
        from telethon import types as module
        return module
    except ImportError:
        return None


class _Errors:
    """`errors().FloodWaitError` and the other classes the application catches; stand-ins that never match when the library is missing."""
    def __getattr__(self, name: str):
        try:
            from telethon import errors as module
        except ImportError:
            return _Unavailable
        return getattr(module, name)


_errors = _Errors()


def errors() -> _Errors:
    return _errors


def document_attribute_filename():
    """The class that names an uploaded file (None when the library is missing)."""
    try:
        from telethon.tl.types import DocumentAttributeFilename
        return DocumentAttributeFilename
    except ImportError:
        return None


def memory_session():
    """A new in-memory session (None when the library is missing)."""
    try:
        from telethon.sessions import MemorySession
        return MemorySession()
    except ImportError:
        return None
