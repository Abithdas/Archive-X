"""Explicit registration for media editing, metadata review, and search routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Quick edit and captions
#       - Suggestions and Scene Match Review
#       - Suggestion decisions
#       - Auto-match review
#       - Suggested videos and search
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request

from .performer_catalogue import list_performers_sync


def register_media_routes(
    router: APIRouter,
    *,
    db,
    config,
    jobs,
    require_auth,
    SCENE_MATCH_FIELD_STATUS_CTE,
    _queue_catalogue_caption_sync,
    _scene_match_review_unresolved_catalog,
    _sync_catalogue_caption,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Quick edit and captions
    # Quick edit and caption sync.
    # ------------------------------------------------------------------
    """Register media editing, review and search routes."""
    @router.post("/api/media/quick-edit")
    async def media_quick_edit(request: Request):
        """Atomically apply title, full performer-list and studio edits, then sync captions."""
        require_auth(request)
        body = await request.json()
        channel_id = str(body.get("channel_id") or "").strip()
        try:
            message_id = int(body.get("message_id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        if not channel_id or not message_id:
            raise HTTPException(400, "channel_id and message_id are required")
        with db.lock:
            media = db.conn.execute(
                "SELECT * FROM media WHERE source_channel=? AND source_message_id=?",
                (channel_id, message_id),
            ).fetchone()
            if not media:
                raise HTTPException(404, "Video not found")
            media_id = int(media["id"])
            now = config.now_iso()
            title = str(body.get("title") or "").strip()
            legacy_performer = body.get("performer") if isinstance(body.get("performer"), dict) else None
            replace_performers = isinstance(body.get("performers"), list)
            incoming_people = body.get("performers") if replace_performers else ([legacy_performer] if legacy_performer else None)
            studio = body.get("studio") if isinstance(body.get("studio"), dict) else None
            changes = []
            db.conn.execute("SAVEPOINT archive_x_quick_edit")
            try:
                resolved_people = []
                if incoming_people is not None:
                    from ..engines.entity_identity import assess_performer_identity, upsert_performer, canonical_key
                    seen_keys, seen_ids = set(), set()
                    for raw in incoming_people:
                        if isinstance(raw, str):
                            person = {"name": raw}
                        elif isinstance(raw, dict):
                            person = raw
                        else:
                            continue
                        name = str(person.get("name") or "").strip()
                        key = canonical_key(name)
                        if not name or not key or key in seen_keys:
                            continue
                        provider = str(person.get("provider") or "").strip()
                        provider_id = person.get("provider_id") or None
                        supplied_local_id = person.get("id")
                        local_row = db.conn.execute("SELECT id,name FROM performers WHERE id=?", (int(supplied_local_id),)).fetchone() if str(supplied_local_id or "").isdigit() else None
                        if local_row and not provider_id:
                            performer_id = int(local_row["id"])
                            resolved_name = str(local_row["name"] or name)
                        else:
                            assessment = assess_performer_identity(db, name, provider=provider, provider_id=provider_id)
                            if assessment.get("status") == "ambiguous":
                                names = ", ".join(str(x.get("name") or "") for x in assessment.get("candidates") or [])
                                raise HTTPException(409, f'Performer identity for "{name}" conflicts with existing records{": " + names if names else ""}. Resolve the identity in Duplicate Performer Audit before saving; no performer links were changed.')
                            if assessment.get("status") == "existing" and assessment.get("id"):
                                performer_id = int(assessment["id"])
                                # Upsert enriches the authoritative identity with provider IDs and image/profile without creating a second row.
                                enriched_id = upsert_performer(db, name, provider=provider, provider_id=provider_id,
                                                                image=person.get("image"), profile=person.get("profile"))
                                performer_id = int(enriched_id or performer_id)
                            elif assessment.get("status") == "new":
                                performer_id = int(upsert_performer(db, name, provider=provider, provider_id=provider_id,
                                                                   image=person.get("image"), profile=person.get("profile")) or 0)
                            else:
                                raise HTTPException(400, f'Invalid performer entry: "{name}"')
                            row = db.conn.execute("SELECT id,name FROM performers WHERE id=?", (performer_id,)).fetchone() if performer_id else None
                            if not row:
                                raise HTTPException(409, f'Could not resolve performer identity for "{name}".')
                            resolved_name = str(row["name"] or name)
                        if performer_id > 0 and performer_id not in seen_ids:
                            seen_keys.add(key)
                            seen_ids.add(performer_id)
                            resolved_people.append((performer_id, resolved_name))

                if title and title != str(media["title"] or "").strip():
                    db.conn.execute("UPDATE media SET title=?, updated_at=? WHERE id=?", (title, now, media_id))
                    db.conn.execute("INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",
                                    (media_id, "title", str(media["title"] or ""), title, "manual", now))
                    db.conn.execute("INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,reason,updated_at) VALUES(?,?,?,?,?)",
                                    (media_id, "title", 1, "manual quick edit", now))
                    changes.append("title")

                if incoming_people is not None:
                    old_rows = db.conn.execute(
                        "SELECT DISTINCT p.id,p.name FROM performers p JOIN ("
                        "SELECT performer_id FROM video_performers WHERE video_id=? UNION "
                        "SELECT performer_id FROM local_video_performers WHERE video_id=?"
                        ") linked ON linked.performer_id=p.id ORDER BY p.name COLLATE NOCASE", (media_id, media_id)
                    ).fetchall()
                    old_names = [str(row["name"] or "") for row in old_rows]
                    new_names = [name for _pid, name in resolved_people]
                    db.conn.execute("DELETE FROM video_performers WHERE video_id=?", (media_id,))
                    db.conn.execute("DELETE FROM local_video_performers WHERE video_id=?", (media_id,))
                    for performer_id, _name in resolved_people:
                        db.conn.execute("INSERT OR IGNORE INTO local_video_performers(video_id,performer_id,created_at) VALUES(?,?,?)",
                                         (media_id, performer_id, now))
                    if old_names != new_names:
                        db.conn.execute("INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",
                                        (media_id, "performer", ", ".join(old_names), ", ".join(new_names), "manual", now))
                    db.conn.execute("INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,reason,updated_at) VALUES(?,?,?,?,?)",
                                    (media_id, "performer", 1, "manual quick edit", now))
                    changes.append("performer")

                if "studio" in body:
                    old_site = str(media["site_name"] or "")
                    old_studio_ids = [int(row[0]) for row in db.conn.execute(
                        "SELECT studio_id FROM video_studios WHERE video_id=? ORDER BY studio_id", (media_id,)
                    ).fetchall()]
                    if studio and str(studio.get("name") or "").strip():
                        name = str(studio.get("name") or "").strip()
                        sid = studio.get("id")
                        row = db.conn.execute("SELECT * FROM studios WHERE id=?", (int(sid),)).fetchone() if str(sid or "").isdigit() else None
                        if row is None:
                            from ..engines.entity_identity import upsert_studio
                            sid = upsert_studio(db, name, provider=studio.get("provider"), provider_id=studio.get("provider_id"), image=studio.get("image"))
                            row = db.conn.execute("SELECT * FROM studios WHERE id=?", (sid,)).fetchone()
                        if row is None:
                            raise HTTPException(409, f'Could not resolve studio identity for "{name}".')
                        db.conn.execute("DELETE FROM video_studios WHERE video_id=?", (media_id,))
                        db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,?)", (media_id, int(row["id"]), 1.0))
                        db.conn.execute("UPDATE media SET site_name=?, updated_at=? WHERE id=?", (name, now, media_id))
                        if old_site != name or old_studio_ids != [int(row["id"])]:
                            db.conn.execute("INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",
                                            (media_id, "site", old_site, name, "manual", now))
                            changes.append("studio")
                    else:
                        # A null studio is an explicit clear from Quick Edit, not an omitted field.
                        if old_site or old_studio_ids:
                            db.conn.execute("DELETE FROM video_studios WHERE video_id=?", (media_id,))
                            db.conn.execute("UPDATE media SET site_name='', updated_at=? WHERE id=?", (now, media_id))
                            db.conn.execute("INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",
                                            (media_id, "site", old_site, "", "manual", now))
                            changes.append("studio")
                    if "studio" in changes:
                        db.conn.execute("INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,reason,updated_at) VALUES(?,?,?,?,?)",
                                        (media_id, "site", 1, "manual quick edit", now))
                db.conn.execute("RELEASE SAVEPOINT archive_x_quick_edit")
                db.conn.commit()
                updated = db.conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
            except Exception:
                try:
                    db.conn.execute("ROLLBACK TO SAVEPOINT archive_x_quick_edit")
                    db.conn.execute("RELEASE SAVEPOINT archive_x_quick_edit")
                    db.conn.commit()
                except Exception:
                    pass
                raise
        if changes and any(x in changes for x in ("performer", "studio", "title")):
            from ..core.resource_registry import mark_media_entity_complete
            mark_media_entity_complete(db, [media_id])
        caption_sync = await _sync_catalogue_caption(media_id)
        db.log(f"Video quick edit: channel={channel_id} message={message_id} fields={','.join(changes) or 'none'}")
        return {"ok": True, "changed": changes, "media": dict(updated) if updated else None,
                "performers": [name for _pid, name in locals().get("resolved_people", [])],
                "caption_sync": caption_sync}

    @router.post("/api/media/caption-sync/reconcile-deliveries")
    async def reconcile_verified_delivery_captions(request: Request):
        """Reconcile captions of already-registered destination posts without channel scans."""
        require_auth(request)
        with db.lock:
            tables = {str(row[0]) for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('transfer_locations','archive_x_deliveries','automation_delivery_fingerprints')"
            ).fetchall()}
            clauses = []
            if "transfer_locations" in tables:
                clauses.append(
                    "EXISTS (SELECT 1 FROM transfer_locations t WHERE t.status='VERIFIED' "
                    "AND t.destination_channel=m.source_channel AND t.destination_message_id=m.source_message_id)"
                )
            if "archive_x_deliveries" in tables:
                clauses.append(
                    "EXISTS (SELECT 1 FROM archive_x_deliveries d WHERE d.status='VERIFIED' "
                    "AND d.destination_channel=m.source_channel AND d.destination_video_message_id=m.source_message_id)"
                )
            if "automation_delivery_fingerprints" in tables:
                clauses.append(
                    "EXISTS (SELECT 1 FROM automation_delivery_fingerprints f "
                    "WHERE f.destination_channel=m.source_channel AND f.destination_video_id=m.source_message_id)"
                )
            if not clauses:
                return {"ok": True, "queued": 0, "message": "No verified-delivery ledger is available yet."}
            rows = db.conn.execute(
                "SELECT m.id FROM media m WHERE m.source_message_id>0 AND COALESCE(m.status,'')<>'SOURCE_DELETED' AND (" + " OR ".join(clauses) + ") "
                "ORDER BY COALESCE(m.updated_at,m.created_at,''),m.id"
            ).fetchall()
            ids = [int(row["id"] or 0) for row in rows if int(row["id"] or 0) > 0]
        ids = list(dict.fromkeys(ids))
        queued = _queue_catalogue_caption_sync(ids)
        db.log(
            f"Verified-delivery caption reconciliation queued: {queued} known Telegram video(s); no channel history scan or re-upload requested.",
            "INFO",
        )
        return {
            "ok": True,
            "queued": queued,
            "message": (f"Queued {queued} known delivered video(s) for direct message-ID caption reconciliation. "
                        "No channel scan or media re-upload will run." if queued else
                        "No registered verified deliveries need reconciliation."),
        }

    @router.post("/api/media/caption-sync")
    async def media_caption_sync(request: Request):
        """Retry synchronizing the caption of an existing catalogued Telegram video."""
        require_auth(request)
        body = await request.json()
        try:
            media_id = int(body.get("media_id") or 0)
        except (TypeError, ValueError):
            media_id = 0
        if not media_id:
            channel_id = str(body.get("channel_id") or "").strip()
            try:
                message_id = int(body.get("message_id") or 0)
            except (TypeError, ValueError):
                message_id = 0
            if not channel_id or not message_id:
                raise HTTPException(400, "media_id or channel_id and message_id are required")
            with db.lock:
                row = db.conn.execute(
                    "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                    (channel_id, message_id),
                ).fetchone()
            media_id = int(row["id"]) if row else 0
        if not media_id:
            raise HTTPException(404, "Video is not in the local catalogue")
        return await _sync_catalogue_caption(media_id)

    @router.get("/api/media/caption-sync")
    async def media_caption_sync_state(request: Request):
        """Return the most recent Telegram-caption synchronization result."""
        require_auth(request)
        try:
            media_id = int(request.query_params.get("media_id") or 0)
        except (TypeError, ValueError):
            media_id = 0
        if not media_id:
            raise HTTPException(400, "media_id is required")
        from ..engines.caption_sync import get_caption_sync_state
        return {"state": get_caption_sync_state(db, media_id)}


    # ------------------------------------------------------------------
    # SUBSECTION: Suggestions and Scene Match Review
    # Review lists and bulk decisions.
    # ------------------------------------------------------------------
    @router.get("/api/media/suggestions")
    async def media_suggestions(request: Request):
        """GET /api/media/suggestions: Media suggestions."""
        require_auth(request)
        from ..metadata.suggestions import list_for_video, candidate_display_fields
        channel_id = str(request.query_params.get("channel_id") or "").strip()
        try:
            message_id = int(request.query_params.get("message_id") or 0)
        except ValueError:
            message_id = 0
        if not channel_id or not message_id:
            raise HTTPException(400, "channel_id and message_id are required")
        def _read_media_id():
            with db.reading() as rdb:
                row = rdb.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?", (channel_id, message_id)).fetchone()
                return int(row["id"]) if row else None
        media_id = await asyncio.to_thread(_read_media_id)
        if not media_id:
            return []
        rows = await asyncio.to_thread(list_for_video, db, media_id, status="PENDING")
        out = []
        for r in rows:
            try:
                payload = json.loads(r.get("payload") or "{}")
            except Exception:
                payload = {}
            out.append({
                "id": r["id"], "provider": r["provider"], "confidence": r["confidence"],
                "score": r["score"], "reason": r["reason"], "performer_id": r["suggested_performer_id"],
                "performer_name": r["suggested_performer_name"],
                "title": payload.get("title"), "studio": payload.get("studio") or payload.get("site"),
                "image": payload.get("image") or payload.get("thumbnail"),
                **candidate_display_fields(r),
            })
        return out

    @router.get("/api/metadata/scene-match-review")
    async def scene_match_review_list(request: Request):
        """Read pending suggestions and unresolved catalogue metadata without starting work."""
        require_auth(request)
        try:
            limit = max(1, min(500, int(request.query_params.get("limit") or 250)))
        except (TypeError, ValueError):
            limit = 250
        try:
            offset = max(0, min(1_000_000, int(request.query_params.get("offset") or 0)))
        except (TypeError, ValueError):
            offset = 0
        try:
            unresolved_limit = max(1, min(250, int(request.query_params.get("unresolved_limit") or 100)))
        except (TypeError, ValueError):
            unresolved_limit = 100
        try:
            unresolved_offset = max(0, min(1_000_000, int(request.query_params.get("unresolved_offset") or 0)))
        except (TypeError, ValueError):
            unresolved_offset = 0
        unresolved_channel = str(request.query_params.get("unresolved_channel_id") or "").strip()[:128]
        unresolved_field = str(request.query_params.get("unresolved_missing_field") or "all").strip().lower()
        if unresolved_field not in {"all", "performer", "studio"}:
            unresolved_field = "all"

        def _read_review_rows():
            with db.reading() as rdb:
                rows = rdb.execute(
                    SCENE_MATCH_FIELD_STATUS_CTE + """SELECT s.id AS suggestion_id,s.provider,s.provider_record_id,
                              s.confidence,s.score,s.reason,s.payload,s.created_at,
                              m.id AS media_id,m.filename,m.title AS local_title,m.duration,
                               m.source_channel,m.source_message_id,
                               COALESCE(NULLIF(TRIM(c.title),''),'Unknown channel') AS channel_title,
                              fs.missing_performer,fs.missing_studio
                        FROM metadata_suggestions s JOIN media m ON m.id=s.video_id
                        LEFT JOIN channels c ON CAST(c.telegram_id AS TEXT)=CAST(m.source_channel AS TEXT)
                       JOIN media_field_status fs ON fs.id=m.id
                       WHERE s.status='PENDING' AND UPPER(COALESCE(m.identification_state,'')) NOT IN ('MATCHED','VERIFIED')
                       ORDER BY COALESCE(s.score,0) DESC, s.updated_at DESC, s.id DESC"""
                ).fetchall()
                unresolved_total, unresolved_rows, unresolved_channels = _scene_match_review_unresolved_catalog(
                    rdb, unresolved_limit, unresolved_offset, unresolved_channel, unresolved_field
                )
            return [dict(row) for row in rows], unresolved_total, unresolved_rows, unresolved_channels

        rows, total_unresolved, unresolved_rows, unresolved_channels = await asyncio.to_thread(_read_review_rows)

        def _filter_review_candidates(candidate_rows):
            from ..engines.entity_identity import mapped_video_entity_context
            from ..metadata.suggestions import candidate_mapping_conflicts
            mapping_contexts = {}
            visible = []
            for candidate_row in candidate_rows:
                media_id = int(candidate_row.get("media_id") or 0)
                if media_id not in mapping_contexts:
                    mapping_contexts[media_id] = mapped_video_entity_context(db, media_id)
                try:
                    candidate = json.loads(candidate_row.get("payload") or "{}")
                except Exception:
                    candidate = {}
                if not isinstance(candidate, dict):
                    candidate = {}
                if candidate_mapping_conflicts(
                    db, media_id, candidate,
                    mapping_context=mapping_contexts[media_id],
                ):
                    continue
                visible.append(candidate_row)
            return visible

        visible_rows = await asyncio.to_thread(_filter_review_candidates, rows)
        total_pending = len(visible_rows)
        rows = visible_rows[offset:offset + limit]
        items = []
        for row in rows:
            try:
                payload = json.loads(row.get("payload") or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            evidence = payload.get("_archive_match") if isinstance(payload.get("_archive_match"), dict) else {}
            score = float(row.get("score") or 0.0)
            channel_id, message_id = str(row.get("source_channel") or ""), int(row.get("source_message_id") or 0)
            items.append({
                "suggestion_id": int(row["suggestion_id"]), "media_id": int(row["media_id"]),
                "provider": row.get("provider"), "provider_record_id": row.get("provider_record_id"),
                "confidence": row.get("confidence"), "score": round(max(0.0, min(1.0, score)) * 100, 1),
                "reason": row.get("reason") or payload.get("_archive_reason") or "Candidate needs review",
                "signals": evidence.get("evidence_text") or "; ".join(evidence.get("evidence") or []) or row.get("reason") or "No strong clue agreement",
                "dimension_scores": evidence.get("dimension_scores") or {},
                "filename": row.get("filename"), "local_title": row.get("local_title"),
                "duration": float(row.get("duration") or 0), "candidate_title": payload.get("title"),
                "missing_fields": [
                    field for field, missing in (
                        ("performer", row.get("missing_performer")),
                        ("studio", row.get("missing_studio")),
                    ) if int(missing or 0)
                ],
                "performers": [str(p.get("name") or "") if isinstance(p, dict) else str(p)
                               for p in (payload.get("performer_details") or payload.get("performers") or [])],
                "studio": payload.get("studio") or payload.get("site"),
                "provider_site": payload.get("site") or payload.get("studio"),
                "provider_site_url": payload.get("site_url") or payload.get("studio_url"),
                "website_variant_conflict": bool(payload.get("_archive_website_variant_conflict")),
                "release_date": payload.get("date") or payload.get("release_date") or payload.get("production_date"),
                "provider_image": payload.get("image") or payload.get("thumbnail"),
                "provider_url": payload.get("profile") or payload.get("url"),
                "local_grid_url": f"/api/play/preview-grid/{channel_id}/{message_id}" if channel_id and message_id else None,
                "channel_id": channel_id, "channel_title": row.get("channel_title") or "Unknown channel",
                "message_id": message_id, "created_at": row.get("created_at"),
            })
        return {
            "count": total_pending, "items": items, "limit": limit, "offset": offset,
            "has_more": offset + len(items) < total_pending,
            "unresolved_count": total_unresolved, "unresolved_items": unresolved_rows,
            "unresolved_limit": unresolved_limit, "unresolved_offset": unresolved_offset,
            "unresolved_has_more": unresolved_offset + len(unresolved_rows) < total_unresolved,
            "unresolved_channels": unresolved_channels,
            "unresolved_filter_channel": unresolved_channel,
            "unresolved_filter_field": unresolved_field,
        }

    @router.post("/api/metadata/scene-match-review/approve-above")
    async def scene_match_review_approve_above(request: Request):
        """Approve pending suggestions above a score, subject to normal identity safety checks."""
        require_auth(request)
        try:
            body = await request.json()
            threshold = float(body.get("threshold", 95))
        except Exception:
            raise HTTPException(400, "threshold must be a number between 0 and 100")
        if not 0 <= threshold <= 100:
            raise HTTPException(400, "threshold must be between 0 and 100")
        def _pending_by_video():
            with db.reading() as rdb:
                return [dict(row) for row in rdb.execute(
                    """SELECT id,video_id,payload,score FROM metadata_suggestions
                       WHERE status='PENDING' ORDER BY video_id,COALESCE(score,0) DESC,id DESC"""
                ).fetchall()]
        from ..metadata.suggestions import candidate_mapping_conflicts, confirm
        from ..engines.entity_identity import performer_identity_conflicts, mapped_video_entity_context
        all_rows = await asyncio.to_thread(_pending_by_video)
        grouped = {}
        mapping_mismatches_skipped = 0
        mapping_contexts = {}
        for candidate in all_rows:
            video_id = int(candidate["video_id"])
            if video_id not in mapping_contexts:
                mapping_contexts[video_id] = await asyncio.to_thread(mapped_video_entity_context, db, video_id)
            try:
                candidate_payload = json.loads(candidate.get("payload") or "{}")
            except Exception:
                candidate_payload = {}
            if candidate_mapping_conflicts(
                db, video_id, candidate_payload if isinstance(candidate_payload, dict) else {},
                mapping_context=mapping_contexts[video_id],
            ):
                mapping_mismatches_skipped += 1
                continue
            grouped.setdefault(int(candidate["video_id"]), []).append(candidate)
        # Approve at most one candidate per video. Even a user-selected bulk
        # threshold must not resolve two near-tied provider scenes automatically.
        rows = []
        ambiguous = 0
        website_variants_skipped = 0
        for video_id, candidates in grouped.items():
            candidates.sort(key=lambda row: (float(row.get("score") or 0.0), int(row.get("id") or 0)), reverse=True)
            top = candidates[0]
            score = float(top.get("score") or 0.0)
            second = float(candidates[1].get("score") or 0.0) if len(candidates) > 1 else 0.0
            if score < threshold / 100.0:
                continue
            has_website_variant = False
            for candidate in candidates:
                try:
                    candidate_payload = json.loads(candidate.get("payload") or "{}")
                except Exception:
                    candidate_payload = {}
                if isinstance(candidate_payload, dict) and candidate_payload.get("_archive_website_variant_conflict"):
                    has_website_variant = True
                    break
            if has_website_variant:
                ambiguous += 1
                website_variants_skipped += 1
                continue
            if len(candidates) > 1 and score - second < 0.12:
                ambiguous += 1
                continue
            rows.append(top)
        approved, conflicted, failed, media_ids = 0, 0, 0, []
        for row in rows:
            try:
                payload = json.loads(row.get("payload") or "{}")
            except Exception:
                payload = {}
            mapping_context = mapping_contexts.get(int(row["video_id"]))
            if mapping_context is None:
                mapping_context = await asyncio.to_thread(mapped_video_entity_context, db, int(row["video_id"]))
            if performer_identity_conflicts(db, payload if isinstance(payload, dict) else {},
                                            ignore_names=mapping_context.get("performer_names")):
                conflicted += 1
                continue
            ok = await asyncio.to_thread(confirm, db, int(row["id"]))
            if ok:
                approved += 1
                media_ids.append(int(row["video_id"]))
            else:
                failed += 1
        caption_queued = _queue_catalogue_caption_sync(list(dict.fromkeys(media_ids))) if media_ids else 0
        db.log(f"Scene-match review bulk approval: threshold={threshold:.1f}%, eligible={len(rows)}, ambiguous={ambiguous}, website variants skipped={website_variants_skipped}, mapped-entity mismatches skipped={mapping_mismatches_skipped}, approved={approved}, performer conflicts={conflicted}, failed={failed}", "INFO", event="metadata.scene_match_review_bulk")
        return {"ok": True, "threshold": threshold, "eligible": len(rows), "ambiguous_skipped": ambiguous,
                "website_variants_skipped": website_variants_skipped,
                "mapped_entity_mismatches_skipped": mapping_mismatches_skipped,
                "approved": approved, "performer_conflicts": conflicted, "failed": failed,
                "caption_sync_queued": caption_queued}

    @router.post("/api/metadata/scene-match-review/reject-selected")
    async def scene_match_review_reject_selected(request: Request):
        """Reject only the explicitly selected pending suggestions."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "suggestion_ids must be a non-empty list")
        raw_ids = body.get("suggestion_ids") if isinstance(body, dict) else None
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(400, "Select at least one scene suggestion")
        if len(raw_ids) > 500:
            raise HTTPException(400, "Select no more than 500 scene suggestions at once")
        selected_ids = []
        for value in raw_ids:
            if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
                raise HTTPException(400, "suggestion_ids must contain positive integers")
            suggestion_id = int(value)
            if suggestion_id <= 0:
                raise HTTPException(400, "suggestion_ids must contain positive integers")
            if suggestion_id not in selected_ids:
                selected_ids.append(suggestion_id)

        def _pending_selected():
            marks = ",".join("?" for _ in selected_ids)
            with db.reading() as rdb:
                return [dict(row) for row in rdb.execute(
                    f"""SELECT id FROM metadata_suggestions
                        WHERE status='PENDING' AND id IN ({marks})
                        ORDER BY id""",
                    selected_ids,
                ).fetchall()]

        from ..metadata.suggestions import reject
        rows = await asyncio.to_thread(_pending_selected)
        pending_ids = {int(row["id"]) for row in rows}
        not_pending_ids = [suggestion_id for suggestion_id in selected_ids if suggestion_id not in pending_ids]
        rejected_ids, failed_ids = [], []
        for suggestion_id in selected_ids:
            if suggestion_id not in pending_ids:
                continue
            try:
                ok = await asyncio.to_thread(reject, db, suggestion_id)
                if ok:
                    rejected_ids.append(suggestion_id)
                else:
                    failed_ids.append(suggestion_id)
            except Exception as exc:
                db.log(f"Selected scene suggestion {suggestion_id} rejection failed: {exc}", "ERROR",
                       event="metadata.scene_match_review_selected_error")
                failed_ids.append(suggestion_id)

        db.log(
            f"Scene-match selected rejection: selected={len(selected_ids)}, rejected={len(rejected_ids)}, "
            f"not pending={len(not_pending_ids)}, failed={len(failed_ids)}",
            "INFO", event="metadata.scene_match_review_selected_reject",
        )
        return {
            "ok": True, "requested": len(selected_ids), "rejected": len(rejected_ids),
            "rejected_ids": rejected_ids, "not_pending": len(not_pending_ids),
            "not_pending_ids": not_pending_ids, "failed": len(failed_ids),
            "failed_ids": failed_ids,
        }

    @router.post("/api/metadata/scene-match-review/retry-unmatched")
    async def scene_match_review_retry_unmatched(request: Request):
        """Queue one explicit recheck of unmatched/suggested media; never scheduled automatically."""
        require_auth(request)
        def _find_unmatched():
            with db.reading() as rdb:
                rows = rdb.execute(
                    """SELECT DISTINCT m.id FROM media m
                       LEFT JOIN metadata_suggestions s ON s.video_id=m.id AND s.status='PENDING'
                       WHERE (s.id IS NOT NULL
                         OR UPPER(COALESCE(m.identification_state,'')) IN ('PARTIAL','NO_MATCH','FAILED','PENDING','NEW')
                         OR LOWER(COALESCE(m.metadata_source,'')) IN ('none','suggested','unknown','unmatched')
                         OR COALESCE(m.metadata_source,'')='')
                         AND UPPER(COALESCE(m.identification_state,'')) NOT IN ('MATCHED','VERIFIED')
                       ORDER BY CAST(m.source_message_id AS INTEGER) DESC,m.id DESC LIMIT 25000"""
                ).fetchall()
            return [int(row["id"]) for row in rows]
        media_ids = await asyncio.to_thread(_find_unmatched)
        if not media_ids:
            return {"ok": True, "count": 0, "message": "No unmatched or pending-review videos were found."}
        active = [job for job in db.active_jobs("METADATA_SYNC") if job.get("status") in {"QUEUED", "RUNNING", "WAITING"}]
        if active:
            return {"ok": True, "already_running": True, "count": len(media_ids),
                    "message": "A Metadata Sync job is already running; retry was not duplicated."}
        jid = db.create_job("METADATA_SYNC", "", "", False,
                            detail=json.dumps({"media_ids": media_ids, "force": True, "limit": 0,
                                               "unbounded": True, "retry_unmatched": True}))
        jobs.submit(jid)
        db.log(f"Queued one manual retry of {len(media_ids)} unmatched/suggested videos", "INFO", event="metadata.scene_match_retry")
        return {"ok": True, "id": jid, "count": len(media_ids), "message": f"Queued a one-time recheck for {len(media_ids)} unmatched/suggested videos."}


    # ------------------------------------------------------------------
    # SUBSECTION: Suggestion decisions
    # Confirm and reject.
    # ------------------------------------------------------------------
    @router.post("/api/suggestions/{suggestion_id}/confirm")
    async def confirm_suggestion(suggestion_id: int, request: Request):
        """POST /api/suggestions/{suggestion_id}/confirm: Confirm suggestion."""
        require_auth(request)
        from ..metadata.suggestions import confirm
        with db.lock:
            suggestion_row = db.conn.execute(
                "SELECT video_id,payload FROM metadata_suggestions WHERE id=? AND status='PENDING'",
                (int(suggestion_id),),
            ).fetchone()
        if suggestion_row:
            try:
                suggestion_payload = json.loads(suggestion_row["payload"] or "{}")
            except Exception:
                suggestion_payload = {}
            from ..engines.entity_identity import performer_identity_conflicts, mapped_video_entity_context
            mapping_context = mapped_video_entity_context(db, int(suggestion_row["video_id"]))
            conflicts = performer_identity_conflicts(db, suggestion_payload,
                                                     ignore_names=mapping_context.get("performer_names"))
            if conflicts:
                names = ", ".join(dict.fromkeys(str(item.get("name") or "") for item in conflicts if item.get("name")))
                raise HTTPException(409, f"Performer identity is ambiguous ({names or 'unknown cast'}). A provider ID may map to multiple local performer records. Existing mappings for this video are preserved; for an unmapped performer, resolve a confirmed duplicate in Operations → Duplicate performers, then retry. No duplicate performer was created.")
        ok = confirm(db, suggestion_id)
        if not ok:
            raise HTTPException(404, "Suggestion not found, already handled, or blocked by performer-identity safety")
        caption_sync = await _sync_catalogue_caption(int(suggestion_row["video_id"])) if suggestion_row else None
        db.log(f"Metadata suggestion {suggestion_id} confirmed")
        return {"ok": True, "caption_sync": caption_sync}

    @router.post("/api/suggestions/{suggestion_id}/confirm-entity")
    async def confirm_suggestion_entity(suggestion_id: int, request: Request):
        """Confirm only the performer or studio relationship shown on that entity page."""
        require_auth(request)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            entity_type = str(body.get("entity_type") or "").strip().lower()
            entity_id = int(body.get("entity_id") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "entity_type and a valid entity_id are required")
        if entity_type not in {"performer", "studio"} or entity_id < 1:
            raise HTTPException(400, "entity_type must be performer or studio and entity_id must be positive")
        from ..metadata.suggestions import confirm_entity
        result = confirm_entity(db, suggestion_id, entity_type, entity_id)
        if result is None:
            raise HTTPException(404, "This suggestion is no longer pending or is not associated with that entity")
        if result.get("conflict") == "already_linked_to_another_studio":
            existing_names = ", ".join(result.get("existing_names") or [])
            raise HTTPException(
                409,
                f"This video is already linked to {existing_names or 'another studio'}. "
                "Use Edit Video to replace the studio mapping.",
            )
        db.log(
            f"Suggestion {suggestion_id}: linked video {result['video_id']} to "
            f"{entity_type} {result['entity_id']} ({result['entity_name']})",
            "INFO",
        )
        return {"ok": True, "mapping": result}

    @router.post("/api/suggestions/{suggestion_id}/reject-entity")
    async def reject_entity_suggestion(suggestion_id: int, request: Request):
        """Reject one performer/studio relationship without rejecting the scene."""
        require_auth(request)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            entity_type = str(body.get("entity_type") or "").strip().lower()
            entity_id = int(body.get("entity_id") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "entity_type and a valid entity_id are required")
        if entity_type not in {"performer", "studio"} or entity_id < 1:
            raise HTTPException(400, "entity_type must be performer or studio and entity_id must be positive")
        from ..metadata.suggestions import reject_entity
        result = reject_entity(db, suggestion_id, entity_type, entity_id)
        if result is None:
            raise HTTPException(404, "This relationship is no longer suggested for that entity")
        db.log(
            f"Suggestion {suggestion_id}: rejected video {result['video_id']} -> "
            f"{entity_type} {result['entity_id']} relationship",
            "INFO",
        )
        return {"ok": True, "decision": result}

    @router.post("/api/suggestions/{suggestion_id}/reject")
    async def reject_suggestion(suggestion_id: int, request: Request):
        """POST /api/suggestions/{suggestion_id}/reject: Reject suggestion."""
        require_auth(request)
        from ..metadata.suggestions import reject
        ok = reject(db, suggestion_id)
        if not ok:
            raise HTTPException(404, "Suggestion not found or already handled")
        db.log(f"Metadata suggestion {suggestion_id} rejected")
        return {"ok": True}


    # ------------------------------------------------------------------
    # SUBSECTION: Auto-match review
    # Review list and actions.
    # ------------------------------------------------------------------
    # V29.4.133: earlier filename auto-matches the current rule would only
    # suggest. Nothing changes until the operator keeps or undoes one.
    @router.get("/api/metadata/auto-match-review")
    async def auto_match_review_list(request: Request):
        require_auth(request)
        from ..metadata import auto_match_review
        items = await asyncio.to_thread(auto_match_review.candidates, db, 500)
        return {"count": len(items), "items": items}

    @router.post("/api/metadata/auto-match-review/{video_id}")
    async def auto_match_review_action(video_id: int, request: Request):
        require_auth(request)
        body = await request.json()
        action = str(body.get("action") or "").strip().lower()
        from ..metadata import auto_match_review
        if action == "keep":
            ok = await asyncio.to_thread(auto_match_review.keep, db, video_id)
        elif action == "undo":
            ok = await asyncio.to_thread(auto_match_review.undo, db, video_id)
        else:
            raise HTTPException(400, "action must be keep or undo")
        if not ok:
            raise HTTPException(404, "Video not found, already reviewed, or manually mapped")
        queued = _queue_catalogue_caption_sync([int(video_id)]) if action == "keep" else 0
        db.log(f"Auto-match review: video {video_id} {'kept' if action == 'keep' else 'sent back to suggestions'}")
        return {"ok": True, "caption_sync": {"status": "queued" if queued else "not_needed", "count": queued}}


    @router.post("/api/metadata/auto-match-review/bulk")
    async def auto_match_review_bulk(request: Request):
        """Review up to 100 filename auto-matches in one explicit operator action."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        action = str(body.get("action") or "").strip().lower()
        raw_ids = body.get("video_ids")
        if action not in {"keep", "undo"}:
            raise HTTPException(400, "action must be keep or undo")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(400, "video_ids must be a non-empty list")
        if len(raw_ids) > 100:
            raise HTTPException(413, "At most 100 video IDs may be submitted at once")
        try:
            ids = list(dict.fromkeys(int(value) for value in raw_ids if int(value) > 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "video_ids must contain positive integers")
        if len(ids) > 100:
            raise HTTPException(413, "At most 100 videos may be reviewed at once")
        if not ids:
            raise HTTPException(400, "video_ids must contain positive integers")
        from ..metadata import auto_match_review
        result = await asyncio.to_thread(auto_match_review.bulk_action, db, ids, action)
        queued = _queue_catalogue_caption_sync(ids) if action == "keep" else 0
        db.log(f"Bulk auto-match review: {action} {result['processed']} video(s); {result['skipped']} skipped")
        return {"ok": True, **result,
                "caption_sync": {"status": "queued" if queued else "not_needed", "count": queued}}


    # ------------------------------------------------------------------
    # SUBSECTION: Suggested videos and search
    # Suggested videos, search and performers.
    # ------------------------------------------------------------------
    @router.get("/api/performers/{performer_id}/suggested")
    async def performer_suggested_videos(performer_id: int, request: Request):
        """GET /api/performers/{performer_id}/suggested: Performer suggested videos."""
        require_auth(request)
        from ..metadata.suggestions import list_for_performer, candidate_display_fields
        rows = list_for_performer(db, performer_id, status="PENDING")
        out = []
        for r in rows:
            def _read_media():
                with db.reading() as rdb:
                    row = rdb.execute("SELECT id, source_channel, source_message_id, filename, title, duration, size FROM archive_x_library_media WHERE id=?", (r["video_id"],)).fetchone()
                    return dict(row) if row else None
            media = await asyncio.to_thread(_read_media)
            if not media:
                continue
            out.append({
                "suggestion_id": r["id"], "media_id": media["id"],
                "channel_id": media["source_channel"], "message_id": media["source_message_id"],
                "filename": media["filename"], "title": media["title"], "duration": float(media["duration"] or 0), "size": int(media["size"] or 0),
                "confidence": r["confidence"], "score": r["score"], "provider": r["provider"], "reason": r["reason"],
                **candidate_display_fields(r),
            })
        return out


    @router.get("/api/studios/{studio_id}/suggested")
    async def studio_suggested_videos(studio_id: int, request: Request):
        """GET /api/studios/{studio_id}/suggested: Site suggested videos."""
        require_auth(request)
        from ..metadata.suggestions import list_for_site, candidate_display_fields
        rows = list_for_site(db, studio_id, status="PENDING")
        out = []
        for r in rows:
            def _read_media():
                with db.reading() as rdb:
                    row = rdb.execute("SELECT id, source_channel, source_message_id, filename, title, duration, size FROM archive_x_library_media WHERE id=?", (r["video_id"],)).fetchone()
                    return dict(row) if row else None
            media = await asyncio.to_thread(_read_media)
            if not media:
                continue
            out.append({
                "suggestion_id": r["id"], "media_id": media["id"],
                "channel_id": media["source_channel"], "message_id": media["source_message_id"],
                "filename": media["filename"], "title": media["title"],
                "duration": float(media["duration"] or 0), "size": int(media["size"] or 0),
                "confidence": r["confidence"], "score": r["score"], "provider": r["provider"], "reason": r["reason"],
                **candidate_display_fields(r),
            })
        return out


    @router.get("/api/search")
    def global_search(request: Request):
        """GET /api/search: Global search."""
        require_auth(request)
        import re as _re
        q = str(request.query_params.get("q") or "").strip()
        try:
            limit = min(100, max(10, int(request.query_params.get("limit", 48))))
        except ValueError:
            limit = 48
        if not q:
            return {"videos": [], "performers": [], "tags": [], "studios": []}

        # Never pass raw user text to FTS5 syntax. Tokenise it into literal
        # unicode words and add prefix matching, falling back to LIKE if an
        # older SQLite build rejects the FTS expression.
        tokens = [x for x in _re.findall(r"[\w]+", q, flags=_re.UNICODE) if x][:8]
        match = " ".join(f'"{t.replace(chr(34), "")}"*' for t in tokens) if tokens else ""
        like = f"%{q}%"
        with db.reading() as _rdb:
            p_rows = _rdb.execute(
                "SELECT p.*, COUNT(DISTINCT combined.video_id) video_count "
                "FROM performers p LEFT JOIN (SELECT performer_id, video_id FROM video_performers WHERE video_id IN (SELECT id FROM archive_x_library_media) UNION SELECT performer_id, video_id FROM local_video_performers WHERE video_id IN (SELECT id FROM archive_x_library_media)) combined "
                "ON combined.performer_id=p.id WHERE p.name LIKE ? OR COALESCE(p.aliases,'') LIKE ? "
                "GROUP BY p.id HAVING video_count > 0 ORDER BY (lower(p.name)=lower(?)) DESC, video_count DESC, p.name LIMIT ?",
                (like, like, q, limit)).fetchall()
            t_rows = _rdb.execute(
                "SELECT t.*, COUNT(DISTINCT vt.video_id) video_count FROM tags t "
                "LEFT JOIN video_tags vt ON vt.tag_id=t.id AND vt.video_id IN (SELECT id FROM archive_x_library_media) WHERE t.name LIKE ? "
                "GROUP BY t.id HAVING video_count > 0 ORDER BY (lower(t.name)=lower(?)) DESC, video_count DESC, t.name LIMIT ?",
                (like, q, limit)).fetchall()
            s_rows = _rdb.execute(
                "SELECT s.*, COUNT(DISTINCT CASE WHEN vs.video_id IN (SELECT id FROM archive_x_library_media) THEN vs.video_id END) video_count FROM studios s "
                "LEFT JOIN video_studios vs ON vs.studio_id=s.id AND vs.video_id IN (SELECT id FROM archive_x_library_media) WHERE s.id IN (SELECT rowid FROM studio_fts WHERE studio_fts MATCH ?) "
                "GROUP BY s.id HAVING video_count > 0 ORDER BY (lower(s.name)=lower(?)) DESC, video_count DESC, s.name LIMIT ?",
                (match or '""', q, limit)).fetchall() if getattr(db, "fts_available", False) and match else _rdb.execute(
                "SELECT s.*, COUNT(DISTINCT CASE WHEN vs.video_id IN (SELECT id FROM archive_x_library_media) THEN vs.video_id END) video_count FROM studios s "
                "LEFT JOIN video_studios vs ON vs.studio_id=s.id AND vs.video_id IN (SELECT id FROM archive_x_library_media) WHERE s.name LIKE ? "
                "GROUP BY s.id HAVING video_count > 0 ORDER BY (lower(s.name)=lower(?)) DESC, video_count DESC, s.name LIMIT ?",
                (like, q, limit)).fetchall()

            media_ids=[]
            try:
                if getattr(db, "fts_available", False) and match:
                    media_ids=[x["id"] for x in _rdb.execute("""
                        SELECT m.id FROM archive_x_library_media m
                        WHERE m.id IN (SELECT rowid FROM media_fts WHERE media_fts MATCH ?)
                           OR m.id IN (SELECT vp.video_id FROM video_performers vp WHERE vp.performer_id IN (SELECT rowid FROM performer_fts WHERE performer_fts MATCH ?))
                           OR m.id IN (SELECT lvp.video_id FROM local_video_performers lvp WHERE lvp.performer_id IN (SELECT rowid FROM performer_fts WHERE performer_fts MATCH ?))
                           OR m.id IN (SELECT vs.video_id FROM video_studios vs WHERE vs.studio_id IN (SELECT rowid FROM studio_fts WHERE studio_fts MATCH ?))
                           OR m.id IN (SELECT vt.video_id FROM video_tags vt WHERE vt.tag_id IN (SELECT rowid FROM tag_fts WHERE tag_fts MATCH ?))
                        ORDER BY m.upload_date DESC LIMIT ?
                    """, (match, match, match, match, match, limit)).fetchall()]
            except Exception:
                media_ids=[]
            if not media_ids:
                media_ids=[x["id"] for x in _rdb.execute("""
                    SELECT m.id FROM archive_x_library_media m
                    WHERE m.title LIKE ? OR m.filename LIKE ? OR m.caption LIKE ?
                       OR EXISTS (SELECT 1 FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=m.id AND p.name LIKE ?)
                       OR EXISTS (SELECT 1 FROM local_video_performers lvp JOIN performers p ON p.id=lvp.performer_id WHERE lvp.video_id=m.id AND p.name LIKE ?)
                       OR EXISTS (SELECT 1 FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=m.id AND s.name LIKE ?)
                       OR EXISTS (SELECT 1 FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=m.id AND t.name LIKE ?)
                    ORDER BY m.upload_date DESC LIMIT ?
                """, (like,like,like,like,like,like,like,limit)).fetchall()]
            videos=[]
            if media_ids:
                ph=','.join('?' for _ in media_ids)
                rows=_rdb.execute(f"SELECT m.*, GROUP_CONCAT(DISTINCT p.name) performers, GROUP_CONCAT(DISTINCT lp.name) local_performers, GROUP_CONCAT(DISTINCT s.name) studios, GROUP_CONCAT(DISTINCT t.name) tags FROM archive_x_library_media m LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id LEFT JOIN local_video_performers lvp ON lvp.video_id=m.id LEFT JOIN performers lp ON lp.id=lvp.performer_id LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id WHERE m.id IN ({ph}) GROUP BY m.id ORDER BY m.upload_date DESC", media_ids).fetchall()
                for row in rows:
                    d=dict(row)
                    d["performers"]=list(dict.fromkeys([x for x in (d.get("performers") or '').split(',') if x]+[x for x in (d.get("local_performers") or '').split(',') if x]))
                    d["local_performers"]=[x for x in (d.get("local_performers") or '').split(',') if x]
                    d["studios"]=[x for x in (d.get("studios") or '').split(',') if x]
                    d["studio"]=d["studios"][0] if d["studios"] else ''
                    d["tags"]=[x for x in (d.get("tags") or '').split(',') if x]
                    d["id"]=d.get("source_message_id"); d["_cid"]=d.get("source_channel")
                    d["name"]=d.get("title") or d.get("filename") or f"video {d['id']}"
                    d["telegram_filename"]=d.get("filename") or ''
                    d["thumbnail_id"]=d.get("thumbnail_message_id") or d.get("source_message_id")
                    videos.append(d)
        return {"videos":videos,"performers":[dict(x) for x in p_rows],"tags":[dict(x) for x in t_rows],"studios":[dict(x) for x in s_rows]}

    @router.get("/api/performers")
    async def list_performers(request: Request):
        """GET /api/performers: List performers."""
        require_auth(request)
        # SQLite work (incl. the one-time backfill) runs in a worker thread so
        # the web server never freezes while it runs.
        return await asyncio.to_thread(list_performers_sync, db, request.query_params)
