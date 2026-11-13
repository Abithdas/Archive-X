"""Explicit registration for tags, logs, diagnostics, backups, and thumbnails."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Tags
#       - Logs and diagnostics downloads
#       - Local unmap and page enrichment
#       - Send to Telegram
#       - Backup and restore
#       - Thumbnails
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import json
import os
import secrets
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


def register_tags_logs_routes(
    router: APIRouter,
    *,
    config,
    db,
    jobs,
    fts_prefix_query,
    page_enricher,
    ps,
    require_auth,
    resources,
    telegram,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Tags
    # Tags and tag videos.
    # ------------------------------------------------------------------
    """Register tag, log, send, backup and thumbnail routes."""
    @router.get("/api/tags")
    async def list_tags(request: Request):
        """GET /api/tags: List locally saved tags without waiting on the writer lock."""
        require_auth(request)
        q = str(request.query_params.get("q") or "").strip()
        brief = str(request.query_params.get("brief") or "") == "1"
        def _read_tags():
            with db.reading() as rdb:
                match = fts_prefix_query(q) if q else ""
                if q and match and getattr(db, "fts_available", False):
                    rows = rdb.execute("SELECT t.*, COUNT(DISTINCT CASE WHEN vt.video_id IN (SELECT id FROM archive_x_library_media) THEN vt.video_id END) video_count FROM tags t LEFT JOIN video_tags vt ON vt.tag_id=t.id WHERE t.id IN (SELECT rowid FROM tag_fts WHERE tag_fts MATCH ?) GROUP BY t.id HAVING video_count > 0 ORDER BY video_count DESC,t.name", (match,)).fetchall()
                elif q:
                    rows = rdb.execute("SELECT t.*, COUNT(DISTINCT CASE WHEN vt.video_id IN (SELECT id FROM archive_x_library_media) THEN vt.video_id END) video_count FROM tags t LEFT JOIN video_tags vt ON vt.tag_id=t.id WHERE t.name LIKE ? GROUP BY t.id HAVING video_count > 0 ORDER BY video_count DESC,t.name", (f"%{q}%",)).fetchall()
                else:
                    rows = rdb.execute("SELECT t.*, COUNT(DISTINCT CASE WHEN vt.video_id IN (SELECT id FROM archive_x_library_media) THEN vt.video_id END) video_count FROM tags t LEFT JOIN video_tags vt ON vt.tag_id=t.id GROUP BY t.id HAVING video_count > 0 ORDER BY video_count DESC,t.name").fetchall()
            result = [dict(r) for r in rows]
            if brief:
                result = [{k: d.get(k) for k in ("id", "name", "video_count")} for d in result]
            return result
        return await asyncio.to_thread(_read_tags)

    @router.get("/api/tags/{tag_id}/videos")
    async def tag_videos(tag_id: int, request: Request):
        """GET /api/tags/{tag_id}/videos: Local catalogue data, no writer lock."""
        require_auth(request)
        def _read_tag_videos():
            with db.reading() as rdb:
                tag = rdb.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
                if not tag:
                    return None, []
                rows = rdb.execute("SELECT m.*, GROUP_CONCAT(DISTINCT p.name) performers, GROUP_CONCAT(DISTINCT lp.name) local_performers, GROUP_CONCAT(DISTINCT s.name) studios, GROUP_CONCAT(DISTINCT t2.name) tags FROM archive_x_library_media m LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id LEFT JOIN local_video_performers lvp ON lvp.video_id=m.id LEFT JOIN performers lp ON lp.id=lvp.performer_id LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id LEFT JOIN video_tags vt2 ON vt2.video_id=m.id LEFT JOIN tags t2 ON t2.id=vt2.tag_id WHERE EXISTS(SELECT 1 FROM video_tags vt WHERE vt.video_id=m.id AND vt.tag_id=?) GROUP BY m.id ORDER BY m.upload_date DESC", (tag_id,)).fetchall()
                return dict(tag), [dict(r) for r in rows]
        tag, rows = await asyncio.to_thread(_read_tag_videos)
        if not tag:
            raise HTTPException(404, "Tag not found")
        videos = []
        for d in rows:
            d["performers"] = list(dict.fromkeys([x for x in (d.get("performers") or "").split(",") if x] + [x for x in (d.get("local_performers") or "").split(",") if x]))
            d["studios"] = [x for x in (d.get("studios") or "").split(",") if x]
            d["tags"] = [x for x in (d.get("tags") or "").split(",") if x]
            d["studio"] = d["studios"][0] if d["studios"] else ""
            d["id"] = d.get("source_message_id"); d["_cid"] = d.get("source_channel")
            d["name"] = d.get("title") or d.get("filename") or f"video {d['id']}"
            d["thumbnail_id"] = d.get("thumbnail_message_id") or d.get("source_message_id")
            videos.append(d)
        return {"tag": tag, "videos": videos}


    # ------------------------------------------------------------------
    # SUBSECTION: Logs and diagnostics downloads
    # Log and report downloads.
    # ------------------------------------------------------------------
    @router.get("/api/log/download")
    async def download_log(request: Request):
        """GET /api/log/download: Download log."""
        require_auth(request)
        from fastapi.responses import Response
        def _read_event_log():
            with db.reading() as rdb:
                return rdb.execute("SELECT level, message, created_at FROM events ORDER BY id DESC LIMIT 5000").fetchall()
        rows = await asyncio.to_thread(_read_event_log)
        lines = [f"[{r['created_at']}] [{r['level']}] {r['message']}" for r in rows]
        try:
            from ..core.ai_evidence import redact_text
            cli_path = getattr(config, "CLI_LOG_PATH", config.LOG_DIR / "launcher-cli.log")
            if cli_path.exists():
                cli_lines = cli_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
                lines.extend(["", "===== ARCHIVE X LAUNCHER / CLI =====", *[redact_text(x) for x in cli_lines]])
        except Exception:
            _swallowed_note("routes_tags_logs.register_tags_logs_routes.download_log")
            pass
        content = "\n".join(lines)
        return Response(content=content.encode(), media_type="text/plain",
                        headers={"Content-Disposition": "attachment; filename=telegram-archive-log.txt"})

    @router.get("/api/diagnostics/report")
    async def download_diagnostics_report(request: Request, scope: str = "current"):
        """Download a redacted diagnostic report without depending on job workers.

        Diagnostics are support infrastructure, not a background job. The
        endpoint therefore has a hard collection deadline and a minimal
        fallback report so a stalled Timeline/Metadata job cannot make the
        diagnostic download appear to hang indefinitely.
        """
        require_auth(request)
        from datetime import datetime, timezone
        from fastapi.responses import Response
        from ..core.diagnostics import flush_diagnostics
        from ..core.ai_evidence import collect_diagnostics

        flush_diagnostics()
        launch_id = os.getenv("ARCHIVE_X_LAUNCH_ID") if str(scope or "current").lower() != "all" else None
        requested_items = [
            "PLAYBACK_LOG",
            "PLAYBACK_STATUS",
            "APPLICATION_LOGS",
            "CLI_LAUNCHER_LOG",
            "APPLICATION_EVENTS",
            "RUNTIME_SNAPSHOT",
            "ENVIRONMENT",
            "CORRELATION_INDEX",
            "PROCESS_TOPOLOGY",
            "BROWSER_DIAGNOSTICS",
            "METADATA_ASSETS",
        ]
        try:
            # Process topology can invoke bounded Windows diagnostics and the
            # database may be briefly busy flushing events. Never let either
            # turn a support download into an unbounded HTTP request.
            report = await asyncio.wait_for(
                asyncio.to_thread(
                    collect_diagnostics,
                    db=db,
                    launch_id=launch_id,
                    requested_items=requested_items,
                    max_chars=180_000,
                    playback_status=((ps.get("engine").status() if ps.get("engine") else {"running":False}) if "PLAYBACK_STATUS" in requested_items else None),
                ),
                timeout=15.0,
            )
            body = report["text"]
        except asyncio.TimeoutError:
            body = json.dumps({
                "schema": 6,
                "kind": "DIAGNOSTICS",
                "partial": True,
                "diagnostic_scope": {
                    "launch_id": launch_id or "all persisted launches",
                    "current_process_launch_id": os.getenv("ARCHIVE_X_LAUNCH_ID") or "-",
                },
                "error": {
                    "type": "DiagnosticCollectionTimeout",
                    "message": "Full diagnostic collection exceeded the 15 second support-download deadline. The application remained responsive; the report contains a bounded failure record instead of hanging the download.",
                },
            }, indent=2)
            try:
                db.log("Diagnostic report collection exceeded 15s; returned bounded fallback report", "WARN", event="diagnostics.report.timeout")
            except Exception:
                _swallowed_note("routes_tags_logs.register_tags_logs_routes.download_diagnostics_report")
                pass
        except Exception as exc:
            body = json.dumps({
                "schema": 6,
                "kind": "DIAGNOSTICS",
                "partial": True,
                "diagnostic_scope": {
                    "launch_id": launch_id or "all persisted launches",
                    "current_process_launch_id": os.getenv("ARCHIVE_X_LAUNCH_ID") or "-",
                },
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                },
            }, indent=2)
            try:
                db.log(f"Diagnostic report collection failed: {type(exc).__name__}: {exc}", "WARN", event="diagnostics.report.error")
            except Exception:
                _swallowed_note("routes_tags_logs.register_tags_logs_routes.download_diagnostics_report")
                pass

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        data = body.encode("utf-8", errors="replace")
        return Response(
            content=data,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="archive-x-diagnostic-report-{stamp}.json"',
                "Content-Length": str(len(data)),
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "X-Archive-X-Diagnostic-Report": "1",
            },
        )

    @router.get("/api/diagnostics/report/html")
    async def download_diagnostics_report_html(request: Request, scope: str = "current"):
        """Download the same canonical diagnostic evidence as a human-readable HTML report."""
        require_auth(request)
        from datetime import datetime, timezone
        from fastapi.responses import HTMLResponse
        from ..core.diagnostics import flush_diagnostics
        from ..core.ai_evidence import collect_diagnostics, render_diagnostics_html
        flush_diagnostics()
        launch_id = os.getenv("ARCHIVE_X_LAUNCH_ID") if str(scope or "current").lower() != "all" else None
        requested_items = ["PLAYBACK_LOG", "APPLICATION_LOGS", "CLI_LAUNCHER_LOG", "APPLICATION_EVENTS", "RUNTIME_SNAPSHOT", "ENVIRONMENT", "CORRELATION_INDEX", "PROCESS_TOPOLOGY", "BROWSER_DIAGNOSTICS", "METADATA_ASSETS", "TELEGRAM_TRANSFER", "CONTENT_AUTOMATION"]
        try:
            report = await asyncio.wait_for(asyncio.to_thread(collect_diagnostics, db=db, launch_id=launch_id, requested_items=requested_items, max_chars=180_000), timeout=15.0)
            body = render_diagnostics_html(report["text"])
        except Exception as exc:
            body = "<html><body><h1>Archive X Diagnostic Report</h1><pre>" + json.dumps({"partial": True, "error": f"{type(exc).__name__}: {exc}"}, indent=2) + "</pre></body></html>"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return HTMLResponse(content=body, headers={"Content-Disposition": f'attachment; filename="archive-x-diagnostic-report-{stamp}.html"', "Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "X-Archive-X-Diagnostic-Report": "1"})


    # ------------------------------------------------------------------
    # SUBSECTION: Local unmap and page enrichment
    # Unmap and background page details.
    # ------------------------------------------------------------------
    @router.post("/api/local/performer-unmap")
    async def local_performer_unmap(request: Request):
        """Remove a performer from one video (only links you added yourself,
        plus filename/API links when you explicitly remove them here)."""
        require_auth(request)
        body = await request.json()
        channel_id = str(body.get("channel_id") or "").strip()
        message_id = int(body.get("message_id") or 0)
        name = str(body.get("name") or "").strip()
        if not channel_id or not message_id or not name:
            raise HTTPException(400, "channel_id, message_id and performer name are required")
        with db.lock:
            media = db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                                    (channel_id, message_id)).fetchone()
            perf = db.conn.execute("SELECT id FROM performers WHERE lower(name)=lower(?)", (name,)).fetchone()
            if not media or not perf:
                raise HTTPException(404, "Not linked")
            db.conn.execute("DELETE FROM local_video_performers WHERE video_id=? AND performer_id=?", (media["id"], perf["id"]))
            db.conn.execute("DELETE FROM video_performers WHERE video_id=? AND performer_id=?", (media["id"], perf["id"]))
            # Removing the explicit local mapping also releases its metadata
            # lock; otherwise future API scans would remain suppressed after
            # the operator has deliberately removed the mapping.
            db.conn.execute("DELETE FROM metadata_mapping_locks WHERE video_id=? AND field='performer'", (media["id"],))
            db.conn.commit()
        db.log(f"Performer {name} removed from channel {channel_id} message {message_id}")
        return {"ok": True}

    # ---------------- background details for the visible page ----------------
    from ..engines.page_enricher import PageEnricher
    enricher = page_enricher or PageEnricher(db, telegram, priority_gate=getattr(jobs, "background_priority", None), resources=resources, metadata_blocked=lambda: jobs.is_active("METADATA_SYNC"))

    @router.post("/api/enrich")
    async def enrich_page(request: Request):
        """Legacy compatibility endpoint; consumer pages never enqueue preparation."""
        require_auth(request)
        return {"ok": True, "queued": 0, "pending": 0, "disabled": True, "reason": "consumer_catalogue_is_published_only", "now": time.time()}

    @router.post("/api/enrich/updates")
    async def enrich_updates(request: Request):
        """Legacy compatibility endpoint; no consumer-side enrichment is active."""
        require_auth(request)
        return {"now": time.time(), "pending": 0, "videos": [], "disabled": True}

    # ---------------- send to Telegram ----------------
    from ..engines import telegram_sender


    # ------------------------------------------------------------------
    # SUBSECTION: Send to Telegram
    # Send settings, previews and bulk sends.
    # ------------------------------------------------------------------
    @router.get("/api/settings/send")
    async def get_send_settings(request: Request):
        """GET /api/settings/send: Get send settings."""
        require_auth(request)
        return telegram_sender.settings(db)

    @router.post("/api/settings/send")
    async def save_send_settings(request: Request):
        """POST /api/settings/send: Save send settings."""
        require_auth(request)
        return telegram_sender.save_settings(db, await request.json())

    @router.post("/api/send/preview")
    async def send_caption_preview(request: Request):
        """POST /api/send/preview: Send caption preview."""
        require_auth(request)
        data = await request.json()
        c, m = int(data["channel_id"]), int(data["message_id"])
        v = await asyncio.to_thread(telegram_sender._video_fields, db, c, m, None)
        tpl = data.get("template") or telegram_sender.settings(db)["template"]
        return {"caption": telegram_sender.render_caption(tpl, v)}

    send_lock = asyncio.Lock()
    # Lightweight in-memory progress for user-started bulk Telegram sends.
    bulk_send_progress = {}

    @router.get("/api/send/bulk/status/{job_id}")
    async def send_to_telegram_bulk_status(request: Request, job_id: str):
        require_auth(request)
        state = bulk_send_progress.get(str(job_id))
        if not state:
            return {"job_id": str(job_id), "active": False}
        return dict(state)

    @router.post("/api/send/repair-covers")
    async def repair_sent_video_covers(request: Request):
        """POST /api/send/repair-covers: Repair sent video covers."""
        require_auth(request)
        if not telegram.connected:
            raise HTTPException(503, "Telegram is not connected")

        from ..engines.thumbnail_cache import get_send_contact_sheet_bytes

        async def thumb_getter(cid, mid):
            data_, _ = await get_send_contact_sheet_bytes(db, telegram, cid, mid)
            return data_

        with db.lock:
            rows = db.conn.execute(
                "SELECT source_channel,message_id,target_channel,sent_message_id "
                "FROM sent_log WHERE sent_message_id IS NOT NULL "
                "ORDER BY sent_at ASC"
            ).fetchall()

        repaired = skipped = failed = 0
        errors = []
        async with send_lock:
            for row in rows:
                try:
                    result = await telegram_sender.repair_existing_cover(
                        db, telegram.client,
                        int(row["source_channel"]), int(row["message_id"]),
                        int(row["target_channel"]), int(row["sent_message_id"]),
                        thumb_getter,
                    )
                    if result.get("changed"):
                        repaired += 1
                    else:
                        skipped += 1
                except Exception as e:
                    failed += 1
                    if len(errors) < 20:
                        errors.append({
                            "source": f"{row['source_channel']}/{row['message_id']}",
                            "destination": f"{row['target_channel']}/{row['sent_message_id']}",
                            "error": f"{type(e).__name__}: {e}",
                        })

        db.log(f"Telegram cover repair: repaired={repaired}, skipped={skipped}, failed={failed}")
        return {"ok": True, "total": len(rows), "repaired": repaired,
                "skipped": skipped, "failed": failed, "errors": errors}

    @router.post("/api/send")
    async def send_to_telegram(request: Request):
        """POST /api/send: Send to telegram."""
        require_auth(request)
        data = await request.json()
        try:
            c, m = int(data["channel_id"]), int(data["message_id"])
        except Exception:
            raise HTTPException(400, "channel_id and message_id are required")
        target = str(data.get("target") or telegram_sender.settings(db)["target"] or "").strip()
        if not target:
            raise HTTPException(400, "Choose the channel to send to")
        if data.get("remember"):
            telegram_sender.save_settings(db, {"target": target, "fixed": True})
        if not telegram.connected:
            raise HTTPException(503, "Telegram is not connected")
        premium = False
        eng = ps.get("engine")
        if eng is not None:
            premium = bool(eng.premium)

        async def thumb_getter(cid, mid):
            try:
                # Telegram delivery uses the dedicated 36-frame contact sheet;
                # UI/library thumbnails remain the representative still.
                from ..engines.thumbnail_cache import get_send_contact_sheet_bytes
                data_, _ = await get_send_contact_sheet_bytes(db, telegram, cid, mid)
                return data_
            except Exception:
                return None

        async with send_lock:
            # Recheck while holding the same lock as bulk sends to prevent a
            # single-send request racing a selected-video batch for the same item.
            if not data.get("force") and await asyncio.to_thread(telegram_sender.already_sent, db, c, m, target):
                repaired = await telegram_sender.reconcile_existing_send_copy(
                    db, telegram.client, c, m, int(target),
                )
                if repaired and repaired.get("library_ready") and not repaired.get("already_ready"):
                    target_title = next(
                        (x["title"] for x in _chan_titles() if x["id"] == str(target)),
                        "the destination channel",
                    )
                    db.log(
                        f"Previously sent video is now available in {target_title}: "
                        f"{repaired.get('filename') or f'message {m}'}",
                        "SUCCESS", event="telegram.send.completed",
                    )
                return JSONResponse({"already_sent": True, "detail": "This video was already sent to that channel"},
                                    status_code=409)
            try:
                result = await telegram_sender.send_video(db, telegram.client, c, m, int(target),
                                                          thumb_getter, premium=premium)
            except FileNotFoundError as e:
                raise HTTPException(404, str(e))
            except Exception as e:
                db.log(f"Send to Telegram failed {c}/{m} -> {target}: {type(e).__name__}: {e}", "ERROR")
                raise HTTPException(502, f"Telegram refused: {type(e).__name__}: {e}")
        title = next((x["title"] for x in (await asyncio.to_thread(_chan_titles)) if x["id"] == str(target)), target)
        db.log(
            f"Telegram video sent to {title}: {result.get('filename') or f'message {m}'}",
            "SUCCESS", event="telegram.send.completed",
            details={"destination_title": title, "filename": result.get("filename") or ""},
        )
        return {**result, "target": target, "target_title": title}

    def _chan_titles():
        with db.lock:
            return [{"id": str(r["telegram_id"]), "title": r["title"]}
                    for r in db.conn.execute("SELECT telegram_id,title FROM channels").fetchall()]

    @router.post("/api/send/bulk")
    async def send_to_telegram_bulk(request: Request):
        """Send a selected set of source videos to one Telegram destination.

        The same delivery engine, cover validation, caption template, and sent-log
        checks as the single-video operation are used. Sends are deliberately
        sequential so a bulk action cannot overwhelm Telegram or FFmpeg.
        """
        require_auth(request)
        data = await request.json()
        raw_videos = data.get("videos")
        if not isinstance(raw_videos, list) or not raw_videos:
            raise HTTPException(400, "Select at least one video")
        if len(raw_videos) > 1000:
            raise HTTPException(413, "Bulk send is limited to 1000 videos per request")

        videos = []
        seen = set()
        for item in raw_videos:
            if not isinstance(item, dict):
                raise HTTPException(400, "Each selected video must include channel_id and message_id")
            try:
                channel_id = int(item["channel_id"])
                message_id = int(item["message_id"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "Each selected video must include valid channel_id and message_id")
            if message_id <= 0:
                raise HTTPException(400, "message_id must be positive")
            key = (str(channel_id), message_id)
            if key in seen:
                continue
            seen.add(key)
            videos.append({"channel_id": channel_id, "message_id": message_id})

        target = str(data.get("target") or telegram_sender.settings(db)["target"] or "").strip()
        if not target:
            raise HTTPException(400, "Choose the channel to send to")
        try:
            target_id = int(target)
        except (TypeError, ValueError):
            raise HTTPException(400, "Choose a valid Telegram channel")
        duplicate_action = str(data.get("duplicate_action") or "").strip().lower()
        if duplicate_action not in ("", "skip", "resend"):
            raise HTTPException(400, "duplicate_action must be skip or resend")
        if data.get("remember"):
            telegram_sender.save_settings(db, {"target": target, "fixed": True})
        if not telegram.connected:
            raise HTTPException(503, "Telegram is not connected")

        premium = False
        eng = ps.get("engine")
        if eng is not None:
            premium = bool(eng.premium)

        async def thumb_getter(cid, mid):
            try:
                # Match the existing single-send contact-sheet delivery path.
                from ..engines.thumbnail_cache import get_send_contact_sheet_bytes
                data_, _ = await get_send_contact_sheet_bytes(db, telegram, cid, mid)
                return data_
            except Exception:
                return None

        duplicate_rows = []
        duplicate_keys = set()
        results = []
        sent_count = skipped_count = failed_count = 0
        library_reconciled_count = 0
        job_id = str(data.get("job_id") or secrets.token_urlsafe(12)).strip()[:80]
        progress = {
            "job_id": job_id, "active": True, "stage": "Checking already-sent videos",
            "total": len(videos), "done": 0, "sent": 0, "skipped": 0, "failed": 0,
            "current_channel_id": None, "current_message_id": None,
        }
        bulk_send_progress[job_id] = progress
        while len(bulk_send_progress) > 32:
            bulk_send_progress.pop(next(iter(bulk_send_progress)))
        # Hold the same lock used by single-video sends for the entire operation.
        async with send_lock:
            for item in videos:
                cid, mid = item["channel_id"], item["message_id"]
                if await asyncio.to_thread(telegram_sender.already_sent, db, cid, mid, target):
                    duplicate_rows.append({"channel_id": str(cid), "message_id": mid})
                    duplicate_keys.add((str(cid), mid))
                    try:
                        repaired = await telegram_sender.reconcile_existing_send_copy(
                            db, telegram.client, cid, mid, target_id,
                        )
                        if repaired and repaired.get("library_ready") and not repaired.get("already_ready"):
                            library_reconciled_count += 1
                    except Exception as repair_exc:
                        db.log(
                            f"Could not reconcile Library visibility for a previously sent video: "
                            f"{type(repair_exc).__name__}: {repair_exc}",
                            "WARN", event="telegram.send.library_registration_pending",
                        )

            # This is a preflight-only response. Nothing has been sent yet, so the
            # caller can safely ask once whether to resend or skip duplicates.
            if duplicate_rows and not duplicate_action:
                progress.update({"active": False, "stage": "confirmation_required"})
                return JSONResponse({
                    "already_sent": True,
                    "duplicate_videos": duplicate_rows,
                    "detail": f"{len(duplicate_rows)} selected video(s) were already sent to this channel",
                    "target": target,
                    "library_reconciled": library_reconciled_count,
                }, status_code=409)

            progress["stage"] = "sending"
            for item in videos:
                cid, mid = item["channel_id"], item["message_id"]
                progress.update({"current_channel_id": str(cid), "current_message_id": mid})
                is_duplicate = (str(cid), mid) in duplicate_keys
                if is_duplicate and duplicate_action == "skip":
                    skipped_count += 1
                    progress.update({"done": progress["done"] + 1, "skipped": skipped_count})
                    results.append({"channel_id": str(cid), "message_id": mid,
                                    "status": "skipped", "reason": "already_sent"})
                    continue
                try:
                    result = await telegram_sender.send_video(
                        db, telegram.client, cid, mid, target_id, thumb_getter, premium=premium
                    )
                    sent_count += 1
                    progress.update({"done": progress["done"] + 1, "sent": sent_count})
                    notes = result.get("notes") or []
                    results.append({"channel_id": str(cid), "message_id": mid,
                                    "status": "sent", "sent_message_id": result.get("sent_message_id"),
                                    "thumb_message_id": result.get("thumb_message_id"), "notes": notes})
                    db.log(f"Bulk Send to Telegram succeeded {cid}/{mid} -> {target}" +
                           (f" ({'; '.join(str(x) for x in notes)})" if notes else ""))
                except Exception as e:
                    failed_count += 1
                    progress.update({"done": progress["done"] + 1, "failed": failed_count})
                    error = f"{type(e).__name__}: {e}"[:400]
                    results.append({"channel_id": str(cid), "message_id": mid,
                                    "status": "failed", "error": error})
                    db.log(f"Bulk Send to Telegram failed {cid}/{mid} -> {target}: {error}", "ERROR")
            progress.update({"active": False, "stage": "complete", "current_channel_id": None,
                             "current_message_id": None, "sent": sent_count,
                             "skipped": skipped_count, "failed": failed_count, "done": len(videos)})

        title = next((x["title"] for x in _chan_titles() if x["id"] == str(target)), target)
        if sent_count and not failed_count:
            db.log(
                f"{sent_count} video{'s' if sent_count != 1 else ''} sent to Telegram channel {title}",
                "SUCCESS", event="telegram.send.completed",
                details={"destination_title": title, "sent": sent_count, "skipped": skipped_count},
            )
        elif library_reconciled_count:
            db.log(
                f"{library_reconciled_count} previously sent video{'s' if library_reconciled_count != 1 else ''} "
                f"are now available in {title}",
                "SUCCESS", event="telegram.send.completed",
                details={"destination_title": title, "library_reconciled": library_reconciled_count},
            )
        return {
            "ok": True,
            "target": target,
            "target_title": title,
            "total": len(videos),
            "sent": sent_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "library_reconciled": library_reconciled_count,
            "results": results,
        }


    # ---------------- Backup & Restore ----------------
    from ..core.backup.manager import (backup_settings, save_backup_settings, list_backups,
                                       inspect_backup, create_backup, stage_restore, repository, PENDING_BACKUP_PASSWORDS)


    # ------------------------------------------------------------------
    # SUBSECTION: Backup and restore
    # Backup config and operations.
    # ------------------------------------------------------------------
    @router.get("/api/settings/backup")
    async def backup_status(request: Request):
        require_auth(request)
        return {"ok": True, "settings": backup_settings(db), "backups": await asyncio.to_thread(list_backups, db)}

    @router.post("/api/settings/backup")
    async def save_backup_config(request: Request):
        require_auth(request)
        data = await request.json()
        try:
            import re
            schedule_time = str(data.get("schedule_time") or "02:00")
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
                raise ValueError("Invalid backup schedule time")
            result = await asyncio.to_thread(save_backup_settings, db, data)
            db.set_setting("backup_schedule_time", schedule_time)
            result["schedule_time"] = schedule_time
            return {"ok": True, "settings": result}
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc))

    @router.post("/api/backup/create")
    async def backup_create(request: Request):
        require_auth(request)
        data = await request.json()
        if jobs.is_active("BACKUP_CREATE"):
            raise HTTPException(409, "A backup is already running — see Active jobs")
        backup_type = str(data.get("backup_type") or "full_application")
        password = str(data.get("password") or "").strip()
        if password and len(password) < 8:
            raise HTTPException(400, "Backup password must be at least 8 characters")
        detail = json.dumps({"backup_type": backup_type,
                             "repository": str(data.get("repository") or "").strip() or None})
        jid = db.create_job("BACKUP_CREATE", "", "", False, detail=detail)
        if password:
            PENDING_BACKUP_PASSWORDS[jid] = password
        jobs.submit(jid)
        return {"ok": True, "id": jid}

    @router.post("/api/backup/verify")
    async def backup_verify(request: Request):
        require_auth(request)
        data = await request.json()
        path = str(data.get("path") or "").strip()
        password = str(data.get("password") or "").strip() or None
        try:
            return await asyncio.to_thread(inspect_backup, path, password)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise HTTPException(400, str(exc))

    @router.post("/api/backup/restore/preview")
    async def backup_restore_preview(request: Request):
        require_auth(request)
        data = await request.json()
        path = str(data.get("path") or "").strip()
        password = str(data.get("password") or "").strip() or None
        try:
            info = await asyncio.to_thread(inspect_backup, path, password)
            active = db.active_jobs()
            return {"ok": True, "manifest": info["manifest"], "active_jobs": len(active),
                    "restore_requires_restart": True, "media_included": bool(info["manifest"].get("media_included"))}
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc))

    @router.post("/api/backup/restore")
    async def backup_restore(request: Request):
        require_auth(request)
        data = await request.json()
        path = str(data.get("path") or "").strip()
        password = str(data.get("password") or "").strip() or None
        if not path:
            raise HTTPException(400, "Backup path is required")
        active_jobs = db.active_jobs()
        if active_jobs:
            raise HTTPException(409, f"Pause or stop {len(active_jobs)} active job(s) before restoring Archive X")
        try:
            # Safety snapshot before staging the replacement. It is kept outside
            # the current data root and therefore survives the restart/rollback.
            await asyncio.to_thread(create_backup, db, backup_type="full_application", password=None)
            result = await asyncio.to_thread(stage_restore, db, path, password)
            db.log(f"Archive X restore staged from {path}; restart required", "WARN")
            return result
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(400, str(exc))

    @router.post("/api/backup/delete")
    async def backup_delete(request: Request):
        require_auth(request)
        data = await request.json()
        path = Path(str(data.get("path") or "").strip()).expanduser().resolve()
        repo = repository(db)
        try:
            path.relative_to(repo)
        except ValueError:
            raise HTTPException(400, "Backup must be inside the configured repository")
        if path.suffix.lower() != ".axbackup" or not path.is_file():
            raise HTTPException(404, "Backup not found")
        path.unlink()
        return {"ok": True}

    # ---------------- thumbnails ----------------
    # Cached thumbnails come straight from SQLite (even while Telegram is
    # reconnecting). Missing thumbnails are filled only by the shared
    # background prefetch service; page navigation never downloads media.
    from ..engines.thumbnail_cache import _thumb_ids_sync

    thumb_sem = asyncio.Semaphore(3)
    THUMB_HEADERS = {"Cache-Control": "private, max-age=604800"}

    # 1x1 transparent PNG used only for an expected cache miss. Returning a
    # successful image response prevents the browser from treating a normal
    # background-prefetch miss as a resource failure. The client reads the
    # explicit header and keeps its existing placeholder.
    THUMB_PENDING_PNG = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
    )


    # ------------------------------------------------------------------
    # SUBSECTION: Thumbnails
    # Serves thumbnails.
    # ------------------------------------------------------------------
    async def _thumb_bytes(channel_id: int, message_id: int):
        # Page rendering is cache-only. The shared resolver verifies that the
        # selected image is a valid Archive X snapshot; it never substitutes an
        # arbitrary Telegram cover and never starts network/FFmpeg work here.
        video_id, _ = await asyncio.to_thread(_thumb_ids_sync, db, channel_id, message_id)
        from ..engines.preview_resolver import resolve_cached_thumbnail
        hit = await asyncio.to_thread(resolve_cached_thumbnail, db, channel_id, video_id)
        if hit:
            return hit["data"], False, hit.get("mime") or "image/jpeg"
        return (THUMB_PENDING_PNG, True, "image/png")

    @router.get("/api/thumb/{channel_id}/{message_id}")
    async def serve_thumbnail(channel_id: int, message_id: int, request: Request):
        """Serve the Archive X-owned playback snapshot cache.

        Telegram is never contacted by this route. The Videos UI consumes the
        representative frame retained from the local 36-frame snapshot set.
        """
        require_auth(request)
        from fastapi.responses import Response
        data, pending, mime = await _thumb_bytes(channel_id, message_id)
        if not pending:
            from ..core.image_derivatives import derive, requested_width
            data, mime = await asyncio.to_thread(derive, data, mime, requested_width(request.query_params.get("w")))
        headers = dict(THUMB_HEADERS)
        if pending:
            headers["X-Thumbnail-Status"] = "pending"
            headers["Cache-Control"] = "private, max-age=5"
        return Response(data, media_type=mime, headers=headers)
