"""Shared Telethon client performance/safety policy.

Archive X routes Telegram API requests through one adaptive governor.  This
keeps independent modules (transfer, metadata, playback, thumbnails, etc.)
from creating their own request bursts and provides one place to react to
Telegram FloodWait/network feedback.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Request classification and reconnect listeners
#   - Governed client
#   - Proxy settings
#   - Client factory
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import ipaddress
import os
import re
import threading
import time
from urllib.parse import unquote, urlsplit

FLOOD_SLEEP_THRESHOLD_SECONDS = 0  # Archive X handles FloodWait centrally.
REQUEST_RETRIES = 3
UPLOAD_PART_SIZE_KB = 512
DOWNLOAD_PART_SIZE_KB = 512
DOWNLOAD_MAX_RETRIES = 4
DOWNLOAD_RETRY_DELAY_SECONDS = 2.0
_TELEGRAM_PROXY_SECRET_KEY = "telegram_proxy_settings"
_TELEGRAM_PROXY_CACHE_LOCK = threading.RLock()
_TELEGRAM_PROXY_RUNTIME_LOADED = False
_TELEGRAM_PROXY_RUNTIME: dict | None = None
_TELEGRAM_PROXY_RESTART_REQUIRED = False
CONNECTION_RETRIES = 12
CONNECTION_RETRY_DELAY_SECONDS = 2
RECONNECT_BUDGET_SECONDS = 90.0
RECONNECT_ATTEMPT_TIMEOUT_SECONDS = 20.0
_IDEMPOTENT_PREFIXES = ("Get", "Search", "Resolve", "Check", "Export", "SaveFilePart", "SaveBigFilePart")
_RECONNECT_LISTENERS: list = []


# ==============================================================================
# SECTION: Request classification and reconnect listeners
# Idempotency, auth errors and listeners.
# ==============================================================================

def _is_idempotent_request(request) -> bool:
    name = type(request).__name__
    return name.endswith("Request") and name.startswith(_IDEMPOTENT_PREFIXES)


def _is_authorization_error(exc) -> bool:
    name = type(exc).__name__
    return any(token in name for token in ("Unauthorized", "AuthKey", "SessionRevoked", "SessionExpired", "UserDeactivated"))


def add_reconnect_listener(callback) -> None:
    """Register ``callback(client, ok, reason, attempts, error)`` for recoveries."""
    if callback not in _RECONNECT_LISTENERS:
        _RECONNECT_LISTENERS.append(callback)


def _notify_reconnect_listeners(client, ok, reason, attempts, error) -> None:
    for callback in list(_RECONNECT_LISTENERS):
        try:
            callback(client, ok, reason, attempts, error)
        except Exception:
            _swallowed_note("client_policy._notify_reconnect_listeners")
            pass


# ==============================================================================
# SECTION: Governed client
# Client configuration and GovernedTelegramClient.
# ==============================================================================

def configure_telethon_client(client):
    """Apply the same bounded FloodWait/retry policy to every client."""
    try:
        client.flood_sleep_threshold = FLOOD_SLEEP_THRESHOLD_SECONDS
    except Exception:
        _swallowed_note("client_policy.configure_telethon_client")
        pass
    try:
        client.request_retries = REQUEST_RETRIES
    except Exception:
        _swallowed_note("client_policy.configure_telethon_client")
        pass
    return client


# The governed client itself lives in governed_client.py: it subclasses the Telegram library, which takes about 0.4 s to import, so it is loaded when the first
# client is made (make_telegram_client below) and not when the application starts.




# ==============================================================================
# SECTION: Proxy settings
# Environment and saved proxies plus runtime state.
# ==============================================================================

def telegram_proxy_from_environment() -> dict | None:
    """Parse an optional proxy URL without exposing its credentials in errors."""
    raw = os.environ.get("TELEGRAM_PROXY_URL", "").strip()
    if not raw:
        return None

    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        schemes = {
            "socks5": ("socks5", True),
            "socks5h": ("socks5", True),
            "socks4": ("socks4", False),
            "socks4a": ("socks4", True),
            "http": ("http", True),
        }
        if scheme not in schemes or not parsed.hostname:
            raise ValueError
        port = parsed.port
        if not port or not 1 <= port <= 65535:
            raise ValueError
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
        proxy_type, rdns = schemes[scheme]
        return {
            "proxy_type": proxy_type,
            "addr": parsed.hostname,
            "port": port,
            "rdns": rdns,
            "username": unquote(parsed.username) if parsed.username is not None else None,
            "password": unquote(parsed.password) if parsed.password is not None else None,
        }
    except (TypeError, ValueError, UnicodeError):
        raise ValueError(
            "Invalid TELEGRAM_PROXY_URL. Use socks5://, socks5h://, socks4://, "
            "socks4a://, or http:// with a host and port."
        ) from None


def _proxy_host(value) -> str:
    host = str(value or "").strip()
    if (
        not host
        or len(host) > 253
        or any(char.isspace() or char in "/?#@" for char in host)
    ):
        raise ValueError("Enter a valid proxy host.")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if ":" in host or not re.fullmatch(
            r"(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*\.?",
            host,
        ):
            raise ValueError("Enter a valid proxy host.") from None
        return host.rstrip(".")


def _proxy_port(value) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        port = int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Enter a proxy port from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("Enter a proxy port from 1 to 65535.")
    return port


def _validate_proxy_settings(data: dict, current: dict | None = None) -> dict:
    """Validate and normalize proxy settings submitted by the user."""
    current = current if isinstance(current, dict) else {}
    proxy_type = str(data.get("type") or "direct").strip().lower()
    if proxy_type == "direct":
        return {"type": "direct"}
    if proxy_type not in {"socks4", "socks5", "http", "mtproto"}:
        raise ValueError("Choose Direct, SOCKS4, SOCKS5, HTTP, or MTProto.")

    host = _proxy_host(data.get("host"))
    port = _proxy_port(data.get("port"))
    if proxy_type in {"socks4", "socks5", "http"}:
        clear_auth = bool(data.get("clear_auth"))
        previous = current if current.get("type") == proxy_type and not clear_auth else {}
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not username:
            username = str(previous.get("username") or "")
        if not password:
            password = str(previous.get("password") or "")
        if len(username) > 512 or len(password) > 512:
            raise ValueError("Proxy username or password is too long.")
        if any(ord(char) < 32 for char in username + password):
            raise ValueError("Proxy credentials cannot contain control characters.")
        return {
            "type": proxy_type,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        }

    secret = str(data.get("secret") or "").strip()
    if not secret and not data.get("clear_secret") and current.get("type") == "mtproto":
        secret = str(current.get("secret") or "")
    if not secret or len(secret) > 512 or any(ord(char) < 33 for char in secret):
        raise ValueError("Enter a valid MTProto proxy secret.")
    try:
        from telethon.network import connection
        normalized_secret = connection.ConnectionTcpMTProxyRandomizedIntermediate.normalize_secret(secret)
    except Exception:
        raise ValueError("Enter a valid MTProto proxy secret.") from None
    if len(normalized_secret) != 16:
        raise ValueError("Enter a valid MTProto proxy secret.")
    return {"type": proxy_type, "host": host, "port": port, "secret": secret}


def _read_saved_proxy_settings() -> dict:
    from .. import config
    settings = config.get_secret(_TELEGRAM_PROXY_SECRET_KEY, {})
    if settings in (None, ""):
        return {}
    if not isinstance(settings, dict):
        raise ValueError("Saved Telegram proxy settings are invalid.")
    return dict(settings)


def _proxy_settings_from_environment() -> dict | None:
    raw = os.environ.get("TELEGRAM_PROXY_URL", "").strip()
    if not raw:
        return None
    proxy = telegram_proxy_from_environment()
    scheme = urlsplit(raw).scheme.lower()
    proxy_type = "http" if scheme == "http" else proxy["proxy_type"]
    return {
        "type": proxy_type,
        "host": proxy["addr"],
        "port": proxy["port"],
        "username": proxy.get("username") or "",
        "password": proxy.get("password") or "",
        "_source": "environment",
    }


def telegram_proxy_settings_for_ui() -> dict:
    """Return proxy form state without returning any credential or MTProto secret."""
    environment_value = os.environ.get("TELEGRAM_PROXY_URL", "").strip()
    if environment_value:
        try:
            settings = _proxy_settings_from_environment()
            return {
                "source": "environment",
                "status": "configured",
                "type": settings["type"],
                "host": settings["host"],
                "port": settings["port"],
                "username_saved": bool(settings.get("username")),
                "password_saved": bool(settings.get("password")),
                "secret_saved": False,
                "restart_required": False,
            }
        except ValueError:
            return {
                "source": "environment",
                "status": "invalid",
                "type": "direct",
                "host": "",
                "port": "",
                "username_saved": False,
                "password_saved": False,
                "secret_saved": False,
                "restart_required": False,
            }

    try:
        raw_settings = _read_saved_proxy_settings()
        settings = _validate_proxy_settings(raw_settings, raw_settings) if raw_settings else {"type": "direct"}
        status = "direct" if settings["type"] == "direct" else "configured"
    except ValueError:
        settings = {"type": "direct"}
        status = "invalid"
    return {
        "source": "app",
        "status": status,
        "type": settings.get("type", "direct"),
        "host": settings.get("host", ""),
        "port": settings.get("port", ""),
        "username_saved": bool(settings.get("username")),
        "password_saved": bool(settings.get("password")),
        "secret_saved": bool(settings.get("secret")),
        "restart_required": _TELEGRAM_PROXY_RESTART_REQUIRED,
    }


def save_telegram_proxy_settings(data: dict) -> dict:
    """Save in-app proxy options in Archive X's encrypted credential vault."""
    global _TELEGRAM_PROXY_RESTART_REQUIRED
    if os.environ.get("TELEGRAM_PROXY_URL", "").strip():
        raise ValueError(
            "Proxy settings are managed by TELEGRAM_PROXY_URL in the runtime environment."
        )
    if not isinstance(data, dict):
        raise ValueError("Proxy settings must be an object.")
    current = _read_saved_proxy_settings()
    settings = _validate_proxy_settings(data, current)
    from .. import config
    config.set_secret(
        _TELEGRAM_PROXY_SECRET_KEY,
        None if settings["type"] == "direct" else settings,
    )
    _TELEGRAM_PROXY_RESTART_REQUIRED = True
    return telegram_proxy_settings_for_ui()


