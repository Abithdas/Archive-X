"""Canonical Telegram delivery facade used by Operations workflows.

This module owns workflow-level delivery composition while retaining the
existing tested Telegram sender/media utility implementations as adapters.
It is deliberately small so Local Upload, Content Automation, and future
migration workflows share the same delivery primitives.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Prepared file inspection
#   - Preparing and sending
#   - Verification and publication
# ------------------------------------------------------------------
from __future__ import annotations
from ..core.swallowed import note as _swallowed_note
from pathlib import Path
import tempfile
from .telegram_sender import send_local_video_with_cover, send_split_local_video
import asyncio
import hashlib
import json
from .media_utils import upload_thumbnail_document, verify_destination_pair
from ..core.upload_limits import get_upload_policy



# ==============================================================================
# SECTION: Prepared file inspection
# Validates prepared files and stores delivery metadata.
# ==============================================================================

def _stat_regular_file(path: Path):
    """Return a stat result for a non-empty regular file or None, off-loop-safe."""
    try:
        if not path.is_file():
            return None
        stat = path.stat()
        return stat if int(stat.st_size) > 0 else None
    except OSError:
        return None


def _inspect_prepared_delivery_file(path: Path, manifest_root: Path):
    """Do local stat/manifest I/O off the async workflow's event loop."""
    try:
        if not path.is_file():
            return None
        stat = path.stat()
        token = f"{str(path.resolve(strict=False)).casefold()}|{stat.st_size}|{stat.st_mtime_ns}"
        key = hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()
        manifest_path = manifest_root / f"{key}.json"
        prepared = None
        if manifest_path.is_file():
            try:
                prepared = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (int(prepared.get("size", -1)) != int(stat.st_size)
                        or int(prepared.get("mtime_ns", -1)) != int(stat.st_mtime_ns)):
                    prepared = None
            except Exception:
                prepared = None
        return stat, manifest_path, prepared
    except OSError:
        return None

def _read_source_media_details(db, source_channel: str, source_message_id: int):
    with db.lock:
        return db.conn.execute(
            "SELECT uploaded_oshash,uploaded_probe_json,video_phash,oshash,width,height,duration,fps,bitrate,codec,audio_codec,size,streamable FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
            (str(source_channel or ""), int(source_message_id or 0)),
        ).fetchone()

def _store_verified_delivery_metadata(db, uploaded_hash: str, probe_json: str, phash: str, channel: str, message_id: int):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with db.lock:
        db.conn.execute(
            "UPDATE media SET uploaded_oshash=COALESCE(NULLIF(?,''),uploaded_oshash),uploaded_probe_json=COALESCE(NULLIF(?,''),uploaded_probe_json),video_phash=COALESCE(NULLIF(?,''),video_phash),updated_at=? WHERE source_channel=? AND source_message_id=?",
            (uploaded_hash, probe_json, phash, now, channel, message_id),
        )
        db.conn.execute(
            "UPDATE resource_state SET catalogue_state='COMPLETE',fingerprint_state=CASE WHEN COALESCE((SELECT oshash FROM media WHERE id=resource_state.media_id),'')<>'' THEN 'COMPLETE' ELSE fingerprint_state END,last_reason=CASE WHEN COALESCE(last_reason,'')='' THEN 'VERIFIED_DELIVERY_PREPARED' ELSE last_reason END,updated_at=? WHERE media_id=(SELECT id FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1)",
            (now, channel, message_id),
        )
        db.conn.commit()



# ==============================================================================
# SECTION: Preparing and sending
# Asset preparation and local video send.
# ==============================================================================

