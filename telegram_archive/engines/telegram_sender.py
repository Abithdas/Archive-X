"""Send a library video to another Telegram channel (v18.8).

* The video itself is re-sent by reference: nothing is downloaded or
  uploaded again, so even multi-GB files are sent in a second.
* The caption is built from the catalogue (title, studio, performers, date)
  with a template you can edit in Operations -> Send to Telegram.
* The selected thumbnail becomes the video's native Telegram cover and is
  also posted as a separate image message immediately after the video.
* The sent copy is added to the local catalogue straight away (metadata and
  thumbnail included), so it shows up complete in the Library.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Settings and caption templates
#   - Persistence and field formatting
#   - Retries and upload stages
#   - Parallel upload transport
#   - Cover photos
#   - Local upload message resolution
#   - Public send operations
#   - Cover repair and send entry
#   - Copy registration
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import hashlib
import html
import inspect
import io
import json
import math
import mimetypes
import re
import secrets
import uuid
import tempfile
import time
from pathlib import Path

from .. import config
from .media_utils import flood_wait_seconds
from ..core.upload_limits import ensure_upload_allowed
from ..core.local_first import caption_requires_remote_metadata, LOCAL_CATALOGUE_COMPLETE, PROVIDER_REQUIRED
from ..core.telegram_delivery_profile import profile as delivery_profile, save_profile as save_delivery_profile
from ..telegram.client_policy import UPLOAD_PART_SIZE_KB

DEFAULT_TEMPLATE = (
    "🎬 <b>{title}</b>\n"
    "🏢 {studio}\n"
    "👤 {performers}\n"
    "📅 {date}"
)

# Local Upload keeps its separate, previously requested tag-inclusive template.
LOCAL_UPLOAD_DEFAULT_TEMPLATE = (
    "🎬 <b>{title}</b>\n"
    "🏢 {studio}\n"
    "👤 {performers}\n"
    "📅 {date}"
)
COVER_MODES = ("both",)
PLACEHOLDERS = ("title", "filename", "studio", "performers", "date", "duration",
                "resolution")
FLOODWAIT_INLINE_MAX_SECONDS = 90




# ==============================================================================
# SECTION: Settings and caption templates
# Caption rendering and send settings.
# ==============================================================================

async def build_local_media_caption(db, video_path, meta: dict, *, oshash_value: str | None = None,
                                   media_id: int | None = None, display_filename: str | None = None,
                                   template_setting: str = "send_caption_template",
                                   default_template: str = DEFAULT_TEMPLATE, limit: int = 4096) -> str:
    """Canonical caption builder for local-media delivery workflows.

    It uses local fields first and only enters the provider identification
    pipeline when the configured template requires metadata that is not locally
    available. Upload Local and Content Automation both call this service.
    """
    from pathlib import Path as _Path
    from ..core.local_first import caption_requires_remote_metadata, PROVIDER_REQUIRED
    from ..metadata.fingerprint import oshash_file
    from ..metadata.pipeline import identify_local_media
    video_path = _Path(video_path)
    filename = str(display_filename or "").strip() or video_path.name
    filename_caption = html.escape(filename, quote=False)
    template = str(db.setting(template_setting) or default_template)
    fields = {"filename": filename, "duration": float(meta.get("duration") or 0),
              "height": int(meta.get("height") or 0), "resolution": f"{int(meta.get('width') or 0)}x{int(meta.get('height') or 0)}"}
    if not caption_requires_remote_metadata(template, fields):
        return render_caption(template, {**fields, "title": video_path.stem, "studio":"", "performers":[], "date":"", "tags":[]}, limit) or filename_caption

    # Content Automation has already run the canonical Metadata & Assets pipeline
    # before it asks for a caption. Read that exact persisted media record rather
    # than doing a second independent provider identification against the filename.
    # This is also confidence-safe: _video_fields respects manual mapping locks and
    # reads only applied catalogue relationships, never pending suggestions.
    if media_id is not None:
        try:
            with db.lock:
                identity = db.conn.execute(
                    "SELECT source_channel,source_message_id FROM media WHERE id=? LIMIT 1",
                    (int(media_id),),
                ).fetchone()
            if identity:
                values = _video_fields(db, str(identity["source_channel"]), int(identity["source_message_id"]))
                values["filename"] = filename
                # Local inventory stores the raw source filename (e.g. "x.ts") as the
                # placeholder title; never let that extension reach the caption.
                raw_title = str(values.get("title") or "").strip()
                if raw_title and (raw_title.lower() == video_path.name.lower()
                                  or _Path(raw_title).suffix.lower() in {".ts", ".mts", ".m2ts", ".m2t", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mp4", ".m4v"}):
                    values["title"] = _Path(raw_title).stem
                values["duration"] = float(meta.get("duration") or values.get("duration") or 0)
                values["height"] = int(meta.get("height") or values.get("height") or 0)
                return render_caption(template, values, limit) or filename_caption
            try:
                db.log(f"Local media caption: prepared media_id {media_id} was not found; using filename only", "WARN")
            except Exception:
                _swallowed_note("telegram_sender.build_local_media_caption")
                pass
            return filename_caption
        except Exception as exc:
            try:
                db.log(f"Local media caption: could not read prepared media_id {media_id}: {type(exc).__name__}: {exc}; using filename only", "WARN")
            except Exception:
                _swallowed_note("telegram_sender.build_local_media_caption")
                pass
            return filename_caption

    oshash = str(oshash_value or "").strip() or None
    if not oshash:
        try:
            oshash = await asyncio.to_thread(oshash_file, str(video_path))
        except Exception as exc:
            try: db.log(f"Local media caption: OSHASH unavailable for {filename}: {exc}", "WARN")
            except Exception:
                _swallowed_note("telegram_sender.build_local_media_caption")
                pass
    try:
        result = await identify_local_media(db, oshash=oshash, filename=filename)
    except Exception as exc:
        try: db.log(f"Local media caption provider lookup failed for {filename}: {exc}", "WARN")
        except Exception:
            _swallowed_note("telegram_sender.build_local_media_caption")
            pass
        return filename_caption

    # Candidate search is deliberately broader than automatic identification:
    # medium/low-ranked results are useful for review, but must never be
    # published as factual title/studio/performer/date fields in a Telegram
    # caption. Previously this path consumed matches[0] regardless of the
    # scorer's confidence, so a weak overlap (for example, "Deepthroat" or
    # "Sky" in a filename) could send an unrelated scene's metadata.
    confidence = str(result.get("confidence") or "").strip().upper()
    best = result.get("best")
    if confidence != "HIGH" or not isinstance(best, dict) or not best:
        try:
            source = str(result.get("source") or "unknown")
            method = str(result.get("method") or "not provided")
            db.log(
                f"Local media caption: withheld unconfirmed metadata for {filename}; "
                f"confidence={confidence or 'UNKNOWN'}, source={source}, method={method}; "
                "using the original filename instead",
                "INFO",
            )
        except Exception:
            _swallowed_note("telegram_sender.build_local_media_caption")
            pass
        return filename_caption

    performers=[]
    for value in best.get("performers") or []:
        if isinstance(value,str) and value.strip(): performers.append(value.strip())
        elif isinstance(value,dict) and str(value.get("name") or "").strip(): performers.append(str(value["name"]).strip())
    values={"title":str(best.get("title") or "").strip() or video_path.stem,
            "filename":filename,"studio":str(best.get("site") or best.get("studio") or "").strip(),
            "performers":list(dict.fromkeys(performers)),"tags":[str(x).strip() for x in (best.get("tags") or []) if str(x).strip()],
            "date":str(best.get("date") or "").strip()[:10],"duration":fields["duration"],"height":fields["height"],"resolution":fields["resolution"]}
    if not any((values["title"] != video_path.stem, values["studio"], values["performers"], values["date"])):
        return filename_caption
    return render_caption(template, values, limit) or filename_caption

def settings(db) -> dict:
    # Direct-send target/fixed state is workflow-specific. Caption and delivery
    # policy come from the single shared Telegram Delivery Profile.
    profile = delivery_profile(db, default_template=DEFAULT_TEMPLATE, sanitize_template=_sanitize_send_template)
    return {
        "target": db.setting("send_target_channel") or "",
        "fixed": (db.setting("send_target_fixed") or "0") == "1",
        **profile,
        "default_template": DEFAULT_TEMPLATE,
    }


_HASHTAG_PLACEHOLDERS = ("{hashtags}", "{performer_tags}", "{tag_tags}", "{studio_tag}")

def _remove_hashtag_placeholders(template: str) -> str:
    """Never emit generated hashtags in any Telegram video caption.

    Existing saved/custom templates may still contain the legacy hashtag
    placeholders. Remove those placeholders (and a now-empty line) so older
    settings cannot reintroduce hashtags after this policy change.
    """
    text = str(template or "")
    for token in _HASHTAG_PLACEHOLDERS:
        text = text.replace(token, "")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


def _sanitize_send_template(template: str) -> str:
    """Return the Send-to-Telegram / Media Manager caption template.

    Tags are intentionally excluded from this delivery path. Existing saved
    templates are sanitized so legacy `{tags}`/tag lines cannot reappear.
    """
    text = _remove_hashtag_placeholders(template)
    lines = []
    for line in text.splitlines():
        # Remove a dedicated legacy tags line or any line containing the tags placeholder.
        if "{tags}" in line or "🏷️" in line:
            continue
        if line.strip():
            lines.append(line.rstrip())
    cleaned = "\n".join(lines).strip()
    return cleaned or DEFAULT_TEMPLATE


def save_settings(db, data: dict) -> dict:
    if "target" in data:
        db.set_setting("send_target_channel", str(data.get("target") or ""))
    if "fixed" in data:
        db.set_setting("send_target_fixed", "1" if data.get("fixed") else "0")
    save_delivery_profile(db, data, default_template=DEFAULT_TEMPLATE, sanitize_template=_sanitize_send_template)
    # Ignore legacy cover-mode choices. The canonical transfer policy always
    # sends one representative playback snapshot as the cover/reference image.
    db.set_setting("send_cover_mode", "both")
    return settings(db)




# ==============================================================================
# SECTION: Persistence and field formatting
# Send tables, duplicate checks and field formatting.
# ==============================================================================

def _ensure_tables(db):
    with db.lock:
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS sent_log (source_channel TEXT NOT NULL, message_id INTEGER NOT NULL,"
            " target_channel TEXT NOT NULL, sent_message_id INTEGER, thumb_message_id INTEGER, sent_at TEXT NOT NULL)")
        db.conn.execute("CREATE INDEX IF NOT EXISTS idx_sent_log_src ON sent_log(source_channel, message_id)")
        db.conn.commit()


def already_sent(db, channel_id, message_id, target) -> bool:
    _ensure_tables(db)
    with db.lock:
        row = db.conn.execute(
            "SELECT 1 FROM sent_log WHERE source_channel=? AND message_id=? AND target_channel=? LIMIT 1",
            (str(channel_id), int(message_id), str(target))).fetchone()
    return bool(row)


def _video_fields(db, channel_id, message_id, message=None) -> dict:
    """Build caption fields while respecting explicit manual mapping locks.

    A locked performer/site mapping is authoritative for delivery: provider
    relationships/suggestions may still exist in the database, but they must
    never leak into the caption used by Send-to-Telegram or Media Manager.
    """
    with db.lock:
        row = db.conn.execute(
            "SELECT m.*, GROUP_CONCAT(DISTINCT p.name) performers, GROUP_CONCAT(DISTINCT lp.name) local_performers,"
            " GROUP_CONCAT(DISTINCT s.name) studios, GROUP_CONCAT(DISTINCT t.name) tags FROM media m"
            " LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id"
            " LEFT JOIN local_video_performers lvp ON lvp.video_id=m.id LEFT JOIN performers lp ON lp.id=lvp.performer_id"
            " LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id"
            " LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id"
            " WHERE m.source_channel=? AND m.source_message_id=? GROUP BY m.id",
            (str(channel_id), int(message_id))).fetchone()
        d = dict(row) if row else {}
        media_id = int(d.get("id") or 0)
        lock_rows = db.conn.execute(
            "SELECT field FROM metadata_mapping_locks WHERE video_id=? AND locked=1",
            (media_id,),
        ).fetchall() if media_id else []
        locks = {str(r["field"]).strip().lower() for r in lock_rows}
    split = lambda v: [x for x in (v or "").split(",") if x.strip()]

    provider_performers = split(d.get("performers"))
    local_performers = split(d.get("local_performers"))
    if "performer" in locks:
        # Manual mapping is authoritative. Never combine it with a provider
        # suggestion because that creates the exact caption mismatch the
        # operator was trying to prevent.
        performers = list(dict.fromkeys(local_performers))
    else:
        performers = list(dict.fromkeys(provider_performers + local_performers))

    provider_studios = split(d.get("studios"))
    if "site" in locks:
        studio = (d.get("site_name") or "").strip()
    else:
        # Provider studio relationship is the canonical studio field for
        # Telegram captions. Fall back to site_name only when no studio entity
        # was linked by metadata matching.
        studio = (provider_studios[:1] or [""])[0] or (d.get("site_name") or "").strip()

    f = getattr(message, "file", None)
    filename = d.get("filename") or getattr(f, "name", None) or f"video_{message_id}.mp4"
    return {
        "row": d,
        "mapping_locks": sorted(locks),
        "title": (d.get("title") or "").strip() or Path(filename).stem,
        "filename": filename,
        "studio": studio,
        "performers": performers,
        "tags": split(d.get("tags")),
        "date": (d.get("release_date") or (d.get("upload_date") or "")[:10] or
                 (message.date.strftime("%Y-%m-%d") if getattr(message, "date", None) else "")),
        "duration": float(d.get("duration") or getattr(f, "duration", 0) or 0),
        "height": int(d.get("height") or 0),
    }

def _fmt_duration(sec: float) -> str:
    sec = int(sec or 0)
    if not sec:
        return ""
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _strip_tags(text: str) -> str:
    """Remove ``<...>`` tags. ``[^<>]`` keeps the match linear on odd input such as many ``<`` in a row."""
    return re.sub(r"<[^<>]*>", "", text)


def render_caption(template: str, v: dict, limit: int = 1024) -> str:
    # Send-to-Telegram / Media Manager captions intentionally exclude tags.
    template = _remove_hashtag_placeholders(template)
    h = v.get("height") or 0

    def build(max_tags: int) -> str:
        values = {
            "title": v["title"], "filename": v["filename"], "studio": v["studio"],
            "performers": ", ".join(v["performers"]), "tags": ", ".join(v["tags"]),
            "date": v["date"], "duration": _fmt_duration(v["duration"]),
            "resolution": ("4K" if h >= 2160 else f"{h}p") if h else "",
            "hashtags": "", "performer_tags": "", "tag_tags": "", "studio_tag": "",
        }
        values = {k: html.escape(str(val or ""), quote=False) for k, val in values.items()}
        out = []
        for line in (template or DEFAULT_TEMPLATE).splitlines():
            names = [n for n in re.findall(r"\{(\w+)\}", line) if n in values]
            if names and all(not values[n] for n in names):
                continue     # drop lines whose placeholders are all empty
            out.append(re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), line))
        return "\n".join(out).strip()

    for max_tags in (60, 25, 10, 0):
        text = build(max_tags)
        if len(_strip_tags(html.unescape(text))) <= limit:
            return text
    plain = html.unescape(_strip_tags(build(0)))
    return html.escape(plain[: limit - 1] + "…")




# ==============================================================================
# SECTION: Retries and upload stages
# Retry wrapper and awaited upload stages.
# ==============================================================================

async def _retry(coro_factory, attempts=3, *, base_delay: float = 1.0, max_delay: float = 30.0, stop_if=None):
    """Retry short Telegram faults without sleeping through long FloodWaits."""
    attempts = max(1, int(attempts))
    for i in range(attempts):
        try:
            return await coro_factory()
        except Exception as e:
            if callable(stop_if) and stop_if(e):
                raise
            flood_seconds = flood_wait_seconds(e)
            if flood_seconds > FLOODWAIT_INLINE_MAX_SECONDS:
                raise
            if i >= attempts - 1:
                raise
            wait_seconds = flood_seconds + 1 if flood_seconds else min(float(max_delay), float(base_delay) * (2 ** i))
            if flood_seconds:
                from .transfer_timing import TIMER
                TIMER.flood(flood_seconds)
            await asyncio.sleep(wait_seconds)


async def _await_upload_stage(awaitable_factory, status_callback, stage: str, *,
                              interval_seconds: float = 15.0):
    """Keep a Telegram upload visibly active while a finalization RPC is pending.

    Upload progress callbacks stop once the video bytes are sent, but Telegram
    still has to register the cover, create the message, and return it for
    verification. Report that wait instead of letting the job watchdog mistake
    a slow control-plane response for a stalled byte upload.
    """
    if not callable(status_callback):
        return await awaitable_factory()

    async def _report(message: str) -> None:
        try:
            value = status_callback(message, 0)
            if inspect.isawaitable(value):
                await value
        except Exception:
            # Liveness reporting must not turn a successful Telegram request
            # into a failed delivery.
            _swallowed_note("telegram_sender._await_upload_stage._report")
            pass

    await _report(stage)
    task = asyncio.create_task(awaitable_factory())
    started = time.monotonic()
    try:
        while not task.done():
            done, _ = await asyncio.wait(
                {task}, timeout=max(0.001, float(interval_seconds))
            )
            if done:
                break
            elapsed = max(1, int(time.monotonic() - started))
            await _report(f"Still waiting for Telegram: {stage} ({elapsed}s)")
        return await task
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


UPLOAD_PART_BYTES = 512 * 1024
UPLOAD_SMALL_FILE_BYTES = 10 * 1024 * 1024
UPLOAD_PART_STALL_SECONDS = 75.0
UPLOAD_PART_MAX_ATTEMPTS = 10
UPLOAD_MAX_WORKERS = 32  # hard ceiling; the governor applies the live X × Y limit


def _signed_random_file_id() -> int:
    """Return a random signed 64-bit value suitable for MTProto `long`."""
    value = secrets.randbits(64)
    return value if value < (1 << 63) else value - (1 << 64)


# A failed upload keeps its file id and acknowledged parts for a short while, so the caller's retry
# continues from the last good part instead of re-sending a multi-GB file. Telegram holds unfinished
# parts far longer than this; if it has dropped them anyway the send step reports a missing part and
# the existing one-time re-upload (see _is_missing_or_expired_upload) starts clean.
UPLOAD_RESUME_TTL_SECONDS = 900.0
_UPLOAD_LEDGER: dict[tuple, dict] = {}


def _upload_ledger_entry(path: Path, file_size: int, is_big: bool) -> tuple[tuple, dict]:
    """The resumable state for this exact file (path, size, mtime and part size must all match)."""
    now = time.monotonic()
    for key in [k for k, v in _UPLOAD_LEDGER.items() if now - v["touched"] > UPLOAD_RESUME_TTL_SECONDS]:
        _UPLOAD_LEDGER.pop(key, None)
    st = path.stat()
    key = (str(path.resolve()), int(file_size), int(st.st_mtime_ns), UPLOAD_PART_BYTES, bool(is_big))
    entry = _UPLOAD_LEDGER.get(key)
    if entry is None:
        entry = _UPLOAD_LEDGER[key] = {"file_id": _signed_random_file_id(), "confirmed": set(), "touched": now}
    entry["touched"] = now
    return key, entry


def _is_missing_or_expired_upload(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".upper()
    compact = re.sub(r"[^A-Z0-9]", "", text)
    return bool(
        re.search(r"FILE_PART(?:S)?(?:_\d+)?_(?:MISSING|EXPIRED)", text)
        or re.search(r"FILEPART(?:S)?(?:\d+)?(?:MISSING|EXPIRED)", compact)
        or "FILE PART MISSING" in text
        or "FILE PART EXPIRED" in text
        or "FILEPARTSINVALID" in compact
        or "UPLOAD FILE EXPIRED" in text
    )




# ==============================================================================
# SECTION: Parallel upload transport
# Upload gate, connection pool and part upload.
# ==============================================================================

class _AdaptiveUploadGate:
    """Per-file safety gate mirroring the governor's current upload X × Y cap."""
    def __init__(self, client):
        self.client = client
        self.active = 0
        self.condition = asyncio.Condition()

    def limit(self) -> int:
        governor = getattr(self.client, "_telegram_archive_governor", None)
        snapshot = getattr(governor, "snapshot", None)
        if callable(snapshot):
            try:
                return max(1, min(UPLOAD_MAX_WORKERS, int(snapshot().get("upload_max_parts") or 4)))
            except Exception:
                _swallowed_note("telegram_sender._AdaptiveUploadGate.limit")
                pass
        return 4

    async def __aenter__(self):
        async with self.condition:
            while self.active >= self.limit():
                try:
                    await asyncio.wait_for(self.condition.wait(), timeout=0.15)
                except asyncio.TimeoutError:
                    pass
            self.active += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        async with self.condition:
            self.active = max(0, self.active - 1)
            self.condition.notify_all()


