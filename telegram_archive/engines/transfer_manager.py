"""Unified Telegram transfer orchestration.

This module is the common seam for Local Upload and Telegram Media Manager.
The existing engines remain the canonical implementations for their source-
specific workflows; this facade unifies job routing, execution locking, and
future transfer UI without duplicating the media logic.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from . import uploader
from .transfer_progress import RollingTransferProgress, format_size
from ..media_manager.runner import RUNNER as media_manager
from .. import config

TRANSFER_JOB_TYPES = {"TELEGRAM_TRANSFER", "UPLOAD_LOCAL", "MEDIA_MANAGER"}




# ==============================================================================
# SECTION: Source lookup and job entry
# Transfer source lookup and job entry point.
# ==============================================================================

def transfer_source(job: dict[str, Any]) -> str:
    """Return the canonical source kind for a transfer job."""
    if str(job.get("job_type") or "") == "UPLOAD_LOCAL":
        return "local"
    if str(job.get("job_type") or "") == "MEDIA_MANAGER":
        return "telegram"
    try:
        detail = json.loads(job.get("detail") or "{}")
    except Exception:
        detail = {}
    source = str(detail.get("source") or detail.get("source_type") or "").strip().casefold()
    if source in {"local", "local_files", "upload_local"}:
        return "local"
    if source in {"telegram", "telegram_media", "media_manager"}:
        return "telegram"
    raise RuntimeError("Telegram Transfer Manager job is missing its source mode")


async def run(ctx):
    """Dispatch the unified preparation-first transfer workflow."""
    source = transfer_source(ctx.job)
    try:
        detail = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        detail = {}
    if hasattr(ctx, "db"):
        ctx.db.log(
            f"Telegram Transfer Manager: {source} workflow started; preparation={detail.get('preparation','METADATA_ASSETS_FIRST')}; "
            f"destination={detail.get('destination_channel') or ctx.job.get('destination_channel') or '—'}",
            "INFO",
            event="telegram_transfer.started",
            job_id=ctx.job_id,
            details={"parent_job_id": str(detail.get("parent_job_id") or "")} if detail.get("parent_job_id") else None,
        )
    try:
        workflow = str(detail.get("workflow") or "").strip().upper()
        if workflow == "PHOTO_CHANNEL_SCAN":
            from . import photo_channel_scanner
            result = await photo_channel_scanner.run(ctx)
        elif workflow == "PHOTO_RECONCILE":
            from . import photo_reconcile
            result = await photo_reconcile.run(ctx)
        elif workflow == "PHOTO_SCAN_AND_RECONCILE":
            from . import photo_channel_scanner, photo_reconcile
            scan_result = await photo_channel_scanner.run(ctx)
            reconcile_result = await photo_reconcile.run(ctx)
            result = {"scan": scan_result, "reconciliation": reconcile_result}
        elif workflow == "PHOTO_LOOK_TAGS":
            from . import photo_look_tags
            result = await photo_look_tags.run(ctx)
        elif workflow == "PHOTO_CONTENT_TAGS":
            from . import photo_content_tags
            result = await photo_content_tags.run(ctx)
        elif workflow == "PHOTO_FACE_ANALYSIS":
            from . import photo_face_clustering
            result = await photo_face_clustering.run(ctx)
        elif source == "local":
            if str(detail.get("media_type") or "VIDEO").strip().upper() == "PHOTOS":
                from . import photo_uploader
                result = await photo_uploader.run(ctx)
            elif str(detail.get("workflow") or "").strip().casefold() == "content_automation" and detail.get("prepared_video_path"):
                from ..operations.automation import resolve_destination
                video_path = Path(str(detail.get("prepared_video_path") or "")).expanduser()
                snapshot_path = Path(str(detail.get("prepared_snapshot_path") or "")).expanduser()
                destination = await resolve_destination(ctx.telegram.client, detail.get("destination_channel") or ctx.job.get("destination_channel"))
                video_path_name = str(detail.get("file_name") or video_path.name)
                try:
                    video_meta = json.loads(detail.get("video_meta_json") or "{}")
                except Exception:
                    video_meta = {}
                ctx.report(completed=0, total=2, current_item=video_path_name,
                           operation="Telegram Transfer · Video", force=True)
                video_id, thumb_id = await deliver_prepared_local_media(
                    ctx, destination=destination, video_path=video_path, snapshot_path=snapshot_path,
                    caption=str(detail.get("caption") or ""), video_meta=video_meta, file_name=video_path_name,
                    source_channel="content_automation", source_message_id=0,
                    source_media_id=int(detail.get("media_id") or 0),
                    identity_key=str(detail.get("identity_key") or ""), action="CONTENT_AUTOMATION",
                    split_upload=bool(detail.get("split_upload", False)),
                    oversized_file_policy=str(detail.get("oversized_file_policy") or ("split" if detail.get("split_upload") else "optimize")),
                )
                detail["result_video_id"] = int(video_id)
                detail["result_thumbnail_id"] = int(thumb_id)
                detail["result_status"] = "VERIFIED"
                ctx.db.update_job(ctx.job_id, detail=json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
                ctx.report(completed=2, total=2, progress=100.0, current_item=video_path_name,
                           operation="Telegram Transfer · Verified", force=True)
                result = {"video_id": video_id, "thumbnail_id": thumb_id}
            else:
                result = await uploader.run(ctx)
        else:
            result = await media_manager.run_job(ctx)
        if hasattr(ctx, "db"):
            ctx.db.log("Telegram Transfer Manager: workflow completed", "SUCCESS", event="telegram_transfer.completed", job_id=ctx.job_id,
                       details={"parent_job_id": str(detail.get("parent_job_id") or "")} if detail.get("parent_job_id") else None)
        return result
    except Exception as exc:
        if hasattr(ctx, "db"):
            ctx.db.log(f"Telegram Transfer Manager: workflow failed: {type(exc).__name__}: {exc}", "ERROR", event="telegram_transfer.failed", job_id=ctx.job_id,
                       details={"parent_job_id": str(detail.get("parent_job_id") or "")} if detail.get("parent_job_id") else None)
        raise




# ==============================================================================
# SECTION: Prepared local media delivery
# Delivery with checkpoints, heartbeats and progress.
# ==============================================================================

async def _deliver_prepared_local_media_impl(ctx, *, destination, video_path: Path, snapshot_path: Path,
                                       caption: str, video_meta: dict[str, Any], file_name: str,
                                       source_channel: str = "local", source_message_id: int = 0,
                                       source_media_id: int | None = None,
                                       identity_key: str = "", action: str = "TRANSFER",
                                       split_upload: bool = False, oversized_file_policy: str | None = None) -> tuple[int, int]:
    """Deliver local media with supervised Telegram RPCs and durable pair checkpoints.

    Each Telegram message ID is committed immediately after that send succeeds.
    If the app/watchdog stops between the video and contact sheet, a retry reuses
    the existing video rather than posting a duplicate. Long RPC waits continue to
    heartbeat the watchdog; actual video-upload bytes feed progress telemetry.
    """
    from .delivery_pipeline import (
        prepare_local_delivery_assets, send_local_video, publish_snapshot,
        verify_delivery, verify_split_delivery,
    )
    from .thumbnail_cache import _store_thumb_sync
    from .media_utils import is_video_message
    if not getattr(ctx.telegram, "client", None) or not ctx.telegram.connected:
        raise RuntimeError("Telegram is not connected")

    def _read_job_detail() -> dict[str, Any]:
        try:
            row = ctx.db.get_job(ctx.job_id) if hasattr(ctx.db, "get_job") else None
            raw = (row or {}).get("detail") if row else getattr(ctx, "job", {}).get("detail", "{}")
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def _save_checkpoint(stage: str, **values: Any) -> None:
        detail = _read_job_detail()
        detail.update(values)
        detail["transfer_stage"] = stage
        serialized = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
        # SQLite can be contended by diagnostics/library work. Persist it off-loop
        # so the transfer's heartbeat loop does not depend on the DB write lock.
        await asyncio.to_thread(ctx.db.update_job, ctx.job_id, detail=serialized)
        try:
            if isinstance(ctx.job, dict):
                ctx.job["detail"] = serialized
        except Exception:
            pass

    progress = {"last_report": 0.0, "last_progress": time.monotonic(), "started": time.monotonic(),
                "upload_pause_until": 0.0, "sent": 0, "total": 0, "last_logged_percent": -5,
                "completion_logged": False}
    rolling_meter = RollingTransferProgress(window_seconds=10.0, minimum_seconds=2.0)

    def _upload_progress(sent_bytes: int, total_bytes: int) -> None:
        """Report upload progress, speed and heartbeats to the job."""
        now = time.monotonic()
        total = max(0, int(total_bytes or 0))
        sent = max(0, int(sent_bytes or 0))
        if total:
            sent = min(sent, total)
        progress["sent"] = sent
        progress["total"] = total
        progress["last_progress"] = now
        final = bool(total and sent >= total)
        percent = int(100 * sent / total) if total else 0
        sample = rolling_meter.update(sent, total, now=now)
        rate_mib = float(sample["rate_mib_per_second"] or 0.0)
        eta_seconds = sample["eta_seconds"]
        eta_label = str(sample["eta_label"])
        operation = f"Telegram transfer · video upload {percent}% · {rate_mib:.1f} MB/s · ETA {eta_label}"
        if total and (percent >= int(progress.get("last_logged_percent", -5)) + 5 or final):
            progress["last_logged_percent"] = percent
            ctx.activity(
                f"Telegram video upload {percent}% · {sent:,}/{total:,} bytes · {rate_mib:.1f} MB/s · ETA {eta_label} · {file_name}",
                operation=operation, current_item=f"{file_name} · {percent}% · {rate_mib:.1f} MB/s · ETA {eta_label}",
                event="telegram_transfer.video_upload_progress",
                details={"filename": file_name, "percent": percent, "bytes_sent": sent,
                         "bytes_total": total, "bytes_remaining": max(0, total-sent),
                         "rate_mib_per_second": round(rate_mib, 2), "eta_seconds": eta_seconds}, progress=True,
            )
        else:
            ctx.report(current_item=f"{file_name} · upload {percent}% · {rate_mib:.1f} MB/s · ETA {eta_label}",
                       operation=operation)
        if final and not progress.get("completion_logged"):
            progress["completion_logged"] = True
            final_sample = rolling_meter.finish(sent, total)
            elapsed_total = max(0.001, float(final_sample["elapsed_seconds"]))
            average_rate = float(final_sample["average_mib_per_second"] or 0.0)
            ctx.db.log(
                f"Telegram upload complete: {file_name} — {format_size(total)} in {elapsed_total:.0f} s "
                f"({average_rate:.1f} MB/s)",
                "SUCCESS", event="telegram_transfer.video_upload_complete", job_id=ctx.job_id,
                details={"filename": file_name, "bytes_total": total, "elapsed_seconds": round(elapsed_total, 2),
                         "average_rate_mib_per_second": round(average_rate, 2)},
            )
        if not final and now - float(progress["last_report"]) < 0.75:
            return
        progress["last_report"] = now
        if getattr(ctx, "watchdog", None) is not None:
            ctx.watchdog.heartbeat(
                ctx.job_id, progress=True, activity=True,
                reason="telegram_video_upload_progress",
                metadata={"bytes_sent": sent, "bytes_total": total, "file_name": file_name},
            )
        if getattr(ctx, "pipeline", None) is not None:
            ctx.pipeline.heartbeat(activity=True)

    def _upload_status(message: str, wait_seconds: int = 0) -> None:
        now = time.monotonic()
        if wait_seconds > 0:
            # A server-directed wait is not a stalled upload. Let the job
            # heartbeat continue without triggering the no-progress watchdog.
            progress["upload_pause_until"] = max(float(progress.get("upload_pause_until", 0.0)), now + wait_seconds)
            progress["last_progress"] = max(float(progress.get("last_progress", now)), now + wait_seconds)
        ctx.report(current_item=f"{file_name} · {message}", operation=message, force=True)
        ctx.activity(
            message, operation=message, current_item=f"{file_name} · {message}",
            event="telegram_transfer.upload_wait" if wait_seconds > 0 else "telegram_transfer.upload_retry",
            details={"filename": file_name, "wait_seconds": int(wait_seconds or 0),
                     "bytes_sent": int(progress.get("sent", 0)), "bytes_total": int(progress.get("total", 0))},
            progress=True,
        )
        try:
            ctx.db.log(message, "WARN" if wait_seconds > 0 else "INFO",
                       event="telegram_transfer.upload_wait", job_id=ctx.job_id)
        except Exception:
            pass

    async def _await_with_heartbeat(awaitable, *, reason: str, no_progress_timeout: float | None = None,
                                    use_upload_progress: bool = False):
        task = asyncio.create_task(awaitable)
        started_at = time.monotonic()
        try:
            while not task.done():
                now = time.monotonic()
                if getattr(ctx, "watchdog", None) is not None:
                    ctx.watchdog.heartbeat(
                        ctx.job_id, progress=False, activity=True,
                        reason=reason,
                        metadata={
                            "file_name": file_name,
                            "bytes_sent": int(progress["sent"]),
                            "bytes_total": int(progress["total"]),
                        } if use_upload_progress else {"file_name": file_name},
                    )
                if getattr(ctx, "pipeline", None) is not None:
                    ctx.pipeline.heartbeat(activity=True)
                intentional_wait = now < float(progress.get("upload_pause_until", 0.0))
                if use_upload_progress and no_progress_timeout is not None and not intentional_wait and now - float(progress["last_progress"]) > no_progress_timeout:
                    raise TimeoutError(
                        f"Telegram upload made no byte-progress for {no_progress_timeout:.0f}s"
                    )
                if not use_upload_progress and no_progress_timeout is not None and now - started_at > no_progress_timeout:
                    raise TimeoutError(f"{reason} did not complete within {no_progress_timeout:.0f}s")
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                except asyncio.TimeoutError:
                    await ctx.checkpoint()
            return await task
        except BaseException:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _known_video_is_valid(message_id: int, split_data: dict | None = None) -> bool:
        if message_id <= 0:
            return False
        if isinstance(split_data, dict) and split_data.get("parts"):
            ok, _reason = await _await_with_heartbeat(
                verify_split_delivery(ctx.telegram.client, destination, split_data),
                reason="telegram_resume_split_checkpoint", no_progress_timeout=300,
            )
            return bool(ok)
        # Distinguish a definite missing/non-video message from a transient RPC
        # failure. A transient failure must bubble up and retry, not trigger a duplicate upload.
        message = await _await_with_heartbeat(
            ctx.telegram.client.get_messages(destination, ids=int(message_id)),
            reason="telegram_resume_video_checkpoint", no_progress_timeout=180,
        )
        return bool(message is not None and is_video_message(message))

    # Generate/reuse the prepared preview and contact sheet. This is intentionally
    # the v29.4.168 delivery path: no new Library snapshot/index migration is mixed in.
    ctx.activity(f"Starting transfer for {file_name}",
                 operation="Telegram transfer · validating source and destination", current_item=file_name,
                 event="telegram_transfer.started",
                 details={"filename": file_name, "video_path": str(video_path),
                          "file_size_bytes": int(Path(video_path).stat().st_size) if Path(video_path).is_file() else 0,
                          "destination": str(getattr(destination, "id", destination) or destination),
                          "metadata": dict(video_meta or {}), "caption": str(caption or "")[:2000]}, progress=True)
    representative, contact_sheet = await prepare_local_delivery_assets(
        ctx.db, video_path, video_meta=video_meta, ctx=ctx,
    )
    checkpoint = _read_job_detail()
    video_id = int(checkpoint.get("video_message_id") or 0)
    thumb_id = int(checkpoint.get("thumbnail_message_id") or 0)
    split_data = checkpoint.get("split_delivery") if isinstance(checkpoint.get("split_delivery"), dict) else None

    checkpoint_invalid = False
    if video_id and split_data:
        valid_split, split_reason = await _await_with_heartbeat(
            verify_split_delivery(ctx.telegram.client, destination, split_data),
            reason="telegram_resume_split_checkpoint", no_progress_timeout=300,
        )
        if not valid_split:
            from .delivery_pipeline import split_verification_is_definite_failure
            if split_verification_is_definite_failure(split_reason):
                try:
                    from ..core.split_files import finalize_split_group
                    await asyncio.to_thread(finalize_split_group, ctx.db, str(split_data.get("group_id") or ""), status="INCOMPLETE")
                except Exception:
                    pass
            checkpoint_invalid = True
    elif video_id and not await _known_video_is_valid(video_id, None):
        checkpoint_invalid = True
    if checkpoint_invalid:
        # Hide an already-registered group if a confirmed missing/mismatched
        # volume is detected. The local source remains untouched for re-upload.
        video_id = 0
        thumb_id = 0
        split_data = None
        await _save_checkpoint("READY_TO_UPLOAD", video_message_id=0, thumbnail_message_id=0, split_delivery=None)

    if video_id and thumb_id:
        ctx.activity(f"Verifying saved Telegram delivery checkpoint for {file_name}",
                     operation="Telegram transfer · validate saved delivery", current_item=file_name,
                     event="telegram_transfer.saved_checkpoint_verification_started",
                     details={"filename": file_name, "video_message_id": video_id, "contact_sheet_message_id": thumb_id}, progress=True)
        if split_data:
            ok, reason = await _await_with_heartbeat(
                verify_split_delivery(ctx.telegram.client, destination, split_data),
                reason="telegram_resume_split_verification", no_progress_timeout=300,
            )
            saved_thumb = await _await_with_heartbeat(
                ctx.telegram.client.get_messages(destination, ids=thumb_id),
                reason="telegram_resume_split_thumbnail_verification", no_progress_timeout=180,
            ) if ok else None
            if ok and saved_thumb is None:
                ok, reason = False, "Saved split upload contact sheet is missing"
            if ok and thumb_id <= max(int(part.get("message_id") or 0) for part in split_data.get("parts", [])):
                ok, reason = False, "Contact sheet is not after all split volumes"
        else:
            ok, reason = await _await_with_heartbeat(
                verify_delivery(ctx.telegram.client, destination, video_id, thumb_id, expected_name=file_name),
                reason="telegram_resume_pair_verification", no_progress_timeout=180,
            )
        if not ok:
            # Keep the video checkpoint if it is still real. Only the thumbnail
            # should be replaced when the saved pair is invalid.
            thumb_id = 0
            await _save_checkpoint("VIDEO_SENT", video_message_id=video_id, thumbnail_message_id=0,
                                   split_delivery=split_data, last_verification_error=str(reason)[:500])

    sent_message = None
    if not video_id:
        ctx.activity(f"Uploading video to Telegram: {file_name}",
                     operation="Telegram transfer · video upload starting", current_item=file_name,
                     event="telegram_transfer.video_upload_started",
                     details={"filename": file_name, "file_size_bytes": int(Path(video_path).stat().st_size),
                              "video_path": str(video_path), "metadata": dict(video_meta or {})}, progress=True)
        _upload_started = time.monotonic()
        rolling_meter = RollingTransferProgress(window_seconds=10.0, minimum_seconds=2.0)
        progress.update({"last_progress": _upload_started, "started": _upload_started,
                         "upload_pause_until": 0.0, "sent": 0, "total": 0, "last_logged_percent": -5,
                         "completion_logged": False})
        sent = await _await_with_heartbeat(
            send_local_video(
                ctx.telegram.client, destination, video_path, contact_sheet,
                caption=caption, video_meta=video_meta, file_name=file_name,
                progress_callback=_upload_progress, status_callback=_upload_status,
                split_upload=bool(split_upload),
                oversized_file_policy=oversized_file_policy,
            ),
            reason="telegram_video_upload_wait", no_progress_timeout=None, use_upload_progress=True,
        )
        from ..core.split_files import SplitDeliveryResult
        split_data = sent.to_dict() if isinstance(sent, SplitDeliveryResult) else None
        sent_message = sent.primary_message if isinstance(sent, SplitDeliveryResult) else sent
        video_id = int(getattr(sent_message, "id", 0) or 0)
        if not video_id:
            raise RuntimeError("Telegram accepted the video but returned no message ID")
        # Persist the fully verified volume sequence before uploading its contact sheet.
        await _save_checkpoint("VIDEO_SENT", video_message_id=video_id, thumbnail_message_id=0,
                               split_delivery=split_data)
        ctx.activity(f"Telegram video accepted; message ID {video_id}",
                     operation="Telegram transfer · video message checkpoint saved", current_item=file_name,
                     event="telegram_transfer.video_message_saved",
                     details={"filename": file_name, "video_message_id": video_id,
                              "checkpoint": "VIDEO_SENT", "contact_sheet_message_id": 0}, progress=True)
        await ctx.checkpoint()

    if not thumb_id:
        ctx.activity(f"Uploading 6×6 contact sheet for {file_name}",
                     operation="Telegram transfer · contact sheet upload starting", current_item=file_name,
                     event="telegram_transfer.contact_sheet_upload_started",
                     details={"filename": f"{Path(file_name).stem}.jpg", "contact_sheet_path": str(contact_sheet),
                              "contact_sheet_bytes": int(contact_sheet.stat().st_size),
                              "frames": 36, "rows": 6, "columns": 6}, progress=True)
        thumb = await _await_with_heartbeat(
            publish_snapshot(
                ctx.telegram.client, destination, contact_sheet,
                file_name=f"{Path(file_name).stem}.jpg",
            ),
            reason="telegram_contact_sheet_upload_wait", no_progress_timeout=300,
        )
        thumb_id = int(getattr(thumb, "id", 0) or 0)
        if not thumb_id:
            raise RuntimeError("Telegram accepted the contact sheet but did not return a message ID")
        await _save_checkpoint("PAIR_SENT", video_message_id=video_id, thumbnail_message_id=thumb_id)
        ctx.activity(f"Contact sheet accepted; message ID {thumb_id}",
                     operation="Telegram transfer · contact sheet checkpoint saved", current_item=file_name,
                     event="telegram_transfer.contact_sheet_message_saved",
                     details={"filename": f"{Path(file_name).stem}.jpg", "contact_sheet_message_id": thumb_id,
                              "video_message_id": video_id, "checkpoint": "PAIR_SENT"}, progress=True)

    ctx.activity(f"Verifying destination video/contact-sheet pair for {file_name}",
                 operation="Telegram transfer · destination verification", current_item=file_name,
                 event="telegram_transfer.destination_verification_started",
                 details={"filename": file_name, "video_message_id": video_id,
                          "contact_sheet_message_id": thumb_id}, progress=True)
    if split_data:
        ok, reason = await _await_with_heartbeat(
            verify_split_delivery(ctx.telegram.client, destination, split_data),
            reason="telegram_split_delivery_verification_wait", no_progress_timeout=300,
        )
        verified_thumb = await _await_with_heartbeat(
            ctx.telegram.client.get_messages(destination, ids=thumb_id),
            reason="telegram_split_thumbnail_verification_wait", no_progress_timeout=180,
        ) if ok else None
        if ok and verified_thumb is None:
            ok, reason = False, "Telegram split-upload contact sheet could not be found"
        if ok and thumb_id <= max(int(part.get("message_id") or 0) for part in split_data.get("parts", [])):
            ok, reason = False, "Contact sheet must follow every split volume"
    else:
        ok, reason = await _await_with_heartbeat(
            verify_delivery(ctx.telegram.client, destination, video_id, thumb_id, expected_name=file_name),
            reason="telegram_delivery_verification_wait", no_progress_timeout=180,
        )
    if not ok:
        raise RuntimeError(f"Destination verification failed: {reason}")
    if thumb_id <= video_id:
        raise RuntimeError(f"Destination verification failed: thumbnail message {thumb_id} is not after video message {video_id}")
    await _save_checkpoint("VERIFIED", video_message_id=video_id, thumbnail_message_id=thumb_id,
                           split_delivery=split_data, result_video_id=video_id,
                           result_thumbnail_id=thumb_id, result_status="VERIFIED")
    ctx.activity(f"Telegram delivery verified for {file_name}",
                 operation="Telegram transfer · destination pair verified", current_item=file_name,
                 event="telegram_transfer.destination_verification_completed",
                 details={"filename": file_name, "video_message_id": video_id,
                          "contact_sheet_message_id": thumb_id, "verification": "VERIFIED"}, progress=True)

    from ..core.telegram_identity import canonical_peer_id
    destination_key = canonical_peer_id(destination)
    await asyncio.to_thread(
        _store_thumb_sync, ctx.db, destination_key, video_id, thumb_id,
        "image/jpeg", 0, 0, representative.read_bytes(), "playback-snapshot", False,
    )
    ctx.activity(f"Persisting verified-delivery ledger and Library registration for {file_name}",
                 operation="Telegram transfer · persist delivery ledger", current_item=file_name,
                 event="telegram_transfer.delivery_ledger_started",
                 details={"filename": file_name, "destination_channel": destination_key,
                          "video_message_id": video_id, "contact_sheet_message_id": thumb_id,
                          "metadata": dict(video_meta or {})}, progress=True)
    if split_data:
        from ..core.split_files import store_split_group
        store_split_group(
            ctx.db, group_id=str(split_data.get("group_id") or ""), destination_channel=destination_key,
            logical_filename=str(split_data.get("logical_filename") or file_name),
            primary_message_id=video_id, thumbnail_message_id=thumb_id,
            total_size=int(split_data.get("total_size") or 0), parts=list(split_data.get("parts") or []),
            metadata={**dict(video_meta or {}), "mime_type": str(split_data.get("mime_type") or "application/octet-stream"), "caption": caption},
            identity_key=str(identity_key or ""), status="PREPARING",
        )
    try:
        from .delivery_ledger import record_verified_delivery
        delivery_record = await asyncio.to_thread(
            record_verified_delivery, ctx.db,
            destination_channel=destination_key, destination_video_message_id=video_id,
            destination_thumbnail_message_id=thumb_id,
            source_channel=str(source_channel or "local"), source_message_id=int(source_message_id or 0),
            source_media_id=source_media_id, identity_key=str(identity_key or ""),
            filename=str(file_name or Path(video_path).name), file_path=video_path,
            file_size=int(Path(video_path).stat().st_size),
            duration=float(video_meta.get("duration") or 0), width=int(video_meta.get("width") or 0),
            height=int(video_meta.get("height") or 0), fps=float(video_meta.get("fps") or 0),
            bitrate=int(video_meta.get("bitrate") or 0),
            codec=str(video_meta.get("video_codec") or video_meta.get("codec") or ""),
            audio_codec=str(video_meta.get("audio_codec") or ""), caption=caption, action=str(action or "TRANSFER"),
        )
    except Exception as exc:
        raise RuntimeError(f"Verified transfer could not be recorded in delivery history and Library: {type(exc).__name__}: {exc}") from exc
    # Persist the destination's own Telegram file reference and playback caches.
    # The index was built before the upload from the exact remuxed/uploaded file.
    try:
        if not split_data:
            from .delivery_pipeline import register_verified_playback_assets
            await register_verified_playback_assets(
                ctx.db, ctx.telegram.client, destination, destination_key, video_id,
                source_channel=str(source_channel or "local"), source_message_id=int(source_message_id or 0),
                file_path=video_path, original_oshash=str(identity_key or ""), video_meta=video_meta,
                destination_message=sent_message,
            )
    except Exception as asset_exc:
        ctx.db.log(f"Verified transfer cache registration incomplete for {file_name}: {type(asset_exc).__name__}: {asset_exc}", "WARN")
    # Reconcile the destination caption by its known video message ID. This is
    # the same message-based service used for scanned channel videos; it does
    # not scan channel history or upload the video again.
    try:
        from .caption_sync import sync_verified_delivery_caption
        library_media_id = int(delivery_record.get("library_media_id") or 0)
        if library_media_id:
            await sync_verified_delivery_caption(ctx.db, ctx.telegram, library_media_id)
    except Exception as caption_exc:
        ctx.db.log(
            f"Verified delivery for {file_name} is saved; caption reconciliation will need retry: "
            f"{type(caption_exc).__name__}: {caption_exc}", "WARN"
        )

    # Populate the unified registry from the verified destination message. This
    # is metadata-only (no media download) and lets later scans recognize this
    # upload by Telegram document ID without rebuilding its prepared assets.
    try:
        from ..playback.stream_server import make_record
        from ..core.telegram_registry import register_known_message, mark_prepared, advance_channel_high_water
        destination_message = await ctx.telegram.client.get_messages(destination, ids=video_id)
        telegram_record = make_record(destination_message) if destination_message else None
        if not telegram_record:
            telegram_record = {
                "id": video_id, "name": file_name, "size": int(Path(video_path).stat().st_size),
                "duration": float(video_meta.get("duration") or 0),
                "width": int(video_meta.get("width") or 0), "height": int(video_meta.get("height") or 0),
                "mime": "video/mp4", "caption": caption,
            }
        telegram_record["oshash"] = str(identity_key or "")
        telegram_record["phash"] = str(video_meta.get("video_phash") or video_meta.get("phash") or "")
        library_media_id = int(delivery_record.get("library_media_id") or 0)
        register_known_message(
            ctx.db, channel_id=destination_key, message_id=video_id, record=telegram_record,
            media_id=library_media_id or None, thumbnail_message_id=thumb_id,
        )
        mark_prepared(
            ctx.db, channel_id=destination_key, message_id=video_id,
            prepared={
                "source": "verified_local_delivery", "media_id": library_media_id or None,
                "probe": dict(video_meta or {}), "uploaded_file": str(Path(video_path).name),
                "prepared_assets": (["36_snapshots", "sprite", "contact_sheet", "probe_details", "split_file_group"] if split_data else ["36_snapshots", "sprite", "contact_sheet", "mp4_index", "keyframe_index", "probe_details"]),
                "contact_sheet_message_id": thumb_id,
                **({"split_group_id": str(split_data.get("group_id") or ""), "logical_filename": str(split_data.get("logical_filename") or file_name), "split_part_count": len(split_data.get("parts") or [])} if split_data else {}),
            }, oshash=str(identity_key or ""),
            phash=str(video_meta.get("video_phash") or video_meta.get("phash") or ""),
        )
        with ctx.db.lock:
            ctx.db.conn.execute(
                "INSERT OR IGNORE INTO channels(telegram_id,title,username,updated_at) VALUES(?,?,?,?)",
                (destination_key, str(getattr(destination, "title", None) or getattr(destination, "username", None) or f"Channel {destination_key}"),
                 str(getattr(destination, "username", None) or ""), config.now_iso()),
            )
            ctx.db.conn.commit()
        advance_channel_high_water(ctx.db, destination_key, video_id, status="DELIVERY_REGISTERED")
    except Exception as registry_exc:
        ctx.db.log(
            f"Verified delivery for {file_name} succeeded, but Telegram document registry reconciliation needs retry: "
            f"{type(registry_exc).__name__}: {registry_exc}", "WARN"
        )

    if split_data:
        from ..core.split_files import finalize_split_group
        finalize_split_group(ctx.db, str(split_data.get("group_id") or ""), status="VERIFIED")

    ctx.activity(f"Verified delivery is registered for {file_name}",
                 operation="Telegram transfer · delivery ledger committed", current_item=file_name,
                 event="telegram_transfer.delivery_ledger_completed",
                 details={"filename": file_name, "destination_channel": destination_key,
                          "video_message_id": video_id, "contact_sheet_message_id": thumb_id}, progress=True)
    try:
        from .thumbnail_cache import cleanup_local_delivery_artifacts
        cleanup_local_delivery_artifacts(video_path)
    except Exception as cleanup_exc:
        try:
            ctx.db.log(
                f"Verified Telegram transfer but contact-sheet cleanup failed for {Path(file_name).name}: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}", "WARN"
            )
        except Exception:
            pass
    ctx.activity(f"Telegram transfer completed for {file_name}",
                 operation="Telegram transfer · completed and verified", current_item=file_name,
                 event="telegram_transfer.completed",
                 details={"filename": file_name, "video_message_id": video_id,
                          "contact_sheet_message_id": thumb_id, "status": "VERIFIED"}, progress=True)
    return video_id, thumb_id


async def deliver_prepared_local_media(ctx, *, destination, video_path: Path, snapshot_path: Path,
                                       caption: str, video_meta: dict[str, Any], file_name: str,
                                       source_channel: str = "local", source_message_id: int = 0,
                                       source_media_id: int | None = None, identity_key: str = "",
                                       action: str = "TRANSFER", split_upload: bool = False,
                                       oversized_file_policy: str | None = None) -> tuple[int, int]:
    """Serialize same-source deliveries while allowing other lanes to progress."""
    from ..core.video_locks import video_operation_lock
    async with video_operation_lock(identity_key=identity_key, source_channel=source_channel,
                                   source_message_id=source_message_id, file_path=video_path):
        return await _deliver_prepared_local_media_impl(
            ctx, destination=destination, video_path=video_path, snapshot_path=snapshot_path,
            caption=caption, video_meta=video_meta, file_name=file_name,
            source_channel=source_channel, source_message_id=source_message_id,
            source_media_id=source_media_id, identity_key=identity_key, action=action,
            split_upload=split_upload, oversized_file_policy=oversized_file_policy,
        )