async def prepare_local_delivery_assets(db, video_path: Path, *, source_oshash: str | None = None,
                                        video_meta: dict | None = None, force: bool = False, ctx=None,
                                        display_filename: str | None = None):
    """Prepare the canonical reusable frames and the separate Telegram delivery sheet.

    This is the shared local-file preparation stage for manual uploads, Content
    Automation/torrent downloads, and Telegram transfers that have been staged
    to a local file. The source adapter is responsible only for making a complete
    readable video file available.
    """
    from .thumbnail_cache import ensure_local_playback_assets, ensure_local_playback_contact_sheet
    video_path = Path(video_path)
    source_stat = await asyncio.to_thread(_stat_regular_file, video_path)
    if source_stat is None:
        raise RuntimeError(f"Local video source is missing or empty: {video_path}")
    if ctx is not None:
        ctx.activity(f"Preparing snapshot assets for {video_path.name}",
                     operation="Transfer preparation · snapshots and contact sheet", current_item=video_path.name,
                     event="telegram_transfer.assets_prepare_started",
                     details={"source_filename": video_path.name, "source_path": str(video_path),
                              "source_bytes": int(source_stat.st_size),
                              "metadata": dict(video_meta or {})}, progress=True)
    if ctx is None:
        representative = await ensure_local_playback_assets(
            db, video_path, source_oshash, metadata=video_meta, force=force,
        )
        contact_sheet = await ensure_local_playback_contact_sheet(
            db, video_path, source_oshash, metadata=video_meta, force=force,
            display_filename=display_filename,
        )
    else:
        representative = await ensure_local_playback_assets(
            db, video_path, source_oshash, metadata=video_meta, force=force, ctx=ctx,
        )
        contact_sheet = await ensure_local_playback_contact_sheet(
            db, video_path, source_oshash, metadata=video_meta, force=force, ctx=ctx,
            display_filename=display_filename,
        )
    for label, asset_path in (("representative snapshot", representative), ("36-frame contact sheet", contact_sheet)):
        asset_stat = await asyncio.to_thread(_stat_regular_file, Path(asset_path))
        if asset_stat is None:
            raise RuntimeError(f"Archive X {label} is unavailable for {video_path.name}")
    # The reusable individual-frame tree travels with the destination media
    # identity. Validate it and write the receipt + grid sprite before sending;
    # copying the directory later then gives the destination a ready grid without
    # any follow-up Telegram scan or frame rebuild.
    from .thumbnail_cache import local_playback_snapshot_frames_path
    from .preview_prefetch import frame_quality_sync, save_frame_validation_sync, build_grid_sprite_sync, FRAME_COUNT
    frames_dir = local_playback_snapshot_frames_path(video_path)
    quality = await asyncio.to_thread(frame_quality_sync, frames_dir)
    if not quality.get("ready") or int(quality.get("frame_count") or 0) != FRAME_COUNT or quality.get("corrupt_frames"):
        raise RuntimeError(f"Archive X snapshot validation failed for {video_path.name}: {quality.get('frame_count', 0)}/{FRAME_COUNT} valid frames")
    if not await asyncio.to_thread(save_frame_validation_sync, frames_dir, quality):
        raise RuntimeError(f"Archive X could not save the 36-frame validation receipt for {video_path.name}")
    if not await asyncio.to_thread(build_grid_sprite_sync, frames_dir):
        raise RuntimeError(f"Archive X could not build the 36-frame grid sprite for {video_path.name}")
    # Precompute every local-file-derived playback asset before Telegram send:
    # the MP4 index and startup segments are keyed to this exact file (post-remux),
    # while OSHASH remains the caller's original-source matching key.
    try:
        from ..core.playback_index_cache import prepare_local_file_index
        from ..metadata.fingerprint import oshash_file
        from ..metadata.perceptual import stash_video_phash_from_snapshots
        from .thumbnail_cache import local_playback_snapshot_frames_path
        upload_hash = await asyncio.to_thread(oshash_file, str(video_path))
        frames_dir = local_playback_snapshot_frames_path(video_path)
        phash = await asyncio.to_thread(
            stash_video_phash_from_snapshots, frames_dir,
            float((video_meta or {}).get("duration") or 0),
        )
        index_stage = await asyncio.to_thread(prepare_local_file_index, video_path)
        stat = video_path.stat()
        token = f"{str(video_path.resolve(strict=False)).casefold()}|{stat.st_size}|{stat.st_mtime_ns}"
        key = hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()
        manifest_root = Path(__import__("telegram_archive.config", fromlist=["PLAYER_CACHE_DIR"]).PLAYER_CACHE_DIR) / "prepared_delivery_assets"
        manifest_root.mkdir(parents=True, exist_ok=True)
        prepared_manifest = {
            "path": str(video_path.resolve(strict=False)), "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns), "uploaded_oshash": str(upload_hash or ""),
            "video_phash": str(phash or ""), "video_meta": dict(video_meta or {}),
            "index_stage": index_stage or {}, "prepared_at": __import__("time").time(),
        }
        (manifest_root / f"{key}.json").write_text(json.dumps(prepared_manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except Exception as prepare_exc:
        # Index parsing can fail for a file whose container isn't supported by
        # the fast indexer; the already validated frames still permit delivery.
        if ctx is not None:
            try:
                ctx.db.log(f"Local delivery playback precompute incomplete for {video_path.name}: {type(prepare_exc).__name__}: {prepare_exc}", "WARN")
            except Exception:
                _swallowed_note("delivery_pipeline.prepare_local_delivery_assets")
                pass
    if ctx is not None:
        ctx.activity(f"Transfer assets verified for {video_path.name}",
                     operation="Transfer preparation · assets verified", current_item=video_path.name,
                     event="telegram_transfer.assets_prepare_completed",
                     details={"source_filename": video_path.name, "representative_path": str(representative),
                              "representative_bytes": int(Path(representative).stat().st_size),
                              "contact_sheet_path": str(contact_sheet),
                              "contact_sheet_bytes": int(Path(contact_sheet).stat().st_size),
                              "snapshot_frames": 36, "contact_sheet_rows": 6, "contact_sheet_columns": 6,
                              "metadata": dict(video_meta or {})}, progress=True)
    return Path(representative), Path(contact_sheet)


async def send_local_video(client, destination, video_path: Path, snapshot_path: Path, *,
                           caption: str, video_meta: dict, file_name: str, progress_callback=None,
                           status_callback=None, split_upload: bool = False,
                           oversized_file_policy: str | None = None,
                           split_group_id: str | None = None, part_callback=None,
                           message_random_id: int | None = None,
                           baseline_message_id: int | None = None):
    """Send a local video according to the chosen oversized-file policy.

    Optimize creates a temporary re-encoded copy and never overwrites the source;
    Split uses verified numbered volumes; Skip is normally handled by the owning
    workflow before delivery and is rejected here as a final safety guard.
    """
    from ..core.upload_limits import normalize_oversized_file_policy, human_size
    from .telegram_sender import send_split_local_video
    policy = await get_upload_policy(client)
    size = int(Path(video_path).stat().st_size)
    mode = normalize_oversized_file_policy(
        oversized_file_policy, legacy_split_upload=bool(split_upload), default="optimize"
    )

    async def _send(source_path: Path):
        thumb = snapshot_path.read_bytes()
        sent = await send_local_video_with_cover(
            client, destination, source_path, thumb,
            caption=caption, progress_callback=progress_callback, status_callback=status_callback,
            supports_streaming=True, video_meta=video_meta, file_name_override=file_name,
            message_random_id=message_random_id,
            baseline_message_id=baseline_message_id,
        )
        return sent[0] if isinstance(sent, (list, tuple)) else sent

    if size <= int(policy.safe_limit_bytes):
        return await _send(Path(video_path))
    if mode == "skip":
        raise RuntimeError(
            f"Oversized-file policy is Skip: {Path(video_path).name} is {human_size(size)}, "
            f"above the {human_size(policy.safe_limit_bytes)} safe upload limit."
        )
    if mode == "split":
        return await send_split_local_video(
            client, destination, video_path, caption=caption, file_name=file_name,
            progress_callback=progress_callback, status_callback=status_callback,
            group_id=split_group_id, part_callback=part_callback,
        )

    from .uploader import ffmpeg_tools, probe
    from ..core.size_optimizer import optimize_for_limit
    ffmpeg, ffprobe = await asyncio.to_thread(ffmpeg_tools)
    if not ffmpeg:
        raise RuntimeError(
            f"{Path(video_path).name} exceeds the {human_size(policy.safe_limit_bytes)} safe upload limit "
            "and FFmpeg is unavailable for the Optimize policy. Choose Split or install FFmpeg."
        )
    async def _optimization_progress(message: str) -> None:
        if callable(status_callback):
            value = status_callback(message, 0)
            if hasattr(value, "__await__"):
                await value

    with tempfile.TemporaryDirectory(prefix="archive_x_oversized_optimize_") as temp_dir:
        optimized_path, result = await optimize_for_limit(
            ffmpeg, Path(video_path), Path(temp_dir), int(policy.safe_limit_bytes), probe, ffprobe=ffprobe,
            report=_optimization_progress if callable(status_callback) else None,
        )
        optimized_size = int(optimized_path.stat().st_size)
        if optimized_size <= 0 or optimized_size > int(policy.safe_limit_bytes):
            raise RuntimeError(
                f"Optimization did not produce a file within the safe upload limit: "
                f"{human_size(optimized_size)} > {human_size(policy.safe_limit_bytes)}"
            )
        if callable(status_callback):
            value = status_callback(
                f"Optimized {Path(video_path).name} to {human_size(optimized_size)} "
                f"using {result.get('encoder', 'FFmpeg')}; original preserved.", 0
            )
            if hasattr(value, "__await__"):
                await value
        return await _send(optimized_path)




# ==============================================================================
# SECTION: Verification and publication
# Verifies delivery and registers playback assets.
# ==============================================================================

def split_verification_is_definite_failure(reason: str) -> bool:
    """Distinguish successful verification and transient RPC failures from corruption."""
    value = str(reason or "").strip().lower()
    if not value or value.startswith("all split volumes are present and verified"):
        return False
    return not value.startswith("could not verify split volumes:")


async def verify_split_delivery(client, destination, split_data: dict) -> tuple[bool, str]:
    """Re-fetch every uploaded volume and verify ordered names, sizes, and total bytes."""
    try:
        data = dict(split_data or {})
        logical_name = Path(str(data.get("logical_filename") or "")).name
        parts = list(data.get("parts") or [])
        total_size = int(data.get("total_size") or 0)
        if not logical_name or not parts or total_size <= 0:
            return False, "Split upload checkpoint is missing its logical filename, parts, or total size"
        parts.sort(key=lambda p: int(p.get("part_index") or 0))
        indices = [int(part.get("part_index") or 0) for part in parts]
        if indices != list(range(1, len(parts) + 1)):
            return False, "Split upload checkpoint has missing or duplicate volume numbers"
        sum_sizes = 0
        for part in parts:
            index = int(part["part_index"])
            message_id = int(part.get("message_id") or 0)
            saved_name = Path(str(part.get("telegram_filename") or "")).name
            # Playable parts (Movie.part01.mp4) and older raw volumes (Movie.mp4.001)
            # are both valid; this check used to accept only the old scheme, so
            # every current split upload failed verification after uploading.
            from ..core.split_files import split_part_expected_name
            expected_name = split_part_expected_name(logical_name, index, saved_name)
            expected_size = int(part.get("size_bytes") or 0)
            if message_id <= 0 or expected_size <= 0 or saved_name != expected_name:
                return False, f"Invalid split volume checkpoint for {expected_name}"
            message = await client.get_messages(destination, ids=message_id)
            if message is None:
                return False, f"Telegram split volume is missing: {expected_name} (message {message_id})"
            file_obj = getattr(message, "file", None)
            actual_name = Path(str(getattr(file_obj, "name", "") or "")).name
            actual_size = int(getattr(file_obj, "size", 0) or 0)
            if actual_name != expected_name or actual_size != expected_size:
                return False, (
                    f"Telegram split volume mismatch for {expected_name}: "
                    f"name={actual_name!r}, size={actual_size}, expected={expected_size}"
                )
            sum_sizes += actual_size
        if sum_sizes != total_size:
            return False, f"Split volume byte total {sum_sizes} does not match original size {total_size}"
        return True, "All split volumes are present and verified"
    except Exception as exc:
        return False, f"Could not verify split volumes: {type(exc).__name__}: {exc}"

async def publish_snapshot(client, destination, snapshot_path: Path, *, file_name: str | None = None,
                           message_random_id: int | None = None,
                           baseline_message_id: int | None = None,
                           progress_callback=None):
    if message_random_id is not None:
        from .telegram_sender import send_local_thumbnail_document
        return await send_local_thumbnail_document(
            client, destination, snapshot_path,
            file_name=str(file_name or Path(snapshot_path).name),
            message_random_id=int(message_random_id),
            baseline_message_id=baseline_message_id,
            progress_callback=progress_callback,
        )
    sent = await upload_thumbnail_document(client, destination, snapshot_path, file_name=file_name)
    return sent[0] if isinstance(sent, (list, tuple)) else sent

async def verify_delivery(client, destination, video_id: int, snapshot_id: int, *, expected_name: str):
    return await verify_destination_pair(client, destination, int(video_id), int(snapshot_id),
        expected_video_name=expected_name, require_native_video=True,
        require_native_cover=True)


async def register_verified_playback_assets(db, client, destination, destination_channel: str | int,
                                            destination_message_id: int, *,
                                            source_channel: str | int = "", source_message_id: int = 0,
                                            file_path: str | Path | None = None, original_oshash: str = "",
                                            video_meta: dict | None = None, destination_message=None) -> dict:
    """Persist the actual destination Telegram location and attach prepared caches.

    Local uploads copy a staged index built before send. Telegram-to-Telegram
    deliveries clone the byte-identical source index/assets and only refresh the
    destination file reference; neither path downloads media to rebuild assets.
    """
    from ..core.telegram_identity import canonical_peer_id
    from ..core.playback_index_cache import (
        update_telegram_location, copy_cached_playback_index_assets,
        remove_local_file_index_stage,
    )
    result = {"location_saved": False, "index_copied": False, "prepared_manifest": False}
    channel = str(canonical_peer_id(destination) or destination_channel or "").strip()
    message_id = int(destination_message_id or 0)
    if not channel or not channel.lstrip("-").isdigit() or message_id <= 0:
        return result
    # Prefer the Telegram Message returned by the successful send operation;
    # this avoids an unnecessary control-plane round trip. Recovery paths, where
    # only a persisted message ID is available, fall back to a targeted lookup.
    message = destination_message if int(getattr(destination_message, "id", 0) or 0) == message_id else None
    if message is None:
        try:
            message = await client.get_messages(destination, ids=message_id) if client is not None else None
            if isinstance(message, (list, tuple)):
                message = next((item for item in message if item is not None), None)
        except Exception:
            message = None
    if message is not None:
        try:
            result["location_saved"] = bool(await asyncio.to_thread(update_telegram_location, int(channel), message_id, message))
        except Exception as exc:
            try:
                db.log(f"Verified delivery location persistence failed for {channel}:{message_id}: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                _swallowed_note("delivery_pipeline.register_verified_playback_assets")
                pass
    try:
        file_obj = getattr(message, "file", None) if message is not None else None
        size = int(getattr(file_obj, "size", 0) or 0)
        path = Path(file_path).expanduser() if file_path else None
        prepared = None
        manifest_root = Path(__import__("telegram_archive.config", fromlist=["PLAYER_CACHE_DIR"]).PLAYER_CACHE_DIR) / "prepared_delivery_assets"
        file_info = await asyncio.to_thread(_inspect_prepared_delivery_file, path, manifest_root) if path is not None else None
        if path is not None and file_info is not None:
            stat, manifest_path, prepared = file_info
            if prepared:
                stage = prepared.get("index_stage") or {}
                stage_cid = int(stage.get("source_channel", 0) or 0)
                stage_mid = int(stage.get("message_id", 0) or 0)
                stage_size = int(stage.get("source_size", stat.st_size) or stat.st_size)
                if stage_mid and size in {0, stage_size}:
                    copied = await asyncio.to_thread(copy_cached_playback_index_assets, stage_cid, stage_mid, int(channel), message_id, stage_size)
                    result["index_copied"] = bool(copied)
                    # Keep the local stage + manifest when a copy fails so an
                    # idempotent verification/recovery pass can retry the cache
                    # hand-off rather than losing the only prepared startup data.
                    if copied:
                        await asyncio.to_thread(remove_local_file_index_stage, path)
                # OSHASH of the exact uploaded file remains separate from the
                # original source fingerprint used for matching in `oshash`.
                uploaded_hash = str(prepared.get("uploaded_oshash") or "")
                phash = str(prepared.get("video_phash") or "")
                probe_json = json.dumps(prepared.get("video_meta") or video_meta or {}, ensure_ascii=False, separators=(",", ":"))
                result["prepared_manifest"] = True
                if not result.get("index_copied") and size > 0 and int(size) == int(stat.st_size):
                    # Rebuild index/startup pieces under the destination Telegram
                    # identity from the same file path that was uploaded. Do not
                    # trust a stage key after remux/re-encode or a failed cache copy.
                    from ..core.playback_index_cache import persist_local_file_index
                    rebuilt = await asyncio.to_thread(persist_local_file_index, int(channel), message_id, path, message)
                    result["index_rebuilt_from_uploaded_file"] = bool(rebuilt)
                    result["index_copied"] = bool(rebuilt)
                if result.get("index_copied"):
                    await asyncio.to_thread(manifest_path.unlink, missing_ok=True)
            else:
                from ..core.playback_index_cache import prepare_local_file_index
                from ..metadata.fingerprint import oshash_file
                from ..metadata.perceptual import stash_video_phash_from_snapshots
                from .thumbnail_cache import local_playback_snapshot_frames_path
                stage = await asyncio.to_thread(prepare_local_file_index, path)
                copied = await asyncio.to_thread(copy_cached_playback_index_assets,
                    int((stage or {}).get("source_channel", 0) or 0), int((stage or {}).get("message_id", 0) or 0),
                    int(channel), message_id, int(stat.st_size)) if stage else None
                result["index_copied"] = bool(copied)
                if not result["index_copied"] and size > 0 and int(size) == int(stat.st_size):
                    from ..core.playback_index_cache import persist_local_file_index
                    rebuilt = await asyncio.to_thread(persist_local_file_index, int(channel), message_id, path, message)
                    result["index_rebuilt_from_uploaded_file"] = bool(rebuilt)
                    result["index_copied"] = bool(rebuilt)
                uploaded_hash = await asyncio.to_thread(oshash_file, str(path))
                phash = await asyncio.to_thread(stash_video_phash_from_snapshots, local_playback_snapshot_frames_path(path), float((video_meta or {}).get("duration") or 0))
                probe_json = json.dumps(video_meta or {}, ensure_ascii=False, separators=(",", ":"))
                if stage:
                    await asyncio.to_thread(remove_local_file_index_stage, path)
        else:
            uploaded_hash, phash, probe_json = "", "", ""
            # A verified Telegram-to-Telegram copy is byte-identical; clone all
            # cached ranges only when the source manifest's file size matches.
            if str(source_channel or "").lstrip("-").isdigit() and int(source_message_id or 0) > 0 and size > 0:
                copied = await asyncio.to_thread(copy_cached_playback_index_assets,
                    int(str(source_channel)), int(source_message_id), int(channel), message_id, size)
                result["index_copied"] = bool(copied)
            source = await asyncio.to_thread(_read_source_media_details, db, str(source_channel or ""), int(source_message_id or 0))
            if source:
                uploaded_hash = str(source["uploaded_oshash"] or source["oshash"] or "")
                probe_json = str(source["uploaded_probe_json"] or "")
                if not probe_json:
                    probe_json = json.dumps({key: source[key] for key in ("width", "height", "duration", "fps", "bitrate", "codec", "audio_codec", "size", "streamable")}, ensure_ascii=False, separators=(",", ":"))
                phash = str(source["video_phash"] or "")
        # Cache cloning clears the source Telegram file reference by design.
        # Reattach the destination's actual file reference after all cache writes.
        if message is not None:
            try:
                result["location_saved"] = bool(await asyncio.to_thread(
                    update_telegram_location, int(channel), message_id, message
                )) or bool(result.get("location_saved"))
            except Exception:
                _swallowed_note("delivery_pipeline.register_verified_playback_assets")
                pass
        await asyncio.to_thread(_store_verified_delivery_metadata, db, uploaded_hash, probe_json, phash, channel, message_id)
    except Exception as exc:
        try:
            db.log(f"Verified delivery playback-cache registration incomplete: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            _swallowed_note("delivery_pipeline.register_verified_playback_assets")
            pass
    return result
