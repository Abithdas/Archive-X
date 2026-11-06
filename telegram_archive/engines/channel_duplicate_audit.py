"""Read-only duplicate-video identity helpers for per-channel maintenance.

Only strong byte/document identities are considered deletable duplicate groups:
Telegram document ID or the standard 16-hex OSHASH (size + first/last 64 KiB).
Filename/title resemblance alone is never enough to nominate a file for deletion.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

_HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def valid_oshash(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if _HASH_RE.fullmatch(value) else ""


def row_document_id(row: dict[str, Any]) -> str:
    value = str(row.get("document_id") or "").strip()
    if not value:
        key = str(row.get("registry_key") or "")
        if key.startswith("doc:"):
            value = key[4:]
    return value if value and value != "0" else ""


def row_oshash(row: dict[str, Any]) -> str:
    return valid_oshash(row.get("registry_oshash")) or valid_oshash(row.get("uploaded_oshash")) or valid_oshash(row.get("oshash"))


def load_indexed_video_rows(
    db,
    channel_id: str | int | None = None,
    *,
    ensure_registry: bool = True,
    full_record: bool = True,
) -> list[dict[str, Any]]:
    """Load active indexed video rows and their durable Telegram identities.

    Supplying a channel keeps the historical per-channel audit behavior. With
    no channel, rows from every saved channel are returned for a local,
    cross-channel duplicate review.
    """
    from ..core.telegram_registry import ensure_telegram_registry

    if ensure_registry:
        ensure_telegram_registry(db)
    channel_filter = ""
    params: tuple[str, ...] = ()
    if channel_id is not None:
        channel_filter = "AND CAST(m.source_channel AS TEXT)=? "
        params = (str(channel_id),)
    columns = (
        "m.*"
        if full_record
        else (
            "m.id,m.source_channel,m.source_message_id,m.title,m.filename,m.size,m.duration,"
            "m.width,m.height,m.upload_date,m.thumbnail_message_id,m.oshash,m.uploaded_oshash"
        )
    )
    with db.reading() as conn:
        rows = conn.execute(
            f"SELECT {columns}, c.title AS joined_channel_title, l.registry_key, "
            "l.thumbnail_message_id AS registry_thumbnail_message_id, "
            "r.document_id, r.oshash AS registry_oshash "
            "FROM media m "
            "LEFT JOIN channels c ON CAST(c.telegram_id AS TEXT)=CAST(m.source_channel AS TEXT) "
            "LEFT JOIN telegram_document_locations l ON l.channel_id=CAST(m.source_channel AS TEXT) "
            "AND l.message_id=CAST(m.source_message_id AS INTEGER) "
            "LEFT JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
            "WHERE CAST(m.source_message_id AS INTEGER)>0 "
            "AND ((CAST(m.source_channel AS TEXT)<>'' AND CAST(m.source_channel AS TEXT) NOT GLOB '*[^0-9]*') "
            "OR (substr(CAST(m.source_channel AS TEXT),1,1)='-' "
            "AND substr(CAST(m.source_channel AS TEXT),2)<>'' "
            "AND substr(CAST(m.source_channel AS TEXT),2) NOT GLOB '*[^0-9]*')) "
            "AND UPPER(COALESCE(m.status,'')) NOT IN ('SOURCE_DELETED','DELETED_BY_ARCHIVE_X') "
            f"{channel_filter}"
            "ORDER BY CAST(m.source_channel AS TEXT),CAST(m.source_message_id AS INTEGER)",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def load_channel_rows(db, channel_id: str | int) -> list[dict[str, Any]]:
    """Load active, indexed video rows for one channel."""
    return load_indexed_video_rows(db, channel_id)


def missing_hash_candidate_buckets(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Return metadata-coincident buckets that merit content fingerprinting.

    The metadata tuple is only used to decide which un-hashed files to fingerprint;
    it is never used as duplicate proof.
    """
    buckets: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            size = int(row.get("size") or 0)
            duration = float(row.get("duration") or 0)
            width = int(row.get("width") or 0)
            height = int(row.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        # Identical byte-for-byte video files must share size, dimensions and duration.
        buckets[(size, round(duration, 3), width, height)].append(row)
    return [bucket for bucket in buckets.values() if len(bucket) > 1 and any(not row_oshash(row) for row in bucket)]


def build_duplicate_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union records sharing the same Telegram document ID or OSHASH."""
    if len(rows) < 2:
        return []
    parents = list(range(len(rows)))

    def find(x: int) -> int:
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parents[rb] = ra

    by_doc: dict[str, int] = {}
    by_hash: dict[tuple[int, str], int] = {}
    for index, row in enumerate(rows):
        doc_id = row_document_id(row)
        fingerprint = row_oshash(row)
        if doc_id:
            if doc_id in by_doc:
                union(index, by_doc[doc_id])
            else:
                by_doc[doc_id] = index
        if fingerprint:
            hash_key = (int(row.get("size") or 0), fingerprint)
            if hash_key in by_hash:
                union(index, by_hash[hash_key])
            else:
                by_hash[hash_key] = index

    members: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        members[find(index)].append(row)

    groups = []
    for items in members.values():
        if len(items) < 2:
            continue
        ids = {row_document_id(row) for row in items if row_document_id(row)}
        hashes = {row_oshash(row) for row in items if row_oshash(row)}
        proof = []
        if any(sum(1 for row in items if row_document_id(row) == doc_id) > 1 for doc_id in ids):
            proof.append("Same Telegram document ID")
        if any(
            sum(1 for row in items if row_oshash(row) == fingerprint and int(row.get("size") or 0) == int(item.get("size") or 0)) > 1
            for item in items for fingerprint in [row_oshash(item)] if fingerprint
        ):
            proof.append("Matching OSHASH content fingerprint")
        candidates = []
        for row in sorted(items, key=lambda r: (
            str(r.get("source_channel") or ""),
            int(r.get("source_message_id") or 0),
        )):
            thumb_id = int(row.get("thumbnail_message_id") or row.get("registry_thumbnail_message_id") or 0)
            title = str(row.get("title") or row.get("filename") or f"Video {row.get('source_message_id')}")
            channel_id = str(row.get("source_channel") or "")
            candidates.append({
                "media_id": int(row.get("id") or 0),
                "channel_id": channel_id,
                "channel_title": str(row.get("joined_channel_title") or row.get("channel_title") or "Unknown channel"),
                "message_id": int(row.get("source_message_id") or 0),
                "title": title,
                "filename": str(row.get("filename") or ""),
                "size": int(row.get("size") or 0),
                "duration": float(row.get("duration") or 0),
                "width": int(row.get("width") or 0),
                "height": int(row.get("height") or 0),
                "upload_date": str(row.get("upload_date") or ""),
                "thumbnail_message_id": thumb_id or None,
                "document_id": row_document_id(row) or None,
                "oshash": row_oshash(row) or None,
            })
        groups.append({
            "group_id": "|".join(f"{c['channel_id']}:{c['message_id']}" for c in candidates),
            "duplicate_count": len(candidates),
            "proof": proof or ["Connected by exact document/hash identity"],
            "fingerprint": next(iter(hashes), ""),
            "candidates": candidates,
        })
    groups.sort(key=lambda g: (
        -int(g["duplicate_count"]),
        min((str(c["channel_id"]), int(c["message_id"])) for c in g["candidates"]),
    ))
    return groups


def audit_all_indexed_duplicates(db) -> dict[str, Any]:
    """Build a read-only cross-channel report from already indexed identities."""
    # Database startup creates the registry tables. Avoid its legacy bootstrap
    # migration here so this review cannot mutate local or Telegram records.
    rows = load_indexed_video_rows(db, ensure_registry=False, full_record=False)
    groups = build_duplicate_groups(rows)
    channel_ids = sorted({
        str(row.get("source_channel") or "").strip()
        for row in rows
        if str(row.get("source_channel") or "").strip()
    })
    with db.reading() as conn:
        scan_rows = conn.execute(
            "SELECT channel_id,last_scan_at,last_scan_status FROM telegram_channel_registry_state"
        ).fetchall()
    scan_state = {
        str(row["channel_id"] or "").strip(): {
            "last_scan_at": str(row["last_scan_at"] or ""),
            "last_scan_status": str(row["last_scan_status"] or "").strip().upper(),
        }
        for row in scan_rows
    }
    complete_channel_count = sum(
        1 for channel_id in channel_ids
        if scan_state.get(channel_id, {}).get("last_scan_status") == "COMPLETE"
    )
    incomplete_channel_count = max(0, len(channel_ids) - complete_channel_count)
    last_scan_at = max(
        (scan_state.get(channel_id, {}).get("last_scan_at", "") for channel_id in channel_ids),
        default="",
    )
    if not rows:
        warning = "No active indexed video records are available. Scan a channel first."
    elif incomplete_channel_count:
        warning = (
            f"Only saved indexed videos are included. {incomplete_channel_count} of "
            f"{len(channel_ids)} indexed channel(s) do not have a recorded complete scan; "
            "newer or unindexed posts may be missing."
        )
    else:
        warning = "This local review uses saved identities only; no Telegram messages were changed."
    return {
        "ok": True,
        "scope": "all_indexed",
        "channel_title": "All indexed videos",
        "indexed_video_count": len(rows),
        "indexed_channel_count": len(channel_ids),
        "complete_channel_count": complete_channel_count,
        "inventory_complete": bool(channel_ids) and incomplete_channel_count == 0,
        "last_scan_at": last_scan_at,
        "hashes_computed": 0,
        "duplicate_group_count": len(groups),
        "duplicate_video_count": sum(int(group["duplicate_count"]) for group in groups),
        "groups": groups,
        "warning": warning,
        "fingerprint_warnings": [],
        "fingerprint_warnings_truncated": False,
    }


def save_oshash(db, *, channel_id: str | int, message_id: int, media_id: int, fingerprint: str) -> None:
    """Cache a computed OSHASH on the media and Telegram registry rows."""
    fingerprint = valid_oshash(fingerprint)
    if not fingerprint:
        raise ValueError("invalid OSHASH fingerprint")
    from .. import config
    from ..core.telegram_registry import ensure_telegram_registry

    ensure_telegram_registry(db)
    now = config.now_iso()
    with db.lock:
        db.conn.execute(
            "UPDATE media SET oshash=CASE WHEN COALESCE(oshash,'')='' THEN ? ELSE oshash END, updated_at=? WHERE id=?",
            (fingerprint, now, int(media_id)),
        )
        db.conn.execute(
            "UPDATE telegram_document_registry SET oshash=CASE WHEN COALESCE(oshash,'')='' THEN ? ELSE oshash END, updated_at=? "
            "WHERE registry_key=(SELECT registry_key FROM telegram_document_locations WHERE channel_id=? AND message_id=?)",
            (fingerprint, now, str(channel_id), int(message_id)),
        )
        db.conn.commit()


def rows_share_duplicate_identity(a: dict[str, Any], b: dict[str, Any]) -> bool:
    doc_a, doc_b = row_document_id(a), row_document_id(b)
    if doc_a and doc_b and doc_a == doc_b:
        return True
    hash_a, hash_b = row_oshash(a), row_oshash(b)
    try:
        same_size = int(a.get("size") or 0) == int(b.get("size") or 0)
    except (TypeError, ValueError):
        same_size = False
    return bool(hash_a and hash_b and hash_a == hash_b and same_size)