class _UploadConnectionPool:
    """One upload queue per TCP sender, sharing the existing account auth key.

    The client's primary sender is always queue zero. Additional MTProtoSender
    instances use the same authorized auth key with their own transport/session
    IDs; they do not create TelegramClient objects, login sessions or accounts.
    If a connection cannot be opened, the governor is reduced to the number of
    queues that actually exist instead of advertising unreal concurrency.
    """
    def __init__(self, client, governor):
        self.client = client
        self.governor = governor
        self.primary = getattr(client, "_sender", None)
        # A small duck-typed client (e.g. offline acceptance simulation) can use
        # its normal __call__ path without requiring private Telethon internals.
        self.senders = [self.primary] if self.primary is not None else [None]
        self.lock = asyncio.Lock()
        self.round_robin = 0

    def _desired_queues(self) -> int:
        snapshot = getattr(self.governor, "snapshot", None)
        if callable(snapshot):
            try:
                return max(1, min(4, int(snapshot().get("upload_y") or 1)))
            except Exception:
                _swallowed_note("telegram_sender._UploadConnectionPool._desired_queues")
                pass
        return 1

    async def _create_same_account_sender(self):
        """Open an extra MTProto sender on the same account for parallel upload."""
        from telethon.network import MTProtoSender

        session = getattr(self.client, "session", None)
        connection_factory = getattr(self.client, "_connection", None)
        loggers = getattr(self.client, "_log", None)
        if session is None or not callable(connection_factory) or loggers is None:
            raise RuntimeError("Telethon client does not expose its existing connection factory")
        auth_key = getattr(session, "auth_key", None) or getattr(self.primary, "auth_key", None)
        if auth_key is None:
            raise RuntimeError("The active Telegram client has no reusable authorization key")
        address = getattr(session, "server_address", None)
        port = getattr(session, "port", None)
        dc_id = getattr(session, "dc_id", None)
        if not address or not port or not dc_id:
            raise RuntimeError("The active Telegram session has no home-DC connection details")

        sender = MTProtoSender(
            auth_key,
            loggers=loggers,
            retries=int(getattr(self.client, "_connection_retries", 5) or 5),
            delay=float(getattr(self.client, "_retry_delay", 1) or 1),
            auto_reconnect=bool(getattr(self.client, "_auto_reconnect", True)),
            connect_timeout=getattr(self.client, "_timeout", 10),
        )
        connection = connection_factory(
            address, int(port), int(dc_id),
            loggers=loggers,
            proxy=getattr(self.client, "_proxy", None),
            local_addr=getattr(self.client, "_local_addr", None),
        )
        try:
            await sender.connect(connection)
            if not sender.is_connected():
                raise ConnectionError("Telethon upload sender did not connect")
            sender.dc_id = int(dc_id)
            return sender
        except BaseException:
            try:
                await sender.disconnect()
            except Exception:
                _swallowed_note("telegram_sender._UploadConnectionPool._create_same_account_sender")
                pass
            raise

    async def _drop_dead_senders(self):
        """Remove extra senders whose connection died and revive queue zero.

        A dead MTProtoSender raises "Cannot send requests while disconnected"
        forever, so keeping it in the rotation turned one network blip into a
        failed upload after the per-part retries ran out.
        """
        alive = [self.senders[0]]
        for sender in self.senders[1:]:
            try:
                ok = bool(sender.is_connected())
            except Exception:
                ok = False
            if ok:
                alive.append(sender)
                continue
            try:
                await sender.disconnect()
            except Exception:
                _swallowed_note("telegram_sender._UploadConnectionPool._drop_dead_senders")
                pass
        self.senders = alive
        primary = self.senders[0]
        if primary is not None:
            try:
                primary_ok = bool(primary.is_connected())
            except Exception:
                primary_ok = True
            reconnect = getattr(self.client, "archive_x_reconnect", None)
            if not primary_ok and callable(reconnect):
                await reconnect(reason="upload connection dropped")

    async def sender_for_part(self):
        desired = self._desired_queues()
        async with self.lock:
            await self._drop_dead_senders()
            while len(self.senders) < desired:
                try:
                    self.senders.append(await self._create_same_account_sender())
                except Exception as exc:
                    available = max(1, len(self.senders))
                    note = getattr(self.governor, "note_upload_connection_failure", None)
                    if callable(note):
                        try:
                            note(available, f"{type(exc).__name__}: {exc}")
                        except Exception:
                            _swallowed_note("telegram_sender._UploadConnectionPool.sender_for_part")
                            pass
                    break
            usable = max(1, min(desired, len(self.senders)))
            sender = self.senders[self.round_robin % usable]
            self.round_robin += 1
            return sender

    async def send_part(self, request):
        sender = await self.sender_for_part()
        if sender is None:
            # Compatibility fallback for injected/mock clients that do not
            # expose Telethon's MTProtoSender. Production clients use direct
            # sender futures so upload RPCs never visit the general API lane.
            return await asyncio.wait_for(self.client(request), timeout=UPLOAD_PART_STALL_SECONDS)
        governor = self.governor
        if governor is None:
            future = sender.send(request)
            return await asyncio.wait_for(asyncio.shield(future), timeout=UPLOAD_PART_STALL_SECONDS)
        async with governor.telegram_upload_slot():
            future = sender.send(request)
            try:
                result = await asyncio.wait_for(asyncio.shield(future), timeout=UPLOAD_PART_STALL_SECONDS)
            except asyncio.TimeoutError:
                # The request may still be acknowledged asynchronously. Consume
                # the future's eventual error/result while retrying this exact
                # part and file id; a duplicate identical part is idempotent.
                def _consume(done):
                    try:
                        if not done.cancelled():
                            done.exception()
                    except Exception:
                        _swallowed_note("telegram_sender._UploadConnectionPool.send_part._consume")
                        pass
                future.add_done_callback(_consume)
                raise
        try:
            governor.note_upload_part_success()
        except Exception:
            _swallowed_note("telegram_sender._UploadConnectionPool.send_part")
            pass
        return result

    async def close(self):
        # Queue zero belongs to TelegramClient; never disconnect it here.
        extras = self.senders[1:]
        self.senders = self.senders[:1]
        for sender in extras:
            try:
                await sender.disconnect()
            except Exception:
                _swallowed_note("telegram_sender._UploadConnectionPool.close")
                pass


