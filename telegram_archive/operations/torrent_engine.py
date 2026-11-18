"""Torrent download backends behind one interface: libtorrent, qBittorrent and an isolated libtorrent worker process."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Hashing and native value helpers
#   - Engine data types and interface
#   - LibtorrentEngine
#   - QBittorrentEngine
#   - Isolated libtorrent worker process
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import faulthandler
import hashlib
import json
import re
import shutil
import tempfile
import multiprocessing
import threading
import time
import traceback
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .qbittorrent_supervisor import QBittorrentSupervisor

import aiohttp

try:  # Deliberately lazy at runtime: the rest of the app can still boot and show a clear prerequisite error.
    import libtorrent as lt  # type: ignore
except Exception as exc:  # pragma: no cover - exercised only on installations without libtorrent
    lt = None  # type: ignore[assignment]
    _LT_IMPORT_ERROR: Exception | None = exc
else:
    _LT_IMPORT_ERROR = None




# ==============================================================================
# SECTION: Hashing and native value helpers
# Info hashes, native conversions and video file choice.
# ==============================================================================

def magnet_info_hash(source: str) -> str:
    """v1 info-hash (40 hex chars, lower case) from a magnet link, or ''."""
    m = re.search(r"xt=urn:btih:([0-9a-zA-Z]{32,40})", str(source or ""))
    if not m:
        return ""
    value = m.group(1)
    if len(value) == 40 and re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value.lower()
    if len(value) == 32:
        import base64
        try:
            return base64.b32decode(value.upper()).hex()
        except Exception:
            return ""
    return ""


def torrent_file_info_hash(data: bytes) -> str:
    """v1 info-hash of a .torrent file: SHA-1 of the bencoded 'info' value."""
    def skip(i: int) -> int:
        c = data[i:i + 1]
        if c == b"i":
            return data.index(b"e", i) + 1
        if c in (b"l", b"d"):
            i += 1
            while data[i:i + 1] != b"e":
                i = skip(i)
            return i + 1
        if c.isdigit():
            colon = data.index(b":", i)
            return colon + 1 + int(data[i:colon])
        raise ValueError("invalid bencode")
    try:
        if data[:1] != b"d":
            return ""
        i = 1
        while data[i:i + 1] != b"e":
            colon = data.index(b":", i)
            key_end = colon + 1 + int(data[i:colon])
            key = data[colon + 1:key_end]
            value_end = skip(key_end)
            if key == b"info":
                return hashlib.sha1(data[key_end:value_end]).hexdigest()
            i = value_end
    except Exception:
        return ""
    return ""


class TorrentEngineError(RuntimeError):
    """A torrent backend operation failed."""
    pass


def _native_int(value: Any, default: int = 0) -> int:
    """Return a plain Python ``int`` that Boost.Python can always convert.

    libtorrent's Python bindings are Boost.Python.  Boost refuses a Python
    ``float`` (and any float-like object) where C++ expects an integer, and
    reports it as::

        No registered converter was able to produce a C++ rvalue of type
        __int64 from this Python object of type float

    Config values that survive a JSON round-trip, computed sizes/offsets and
    numpy-style scalars can all arrive as floats.  Every number this module
    hands to libtorrent is passed through here first, so that conversion
    failure cannot happen from any call site.
    """
    if isinstance(value, bool):
        return int(value)
    try:
        return int(round(float(value)))
    except Exception:
        try:
            return int(default)
        except Exception:
            return 0


def _native_bool(value: Any) -> bool:
    return bool(value)


def _native_str(value: Any) -> str:
    return str(value if value is not None else "")


def _short_traceback(limit: int = 4) -> str:
    """Compact ``file:line in function`` trail for the most recent exception.

    The worker process runs in its own interpreter, so a traceback printed
    there would be invisible.  This string travels back over the pipe and is
    shown in Operations, which turns "something failed" into "this exact line
    failed".
    """
    try:
        frames = traceback.format_exc(limit=limit).strip().splitlines()
        wanted = [line.strip() for line in frames if line.strip().startswith("File ")]
        compact = []
        for line in wanted[-limit:]:
            match = re.match(r'File "(?P<f>[^"]+)", line (?P<l>\d+), in (?P<fn>.+)', line)
            if match:
                compact.append(f"{Path(match.group('f')).name}:{match.group('l')} in {match.group('fn')}")
            else:
                compact.append(line)
        return " <- ".join(reversed(compact))
    except Exception:
        return ""


# Canonical video-extension set for this module -- automation.py imports this
# rather than keeping its own separate copy (that used to exist and could
# silently drift out of sync with what select_video_only() below treats as
# "a video file").
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".m4p", ".mov", ".qt", ".mkv", ".mk3d", ".avi",
    ".divx", ".wmv", ".asf", ".flv", ".f4v", ".f4p", ".mpg",
    ".mpeg", ".mpe", ".mpv", ".m2v", ".mpeg1", ".mpeg2", ".webm",
    ".ts", ".mts", ".m2ts", ".m2t", ".3gp", ".3g2", ".vob",
    ".ogv", ".ogx", ".rm", ".rmvb", ".amv", ".mxf", ".roq",
    ".nsv", ".yuv",
}

# V29.4.130: extras that share the main video's extension. A file counts as a
# sample/extra when "sample" (or "trailer") is a separate word in its name or
# folder, or when it is under 10% of the largest video in the torrent (the
# size idea DownloadBot uses to pick the real files).
_EXTRA_WORD = re.compile(r"(?:^|[^a-z0-9])(sample|trailer)(?:[^a-z0-9]|$)", re.IGNORECASE)
EXTRA_SIZE_FRACTION = 0.10


def choose_video_files(files) -> list[int]:
    """Pick which torrent files to download: the real videos only.

    ``files`` is a sequence of ``(index, name, size)``. Samples, trailers and
    tiny extras are skipped when at least one real video remains; if every
    video looks like an extra, all videos are kept (never select nothing
    because of this rule).
    """
    videos = [(int(i), str(n or ""), max(0, int(s or 0))) for i, n, s in files
              if Path(str(n or "")).suffix.lower() in VIDEO_EXTENSIONS]
    if not videos:
        return []
    largest = max(size for _, _, size in videos)

    def is_extra(name: str, size: int) -> bool:
        if _EXTRA_WORD.search(name.replace("\\", "/")):
            return True
        return largest > 0 and size < largest * EXTRA_SIZE_FRACTION

    main = [i for i, name, size in videos if not is_extra(name, size)]
    return main or [i for i, _, _ in videos]

# One shared qBittorrent category for all Archive X automation torrents.
# Keeping a single category prevents a new qBittorrent category from being
# created for every RSS release and makes strict serial queue management
# predictable across application restarts.
ARCHIVE_X_QBIT_CATEGORY = "Archive X"
LEGACY_ARCHIVE_X_CATEGORY_PREFIX = "Archive X-"

# qBittorrent states that represent an Archive X torrent consuming a download slot.
# This is deliberately scoped to Archive X categories so unrelated qBittorrent
# torrents are never paused/stopped by Content Automation.
QBIT_DOWNLOAD_ACTIVE_STATES = {
    # Resume-data checking consumes a real download slot; ignoring it can start
    # another torrent while qBittorrent is still restoring the existing item.
    "downloading", "stalledDL", "queuedDL", "checkingDL", "checkingResumeData",
    "metadataDL", "metaDL", "forcedMetaDL", "allocating", "moving", "forcedDL",
}




# ==============================================================================
# SECTION: Engine data types and interface
# TorrentInfo and the DownloadEngine base class.
# ==============================================================================

@dataclass
class TorrentInfo:
    """Backend-independent status of one torrent."""
    hash: str
    name: str
    progress: float
    state: str
    content_path: str = ""
    save_path: str = ""
    size: int = 0
    download_rate: int = 0
    upload_rate: int = 0
    eta: int = -1
    error: str = ""
    num_peers: int = 0
    num_seeds: int = 0
    has_metadata: bool = False
    paused: bool = False
    upload_mode: bool = False
    total_wanted_done: int = 0
    total_wanted: int = 0

    # Build a TorrentInfo from a libtorrent status object.
    @classmethod
    def from_status(cls, engine: "LibtorrentEngine", status: Any) -> "TorrentInfo":
        handle = getattr(status, "handle", None)
        torrent_hash = engine._handle_hash(handle) if handle is not None else ""
        name = str(getattr(status, "name", "") or "")
        save_path = str(getattr(status, "save_path", "") or "")
        total_wanted = int(getattr(status, "total_wanted", 0) or 0)
        total_wanted_done = int(getattr(status, "total_wanted_done", 0) or 0)
        total_size = int(total_wanted or getattr(status, "total", 0) or 0)
        state = engine._state_name(getattr(status, "state", None))
        err_obj = getattr(status, "errc", None)
        try:
            message = getattr(err_obj, "message", "")
            error = str(message() if callable(message) else message or "")
        except Exception:
            error = str(err_obj or "")
        content_path = str(Path(save_path) / name) if save_path and name else save_path
        eta = int(getattr(status, "eta", -1) or -1)
        if eta < 0:
            eta = -1
        download_rate = int(
            getattr(status, "download_payload_rate", 0)
            or getattr(status, "download_rate", 0)
            or 0
        )
        upload_rate = int(
            getattr(status, "upload_payload_rate", 0)
            or getattr(status, "upload_rate", 0)
            or 0
        )
        return cls(
            hash=torrent_hash,
            name=name,
            progress=float(getattr(status, "progress", 0.0) or 0.0),
            state=state,
            content_path=content_path,
            save_path=save_path,
            size=total_size,
            download_rate=download_rate,
            upload_rate=upload_rate,
            eta=eta,
            error=error,
            num_peers=int(getattr(status, "num_peers", 0) or 0),
            num_seeds=int(getattr(status, "num_seeds", 0) or 0),
            has_metadata=bool(getattr(status, "has_metadata", False)),
            paused=bool(getattr(status, "paused", False)),
            upload_mode=bool(getattr(status, "upload_mode", False)),
            total_wanted_done=total_wanted_done,
            total_wanted=total_wanted,
        )


class DownloadEngine:
    """Interface shared by all torrent backends."""
    @staticmethod
    def _is_archive_category(category: object) -> bool:
        value = str(category or "")
        return value == ARCHIVE_X_QBIT_CATEGORY or value.startswith(LEGACY_ARCHIVE_X_CATEGORY_PREFIX)

    async def _require_archive_owned(self, torrent_hash: str, *, allow_missing: bool = False) -> dict[str, Any] | None:
        rows = await self._info_rows(params={"hashes": str(torrent_hash)})
        if not rows:
            if allow_missing:
                return None
            raise TorrentEngineError(f"qBittorrent torrent {torrent_hash} was not found")
        row = rows[0]
        if not self._is_archive_category(row.get("category")):
            raise TorrentEngineError(
                f"Refusing to control qBittorrent torrent {torrent_hash}: it is not owned by the Archive X category"
            )
        return row

    async def add(self, source: str, *, save_path: str = "", category: str = "") -> str:
        raise NotImplementedError

    async def get(self, torrent_hash: str) -> Optional[TorrentInfo]:
        raise NotImplementedError

    async def list(self, *, category: str = "") -> list[TorrentInfo]:
        raise NotImplementedError

    async def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def select_video_only(self, torrent_hash: str) -> bool:
        raise NotImplementedError

    async def prepare_download(self, torrent_hash: str) -> dict[str, Any]:
        """Start the torrent and enable sequential/first-last piece priority when supported."""
        return {"force_start": False, "sequential": False, "first_last_piece": False}

    async def pause(self, torrent_hash: str) -> None:
        raise NotImplementedError

    async def resume(self, torrent_hash: str) -> None:
        raise NotImplementedError

    async def recheck(self, torrent_hash: str) -> bool:
        """Re-verify the downloaded data against the torrent (V29.4.130).

        Used once for a torrent that stalls just short of 100%. Returns False
        when the engine cannot do it.
        """
        return False

    async def remove(self, torrent_hash: str, *, delete_files: bool = False) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None

    async def version(self) -> dict[str, str]:
        raise NotImplementedError




# ==============================================================================
# SECTION: LibtorrentEngine
# In-process libtorrent backend.
# ==============================================================================

class LibtorrentEngine(DownloadEngine):
    """Embedded BitTorrent download engine backed directly by libtorrent-rasterbar.

    This intentionally exposes only the small subset the Operations pipeline needs:
    add, list, get, file discovery, cleanup, and version/prerequisite checks.
    There is no external torrent-client process, WebUI, credential store, or API dependency.
    """

    def __init__(self, *, metadata_dir: str | Path | None = None, listen_port: int = 6881,
                 enable_dht_upnp: bool = False, allow_incoming_peers: bool = False):
        self._require_libtorrent()
        self.metadata_dir = Path(metadata_dir or Path(tempfile.gettempdir()) / "telegram_archive_libtorrent")
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.torrent_cache_dir = self.metadata_dir / "torrent_cache"
        self.torrent_cache_dir.mkdir(parents=True, exist_ok=True)
        self.listen_port = max(0, _native_int(listen_port, 6881))
        self.enable_dht_upnp = _native_bool(enable_dht_upnp)
        self.allow_incoming_peers = _native_bool(allow_incoming_peers)
        # Filled in by _create_session(); surfaced through version() so the
        # Operations panel can show how the session was actually created.
        self.session_notes: list[str] = []
        self._session = self._create_session()
        self._handles: dict[str, Any] = {}
        self._sources: dict[str, str] = {}
        self._meta_path = self.metadata_dir / "sources.json"
        self._load_sources()
        self._lock = asyncio.Lock()

    @staticmethod
    def _libtorrent_version_tuple() -> tuple[int, int, int] | None:
        """Return the installed libtorrent version as a comparable tuple.

        ``None`` is returned for non-standard/mocked bindings. Real wheels
        expose a normal dotted version string, so production installs still
        receive the 2.1.1 floor check.
        """
        if lt is None:
            return (0, 0, 0)
        raw = str(getattr(lt, "version", "") or "").strip()
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw)
        if not match:
            return None
        return tuple(int(part) for part in match.groups())  # type: ignore[return-value]

    @staticmethod
    def _require_libtorrent() -> None:
        if lt is None:
            detail = f" ({_LT_IMPORT_ERROR})" if _LT_IMPORT_ERROR else ""
            raise TorrentEngineError(
                "Embedded torrent engine is unavailable. Install the bundled Python dependency "
                "'libtorrent>=2.1.1,<2.2'." + detail
            )
        version = LibtorrentEngine._libtorrent_version_tuple()
        if version is not None and (version < (2, 1, 1) or version >= (2, 2, 0)):
            detected = str(getattr(lt, "version", "unknown"))
            raise TorrentEngineError(
                "Embedded torrent engine requires libtorrent>=2.1.1,<2.2; "
                f"detected {detected}. Close Archive X completely, run Setup-And-Run.ps1 "
                "again to repair the bundled dependency, then restart the application."
            )

    def _create_session(self):
        """Create the native session defensively, and never fail on tuning.

        Two independent failure sources are handled here:

        1. The *constructor*.  Different libtorrent wheels expose different
           ``session()`` overloads.  Passing a settings dictionary can select a
           native overload that rejects a value outright::

               No registered converter was able to produce a C++ rvalue of type
               __int64 from this Python object of type float

           Several constructor forms are therefore attempted in order, from the
           documented no-argument form downwards, and the first one that works
           is used.  Which form succeeded (and what the others said) is recorded
           in ``session_notes`` so Operations can display it.

        2. The *settings*.  Every value is coerced to a real Python
           ``bool``/``int``/``str`` and applied one at a time, so a wheel that
           does not expose a single tuning key can never stop the engine from
           starting.
        """
        notes: list[str] = []
        session = self._new_native_session(notes)

        bool_settings = {
            "enable_dht": _native_bool(self.enable_dht_upnp),
            "enable_lsd": _native_bool(self.enable_dht_upnp),
            "enable_upnp": _native_bool(self.enable_dht_upnp),
            "enable_natpmp": _native_bool(self.enable_dht_upnp),
            # Outbound TCP/uTP remain available for downloading, while inbound
            # peer connections are disabled by default. This materially reduces
            # the native attack surface of an embedded torrent client.
            "enable_incoming_tcp": _native_bool(getattr(self, "allow_incoming_peers", False)),
            "enable_incoming_utp": _native_bool(getattr(self, "allow_incoming_peers", False)),
            "enable_outgoing_tcp": True,
            "enable_outgoing_utp": True,
        }
        int_settings = {
            "active_downloads": _native_int(1),
            "active_seeds": _native_int(1),
            "active_limit": _native_int(2),
            "connections_limit": _native_int(300),
            "unchoke_slots_limit": _native_int(20),
            "download_rate_limit": _native_int(0),
            "upload_rate_limit": _native_int(0),
            "peer_turnover": _native_int(1),
        }
        str_settings = {
            "listen_interfaces": _native_str(f"0.0.0.0:{_native_int(self.listen_port)}"),
            "user_agent": _native_str("Telegram-Archive-EmbeddedTorrent/18.43.4"),
        }

        skipped: list[str] = []
        for group, caster in (
            (bool_settings, _native_bool),
            (int_settings, _native_int),
            (str_settings, _native_str),
        ):
            for name, value in group.items():
                try:
                    session.apply_settings({name: caster(value)})
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:  # binding/version specific, never fatal
                    skipped.append(f"{name} ({type(exc).__name__})")

        if skipped:
            notes.append("Settings not supported by this libtorrent build: " + ", ".join(skipped))
        if self.enable_dht_upnp and any(entry.startswith("enable_dht ") for entry in skipped):
            notes.append("DHT could not be enabled; peer discovery relies on trackers only")
        if not self.allow_incoming_peers:
            notes.append("Inbound TCP/uTP peers disabled; outbound peer connections remain enabled")

        self.session_notes = notes
        return session

    def _new_native_session(self, notes: list[str]):
        """Return a live ``lt.session`` using the first constructor that works."""
        strategies: list[tuple[str, Any]] = [("lt.session()", lambda: lt.session())]

        params_cls = getattr(lt, "session_params", None)
        if params_cls is not None:
            strategies.append(("lt.session(session_params())", lambda: lt.session(params_cls())))

        pack_cls = getattr(lt, "settings_pack", None)
        if pack_cls is not None:
            strategies.append(("lt.session(settings_pack())", lambda: lt.session(pack_cls())))

        listen = _native_str(f"0.0.0.0:{_native_int(self.listen_port)}")
        strategies.append(("lt.session({listen_interfaces})", lambda: lt.session({"listen_interfaces": listen})))
        strategies.append(("lt.session({})", lambda: lt.session({})))

        failures: list[str] = []
        for label, build in strategies:
            try:
                session = build()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                failures.append(f"{label} -> {type(exc).__name__}: {exc}")
                continue
            notes.append(f"Session created with {label}")
            if failures:
                notes.append("Constructor fallbacks used after: " + " | ".join(failures))
            return session

        raise TorrentEngineError(
            "Could not create libtorrent session. Tried: " + " | ".join(failures)
        )

    @staticmethod
    def _state_name(state: Any) -> str:
        if lt is None:
            return "unknown"
        names = (
            "checking_files",
            "downloading_metadata",
            "allocating",
            "downloading",
            "finished",
            "seeding",
            "checking_resume_data",
            "error",
        )
        for name in names:
            value = getattr(getattr(lt, "torrent_status", object()), name, None)
            if value is not None and state == value:
                return name
        # Some generated bindings stringify enum values as e.g. "torrent_status::downloading".
        text = str(state).lower()
        for name in names:
            if name in text:
                return name
        return text or "unknown"

    @staticmethod
    def _handle_hash(handle: Any) -> str:
        if handle is None:
            return ""
        try:
            hashes = handle.info_hashes()
            v1 = getattr(hashes, "v1", None)
            if v1 and str(v1):
                return str(v1).lower()
            v2 = getattr(hashes, "v2", None)
            if v2 and str(v2):
                return str(v2).lower()
        except Exception:
            _swallowed_note("torrent_engine.LibtorrentEngine._handle_hash")
            pass
        try:
            value = handle.info_hash()
            return str(value).lower()
        except Exception:
            return ""

    def _load_sources(self) -> None:
        try:
            payload = json.loads(self._meta_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._sources = {str(k): str(v) for k, v in payload.items() if v}
        except Exception:
            self._sources = {}

    def _save_sources(self) -> None:
        tmp = self._meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._sources, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._meta_path)

    async def version(self) -> dict[str, str]:
        self._require_libtorrent()
        value = _native_str(getattr(lt, "version", "unknown")) or "unknown"
        major = _native_int(getattr(lt, "version_major", 0) or 0)
        minor = _native_int(getattr(lt, "version_minor", 0) or 0)
        patch = _native_int(getattr(lt, "version_patch", 0) or 0)
        if value == "unknown" and major:
            value = f"{major}.{minor}.{patch}"
        return {
            "version": value,
            "backend": "libtorrent-rasterbar",
            "mode": "embedded",
            "notes": " · ".join(getattr(self, "session_notes", []) or []),
        }

    async def add(self, source: str, *, save_path: str = "", category: str = "") -> str:
        self._require_libtorrent()
        source = str(source or "").strip()
        if not source:
            raise ValueError("Torrent source is empty")
        root = Path(save_path or Path.cwd() / "downloads").expanduser()
        root.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            existing = self._find_handle_for_source(source)
            if existing is not None:
                digest = self._handle_hash(existing)
                if digest:
                    return digest

            try:
                handle = await self._add_source(source, root)
            except Exception as exc:
                raise TorrentEngineError(f"Could not add torrent: {exc}") from exc

            torrent_hash = self._handle_hash(handle)
            if not torrent_hash:
                # Metadata may not be available yet for a magnet. Keep a deterministic identity
                # only as a temporary key; get/list will switch to the real info hash when metadata arrives.
                torrent_hash = hashlib.sha1(source.encode("utf-8", "ignore")).hexdigest()
            self._handles[torrent_hash.lower()] = handle
            self._sources[torrent_hash.lower()] = source
            self._save_sources()
            return torrent_hash

    def _find_handle_for_source(self, source: str) -> Any | None:
        source_norm = source.strip().lower()
        for handle in list(self._handles.values()):
            try:
                if not handle.is_valid():
                    continue
                key = self._handle_hash(handle).lower()
                if key and self._sources.get(key, "").strip().lower() == source_norm:
                    return handle
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._find_handle_for_source")
                continue
        return None

    async def _add_source(self, source: str, root: Path):
        if source.lower().startswith("magnet:"):
            # libtorrent 2.x uses parse_magnet_uri() followed by add_torrent().
            return await asyncio.to_thread(self._sync_add_magnet, source, root)

        filename = hashlib.sha1(source.encode("utf-8", "ignore")).hexdigest() + ".torrent"
        cached = self.torrent_cache_dir / filename
        if cached.is_file():
            torrent_path = cached
        elif re.match(r"^https?://", source, re.I):
            torrent_bytes = await self._download_torrent_file(source)
            cached.write_bytes(torrent_bytes)
            torrent_path = cached
        else:
            torrent_path = Path(source).expanduser()
            if not torrent_path.is_file():
                raise FileNotFoundError(source)

        return await asyncio.to_thread(self._sync_add_torrent_file, torrent_path, root)

    def _sync_add_magnet(self, source: str, root: Path):
        params = lt.parse_magnet_uri(source)
        params.save_path = str(root)
        storage_mode_t = getattr(lt, "storage_mode_t", None)
        sparse_mode = getattr(storage_mode_t, "storage_mode_sparse", None) if storage_mode_t else None
        if sparse_mode is not None:
            params.storage_mode = sparse_mode
        return self._session.add_torrent(params)

    def _sync_add_torrent_file(self, torrent_path: Path, root: Path):
        params = lt.load_torrent_file(str(torrent_path))
        params.save_path = str(root)
        return self._session.add_torrent(params)

    async def _download_torrent_file(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=60)
        headers = {"User-Agent": "Telegram-Archive-EmbeddedTorrent/18.30"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    raise TorrentEngineError(f"Torrent file HTTP {response.status}")
                payload = await response.read()
        if len(payload) < 20:
            raise TorrentEngineError("Downloaded .torrent file is unexpectedly small")
        return payload

    def _handles_snapshot(self) -> list[Any]:
        out = []
        seen = set()
        for handle in list(self._handles.values()):
            try:
                if not handle.is_valid() or not handle.in_session():
                    continue
                key = id(handle)
                if key in seen:
                    continue
                seen.add(key)
                out.append(handle)
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._handles_snapshot")
                continue
        # Also discover handles that may have been added by libtorrent but aren't in our map.
        try:
            if hasattr(self._session, "get_torrent_status"):
                statuses = self._session.get_torrent_status(lambda _: True)
                for status in statuses:
                    handle = getattr(status, "handle", None)
                    if handle is None:
                        continue
                    key = id(handle)
                    if key not in seen:
                        out.append(handle)
                        seen.add(key)
        except Exception:
            _swallowed_note("torrent_engine.LibtorrentEngine._handles_snapshot")
            pass
        return out

    async def get(self, torrent_hash: str) -> Optional[TorrentInfo]:
        return await asyncio.to_thread(self._sync_get, torrent_hash)

    def _sync_get(self, torrent_hash: str) -> Optional[TorrentInfo]:
        self._require_libtorrent()
        target = str(torrent_hash or "").lower()
        if not target:
            return None
        for handle in self._handles_snapshot():
            try:
                actual = self._handle_hash(handle).lower()
                if actual == target or (actual and (target.startswith(actual) or actual.startswith(target))):
                    info = TorrentInfo.from_status(self, handle.status())
                    # A magnet can be present before metadata arrives. Keep the
                    # persistent temporary identity usable until the real hash exists.
                    if not info.hash:
                        info.hash = target
                    return info
                if not actual:
                    # Compare against our persisted handle keys for metadata-less magnets.
                    for key, candidate in self._handles.items():
                        if candidate is handle and key == target:
                            info = TorrentInfo.from_status(self, handle.status())
                            info.hash = target
                            return info
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_get")
                continue
        return None

    async def list(self, *, category: str = "") -> list[TorrentInfo]:
        return await asyncio.to_thread(self._sync_list, category=category)

    def _sync_list(self, *, category: str = "") -> list[TorrentInfo]:
        self._require_libtorrent()
        out: list[TorrentInfo] = []
        seen: set[str] = set()
        for key, handle in list(self._handles.items()):
            try:
                status = handle.status()
                info = TorrentInfo.from_status(self, status)
                identity = info.hash or key or f"{info.name}:{info.save_path}"
                if not info.hash and key:
                    info.hash = key
                if identity in seen:
                    continue
                seen.add(identity)
                out.append(info)
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_list")
                continue
        # Include any valid handles discovered by libtorrent that were not in our
        # local map; this is mainly defensive for a long-lived session.
        for handle in self._handles_snapshot():
            if any(handle is h for h in self._handles.values()):
                continue
            try:
                info = TorrentInfo.from_status(self, handle.status())
                identity = info.hash or f"{info.name}:{info.save_path}"
                if identity in seen:
                    continue
                seen.add(identity)
                out.append(info)
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_list")
                continue
        return out

    async def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._sync_files, torrent_hash)

    def _sync_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        self._require_libtorrent()
        target = str(torrent_hash or "").lower()
        for handle in self._handles_snapshot():
            try:
                actual = self._handle_hash(handle).lower()
                if actual != target and not target.startswith(actual) and not actual.startswith(target):
                    continue
                ti = handle.get_torrent_info()
                storage = ti.files()
                out = []
                for index in range(storage.num_files()):
                    native_index = _native_int(index)
                    try:
                        name = _native_str(storage.file_path(native_index))
                    except Exception:
                        name = ""
                    out.append({
                        "name": name,
                        "size": _native_int(storage.file_size(native_index)),
                        "index": int(native_index),
                    })
                return out
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_files")
                continue
        return []

    async def select_video_only(self, torrent_hash: str) -> bool:
        """Deselect every non-video file in the torrent so libtorrent never
        downloads samples/.nfo/posters/screenshots/etc -- only actual video
        files get piece priority. Returns True once applied (requires the
        torrent's metadata to already be available -- for a magnet link that
        can take a moment after add(), so callers should retry this on each
        poll until it returns True). Idempotent: safe to call repeatedly.
        """
        return await asyncio.to_thread(self._sync_select_video_only, torrent_hash)

    def _sync_select_video_only(self, torrent_hash: str) -> bool:
        """Set file priorities so that only chosen video files download."""
        self._require_libtorrent()
        target = str(torrent_hash or "").lower()
        for handle in self._handles_snapshot():
            try:
                actual = self._handle_hash(handle).lower()
                if actual != target and not target.startswith(actual) and not actual.startswith(target):
                    continue
                has_metadata = getattr(handle, "has_metadata", None)
                if callable(has_metadata) and not has_metadata():
                    return False  # magnet still resolving -- nothing to select yet
                ti = handle.get_torrent_info()
                storage = ti.files()
                num_files = storage.num_files()
                if num_files <= 0:
                    return False
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_select_video_only")
                continue
            listing = []
            for index in range(num_files):
                try:
                    name = _native_str(storage.file_path(_native_int(index)))
                except (IndexError, TypeError, ValueError, RuntimeError):
                    name = ""
                try:
                    size = _native_int(storage.file_size(_native_int(index)))
                except Exception:
                    size = 0
                listing.append((index, name, size))
            wanted = set(choose_video_files(listing))
            # 4 = default piece priority, 0 = don't download
            priorities = [_native_int(4) if index in wanted else _native_int(0) for index in range(num_files)]
            if not any(p > 0 for p in priorities):
                # Never silently download a whole torrent when no supported
                # video file was identified.  The caller can surface this
                # as a clear selection error instead.
                return False
            # Once the matching handle is found, a failure to apply the
            # priorities is a real torrent-state failure. Do not silently
            # continue to another handle and report success/false as though
            # the selection operation was merely a lookup miss.
            handle.prioritize_files([_native_int(p) for p in priorities])
            return True
        return False

    async def remove(self, torrent_hash: str, *, delete_files: bool = False) -> None:
        await asyncio.to_thread(self._sync_remove, torrent_hash, delete_files=delete_files)

    def _sync_remove(self, torrent_hash: str, *, delete_files: bool = False) -> None:
        self._require_libtorrent()
        target = str(torrent_hash or "").lower()
        handle = None
        key_to_remove = None
        for key, candidate in list(self._handles.items()):
            try:
                actual = self._handle_hash(candidate).lower()
                if actual == target or key == target or target.startswith(actual) or actual.startswith(target):
                    handle = candidate
                    key_to_remove = key
                    break
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_remove")
                continue
        if handle is None:
            return
        flags = None
        if delete_files:
            flags = getattr(getattr(lt, "session_handle", object()), "delete_files", None)
            if flags is None:
                flags = getattr(getattr(lt, "remove_flags_t", object()), "delete_files", None)
        try:
            if flags is None:
                self._session.remove_torrent(handle)
            else:
                self._session.remove_torrent(handle, flags)
        finally:
            if key_to_remove:
                self._handles.pop(key_to_remove, None)
                self._sources.pop(key_to_remove, None)
                self._save_sources()

    async def close(self) -> None:
        await asyncio.to_thread(self._sync_close)

    def _sync_close(self) -> None:
        session = getattr(self, "_session", None)
        if session is None:
            return
        try:
            for handle in self._handles_snapshot():
                try:
                    handle.pause()
                except Exception:
                    _swallowed_note("torrent_engine.LibtorrentEngine._sync_close")
                    pass
        finally:
            self._handles.clear()
            self._save_sources()
            try:
                abort = getattr(session, "abort", None)
                if callable(abort):
                    abort()
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_close")
                pass
            self._session = None



    def _matching_handles(self, torrent_hash: str) -> list[Any]:
        target = str(torrent_hash or "").lower()
        out = []
        for handle in self._handles_snapshot():
            try:
                actual = self._handle_hash(handle).lower()
                if actual and (actual == target or target.startswith(actual) or actual.startswith(target)):
                    out.append(handle)
                elif not actual and any(c is handle and k == target for k, c in self._handles.items()):
                    out.append(handle)
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._matching_handles")
                continue
        return out

    def _sync_set_paused(self, torrent_hash: str, paused: bool) -> None:
        self._require_libtorrent()
        flags = getattr(lt, "torrent_flags", None)
        auto_managed = getattr(flags, "auto_managed", None) if flags else None
        for handle in self._matching_handles(torrent_hash):
            # Take the torrent out of libtorrent's own queue first, otherwise
            # the queue manager can resume a paused torrent on its own.
            if auto_managed is not None and hasattr(handle, "unset_flags"):
                handle.unset_flags(auto_managed)
            elif hasattr(handle, "auto_managed"):
                handle.auto_managed(False)
            if paused:
                handle.pause()
            else:
                handle.resume()

    async def pause(self, torrent_hash: str) -> None:
        # Previously missing for the built-in engine: "Pause Automation"
        # raised NotImplementedError for every torrent on every pass.
        await asyncio.to_thread(self._sync_set_paused, torrent_hash, True)

    async def resume(self, torrent_hash: str) -> None:
        await asyncio.to_thread(self._sync_set_paused, torrent_hash, False)

    async def recheck(self, torrent_hash: str) -> bool:
        return await asyncio.to_thread(self._sync_recheck, torrent_hash)

    def _sync_recheck(self, torrent_hash: str) -> bool:
        self._require_libtorrent()
        target = str(torrent_hash or "").lower()
        for handle in self._handles_snapshot():
            try:
                actual = self._handle_hash(handle).lower()
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._sync_recheck")
                continue
            if actual != target and not target.startswith(actual) and not actual.startswith(target):
                continue
            force_recheck = getattr(handle, "force_recheck", None)
            if not callable(force_recheck):
                return False
            force_recheck()
            return True
        return False

    async def prepare_download(self, torrent_hash: str) -> dict[str, Any]:
        """Force-start one torrent and configure the production download strategy.

        The strategy is deliberately explicit:
        * remove auto-management so the application, not libtorrent's queue, owns
          the single active torrent;
        * resume the torrent;
        * use sequential piece picking;
        * temporarily boost the first and last piece of each selected video file;
        * once those edge pieces are complete, restore normal video priorities so
          sequential order takes over.
        """
        return await asyncio.to_thread(self._sync_prepare_download, torrent_hash)

    @staticmethod
    def _piece_done(handle: Any, status: Any, piece: int) -> bool:
        try:
            have_piece = getattr(handle, "have_piece", None)
            if callable(have_piece):
                return bool(have_piece(_native_int(piece)))
        except Exception:
            _swallowed_note("torrent_engine.LibtorrentEngine._piece_done")
            pass
        try:
            pieces = getattr(status, "pieces", None)
            return bool(pieces[piece]) if pieces is not None and 0 <= piece < len(pieces) else False
        except Exception:
            return False

    def _selected_video_edge_pieces(self, handle: Any) -> set[int]:
        ti = handle.get_torrent_info()
        storage = ti.files()
        num_pieces = _native_int(getattr(ti, "num_pieces", lambda: 0)() or 0)
        if num_pieces <= 0 or not hasattr(handle, "piece_priority"):
            return set()
        edges: set[int] = set()
        listing = []
        for index in range(storage.num_files()):
            name = _native_str(storage.file_path(_native_int(index)) or "")
            size = _native_int(storage.file_size(_native_int(index)) or 0)
            listing.append((index, name, size))
        # V29.4.130: only the files chosen for download (not samples/extras).
        wanted = set(choose_video_files(listing))
        for index, name, size in listing:
            if index not in wanted:
                continue
            if size <= 0:
                continue
            try:
                # map_file() takes a native int64 offset: coerce explicitly.
                first = storage.map_file(_native_int(index), _native_int(0), _native_int(1))
                last = storage.map_file(_native_int(index), _native_int(max(0, size - 1)), _native_int(1))
                for mapped in (first, last):
                    piece = _native_int(getattr(mapped, "piece", -1), -1)
                    if 0 <= piece < num_pieces:
                        edges.add(piece)
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentEngine._selected_video_edge_pieces")
                continue
        return edges

    def _sync_prepare_download(self, torrent_hash: str) -> dict[str, Any]:
        """Prioritize selected files and their edge pieces, then resume."""
        self._require_libtorrent()
        target = str(torrent_hash or "").lower()
        for handle in self._handles_snapshot():
            try:
                actual = self._handle_hash(handle).lower()
                if actual != target and not target.startswith(actual) and not actual.startswith(target):
                    continue
                has_metadata = getattr(handle, "has_metadata", None)
                if callable(has_metadata) and not has_metadata():
                    return {"force_start": False, "sequential": False, "first_last_piece": False,
                            "state": "downloading_metadata", "peers": 0, "seeds": 0,
                            "detail": "Waiting for torrent metadata"}

                flags = getattr(lt, "torrent_flags", None)
                auto_managed = getattr(flags, "auto_managed", None) if flags else None
                sequential_flag = getattr(flags, "sequential_download", None) if flags else None
                if auto_managed is not None and hasattr(handle, "unset_flags"):
                    handle.unset_flags(auto_managed)
                if sequential_flag is not None and hasattr(handle, "set_flags"):
                    handle.set_flags(sequential_flag)
                elif hasattr(handle, "set_sequential_download"):
                    handle.set_sequential_download(True)
                if hasattr(handle, "resume"):
                    handle.resume()
                if hasattr(handle, "set_priority"):
                    handle.set_priority(_native_int(255))

                status = handle.status()
                edges = self._selected_video_edge_pieces(handle)
                completed_edges = {p for p in edges if self._piece_done(handle, status, p)}
                active_edge_phase = bool(edges - completed_edges)

                # File priorities are already established by select_video_only().
                # During the startup phase, boost edge pieces so the video gets
                # a valid beginning and ending quickly.  Once both ends exist,
                # reset them to normal priority and let sequential mode pick from
                # the lowest remaining piece index.
                if hasattr(handle, "piece_priority"):
                    for piece in edges:
                        handle.piece_priority(_native_int(piece), _native_int(7 if active_edge_phase else 4))

                status = handle.status()
                state = self._state_name(getattr(status, "state", None))
                peers = int(getattr(status, "num_peers", 0) or 0)
                seeds = int(getattr(status, "num_seeds", 0) or 0)
                rate = int(getattr(status, "download_payload_rate", 0) or getattr(status, "download_rate", 0) or 0)
                error = str(getattr(getattr(status, "errc", None), "message", "") or "")
                if getattr(status, "upload_mode", False):
                    detail = "Torrent is in upload-only mode; checking disk/error state"
                elif error:
                    detail = error
                elif state == "downloading_metadata":
                    detail = "Waiting for torrent metadata"
                elif peers == 0 and rate == 0:
                    detail = "No peers connected yet; waiting for tracker/DHT peer discovery"
                elif rate == 0:
                    detail = "Connected but currently waiting for piece data"
                else:
                    detail = f"Downloading · {peers} peers · {seeds} seeds"
                return {
                    "force_start": True,
                    "sequential": True,
                    "first_last_piece": bool(edges),
                    "first_last_piece_active": active_edge_phase,
                    "first_last_piece_count": len(edges),
                    "first_last_piece_completed": len(completed_edges),
                    "state": state,
                    "peers": peers,
                    "seeds": seeds,
                    "download_rate": rate,
                    "detail": detail,
                }
            except Exception as exc:
                raise TorrentEngineError(f"Embedded torrent could not be prepared for download: {exc}") from exc
        raise TorrentEngineError("Embedded torrent disappeared before download preparation")




# ==============================================================================
# SECTION: QBittorrentEngine
# qBittorrent WebUI backend.
# ==============================================================================

class QBittorrentEngine(DownloadEngine):
    """qBittorrent Web API download engine.

    This backend delegates torrent networking to the user's installed
    qBittorrent instance instead of loading libtorrent inside Archive X.
    It implements the same small DownloadEngine surface used by Content
    Automation, so the sequential pipeline remains unchanged.
    """

    def __init__(self, *, base_url: str = "http://127.0.0.1:8080", username: str = "",
                 password: str = "", api_key: str = "", timeout: float = 20.0,
                 auto_start: bool = True, auto_install: bool = True, auto_configure_local_webui: bool = True):
        self.base_url = str(base_url or "http://127.0.0.1:8080").rstrip("/")
        self.username = str(username or "")
        self.password = str(password or "")
        self.api_key = str(api_key or "").strip()
        self.timeout = float(timeout)
        self.auto_start = bool(auto_start)
        self.auto_install = bool(auto_install)
        self.auto_configure_local_webui = bool(auto_configure_local_webui)
        self._session: aiohttp.ClientSession | None = None
        self._logged_in = False
        self._supervisor = QBittorrentSupervisor(
            self.base_url,
            auto_start=self.auto_start,
            auto_install=self.auto_install,
            auto_configure_local_webui=self.auto_configure_local_webui,
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.CookieJar(unsafe=True),
                                                  headers={"User-Agent": "Telegram-Archive-qBittorrent/18.43.7"})
        return self._session

    async def _request(self, method: str, path: str, *, data=None, params=None, json_data=False):
        session = await self._ensure_session()
        url = self.base_url + "/api/v2/" + path.lstrip("/")
        headers = {"Referer": self.base_url + "/", "Origin": self.base_url}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with session.request(method, url, data=data if not json_data else None,
                                       json=data if json_data else None, params=params, headers=headers) as resp:
                text = await resp.text()
                # qBittorrent 5.2.x may use HTTP 409 for an /torrents/add
                # response when every submitted source failed. Preserve the
                # structured JSON response so add() can expose the exact
                # success/pending/failure counts and reason. Other 4xx/5xx
                # responses remain hard API errors.
                if resp.status >= 400 and not (path.lstrip("/") == "torrents/add" and resp.status == 409):
                    raise TorrentEngineError(f"qBittorrent HTTP {resp.status}: {text[:300]}")
                return text
        except aiohttp.ClientError as exc:
            raise TorrentEngineError(f"Cannot reach qBittorrent at {self.base_url}: {exc}") from exc

    async def _login(self) -> None:
        if self._logged_in:
            return
        if self.auto_start:
            try:
                await self._supervisor.ensure_ready()
            except Exception as exc:
                raise TorrentEngineError(str(exc)) from exc
        if self.api_key:
            # qBittorrent >= 5.2 supports bearer API-key authentication.
            await self._request("GET", "app/version")
            self._logged_in = True
            return
        # qBittorrent can be configured to bypass localhost authentication.
        # Try the API directly first when credentials are intentionally empty.
        if not self.username and not self.password:
            try:
                await self._request("GET", "app/version")
                self._logged_in = True
                return
            except TorrentEngineError:
                pass
        if not self.username or not self.password:
            raise TorrentEngineError(
                "qBittorrent Web API requires username/password. Configure them in Content Automation settings."
            )
        text = await self._request("POST", "auth/login", data={"username": self.username, "password": self.password})
        if text.strip().lower() not in {"ok.", "ok"}:
            raise TorrentEngineError("qBittorrent login failed. Check WebUI username/password.")
        self._logged_in = True

    async def _api(self, method: str, path: str, *, data=None, params=None):
        await self._login()
        try:
            return await self._request(method, path, data=data, params=params)
        except TorrentEngineError as exc:
            if "HTTP 403" in str(exc) or "HTTP 401" in str(exc):
                self._logged_in = False
                await self._login()
                return await self._request(method, path, data=data, params=params)
            raise

    async def version(self) -> dict[str, str]:
        value = (await self._api("GET", "app/version")).strip()
        api_version = "unknown"
        try:
            api_version = (await self._api("GET", "app/webapiVersion")).strip() or "unknown"
        except TorrentEngineError:
            # Older qBittorrent releases do not expose this endpoint.
            pass
        return {"version": value or "unknown", "api_version": api_version,
                "backend": "qBittorrent Web API", "mode": "external-client"}

    async def _ensure_category(self, category: str, save_path: str) -> None:
        # WebAPI 2.x requires an explicit category creation step; keep this
        # idempotent by checking the category list first.
        try:
            raw = await self._api("GET", "torrents/categories")
            categories = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError) as exc:
            raise TorrentEngineError(f"Invalid qBittorrent categories response: {exc}") from exc
        except TorrentEngineError:
            categories = {}
        if isinstance(categories, dict) and category in categories:
            return
        try:
            await self._api("POST", "torrents/createCategory", data={"category": category, "savePath": save_path})
        except TorrentEngineError as exc:
            if "HTTP 409" not in str(exc):
                raise
            # Another process may have created it between the check and create.
            raw = await self._api("GET", "torrents/categories")
            try:
                categories = json.loads(raw or "{}")
            except json.JSONDecodeError as parse_exc:
                raise TorrentEngineError(f"Invalid qBittorrent categories response: {parse_exc}") from parse_exc
            if not isinstance(categories, dict) or category not in categories:
                raise

    async def _torrent_control(self, action: str, torrent_hashes: str) -> None:
        """Start/stop torrents across qBittorrent 5.x and legacy WebAPI variants.

        qBittorrent's newer v5 WebAPI renamed the old ``resume``/``pause``
        torrent-control routes to ``start``/``stop``. qBittorrent 5.2.x in the
        diagnostic report is returning 404 specifically for the old routes.
        Keep a compatibility fallback for older WebAPI variants instead of
        making the caller guess which API family is active.
        """
        action = str(action or "").strip().lower()
        if action not in {"start", "stop"}:
            raise ValueError(f"Unsupported torrent control action: {action}")
        preferred = f"torrents/{action}"
        legacy = "torrents/resume" if action == "start" else "torrents/pause"
        try:
            await self._api("POST", preferred, data={"hashes": str(torrent_hashes)})
            return
        except TorrentEngineError as exc:
            message = str(exc)
            if "HTTP 404" not in message and "Endpoint does not exist" not in message:
                raise
        # Compatibility fallback for older API variants.
        await self._api("POST", legacy, data={"hashes": str(torrent_hashes)})

    async def _archive_categories(self) -> list[str]:
        """Return the exact current and legacy Archive X categories once.

        Avoid torrents/info?filter=all for every scheduler reconciliation;
        unrelated qBittorrent torrents can number in the hundreds/thousands.
        """
        categories = [ARCHIVE_X_QBIT_CATEGORY]
        try:
            raw = await self._api("GET", "torrents/categories")
            data = json.loads(raw or "{}")
            if isinstance(data, dict):
                for name in data.keys():
                    value = str(name or "")
                    if value.startswith(LEGACY_ARCHIVE_X_CATEGORY_PREFIX) and value not in categories:
                        categories.append(value)
        except Exception:
            # The current category is always queried even if category metadata
            # is temporarily unavailable.
            _swallowed_note("torrent_engine.QBittorrentEngine._archive_categories")
            pass
        return categories

    async def list_automation_torrents(self) -> list[dict[str, Any]]:
        """Return every torrent in an Archive X category, including paused/seeding items.

        The scheduler needs the full backlog, not only actively downloading rows.
        Otherwise a completed/seeding or previously paused torrent can be invisible
        to the download-slot counter and a new RSS item may be added ahead of it.
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for category in await self._archive_categories():
            rows = await self._info_rows(params={"category": category})
            for row in rows:
                torrent_hash = str(row.get("hash") or "").strip()
                if not torrent_hash or torrent_hash.lower() in seen:
                    continue
                seen.add(torrent_hash.lower())
                out.append(dict(row))
        out.sort(key=lambda row: float(row.get("added_on") or 0))
        return out

    async def list_active_automation_torrents(self) -> list[dict[str, Any]]:
        """Return active download-state torrents owned by the Archive X category.

        qBittorrent queueing is global, while Archive X needs a per-category
        concurrency limit. We therefore inspect the Web API directly and manage
        only torrents in the Archive X categories. Unrelated torrents are
        never included.
        """
        out: list[dict[str, Any]] = []
        for category in await self._archive_categories():
            rows = await self._info_rows(params={"category": category})
            for row in rows:
                state = str(row.get("state") or "")
                if state in QBIT_DOWNLOAD_ACTIVE_STATES:
                    out.append(dict(row))
        out.sort(key=lambda row: float(row.get("added_on") or 0))
        return out

    async def reconcile_automation_download_limit(
        self, *, max_downloads: int = 1, keep_hashes: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Enforce Archive X's download limit using qBittorrent's Web API.

        qBittorrent's native queue limit is global, so changing
        ``max_active_downloads`` here would also affect the user's unrelated
        torrents. Instead, stop excess *Archive X* download torrents and
        leave all other categories untouched.
        """
        max_downloads = max(1, min(10, int(max_downloads or 1)))
        active = await self.list_active_automation_torrents()
        if not active:
            return {"active": [], "stopped": [], "orphaned": []}

        keep_set = {str(value or "").strip().lower() for value in keep_hashes if str(value or "").strip()}
        ordered: list[dict[str, Any]] = []
        for row in active:
            torrent_hash = str(row.get("hash") or "").strip().lower()
            if torrent_hash and torrent_hash in keep_set:
                ordered.append(row)
        for row in active:
            torrent_hash = str(row.get("hash") or "").strip().lower()
            if row not in ordered:
                ordered.append(row)

        allowed = ordered[:max_downloads]
        allowed_hashes = {str(row.get("hash") or "").strip().lower() for row in allowed}
        excess = [row for row in active if str(row.get("hash") or "").strip().lower() not in allowed_hashes]
        stopped: list[str] = []
        if excess:
            hashes = [str(row.get("hash") or "").strip() for row in excess if str(row.get("hash") or "").strip()]
            if hashes:
                await self._torrent_control("stop", "|".join(hashes))
                stopped = hashes
        return {
            "active": [str(row.get("hash") or "").strip() for row in allowed if str(row.get("hash") or "").strip()],
            "stopped": stopped,
            "orphaned": [],
        }

    async def remove_all_automation_torrents(self, *, delete_files: bool = True) -> int:
        """Delete all Archive X-owned qBittorrent torrents.

        Only the Archive X automation category (including legacy category names)
        is affected; unrelated qBittorrent torrents are left untouched.
        """
        hashes: list[str] = []
        for category in await self._archive_categories():
            rows = await self._info_rows(params={"category": category})
            for row in rows:
                torrent_hash = str(row.get("hash") or "").strip()
                if torrent_hash:
                    hashes.append(torrent_hash)
        if not hashes:
            return 0
        await self._api(
            "POST",
            "torrents/delete",
            data={
                "hashes": "|".join(hashes),
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        deadline = time.monotonic() + 15.0
        remaining = set(h.lower() for h in hashes)
        categories = await self._archive_categories()
        while remaining and time.monotonic() < deadline:
            visible = set()
            for category in categories:
                rows = await self._info_rows(params={"category": category})
                visible.update(str(row.get("hash") or "").strip().lower() for row in rows if str(row.get("hash") or "").strip())
            remaining -= visible
            if remaining:
                await asyncio.sleep(0.5)
        if remaining:
            raise TorrentEngineError(
                "qBittorrent did not remove all Archive X torrents: " + ", ".join(sorted(remaining)[:10])
            )
        return len(hashes)

    async def pause_all_automation_torrents(self, *, except_hash: str = "") -> int:
        """Stop all Archive X torrents, leaving unrelated qBittorrent torrents alone."""
        rows = await self._info_rows(params={"filter": "all"})
        keep = str(except_hash or "").strip().lower()
        hashes: list[str] = []
        for row in rows:
            torrent_hash = str(row.get("hash") or "").strip()
            category = str(row.get("category") or "")
            state = str(row.get("state") or "")
            if (
                not torrent_hash
                or torrent_hash.lower() == keep
                or not (category == ARCHIVE_X_QBIT_CATEGORY or category.startswith(LEGACY_ARCHIVE_X_CATEGORY_PREFIX))
            ):
                continue
            if state in {"downloading", "forcedDL", "stalledDL", "queuedDL", "checkingDL", "checkingResumeData", "metadataDL", "metaDL", "forcedMetaDL", "allocating", "moving"}:
                hashes.append(torrent_hash)
        if hashes:
            await self._torrent_control("stop", "|".join(hashes))
        return len(hashes)

    async def add(self, source: str, *, save_path: str = "", category: str = "") -> str:
        """Add one source and return only a newly-created/identified hash."""
        source = str(source or "").strip()
        if not source:
            raise TorrentEngineError("Torrent source is empty")
        root_path = Path(save_path or Path.cwd() / "downloads").expanduser()
        try:
            root_path.mkdir(parents=True, exist_ok=True)
            if not root_path.is_dir():
                raise RuntimeError("configured download path is not a directory")
            probe = root_path / ".telegram_archive_write_test"
            probe.write_bytes(b"ok")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            raise TorrentEngineError(f"qBittorrent download folder is not writable: {root_path} ({exc})") from exc
        root = str(root_path)
        torrent_category = category.strip() if str(category or "").strip() else ARCHIVE_X_QBIT_CATEGORY
        await self._ensure_category(torrent_category, root)

        # Work out the torrent's own ID (info-hash) up front when possible.
        # The old way - "whatever new torrent appears in the category" - broke
        # when the release was already in qBittorrent (nothing new appears ->
        # error after 45 s) and could pick up an unrelated torrent.
        expected_hash = magnet_info_hash(source)
        if not expected_hash and re.match(r"^https?://", source, re.I):
            try:
                session = await self._ensure_session()
                async with session.get(source, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status < 400:
                        body = await resp.content.read(10 * 1024 * 1024 + 1)
                        if len(body) <= 10 * 1024 * 1024:
                            expected_hash = torrent_file_info_hash(body)
            except Exception:
                expected_hash = ""
        if expected_hash:
            existing = await self._info_rows(params={"hashes": expected_hash})
            if existing:
                existing_row = existing[0]
                if self._is_archive_category(existing_row.get("category")):
                    existing_root = str(existing_row.get("save_path") or "").strip()
                    try:
                        same_root = existing_root and Path(existing_root).expanduser().resolve(strict=False) == root_path.resolve(strict=False)
                    except Exception:
                        same_root = existing_root.casefold().rstrip("\\/") == root.casefold().rstrip("\\/")
                    if not same_root:
                        raise TorrentEngineError(
                            f"Archive X torrent {expected_hash} already exists in a different download folder "
                            f"({existing_root or 'unknown'}); refusing to reuse it for {root}."
                        )
                    return str(existing_row.get("hash") or expected_hash)
                raise TorrentEngineError(
                    f"Torrent {expected_hash} already exists in qBittorrent outside the {ARCHIVE_X_QBIT_CATEGORY} category; "
                    "Archive X will not take ownership of another qBittorrent torrent."
                )

        before_rows = await self._info_rows(params={"category": torrent_category})
        before_hashes = {str(r.get("hash") or "").strip().lower() for r in before_rows if str(r.get("hash") or "").strip()}
        started_at = time.time()
        # Add paused first. Archive X owns a category-scoped queue and will
        # explicitly start exactly one selected torrent after reconciliation.
        # This avoids qBittorrent starting every RSS release immediately when
        # the Web API receives several add requests.
        add_data = {
            "urls": source, "savepath": root, "category": torrent_category,
            "paused": "true", "skip_checking": "false",
            "sequentialDownload": "true", "firstLastPiecePrio": "true",
        }
        add_response = await self._api("POST", "torrents/add", data=add_data)
        clean_response = str(add_response or "").strip()
        try:
            payload = json.loads(clean_response) if clean_response else None
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            added = payload.get("added_torrent_ids")
            if isinstance(added, list):
                for candidate_hash in added:
                    candidate_hash = str(candidate_hash or "").strip()
                    if candidate_hash:
                        return candidate_hash
            candidate_hash = str(payload.get("torrent_id") or "").strip()
            if candidate_hash:
                return candidate_hash
            success_count = int(payload.get("success_count") or 0)
            pending_count = int(payload.get("pending_count") or 0)
            failure_count = int(payload.get("failure_count") or 0)
            if failure_count and not success_count and not pending_count:
                reason = payload.get("failure_reason") or payload.get("message") or "qBittorrent rejected the torrent"
                raise TorrentEngineError(f"qBittorrent rejected torrent source (success={success_count}, pending={pending_count}, failed={failure_count}): {reason}")
        elif clean_response.lower() in {"fails.", "fail", "failed"}:
            raise TorrentEngineError(f"qBittorrent rejected torrent source: {clean_response}")
        elif clean_response.lower() not in {"ok.", "ok", "success", ""}:
            raise TorrentEngineError(f"Unexpected qBittorrent torrents/add response: {clean_response[:300]}")

        # qBittorrent 5.2.x may return HTTP 202 with pending_count>0 while
        # it asynchronously fetches/creates a torrent from a remote URL.
        # Give that server-side operation enough time to materialize the new
        # torrent before declaring the add failed.
        pending_count = 0
        if isinstance(payload, dict):
            try:
                pending_count = int(payload.get("pending_count") or 0)
            except (TypeError, ValueError):
                pending_count = 0
        deadline = time.monotonic() + (180 if pending_count > 0 else 45)
        while time.monotonic() < deadline:
            if expected_hash:
                rows = await self._info_rows(params={"hashes": expected_hash})
                if rows:
                    return str(rows[0].get("hash") or expected_hash)
                await asyncio.sleep(0.5)
                continue
            rows = await self._info_rows(params={
                "category": torrent_category, "sort": "added_on", "reverse": "true", "limit": "50"
            })
            for row in rows:
                candidate_hash = str(row.get("hash") or "").strip()
                if not candidate_hash or candidate_hash.lower() in before_hashes:
                    continue
                added_on = float(row.get("added_on") or 0)
                if added_on <= 0 or added_on >= started_at - 5:
                    return candidate_hash
            await asyncio.sleep(0.5)
        raise TorrentEngineError(
            "qBittorrent accepted the add request but no newly-added torrent appeared within 45 seconds "
            f"(category={torrent_category}, save_path={root}). Response={clean_response[:200]!r}"
        )

    async def _info_rows(self, *, params=None) -> list[dict[str, Any]]:
        text = await self._api("GET", "torrents/info", params=params)
        try:
            payload = json.loads(text or "[]")
        except json.JSONDecodeError as exc:
            raise TorrentEngineError(f"Invalid qBittorrent torrent-info response: {exc}") from exc
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _state(row: dict[str, Any]) -> str:
        state = str(row.get("state") or "unknown")
        mapping = {
            "downloading": "downloading", "stalledDL": "stalled", "queuedDL": "queued",
            "pausedDL": "paused", "checkingDL": "checking", "checkingUP": "checking",
            "metadataDL": "downloading_metadata", "metaDL": "downloading_metadata", "allocating": "allocating",
            "uploading": "seeding", "stalledUP": "seeding", "queuedUP": "seeding",
            "pausedUP": "paused", "moving": "moving", "forcedDL": "downloading",
            # qBittorrent 5.x renamed pausedDL/pausedUP to stoppedDL/stoppedUP.
            # Unmapped, a stopped torrent looked like it was still running.
            "stoppedDL": "paused", "stoppedUP": "paused",
            "forcedUP": "seeding", "forcedMetaDL": "downloading_metadata",
            "checkingResumeData": "checking", "missingFiles": "error", "error": "error",
        }
        return mapping.get(state, state)

    @classmethod
    def _row_to_info(cls, row: dict[str, Any]) -> TorrentInfo:
        save_path = str(row.get("save_path") or "")
        name = str(row.get("name") or "")
        content_path = str(row.get("content_path") or (Path(save_path) / name if save_path and name else save_path))
        state = cls._state(row)
        size = int(row.get("size") or 0)
        amount_left = int(row.get("amount_left") or 0)
        return TorrentInfo(
            hash=str(row.get("hash") or ""), name=name, progress=float(row.get("progress") or 0),
            state=state, content_path=content_path, save_path=save_path,
            size=size, download_rate=int(row.get("dlspeed") or 0),
            upload_rate=int(row.get("upspeed") or 0), eta=int(row.get("eta") or -1),
            error=str(row.get("message") or row.get("tracker_msg") or ""),
            num_peers=int(row.get("num_seeds") or 0) + int(row.get("num_leechs") or 0),
            num_seeds=int(row.get("num_seeds") or 0),
            has_metadata=(state != "downloading_metadata"),
            paused=state == "paused",
            upload_mode=str(row.get("state") or "") in {"pausedUP", "stoppedUP"},
            total_wanted_done=max(0, size - amount_left), total_wanted=size,
        )

    async def get(self, torrent_hash: str) -> Optional[TorrentInfo]:
        rows = await self._info_rows(params={"hashes": torrent_hash})
        return self._row_to_info(rows[0]) if rows else None

    async def list(self, *, category: str = "") -> list[TorrentInfo]:
        params = {"category": category} if category else None
        return [self._row_to_info(row) for row in await self._info_rows(params=params)]

    async def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        text = await self._api("GET", "torrents/files", params={"hash": torrent_hash})
        try:
            rows = json.loads(text or "[]")
        except json.JSONDecodeError as exc:
            raise TorrentEngineError(f"Invalid qBittorrent file response: {exc}") from exc
        return [{"name": str(r.get("name") or ""), "size": int(r.get("size") or 0),
                 "index": int(r.get("index", i)), "progress": float(r.get("progress") or 0),
                 "priority": int(r.get("priority") or 0)} for i, r in enumerate(rows or [])]

    async def select_video_only(self, torrent_hash: str) -> bool:
        rows = await self.files(torrent_hash)
        if not rows:
            return False
        video_ids = [str(i) for i in choose_video_files(
            [(r["index"], r["name"], r.get("size") or 0) for r in rows])]
        if not video_ids:
            return False
        all_ids = [str(r["index"]) for r in rows]
        non_video = [i for i in all_ids if i not in set(video_ids)]
        if non_video:
            await self._api("POST", "torrents/filePrio", data={"hash": torrent_hash, "id": "|".join(non_video), "priority": "0"})
        await self._api("POST", "torrents/filePrio", data={"hash": torrent_hash, "id": "|".join(video_ids), "priority": "1"})
        # Verify the selection instead of assuming qBittorrent accepted it.
        verify = await self.files(torrent_hash)
        selected = {str(r["index"]): int(r.get("priority", 0)) for r in verify}
        if any(selected.get(i, 0) <= 0 for i in video_ids):
            raise TorrentEngineError("qBittorrent did not apply the requested video file priorities")
        if any(selected.get(i, 0) != 0 for i in non_video):
            raise TorrentEngineError("qBittorrent left non-video files selected")
        return True

    async def prepare_download(self, torrent_hash: str) -> dict[str, Any]:
        """Explicitly start the torrent and enable download-order controls.

        qBittorrent exposes these as toggle endpoints, so inspect current state
        first to make the operation idempotent across every automation poll.
        """
        rows = await self._info_rows(params={"hashes": torrent_hash})
        if not rows:
            raise TorrentEngineError("qBittorrent torrent disappeared before download preparation")
        row = rows[0]
        if not self._is_archive_category(row.get("category")):
            raise TorrentEngineError(
                f"Refusing to start qBittorrent torrent {torrent_hash}: it is outside the Archive X category"
            )
        # Do not pause other Archive X torrents here. Content Automation
        # controls concurrency centrally; this method only prepares/starts the
        # requested torrent. Startup reconciliation and the scheduler decide which
        # torrents are allowed to remain active.
        # Never leave an automation torrent waiting behind qBittorrent's global
        # queue. Force-start is intentionally scoped to this torrent only.
        await self._api("POST", "torrents/setForceStart", data={"hashes": torrent_hash, "value": "true"})
        await self._torrent_control("start", torrent_hash)

        sequential = bool(row.get("seq_dl"))
        first_last = bool(row.get("f_l_piece_prio"))
        if not sequential:
            await self._api("POST", "torrents/toggleSequentialDownload", data={"hashes": torrent_hash})
        if not first_last:
            await self._api("POST", "torrents/toggleFirstLastPiecePrio", data={"hashes": torrent_hash})

        # qBittorrent may acknowledge start before its state changes from
        # stoppedDL to checkingResumeData/downloading. Immediate state checks
        # produced false failures after an Archive X restart: the torrent became
        # active a few seconds later and raced the next dispatch. Poll for a bounded
        # transition, resend start once, and only then report a start failure.
        inactive_states = {"pausedDL", "queuedDL", "stoppedDL"}
        deadline = time.monotonic() + 8.0
        retried_start = False
        verified: dict[str, Any] = {}
        state = "unknown"
        while True:
            verify_rows = await self._info_rows(params={"hashes": torrent_hash})
            if not verify_rows:
                raise TorrentEngineError("qBittorrent torrent disappeared after download preparation")
            verified = verify_rows[0]
            state = str(verified.get("state") or "unknown")
            if state in {"missingFiles", "error"}:
                raise TorrentEngineError(f"qBittorrent reports the torrent as {state}; check the download folder/disk")
            if state not in inactive_states:
                break
            if not retried_start:
                retried_start = True
                await asyncio.sleep(0.35)
                await self._api("POST", "torrents/setForceStart", data={"hashes": torrent_hash, "value": "true"})
                await self._torrent_control("start", torrent_hash)
                continue
            if time.monotonic() >= deadline:
                raise TorrentEngineError(f"qBittorrent did not start torrent after retry; state={state}")
            await asyncio.sleep(0.4)
        return {
            "force_start": bool(verified.get("force_start", True)),
            "sequential": bool(verified.get("seq_dl", True)),
            "first_last_piece": bool(verified.get("f_l_piece_prio", True)),
            "state": state,
        }

    async def remove(self, torrent_hash: str, *, delete_files: bool = False) -> None:
        row = await self._require_archive_owned(torrent_hash, allow_missing=True)
        if row is None:
            return
        await self._api("POST", "torrents/delete", data={"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"})
        # qBittorrent may acknowledge deletion before the torrent disappears
        # from torrents/info. Wait briefly so callers never delete source files
        # while qBittorrent still owns the torrent.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            rows = await self._info_rows(params={"hashes": torrent_hash})
            if not rows:
                return
            await asyncio.sleep(0.5)
        raise TorrentEngineError(f"qBittorrent did not remove Archive X torrent {torrent_hash} within 15 seconds")

    async def pause(self, torrent_hash: str) -> None:
        await self._require_archive_owned(torrent_hash)
        await self._torrent_control("stop", str(torrent_hash))

    async def resume(self, torrent_hash: str) -> None:
        await self._require_archive_owned(torrent_hash)
        await self._torrent_control("start", str(torrent_hash))

    async def recheck(self, torrent_hash: str) -> bool:
        await self._require_archive_owned(torrent_hash)
        await self._api("POST", "torrents/recheck", data={"hashes": str(torrent_hash)})
        return True

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            try:
                if self._logged_in:
                    await self._request("POST", "auth/logout")
            except Exception:
                _swallowed_note("torrent_engine.QBittorrentEngine.close")
                pass
            await self._session.close()
        self._session = None
        self._logged_in = False


# ---------------------------------------------------------------------------
# Isolated worker process
# ---------------------------------------------------------------------------
# libtorrent-rasterbar is a native C++ extension.  A native access violation
# or DLL/networking failure cannot be caught by the parent Python process.
# Content Automation therefore runs the long-lived libtorrent session in a
# separate spawned process.  The web application communicates with that
# process through a small request/response pipe.  A worker crash becomes a
# normal TorrentEngineError in the parent instead of terminating FastAPI.




# ==============================================================================
# SECTION: Isolated libtorrent worker process
# Worker entry point and the RPC parent engine.
# ==============================================================================

def _torrent_worker_process_main(conn, metadata_dir: str, listen_port: int, enable_dht_upnp: bool, allow_incoming_peers: bool) -> None:
    """Entry point for the Windows-spawned libtorrent worker."""
    crash_log = None
    try:
        crash_path = Path(metadata_dir) / "torrent_worker_fatal.log"
        crash_path.parent.mkdir(parents=True, exist_ok=True)
        crash_log = crash_path.open("a", encoding="utf-8", buffering=1)
        try:
            crash_log.write(f"\n--- worker PID={multiprocessing.current_process().pid} started ---\n")
            faulthandler.enable(file=crash_log, all_threads=True)
        except Exception:
            _swallowed_note("torrent_engine._torrent_worker_process_main")
            pass
        engine = LibtorrentEngine(
            metadata_dir=metadata_dir,
            listen_port=listen_port,
            enable_dht_upnp=enable_dht_upnp,
            allow_incoming_peers=allow_incoming_peers,
        )

        async def loop() -> None:
            info = await engine.version()
            conn.send({"ok": True, "type": "ready", "result": info})
            while True:
                try:
                    request = await asyncio.to_thread(conn.recv)
                except (EOFError, OSError):
                    break
                if not isinstance(request, dict):
                    continue
                request_id = request.get("id")
                op = str(request.get("op") or "")
                try:
                    if op == "version":
                        result = await engine.version()
                    elif op == "add":
                        result = await engine.add(
                            str(request.get("source") or ""),
                            save_path=str(request.get("save_path") or ""),
                            category=str(request.get("category") or ""),
                        )
                    elif op == "get":
                        result_obj = await engine.get(str(request.get("torrent_hash") or ""))
                        result = result_obj.__dict__ if result_obj is not None else None
                    elif op == "list":
                        result = [item.__dict__ for item in await engine.list(category=str(request.get("category") or ""))]
                    elif op == "files":
                        result = await engine.files(str(request.get("torrent_hash") or ""))
                    elif op == "select_video_only":
                        result = await engine.select_video_only(str(request.get("torrent_hash") or ""))
                    elif op == "prepare_download":
                        result = await engine.prepare_download(str(request.get("torrent_hash") or ""))
                    elif op == "pause":
                        await engine.pause(str(request.get("torrent_hash") or ""))
                        result = None
                    elif op == "resume":
                        await engine.resume(str(request.get("torrent_hash") or ""))
                        result = None
                    elif op == "recheck":
                        result = await engine.recheck(str(request.get("torrent_hash") or ""))
                    elif op == "remove":
                        await engine.remove(
                            str(request.get("torrent_hash") or ""),
                            delete_files=bool(request.get("delete_files")),
                        )
                        result = None
                    elif op == "close":
                        await engine.close()
                        conn.send({"ok": True, "id": request_id, "result": None})
                        return
                    else:
                        raise TorrentEngineError(f"Unknown torrent worker operation: {op}")
                    conn.send({"ok": True, "id": request_id, "result": result})
                except Exception as exc:
                    conn.send({
                        "ok": False,
                        "id": request_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": _short_traceback(),
                    })

        asyncio.run(loop())
    except BaseException as exc:
        # If libtorrent aborts the native process, this code is never reached.
        # For normal Python failures, make the failure visible to the parent --
        # including where it happened, because this process has no console.
        try:
            conn.send({
                "ok": False,
                "type": "fatal",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": _short_traceback(),
            })
        except Exception:
            _swallowed_note("torrent_engine._torrent_worker_process_main")
            pass
    finally:
        try:
            conn.close()
        except Exception:
            _swallowed_note("torrent_engine._torrent_worker_process_main")
            pass
        try:
            if crash_log is not None:
                crash_log.close()
        except Exception:
            _swallowed_note("torrent_engine._torrent_worker_process_main")
            pass


class LibtorrentWorkerEngine(DownloadEngine):
    """Process-isolated libtorrent engine used by long-running automation.

    The public API intentionally mirrors ``DownloadEngine``.  The parent
    application never holds a libtorrent session; only the spawned child does.
    If the child exits unexpectedly, the next request transparently creates a
    fresh session.  Content Automation can then reattach the same torrent from
    its saved URL and reuse partial data on disk.
    """

    def __init__(self, *, metadata_dir: str | Path | None = None, listen_port: int = 6881,
                 enable_dht_upnp: bool = False, allow_incoming_peers: bool = False, startup_timeout: float = 20.0):
        self.metadata_dir = Path(metadata_dir or Path(tempfile.gettempdir()) / "telegram_archive_libtorrent")
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.listen_port = max(0, int(listen_port))
        self.enable_dht_upnp = bool(enable_dht_upnp)
        self.allow_incoming_peers = bool(allow_incoming_peers)
        self.startup_timeout = float(startup_timeout)
        self._ctx = multiprocessing.get_context("spawn")
        self._process = None
        self._conn = None
        self._lock = threading.RLock()
        self._next_id = 0
        self._last_worker_error = ""
        # How the worker's libtorrent session was built (constructor used,
        # settings the installed wheel rejected). Shown by "Test engine".
        self.worker_notes = ""
        self._diagnostic_path = self.metadata_dir / "torrent_worker_events.log"
        self._last_exit_path = self.metadata_dir / "torrent_worker_last_exit.json"
        self._write_parent_event("engine_init", {
            "pid": os.getpid(),
            "python": sys.executable,
            "metadata_dir": str(self.metadata_dir),
            "libtorrent_requirement": ">=2.1.1,<2.2",
        })
        self._start_process()

    def _write_parent_event(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """Write diagnostics from the surviving parent process.

        A native access violation can terminate the child before faulthandler
        flushes its file. Parent-side diagnostics therefore remain available
        even when the embedded libtorrent worker dies in native code.
        """
        try:
            record = {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "event": str(event),
                **(payload or {}),
            }
            with self._diagnostic_path.open("a", encoding="utf-8", buffering=1) as fh:
                fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
        except Exception:
            _swallowed_note("torrent_engine.LibtorrentWorkerEngine._write_parent_event")
            pass

    def _record_worker_exit(self, *, operation: str, process: Any, exit_code: int | None) -> None:
        payload = {
            "operation": str(operation),
            "worker_pid": getattr(process, "pid", None),
            "exit_code": exit_code,
            "exit_code_hex": (f"0x{(int(exit_code) & 0xFFFFFFFF):08X}" if exit_code is not None else None),
            "metadata_dir": str(self.metadata_dir),
            "python": sys.executable,
        }
        self._write_parent_event("worker_exit", payload)
        try:
            self._last_exit_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            _swallowed_note("torrent_engine.LibtorrentWorkerEngine._record_worker_exit")
            pass

    def _start_process(self) -> None:
        """Start the libtorrent worker process and wait for it to be ready."""
        with self._lock:
            if self._process is not None and self._process.is_alive():
                return
            if self._process is not None and not self._process.is_alive():
                self._discard_worker_locked()
            parent_conn, child_conn = self._ctx.Pipe(duplex=True)
            process = self._ctx.Process(
                target=_torrent_worker_process_main,
                args=(child_conn, str(self.metadata_dir), self.listen_port, self.enable_dht_upnp, self.allow_incoming_peers),
                name="Archive X-TorrentWorker",
            )
            process.daemon = True
            process.start()
            child_conn.close()
            self._conn = parent_conn
            self._process = process
            self._write_parent_event("worker_started", {
                "worker_pid": process.pid,
                "python": sys.executable,
                "metadata_dir": str(self.metadata_dir),
            })
            try:
                if not parent_conn.poll(self.startup_timeout):
                    exit_code = process.exitcode
                    self._discard_worker_locked()
                    raise TorrentEngineError(
                        f"Embedded torrent worker did not start within {self.startup_timeout:.0f}s"
                        + (f" (exit code {exit_code})" if exit_code is not None else "")
                    )
                ready = parent_conn.recv()
            except (EOFError, OSError) as exc:
                code = process.exitcode
                self._record_worker_exit(operation="startup", process=process, exit_code=code)
                self._discard_worker_locked()
                raise TorrentEngineError(
                    f"Embedded torrent worker exited during startup"
                    + (f" (exit code {code})" if code is not None else "")
                    + f": {exc}"
                ) from exc
            if not isinstance(ready, dict) or not ready.get("ok") or ready.get("type") != "ready":
                if isinstance(ready, dict):
                    error = str(ready.get("error") or "unknown worker startup failure")
                    where = str(ready.get("traceback") or "")
                    if where:
                        error = f"{error} [at {where}]"
                else:
                    error = str(ready)
                self._last_worker_error = error
                # Do not keep a half-dead child and a stale pipe around; the
                # next attempt must start from a clean process.
                self._discard_worker_locked()
                raise TorrentEngineError(f"Embedded torrent worker failed to start: {error}")
            self._last_worker_error = ""
            self.worker_notes = str((ready.get("result") or {}).get("notes") or "")

    def _rpc_sync(self, op: str, **payload):
        """Send one request to the worker, discarding the worker on failure."""
        with self._lock:
            self._start_process()
            if self._conn is None:
                raise TorrentEngineError("Embedded torrent worker connection is unavailable")
            self._next_id += 1
            request_id = self._next_id
            self._conn.send({"id": request_id, "op": op, **payload})
            timeout = 120.0 if op in {"add", "remove"} else 30.0
            deadline = time.monotonic() + timeout
            while not self._conn.poll(0.5):
                process = self._process
                if process is None or not process.is_alive():
                    try:
                        if process is not None:
                            process.join(timeout=0.25)
                    except Exception:
                        _swallowed_note("torrent_engine.LibtorrentWorkerEngine._rpc_sync")
                        pass
                    code = process.exitcode if process is not None else None
                    code_hex = f"0x{(int(code) & 0xFFFFFFFF):08X}" if code is not None else ""
                    self._last_worker_error = (
                        f"worker exited unexpectedly during {op}"
                        + (f" (exit code {code} / {code_hex})" if code is not None else "")
                    )
                    self._discard_worker_locked()
                    raise TorrentEngineError(
                        "Embedded torrent worker exited unexpectedly"
                        + (f" (exit code {code} / 0x{(int(code) & 0xFFFFFFFF):08X})" if code is not None else "")
                        + f" during {op}"
                    )
                if time.monotonic() >= deadline:
                    self._last_worker_error = f"worker timed out during {op}"
                    self._terminate_worker_locked()
                    raise TorrentEngineError(f"Embedded torrent worker timed out during {op}")
            try:
                response = self._conn.recv()
            except (EOFError, OSError) as exc:
                process = self._process
                try:
                    if process is not None:
                        process.join(timeout=0.25)
                except Exception:
                    _swallowed_note("torrent_engine.LibtorrentWorkerEngine._rpc_sync")
                    pass
                code = process.exitcode if process is not None else None
                self._last_worker_error = (
                    f"worker exited unexpectedly during {op}: {exc}"
                    + (f" (exit code {code} / 0x{(int(code) & 0xFFFFFFFF):08X})" if code is not None else "")
                )
                self._record_worker_exit(operation=op, process=process, exit_code=code)
                self._discard_worker_locked()
                raise TorrentEngineError(
                    "Embedded torrent worker exited unexpectedly"
                    + (f" (exit code {code} / 0x{(int(code) & 0xFFFFFFFF):08X})" if code is not None else "")
                    + f" during {op}"
                ) from exc
            if not isinstance(response, dict):
                raise TorrentEngineError(f"Invalid response from embedded torrent worker: {response!r}")
            if int(response.get("id", request_id)) != request_id and response.get("type") != "fatal":
                raise TorrentEngineError("Embedded torrent worker returned an unexpected response")
            if not response.get("ok"):
                error = str(response.get("error") or "worker operation failed")
                where = str(response.get("traceback") or "")
                if where:
                    error = f"{error} [at {where}]"
                self._last_worker_error = error
                raise TorrentEngineError(error)
            return response.get("result")

    def _discard_worker_locked(self) -> None:
        conn, process = self._conn, self._process
        self._conn = None
        self._process = None
        try:
            if conn is not None:
                conn.close()
        except Exception:
            _swallowed_note("torrent_engine.LibtorrentWorkerEngine._discard_worker_locked")
            pass
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentWorkerEngine._discard_worker_locked")
                pass
            try:
                process.join(timeout=3)
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentWorkerEngine._discard_worker_locked")
                pass

    def _terminate_worker_locked(self) -> None:
        self._discard_worker_locked()

    @property
    def worker_pid(self) -> int | None:
        process = self._process
        pid = getattr(process, "pid", None) if process is not None else None
        return int(pid) if pid else None

    @property
    def worker_alive(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    @property
    def worker_error(self) -> str:
        return self._last_worker_error

    async def version(self) -> dict[str, str]:
        result = await asyncio.to_thread(self._rpc_sync, "version")
        info = dict(result or {})
        info["diagnostic_log"] = str(self._diagnostic_path)
        info["last_worker_exit_log"] = str(self._last_exit_path)
        return info

    async def add(self, source: str, *, save_path: str = "", category: str = "") -> str:
        result = await asyncio.to_thread(
            self._rpc_sync, "add", source=source, save_path=save_path, category=category
        )
        return str(result or "")

    async def get(self, torrent_hash: str) -> Optional[TorrentInfo]:
        result = await asyncio.to_thread(self._rpc_sync, "get", torrent_hash=torrent_hash)
        return TorrentInfo(**result) if isinstance(result, dict) else None

    async def list(self, *, category: str = "") -> list[TorrentInfo]:
        result = await asyncio.to_thread(self._rpc_sync, "list", category=category)
        return [TorrentInfo(**item) for item in (result or []) if isinstance(item, dict)]

    async def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        result = await asyncio.to_thread(self._rpc_sync, "files", torrent_hash=torrent_hash)
        return list(result or [])

    async def select_video_only(self, torrent_hash: str) -> bool:
        return bool(await asyncio.to_thread(self._rpc_sync, "select_video_only", torrent_hash=torrent_hash))

    async def prepare_download(self, torrent_hash: str) -> dict[str, Any]:
        return dict(await asyncio.to_thread(self._rpc_sync, "prepare_download", torrent_hash=torrent_hash) or {})

    async def pause(self, torrent_hash: str) -> None:
        await asyncio.to_thread(self._rpc_sync, "pause", torrent_hash=torrent_hash)

    async def resume(self, torrent_hash: str) -> None:
        await asyncio.to_thread(self._rpc_sync, "resume", torrent_hash=torrent_hash)

    async def recheck(self, torrent_hash: str) -> bool:
        return bool(await asyncio.to_thread(self._rpc_sync, "recheck", torrent_hash=torrent_hash))

    async def remove(self, torrent_hash: str, *, delete_files: bool = False) -> None:
        await asyncio.to_thread(
            self._rpc_sync, "remove", torrent_hash=torrent_hash, delete_files=delete_files
        )

    async def close(self) -> None:
        with self._lock:
            if self._conn is None or self._process is None:
                return
            try:
                self._next_id += 1
                request_id = self._next_id
                self._conn.send({"id": request_id, "op": "close"})
                if self._conn.poll(10):
                    try:
                        self._conn.recv()
                    except Exception:
                        _swallowed_note("torrent_engine.LibtorrentWorkerEngine.close")
                        pass
            except Exception:
                _swallowed_note("torrent_engine.LibtorrentWorkerEngine.close")
                pass
            finally:
                self._discard_worker_locked()
