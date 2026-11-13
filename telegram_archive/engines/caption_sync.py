"""Synchronize edited Archive X metadata into related Telegram video captions.

The local catalogue remains authoritative. The service edits existing Telegram
messages in place, including scanned source-channel posts, Send-to-Telegram copies,
and verified Content Automation / Media Manager deliveries. A Telegram edit
failure never rolls back local metadata; it is recorded for diagnosis and retry.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Persistence helpers
#   - Telegram target resolution
#   - Public sync operations
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import html
import re
from typing import Any

from .. import config


SYNC_TABLE = "telegram_caption_sync"




# ==============================================================================
# SECTION: Persistence helpers
# Sync-state table and result recording.
# ==============================================================================

def _ensure_table(db) -> None:
    with db.lock:
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS telegram_caption_sync ("
            "media_id INTEGER PRIMARY KEY, source_channel TEXT NOT NULL, "
            "source_message_id INTEGER NOT NULL, status TEXT NOT NULL, "
            "caption_text TEXT, last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0, "
            "last_attempt_at TEXT NOT NULL, synced_at TEXT)"
        )
        db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telegram_caption_sync_status "
            "ON telegram_caption_sync(status, last_attempt_at)"
        )
        db.conn.commit()


def _table_exists(db, name: str) -> bool:
    with db.lock:
        return bool(db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (str(name),)
        ).fetchone())


def _record(db, media_id: int, channel_id: str, message_id: int, status: str,
            *, caption_text: str = "", error: str = "", success: bool = False) -> None:
    _ensure_table(db)
    now = config.now_iso()
    with db.lock:
        db.conn.execute(
            "INSERT INTO telegram_caption_sync(media_id,source_channel,source_message_id,status,"
            "caption_text,last_error,attempts,last_attempt_at,synced_at) VALUES(?,?,?,?,?,?,1,?,?) "
            "ON CONFLICT(media_id) DO UPDATE SET source_channel=excluded.source_channel, "
            "source_message_id=excluded.source_message_id,status=excluded.status, "
            "caption_text=excluded.caption_text,last_error=excluded.last_error, "
            "attempts=telegram_caption_sync.attempts+1,last_attempt_at=excluded.last_attempt_at, "
            "synced_at=CASE WHEN excluded.status IN ('synced','unchanged','partial') "
            "THEN excluded.synced_at ELSE telegram_caption_sync.synced_at END",
            (int(media_id), str(channel_id), int(message_id), str(status),
             str(caption_text or ""), str(error or "")[:3000], now, now if success else None),
        )
        db.conn.commit()


def _result(media_id: int, status: str, detail: str, *, ok: bool,
            channel_id: str = "", message_id: int = 0, targets: list[dict] | None = None,
            synced_count: int = 0, failed_count: int = 0) -> dict[str, Any]:
    return {
        "ok": bool(ok), "media_id": int(media_id), "status": str(status),
        "detail": str(detail), "channel_id": str(channel_id or ""),
        "message_id": int(message_id or 0), "targets": list(targets or []),
        "synced_count": int(synced_count), "failed_count": int(failed_count),
    }




# ==============================================================================
# SECTION: Telegram target resolution
# Channel aliases, linked messages and caption filtering.
# ==============================================================================

def _channel_aliases(value: Any) -> set[str]:
    """Return raw and Telethon-marked representations of a channel ID."""
    text = str(value or "").strip()
    try:
        number = int(text)
    except (TypeError, ValueError):
        return {text} if text else set()
    if number == 0:
        return {"0"}
    aliases = {str(number)}
    if number < -1_000_000_000_000:
        raw = abs(number) - 1_000_000_000_000
        if raw > 0:
            aliases.add(str(raw))
            aliases.add(str(-1_000_000_000_000 - raw))
    elif number > 0:
        aliases.add(str(-1_000_000_000_000 - number))
    return aliases


def _telegram_channel_argument(value: Any) -> int:
    """Return a marked Telegram channel ID when a ledger stored the raw ID."""
    number = int(value)
    if number > 0:
        return -1_000_000_000_000 - number
    return number


def _catalogue_message_deleted(db, channel: str, message_id: int) -> bool:
    aliases = _channel_aliases(channel)
    statuses = []
    for alias in aliases:
        with db.lock:
            rows = db.conn.execute(
                "SELECT status FROM media WHERE source_channel=? AND source_message_id=?",
                (alias, int(message_id)),
            ).fetchall()
        statuses.extend(str(row["status"] or "").upper() for row in rows)
    return bool(statuses and all(value == "SOURCE_DELETED" for value in statuses))


def _linked_message_targets(db, source_channel: str, source_message_id: int,
                            identity_keys: tuple[str, ...] = ()) -> list[tuple[str, int]]:
    """Find a video's source post and all verified/direct copies linked to it."""
    found: set[tuple[str, int]] = set()
    queue = [(str(source_channel or "").strip(), int(source_message_id or 0))]
    visited: set[tuple[str, int]] = set()
    has_sent_log = _table_exists(db, "sent_log")
    has_transfers = _table_exists(db, "transfer_locations")
    has_automation_fingerprints = _table_exists(db, "automation_delivery_fingerprints")

    # Local Upload and Content Automation rows may identify the source by a
    # stable identity/hash rather than a Telegram source message ID. Verified
    # transfer rows preserve that link, so edited metadata reaches the output.
    if has_transfers:
        for identity in dict.fromkeys(str(x or "").strip() for x in identity_keys if str(x or "").strip()):
            with db.lock:
                rows = db.conn.execute(
                    "SELECT source_channel,source_message_id,destination_channel,destination_message_id "
                    "FROM transfer_locations WHERE status='VERIFIED' AND identity_key=?",
                    (identity,),
                ).fetchall()
            for row in rows:
                queue.extend([
                    (str(row["source_channel"] or ""), int(row["source_message_id"] or 0)),
                    (str(row["destination_channel"] or ""), int(row["destination_message_id"] or 0)),
                ])
    if has_automation_fingerprints:
        for identity in dict.fromkeys(str(x or "").strip() for x in identity_keys if str(x or "").strip()):
            with db.lock:
                rows = db.conn.execute(
                    "SELECT destination_channel,destination_video_id FROM automation_delivery_fingerprints WHERE raw_oshash=?",
                    (identity,),
                ).fetchall()
            for row in rows:
                queue.append((str(row["destination_channel"] or ""), int(row["destination_video_id"] or 0)))

    while queue and len(visited) < 128:
        channel, message_id = queue.pop(0)
        key = (channel, int(message_id or 0))
        if key in visited or not channel or key[1] <= 0:
            continue
        visited.add(key)
        aliases = _channel_aliases(channel)
        try:
            int(channel)
            is_telegram_channel = channel.casefold() not in {"local", "content_automation"}
        except (TypeError, ValueError):
            is_telegram_channel = False
        if is_telegram_channel and not _catalogue_message_deleted(db, channel, key[1]):
            found.add(key)

        if has_sent_log:
            for alias in aliases:
                with db.lock:
                    rows = db.conn.execute(
                        "SELECT source_channel,message_id,target_channel,sent_message_id "
                        "FROM sent_log WHERE sent_message_id IS NOT NULL AND "
                        "((source_channel=? AND message_id=?) OR (target_channel=? AND sent_message_id=?))",
                        (alias, key[1], alias, key[1]),
                    ).fetchall()
                for row in rows:
                    for next_key in (
                        (str(row["source_channel"] or ""), int(row["message_id"] or 0)),
                        (str(row["target_channel"] or ""), int(row["sent_message_id"] or 0)),
                    ):
                        if next_key not in visited:
                            queue.append(next_key)

        if has_transfers:
            for alias in aliases:
                with db.lock:
                    rows = db.conn.execute(
                        "SELECT source_channel,source_message_id,destination_channel,destination_message_id "
                        "FROM transfer_locations WHERE status='VERIFIED' AND "
                        "((source_channel=? AND source_message_id=?) OR "
                        "(destination_channel=? AND destination_message_id=?))",
                        (alias, key[1], alias, key[1]),
                    ).fetchall()
                for row in rows:
                    for next_key in (
                        (str(row["source_channel"] or ""), int(row["source_message_id"] or 0)),
                        (str(row["destination_channel"] or ""), int(row["destination_message_id"] or 0)),
                    ):
                        if next_key not in visited:
                            queue.append(next_key)

    # Keep deterministic ordering and never send an edit to synthetic local IDs.
    return sorted(found, key=lambda x: (x[0], x[1]))