def _read_upload_part(stream, part_index: int, part_size: int, file_size: int) -> bytes:
    stream.seek(int(part_index) * int(part_size))
    expected = min(int(part_size), max(0, int(file_size) - int(part_index) * int(part_size)))
    data = stream.read(expected)
    if len(data) != expected:
        raise OSError(f"Could not read upload part {part_index}: expected {expected} bytes, read {len(data)}")
    return data


async def _archive_x_upload_file(client, file_path, *, file_name: str | None = None,
                                 progress_callback=None, status_callback=None):
    """Parallel, resumable 512-KiB Telegram upload using one stable file id.

    Every acknowledged part is tracked in memory; timeouts and transient errors
    retry only that part. The governored client caps active parts dynamically,
    isolates FloodWaits to the upload lane, and reports live X/Y diagnostics.
    """
    from telethon import functions, types

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Telegram upload source file does not exist: {path}")
    file_size = int(path.stat().st_size)
    if file_size <= 0:
        raise RuntimeError(f"Telegram upload source is empty: {path.name}")
    part_count = int(math.ceil(file_size / UPLOAD_PART_BYTES))
    if part_count <= 0:
        raise RuntimeError(f"Telegram upload has no parts: {path.name}")
    is_big = file_size > UPLOAD_SMALL_FILE_BYTES
    file_name = str(file_name or path.name)
    ledger_key, ledger = _upload_ledger_entry(path, file_size, is_big)
    file_id = int(ledger["file_id"])
    confirmed: set[int] = ledger["confirmed"]   # shared with the ledger, so parts survive a failed attempt
    confirmed_bytes = sum(min(UPLOAD_PART_BYTES, file_size - i * UPLOAD_PART_BYTES) for i in confirmed)
    resumed_parts = len(confirmed)
    failure: list[BaseException] = []
    abort = asyncio.Event()
    gate = _AdaptiveUploadGate(client)
    connections = _UploadConnectionPool(client, governor=getattr(client, "_telegram_archive_governor", None))
    queue: asyncio.Queue[int | None] = asyncio.Queue()
    for part_index in range(part_count):
        if part_index not in confirmed:
            queue.put_nowait(part_index)
    worker_count = min(UPLOAD_MAX_WORKERS, max(1, part_count - resumed_parts))
    for _ in range(worker_count):
        queue.put_nowait(None)
    governor = getattr(client, "_telegram_archive_governor", None)
    old_wait_callback = getattr(client, "_archive_x_upload_wait_callback", None)

    async def emit_status(message: str, seconds: int = 0):
        if callable(status_callback):
            try:
                value = status_callback(str(message), int(seconds or 0))
                if inspect.isawaitable(value):
                    await value
            except Exception:
                _swallowed_note("telegram_sender._archive_x_upload_file.emit_status")
                pass

    def policy_wait_callback(seconds: int, request_name: str):
        return emit_status(
            f"Telegram asked to slow down — resuming in {int(seconds)}s",
            int(seconds),
        )

    if resumed_parts:
        await emit_status(f"Resuming upload from part {resumed_parts + 1}/{part_count}", 0)
    # GovernedTelegramClient uses this callback only for upload-part FloodWaits.
    try:
        setattr(client, "_archive_x_upload_wait_callback", policy_wait_callback)
    except Exception:
        _swallowed_note("telegram_sender._archive_x_upload_file")
        pass

    async def upload_worker() -> None:
        """Upload queued file parts, handling FloodWait and progress callbacks."""
        nonlocal confirmed_bytes
        try:
            with path.open("rb") as stream:
                while True:
                    part_index = await queue.get()
                    try:
                        if part_index is None:
                            return
                        if abort.is_set() or part_index in confirmed:
                            continue
                        retries = 0
                        while part_index not in confirmed:
                            if abort.is_set():
                                break
                            try:
                                part_bytes = await asyncio.to_thread(
                                    _read_upload_part, stream, part_index, UPLOAD_PART_BYTES, file_size
                                )
                                if is_big:
                                    request = functions.upload.SaveBigFilePartRequest(
                                        file_id=file_id, file_part=part_index,
                                        file_total_parts=part_count, bytes=part_bytes,
                                    )
                                else:
                                    request = functions.upload.SaveFilePartRequest(
                                        file_id=file_id, file_part=part_index, bytes=part_bytes,
                                    )
                                async with gate:
                                    result = await connections.send_part(request)
                                if not result:
                                    raise RuntimeError(f"Telegram rejected upload part {part_index + 1}/{part_count}")
                                if part_index not in confirmed:
                                    confirmed.add(part_index)
                                    confirmed_bytes += len(part_bytes)
                                    if callable(progress_callback):
                                        value = progress_callback(confirmed_bytes, file_size)
                                        if inspect.isawaitable(value):
                                            await value
                                break
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                seconds = flood_wait_seconds(exc)
                                if seconds > 0:
                                    if governor is not None and not getattr(exc, "_archive_x_upload_floodwait_noted", False):
                                        try:
                                            governor.note_upload_floodwait(seconds, request=type(exc).__name__)
                                        except Exception:
                                            _swallowed_note("telegram_sender._archive_x_upload_file.upload_worker")
                                            pass
                                    await emit_status(
                                        f"Telegram asked to slow down — resuming in {int(seconds)}s",
                                        int(seconds),
                                    )
                                    # No immediate retry. Reuse the same file id and
                                    # put only the rejected part back after the wait.
                                    await asyncio.sleep(seconds)
                                    retries += 1
                                else:
                                    retries += 1
                                    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                                        await emit_status(
                                            f"Upload stalled at part {part_index + 1}/{part_count}; retrying that part", 0
                                        )
                                    if retries < UPLOAD_PART_MAX_ATTEMPTS:
                                        await asyncio.sleep(min(8.0, 0.5 * (2 ** min(retries - 1, 4))))
                                if retries >= UPLOAD_PART_MAX_ATTEMPTS:
                                    failure.append(RuntimeError(
                                        f"Telegram upload failed for {path.name}, part {part_index + 1}/{part_count}: "
                                        f"{type(exc).__name__}: {exc}"
                                    ))
                                    abort.set()
                                    break
                    finally:
                        queue.task_done()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            failure.append(exc)
            abort.set()

    workers = [asyncio.create_task(upload_worker()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        try:
            if old_wait_callback is None:
                delattr(client, "_archive_x_upload_wait_callback")
            else:
                setattr(client, "_archive_x_upload_wait_callback", old_wait_callback)
        except Exception:
            _swallowed_note("telegram_sender._archive_x_upload_file")
            pass
        await connections.close()
    if failure:
        raise failure[0]
    missing = sorted(set(range(part_count)) - confirmed)
    if missing:
        raise RuntimeError(
            f"Telegram upload incomplete for {path.name}: {len(missing)} parts remain unconfirmed "
            f"(first missing part {missing[0] + 1}/{part_count})"
        )
    _UPLOAD_LEDGER.pop(ledger_key, None)   # finished: a later upload of this file starts with a new id
    if callable(progress_callback):
        value = progress_callback(file_size, file_size)
        if inspect.isawaitable(value):
            await value
    if is_big:
        return types.InputFileBig(id=file_id, parts=part_count, name=file_name)
    md5 = await asyncio.to_thread(_md5_file, path)
    return types.InputFile(id=file_id, parts=part_count, name=file_name, md5_checksum=md5)




# ==============================================================================
# SECTION: Cover photos
# Cover upload and cover detection.
# ==============================================================================

def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CoverReuse:
    """Whether the contact sheet is uploaded once and used for both the video's cover and the picture document.

    The sheet used to be uploaded twice for every transferred video (as the cover photo, then again as the separate picture). Telegram lets one uploaded
    file be used by several messages, but that is not something this application can check without Telegram: the first time Telegram refuses an
    uploaded file for the second use, reuse is switched off for the rest of the process and everything goes back to uploading each time, as before."""
    enabled = True

    @classmethod
    def reset(cls) -> None:
        cls.enabled = True


_REFUSED_UPLOAD_TOKENS = ("FILE_ID_INVALID", "FILE_PARTS_INVALID", "FILE_PART_", "FILE_REFERENCE", "MEDIA_INVALID", "IMAGE_PROCESS_FAILED", "ALREADY_USED")
_REFUSED_UPLOAD_CLASSES = ("FileIdInvalidError", "FilePartsInvalidError", "FilePartInvalidError", "FilePartMissingError", "FileReferenceInvalidError",
                           "FileReferenceExpiredError", "FileReferenceEmptyError", "MediaInvalidError", "ImageProcessFailedError")


def upload_was_refused(exc: BaseException) -> bool:
    """Telegram refused an uploaded file handle (so no message was created): safe to upload again. Any other error is not treated this way."""
    if type(exc).__name__ in _REFUSED_UPLOAD_CLASSES:
        return True
    text = f"{type(exc).__name__} {exc}".upper().replace(" ", "_")
    return any(token in text for token in _REFUSED_UPLOAD_TOKENS)


async def preupload_cover_file(client, thumb: bytes, file_name: str = "cover.jpg"):
    """Upload the contact-sheet bytes once and return the uploaded-file handle, or None when reuse is off or the upload failed (callers then upload as before)."""
    if not CoverReuse.enabled or not thumb:
        return None
    try:
        return await client.upload_file(io.BytesIO(thumb), file_name=file_name, file_size=len(thumb), part_size_kb=UPLOAD_PART_SIZE_KB)
    except asyncio.CancelledError:
        raise
    except Exception:
        _swallowed_note("telegram_sender.preupload_cover_file")
        return None


async def _register_cover_photo(client, target_entity, uploaded, status_callback=None, *, stop_on_refusal=False):
    """Register an uploaded file as a Telegram photo and return the InputPhoto for video_cover. `stop_on_refusal`: for a shared upload, a refusal is
    reported at once (the caller uploads again) and not retried three times first."""
    from telethon import functions, types, utils
    res = await _retry(lambda: _await_upload_stage(
        lambda: client(functions.messages.UploadMediaRequest(
            peer=target_entity, media=types.InputMediaUploadedPhoto(file=uploaded))),
        status_callback,
        "Registering Telegram video cover",
    ), stop_if=upload_was_refused if stop_on_refusal else None)
    photo = getattr(res, "photo", None)
    if not photo:
        raise RuntimeError("Telegram did not return an uploaded cover photo")
    return utils.get_input_photo(photo)


async def _upload_cover_photo(client, target_entity, thumb: bytes, *, status_callback=None, uploaded=None):
    """Upload a thumbnail as a Telegram Photo and return InputPhoto for video_cover. `uploaded` is a handle from preupload_cover_file: used when
    given (no second upload), and if Telegram refuses it the sheet is uploaded again and reuse is switched off."""
    if uploaded is not None:
        try:
            return await _register_cover_photo(client, target_entity, uploaded, status_callback, stop_on_refusal=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not upload_was_refused(exc):
                raise
            from .transfer_timing import TIMER
            CoverReuse.enabled = False
            TIMER.reuse(False)
            _swallowed_note("telegram_sender._upload_cover_photo")
    uploaded = await _await_upload_stage(
        lambda: client.upload_file(
            io.BytesIO(thumb), file_name="cover.jpg", file_size=len(thumb),
            part_size_kb=UPLOAD_PART_SIZE_KB,
        ),
        status_callback,
        "Uploading Telegram video cover",
    )
    return await _register_cover_photo(client, target_entity, uploaded, status_callback)


def _message_has_custom_cover(message) -> bool:
    """Return True only when Telegram reports an actual video_cover on the sent media."""
    media = getattr(message, "media", None)
    document = getattr(media, "document", None)
    return bool(getattr(media, "video_cover", None) or getattr(document, "video_cover", None))




# ==============================================================================
# SECTION: Local upload message resolution
# Locates an uploaded message and attaches its cover.
# ==============================================================================

class LocalVideoCoverError(RuntimeError):
    """A created local-upload video needs a cover repair before delivery can finish."""

    def __init__(self, message: str, *, message_id: int | None = None):
        super().__init__(message)
        self.message_id = int(message_id or 0)


def _telegram_message_file_name(message) -> str:
    file_name = str(getattr(getattr(message, "file", None), "name", "") or "").strip()
    if file_name:
        return Path(file_name).name
    document = getattr(message, "document", None)
    if document is None:
        document = getattr(getattr(message, "media", None), "document", None)
    for attribute in getattr(document, "attributes", ()) or ():
        if type(attribute).__name__ == "DocumentAttributeFilename":
            return Path(str(getattr(attribute, "file_name", "") or "")).name
    return ""


def _matches_local_upload_message(message, *, file_name: str, baseline_message_id: int,
                                  caption_text: str, image: bool = False,
                                  expected_size: int | None = None) -> bool:
    if message is None:
        return False
    try:
        if int(getattr(message, "id", 0) or 0) <= int(baseline_message_id or 0):
            return False
    except (TypeError, ValueError):
        return False
    actual_name = _telegram_message_file_name(message)
    wanted_name = Path(str(file_name or "")).name
    from .media_utils import canonical_media_key, is_image_message, is_video_message
    # Telegram may sanitize a name, or the container extension may differ from the source (.ts converted to .mp4);
    # a normalized name or the exact uploaded byte size still identifies our own message.
    name_ok = actual_name.casefold() == wanted_name.casefold() or (
        bool(actual_name) and canonical_media_key(actual_name) == canonical_media_key(wanted_name) != ""
    )
    remote_size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
    size_ok = bool(expected_size) and remote_size == int(expected_size)
    if not (name_ok or size_ok):
        return False
    if image:
        is_image = is_image_message(message)
        if not is_image:
            document = getattr(message, "document", None)
            if document is None:
                document = getattr(getattr(message, "media", None), "document", None)
            mime = str(getattr(document, "mime_type", "") or "").lower()
            is_image = mime.startswith("image/") or Path(actual_name).suffix.lower() in {
                ".jpg", ".jpeg", ".png", ".webp",
            }
        if not is_image:
            return False
    elif not is_video_message(message):
        return False
    actual_caption = str(getattr(message, "message", "") or "").strip()
    return not caption_text or not actual_caption or actual_caption == str(caption_text).strip()


def _sent_message_id_from_response(response, random_id: int) -> int:
    """Telegram's own random_id -> message id mapping for a SendMedia response."""
    for item in [response, *(getattr(response, "updates", None) or [])]:
        name = type(item).__name__
        if name == "UpdateMessageID" and int(getattr(item, "random_id", 0) or 0) == int(random_id):
            return int(getattr(item, "id", 0) or 0)
        if name == "UpdateShortSentMessage":
            return int(getattr(item, "id", 0) or 0)
    return 0


async def _message_by_sent_id(client, target_entity, response, random_id: int, baseline_message_id: int):
    message_id = _sent_message_id_from_response(response, random_id)
    if message_id <= int(baseline_message_id or 0):
        return None
    found = await client.get_messages(target_entity, ids=message_id)
    if isinstance(found, (list, tuple)):
        found = next((m for m in found if m is not None), None)
    return found


async def _resolve_local_upload_message(client, target_entity, *, file_name: str,
                                        baseline_message_id: int, caption_text: str = "",
                                        image: bool = False, expected_size: int | None = None):
    """Find exactly one matching message newer than the saved upload watermark."""
    recent = await client.get_messages(target_entity, limit=100)
    matches = [
        message for message in (recent or [])
        if _matches_local_upload_message(
            message, file_name=file_name, baseline_message_id=baseline_message_id,
            caption_text=caption_text, image=image, expected_size=expected_size,
        )
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple Telegram messages match {Path(file_name).name!r} after checkpoint "
            f"{int(baseline_message_id or 0)}; delivery remains ambiguous."
        )
    return matches[0] if matches else None


async def _latest_message_id(client, target_entity) -> int:
    recent = await client.get_messages(target_entity, limit=1)
    return max((int(getattr(message, "id", 0) or 0) for message in (recent or [])), default=0)


async def _attach_local_video_cover(client, target_entity, message, cover, *,
                                    status_callback=None):
    """Attach and verify a cover on a known Telegram video without re-uploading it."""
    from telethon import functions, types, utils
    from ..jobs.context import JobCancelled, JobSkipped
    message_id = int(getattr(message, "id", 0) or 0)
    document = getattr(message, "document", None)
    if document is None:
        document = getattr(getattr(message, "media", None), "document", None)
    if not message_id or document is None:
        raise LocalVideoCoverError(
            "Cover verification failed: the created Telegram video could not be edited.",
            message_id=message_id,
        )
    try:
        await _retry(lambda: _await_upload_stage(
            lambda: client(functions.messages.EditMessageRequest(
                peer=target_entity,
                id=message_id,
                media=types.InputMediaDocument(
                    id=utils.get_input_document(document),
                    video_cover=cover,
                ),
            )),
            status_callback,
            "Repairing the Telegram video cover",
        ))
        checked = await _await_upload_stage(
            lambda: client.get_messages(target_entity, ids=message_id),
            status_callback,
            "Verifying the repaired Telegram video cover",
        )
        if checked is None or not _message_has_custom_cover(checked):
            raise RuntimeError("Telegram did not persist the repaired custom cover")
        return checked
    except (JobCancelled, JobSkipped):
        raise
    except Exception as exc:
        raise LocalVideoCoverError(
            f"Cover verification failed for Telegram video {message_id}: {type(exc).__name__}: {exc}",
            message_id=message_id,
        ) from exc


async def ensure_local_video_cover(client, target, message_id: int, cover_bytes: bytes, *,
                                  status_callback=None):
    """Repair a known Upload Local Files video cover and require Telegram verification."""
    from ..jobs.context import JobCancelled, JobSkipped
    entity = await client.get_input_entity(target)
    checked = await _await_upload_stage(
        lambda: client.get_messages(entity, ids=int(message_id)),
        status_callback,
        "Checking the existing Telegram video cover",
    )
    if not checked:
        raise LocalVideoCoverError(
            f"Cover verification failed: Telegram video {int(message_id)} was not found.",
            message_id=int(message_id),
        )
    if _message_has_custom_cover(checked):
        return checked
    try:
        cover = await _upload_cover_photo(client, entity, bytes(cover_bytes), status_callback=status_callback)
    except (JobCancelled, JobSkipped):
        raise
    except Exception as exc:
        raise LocalVideoCoverError(
            f"Cover upload failed while repairing Telegram video {int(message_id)}: "
            f"{type(exc).__name__}: {exc}",
            message_id=int(message_id),
        ) from exc
    return await _attach_local_video_cover(
        client, entity, checked, cover, status_callback=status_callback,
    )




# ==============================================================================
# SECTION: Public send operations
# Thumbnail, split and video-with-cover sends.
# ==============================================================================

async def send_local_thumbnail_document(client, target, path, *, file_name: str,
                                        message_random_id: int, baseline_message_id: int,
                                        progress_callback=None):
    """Send an idempotent Local Upload contact sheet and reconcile lost responses."""
    from telethon import functions, types
    from ..jobs.context import JobCancelled, JobSkipped

    target_entity = await client.get_input_entity(target)
    display_name = Path(str(file_name or Path(path).name)).name
    random_id = int(message_random_id or 0)
    if not display_name or not random_id or random_id >= (1 << 63):
        raise ValueError("A safe thumbnail filename and positive 63-bit Telegram request ID are required")
    watermark = int(baseline_message_id or 0)
    if baseline_message_id is None:
        watermark = await _latest_message_id(client, target_entity)

    try:
        uploaded = await _archive_x_upload_file(
            client, Path(path), file_name=display_name,
            progress_callback=progress_callback,
        )
        mime_type = "image/jpeg"
        media = types.InputMediaUploadedDocument(
            file=uploaded,
            mime_type=mime_type,
            attributes=[types.DocumentAttributeFilename(file_name=display_name)],
        )
        response = await _retry(lambda: client(functions.messages.SendMediaRequest(
            peer=target_entity,
            media=media,
            message="",
            random_id=random_id,
            entities=[],
        )))
    except (JobCancelled, JobSkipped):
        raise
    except Exception as exc:
        try:
            recovered = await _resolve_local_upload_message(
                client, target_entity, file_name=display_name,
                baseline_message_id=watermark, image=True,
            )
        except Exception as lookup_exc:
            raise RuntimeError(
                f"Companion-image upload status is uncertain for {display_name}: "
                f"{type(exc).__name__}: {exc}; reconciliation failed: "
                f"{type(lookup_exc).__name__}: {lookup_exc}"
            ) from exc
        if recovered is not None:
            return recovered
        raise RuntimeError(
            f"Companion-image upload failed or is still unconfirmed for {display_name}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    try:
        by_id = await _message_by_sent_id(client, target_entity, response, random_id, watermark)
    except Exception:
        by_id = None
    if by_id is not None:
        return by_id

    candidates = [
        getattr(update, "message", None)
        for update in (getattr(response, "updates", None) or [])
    ]
    if getattr(response, "message", None) is not None:
        candidates.append(response.message)
    matching = [
        candidate for candidate in candidates
        if _matches_local_upload_message(
            candidate, file_name=display_name, baseline_message_id=watermark,
            caption_text="", image=True,
        )
    ]
    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        raise RuntimeError(f"Telegram returned multiple companion-image messages for {display_name}")
    recovered = await _resolve_local_upload_message(
        client, target_entity, file_name=display_name,
        baseline_message_id=watermark, image=True,
    )
    if recovered is None:
        raise RuntimeError(
            f"Companion-image upload completed but Telegram message could not be resolved: {display_name}"
        )
    return recovered




async def send_split_local_video(
    client, target, video_path, *, caption: str = "", file_name: str | None = None,
    progress_callback=None, status_callback=None, max_part_bytes: int | None = None,
    group_id: str | None = None, part_callback=None,
):
    """Upload an oversized video as independently playable ``name.partNN.ext`` videos.

    Each part is split using FFmpeg lossless stream-copy (``-c copy``) at
    keyframe boundaries, then uploaded as a Telegram video with
    ``DocumentAttributeVideo`` so it plays natively in clients. Volumes are
    uploaded sequentially. A failed upload removes any parts already posted.
    """
    from telethon import functions, types
    from telethon.extensions import html as telethon_html
    from ..core.upload_limits import get_upload_policy
    from ..core.split_files import (
        SplitDeliveryResult, UploadedSplitPart,
        split_video_into_playable_parts, _playable_part_telegram_name,
    )

    source = Path(video_path)
    if not source.is_file() or source.stat().st_size <= 0:
        raise RuntimeError(f"Split-upload source is missing or empty: {source}")
    policy = await get_upload_policy(client)
    total_size = int(source.stat().st_size)
    if total_size <= int(policy.safe_limit_bytes):
        raise RuntimeError("Split upload is only used when the source exceeds the configured Telegram safe upload limit")
    # Keep a single Telegram file below the practical MTProto part-count ceiling.
    volume_limit = min(int(max_part_bytes or policy.safe_limit_bytes), int(policy.safe_limit_bytes), 1_800_000_000)
    if volume_limit <= 0:
        raise RuntimeError("Telegram upload policy has no positive per-volume size limit")
    display_name = Path(str(file_name or source.name)).name
    if not display_name or display_name in {".", ".."}:
        raise RuntimeError("A safe original filename is required for split upload")
    target_entity = await client.get_input_entity(target)
    created_messages: list[int] = []
    uploaded_parts: list[UploadedSplitPart] = []
    sent_bytes = 0
    resolved_group_id = str(group_id or uuid.uuid4().hex)
    previous_upload_wait_callback = getattr(client, "_archive_x_upload_wait_callback", None)

    async def emit_status(message: str, seconds: int = 0):
        if callable(status_callback):
            try:
                value = status_callback(str(message), int(seconds or 0))
                if inspect.isawaitable(value):
                    await value
            except Exception:
                _swallowed_note("telegram_sender.send_split_local_video.emit_status")
                pass

    try:
        with tempfile.TemporaryDirectory(prefix="archive_x_split_upload_") as staging:
            await emit_status("Splitting video into playable parts (FFmpeg lossless copy)…")
            split_set = await asyncio.to_thread(
                split_video_into_playable_parts, source, staging, volume_limit,
                logical_filename=display_name,
            )
            try:
                caption_text, caption_entities = telethon_html.parse(str(caption or ""))
            except Exception:
                caption_text, caption_entities = str(caption or ""), []
            part_count = len(split_set.volumes)
            combined_size = split_set.total_size
            for volume in split_set.volumes:
                telegram_filename = _playable_part_telegram_name(display_name, volume.index)
                await emit_status(f"Uploading part {volume.index}/{part_count}: {telegram_filename}")

                # Probe each part for video metadata
                part_meta: dict = {}
                try:
                    from .uploader import probe, ffmpeg_tools
                    _ff, _fp = await asyncio.to_thread(ffmpeg_tools)
                    part_meta = await asyncio.to_thread(probe, _fp, volume.path)
                except Exception:
                    _swallowed_note("telegram_sender.send_split_local_video")
                    pass
                part_width = max(1, int(part_meta.get("width") or 0))
                part_height = max(1, int(part_meta.get("height") or 0))
                part_duration = max(0, int(round(float(part_meta.get("duration") or 0))))
                part_audio = str(part_meta.get("audio_codec") or "").lower()
                part_nosound = not bool(part_audio)

                def part_progress(part_sent: int, _part_total: int, base: int = sent_bytes):
                    if callable(progress_callback):
                        value = progress_callback(min(combined_size, base + int(part_sent)), combined_size)
                        if inspect.isawaitable(value):
                            return value
                    return None

                uploaded = await _archive_x_upload_file(
                    client, volume.path, file_name=telegram_filename,
                    progress_callback=part_progress if callable(progress_callback) else None,
                    status_callback=status_callback,
                )

                # Determine MIME type for the part file
                part_mime = mimetypes.guess_type(telegram_filename)[0] or "video/mp4"
                if not str(part_mime).lower().startswith("video/"):
                    part_mime = "video/mp4"

                # Upload as a real video with DocumentAttributeVideo
                media = types.InputMediaUploadedDocument(
                    file=uploaded,
                    mime_type=part_mime,
                    attributes=[
                        types.DocumentAttributeVideo(
                            duration=part_duration,
                            w=part_width, h=part_height,
                            round_message=False,
                            supports_streaming=True,
                            nosound=part_nosound,
                        ),
                        types.DocumentAttributeFilename(file_name=telegram_filename),
                    ],
                )
                # Retry with the same random_id so an uncertain timeout cannot
                # publish the same physical part twice.
                random_id = secrets.randbits(63) or 1
                result = await _retry(lambda: client(functions.messages.SendMediaRequest(
                    peer=target_entity,
                    media=media,
                    message=caption_text if volume.index == 1 else "",
                    random_id=random_id,
                    entities=caption_entities if volume.index == 1 else [],
                )))
                sent_message = None
                for update in (getattr(result, "updates", None) or []):
                    candidate = getattr(update, "message", None)
                    candidate_file = getattr(candidate, "file", None) if candidate is not None else None
                    if (candidate is not None and getattr(candidate, "id", None)
                            and str(getattr(candidate_file, "name", "") or "") == telegram_filename):
                        sent_message = candidate
                        break
                if sent_message is None and getattr(result, "message", None) is not None:
                    candidate = result.message
                    candidate_file = getattr(candidate, "file", None)
                    if str(getattr(candidate_file, "name", "") or "") == telegram_filename:
                        sent_message = candidate
                if sent_message is None:
                    recent = await client.get_messages(target_entity, limit=min(20, part_count + 5))
                    for candidate in (recent or []):
                        candidate_file = getattr(candidate, "file", None)
                        if str(getattr(candidate_file, "name", "") or "") == telegram_filename:
                            sent_message = candidate
                            break
                if sent_message is None or not getattr(sent_message, "id", None):
                    raise RuntimeError(f"Telegram accepted {telegram_filename}, but its message ID could not be resolved")
                # Track as soon as we have an ID, before verification, so a
                # verification failure still triggers best-effort cleanup.
                created_messages.append(int(sent_message.id))
                checked = await client.get_messages(target_entity, ids=int(sent_message.id))
                checked_file = getattr(checked, "file", None) if checked is not None else None
                actual_name = str(getattr(checked_file, "name", "") or "")
                actual_size = int(getattr(checked_file, "size", 0) or 0)
                if actual_name != telegram_filename or actual_size != int(volume.size):
                    raise RuntimeError(
                        f"Telegram volume verification failed for {telegram_filename}: "
                        f"name={actual_name!r}, size={actual_size}, expected_size={volume.size}"
                    )
                part = UploadedSplitPart(
                    part_index=int(volume.index), message_id=int(checked.id),
                    logical_filename=display_name, telegram_filename=telegram_filename,
                    size_bytes=int(volume.size),
                )
                uploaded_parts.append(part)
                sent_bytes += int(volume.size)
                if callable(part_callback):
                    value = part_callback(part)
                    if inspect.isawaitable(value):
                        await value
            if sent_bytes != combined_size or len(uploaded_parts) != part_count:
                raise RuntimeError("Split upload completed with an inconsistent part count or byte total")
            # Use video MIME type for the logical file
            mime_type = mimetypes.guess_type(source.name)[0] or mimetypes.guess_type(display_name)[0] or "video/mp4"
            if not str(mime_type).lower().startswith("video/"):
                mime_type = "video/mp4"
            primary = await client.get_messages(target_entity, ids=uploaded_parts[0].message_id)
            if primary is None:
                raise RuntimeError("Telegram's first split volume disappeared before upload verification completed")
            return SplitDeliveryResult(
                primary_message=primary, parts=tuple(uploaded_parts), group_id=resolved_group_id,
                logical_filename=display_name, total_size=combined_size, mime_type=mime_type,
            )
    except BaseException:
        if created_messages:
            try:
                await client.delete_messages(target_entity, created_messages)
            except Exception:
                _swallowed_note("telegram_sender.send_split_local_video")
                pass
        raise
    finally:
        try:
            if previous_upload_wait_callback is None:
                delattr(client, "_archive_x_upload_wait_callback")
            else:
                setattr(client, "_archive_x_upload_wait_callback", previous_upload_wait_callback)
        except Exception:
            _swallowed_note("telegram_sender.send_split_local_video")
            pass


async def verify_video_cover_message(client, target_entity, message_id):
    """Fetch a sent video and require Telegram's native custom video cover."""
    checked = await client.get_messages(target_entity, ids=int(message_id))
    if not checked or not _message_has_custom_cover(checked):
        raise RuntimeError("Telegram video exists but the custom video cover was not persisted")
    return checked

async def send_existing_video_with_cover(client, target, source_message, thumb: bytes, caption: str = "", *, cover_upload=None):
    """Re-send an existing Telegram video with an explicit custom cover.

    The same image is later allowed to be posted separately by the caller.
    The sent message is read back and the custom video_cover is required.
    """
    target_entity = await client.get_input_entity(target)
    return await _send_existing_video_with_cover(client, target_entity, source_message, thumb, caption, cover_upload=cover_upload)


async def send_local_video_with_cover(client, target, video_path, thumb: bytes, *,
                                     caption: str = "", progress_callback=None, status_callback=None,
                                     supports_streaming: bool = True, video_meta: dict | None = None,
                                      file_name_override: str | None = None,
                                      message_random_id: int | None = None,
                                      baseline_message_id: int | None = None):
    """Upload a local video with the custom cover attached in the *initial* MTProto request.

    This deliberately avoids the older ``send_file() -> editMessage()`` sequence.  That
    sequence can briefly create a normal video preview and then replace it with the custom
    cover; a client that cached the first media representation (notably some iPad builds)
    can continue showing the first preview.  Telegram's MTProto API supports ``video_cover``
    directly on ``inputMediaUploadedDocument`` so the cover is present when the message is
    created.  The returned message is then read back and the persisted ``video_cover`` is
    verified before the upload is considered successful.
    """
    from telethon import functions, types, utils
    from telethon.extensions import html as telethon_html
    from ..jobs.context import JobCancelled, JobSkipped

    if not thumb:
        raise RuntimeError("A custom video cover is required but no thumbnail bytes were supplied")
    await ensure_upload_allowed(client, video_path)
    target_entity = await client.get_input_entity(target)
    if baseline_message_id is None:
        try:
            baseline_message_id = await _latest_message_id(client, target_entity)
        except Exception as exc:
            raise RuntimeError(
                f"Could not establish a Telegram message checkpoint before uploading {Path(video_path).name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    baseline_message_id = int(baseline_message_id or 0)

    meta = dict(video_meta or {})
    if not meta.get("width") or not meta.get("height") or not meta.get("duration"):
        try:
            from ..core import internal_media
            probed = internal_media.probe(video_path)
            meta = {**probed, **meta}
        except Exception as exc:
            raise RuntimeError(f"Could not determine video metadata before Telegram upload: {exc}") from exc

    width = max(1, int(meta.get("width") or 0))
    height = max(1, int(meta.get("height") or 0))
    duration = max(0, int(round(float(meta.get("duration") or 0))))
    audio_codec = str(meta.get("audio_codec") or "").lower()
    nosound = not bool(audio_codec)
    display_filename = str(file_name_override or Path(video_path).name).strip() or Path(video_path).name
    upload_size = int(Path(video_path).stat().st_size)
    import mimetypes
    mime_type = mimetypes.guess_type(str(video_path))[0] or mimetypes.guess_type(display_filename)[0] or "video/mp4"
    if not str(mime_type).lower().startswith("video/"):
        mime_type = "video/mp4"

    attributes = [
        types.DocumentAttributeVideo(
            duration=duration, w=width, h=height,
            round_message=False, supports_streaming=bool(supports_streaming),
            nosound=nosound,
        ),
        types.DocumentAttributeFilename(file_name=display_filename),
    ]

    caption_text, caption_entities = telethon_html.parse(str(caption or ""))
    sent = None
    # Keep FloodWait messaging active for the cover's small upload as well as
    # the main file. _archive_x_upload_file temporarily installs its own
    # callback and restores this wrapper when it finishes.
    previous_upload_wait_callback = getattr(client, "_archive_x_upload_wait_callback", None)
    def _send_upload_wait(seconds: int, request_name: str):
        if not callable(status_callback):
            return None
        return status_callback(
            f"Telegram asked to slow down — resuming in {int(seconds)}s",
            int(seconds),
        )
    try:
        setattr(client, "_archive_x_upload_wait_callback", _send_upload_wait)
    except Exception:
        _swallowed_note("telegram_sender.send_local_video_with_cover")
        pass
    try:
        # Upload the video bytes once, and the cover as a Telegram Photo.  The
        # photo is not posted as a separate message; messages.sendMedia uses it
        # as InputMediaUploadedDocument.video_cover.
        video_file = await _archive_x_upload_file(
            client, video_path, file_name=display_filename,
            progress_callback=progress_callback, status_callback=status_callback,
        )
        try:
            cover = await _upload_cover_photo(
                client, target_entity, thumb, status_callback=status_callback
            )
        except (JobCancelled, JobSkipped):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Native video cover upload failed for {display_filename}: {type(exc).__name__}: {exc}"
            ) from exc

        # If Telegram explicitly says one or more parts have expired/missing,
        # upload once with a fresh file id. Other sendMedia errors do not cause
        # the video bytes to be uploaded again. The message random_id is stable.
        random_id = int(message_random_id or secrets.randbits(63) or 1)
        if random_id <= 0 or random_id >= (1 << 63):
            raise ValueError("A positive 63-bit Telegram message request ID is required")
        for video_upload_attempt in range(2):
            media = types.InputMediaUploadedDocument(
                file=video_file,
                mime_type=mime_type,
                attributes=attributes,
                video_cover=cover,
            )
            try:
                sent = await _retry(lambda: _await_upload_stage(
                    lambda: client(functions.messages.SendMediaRequest(
                        peer=target_entity,
                        media=media,
                        message=caption_text,
                        random_id=random_id,
                        entities=caption_entities,
                    )),
                    status_callback,
                    "Waiting for Telegram to create the video message",
                ), stop_if=_is_missing_or_expired_upload)
                break
            except Exception as exc:
                if video_upload_attempt == 0 and _is_missing_or_expired_upload(exc):
                    try:
                        if callable(status_callback):
                            value = status_callback("Telegram expired upload parts; re-uploading missing file with a fresh file id", 0)
                            if inspect.isawaitable(value):
                                await value
                    except Exception:
                        _swallowed_note("telegram_sender.send_local_video_with_cover")
                        pass
                    video_file = await _archive_x_upload_file(
                        client, video_path, file_name=display_filename,
                        progress_callback=progress_callback, status_callback=status_callback,
                    )
                    continue
                try:
                    recovered = await _resolve_local_upload_message(
                        client, target_entity, file_name=display_filename,
                        baseline_message_id=baseline_message_id,
                        caption_text=caption_text, expected_size=upload_size,
                    )
                except Exception:
                    recovered = None
                if recovered is not None:
                    # Telegram accepted the message but the response was lost.
                    # The caller's persisted random ID makes a later retry safe
                    # if this immediate history lookup is temporarily stale.
                    sent = type("_RecoveredSend", (), {"message": recovered})()
                    break
                raise

        # SendMediaRequest returns Updates rather than a single Message.  Resolve
        # the newly-created message by its id from the returned update when
        # possible; otherwise use the latest matching video as a conservative
        # fallback.  We never report success until Telegram exposes video_cover.
        checked = None
        try:
            checked = await _message_by_sent_id(client, target_entity, sent, random_id, baseline_message_id)
        except Exception:
            checked = None
        updates = getattr(sent, "updates", None) or []
        for update in ([] if checked is not None else updates):
            candidate = getattr(update, "message", None)
            if _matches_local_upload_message(
                candidate, file_name=display_filename,
                baseline_message_id=baseline_message_id, caption_text=caption_text,
                expected_size=upload_size,
            ):
                checked = candidate
                break
        if checked is None and getattr(sent, "message", None) is not None:
            candidate = getattr(sent, "message")
            if _matches_local_upload_message(
                candidate, file_name=display_filename,
                baseline_message_id=baseline_message_id, caption_text=caption_text,
                expected_size=upload_size,
            ):
                checked = candidate
        if checked is None:
            checked = await _await_upload_stage(
                lambda: _resolve_local_upload_message(
                    client, target_entity, file_name=display_filename,
                    baseline_message_id=baseline_message_id,
                    caption_text=caption_text, expected_size=upload_size,
                ),
                status_callback,
                "Locating the uploaded Telegram video",
            )
        if checked is None or not getattr(checked, "id", None):
            try:
                newest = await client.get_messages(target_entity, limit=4)
                seen = "; ".join(
                    f"#{getattr(m, 'id', '?')} {_telegram_message_file_name(m)!r} {getattr(getattr(m, 'file', None), 'size', '?')}"
                    for m in (newest or [])
                )
            except Exception:
                seen = "unavailable"
            raise RuntimeError(
                "Telegram accepted the upload but the created video message could not be resolved "
                f"(expected name {display_filename!r}, size {upload_size}, after message {int(baseline_message_id or 0)}; "
                f"newest messages: {seen})"
            )

        verified = await _await_upload_stage(
            lambda: client.get_messages(target_entity, ids=int(checked.id)),
            status_callback,
            "Verifying the Telegram video and its cover",
        )
        if not verified or not _message_has_custom_cover(verified):
            try:
                verified = await _attach_local_video_cover(
                    client, target_entity, verified or checked, cover,
                    status_callback=status_callback,
                )
            except LocalVideoCoverError:
                raise
            except Exception as exc:
                raise LocalVideoCoverError(
                    f"Cover verification failed for Telegram video {int(checked.id)}: "
                    f"{type(exc).__name__}: {exc}",
                    message_id=int(checked.id),
                ) from exc
        return verified
    except asyncio.CancelledError:
        if sent is not None and getattr(sent, "id", None):
            try:
                await client.delete_messages(target_entity, [int(sent.id)])
            except Exception:
                _swallowed_note("telegram_sender.send_local_video_with_cover")
                pass
        raise
    except Exception:
        # A SendMediaRequest may return an Updates object, so cleanup is best
        # effort. Do not delete an unrelated recent message when no concrete id
        # was resolved.
        if sent is not None and getattr(sent, "id", None):
            try:
                await client.delete_messages(target_entity, [int(sent.id)])
            except Exception:
                _swallowed_note("telegram_sender.send_local_video_with_cover")
                pass
        raise
    finally:
        try:
            if previous_upload_wait_callback is None:
                delattr(client, "_archive_x_upload_wait_callback")
            else:
                setattr(client, "_archive_x_upload_wait_callback", previous_upload_wait_callback)
        except Exception:
            _swallowed_note("telegram_sender.send_local_video_with_cover")
            pass

async def _send_existing_video_with_cover(client, target_entity, source_message, thumb: bytes, caption: str, *, cover_upload=None):
    """Send an existing Telegram video with an explicit MTProto video cover."""
    from telethon import types, utils
    if not getattr(source_message, "document", None):
        raise RuntimeError("Source message has no Telegram document")
    cover = await _upload_cover_photo(client, target_entity, thumb, uploaded=cover_upload)
    media = types.InputMediaDocument(
        id=utils.get_input_document(source_message.document),
        video_cover=cover,
    )
    sent = None
    try:
        sent = await _retry(lambda: client.send_file(
            target_entity, media, caption=caption, parse_mode="html"))
        # Telegram can accept the media request but return a message without the
        # custom cover. Never report success for that case.
        checked = await client.get_messages(target_entity, ids=int(sent.id))
        if not checked or not _message_has_custom_cover(checked):
            raise RuntimeError("Telegram accepted the video but did not attach the custom video cover")
        return checked
    except Exception:
        if sent is not None and getattr(sent, "id", None):
            try:
                await client.delete_messages(target_entity, [int(sent.id)])
            except Exception:
                _swallowed_note("telegram_sender._send_existing_video_with_cover")
                pass
        raise




# ==============================================================================
# SECTION: Cover repair and send entry
# Cover repair and the send_video entry point.
# ==============================================================================

async def repair_existing_cover(db, client, source_channel_id: int, source_message_id: int,
                                target_channel_id: int, target_message_id: int, thumb_getter) -> dict:
    """Attach the custom cover to an already-sent video without re-uploading it.

    Telegram's MTProto API supports editing an existing media message with an
    InputMediaDocument carrying only the new video_cover, so the video bytes do
    not need to be uploaded again.
    """
    from telethon import functions, types, utils
    source = await client.get_messages(int(source_channel_id), ids=int(source_message_id))
    dest = await client.get_messages(int(target_channel_id), ids=int(target_message_id))
    if not source or not getattr(source, "document", None):
        raise FileNotFoundError("Source video no longer exists")
    if not dest or not getattr(dest, "document", None):
        raise FileNotFoundError("Destination video message no longer exists")
    if _message_has_custom_cover(dest):
        return {"ok": True, "changed": False, "message_id": int(dest.id), "detail": "cover already present"}

    thumb = await thumb_getter(int(source_channel_id), int(source_message_id))
    if not thumb:
        raise RuntimeError("No custom thumbnail is available for this video")
    target_entity = await client.get_input_entity(int(target_channel_id))
    cover = await _upload_cover_photo(client, target_entity, thumb)
    media = types.InputMediaDocument(
        id=utils.get_input_document(dest.document),
        video_cover=cover,
    )
    await _retry(lambda: client(functions.messages.EditMessageRequest(
        peer=target_entity, id=int(dest.id), media=media)))
    checked = await client.get_messages(target_entity, ids=int(dest.id))
    if not checked or not _message_has_custom_cover(checked):
        raise RuntimeError("Telegram did not persist the custom video cover")
    return {"ok": True, "changed": True, "message_id": int(dest.id)}


async def send_video(db, client, channel_id: int, message_id: int, target, thumb_getter=None, *,
                     premium: bool = False) -> dict:
    """Send an existing Telegram video to another channel using the shared
    archive delivery policy.

    The source bytes stay on Telegram: the video itself is resent by Telegram
    media reference. Telegram-origin visual reuse is source-first: an existing
    companion thumbnail, custom video cover, or native video thumbnail is reused
    before Archive X generates/fetches its persisted representative snapshot.
    When Archive X is the fallback, the representative still is used for
    the custom video cover and the separate thumbnail document and is retained
    locally for application use.
    The source message is never modified or deleted.
    """
    from telethon import utils
    _ensure_tables(db)
    opts = settings(db)
    message = await client.get_messages(int(channel_id), ids=int(message_id))
    if not message or not getattr(message, "file", None):
        raise FileNotFoundError("The video message no longer exists")
    if not getattr(message, "document", None):
        raise RuntimeError("The selected message is not a Telegram video/document")
    target_entity = await client.get_input_entity(target)

    # Resolve the local catalogue first. Identification/provider work is only
    # required when the configured caption actually needs fields that are not
    # already available locally. This prevents Send-to-Telegram from redoing
    # metadata work for an already-complete catalogue row.
    metadata_note = ""
    try:
        with db.lock:
            existing = db.conn.execute(
                "SELECT filename, oshash, identification_state FROM media "
                "WHERE source_channel=? AND source_message_id=? LIMIT 1",
                (str(channel_id), int(message_id)),
            ).fetchone()
        filename = str((existing["filename"] if existing else "") or getattr(getattr(message, "file", None), "name", None) or f"video_{message_id}.mp4")
        oshash = str((existing["oshash"] if existing else "") or "").strip() or None
        v_local = _video_fields(db, channel_id, message_id, message)
        if caption_requires_remote_metadata(opts["template"], v_local):
            from ..metadata.pipeline import identify_video
            metadata_note = "metadata refresh required: " + PROVIDER_REQUIRED
            match_result = await identify_video(
                db, channel_id=int(channel_id), message_id=int(message_id),
                filename=filename, oshash=oshash, force=False, suggest_only=False,
            )
            action = str(match_result.get("action") or "")
            method = str(match_result.get("method") or match_result.get("source") or "")
            if action in ("applied", "suggested") and method:
                metadata_note = f"metadata {action}: {method}"
        else:
            metadata_note = "metadata refresh skipped: " + LOCAL_CATALOGUE_COMPLETE
    except Exception as exc:
        # A metadata provider outage must not prevent a normal Telegram send.
        metadata_note = f"metadata refresh unavailable: {type(exc).__name__}"

    v = _video_fields(db, channel_id, message_id, message)
    caption = render_caption(opts["template"], v, 4096 if premium else 1024)
    notes: list[str] = []
    sent = None
    thumb = None

    # Telegram cover/document delivery always uses the validated 36-frame
    # contact sheet. The single-frame representative remains the Library/UI
    # thumbnail and is persisted separately below; it is never overwritten by
    # the delivery grid.
    from types import SimpleNamespace
    from ..engines.thumbnail_cache import ensure_playback_snapshot_sheet, playback_snapshot_cached_sync
    thumb_source = "playback-contact-sheet-36"
    with tempfile.TemporaryDirectory(prefix="archive_x_send_visual_"):
        sheet, sheet_mime = await ensure_playback_snapshot_sheet(
            db, SimpleNamespace(client=client, connected=True), channel_id, message_id,
            message=message,
        )
        if not sheet or not str(sheet_mime or "").lower().startswith("image/"):
            raise RuntimeError("Archive X 36-frame Telegram contact sheet is unavailable")
        thumb = bytes(sheet)
        representative = await asyncio.to_thread(playback_snapshot_cached_sync, db, channel_id, message_id)
        playback_snapshot = bytes(representative[0]) if representative else None
        if playback_snapshot is None:
            raise RuntimeError("Canonical representative snapshot is missing after contact-sheet preparation")

    cover_requested = opts["cover_mode"] in ("cover", "both")
    cover_supported = "video_cover" in inspect.signature(__import__("telethon").types.InputMediaDocument.__init__).parameters
    if cover_requested and not cover_supported:
        raise RuntimeError("Telethon does not expose video_cover; a verified custom cover is required")

    try:
        if cover_requested:
            # Re-send the existing Telegram video by reference, attaching the
            # Attach the validated 36-frame contact sheet as the native video cover.
            sent = await _send_existing_video_with_cover(client, target_entity, message, thumb, caption)
        else:
            async def plain():
                src = await client.get_messages(int(channel_id), ids=int(message_id))
                return await client.send_file(
                    target_entity, src.media, caption=caption, parse_mode="html",
                    supports_streaming=True,
                )
            sent = await _retry(plain)

        if isinstance(sent, (list, tuple)):
            sent = sent[0]

        # Verify the native contact-sheet cover first, then publish the same
        # 36-frame grid as a separate visible document immediately after the video.  Treat the
        # pair as one atomic delivery: if the thumbnail message fails, remove the
        # newly created video so a retry cannot leave a half-complete pair.
        checked = await verify_video_cover_message(client, target_entity, int(sent.id))
        sent = checked

        async def _send_separate_thumbnail():
            # Publish the full 36-frame contact sheet as a Telegram DOCUMENT
            # immediately after the video; do not change the established type.
            bio = io.BytesIO(thumb)
            bio.name = f"{Path(str(v['filename'])).stem}.jpg"
            return await _retry(lambda: client.send_file(
                target_entity, bio, force_document=True, caption=None,
            ))

        thumb_msg = await _send_separate_thumbnail()
        if isinstance(thumb_msg, (list, tuple)):
            thumb_msg = thumb_msg[0]
        if not thumb_msg or not getattr(thumb_msg, "id", None):
            raise RuntimeError("Telegram did not return the separate thumbnail message")
        try:
            checked_thumb = await client.get_messages(target_entity, ids=int(thumb_msg.id))
            if not checked_thumb or not getattr(checked_thumb, "document", None):
                raise RuntimeError("Telegram did not persist the separate thumbnail document")
        except Exception:
            try:
                await client.delete_messages(target_entity, [int(thumb_msg.id)])
            except Exception:
                _swallowed_note("telegram_sender.send_video")
                pass
            raise

        v["caption"] = caption
        await asyncio.to_thread(
            _register_copy, db, channel_id, message_id,
            int(utils.get_peer_id(target_entity)), sent, thumb_msg, thumb, v, thumb_source,
            playback_snapshot=playback_snapshot,
        )
        destination_channel_id = int(utils.get_peer_id(target_entity))
        library_ready = False
        library_media_id = None
        try:
            delivery = await asyncio.to_thread(
                _record_verified_send_copy, db,
                channel_id, message_id, destination_channel_id, int(sent.id),
                int(thumb_msg.id), v, caption,
            )
            library_ready = str(delivery.get("snapshot_state") or "").upper() == "READY"
            library_media_id = int(delivery.get("library_media_id") or 0) or None
            if not library_ready:
                notes.append("Library preview is still preparing")
                db.log(
                    f"Telegram send succeeded but the destination Library preview is still pending for {v['filename']}",
                    "WARN", event="telegram.send.library_registration_pending",
                )
        except Exception as library_exc:
            # Telegram has already verified the video and companion image.
            # Keep that successful delivery; log the separate catalogue issue
            # so a local indexing problem never falsely reports the send itself
            # as failed or deletes the verified Telegram messages.
            db.log(
                f"Telegram send succeeded but Library registration is pending for {v['filename']}: "
                f"{type(library_exc).__name__}: {library_exc}",
                "WARN", event="telegram.send.library_registration_pending",
            )
            notes.append("Library registration is pending")
        # Only the persistent 36 individual frames + representative remain in
        # Archive X. The composed grid is send-only and is removed after the
        # video cover, companion document, and delivery record are verified.
        try:
            from .thumbnail_cache import cleanup_telegram_delivery_contact_sheet
            cleanup_telegram_delivery_contact_sheet(int(channel_id), int(message_id))
        except Exception as cleanup_exc:
            try:
                db.log(
                    f"Verified send but delivery contact-sheet cleanup failed for {channel_id}/{message_id}: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}", "WARN"
                )
            except Exception:
                _swallowed_note("telegram_sender.send_video")
                pass
        if metadata_note:
            notes.append(metadata_note)
        notes.append(f"thumbnail: {thumb_source}")
        return {
            "ok": True,
            "sent_message_id": int(sent.id),
            "thumb_message_id": int(thumb_msg.id) if thumb_msg else None,
            "filename": str(v["filename"]),
            "library_media_id": library_media_id,
            "library_ready": library_ready,
            "caption": caption,
            "notes": notes,
            "thumbnail_source": thumb_source,
        }
    except asyncio.CancelledError:
        # Do not touch the source. Clean only a destination video that this
        # specific attempt created, preventing an orphaned partial send.
        if sent is not None and getattr(sent, "id", None):
            try:
                await client.delete_messages(target_entity, [int(sent.id)])
            except Exception:
                _swallowed_note("telegram_sender.send_video")
                pass
        raise
    except Exception:
        # A video without its verified thumbnail/cover is not a successful
        # Send-to-Telegram operation. Remove the newly created destination
        # video when possible; the source remains untouched.
        if sent is not None and getattr(sent, "id", None):
            try:
                await client.delete_messages(target_entity, [int(sent.id)])
            except Exception:
                _swallowed_note("telegram_sender.send_video")
                pass
        raise




# ==============================================================================
# SECTION: Copy registration
# Records and reconciles sent copies.
# ==============================================================================

def _register_copy(db, channel_id, message_id, target_id, sent, thumb_msg, thumb, v,
                   thumbnail_source="playback-contact-sheet-36", playback_snapshot: bytes | None = None):
    """Register delivery metadata while keeping the Library still separate from the sheet."""
    _ensure_tables(db)
    now = config.now_iso()
    src = v["row"]
    with db.lock:
        db.conn.execute(
            "INSERT INTO sent_log(source_channel,message_id,target_channel,sent_message_id,thumb_message_id,sent_at) "
            "VALUES(?,?,?,?,?,?)",
            (str(channel_id), int(message_id), str(target_id), int(sent.id),
             int(thumb_msg.id) if thumb_msg else None, now))
        db.conn.execute(
            "INSERT OR IGNORE INTO media(source_channel,source_message_id,filename,title,size,duration,width,height,"
            " fps,codec,audio_codec,orientation,resolution_category,streamable,thumbnail_message_id,thumbnail_status,"
            " caption,upload_date,release_date,metadata_source,provider_scene_id,provider_updated_at,metadata_synced_at,"
            " scene_type,site_name,site_url,provider_url,provider_payload,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(target_id), int(sent.id), v["filename"], src.get("title") or v["title"],
             src.get("size") or getattr(getattr(sent, "file", None), "size", 0) or 0,
             v["duration"], src.get("width") or 0, src.get("height") or 0, src.get("fps") or 0,
             src.get("codec"), src.get("audio_codec"), src.get("orientation"), src.get("resolution_category"),
             src.get("streamable"), int(thumb_msg.id) if thumb_msg else None,
             "COVER" if thumb else None, v.get("caption") or None, now, src.get("release_date"), src.get("metadata_source"),
             src.get("provider_scene_id"), src.get("provider_updated_at"), src.get("metadata_synced_at"),
             src.get("scene_type"), src.get("site_name"), src.get("site_url"), src.get("provider_url"),
             src.get("provider_payload"), "SENT", now, now))
        new = db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                              (str(target_id), int(sent.id))).fetchone()
        old_id = src.get("id")
        if new and old_id:
            nid = int(new["id"])
            db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id) "
                            "SELECT ?,studio_id FROM video_studios WHERE video_id=?", (nid, old_id))
            db.conn.execute("INSERT OR IGNORE INTO video_tags(video_id,tag_id) "
                            "SELECT ?,tag_id FROM video_tags WHERE video_id=?", (nid, old_id))
            db.conn.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id) "
                            "SELECT ?,performer_id FROM video_performers WHERE video_id=?", (nid, old_id))
            db.conn.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id) "
                            "SELECT ?,performer_id FROM local_video_performers WHERE video_id=?", (nid, old_id))
        # Never cache a contact sheet as a Library thumbnail: the Library and
        # player keep using the single representative frame. Telegram's
        # adjacent document and video_cover carry the full 36-frame grid.
        if playback_snapshot:
            db.conn.execute(
                "INSERT OR REPLACE INTO thumbnail_cache(source_channel,video_message_id,thumbnail_message_id,mime,"
                "width,height,data,source,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (str(target_id), int(sent.id), int(thumb_msg.id) if thumb_msg else None, "image/jpeg",
                 0, 0, bytes(playback_snapshot), "playback-snapshot", now))
        # A destination that already exists in Telegram may not yet have any
        # published Library media. Bump its channel timestamp so the channel
        # projection refreshes along with the video/resource-state triggers.
        db.conn.execute(
            "UPDATE channels SET updated_at=? WHERE telegram_id=?",
            (now, str(target_id)),
        )
        db.conn.commit()


