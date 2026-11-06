"""Review of earlier filename auto-matches (V29.4.133).

Before V29.4.132 a ThePornDB filename search was applied automatically when
one performer name from the candidate appeared in the filename, whichever of
that performer's scenes it was. Those videos are recognisable: the stored
provider record says "filename search — please confirm" and the video is
MATCHED (not VERIFIED by the operator).

This module lists the ones the current rule would NOT apply automatically and
lets the operator keep each one (VERIFIED) or undo it. Undo removes the
provider-applied title/performers/studio, puts the Telegram title back and
turns the match into a pending suggestion that can still be confirmed later.
The video stays in the Library either way. Nothing changes without an
explicit action, and videos with manual mapping locks are never listed.
"""
from __future__ import annotations

import json

from .. import config

FILENAME_MARK = "filename search"


def _payload(raw) -> dict:
    try:
        data = json.loads(raw or "{}") if isinstance(raw, str) else dict(raw or {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _current_rule(filename: str, match: dict) -> tuple[str, str]:
    from .matching import _filename_provider_score, _title_overlap, classify_confidence
    score, count, matched = _filename_provider_score(filename, match)
    return classify_confidence({}, match, "tpdb filename parse", score=score, performer_count=count,
                               matched_performers=matched, title_overlap=_title_overlap(filename, match))


def candidates(db, limit: int = 500) -> list[dict]:
    """Auto-applied filename matches that the current rule would only suggest."""
    with db.reading() as conn:
        rows = conn.execute(
            "SELECT m.id, m.source_channel, m.source_message_id, m.filename, m.title, m.site_name, m.provider_payload "
            "FROM media m WHERE m.identification_state='MATCHED' "
            "AND m.provider_payload LIKE ? "
            "AND COALESCE(m.status,'')<>'SOURCE_DELETED' "
            "AND NOT EXISTS (SELECT 1 FROM metadata_mapping_locks l WHERE l.video_id=m.id AND l.locked=1) "
            "ORDER BY m.id DESC",
            (f"%{FILENAME_MARK}%",),
        ).fetchall()
        out = []
        for r in rows:
            match = _payload(r["provider_payload"])
            if FILENAME_MARK not in str(match.get("confidence") or ""):
                continue
            filename = str(r["filename"] or "")
            confidence, reason = _current_rule(filename, match)
            if confidence == "HIGH":
                continue                    # still a safe automatic match
            performers = [x["name"] for x in conn.execute(
                "SELECT p.name FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=? ORDER BY p.name",
                (int(r["id"]),)).fetchall()]
            out.append({
                "video_id": int(r["id"]), "channel_id": str(r["source_channel"]), "message_id": int(r["source_message_id"]),
                "filename": filename, "applied_title": str(r["title"] or ""), "applied_studio": str(r["site_name"] or ""),
                "applied_performers": performers, "provider": str(match.get("source") or ""),
                "current_rule": confidence, "reason": reason,
            })
            if len(out) >= int(limit):
                break
    return out


def keep(db, video_id: int) -> bool:
    """The operator checked it: the match is correct."""
    with db.lock:
        cur = db.conn.execute("UPDATE media SET identification_state='VERIFIED', updated_at=? "
                              "WHERE id=? AND identification_state='MATCHED'", (config.now_iso(), int(video_id)))
        db.conn.commit()
        return cur.rowcount > 0


def undo(db, video_id: int) -> bool:
    """Take the automatic match off the video and keep it as a suggestion."""
    from .suggestions import save_suggestion
    from ..core.resource_registry import ensure_unknown_entity_links
    with db.lock:
        row = db.conn.execute(
            "SELECT id, source_channel, source_message_id, filename, provider_payload FROM media "
            "WHERE id=? AND identification_state='MATCHED'", (int(video_id),)).fetchone()
        if not row:
            return False
        if db.conn.execute("SELECT 1 FROM metadata_mapping_locks WHERE video_id=? AND locked=1", (int(video_id),)).fetchone():
            return False                    # an operator mapping is never undone here
        match = _payload(row["provider_payload"])
        now = config.now_iso()
        filename = str(row["filename"] or "")
        db.conn.execute("DELETE FROM video_performers WHERE video_id=?", (int(video_id),))
        db.conn.execute("DELETE FROM video_studios WHERE video_id=?", (int(video_id),))
        db.conn.execute("DELETE FROM provider_field_sources WHERE media_id=?", (int(video_id),))
        tags = [str(t).strip() for t in (match.get("tags") or []) if str(t or "").strip()]
        if tags:     # only the tags that came with this provider record
            db.conn.execute(
                f"DELETE FROM video_tags WHERE video_id=? AND tag_id IN (SELECT id FROM tags WHERE name IN ({','.join('?' for _ in tags)}))",
                (int(video_id), *tags))
        db.conn.execute(
            "UPDATE media SET title=?, site_name=NULL, site_url=NULL, provider_url=NULL, provider_scene_id=NULL, "
            "release_date=NULL, metadata_source='suggested', identification_state='PARTIAL', updated_at=? WHERE id=?",
            (filename or None, now, int(video_id)))
        db.conn.commit()
    save_suggestion(db, int(row["source_channel"]), int(row["source_message_id"]), match, "MEDIUM",
                    "earlier automatic filename match, sent back for review", score=None)
    ensure_unknown_entity_links(db, [int(video_id)])
    return True


def bulk_action(db, video_ids, action: str) -> dict:
    """Apply an explicit, bounded keep/undo action and report skipped IDs.

    Each undo uses the existing canonical suggestion/mapping cleanup path. A
    failed or already-reviewed item does not block the rest of a batch, and the
    response names exactly which IDs changed so the UI can refresh safely.
    """
    action = str(action or "").strip().lower()
    if action not in {"keep", "undo"}:
        raise ValueError("action must be keep or undo")
    ids = list(dict.fromkeys(int(value) for value in video_ids if int(value) > 0))
    if not ids:
        raise ValueError("at least one positive video ID is required")
    if len(ids) > 100:
        raise ValueError("at most 100 videos may be reviewed at once")
    processed, skipped, errors = [], [], []
    worker = keep if action == "keep" else undo
    for video_id in ids:
        try:
            if worker(db, video_id):
                processed.append(video_id)
            else:
                skipped.append(video_id)
        except Exception as exc:
            errors.append({"video_id": video_id, "error": f"{type(exc).__name__}: {exc}"[:240]})
    return {
        "action": action,
        "processed": len(processed),
        "skipped": len(skipped),
        "failed": len(errors),
        "processed_ids": processed,
        "skipped_ids": skipped,
        "errors": errors,
    }