def _plain_caption(caption: str) -> str:
    # Values are escaped before insertion into the template. Strip only the
    # template's formatting tags, then restore escaped title/studio characters.
    return html.unescape(re.sub(r"<[^>]*>", "", str(caption or "")))


def _is_video_message(message) -> bool:
    from .media_utils import is_video_message
    return bool(is_video_message(message))

def _permission_denied(exc: Exception) -> bool:
    value = f"{type(exc).__name__}: {exc}".casefold()
    return any(token in value for token in (
        "chat_admin_required", "chatadminrequirederror", "message_author_required",
        "messageauthorrequirederror", "not enough rights", "admin rights",
        "can't be edited", "cannot be edited", "can't edit this message",
        "cannot edit this message", "edit time expired",
    ))




# ==============================================================================
# SECTION: Public sync operations
# Catalogue and verified-delivery caption sync.
# ==============================================================================

async def sync_catalogue_caption(db, telegram, media_id: int) -> dict[str, Any]:
    """Render from saved metadata and update all linked Telegram video messages.

    Scanned source posts and their linked sent/verified copies are updated in
    place. Content Automation catalogue rows use their transfer ledger to reach
    the published Telegram post even when the local source itself is not a
    Telegram message. No video media is re-uploaded.
    """
    _ensure_table(db)
    with db.lock:
        row = db.conn.execute(
            "SELECT id,source_channel,source_message_id,status,identity_key,oshash FROM media WHERE id=?",
            (int(media_id),),
        ).fetchone()
    if not row:
        return _result(media_id, "not_found", "Video is not in the local catalogue.", ok=False)

    source_channel = str(row["source_channel"] or "").strip()
    try:
        source_message_id = int(row["source_message_id"] or 0)
    except (TypeError, ValueError):
        source_message_id = 0
    identity_keys = tuple(
        str(value or "").strip() for value in (row["identity_key"], row["oshash"])
        if str(value or "").strip()
    )
    # Some local-upload rows do not have a Telegram-style source message ID.
    # They can still be resolved safely through the verified delivery ledger
    # using their stable identity key / OSHASH.
    if source_message_id <= 0 and not identity_keys:
        detail = "This catalogue item has neither a source message ID nor a delivery identity."
        _record(db, int(media_id), source_channel, 0, "unavailable", error=detail)
        return _result(media_id, "unavailable", detail, ok=False, channel_id=source_channel)
    targets = _linked_message_targets(db, source_channel, source_message_id, identity_keys)
    if not targets:
        detail = "No existing Telegram video message is linked to this catalogue item."
        _record(db, int(media_id), source_channel, source_message_id, "unavailable", error=detail)
        return _result(media_id, "unavailable", detail, ok=False,
                       channel_id=source_channel, message_id=source_message_id)
    try:
        from ..engines.telegram_sender import _video_fields, render_caption, settings as sender_settings
        values = _video_fields(db, source_channel, source_message_id, None)
        template = str(sender_settings(db).get("template") or "")
        caption = render_caption(template, values, 1024)
        plain_caption = _plain_caption(caption)
    except Exception as exc:
        raw = f"{type(exc).__name__}: {exc}"
        detail = "Could not build a Telegram caption from the saved metadata."
        _record(db, int(media_id), source_channel, source_message_id, "failed", error=raw)
        return _result(media_id, "failed", detail, ok=False, channel_id=source_channel,
                       message_id=source_message_id, targets=[{"channel_id": c, "message_id": m, "status": "failed", "detail": raw} for c, m in targets])

    # Accept either Archive X's Telegram service wrapper or a raw connected
    # Telethon client. Telegram Media Manager owns a raw client in its worker,
    # while local upload / Content Automation use the shared service wrapper.
    client = getattr(telegram, "client", None) if telegram is not None else None
    if client is None and telegram is not None and callable(getattr(telegram, "get_entity", None)):
        client = telegram
        connected_check = getattr(client, "is_connected", None)
        try:
            connected = bool(connected_check()) if callable(connected_check) else True
        except Exception:
            connected = False
    else:
        connected = bool(getattr(telegram, "connected", False)) if telegram is not None else False
    if not client or not connected:
        detail = "Telegram is not connected; retry caption sync after reconnecting."
        _record(db, int(media_id), source_channel, source_message_id, "pending", error=detail)
        return _result(media_id, "pending", detail, ok=False, channel_id=source_channel,
                       message_id=source_message_id, targets=[{"channel_id": c, "message_id": m, "status": "pending"} for c, m in targets])

    outcomes: list[dict] = []
    changed = 0
    unchanged = 0
    failures: list[tuple[str, int, str, str]] = []

    for target_channel, target_message_id in targets:
        entry = {"channel_id": target_channel, "message_id": int(target_message_id)}
        try:
            marked_channel = _telegram_channel_argument(target_channel)
            entity = await client.get_entity(marked_channel)
            message = await client.get_messages(entity, ids=int(target_message_id))
            if not message:
                entry.update(status="unavailable", detail="Telegram message not found.")
                failures.append((target_channel, target_message_id, "unavailable", "Telegram message not found"))
            elif not _is_video_message(message):
                entry.update(status="not_video", detail="Message is not a video.")
                failures.append((target_channel, target_message_id, "not_video", "Message is not a video"))
            elif str(getattr(message, "message", "") or "") == plain_caption:
                entry.update(status="unchanged", detail="Caption already matches.")
                unchanged += 1
            else:
                await client.edit_message(entity, int(target_message_id), caption, parse_mode="html")
                entry.update(status="synced", detail="Caption updated.")
                changed += 1
        except Exception as exc:
            raw = f"{type(exc).__name__}: {exc}"
            status = "permission_denied" if _permission_denied(exc) else "failed"
            detail = "This account cannot edit this scanned/delivered message." if status == "permission_denied" else f"{type(exc).__name__}: {exc}"
            entry.update(status=status, detail=detail)
            failures.append((target_channel, target_message_id, status, raw))
        outcomes.append(entry)

    failed_count = len(failures)
    success_count = changed + unchanged
    if failed_count == 0:
        status = "synced" if changed else "unchanged"
        ok = True
        detail = (f"Updated {changed} Telegram caption(s)." if changed
                  else f"All {unchanged} linked Telegram caption(s) already match the saved metadata.")
    elif success_count > 0:
        status = "partial"
        ok = False
        detail = f"Updated or verified {success_count} caption(s); {failed_count} linked message(s) could not be updated."
    elif all(item[2] == "permission_denied" for item in failures):
        status = "permission_denied"
        ok = False
        detail = "Telegram does not allow this account to edit the linked message(s). Archive X metadata remains saved locally."
    elif all(item[2] in {"unavailable", "not_video"} for item in failures):
        status = "unavailable"
        ok = False
        detail = "No editable Telegram video message was found among the linked records."
    else:
        status = "failed"
        ok = False
        detail = f"Telegram caption sync failed for {failed_count} linked message(s); Archive X metadata remains saved locally."

    error_text = "; ".join(f"{channel}/{mid} {state}: {error}" for channel, mid, state, error in failures)
    if success_count:
        with db.lock:
            db.conn.execute(
                "UPDATE media SET caption=?,updated_at=? WHERE id=?",
                (plain_caption, config.now_iso(), int(media_id)),
            )
            db.conn.commit()
    _record(db, int(media_id), source_channel, source_message_id, status,
            caption_text=plain_caption if success_count else "", error=error_text, success=bool(success_count))
    try:
        level = "INFO" if ok else "WARN"
        db.log(f"Telegram caption sync {status} for media_id={media_id}: {detail}", level)
    except Exception:
        _swallowed_note("caption_sync.sync_catalogue_caption")
        pass
    return _result(media_id, status, detail, ok=ok, channel_id=source_channel,
                   message_id=source_message_id, targets=outcomes,
                   synced_count=changed, failed_count=failed_count)