def _load_telegram_proxy_runtime() -> dict:
    environment_value = os.environ.get("TELEGRAM_PROXY_URL", "").strip()
    if environment_value:
        settings = _proxy_settings_from_environment()
    else:
        raw_settings = _read_saved_proxy_settings()
        settings = _validate_proxy_settings(raw_settings, raw_settings) if raw_settings else {"type": "direct"}

    proxy_type = settings["type"]
    runtime = {"type": proxy_type, "proxy": None, "connection": None}
    if proxy_type in {"socks5", "http"}:
        runtime["proxy"] = {
            "proxy_type": proxy_type,
            "addr": settings["host"],
            "port": settings["port"],
            "rdns": True,
            "username": settings.get("username") or None,
            "password": settings.get("password") or None,
        }
    elif proxy_type == "socks4":
        runtime["proxy"] = {
            "proxy_type": "socks4",
            "addr": settings["host"],
            "port": settings["port"],
            "rdns": False,
            "username": settings.get("username") or None,
            "password": settings.get("password") or None,
        }
    elif proxy_type == "mtproto":
        from telethon.network import connection
        runtime["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
        runtime["proxy"] = (settings["host"], settings["port"], settings["secret"])
    runtime["source"] = settings.get("_source", "app")
    return runtime


def _active_telegram_proxy_runtime() -> dict:
    global _TELEGRAM_PROXY_RUNTIME_LOADED, _TELEGRAM_PROXY_RUNTIME
    with _TELEGRAM_PROXY_CACHE_LOCK:
        if not _TELEGRAM_PROXY_RUNTIME_LOADED:
            _TELEGRAM_PROXY_RUNTIME = _load_telegram_proxy_runtime()
            _TELEGRAM_PROXY_RUNTIME_LOADED = True
        runtime = _TELEGRAM_PROXY_RUNTIME or {"type": "direct", "proxy": None, "connection": None}
        return {
            **runtime,
            "proxy": dict(runtime["proxy"]) if isinstance(runtime.get("proxy"), dict) else runtime.get("proxy"),
        }


# ==============================================================================
# SECTION: Client factory
# Flood notes and make_telegram_client.
# ==============================================================================

def _upload_floodwait_seconds(exc) -> int:
    """Recognize both FLOOD_WAIT_X and FLOOD_PREMIUM_WAIT_X upload errors."""
    try:
        seconds = int(getattr(exc, "seconds", 0) or 0)
        if seconds > 0:
            return seconds
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"FLOOD(?:_PREMIUM)?_WAIT[_ ](\d+)", str(exc or ""), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _note_flood(governor, seconds, request_name, playback=False):
    """Tell the governor which lane and request flooded (V29.4.130).

    Older/duck-typed governors that only accept ``seconds`` still work.
    """
    lane_for = getattr(governor, "flood_lane_for", None)
    kind = lane_for(request_name, playback) if callable(lane_for) else None
    try:
        governor.note_floodwait(seconds, kind=kind, request=request_name)
    except TypeError:
        governor.note_floodwait(seconds)


def make_telegram_client(*args, governor=None, playback_priority=False, **kwargs):
    """Create a consistently governed Telethon client."""
    from os import PathLike
    from .encrypted_session import EncryptedSQLiteSession
    proxy_runtime = _active_telegram_proxy_runtime()
    if "proxy" not in kwargs:
        if proxy_runtime.get("proxy") is not None:
            kwargs["proxy"] = proxy_runtime["proxy"]
        if proxy_runtime.get("connection") is not None:
            kwargs["connection"] = proxy_runtime["connection"]
    args = list(args)
    if args and isinstance(args[0], (str, PathLike)):
        args[0] = EncryptedSQLiteSession(args[0])
    elif isinstance(kwargs.get("session"), (str, PathLike)):
        kwargs["session"] = EncryptedSQLiteSession(kwargs["session"])
    from .governed_client import GovernedTelegramClient   # loads the Telegram library, now that a client is wanted
    client = GovernedTelegramClient(
        *args,
        governor=governor,
        playback_priority=playback_priority,
        **kwargs,
    )
    if "proxy" in kwargs:
        try:
            setattr(client, "_archive_x_proxy_type", proxy_runtime.get("type"))
        except Exception:
            _swallowed_note("client_policy.make_telegram_client")
            pass
    return configure_telethon_client(client)