def _record_verified_send_copy(db, source_channel, source_message_id, destination_channel,
                               destination_video_message_id, destination_thumbnail_message_id,
                               video_fields, caption):
    """Publish a verified Telegram-to-Telegram copy using the shared readiness ledger."""
    source = dict((video_fields or {}).get("row") or {})
    from .delivery_ledger import record_verified_delivery

    identity_key = str(
        source.get("oshash") or source.get("identity_key") or source.get("uploaded_oshash") or ""
    ).strip()
    sent_file = (video_fields or {}).get("file") or None
    return record_verified_delivery(
        db,
        destination_channel=str(destination_channel),
        destination_video_message_id=int(destination_video_message_id),
        destination_thumbnail_message_id=int(destination_thumbnail_message_id or 0) or None,
        source_channel=str(source_channel),
        source_message_id=int(source_message_id),
        source_media_id=int(source.get("id") or 0) or None,
        identity_key=identity_key,
        filename=str((video_fields or {}).get("filename") or source.get("filename") or ""),
        file_size=int(source.get("size") or getattr(sent_file, "size", 0) or 0),
        duration=float((video_fields or {}).get("duration") or source.get("duration") or 0),
        width=int(source.get("width") or 0),
        height=int(source.get("height") or (video_fields or {}).get("height") or 0),
        fps=float(source.get("fps") or 0),
        bitrate=int(source.get("bitrate") or 0),
        codec=str(source.get("codec") or ""),
        audio_codec=str(source.get("audio_codec") or ""),
        caption=str(caption or ""),
        action="SEND",
        file_path=None,
    )


