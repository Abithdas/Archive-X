"""SFW / NSFW content mode: which sources and entities the app shows, decided in the database itself.

One setting (``content_mode``, default ``sfw``) and per-source marks decide what is visible:
  * a Telegram channel is shown when it is in the library (``channels.in_library``) and its ``content_rating`` equals the mode;
  * performers, studios and tags carry their own ``content_rating`` (adult ones ``nsfw``, movie cast, networks and genres ``sfw``).

The rule is applied by shadowing tables with TEMP VIEWS on read-only connections (:data:`VISIBILITY_SQL`). Reads then see only what the
mode allows, however a query is written, and writes are untouched because the write connection never has the views. The Rust front door
(crates/ax-db) runs the very same SQL, kept equal by tools/tests/test_visibility_parity.py, since it answers many lists itself.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

MODES = ("sfw", "nsfw")
DEFAULT_MODE = "sfw"
SETTING_MODE = "content_mode"
SETTING_PIN = "nsfw_pin"

# True while a request that shows library content is being served: read connections opened then are the filtered ones.
FILTER_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("ax_content_filter", default=False)

_MODE_SQL = "COALESCE((SELECT json_extract(value,'$') FROM main.settings WHERE key='content_mode'),'sfw')"
_CHANNELS_OK = f"COALESCE(in_library,1)=1 AND COALESCE(content_rating,'nsfw')={_MODE_SQL}"
_HOLDS = {"video": "('videos','both')", "photo": "('photos','both')"}


def _visible_channels(kind: str | None = None) -> str:
    """The channels shown now: in the library, marked like the mode, and (for a kind of content) holding that kind."""
    extra = f" AND COALESCE(holds,'both') IN {_HOLDS[kind]}" if kind else ""
    return f"(SELECT telegram_id FROM main.channels WHERE {_CHANNELS_OK}{extra})"


def _channel_ok(col: str, kind: str | None = None) -> str:
    """Is the channel in `col` shown? A marked channel by its marks; a channel with no row at all (older data) counts as an NSFW library channel."""
    return f"({col} IN {_visible_channels(kind)} OR ({_MODE_SQL}='nsfw' AND NOT EXISTS (SELECT 1 FROM main.channels oc WHERE oc.telegram_id={col})))"




def _of_visible_media(table: str, col: str) -> str:
    """The rows of a link table whose video is shown. A join, not `col IN (SELECT ...)`: the nested form made SQLite run the inner query again
    for every row of the outer one (tens of seconds on a big library)."""
    return f"SELECT t.* FROM main.{table} t JOIN main.media m ON m.id=t.{col} WHERE {_channel_ok('m.source_channel', 'video')}"


# One statement per entry; every one is `CREATE TEMP VIEW <table> AS ...` so the view shadows the table of the same name.
VISIBILITY_VIEWS: tuple[tuple[str, str], ...] = (
    # The library is made of these three views of `media`. A view stored in the database reads the real `media`, never the shadow below,
    # so each is shadowed itself.
    ("archive_x_published_media", f"SELECT * FROM main.archive_x_published_media WHERE {_channel_ok('source_channel', 'video')}"),
    ("archive_x_catalogued_media", f"SELECT * FROM main.archive_x_catalogued_media WHERE {_channel_ok('source_channel', 'video')}"),
    ("archive_x_library_media", f"SELECT * FROM main.archive_x_library_media WHERE {_channel_ok('source_channel', 'video')}"),
    ("channels", f"SELECT * FROM main.channels WHERE {_CHANNELS_OK}"),
    ("media", f"SELECT * FROM main.media WHERE {_channel_ok('source_channel', 'video')}"),
    ("photo_library_items", f"SELECT * FROM main.photo_library_items WHERE {_channel_ok('channel_id', 'photo')}"),
    ("photo_face_detections", f"SELECT t.* FROM main.photo_face_detections t JOIN main.photo_library_items p ON p.id=t.photo_id WHERE {_channel_ok('p.channel_id', 'photo')}"),
    ("video_performers", _of_visible_media("video_performers", "video_id")),
    ("local_video_performers", _of_visible_media("local_video_performers", "video_id")),
    ("video_studios", _of_visible_media("video_studios", "video_id")),
    ("video_tags", _of_visible_media("video_tags", "video_id")),
    ("watch_history", _of_visible_media("watch_history", "media_id")),
    ("scene_markers", _of_visible_media("scene_markers", "media_id")),
    ("performers", f"SELECT * FROM main.performers WHERE COALESCE(content_rating,'nsfw')={_MODE_SQL}"),
    ("studios", f"SELECT * FROM main.studios WHERE COALESCE(content_rating,'nsfw')={_MODE_SQL}"),
    ("tags", f"SELECT * FROM main.tags WHERE COALESCE(content_rating,'nsfw')={_MODE_SQL}"),
)

VISIBILITY_SQL: str = ";\n".join(f"CREATE TEMP VIEW {name} AS {body}" for name, body in VISIBILITY_VIEWS) + ";"

# Columns the views need; added to old databases by Database._migrate_columns.
VISIBILITY_COLUMNS = {
    "channels": {"in_library": "INTEGER NOT NULL DEFAULT 1", "content_rating": "TEXT NOT NULL DEFAULT 'nsfw'", "holds": "TEXT NOT NULL DEFAULT 'both'"},
    "performers": {"content_rating": "TEXT NOT NULL DEFAULT 'nsfw'", "tmdb_id": "TEXT"},
    "studios": {"content_rating": "TEXT NOT NULL DEFAULT 'nsfw'", "tmdb_id": "TEXT"},
    "tags": {"content_rating": "TEXT NOT NULL DEFAULT 'nsfw'"},
}


def install_views(conn) -> None:
    """Shadow the content tables with their visible parts on ``conn`` (a connection that is, or is about to be, read-only)."""
    for name, body in VISIBILITY_VIEWS:
        conn.execute(f"CREATE TEMP VIEW {name} AS {body}")


# ------------------------------------------------------------------ the mode
def normalise_mode(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in MODES else DEFAULT_MODE


def get_mode(db) -> str:
    return normalise_mode(db.setting(SETTING_MODE, DEFAULT_MODE))


def hidden_channels(db) -> set[str]:
    """In SFW: the channels that are in the library as NSFW. Management lists leave them out, because a name alone says what a channel holds."""
    if get_mode(db) != "sfw":
        return set()
    with db.reading() as conn:   # plain connection: this is the one place that has to see them
        return {str(r[0]) for r in conn.execute("SELECT telegram_id FROM channels WHERE COALESCE(in_library,1)=1 AND COALESCE(content_rating,'nsfw')='nsfw'")}


def set_mode(db, mode: str) -> str:
    mode = normalise_mode(mode)
    db.set_setting(SETTING_MODE, mode)
    return mode


# ------------------------------------------------------------------ the PIN (needed to switch into NSFW)
_PIN_RE = re.compile(r"^\d{4,12}$")
_ITER = 200_000
_fail_lock = threading.Lock()
_failures: list[float] = []     # monotonic times of recent wrong PINs
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60


def _hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _ITER).hex()


def pin_is_set(db) -> bool:
    return bool(db.setting(SETTING_PIN, ""))


def valid_pin_format(pin: str) -> bool:
    return bool(_PIN_RE.match(str(pin or "")))


def set_pin(db, new_pin: str) -> None:
    if not valid_pin_format(new_pin):
        raise ValueError("The PIN must be 4 to 12 digits")
    salt = secrets.token_bytes(16)
    db.set_setting(SETTING_PIN, json.dumps({"salt": salt.hex(), "hash": _hash(new_pin, salt), "iter": _ITER}))


def clear_pin(db) -> None:
    db.set_setting(SETTING_PIN, "")


def locked_out(now: float | None = None) -> float:
    """Seconds left of a lockout after too many wrong PINs, else 0."""
    now = time.monotonic() if now is None else now
    with _fail_lock:
        recent = [t for t in _failures if now - t < LOCKOUT_SECONDS]
        _failures[:] = recent
        return max(0.0, LOCKOUT_SECONDS - (now - recent[0])) if len(recent) >= MAX_FAILURES else 0.0


def verify_pin(db, pin: str) -> bool:
    raw = db.setting(SETTING_PIN, "")
    if not raw:
        return False
    try:
        data = json.loads(raw)
        ok = hmac.compare_digest(_hash(str(pin or ""), bytes.fromhex(data["salt"])), data["hash"])
    except (ValueError, KeyError, TypeError):
        ok = False
    with _fail_lock:
        if ok:
            _failures.clear()
        else:
            _failures.append(time.monotonic())
    return ok