async def sync_verified_delivery_caption(db, telegram, media_id: int) -> dict[str, Any]:
    """Run the same caption reconciliation used for scanned videos after delivery.

    This is intentionally non-fatal to an already verified upload: a Telegram
    permission/RPC problem is persisted by ``sync_catalogue_caption`` and must
    not cause the transfer job to re-upload the media. ``telegram`` may be the
    service wrapper or the raw Telethon client held by Media Manager.
    """
    try:
        outcome = await sync_catalogue_caption(db, telegram, int(media_id))
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        try:
            db.log(
                f"Verified delivery caption reconciliation failed for media_id={media_id}: {detail}",
                "WARN",
            )
        except Exception:
            _swallowed_note("caption_sync.sync_verified_delivery_caption")
            pass
        return _result(int(media_id), "failed", detail, ok=False)
    try:
        db.log(
            f"Verified delivery caption reconciliation for media_id={media_id}: "
            f"{outcome.get('status')}: {outcome.get('detail')}",
            "INFO" if outcome.get("ok") else "WARN",
        )
    except Exception:
        _swallowed_note("caption_sync.sync_verified_delivery_caption")
        pass
    return outcome


def get_caption_sync_state(db, media_id: int) -> dict[str, Any] | None:
    """Return the most recent result so the UI can explain/retry failed syncs."""
    _ensure_table(db)
    with db.lock:
        row = db.conn.execute(
            "SELECT media_id,source_channel,source_message_id,status,caption_text,last_error,attempts,"
            "last_attempt_at,synced_at FROM telegram_caption_sync WHERE media_id=?",
            (int(media_id),),
        ).fetchone()
    return dict(row) if row else None