async def reconcile_existing_send_copy(db, client, source_channel, source_message_id, target_channel):
    """Repair Library readiness for an already-sent copy without sending it again."""
    source_channel, source_message_id = str(source_channel), int(source_message_id)
    target_channel = int(target_channel)
    with db.lock:
        sent_row = db.conn.execute(
            "SELECT sent_message_id,thumb_message_id FROM sent_log "
            "WHERE source_channel=? AND message_id=? AND target_channel=? "
            "ORDER BY sent_at DESC LIMIT 1",
            (source_channel, source_message_id, str(target_channel)),
        ).fetchone()
        if not sent_row:
            return None
        destination_video_message_id = int(sent_row["sent_message_id"] or 0)
        destination_thumbnail_message_id = int(sent_row["thumb_message_id"] or 0)
        if destination_video_message_id <= 0:
            return None
        try:
            ready = db.conn.execute(
                "SELECT 1 FROM archive_x_library_media "
                "WHERE source_channel=? AND source_message_id=? LIMIT 1",
                (str(target_channel), destination_video_message_id),
            ).fetchone()
        except Exception:
            ready = None
        if ready:
            return {"library_ready": True, "already_ready": True,
                    "library_media_id": None, "filename": ""}

    try:
        target_entity = await client.get_input_entity(target_channel)
        destination_message = await client.get_messages(
            target_entity, ids=destination_video_message_id,
        )
    except Exception:
        return None
    if not destination_message or not getattr(destination_message, "document", None):
        return None

    fields = await asyncio.to_thread(
        _video_fields, db, source_channel, source_message_id, None,
    )
    source = dict(fields.get("row") or {})
    if not source:
        return None
    with db.lock:
        destination_row = db.conn.execute(
            "SELECT caption FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
            (str(target_channel), destination_video_message_id),
        ).fetchone()
    caption = str((destination_row["caption"] if destination_row else "") or "")
    if not caption:
        caption = str(getattr(destination_message, "message", "") or "")
    result = await asyncio.to_thread(
        _record_verified_send_copy, db,
        source_channel, source_message_id, target_channel, destination_video_message_id,
        destination_thumbnail_message_id, fields, caption,
    )
    return {
        **result,
        "library_ready": str(result.get("snapshot_state") or "").upper() == "READY",
        "already_ready": False,
    }
