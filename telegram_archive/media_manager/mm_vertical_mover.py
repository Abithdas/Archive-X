"""Telegram Media Manager: Telegram vertical / mobile video mover.

Split out of ``telegram_media_manager.py`` in v29.4.387 without behaviour
changes. The main module executes the parts, in order, inside its own module
namespace, so every function, constant and ``globals()`` lookup is exactly as
when this was one file. Do not import this file directly.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Vertical mover: thumbnails and state
#   - Source scanning
#   - Copy and verified transfer records
#   - Per-item processing
#   - Vertical mover: scan and process
#   - Transfer job state
#   - Destination duplicate detection
#   - Candidate scan and metadata
#   - Job entry points
# ------------------------------------------------------------------
# ============================================================================
# TELEGRAM VERTICAL / MOBILE VIDEO MOVER
# ============================================================================
# Standalone version: includes the proven Telegram/session/thumbnail engine
# directly in this file. It does NOT depend on telegram_smart_video_router.py
# being present beside it.
#
# RULES
#   1. Scan one source channel, oldest -> newest.
#   2. Select ONLY videos that Telegram reports as non-streamable, including
#      Telegram FILE/DOCUMENT videos.
#   3. VIDEO + immediately-following matching thumbnail -> copy both.
#   4. VIDEO without a matching thumbnail -> copy video and generate thumbnail
#      directly from Telegram byte ranges (full video is not downloaded).
#   5. Existing thumbnails are uploaded as DOCUMENT while preserving a
#      filename convention recognized by the channel cleanup logic.
#   6. Verify destination video + thumbnail before any source deletion.
#   7. Delete source video and its matched thumbnail ONLY after verification.
#   8. On any failure, source stays untouched. Incomplete destination video is
#      removed where safe, and the item can be retried on the next run.
#   9. State is saved after every important step for restart/resume safety.
# ============================================================================

VERTICAL_MOBILE_STATE_FILE = config.MEDIA_MANAGER_STATE_DIR / "telegram_vertical_mobile_mover_state.json"
TRANSFER_JOB_STATE_FILE = config.MEDIA_MANAGER_STATE_DIR / "telegram_transfer_job_state.json"
CHANNEL_CLEANUP_STATE_FILE = config.MEDIA_MANAGER_STATE_DIR / "telegram_channel_cleanup_state.json"


# ==============================================================================
# SECTION: Vertical mover: thumbnails and state
# Thumbnail lookup, state and candidate checks.
# ==============================================================================


async def _resolve_associated_source_thumbnail_for_move(
    client, source, video, db=None, *, known_message_id=None,
):
    """Return a verified, exclusively-associated source image to delete with a moved video.

    Prefer Archive X's saved association. For older videos with no saved link, use
    the same conservative filename/adjacency discovery used by Library's Send/Move
    action. Never delete an unverified or shared image, and never guess from a
    distant channel message.
    """
    db = db or globals().get("MEDIA_MANAGER_DB")
    if db is None or video is None:
        return 0
    try:
        video_id = int(getattr(video, "id", 0) or 0)
        if video_id <= 0:
            return 0
        canonical_channel = str(canonical_peer_id(source))
        raw_channel = str(getattr(source, "id", source) or "")
        channel_keys = list(dict.fromkeys(k for k in (canonical_channel, raw_channel) if k))

        from ..core.telegram_registry import (
            document_for_location,
            resolve_linked_thumbnail_message_id,
            thumbnail_message_is_unshared,
        )
        from ..engines.media_utils import is_image_message

        media_row = None
        try:
            with db.lock:
                tables = {str(row[0]) for row in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                if "media" in tables:
                    placeholders = ",".join("?" for _ in channel_keys)
                    media_row = db.conn.execute(
                        "SELECT source_channel,thumbnail_message_id FROM media "
                        f"WHERE CAST(source_channel AS TEXT) IN ({placeholders}) "
                        "AND CAST(source_message_id AS INTEGER)=? LIMIT 1",
                        (*channel_keys, video_id),
                    ).fetchone()
        except Exception:
            media_row = None

        media_channel = str(media_row["source_channel"]) if media_row else canonical_channel
        media_thumbnail_id = media_row["thumbnail_message_id"] if media_row else None
        location = None
        location_channel = media_channel
        for channel_key in dict.fromkeys((media_channel, *channel_keys)):
            try:
                location = document_for_location(db, channel_key, video_id)
            except Exception:
                location = None
            if location:
                location_channel = channel_key
                break

        candidate_id = int(known_message_id or 0)
        if candidate_id <= 0:
            candidate_id = resolve_linked_thumbnail_message_id(
                db, channel_id=location_channel, video_message_id=video_id,
                media_thumbnail_message_id=media_thumbnail_id, location=location,
            )

        # No saved association? Use Library's cautious matching rules for older
        # videos: filename match, or an immediately-following image with no name.
        if candidate_id <= 0:
            from ..engines.safe_message_move import discover_companion_thumbnail
            candidate_id = int(await discover_companion_thumbnail(
                client, source, video_id, safe_video_name(video), look_ahead=3,
            ) or 0)
            location_channel = media_channel or canonical_channel

        if candidate_id <= 0 or candidate_id == video_id:
            return 0

        # A saved ID from a previously verified run is still safe to include if
        # Telegram already removed it; deleting an absent ID is harmless.
        try:
            image_message = await client.get_messages(source, ids=candidate_id)
            if isinstance(image_message, (list, tuple)):
                image_message = next((item for item in image_message if item is not None), None)
        except Exception:
            return 0
        if image_message is None:
            return candidate_id if int(known_message_id or 0) == candidate_id else 0
        if not is_image_message(image_message):
            return 0
        if not thumbnail_message_is_unshared(
            db, channel_id=location_channel, video_message_id=video_id,
            thumbnail_message_id=candidate_id,
        ):
            return 0
        return candidate_id
    except Exception as exc:
        try:
            print(f"  Associated source thumbnail lookup skipped safely: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return 0


def _mark_moved_source_thumbnail_deleted(db, source, thumbnail_message_id):
    """Mark a confirmed-removed source thumbnail as moved in Archive X's registry."""
    if db is None or not thumbnail_message_id:
        return
    try:
        channel_keys = list(dict.fromkeys((
            str(canonical_peer_id(source)), str(getattr(source, "id", source) or "")
        )))
        placeholders = ",".join("?" for _ in channel_keys if _)
        params = [config.now_iso(), str(thumbnail_message_id), *[k for k in channel_keys if k]]
        if not placeholders:
            return
        with db.lock:
            db.conn.execute(
                "UPDATE telegram_document_locations SET location_state='MOVED_BY_ARCHIVE_X',last_seen_at=? "
                "WHERE CAST(message_id AS TEXT)=? AND CAST(channel_id AS TEXT) IN (" + placeholders + ")",
                params,
            )
            db.conn.commit()
    except Exception:
        pass

def vertical_mobile_state_key(source, destination, message_id):
    return f"{int(source.id)}:{int(destination.id)}:{int(message_id)}"


def vertical_mobile_load_state():
    state = load_json(VERTICAL_MOBILE_STATE_FILE, {})
    state.setdefault("version", 3)
    state.setdefault("source_channel_id", None)
    state.setdefault("destination_channel_id", None)
    state.setdefault("source_title", "")
    state.setdefault("destination_title", "")
    state.setdefault("items", {})
    return state


def vertical_mobile_save_state(state):
    save_json(VERTICAL_MOBILE_STATE_FILE, state)


def vertical_mobile_is_candidate(message, source_mode=None, orientation="VERTICAL"):
    """Return True for the requested Telegram video representation/orientation.

    ``orientation`` is one of ``VERTICAL``, ``HORIZONTAL`` or ``BOTH``.
    Legacy callers omit it and therefore keep the original vertical-only behavior.
    """
    if source_mode == "FILE":
        if not is_document_video_message(message) or getattr(message, "video", None):
            return False
    elif source_mode == "VIDEO":
        if not getattr(message, "video", None):
            return False
    elif not is_video_message(message):
        return False

    orientation = str(orientation or "VERTICAL").upper()
    if orientation == "BOTH":
        return True
    actual, width, height, _rotation = effective_orientation(message)
    if actual == "UNKNOWN":
        return False
    return actual == orientation


# ==============================================================================
# SECTION: Source scanning
# Media mode keys and source scan.
# ==============================================================================

def media_mode_key(source, destination, message_id, source_mode, destination_mode):
    return f"{canonical_peer_id(source)}:{canonical_peer_id(destination)}:{int(message_id)}:{source_mode}:{destination_mode}"


def is_document_video_message(message):
    doc = getattr(message, "document", None)
    if not doc:
        return False
    mime = str(getattr(doc, "mime_type", "") or "").lower()
    name = ""
    try:
        name = str(getattr(message.file, "name", "") or "").lower()
    except Exception:
        pass
    if mime.startswith("video/") or Path(name).suffix.lower() in VIDEO_EXTENSIONS:
        return True
    try:
        return any(type(attr).__name__ == "DocumentAttributeVideo"
                   for attr in (getattr(doc, "attributes", ()) or ()))
    except Exception:
        return False


async def scan_source_mode(client, source, source_mode):
    """Scan selected source media and retain only portrait/vertical videos."""
    return await vertical_mobile_scan(client, source, source_mode)


# ==============================================================================
# SECTION: Copy and verified transfer records
# Copies media and records verified transfers.
# ==============================================================================

async def copy_media_to_destination(
    client,
    destination,
    source,
    message,
    destination_mode,
    tempdir,
    cover_bytes=None,
    caption="",
    prepared_local_video=None,
    prepared_video_meta=None,
    prepared_display_name=None,
):
    """Copy media according to destination mode.

    VIDEO -> VIDEO and VIDEO -> FILE are Telegram-side media copies.
    FILE -> FILE is also Telegram-side.
    FILE -> VIDEO requires a real download + re-upload because Telegram cannot
    change an existing document media object into native video merely by setting
    force_document=False.
    """
    name = safe_video_name(message)
    if destination_mode == "FILE":
        while True:
            try:
                thumb_stream = None
                if cover_bytes:
                    thumb_stream = io.BytesIO(cover_bytes)
                    thumb_stream.name = f"{safe_video_name(message)}.jpg"
                result = await client.send_file(
                    destination, message.media, caption=caption, parse_mode="html",
                    force_document=True, allow_cache=True,
                    thumb=thumb_stream,
                )
                return result[0] if isinstance(result, list) else result
            except Exception as exc:
                wait = flood_wait_seconds(exc)
                if wait:
                    print(f"\nTelegram flood wait: {wait}s. Waiting {wait + 2}s...")
                    await asyncio.sleep(wait + 2)
                    continue
                raise

    # Destination VIDEO
    if getattr(message, "video", None):
        if not cover_bytes:
            raise RuntimeError("Telegram Media Manager requires a custom cover for every destination video")
        return await send_existing_video_with_cover(
            client, destination, message, cover_bytes, caption=caption
        )

    if not cover_bytes:
        raise RuntimeError("Telegram Media Manager requires a custom cover for every destination video")

    # FILE -> VIDEO: Telegram cannot server-side change the media type.
    # The preferred HIFI path prepares the local streamable MP4 once upstream:
    #   download source -> convert/remux -> local thumbnail -> upload native VIDEO.
    # This avoids a second Telegram download and completely avoids remote
    # FFmpeg/MP4-moov thumbnail probing for FILE sources.
    if prepared_local_video is not None:
        upload_path = Path(prepared_local_video)
        if not upload_path.exists() or upload_path.stat().st_size <= 0:
            raise RuntimeError(f"prepared FILE->VIDEO output is missing: {upload_path}")
        display_name = str(prepared_display_name or upload_path.name)
        print(
            f"  FILE -> VIDEO HIFI: uploading prepared streamable MP4 "
            f"{upload_path.name} ({human_size(upload_path.stat().st_size)})"
        )
        while True:
            try:
                return await send_local_video_with_cover(
                    client,
                    destination,
                    upload_path,
                    cover_bytes,
                    caption=caption,
                    progress_callback=progress_printer("VIDEO UPLOAD", with_rate=True),
                    status_callback=upload_status_printer("VIDEO UPLOAD"),
                    supports_streaming=True,
                    video_meta=prepared_video_meta,
                    file_name_override=display_name,
                )
            except Exception as exc:
                wait = flood_wait_seconds(exc)
                if wait:
                    print(f"\nTelegram flood wait: {wait}s. Waiting {wait + 2}s...")
                    await asyncio.sleep(wait + 2)
                    continue
                raise

    # Compatibility fallback for callers that do not pre-prepare a FILE->VIDEO
    # output. This branch still performs exactly one temporary source download.
    print("  FILE -> VIDEO: HIFI temporary download + normalize + VIDEO upload.")
    temp_path = Path(tempdir) / name
    downloaded = None
    try:
        downloaded = await _download_media_governed(client, 
            message, file=str(temp_path), progress_callback=progress_printer("FILE DL")
        )
        if not downloaded or not Path(downloaded).exists():
            raise RuntimeError("source file download failed")
        while True:
            try:
                result = await send_local_video_with_cover(
                    client, destination, downloaded, cover_bytes, caption=caption,
                    progress_callback=None, supports_streaming=True,
                )
                return result
            except Exception as exc:
                wait = flood_wait_seconds(exc)
                if wait:
                    print(f"\nTelegram flood wait: {wait}s. Waiting {wait + 2}s...")
                    await asyncio.sleep(wait + 2)
                    continue
                raise
    finally:
        try:
            if downloaded and Path(downloaded).exists():
                Path(downloaded).unlink()
        except Exception:
            pass


async def _record_verified_transfer_location(db, source, message, destination, destination_video_id, destination_thumb_id, *, client=None, action="TRANSFER", caption=None, video_meta=None, filename=None, file_path=None, destination_message=None):
    """Persist a verified source/destination pair and register the destination in Library."""
    if db is None:
        return
    try:
        from ..core.telegram_identity import canonical_peer_id
        source_channel = canonical_peer_id(source)
        source_message_id = int(getattr(message, "id", 0) or 0)
        identity_key = ""
        with db.lock:
            row = db.conn.execute("SELECT identity_key,id FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1", (source_channel, source_message_id)).fetchone()
            if row:
                identity_key = str(row["identity_key"] or "")
                source_media_id = int(row["id"])
            else:
                source_media_id = None
        file_obj = getattr(message, "file", None)
        video = getattr(message, "video", None)
        meta = dict(video_meta or {})
        meta.setdefault("duration", getattr(file_obj, "duration", 0) or 0)
        meta.setdefault("width", getattr(video, "w", 0) or getattr(video, "width", 0) or 0)
        meta.setdefault("height", getattr(video, "h", 0) or getattr(video, "height", 0) or 0)
        meta.setdefault("codec", "")
        from ..engines.delivery_ledger import record_verified_delivery
        delivery_record = record_verified_delivery(
            db, destination_channel=canonical_peer_id(destination),
            destination_video_message_id=int(destination_video_id),
            destination_thumbnail_message_id=int(destination_thumb_id or 0),
            source_channel=source_channel, source_message_id=source_message_id,
            source_media_id=source_media_id, identity_key=identity_key,
            filename=str(filename or safe_video_name(message)), file_path=file_path,
            file_size=int(getattr(file_obj, "size", 0) or 0),
            duration=float(meta.get("duration") or 0), width=int(meta.get("width") or 0),
            height=int(meta.get("height") or 0), fps=float(meta.get("fps") or 0),
            bitrate=int(meta.get("bitrate") or 0), codec=str(meta.get("video_codec") or meta.get("codec") or ""),
            audio_codec=str(meta.get("audio_codec") or ""),
            caption=str(caption if caption is not None else getattr(message, "message", "") or ""),
            action=str(action or "TRANSFER"),
        )
        library_media_id = int(delivery_record.get("library_media_id") or 0)
        try:
            from ..engines.delivery_pipeline import register_verified_playback_assets
            await register_verified_playback_assets(
                db, client, destination, delivery_record.get("destination_channel") or canonical_peer_id(destination),
                int(destination_video_id), source_channel=source_channel, source_message_id=source_message_id,
                file_path=file_path, original_oshash=identity_key, video_meta=meta,
                destination_message=destination_message,
            )
        except Exception as asset_exc:
            try: db.log(f"Verified Telegram transfer playback-cache registration incomplete: {type(asset_exc).__name__}: {asset_exc}", "WARN")
            except Exception: pass
        if library_media_id and client is not None:
            from ..engines.caption_sync import sync_verified_delivery_caption
            await sync_verified_delivery_caption(db, client, library_media_id)
    except Exception as exc:
        try: db.log(f"Telegram Transfer Manager: destination delivery ledger/Library update failed: {type(exc).__name__}: {exc}", "ERROR")
        except Exception: pass
        raise


async def _sync_verified_transfer_destination_registry(db, client, destination, destination_video_id,
                                                        destination_thumb_id, *, destination_message=None):
    """Register a verified transfer destination and mirror real Library readiness."""
    if db is None:
        return {"registered": False, "prepared": False}
    try:
        from ..core.telegram_identity import canonical_peer_id
        from ..core.telegram_registry import register_known_message, mark_prepared
        from ..playback.stream_server import make_record

        channel = canonical_peer_id(destination)
        message_id = int(destination_video_id or 0)
        message = destination_message if int(getattr(destination_message, "id", 0) or 0) == message_id else None
        if message is None and client is not None and message_id > 0:
            message = await client.get_messages(destination, ids=message_id)
            if isinstance(message, (list, tuple)):
                message = next((item for item in message if item is not None), None)
        if message is None or not channel or message_id <= 0:
            return {"registered": False, "prepared": False}

        record = make_record(message) or {
            "id": message_id,
            "name": str(getattr(getattr(message, "file", None), "name", "") or ""),
            "size": int(getattr(getattr(message, "file", None), "size", 0) or 0),
        }
        with db.lock:
            media = db.conn.execute(
                "SELECT id,oshash,video_phash FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
                (str(channel), message_id),
            ).fetchone()
        media_id = int(media["id"]) if media else None
        register_known_message(
            db, channel_id=channel, message_id=message_id, record=record,
            media_id=media_id, thumbnail_message_id=int(destination_thumb_id or 0) or None,
        )
        ready = False
        if media_id:
            with db.lock:
                # Readiness must match the actual Library publication contract.
                # The view accepts a READY representative asset keyed by Telegram
                # identity even if the asset_registry.media_id is stale from the
                # source record; this is common after forward/copy transfers.
                ready = bool(db.conn.execute(
                    "SELECT 1 FROM archive_x_published_media WHERE id=? LIMIT 1",
                    (media_id,),
                ).fetchone())
        prepared = False
        if ready:
            prepared = bool(mark_prepared(
                db, channel_id=channel, message_id=message_id,
                prepared={"source": "verified_transfer_reused_assets", "media_id": media_id},
                oshash=str((media["oshash"] if media else "") or ""),
                phash=str((media["video_phash"] if media else "") or ""),
            ))
        return {"registered": True, "prepared": prepared}
    except Exception as exc:
        try:
            db.log(f"Verified transfer destination registry sync deferred for {destination_video_id}: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            pass
        return {"registered": False, "prepared": False, "error": f"{type(exc).__name__}: {exc}"}


# ==============================================================================
# SECTION: Per-item processing
# Processes one item.
# ==============================================================================

async def _media_process_one_unlocked(client, source, destination, message, state, source_mode, destination_mode, db=None, orientation="VERTICAL", cover_policy="AUTOMATIC", save_state_fn=None):
    """Move one item through copy, thumbnail, verification and source cleanup."""
    key = media_mode_key(source, destination, message.id, source_mode, destination_mode)
    entry = state["items"].setdefault(key, {})
    prefetch_key = f"{canonical_peer_id(source)}:{int(message.id)}"
    prefetched_meta = dict((state.get("metadata_prefetch") or {}).get(prefetch_key) or {})
    entry.update({"source_message_id": int(message.id), "source_filename": safe_video_name(message), "source_mode": source_mode, "destination_mode": destination_mode, "status": entry.get("status", "STARTED")})
    save_state_fn = save_state_fn or vertical_mobile_save_state
    save_state_fn(state)

    fresh = await client.get_messages(source, ids=int(message.id))
    if not fresh:
        raise RuntimeError(f"source msg={message.id} no longer exists")
    if not vertical_mobile_is_candidate(fresh, source_mode, orientation=orientation):
        raise RuntimeError(
            f"source msg={message.id} is no longer an eligible {orientation.lower()} {source_mode} video"
        )
    message = fresh

    with tempfile.TemporaryDirectory(prefix="tg_media_mover_") as td:
        dest_video_id = entry.get("destination_video_id")
        dest_thumb_id = entry.get("destination_thumbnail_id")
        generated = None

        # HIFI FILE -> VIDEO preparation MUST occur before metadata, caption,
        # thumbnail discovery, or any remote range access. The single local
        # converted MP4 becomes the source for OSHASH, thumbnail and upload.
        # Download the source exactly once, normalize it to Telegram-streamable
        # MP4, then reuse that same local result for both the thumbnail and upload.
        prepared_local_video = None
        prepared_video_meta = None
        prepared_display_name = None
        if source_mode == "FILE" and destination_mode == "VIDEO":
            source_name = safe_video_name(message)
            raw_path = Path(td) / source_name
            output_name = f"{Path(source_name).stem}.mp4" if Path(source_name).suffix else f"telegram_{message.id}.mp4"
            # Keep the conversion output different from the downloaded input.
            output_path = Path(td) / f".streamable_{message.id}_{output_name}"

            print("")
            print("  HIFI FILE -> VIDEO preparation")
            print("  Step 1/3: downloading source exactly once...")
            source_bytes = int(getattr(getattr(message, "file", None), "size", 0) or 0)
            download_progress = (_manager_download_progress_callback(
                source_name, db=globals().get("MEDIA_MANAGER_DB"),
                job_id=globals().get("TRANSFER_JOB_ID"),
            ) if source_bytes >= 1024 * 1024 else None)
            downloaded_source = await download_source_file(
                client, source, message, raw_path, progress_callback=download_progress
            )
            print("  Step 2/3: normalizing to Telegram-streamable MP4...")
            prepared_video_meta = await asyncio.wait_for(
                convert_to_streamable_mp4(downloaded_source, output_path),
                timeout=CONVERSION_TIMEOUT_SECONDS,
            )

            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("HIFI FILE->VIDEO conversion produced no usable MP4")

            # The source download is no longer needed after the conversion has
            # passed final MP4/codec validation. Remove it before upload so the
            # temporary workspace does not hold two full-size copies.
            try:
                Path(downloaded_source).unlink()
            except Exception:
                pass

            final_output_path = Path(td) / output_name
            if final_output_path.exists():
                final_output_path.unlink()
            output_path.replace(final_output_path)
            prepared_local_video = final_output_path
            # Build the canonical 36-frame set, contact sheet, exact-output
            # fingerprint, MP4 index/startup segments and keyframe map before
            # the first destination send (no subsequent Telegram redownload).
            try:
                from ..engines.delivery_pipeline import prepare_local_delivery_assets
                await prepare_local_delivery_assets(
                    db, prepared_local_video, source_oshash=None,
                    video_meta=prepared_video_meta or {},
                )
            except Exception as prepare_exc:
                raise RuntimeError(f"Prepared local conversion could not build its delivery assets: {type(prepare_exc).__name__}: {prepare_exc}") from prepare_exc
            prepared_display_name = output_name
            entry["converted_filename"] = output_name
            entry["converted_size"] = int(final_output_path.stat().st_size)
            entry["converted_meta"] = {
                k: prepared_video_meta.get(k)
                for k in ("width", "height", "duration", "video_codec", "audio_codec", "format_name")
            }
            entry["status"] = "LOCAL_STREAMABLE_READY"
            save_state_fn(state)

            print(
                f"  HIFI READY: {output_name} | "
                f"{human_size(final_output_path.stat().st_size)} | "
                f"{prepared_video_meta.get('width')}x{prepared_video_meta.get('height')} | "
                f"{prepared_video_meta.get('video_codec')} + {prepared_video_meta.get('audio_codec')}"
            )

        # Metadata/caption happens only after HIFI FILE->VIDEO preparation.
        # This prevents the old Telegram-range OSHASH path from running first.
        print("  Metadata/API check + caption preparation...")
        # The transfer preflight already did this work for Telegram-origin
        # media. FILE->VIDEO also reuses the original Telegram metadata after
        # local normalization; no second provider/range lookup is needed.
        caption = str(prefetched_meta.get("caption") or "").strip()
        if not caption:
            # Transfer must consume persisted catalogue state. Do not silently
            # restart Metadata & Assets/provider work because a transfer item
            # has no cached caption.
            caption = await _prepare_transfer_catalogue_caption(db, source, message)
        if prefetched_meta:
            entry["prefetched_metadata"] = prefetched_meta

        # Cover policy for Telegram -> Telegram / FILE -> VIDEO delivery:
        # every policy uses the Archive X 36-frame contact sheet; EXISTING uses
        # a persisted sheet, while AUTOMATIC/GENERATE may prepare one as needed.
        cover_policy = str(cover_policy or "AUTOMATIC").upper()
        if cover_policy not in {"AUTOMATIC", "EXISTING", "GENERATE"}:
            cover_policy = "AUTOMATIC"
        # EXISTING means an already-persisted Archive X playback snapshot.
        # The selector can reuse it without any Telegram thumbnail lookup.
        save_state_fn(state)

        cover_bytes, thumbnail_kind, generated, source_thumb = await _await_thumbnail_watchdog(
            _select_manager_thumbnail_with_retry(
                client, source, message, td,
                explicit_thumbnail_id=None,
                generate_fallback=(cover_policy != "EXISTING"),
                local_video_path=prepared_local_video,
                display_name=prepared_display_name,
                force_snapshot=False,
            ),
            timeout=THUMBNAIL_TIMEOUT_SECONDS * 2,
            label=safe_video_name(message),
        )
        if not cover_bytes:
            raise RuntimeError("No usable thumbnail was available or generated")
        entry["cover_policy"] = cover_policy
        entry["thumbnail_kind"] = thumbnail_kind
        entry["representative_snapshot_path"] = str(generated) if generated else None
        save_state_fn(state)

        # Existing destination IDs can originate from the legacy media-only
        # mover. Rebuild that pair when the caption changed so the new metadata
        # contract is not mixed with an older delivery.
        if destination_mode == "VIDEO" and dest_video_id:
            try:
                existing_dest = await client.get_messages(destination, ids=int(dest_video_id))
                existing_caption = str(getattr(existing_dest, "message", "") or "").strip() if existing_dest else ""
                if existing_dest and existing_caption != str(caption or "").strip():
                    print("  Existing destination VIDEO uses a different caption; rebuilding destination pair...")
                    old_ids = [int(dest_video_id)]
                    if dest_thumb_id:
                        old_ids.append(int(dest_thumb_id))
                    await delete_messages(client, destination, old_ids)
                    dest_video_id = None
                    dest_thumb_id = None
                    entry.pop("destination_video_id", None)
                    entry.pop("destination_thumbnail_id", None)
                    entry["status"] = "REBUILD_WITH_METADATA"
                    save_state_fn(state)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not inspect existing destination video msg={dest_video_id}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

        destination_video_message = None
        async with _destination_delivery_lock(destination):
            if not dest_video_id:
                print(f"  Uploading/copying source msg={message.id} -> destination as {destination_mode}...")
                copied = await copy_media_to_destination(
                    client,
                    destination,
                    source,
                    message,
                    destination_mode,
                    td,
                    cover_bytes,
                    caption,
                    prepared_local_video=prepared_local_video,
                    prepared_video_meta=prepared_video_meta,
                    prepared_display_name=prepared_display_name,
                )
                destination_video_message = copied if int(getattr(copied, "id", 0) or 0) > 0 else None
                dest_video_id = int(getattr(copied, "id", copied))
                created_media = True
                entry["destination_video_id"] = dest_video_id
                entry["status"] = "MEDIA_COPIED"
                save_state_fn(state)
                print(f"  DESTINATION MEDIA COPIED: msg={dest_video_id}")
            else:
                print(f"  Destination MEDIA already exists: msg={dest_video_id}")

            if dest_thumb_id and entry.get("thumbnail_kind") not in {"playback-contact-sheet-36"}:
                print(f"  Replacing legacy destination thumbnail msg={dest_thumb_id} with the 36-frame contact sheet...")
                await delete_messages(client, destination, [int(dest_thumb_id)])
                dest_thumb_id = None
                entry.pop("destination_thumbnail_id", None)
                entry["status"] = "MEDIA_COPIED"
                save_state_fn(state)

            if not dest_thumb_id:
                print(f"  Posting 36-frame contact-sheet DOCUMENT after MEDIA msg={dest_video_id}...")
                thumb_msg = await upload_thumbnail_bytes_document(
                    client, destination, cover_bytes, (prepared_display_name or safe_video_name(message))
                )
                dest_thumb_id = int(getattr(thumb_msg, "id", thumb_msg) or 0)
                if not dest_thumb_id or dest_thumb_id <= int(dest_video_id):
                    raise RuntimeError(
                        f"Thumbnail was not posted after media: media={dest_video_id}, thumbnail={dest_thumb_id}"
                    )
                entry["destination_thumbnail_id"] = dest_thumb_id
                entry["status"] = "PAIR_COPIED"
                save_state_fn(state)
            else:
                print(f"  Destination THUMBNAIL already exists: msg={dest_thumb_id}")

        expected_delivery_name = prepared_display_name or safe_video_name(message)
        ok, reason = await verify_destination_pair(
            client, destination, dest_video_id, dest_thumb_id,
            expected_video_name=expected_delivery_name,
            destination_mode=destination_mode,
            require_native_video=(destination_mode == "VIDEO"),
            expected_source=message,
            expected_meta=prepared_video_meta,
            require_adjacent=created_media,
        )
        if not ok:
            entry["status"] = "DESTINATION_VERIFY_FAILED"
            entry["error"] = reason
            save_state_fn(state)
            raise RuntimeError(f"Destination verification failed: {reason}")
        print(
            f"  DESTINATION VERIFIED: {destination_mode}={dest_video_id} | "
            f"thumbnail={dest_thumb_id} | native cover/thumbnail + separate message PASS"
        )
        previous_thumbnail_id = int(entry.get("source_thumbnail_message_id_to_delete") or 0)
        source_thumbnail_id = await _resolve_associated_source_thumbnail_for_move(
            client, source, message, db, known_message_id=previous_thumbnail_id or None,
        )
        if source_thumbnail_id:
            entry["source_thumbnail_message_id_to_delete"] = int(source_thumbnail_id)
            save_state_fn(state)
            if not previous_thumbnail_id:
                print(f"  Associated SOURCE thumbnail found: msg={source_thumbnail_id}")
        else:
            entry.pop("source_thumbnail_message_id_to_delete", None)
        await _record_verified_transfer_location(
            db, source, message, destination, dest_video_id, dest_thumb_id,
            client=client, action="TRANSFER", caption=caption,
            video_meta=prepared_video_meta,
            filename=prepared_display_name or safe_video_name(message),
            file_path=prepared_local_video, destination_message=destination_video_message,
        )
        # The destination is now verified and has its own Library record. Hide
        # the old source record immediately so a failed Telegram delete cannot
        # leave two visible Library copies. A later success promotes the source
        # location from pending-delete to moved-by-Archive-X.
        if db is not None:
            # canonical_peer_id is imported at module scope. Do not re-import it
            # inside this function: Python would treat it as a local throughout
            # the function and the earlier prefetch_key calculation would fail
            # with UnboundLocalError before any transfer operation starts.
            from ..engines.delivery_ledger import mark_verified_transfer_source_hidden
            await asyncio.to_thread(
                mark_verified_transfer_source_hidden, db,
                source_channel=canonical_peer_id(source), source_message_id=int(message.id),
                destination_channel=canonical_peer_id(destination), destination_message_id=int(dest_video_id),
                source_deleted=False,
            )
        entry["destination_channel_id"] = int(getattr(destination, "id", destination))
        entry["destination_video_id"] = int(dest_video_id)
        entry["destination_thumbnail_id"] = int(dest_thumb_id)
        entry["destination_state"] = "VERIFIED"
        save_state_fn(state)

        await _store_delivery_representative_snapshot(
            db, destination, dest_video_id, dest_thumb_id,
            source=source, source_video=message, generated_sheet=generated,
            local_video_path=prepared_local_video,
        )
        await _sync_verified_transfer_destination_registry(
            db, client, destination, dest_video_id, dest_thumb_id,
            destination_message=destination_video_message,
        )
        _cleanup_verified_delivery_artifacts(
            source, message, local_video_path=prepared_local_video, db=db,
            temporary_local_video=prepared_local_video is not None,
        )

        source_ids = [int(message.id)]
        source_thumbnail_id = int(entry.get("source_thumbnail_message_id_to_delete") or 0)
        if source_thumbnail_id > 0 and source_thumbnail_id != int(message.id):
            source_ids.append(source_thumbnail_id)
        entry["source_ids_to_delete"] = list(source_ids)
        save_state_fn(state)
        print(f"  Deleting SOURCE after destination verification (video + associated thumbnail if present): {source_ids}")
        await delete_source_after_verify(client, source, source_ids)
        if source_thumbnail_id > 0 and source_thumbnail_id in source_ids[1:]:
            _mark_moved_source_thumbnail_deleted(db, source, source_thumbnail_id)
        remaining = await client.get_messages(source, ids=source_ids)
        if not isinstance(remaining, list):
            remaining = [remaining] if remaining else []
        if [m for m in remaining if m and getattr(m, "id", None)]:
            raise RuntimeError("Telegram deletion did not remove all requested source messages")
        if db is not None:
            # canonical_peer_id is imported at module scope. Do not re-import it
            # inside this function: Python would treat it as a local throughout
            # the function and the earlier prefetch_key calculation would fail
            # with UnboundLocalError before any transfer operation starts.
            from ..engines.delivery_ledger import mark_verified_transfer_source_hidden
            await asyncio.to_thread(
                mark_verified_transfer_source_hidden, db,
                source_channel=canonical_peer_id(source), source_message_id=int(message.id),
                destination_channel=canonical_peer_id(destination), destination_message_id=int(dest_video_id),
                source_deleted=True,
            )
        entry["source_deleted"] = True
        entry["status"] = "COMPLETE"
        save_state_fn(state)
        print(f"  MOVE COMPLETE: {safe_video_name(message)}")
        return True


# ==============================================================================
# SECTION: Vertical mover: scan and process
# Vertical/mobile scan, thumbnail and item processing.
# ==============================================================================

async def vertical_mobile_scan(client, source, source_mode):
    found = await _vertical_mobile_scan_impl(client, source, source_mode)
    if not found and ON_EMPTY_SCAN is not None:
        try:
            await ON_EMPTY_SCAN(client, source, source_mode, MOVER_ANY_ORIENTATION)
        except Exception as exc:
            print(f"[SCAN] diagnostics failed: {type(exc).__name__}: {exc}")
    return found


async def _vertical_mobile_scan_impl(client, source, source_mode):
    """Scan oldest->newest and keep only portrait/vertical video items.

    Uses bounded message windows so Telegram connection drops resume above the
    last processed message. No full video bytes are requested during scanning.
    """
    print("\n" + "=" * 90)
    print(" SOURCE SCAN — VERTICAL / MOBILE-RECORDED VIDEOS ONLY")
    print("=" * 90)
    print("Order              : OLDEST -> NEWEST")
    print(f"Source media       : {source_mode}")
    print("Selection          : PORTRAIT / VERTICAL only (rotation-aware)")
    print("Full video download : NO during scan")
    print("Connection mode    : RESUMABLE — socket errors resume from last message")
    print()

    started = time.monotonic()
    last_print = started
    scanned = 0
    video_count = 0
    candidate_count = 0
    unknown_dimensions = 0
    candidates = []
    last_id = 0
    reconnects = 0
    batch_size = 200
    completed = False

    while not completed:
        batch_received = 0
        try:
            if not client.is_connected():
                reconnects += 1
                await connect_with_retry(client, label=f"scan reconnect #{reconnects}")

            async for message in client.iter_messages(
                source, reverse=True, min_id=last_id, limit=batch_size
            ):
                batch_received += 1
                scanned += 1
                if not message:
                    continue

                last_id = int(message.id)
                try:
                    is_source_video = (
                        bool(getattr(message, "video", None))
                        if source_mode == "VIDEO"
                        else is_document_video_message(message) and not getattr(message, "video", None)
                    )
                    if is_source_video:
                        video_count += 1
                        width, height, _, _ = get_video_dimensions(message)
                        if (not width or not height) and not MOVER_ANY_ORIENTATION:
                            unknown_dimensions += 1
                        elif vertical_mobile_is_candidate(message, source_mode):
                            candidate_count += 1
                            candidates.append(message)
                except Exception as exc:
                    print(
                        f"\n[SCAN WARNING] msg={getattr(message, 'id', '?')} "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )

                now = time.monotonic()
                if now - last_print >= 1.0:
                    elapsed = max(now - started, 0.001)
                    speed = scanned / elapsed
                    print(
                        f"\r[SCAN] Messages: {scanned:,} | Videos: {video_count:,} | "
                        f"Vertical/mobile: {candidate_count:,} | Unknown dimensions: {unknown_dimensions:,} | "
                        f"Last msg: {last_id} | Speed: {speed:,.0f} msg/s",
                        end="",
                        flush=True,
                    )
                    last_print = now

            if batch_received < batch_size:
                completed = True

        except Exception as exc:
            wait = flood_wait_seconds(exc)
            if wait:
                wait += 2
                print(f"\n[SCAN] Telegram rate limit. Waiting {wait}s and resuming from msg {last_id}...")
                await asyncio.sleep(wait)
                continue

            if not is_transient_telegram_connection_error(exc):
                raise

            reconnects += 1
            print(
                f"\n[SCAN] Telegram connection dropped at msg {last_id}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            print("[SCAN] No scan data is discarded. Reconnecting and resuming...", flush=True)
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(min(10, 2 + reconnects))
            await connect_with_retry(client, label=f"scan reconnect #{reconnects}")

    elapsed = max(time.monotonic() - started, 0.001)
    print(
        f"\r[SCAN COMPLETE] Messages: {scanned:,} | Videos: {video_count:,} | "
        f"Vertical/mobile: {candidate_count:,} | Unknown dimensions: {unknown_dimensions:,} | "
        f"Elapsed: {int(elapsed)//60:02d}:{int(elapsed)%60:02d} | Reconnects: {reconnects:,}"
    )
    return candidates


# ==============================================================================
# SECTION: Transfer job state
# Control checkpoints and persisted job state.
# ==============================================================================

async def transfer_control_checkpoint():
    """Wait at a safe boundary when the operator pauses a Telegram transfer."""
    event = PAUSE_EVENT
    while event is not None and not event.is_set():
        await asyncio.sleep(0.25)


def transfer_job_state_load(job_id=None, *, resume=False):
    state = load_json(TRANSFER_JOB_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 1)
    state.setdefault("config", {})
    state.setdefault("items", {})
    cfg = state.get("config") if isinstance(state.get("config"), dict) else {}
    saved_job_id = str(cfg.get("job_id") or "")
    current_job_id = str(job_id or "")
    if current_job_id and saved_job_id and saved_job_id != current_job_id and not resume:
        state = {"version": 2, "config": {}, "items": {}}
    return state


def transfer_job_state_save(state):
    save_json(TRANSFER_JOB_STATE_FILE, state)


def channel_cleanup_state_load():
    state = load_json(CHANNEL_CLEANUP_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 1)
    state.setdefault("source_channel_id", None)
    state.setdefault("source_title", "")
    state.setdefault("items", {})
    return state


def channel_cleanup_state_save(state):
    save_json(CHANNEL_CLEANUP_STATE_FILE, state)


TRANSFER_DUPLICATE_POLICIES = {
    "1": "NONE",
    "2": "SKIP",
    "3": "ASK",
}


# ==============================================================================
# SECTION: Destination duplicate detection
# Signatures and destination duplicate indexes.
# ==============================================================================

def _transfer_duplicate_signature(message):
    """Fast metadata-only fallback duplicate signature; never downloads media."""
    try:
        return dup_video_signature(message)
    except Exception:
        return (
            dup_canonical_key(safe_video_name(message)),
            message_size(message),
            *effective_orientation(message)[:3],
        )


def _transfer_document_id(message):
    """Return Telegram's stable document ID when the message exposes one."""
    document = getattr(message, "document", None)
    if document is None:
        document = getattr(getattr(message, "media", None), "document", None)
    value = getattr(document, "id", None) if document is not None else None
    return str(value) if value not in (None, "", 0, "0") else ""


def _transfer_duplicate_keys(message, oshash=""):
    """Identity-first keys followed by the old conservative metadata signature."""
    keys = [("signature", _transfer_duplicate_signature(message))]
    document_id = _transfer_document_id(message)
    if document_id:
        keys.insert(0, ("document_id", document_id))
    fingerprint = str(oshash or "").strip().lower()
    if len(fingerprint) == 16 and all(ch in "0123456789abcdef" for ch in fingerprint):
        keys.insert(0, ("oshash", fingerprint))
    return keys


def _transfer_saved_oshashes(db, channel_id):
    """Read known OSHASH values for a channel in one short read-only query pass."""
    if db is None:
        return {}
    result = {}
    try:
        with db.reading() as conn:
            rows = conn.execute(
                "SELECT l.message_id AS message_id, "
                "COALESCE(NULLIF(LOWER(TRIM(r.oshash)),''),NULLIF(LOWER(TRIM(m.uploaded_oshash)),''),"
                "NULLIF(LOWER(TRIM(m.oshash)),''),'') AS oshash "
                "FROM telegram_document_locations l "
                "LEFT JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
                "LEFT JOIN media m ON CAST(m.source_channel AS TEXT)=l.channel_id "
                "AND CAST(m.source_message_id AS INTEGER)=l.message_id "
                "WHERE l.channel_id=? AND l.location_state='PRESENT'",
                (str(channel_id),),
            ).fetchall()
            for row in rows:
                fingerprint = str(row["oshash"] or "").strip().lower()
                if len(fingerprint) == 16 and all(ch in "0123456789abcdef" for ch in fingerprint):
                    result[int(row["message_id"])] = fingerprint
            rows = conn.execute(
                "SELECT CAST(source_message_id AS INTEGER) AS message_id, "
                "COALESCE(NULLIF(LOWER(TRIM(uploaded_oshash)),''),NULLIF(LOWER(TRIM(oshash)),''),'') AS oshash "
                "FROM media WHERE CAST(source_channel AS TEXT)=?",
                (str(channel_id),),
            ).fetchall()
            for row in rows:
                fingerprint = str(row["oshash"] or "").strip().lower()
                if len(fingerprint) == 16 and all(ch in "0123456789abcdef" for ch in fingerprint):
                    result.setdefault(int(row["message_id"]), fingerprint)
    except Exception as exc:
        try:
            db.log(f"Transfer duplicate OSHASH lookup failed for channel {channel_id}: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            pass
    return result


def _transfer_duplicate_index_from_record(row):
    """Build document/OSHASH/signature keys from a saved registry row, without Telegram IO."""
    keys = []
    registry_key = str(row.get("registry_key") or "")
    document_id = str(row.get("document_id") or "").strip()
    if not document_id and registry_key.startswith("doc:"):
        document_id = registry_key[4:]
    if document_id and document_id != "0":
        keys.append(("document_id", document_id))
    fingerprint = str(row.get("oshash") or "").strip().lower()
    if len(fingerprint) == 16 and all(ch in "0123456789abcdef" for ch in fingerprint):
        keys.insert(0, ("oshash", fingerprint))
    filename = str(row.get("filename") or row.get("media_filename") or "").strip()
    size = int(row.get("size_bytes") or row.get("media_size") or 0)
    width = int(row.get("width") or row.get("media_width") or 0)
    height = int(row.get("height") or row.get("media_height") or 0)
    duration = round(float(row.get("duration") or row.get("media_duration") or 0), 2)
    keys.append(("signature", (dup_canonical_key(filename), size, width, height, duration)))
    return keys


def _transfer_saved_destination_index(db, channel_id):
    """Return None unless Archive X has a completed inventory for this channel."""
    if db is None:
        return None
    try:
        with db.reading() as conn:
            state = conn.execute(
                "SELECT last_scan_status FROM telegram_channel_registry_state WHERE channel_id=? LIMIT 1",
                (str(channel_id),),
            ).fetchone()
            if not state or str(state["last_scan_status"] or "").upper() != "COMPLETE":
                return None
            # Query saved document identity and prepared metadata first. The media
            # join is a compatibility path for older records without registry data.
            rows = conn.execute(
                "SELECT l.message_id AS message_id,l.registry_key,r.document_id,"
                "COALESCE(NULLIF(LOWER(TRIM(r.oshash)),''),NULLIF(LOWER(TRIM(m.uploaded_oshash)),''),"
                "NULLIF(LOWER(TRIM(m.oshash)),''),'') AS oshash,"
                "COALESCE(NULLIF(r.filename,''),m.filename,'') AS filename,"
                "COALESCE(NULLIF(r.size_bytes,0),m.size,0) AS size_bytes,"
                "COALESCE(NULLIF(r.duration,0),m.duration,0) AS duration,"
                "COALESCE(NULLIF(r.width,0),m.width,0) AS width,"
                "COALESCE(NULLIF(r.height,0),m.height,0) AS height "
                "FROM telegram_document_locations l "
                "LEFT JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
                "LEFT JOIN media m ON CAST(m.source_channel AS TEXT)=l.channel_id "
                "AND CAST(m.source_message_id AS INTEGER)=l.message_id "
                "WHERE l.channel_id=? AND l.location_state='PRESENT' "
                "UNION ALL "
                "SELECT CAST(m.source_message_id AS INTEGER) AS message_id,'' AS registry_key,'' AS document_id,"
                "COALESCE(NULLIF(LOWER(TRIM(m.uploaded_oshash)),''),NULLIF(LOWER(TRIM(m.oshash)),''),'') AS oshash,"
                "COALESCE(m.filename,''),COALESCE(m.size,0),COALESCE(m.duration,0),COALESCE(m.width,0),COALESCE(m.height,0) "
                "FROM media m WHERE CAST(m.source_channel AS TEXT)=? "
                "AND NOT EXISTS(SELECT 1 FROM telegram_document_locations l WHERE l.channel_id=? "
                "AND CAST(l.message_id AS INTEGER)=CAST(m.source_message_id AS INTEGER)) "
                "AND COALESCE(m.status,'')<>'SOURCE_DELETED'",
                (str(channel_id), str(channel_id), str(channel_id)),
            ).fetchall()
        index = {}
        for row in rows:
            record = dict(row)
            message_id = int(record.get("message_id") or 0)
            if message_id <= 0:
                continue
            for key in _transfer_duplicate_index_from_record(record):
                index.setdefault(key, []).append(message_id)
        return index, len(rows)
    except Exception as exc:
        try:
            db.log(f"Transfer destination registry index failed for channel {channel_id}: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            pass
        return None


async def build_transfer_destination_duplicate_index(client, destination):
    """Index destination videos by Telegram document ID, saved OSHASH and metadata."""
    print("\n" + "=" * 90)
    print(" DESTINATION DUPLICATE CHECK")
    print("=" * 90)
    print(f"Destination         : {channel_display_name(destination)}")
    print("Scan bytes          : NO full media download")
    started = time.monotonic()
    index = {}
    scanned = videos = 0
    last_print = started
    destination_ids = []
    db = globals().get("MEDIA_MANAGER_DB")
    saved_index = await asyncio.to_thread(_transfer_saved_destination_index, db, canonical_peer_id(destination))
    if saved_index is not None:
        index, saved_count = saved_index
        elapsed = max(time.monotonic() - started, 0.001)
        print(f"[DESTINATION INDEX REUSED] Registry rows: {saved_count:,} | keys: {len(index):,} | no Telegram history scan | elapsed: {elapsed:.2f}s")
        return index
    async for message in client.iter_messages(destination, reverse=True):
        scanned += 1
        if scanned % 100 == 0:
            await transfer_control_checkpoint()
        if not message:
            continue
        if not (getattr(message, "video", None) or is_document_video_message(message)):
            continue
        videos += 1
        message_id = int(message.id)
        destination_ids.append(message_id)
        for key in _transfer_duplicate_keys(message):
            index.setdefault(key, []).append(message_id)
        now = time.monotonic()
        if now - last_print >= 1.0:
            print(f"\r[DESTINATION INDEX] Messages: {scanned:,} | Videos: {videos:,}", end="", flush=True)
            last_print = now
    oshashes = await asyncio.to_thread(_transfer_saved_oshashes, db, canonical_peer_id(destination))
    for message_id in destination_ids:
        fingerprint = oshashes.get(message_id)
        if fingerprint:
            index.setdefault(("oshash", fingerprint), []).append(message_id)
    elapsed = max(time.monotonic() - started, 0.001)
    print(f"\n[DESTINATION INDEX COMPLETE] Messages: {scanned:,} | Videos: {videos:,} | Elapsed: {elapsed:.1f}s")
    return index


def transfer_destination_duplicate_ids(index, message, oshash=""):
    """Return destination IDs matching document ID, OSHASH or legacy signature."""
    matches = []
    seen = set()
    for key in _transfer_duplicate_keys(message, oshash):
        for message_id in index.get(key, []):
            try:
                value = int(message_id)
            except (TypeError, ValueError):
                continue
            if value not in seen:
                seen.add(value)
                matches.append(value)
    return matches


# ==============================================================================
# SECTION: Candidate scan and metadata
# Prefetch, candidate scan and metadata child jobs.
# ==============================================================================

async def transfer_prefetch_metadata(client, source, candidates, state, db=None):
    """Prefetch transfer metadata before any destination upload/copy begins.

    The scan has already captured cheap Telegram-side facts (message id,
    filename, byte size, dimensions). This pass resolves the expensive pieces
    once: OSHASH/provider metadata and the final caption. Results are persisted
    in the transfer state so a later item does not repeat the same range/API
    work after a restart. Concurrency is deliberately small to avoid turning
    metadata prefetch into an upload-rate/flood-wait problem.
    """
    if not candidates:
        return
    await transfer_control_checkpoint()
    print("\n" + "=" * 90)
    print(" PREFETCH — TELEGRAM METADATA")
    print("=" * 90)
    print("Upload/copy starts only after this transfer metadata pass completes.")
    print("Captured/reused: filename, Telegram byte size, dimensions, persisted OSHASH/provider metadata/caption.")
    print("Policy: Transfer Manager consumes persisted Archive X metadata; it does not rerun Metadata & Assets/provider matching.")
    state.setdefault("metadata_prefetch", {})
    sem = asyncio.Semaphore(2)
    started = time.monotonic()

    async def one(index, message):
        """Prefetch metadata for one message and save it in the job state."""
        key = media_mode_key(source, source, message.id, "PREFETCH", "PREFETCH")
        # The prefetch key is source/message based so the same metadata can be
        # reused for VIDEO->VIDEO, VIDEO->FILE and FILE->VIDEO routes.
        key = f"{canonical_peer_id(source)}:{int(message.id)}"
        existing = dict(state["metadata_prefetch"].get(key) or {})
        if existing.get("caption") and existing.get("status") == "READY":
            return existing
        await transfer_control_checkpoint()
        existing.update({
            "source_message_id": int(message.id),
            "filename": safe_video_name(message),
            "size": int(message_size(message) or 0),
            "size_human": human_size(message_size(message)),
        })
        try:
            actual, width, height, rotation = effective_orientation(message)
            existing.update({"width": int(width or 0), "height": int(height or 0), "orientation": actual, "rotation": int(rotation or 0)})
        except Exception:
            pass
        try:
            async with sem:
                caption = await _prepare_transfer_catalogue_caption(db, source, message)
                from ..engines.thumbnail_cache import playback_snapshot_cached_sync
                snapshot = await asyncio.to_thread(
                    playback_snapshot_cached_sync, db, int(canonical_peer_id(source)), int(message.id)
                ) if db is not None else None
            existing["caption"] = caption
            existing["playback_snapshot_ready"] = bool(snapshot)
            existing["thumbnail_id"] = None
            existing["thumbnail_type"] = "playback-snapshot" if snapshot else None
            existing["status"] = "READY"
        except Exception as exc:
            existing["status"] = "PARTIAL"
            existing["error"] = f"{type(exc).__name__}: {exc}"
        state["metadata_prefetch"][key] = existing
        transfer_job_state_save(state)
        if index % 10 == 0 or index == len(candidates):
            ready = sum(1 for x in state["metadata_prefetch"].values() if x.get("status") == "READY")
            total_known = sum(len(v) for v in (state.get("scan_manifest") or {}).values()) or len(candidates)
            print(f"\r[PREFETCH] {ready:,}/{total_known:,} | metadata ready: {ready:,}", end="", flush=True)
        return existing

    await asyncio.gather(*(one(i, m) for i, m in enumerate(candidates, 1)))
    ready = sum(1 for x in state["metadata_prefetch"].values() if x.get("status") == "READY")
    elapsed = max(time.monotonic() - started, 0.001)
    state["metadata_prefetch_summary"] = {
        "items": len(candidates), "ready": ready, "elapsed_seconds": round(elapsed, 3),
        "captured_before_upload": True,
    }
    transfer_job_state_save(state)
    print(f"\n[PREFETCH COMPLETE] {len(candidates):,} item(s) | metadata ready: {ready:,} | elapsed: {elapsed:.1f}s")


async def transfer_scan_candidates(client, source, source_mode, orientation, state=None):
    """Full oldest->newest scan with durable manifest checkpoints.

    The manifest is written during the scan, not only after it completes. If
    Windows/power is lost halfway through a 7,000-message channel, the next
    explicit Resume continues from the last Telegram message ID already saved.
    """
    print("\n" + "=" * 90)
    print(" SOURCE SCAN — TRANSFER JOB")
    print("=" * 90)
    print("Order              : OLDEST -> NEWEST")
    print(f"Source media       : {source_mode}")
    print(f"Orientation        : {orientation}")
    print("Full video download : NO during scan")
    print("Scan scope          : video messages / video documents only")
    print()

    source_key = str(canonical_peer_id(source))
    scan_manifest = []
    scan_progress = {}
    seen_ids = set()
    if isinstance(state, dict):
        scan_manifest = list((state.setdefault("scan_manifest", {}).get(source_key) or []))
        scan_progress = state.setdefault("scan_progress", {}).get(source_key) or {}
        seen_ids = {int(x.get("message_id")) for x in scan_manifest if x.get("message_id")}

    started = time.monotonic()
    last_progress = started
    last_id = int(scan_progress.get("last_id") or 0)
    reconnects = 0
    batch_size = 200
    completed = bool(scan_progress.get("complete"))
    scanned = video_count = candidate_count = 0
    candidates = []
    unknown_dimensions = 0

    if completed:
        print(f"Saved scan already complete: {len(scan_manifest):,} item(s).")
        return scan_manifest

    # A completed Archive X inventory is the source of truth. The Inventory
    # child job runs immediately before this function and incrementally updates
    # records beyond its high-water mark. Use known message IDs rather than
    # re-iterating the channel's entire Telegram history.
    inventory_db = globals().get("MEDIA_MANAGER_DB")
    inventory_complete = False
    known_ids = []
    if inventory_db is not None:
        try:
            with inventory_db.reading() as conn:
                state_row = conn.execute(
                    "SELECT high_water_mark,last_scan_status,last_scan_at FROM telegram_channel_registry_state WHERE channel_id=? LIMIT 1",
                    (source_key,),
                ).fetchone()
                inventory_complete = bool(state_row and str(state_row["last_scan_status"] or "").upper() == "COMPLETE")
                if inventory_complete:
                    rows = conn.execute(
                        "SELECT CAST(source_message_id AS INTEGER) AS message_id FROM media "
                        "WHERE CAST(source_channel AS TEXT)=? AND COALESCE(status,'')<>'SOURCE_DELETED' "
                        "UNION SELECT CAST(message_id AS INTEGER) AS message_id FROM telegram_document_locations "
                        "WHERE channel_id=? AND location_state='PRESENT' ORDER BY message_id",
                        (source_key, source_key),
                    ).fetchall()
                    known_ids = sorted({int(row["message_id"]) for row in rows if int(row["message_id"] or 0) > 0})
        except Exception as exc:
            try: inventory_db.log(f"Transfer completed-inventory lookup failed channel={source_key}: {type(exc).__name__}: {exc}", "WARN")
            except Exception: pass
            inventory_complete = False
    if inventory_complete:
        print(f"Using completed Archive X inventory: {len(known_ids):,} known message ID(s); no Telegram history rescan.")
        for offset in range(0, len(known_ids), 100):
            await transfer_control_checkpoint()
            batch_ids = known_ids[offset:offset + 100]
            batch = await client.get_messages(source, ids=batch_ids)
            if not isinstance(batch, list):
                batch = [batch] if batch else []
            for message in batch:
                if not message:
                    continue
                try:
                    source_video = (
                        bool(getattr(message, "video", None))
                        if source_mode == "VIDEO"
                        else (is_document_video_message(message) and not getattr(message, "video", None))
                    )
                    if not source_video:
                        continue
                    actual_orientation, width, height, _ = effective_orientation(message)
                    if actual_orientation == "UNKNOWN": unknown_dimensions += 1
                    allowed = orientation == "BOTH" or actual_orientation == orientation
                    if orientation == "BOTH" and actual_orientation == "UNKNOWN": allowed = True
                    if not allowed: continue
                    candidates.append(message)
                    mid = int(message.id)
                    if mid not in seen_ids:
                        seen_ids.add(mid)
                        scan_manifest.append({
                            "message_id": mid, "filename": safe_video_name(message),
                            "size": int(message_size(message) or 0),
                            "size_human": human_size(message_size(message)),
                            "width": int(width or 0), "height": int(height or 0),
                        })
                except Exception as exc:
                    print(f"\n[INVENTORY WARNING] msg={getattr(message, 'id', '?')} {type(exc).__name__}: {exc}")
        if isinstance(state, dict):
            state.setdefault("scan_manifest", {})[source_key] = scan_manifest
            state.setdefault("scan_progress", {})[source_key] = {
                "last_id": max(known_ids, default=0), "complete": True,
                "source": "archive_x_completed_inventory", "high_water_mark": int(state_row["high_water_mark"] or 0) if state_row else 0,
            }
            transfer_job_state_save(state)
        print(f"[INVENTORY REUSE] {len(candidates):,} eligible message(s); no full-history iteration.")
        return candidates

    while not completed:
        batch_received = 0
        try:
            if not client.is_connected():
                reconnects += 1
                await connect_with_retry(client, label=f"transfer scan reconnect #{reconnects}")
            async for message in client.iter_messages(source, reverse=True, min_id=last_id, limit=batch_size):
                batch_received += 1
                scanned += 1
                if scanned % 25 == 0:
                    await transfer_control_checkpoint()
                if not message:
                    continue
                last_id = int(message.id)
                try:
                    source_video = (
                        bool(getattr(message, "video", None))
                        if source_mode == "VIDEO"
                        else (is_document_video_message(message) and not getattr(message, "video", None))
                    )
                    if not source_video:
                        continue
                    video_count += 1
                    actual_orientation, width, height, _ = effective_orientation(message)
                    if actual_orientation == "UNKNOWN":
                        unknown_dimensions += 1
                    allowed = orientation == "BOTH" or actual_orientation == orientation
                    if orientation == "BOTH" and actual_orientation == "UNKNOWN":
                        allowed = True
                    if allowed:
                        candidate_count += 1
                        candidates.append(message)
                        if int(message.id) not in seen_ids:
                            seen_ids.add(int(message.id))
                            scan_manifest.append({
                                "message_id": int(message.id),
                                "filename": safe_video_name(message),
                                "size": int(message_size(message) or 0),
                                "size_human": human_size(message_size(message)),
                                "width": int(width or 0),
                                "height": int(height or 0),
                            })
                except Exception as exc:
                    print(f"\n[SCAN WARNING] msg={getattr(message, 'id', '?')} {type(exc).__name__}: {exc}")

                now = time.monotonic()
                if now - last_progress >= 1.0:
                    print(
                        f"\r[SCAN] Messages: {scanned:,} | Video items: {video_count:,} | "
                        f"Selected: {candidate_count:,} | Unknown orientation: {unknown_dimensions:,} | "
                        f"Last msg: {last_id}", end="", flush=True
                    )
                    last_progress = now

            if isinstance(state, dict):
                state.setdefault("scan_manifest", {})[source_key] = scan_manifest
                state.setdefault("scan_progress", {})[source_key] = {"last_id": last_id, "complete": False}
                transfer_job_state_save(state)
            if batch_received < batch_size:
                completed = True
        except Exception as exc:
            wait = flood_wait_seconds(exc)
            if wait:
                wait += 2
                print(f"\n[SCAN] Telegram rate limit. Waiting {wait}s and resuming from msg {last_id}...")
                await asyncio.sleep(wait)
                continue
            if not is_transient_telegram_connection_error(exc):
                raise
            reconnects += 1
            print(f"\n[SCAN] Connection dropped at msg {last_id}: {type(exc).__name__}: {exc}")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(min(10, 2 + reconnects))
            await connect_with_retry(client, label=f"transfer scan reconnect #{reconnects}")

    if isinstance(state, dict):
        state.setdefault("scan_manifest", {})[source_key] = scan_manifest
        state.setdefault("scan_progress", {})[source_key] = {"last_id": last_id, "complete": True}
        transfer_job_state_save(state)

    elapsed = max(time.monotonic() - started, 0.001)
    print(f"\n[SCAN COMPLETE] Messages: {scanned:,} | Video items: {video_count:,} | Selected: {len(scan_manifest):,} | Unknown: {unknown_dimensions:,} | Elapsed: {int(elapsed)//60:02d}:{int(elapsed)%60:02d}")
    return candidates


def _preparation_only_incomplete(row) -> bool:
    """True when a Metadata & Assets child finished every phase but some videos
    are still not ready (V29.4.132 reports that as INCOMPLETE / Completed with
    errors instead of a false 100%). That is how transfers always proceeded;
    only a phase that actually failed stops the transfer."""
    try:
        detail = json.loads((row or {}).get("detail") or "{}")
    except Exception:
        return False
    queue = detail.get("channel_queue") if isinstance(detail.get("channel_queue"), list) else []
    if not queue:
        return False
    for entry in queue:
        if not isinstance(entry, dict):
            return False
        if str(entry.get("state") or "").upper() not in {"COMPLETED", "INCOMPLETE"}:
            return False
        phases = entry.get("phases") if isinstance(entry.get("phases"), dict) else {}
        if any(str(v or "").upper() == "FAILED" for v in phases.values()):
            return False
    return True


def _preparation_gap_note(row) -> str:
    try:
        detail = json.loads((row or {}).get("detail") or "{}")
        return f"{int(detail.get('overall_not_in_library') or 0)} video(s) not fully prepared"
    except Exception:
        return "some videos not fully prepared"


async def _run_metadata_child_job(db, manager, manager_loop, *, parent_job_id, channel_ids, stages, label, existing_job_id=None):
    """Run canonical Metadata & Assets phases as child jobs of one transfer workflow."""
    if not channel_ids:
        return None
    if existing_job_id:
        jid = str(existing_job_id)
        existing = db.get_job(jid) or {}
        if str(existing.get("status") or "") == "COMPLETED":
            return jid
        if str(existing.get("status") or "") == "COMPLETED_WITH_ERRORS" and _preparation_only_incomplete(existing):
            return jid
        if str(existing.get("status") or "") in {"FAILED","CANCELLED","COMPLETED_WITH_ERRORS"}:
            raise RuntimeError(f"Metadata & Assets preparation child job {jid} ended {existing.get('status')}. Open that child job in Operations for the detailed error.")
        if manager is None or manager_loop is None:
            raise RuntimeError("Telegram Transfer Manager cannot resume Metadata & Assets preparation")
        manager_loop.call_soon_threadsafe(manager.submit, jid)
    else:
        detail = {"channel_ids":[str(x) for x in channel_ids],"stages":list(stages),"limit":0,"unbounded":True,
                  "parent_transfer_job_id":str(parent_job_id or ""),"transfer_preparation":True,"preparation_label":str(label)}
        jid = db.create_job("METADATA_CHANNEL_PIPELINE", ",".join(str(x) for x in channel_ids), "", False, detail=json.dumps(detail))
    try:
        parent = db.get_job(str(parent_job_id)) or {}
        parent_detail = json.loads(parent.get("detail") or "{}") if parent else {}
        prep = parent_detail.setdefault("preparation_jobs", {})
        prep["inventory" if "inventory" in stages else "metadata_assets"] = jid
        db.update_job(str(parent_job_id), detail=json.dumps(parent_detail))
    except Exception:
        pass
    db.update_job(str(parent_job_id), current_operation=f"Preparing · {label}")
    db.log(f"Telegram Transfer Manager: {label} child Metadata & Assets job {jid} queued for parent {parent_job_id}", "INFO", event="telegram_transfer.preparation_queued", job_id=parent_job_id)
    if manager is None or manager_loop is None:
        raise RuntimeError("Telegram Transfer Manager cannot schedule Metadata & Assets preparation")
    manager_loop.call_soon_threadsafe(manager.submit, jid)
    while True:
        await transfer_control_checkpoint()
        row = db.get_job(jid) or {}
        status = str(row.get("status") or "")
        if status in {"COMPLETED","COMPLETED_WITH_ERRORS","FAILED","CANCELLED"}:
            if status == "COMPLETED_WITH_ERRORS" and _preparation_only_incomplete(row):
                db.log(f"Telegram Transfer Manager: {label} child job {jid} finished with {_preparation_gap_note(row)}; continuing the transfer",
                       "WARN", event="telegram_transfer.preparation_incomplete", job_id=parent_job_id)
            elif status != "COMPLETED":
                raise RuntimeError(f"Metadata & Assets preparation child job {jid} ended {status}. Open that child job in Operations for the detailed error.")
            db.update_job(str(parent_job_id), current_operation=f"Preparation complete · {label}")
            db.log(f"Telegram Transfer Manager: {label} Metadata & Assets child job {jid} completed", "SUCCESS", event="telegram_transfer.preparation_completed", job_id=parent_job_id)
            return jid
        await asyncio.sleep(0.5)


# ==============================================================================
# SECTION: Job entry points
# Transfer, cleanup and mover main functions.
# ==============================================================================

def transfer_requires_cli_confirmation(*, preconfigured: bool, can_resume: bool, auto_confirm: bool = True) -> bool:
    """Only standalone interactive CLI runs need a generic proceed prompt.

    A web-managed job already requires the operator to click Start with its
    source/destination choices filled in. Explicit resume is also intentional.
    """
    return not bool(can_resume or (preconfigured and auto_confirm))


async def telegram_transfer_job_main(*, job_id=None, resume_job_id=None, job_detail=None, job_manager=None, manager_loop=None):
    """Guided Telegram transfer job with durable restart/resume state.

    A normal run asks for the workflow configuration and creates a persistent
    scan manifest + metadata prefetch cache. An explicit restart-resume restores
    that configuration and uses the manifest/message IDs already captured; it
    never starts a fresh 7,000-message source scan just to rebuild the queue.
    """
    if STARTUP_IMPORT_ERRORS:
        print("\nStartup prerequisites are not ready:")
        for problem in STARTUP_IMPORT_ERRORS:
            print(f"- {problem}")
        return
    from ..core.resolution_filter import (
        normalize_resolution_filter,
        resolution_tier_for_dimensions,
    )

    client = None
    print("\n" + "=" * 90)
    print(" TELEGRAM TRANSFER JOB")
    print("=" * 90)

    resume_state = transfer_job_state_load(job_id, resume=bool(resume_job_id))
    resume_config = resume_state.get("config") if isinstance(resume_state.get("config"), dict) else {}
    can_resume = bool(
        resume_job_id
        and resume_config.get("source_channels")
        and resume_config.get("destination_channel")
        and resume_config.get("source_mode")
        and resume_config.get("destination_mode")
    )

    preconfigured = isinstance(job_detail, dict) and str(job_detail.get("workflow") or "").upper() == "PROCESS" and bool(job_detail.get("source_channels"))
    # A click on "Start transfer" is the operator's confirmation. Do not stop
    # a background/web-managed job waiting for a terminal [y/N] response.
    app_managed = not transfer_requires_cli_confirmation(
        preconfigured=preconfigured,
        can_resume=can_resume,
        auto_confirm=bool(job_detail.get("auto_confirm", True)) if isinstance(job_detail, dict) else True,
    )

    if preconfigured and not can_resume:
        source_mode = str(job_detail.get("source_mode") or "VIDEO").upper()
        destination_mode = str(job_detail.get("destination_mode") or "VIDEO").upper()
        orientation = str(job_detail.get("orientation") or "BOTH").upper()
        resolution_filter = normalize_resolution_filter(job_detail.get("resolution_filter"))
        source_ids = [int(x) for x in (job_detail.get("source_channels") or [])]
        destination_id = int(str(job_detail.get("destination_channel") or "0"))
        if not source_ids or not destination_id:
            raise RuntimeError("Telegram Transfer Manager job is missing source/destination channels")
        config = load_json(CONFIG_FILE, {})
        client = await login_and_get_client(config)
        sources = [await resolve_saved_entity(client, x) for x in source_ids]
        destination = await resolve_saved_entity(client, destination_id)
        duplicate_check = job_detail.get("duplicate_check") if isinstance(job_detail.get("duplicate_check"), dict) else {}
        duplicate_policy = "SKIP" if duplicate_check.get("destination", True) else "NONE"
        cover_policy = "AUTOMATIC"
        source_count = len(sources)
        state = transfer_job_state_load(job_id, resume=False)
        state["config"] = {
            "source_mode": source_mode, "destination_mode": destination_mode, "orientation": orientation,
            "resolution_filter": resolution_filter,
            "source_count": source_count,
            "source_channels": [{"id": int(x.id), "title": channel_display_name(x)} for x in sources],
            "destination_channel": {"id": int(destination.id), "title": channel_display_name(destination)},
            "job_id": str(job_id or ""), "duplicate_policy": duplicate_policy, "cover_policy": cover_policy,
            "duplicate_check": duplicate_check, "cleanup": job_detail.get("cleanup") or {},
            "processing_policy": "INVENTORY_THEN_METADATA_ASSETS_THEN_TRANSFER",
        }
        transfer_job_state_save(state)
        print("Configured by Telegram Transfer Manager module; no interactive mode selection is required.")
    elif can_resume:
        source_mode = str(resume_config.get("source_mode") or "VIDEO")
        destination_mode = str(resume_config.get("destination_mode") or "VIDEO")
        orientation = str(resume_config.get("orientation") or "BOTH")
        resolution_filter = normalize_resolution_filter(resume_config.get("resolution_filter"))
        source_count = int(resume_config.get("source_count") or len(resume_config.get("source_channels") or []))
        duplicate_policy = str(resume_config.get("duplicate_policy") or "NONE")
        cover_policy = str(resume_config.get("cover_policy") or "AUTOMATIC")
        print("Explicit resume: restoring the saved Telegram transfer configuration.")
        print("Saved scan manifest and metadata are reused; no full source scan/prefetch is repeated.")
        config = resume_config
        client = await login_and_get_client(config)
        sources = []
        for item in config.get("source_channels") or []:
            sources.append(await resolve_saved_entity(client, int(item.get("id"))))
        destination = await resolve_saved_entity(client, int((config.get("destination_channel") or {}).get("id")))
        state = resume_state
        state.setdefault("config", {})["job_id"] = str(job_id or resume_job_id or "")
        transfer_job_state_save(state)
    else:
        print("Configure the complete job first. Channel scanning starts only after the rules are fixed.")
        resolution_filter = "ANY"

        source_mode_choice = input("\nSource media [1=VIDEO, 2=FILE]: ").strip()
        source_mode = {"1": "VIDEO", "2": "FILE"}.get(source_mode_choice)
        if not source_mode:
            print("Invalid source media selection.")
            return

        destination_mode_choice = input("Destination media [1=VIDEO, 2=FILE]: ").strip()
        destination_mode = {"1": "VIDEO", "2": "FILE"}.get(destination_mode_choice)
        if not destination_mode:
            print("Invalid destination media selection.")
            return

        if source_mode == "VIDEO" or destination_mode == "VIDEO":
            orientation_choice = input("Orientation [1=HORIZONTAL, 2=VERTICAL, 3=BOTH]: ").strip()
            orientation = {"1": "HORIZONTAL", "2": "VERTICAL", "3": "BOTH"}.get(orientation_choice)
            if not orientation:
                print("Invalid orientation selection.")
                return
        else:
            orientation = "BOTH"
            print("Orientation: not applicable for FILE -> FILE")

        duplicate_choice = input(
            "Destination duplicate check [1=NO CHECK, 2=SKIP DUPLICATES, 3=ASK]: "
        ).strip()
        duplicate_policy = TRANSFER_DUPLICATE_POLICIES.get(duplicate_choice)
        if not duplicate_policy:
            print("Invalid duplicate-check selection.")
            return

        cover_policy = "AUTOMATIC"
        if destination_mode == "VIDEO" and source_mode in {"VIDEO", "FILE"}:
            cover_choice = input(
                "Video cover [1=AUTOMATIC - reuse valid existing, 2=ALWAYS USE EXISTING, 3=ALWAYS GENERATE NEW]: "
            ).strip()
            cover_policy = {"1": "AUTOMATIC", "2": "EXISTING", "3": "GENERATE"}.get(cover_choice)
            if not cover_policy:
                print("Invalid video-cover selection.")
                return

        try:
            source_count = int(input("How many source channels? ").strip())
        except ValueError:
            source_count = 0
        if source_count < 1:
            print("Source channel count must be at least 1.")
            return

        config = load_json(CONFIG_FILE, {})
        client = await login_and_get_client(config)
        catalog = await load_channel_catalog(client)
        blocked = set()
        sources = []
        print("\nSOURCE CHANNELS")
        for idx in range(source_count):
            source = await choose_channel_from_catalog(
                client, catalog, f"SOURCE CHANNEL {idx + 1}",
                blocked_ids=blocked, show_catalog=(idx == 0)
            )
            sources.append(source)
            blocked.add(int(source.id))

        destination = await choose_channel_from_catalog(
            client, catalog, "DESTINATION CHANNEL", blocked_ids=set(), show_catalog=False
        )

        state = transfer_job_state_load(job_id, resume=False)
        state["config"] = {
            "source_mode": source_mode,
            "destination_mode": destination_mode,
            "orientation": orientation,
            "resolution_filter": resolution_filter,
            "source_count": source_count,
            "source_channels": [{"id": int(s.id), "title": channel_display_name(s)} for s in sources],
            "destination_channel": {"id": int(destination.id), "title": channel_display_name(destination)},
            "job_id": str(job_id or ""),
            "duplicate_policy": duplicate_policy,
            "cover_policy": cover_policy,
            "processing_policy": "ONE_SOURCE_CHANNEL_AT_A_TIME",
        }
        transfer_job_state_save(state)

    # Normalize every participating Telegram entity once after resolving it.
    # Archive X's catalogue uses marked peer IDs; entity.id is raw-positive for
    # channels, and both source and destination registry state must use the same key.
    identity_db = globals().get("MEDIA_MANAGER_DB")
    identity_entities = []
    seen_identity_ids = set()
    for entity in [*sources, destination]:
        marked_channel_id = str(canonical_peer_id(entity))
        if not marked_channel_id or marked_channel_id in seen_identity_ids:
            continue
        seen_identity_ids.add(marked_channel_id)
        identity_entities.append(entity)
        raw_channel_id = str(int(getattr(entity, "id", 0) or 0))
        if marked_channel_id != raw_channel_id and identity_db is not None:
            try:
                from ..core.telegram_identity_migration import rekey_legacy_channel_rows
                from .. import config as app_config
                roots = [
                    app_config.THUMBNAIL_DIR,
                    app_config.PLAYER_CACHE_DIR / "preview_frames",
                    app_config.PLAYER_CACHE_DIR / "mp4_index",
                    app_config.PLAYER_CACHE_DIR / "telegram_locations",
                    app_config.PLAYER_CACHE_DIR / "telegram_delivery_contact_sheets",
                ]
                migration = await asyncio.to_thread(
                    rekey_legacy_channel_rows, identity_db, raw_channel_id, marked_channel_id, roots
                )
                changed = sum(int(value or 0) for value in migration.values())
                if changed:
                    identity_db.log(
                        f"Transfer channel identity repaired raw={raw_channel_id} canonical={marked_channel_id}: "
                        + ", ".join(f"{k}={v}" for k, v in migration.items() if v), "INFO"
                    )
            except Exception as migration_exc:
                identity_db.log(
                    f"Transfer channel identity repair failed channel={channel_display_name(entity)} "
                    f"raw={raw_channel_id} canonical={marked_channel_id}: "
                    f"{type(migration_exc).__name__}: {migration_exc}", "ERROR"
                )
                raise
    normalized_sources = [
        {"id": int(canonical_peer_id(source)), "title": channel_display_name(source)}
        for source in sources
    ]
    state.setdefault("config", {})["source_channels"] = normalized_sources
    state["config"]["destination_channel"] = {
        "id": int(canonical_peer_id(destination)), "title": channel_display_name(destination)
    }
    # Existing interrupted states may have raw-positive nested keys; promote them
    # once so resuming uses the canonical record book and does not redo preparation.
    for source in sources:
        raw_key, marked_key = str(int(source.id)), str(canonical_peer_id(source))
        for field in ("scan_manifest", "scan_progress", "database_inventory", "duplicate_analysis"):
            table = state.get(field)
            if isinstance(table, dict) and raw_key in table:
                table.setdefault(marked_key, table[raw_key])
                table.pop(raw_key, None)
        meta = state.get("metadata_prefetch")
        if isinstance(meta, dict):
            for key in list(meta):
                if key.startswith(raw_key + ":"):
                    meta.setdefault(marked_key + key[len(raw_key):], meta[key])
                    meta.pop(key, None)
        items = state.get("items")
        raw_destination, marked_destination = str(int(destination.id)), str(canonical_peer_id(destination))
        if isinstance(items, dict):
            for key in list(items):
                prefix = f"{raw_key}:{raw_destination}:"
                if key.startswith(prefix):
                    items.setdefault(f"{marked_key}:{marked_destination}:" + key[len(prefix):], items[key])
                    items.pop(key, None)
    # Do not attach a previously completed child that targeted raw-positive IDs.
    # Requeue against canonical IDs; the migrated record book makes it incremental.
    child_jobs = state.setdefault("preparation_jobs", {})
    if identity_db is not None:
        expected_source_ids = sorted(str(canonical_peer_id(src)) for src in sources)
        expected_inventory_ids = sorted(str(canonical_peer_id(entity)) for entity in identity_entities)
        for child_name in ("inventory", "metadata_assets"):
            child_id = child_jobs.get(child_name)
            if not child_id:
                continue
            try:
                child_row = identity_db.get_job(str(child_id)) or {}
                child_detail = json.loads(child_row.get("detail") or "{}")
                prior_ids = sorted(str(x) for x in (child_detail.get("channel_ids") or []))
                expected_ids = expected_inventory_ids if child_name == "inventory" else expected_source_ids
                if prior_ids and prior_ids != expected_ids:
                    child_jobs[child_name] = None
            except Exception:
                child_jobs[child_name] = None
    transfer_job_state_save(state)

    print("\n" + "=" * 90)
    print(" TRANSFER JOB CONFIGURATION")
    print("=" * 90)
    print(f"Source media       : {source_mode}")
    print(f"Destination media  : {destination_mode}")
    print(f"Orientation        : {orientation}")
    print(f"Source channels    : {len(sources)}")
    for n, src in enumerate(sources, 1):
        print(f"  {n}. {channel_display_name(src)}")
    print(f"Destination        : {channel_display_name(destination)}")
    print(f"Destination duplicate check: {duplicate_policy}")
    if destination_mode == "VIDEO":
        print(f"Video cover         : {cover_policy} (Archive X playback snapshot reused automatically)")
    print("Processing          : ONE SOURCE CHANNEL AT A TIME")
    print("Workspace           : only the current transfer item may use disk")
    print("Metadata            : persisted Archive X catalogue; no provider refresh during transfer")
    print("Thumbnail           : Archive X playback snapshot (representative frame from retained 36-frame set)")
    print("Caption             : title / studio / performers / date only; tags excluded")
    print("Source deletion     : ONLY after destination verification")
    print("Duplicate cleanup   : separate job; not mixed into this transfer")
    if source_mode == "FILE" and destination_mode == "VIDEO":
        print("FILE -> VIDEO       : temporary download + conversion/upload required")
    else:
        print("Transfer            : Telegram-side copy where supported")

    if not app_managed:
        if not ask_yes_no("Proceed with this transfer job?", default=False):
            print("Cancelled. No Telegram messages were changed.")
            return
    else:
        print("Transfer confirmation: accepted from Archive X; continuing without a CLI prompt.")

    # Unified workflow preparation: inventory is persisted first, then optional
    # cleanup/duplicate inspection, then the remaining Metadata & Assets phases.
    preparation_channels = [str(canonical_peer_id(s)) for s in sources]
    child_jobs = state.setdefault("preparation_jobs", {})
    inventory_channels = list(dict.fromkeys(
        str(canonical_peer_id(entity)) for entity in identity_entities
    ))
    inventory_job = await _run_metadata_child_job(
        globals().get("MEDIA_MANAGER_DB"), job_manager, manager_loop,
        parent_job_id=job_id, channel_ids=inventory_channels,
        stages=["inventory"], label="Inventory", existing_job_id=child_jobs.get("inventory"),
    )
    child_jobs["inventory"] = inventory_job
    transfer_job_state_save(state)

    # Unified workflow: optional cleanup and source-side duplicate analysis happen
    # before the detailed Metadata & Assets phases. They are recorded in the same durable job state.
    unified_cleanup = state.get("config", {}).get("cleanup") if isinstance(state.get("config"), dict) else {}
    if isinstance(unified_cleanup, dict) and unified_cleanup.get("enabled"):
        trash_id = int(str(unified_cleanup.get("trash_channel") or "0"))
        trash = await resolve_saved_entity(client, trash_id) if trash_id else None
        if trash is None:
            raise RuntimeError("Cleanup is enabled but no trash channel was configured")
        print("\n[UNIFIED CLEANUP] Inspecting broken/orphaned media before transfer...")
        await channel_cleanup_job_main(client_override=client, source_overrides=sources, trash_override=trash, auto_confirm=True, disconnect_client=False)
        print("[UNIFIED CLEANUP] Cleanup phase complete; transfer inventory follows.")

    destination_duplicate_index = {}
    duplicate_check = state.get("config", {}).get("duplicate_check") if isinstance(state.get("config"), dict) else {}
    if duplicate_check.get("source"):
        state.setdefault("duplicate_analysis", {})
        for source_for_dup in sources:
            try:
                audit = await duplicate_scan(client, source_for_dup)
                state["duplicate_analysis"][str(canonical_peer_id(source_for_dup))] = audit.get("stats") or {"actions": len(audit.get("actions") or [])}
                print(f"[DUPLICATE CHECK] {channel_display_name(source_for_dup)}: {len(audit.get('actions') or []):,} duplicate candidate action(s)")
            except Exception as exc:
                state["duplicate_analysis"][str(canonical_peer_id(source_for_dup))] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"[DUPLICATE CHECK] {channel_display_name(source_for_dup)} failed: {type(exc).__name__}: {exc}")
        transfer_job_state_save(state)
    if duplicate_check.get("destination", True):
        destination_duplicate_index = await build_transfer_destination_duplicate_index(client, destination)

    metadata_job = await _run_metadata_child_job(
        globals().get("MEDIA_MANAGER_DB"), job_manager, manager_loop,
        parent_job_id=job_id, channel_ids=preparation_channels,
        stages=["metadata", "performers", "channel_data", "snapshots"],
        label="Metadata & Assets", existing_job_id=child_jobs.get("metadata_assets"),
    )
    child_jobs["metadata_assets"] = metadata_job
    transfer_job_state_save(state)

    total_complete = total_failed = total_skipped = total_duplicates = 0

    try:
        # Critical behavior: finish source #1 completely before scanning source #2.
        for source_index, source in enumerate(sources, 1):
            await transfer_control_checkpoint()
            print("\n" + "#" * 96)
            print(f" SOURCE CHANNEL {source_index}/{len(sources)}")
            print(f" {channel_display_name(source)}")
            print("#" * 96)

            source_key = str(canonical_peer_id(source))
            saved_manifest = (state.get("scan_manifest") or {}).get(source_key) or []
            saved_scan_complete = bool((state.get("scan_progress") or {}).get(source_key, {}).get("complete"))

            async def restore_manifest(records):
                ids = [int(x.get("message_id")) for x in records if x.get("message_id")]
                restored = []
                for pos in range(0, len(ids), 100):
                    await transfer_control_checkpoint()
                    batch = await client.get_messages(source, ids=ids[pos:pos + 100])
                    if not isinstance(batch, list):
                        batch = [batch] if batch else []
                    by_id = {int(getattr(m, "id", 0)): m for m in batch if m}
                    restored.extend(by_id[mid] for mid in ids[pos:pos + 100] if mid in by_id)
                print(f"Restored {len(restored):,}/{len(ids):,} source messages from the saved manifest.")
                return restored

            if can_resume and saved_manifest and saved_scan_complete:
                print(f"Restoring saved scan manifest: {len(saved_manifest):,} item(s). No full source scan required.")
                candidates = await restore_manifest(saved_manifest)
            elif can_resume and saved_manifest and not saved_scan_complete:
                print(f"Resuming an interrupted source scan from message {(state.get('scan_progress') or {}).get(source_key, {}).get('last_id', 0):,}; {len(saved_manifest):,} candidates are already saved.")
                await transfer_scan_candidates(client, source, source_mode, orientation, state=state)
                candidates = await restore_manifest((state.get("scan_manifest") or {}).get(source_key) or [])
            else:
                candidates = await transfer_scan_candidates(client, source, source_mode, orientation, state=state)
                if candidates and isinstance(candidates[0], dict):
                    candidates = await restore_manifest(candidates)

            if resolution_filter != "ANY":
                checked = len(candidates)
                selected = []
                unknown_dimensions = 0
                for message in candidates:
                    try:
                        _, width, height, _ = effective_orientation(message)
                    except Exception:
                        width = height = 0
                    tier = resolution_tier_for_dimensions(width, height)
                    if tier == resolution_filter:
                        selected.append(message)
                    elif tier is None:
                        unknown_dimensions += 1
                candidates = selected
                summary = {
                    "selection": resolution_filter,
                    "checked": checked,
                    "selected": len(candidates),
                    "excluded": checked - len(candidates),
                    "unknown_dimensions": unknown_dimensions,
                }
                state.setdefault("resolution_filter_summary", {})[source_key] = summary
                transfer_job_state_save(state)
                print(
                    f"[RESOLUTION FILTER] {resolution_filter}: kept {len(candidates):,}/"
                    f"{checked:,} source videos; {unknown_dimensions:,} had unknown dimensions."
                )

            if not candidates:
                print("No matching items in this source channel. Moving to the next source channel.")
                continue

            # The scan function already writes the manifest incrementally and marks
            # it complete. Never overwrite a partial manifest with only the newly
            # scanned tail after a power loss.
            transfer_job_state_save(state)

            if (state.get("config", {}).get("duplicate_check") or {}).get("database", True):
                known = 0
                if globals().get("MEDIA_MANAGER_DB") is not None:
                    db_for_dup = globals().get("MEDIA_MANAGER_DB")
                    with db_for_dup.lock:
                        for msg in candidates:
                            row = db_for_dup.conn.execute("SELECT 1 FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1", (canonical_peer_id(source), int(msg.id))).fetchone()
                            if row: known += 1
                state.setdefault("database_inventory", {})[str(canonical_peer_id(source))] = {"scanned": len(candidates), "already_recorded": known, "new_or_unrecorded": max(0, len(candidates)-known)}
                transfer_job_state_save(state)
                print(f"[DATABASE CHECK] {known:,}/{len(candidates):,} source items already recorded; {max(0, len(candidates)-known):,} new/unrecorded")

            # Legacy report marker retained: Queued file sizes captured before transfer:
            metadata_map = state.setdefault("metadata_prefetch", {})
            pending_metadata = [
                m for m in candidates
                if (metadata_map.get(f"{canonical_peer_id(source)}:{int(m.id)}") or {}).get("status") != "READY"
            ]
            if pending_metadata:
                print(f"Metadata: {len(candidates) - len(pending_metadata):,} ready, {len(pending_metadata):,} still pending.")
                await transfer_prefetch_metadata(
                    client, source, pending_metadata, state,
                    db=globals().get("MEDIA_MANAGER_DB")
                )
            else:
                print(f"Metadata: all {len(candidates):,} items are already ready; skipping provider/thumbnail prefetch.")

            # Read known OSHASH values once per source channel. Document ID is
            # still the primary cross-channel identity and needs no download.
            source_db_for_dup = globals().get("MEDIA_MANAGER_DB")
            source_oshashes = await asyncio.to_thread(
                _transfer_saved_oshashes, source_db_for_dup, canonical_peer_id(source)
            )

            print("\n" + "=" * 90)
            print(" CHANNEL QUEUE SUMMARY")
            print("=" * 90)
            print(f"Source channel      : {channel_display_name(source)}")
            print(f"Queued items        : {len(candidates):,}")
            print("Processing policy   : one item at a time; next item starts only after the current item finishes")
            if not app_managed and not ask_yes_no(f"Start processing {channel_display_name(source)}?", default=False):
                print("Skipped this source channel. The next source channel will not be scanned until you choose it.")
                continue

            channel_moved = channel_failed = channel_skipped = 0
            for index, message in enumerate(candidates, 1):
                await transfer_control_checkpoint()
                key = media_mode_key(source, destination, message.id, source_mode, destination_mode)
                entry = state["items"].setdefault(key, {})
                if entry.get("status") == "COMPLETE" and entry.get("source_deleted"):
                    channel_skipped += 1
                    total_skipped += 1
                    continue
                print("\n" + "-" * 110)
                print(
                    f"CHANNEL {source_index}/{len(sources)} | ITEM {index}/{len(candidates)} | "
                    f"{channel_display_name(source)} | msg={message.id} | {safe_video_name(message)}"
                )
                try:
                    source_oshash = source_oshashes.get(int(message.id), "")
                    duplicate_ids = transfer_destination_duplicate_ids(
                        destination_duplicate_index, message, source_oshash
                    ) if duplicate_policy != "NONE" else []
                    if duplicate_ids:
                        total_duplicates += 1
                        entry["duplicate_destination_ids"] = duplicate_ids[:20]
                        entry["duplicate_policy"] = duplicate_policy
                        if duplicate_policy == "SKIP":
                            entry["status"] = "SKIPPED_DUPLICATE"
                            entry["error"] = None
                            transfer_job_state_save(state)
                            channel_skipped += 1
                            total_skipped += 1
                            print(f"  DUPLICATE FOUND in destination (message IDs: {duplicate_ids[:10]}) — skipped source item")
                            continue
                        if duplicate_policy == "ASK":
                            if not ask_yes_no(
                                f"Destination already contains a matching video (message IDs: {duplicate_ids[:10]}). Transfer this item anyway?",
                                default=False,
                            ):
                                entry["status"] = "SKIPPED_DUPLICATE"
                                entry["error"] = None
                                transfer_job_state_save(state)
                                channel_skipped += 1
                                total_skipped += 1
                                print("  Duplicate skipped. Source retained.")
                                continue

                    ok = await media_process_one(
                        client, source, destination, message, state, source_mode, destination_mode,
                        db=globals().get("MEDIA_MANAGER_DB"), orientation=orientation,
                        cover_policy=cover_policy, save_state_fn=transfer_job_state_save,
                    )
                    if ok:
                        if duplicate_policy != "NONE":
                            # Keep subsequent candidates from the same source
                            # batch aware of the successful destination delivery.
                            registered_id = int(entry.get("destination_video_id") or -int(message.id))
                            for duplicate_key in _transfer_duplicate_keys(message, source_oshash):
                                destination_duplicate_index.setdefault(duplicate_key, []).append(registered_id)
                        channel_moved += 1
                        total_complete += 1
                    else:
                        channel_failed += 1
                        total_failed += 1
                except ContactSheetUnavailable as exc:
                    channel_skipped += 1
                    total_skipped += 1
                    entry["status"] = "NEEDS_SNAPSHOTS"
                    entry["needs_snapshots"] = True
                    entry["error"] = str(exc)
                    transfer_job_state_save(state)
                    print(f"  SKIPPED — NEEDS SNAPSHOTS: {exc}")
                    print("  Source retained. The next item will be processed normally.")
                except Exception as exc:
                    channel_failed += 1
                    total_failed += 1
                    entry["status"] = "ERROR"
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    transfer_job_state_save(state)
                    print(f"  ERROR: {type(exc).__name__}: {exc}")
                    print("  Source retained. The next item will be processed normally.")

            print("\n" + "=" * 90)
            print(f" SOURCE CHANNEL {source_index}/{len(sources)} COMPLETE")
            print("=" * 90)
            print(f"Source channel      : {channel_display_name(source)}")
            print(f"Processed           : {channel_moved:,}")
            print(f"Skipped/resumed     : {channel_skipped:,}")
            print(f"Failed              : {channel_failed:,}")
            print("Next source channel will start only now.")

        print("\n" + "=" * 90)
        print(" TELEGRAM TRANSFER JOB COMPLETE")
        print("=" * 90)
        print(f"Source channels     : {len(sources)}")
        print(f"Completed items     : {total_complete:,}")
        print(f"Skipped/resumed     : {total_skipped:,}")
        print(f"Duplicates detected : {total_duplicates:,}")
        print(f"Failed items        : {total_failed:,}")
        print("Processing model    : one source channel at a time; one transfer item at a time")
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


async def channel_cleanup_job_main(*, client_override=None, source_overrides=None, trash_override=None, auto_confirm=False, disconnect_client=True):
    """Clean multiple Telegram channels one channel at a time.

    Cleanup actions:
      - Repair videos missing a companion thumbnail and/or metadata caption by
        posting a fresh VIDEO + thumbnail DOCUMENT pair in the same channel,
        verifying it, then deleting the old video + matched thumbnail.
      - Detect orphaned image thumbnails/photos that are not paired with any
        video and move them to the selected trash destination, verify, then
        delete them from the source.

    Flow for this job:
      1. Ask source-channel count.
      2. Select all source channels.
      3. Ask for the trash/destination channel.
      4. Scan/process each source channel completely before the next channel.
    """
    global organizer_runtime_state
    if STARTUP_IMPORT_ERRORS:
        print("\nStartup prerequisites are not ready:")
        for problem in STARTUP_IMPORT_ERRORS:
            print(f"- {problem}")
        return

    print("\n" + "=" * 90)
    print(" TELEGRAM CHANNEL CLEANUP")
    print("=" * 90)
    print("Checks each selected channel for missing-thumbnail/missing-caption videos")
    print("and removes orphaned thumbnail/image messages.")
    print("Video repair: NEW VIDEO + NEW thumbnail -> verify -> delete old pair.")
    print("Orphan removal: COPY TO TRASH -> VERIFY -> delete source.")

    try:
        source_count = int(input("\nHow many source channels? ").strip())
    except ValueError:
        source_count = 0
    if source_count < 1:
        print("Source channel count must be at least 1.")
        return

    config = load_json(CONFIG_FILE, {})
    client = client_override or await login_and_get_client(config)
    try:
        catalog = await load_channel_catalog(client)
        if source_overrides:
            sources = list(source_overrides)
            source_count = len(sources)
            trash = trash_override or await choose_channel_from_catalog(client, catalog, "DESTINATION / TRASH CHANNEL", blocked_ids={int(x.id) for x in sources}, show_catalog=False)
        else:
            blocked = set()
            sources = []
            print("\nSOURCE CHANNELS")
            for idx in range(source_count):
                source = await choose_channel_from_catalog(
                    client, catalog, f"SOURCE CHANNEL {idx + 1}", blocked_ids=blocked, show_catalog=(idx == 0)
                )
                sources.append(source)
                blocked.add(int(source.id))
            trash = trash_override or await choose_channel_from_catalog(
                client, catalog, "DESTINATION / TRASH CHANNEL", blocked_ids={int(x.id) for x in sources}, show_catalog=False
            )

        state = channel_cleanup_state_load()
        state["source_count"] = source_count
        state["source_channels"] = [{"id": int(s.id), "title": channel_display_name(s)} for s in sources]
        state["trash_destination"] = {"id": int(trash.id), "title": channel_display_name(trash)}
        state.setdefault("orphan_items", {})
        channel_cleanup_state_save(state)

        print("\n" + "=" * 90)
        print(" CLEANUP JOB CONFIGURATION")
        print("=" * 90)
        print(f"Source channels    : {len(sources)}")
        for n, src in enumerate(sources, 1):
            print(f"  {n}. {channel_display_name(src)}")
        print(f"Destination / trash: {channel_display_name(trash)}")
        print("Processing          : ONE SOURCE CHANNEL AT A TIME")
        print("Repair destination  : SAME SOURCE CHANNEL (fresh replacement messages)")
        print("Orphan handling     : move orphan image/thumbnail to destination, verify, then delete source")

        if not auto_confirm and not ask_yes_no("Proceed with this cleanup job?", default=False):
            print("Cancelled. No Telegram messages were changed.")
            return

        total_repairs = total_orphans = total_failed = total_skipped = 0
        organizer_runtime_state = organizer_state_load()

        for source_index, source in enumerate(sources, 1):
            print("\n" + "#" * 96)
            print(f" CLEANUP SOURCE CHANNEL {source_index}/{len(sources)}")
            print(f" {channel_display_name(source)}")
            print("#" * 96)

            state["source_channel_id"] = int(source.id)
            state["source_title"] = channel_display_name(source)
            channel_cleanup_state_save(state)

            audit = await organizer_scan_full_channel(client, source)
            videos = audit.get("videos") or []
            matches = audit.get("matches") or {}
            orphan_images = audit.get("orphan_images") or []

            candidates = []
            for video in videos:
                vid = int(video.id)
                old_caption = str(getattr(video, "message", "") or "").strip()
                needs_thumb = vid not in matches
                needs_caption = not old_caption
                if needs_thumb or needs_caption:
                    candidates.append((video, matches.get(vid), needs_thumb, needs_caption))

            pending_repairs = []
            for video, old_thumb, needs_thumb, needs_caption in candidates:
                vid = int(video.id)
                key = f"{int(source.id)}:{vid}"
                legacy = str(vid)
                entry = state["items"].get(key) or state["items"].get(legacy, {})
                if not (entry.get("status") == "COMPLETE" and entry.get("source_deleted")):
                    pending_repairs.append((video, old_thumb, needs_thumb, needs_caption, key))

            pending_orphans = []
            for image in orphan_images:
                iid = int(image.id)
                key = f"{int(source.id)}:{iid}"
                rec = organizer_runtime_state["orphan_items"].get(key)
                # Preserve older state keyed only by message ID when possible.
                if rec is None:
                    rec = organizer_runtime_state["orphan_items"].get(str(iid), {})
                if not rec.get("source_deleted"):
                    pending_orphans.append((image, key))

            print("\n" + "=" * 90)
            print(" CHANNEL CLEANUP PLAN")
            print("=" * 90)
            print(f"Videos scanned             : {len(videos):,}")
            print(f"Need thumbnail            : {sum(1 for _,_,t,_,_ in pending_repairs if t):,}")
            print(f"Need metadata caption     : {sum(1 for _,_,_,c,_ in pending_repairs if c):,}")
            print(f"Repair items              : {len(pending_repairs):,}")
            print(f"Orphan thumbnails/images : {len(pending_orphans):,}")

            if not pending_repairs and not pending_orphans:
                print("Nothing requires cleanup in this channel.")
                continue

            channel_success = channel_failed = channel_skipped = 0
            with tempfile.TemporaryDirectory(prefix="archive_x_channel_cleanup_") as td:
                for index, (video, old_thumb, needs_thumb, needs_caption, key) in enumerate(pending_repairs, 1):
                    vid = int(video.id)
                    entry = state["items"].setdefault(key, {})
                    if entry.get("status") == "COMPLETE" and entry.get("source_deleted"):
                        continue
                    print(f"\n[{index}/{len(pending_repairs)}] REPAIR msg={vid} | {safe_video_name(video)}")
                    try:
                        fresh = await client.get_messages(source, ids=vid)
                        if not fresh or not video_candidate_message(fresh):
                            raise RuntimeError("source video no longer exists or is no longer a video")
                        old_thumb = None
                        try:
                            old_thumb = await find_source_thumbnail(client, source, fresh)
                        except Exception:
                            old_thumb = None
                        caption = await _prepare_manager_video_delivery(
                            client, globals().get("MEDIA_MANAGER_DB"), source, fresh
                        )
                        if not caption and not needs_thumb:
                            print("  Metadata provider produced no caption; leaving source unchanged.")
                            continue
                        with tempfile.TemporaryDirectory(prefix=f"cleanup_{vid}_") as item_td:
                            cover_bytes, thumbnail_kind, generated, source_thumb = await _await_thumbnail_watchdog(
                                _select_manager_thumbnail_with_retry(
                                    client, source, fresh, item_td,
                                    explicit_thumbnail_id=None,
                                    generate_fallback=True,
                                    force_snapshot=False,
                                ),
                                timeout=THUMBNAIL_TIMEOUT_SECONDS * 2,
                                label=safe_video_name(fresh),
                            )
                            if not cover_bytes:
                                raise RuntimeError("Could not generate a usable replacement thumbnail")

                            new_video = await copy_media_to_destination(
                                client, source, source, fresh, "VIDEO", item_td,
                                cover_bytes=cover_bytes, caption=caption,
                            )
                            new_video_id = int(getattr(new_video, "id", new_video))
                            new_thumb = await upload_thumbnail_bytes_document(
                                client, source, cover_bytes, safe_video_name(fresh)
                            )
                            new_thumb_id = int(getattr(new_thumb, "id", new_thumb))

                            verify_ok, verify_reason = await verify_destination_pair(
                                client, source, new_video_id, new_thumb_id,
                                expected_video_name=safe_video_name(fresh),
                                destination_mode="VIDEO", expected_source=fresh,
                                require_adjacent=True,
                            )
                            if not verify_ok:
                                raise RuntimeError(f"replacement pair verification failed: {verify_reason}")
                            verified_video = await client.get_messages(source, ids=new_video_id)
                            if str(getattr(verified_video, "message", "") or "").strip() != str(caption or "").strip():
                                raise RuntimeError("replacement caption verification failed")

                            old_ids = [vid]
                            if old_thumb:
                                old_ids.append(int(old_thumb.id))
                            await delete_source_after_verify(client, source, old_ids)
                            entry.update({
                                "source_channel_id": int(source.id),
                                "source_message_id": vid,
                                "old_thumbnail_id": int(old_thumb.id) if old_thumb else None,
                                "destination_video_id": new_video_id,
                                "destination_thumbnail_id": new_thumb_id,
                                "caption": caption,
                                "thumbnail_kind": thumbnail_kind,
                                "source_ids_deleted": old_ids,
                                "source_deleted": True,
                                "status": "COMPLETE",
                                "error": None,
                            })
                            channel_cleanup_state_save(state)
                            channel_success += 1
                            total_repairs += 1
                            print(f"  REPLACEMENT VERIFIED: video={new_video_id}, thumbnail={new_thumb_id}; old={old_ids} deleted")
                    except ContactSheetUnavailable as exc:
                        channel_skipped += 1
                        total_skipped += 1
                        entry["status"] = "NEEDS_SNAPSHOTS"
                        entry["needs_snapshots"] = True
                        entry["error"] = str(exc)
                        channel_cleanup_state_save(state)
                        print(f"  SKIPPED — NEEDS SNAPSHOTS: {exc}")
                        print("  Source retained. Continuing to the next repair item.")
                    except Exception as exc:
                        channel_failed += 1
                        total_failed += 1
                        entry["status"] = "ERROR"
                        entry["error"] = f"{type(exc).__name__}: {exc}"
                        channel_cleanup_state_save(state)
                        print(f"  ERROR: {type(exc).__name__}: {exc}")
                        print("  Source retained. No deletion was attempted until replacement verification.")

                # Orphan images/thumbnails are independent cleanup items. They are
                # processed only after all repair items in THIS source channel finish.
                for index, (image, key) in enumerate(pending_orphans, 1):
                    iid = int(image.id)
                    entry = organizer_runtime_state["orphan_items"].setdefault(key, {})
                    entry.update({
                        "source_channel_id": int(source.id),
                        "source_message_id": iid,
                        "source_filename": organizer_image_filename(image),
                        "status": entry.get("status", "STARTED"),
                        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                    organizer_state_save(organizer_runtime_state)
                    print(f"\n[ORPHAN {index}/{len(pending_orphans)}] msg={iid}")
                    try:
                        await organizer_copy_orphan_to_trash(client, source, trash, image, entry, td)
                        # Ensure the new multi-source key is persisted even though the shared helper
                        # stores its own status fields.
                        organizer_runtime_state["orphan_items"][key] = dict(entry)
                        organizer_state_save(organizer_runtime_state)
                        channel_success += 1
                        total_orphans += 1
                    except Exception as exc:
                        channel_failed += 1
                        total_failed += 1
                        entry["status"] = "ERROR"
                        entry["error"] = f"{type(exc).__name__}: {exc}"
                        organizer_runtime_state["orphan_items"][key] = dict(entry)
                        organizer_state_save(organizer_runtime_state)
                        print(f"  ERROR: {type(exc).__name__}: {exc}")
                        print("  Source orphan image retained.")

            print("\n" + "=" * 90)
            print(f" CLEANUP SOURCE CHANNEL {source_index}/{len(sources)} COMPLETE")
            print("=" * 90)
            print(f"Source channel      : {channel_display_name(source)}")
            print(f"Successful actions  : {channel_success:,}")
            print(f"Failed actions      : {channel_failed:,}")
            print(f"Skipped — needs snapshots: {channel_skipped:,}")
            print("Next source channel starts only after this channel is complete.")

        print("\n" + "=" * 90)
        print(" TELEGRAM CHANNEL CLEANUP COMPLETE")
        print("=" * 90)
        print(f"Source channels     : {len(sources)}")
        print(f"Video repairs       : {total_repairs:,}")
        print(f"Orphans removed     : {total_orphans:,}")
        print(f"Failed actions      : {total_failed:,}")
        print(f"Skipped — needs snapshots: {total_skipped:,}")
        print("Processing model    : one source channel at a time")
    finally:
        if disconnect_client:
            try:
                await client.disconnect()
            except Exception:
                pass


async def vertical_mobile_mover_main():
    """Interactive mover entry: choose channels, scan and process."""
    if STARTUP_IMPORT_ERRORS:
        print("=" * 90)
        print(" TELEGRAM MEDIA MOVER — STARTUP ERROR")
        print("=" * 90)
        for problem in STARTUP_IMPORT_ERRORS: print(f"\n{problem}")
        input("\nPress Enter to close...")
        return

    print("=" * 90)
    print(f" {APP_NAME} v{APP_VERSION}")
    print("=" * 90)
    print("\nThis mover ONLY processes vertical / portrait-style mobile videos.")
    print("Horizontal / landscape videos are ignored.")
    print("Orientation is checked after applying Telegram's rotation metadata.")
    print("VIDEO -> VIDEO : Telegram-side copy, no full video download")
    print("VIDEO -> FILE  : Telegram-side copy, no full video download")
    print("FILE  -> FILE  : Telegram-side copy, no full video download")
    print("FILE  -> VIDEO : temporary full download + re-upload is required")

    source_mode = input("\nSOURCE type [1=VIDEO, 2=FILE]: ").strip()
    source_mode = "VIDEO" if source_mode == "1" else "FILE" if source_mode == "2" else ""
    if not source_mode:
        print("Invalid source selection.")
        return
    destination_mode = input("DESTINATION type [1=VIDEO, 2=FILE]: ").strip()
    destination_mode = "VIDEO" if destination_mode == "1" else "FILE" if destination_mode == "2" else ""
    if not destination_mode:
        print("Invalid destination selection.")
        return

    print("\nSelected:")
    print(f"  Source      : {source_mode} (vertical/mobile videos only)")
    print(f"  Destination : {destination_mode}")
    if source_mode == "FILE" and destination_mode == "VIDEO":
        print("  NOTE        : FILE -> VIDEO requires downloading the source file temporarily and uploading it as VIDEO.")

    config = load_json(CONFIG_FILE, {})
    state = vertical_mobile_load_state()
    client = None
    try:
        client = await login_and_get_client(config)
        me = await client.get_me()
        print(f"Logged in as: {getattr(me, 'first_name', '') or getattr(me, 'username', '') or 'account'}")

        # Build the channel catalog exactly once. The displayed numbers remain
        # fixed for the entire run. After SOURCE selection, do NOT print the
        # channel list again; DESTINATION uses the same numbers. SOURCE and
        # DESTINATION are intentionally allowed to be the same channel.
        catalog = await load_channel_catalog(client)
        source = await choose_channel_from_catalog(
            client, catalog, "SOURCE", show_catalog=True
        )
        destination = await choose_channel_from_catalog(
            client, catalog, "DESTINATION", show_catalog=False
        )

        state["source_channel_id"] = int(source.id)
        state["destination_channel_id"] = int(destination.id)
        state["source_title"] = channel_display_name(source)
        state["destination_title"] = channel_display_name(destination)
        state["source_mode"] = source_mode
        state["destination_mode"] = destination_mode
        vertical_mobile_save_state(state)

        print("\n" + "=" * 90)
        print(" CONFIGURATION")
        print("=" * 90)
        print(f"Source             : {channel_display_name(source)}")
        print(f"Destination        : {channel_display_name(destination)}")
        print(f"Source media       : {source_mode}")
        print(f"Destination media  : {destination_mode}")
        print("Orientation filter : vertical / portrait only (rotation-aware)")
        if source_mode == "VIDEO" and destination_mode == "VIDEO":
            print("Thumbnail          : Archive X playback snapshot")
            print("Video cover        : SAME Archive X playback snapshot")
        else:
            print("Thumbnail          : Archive X playback snapshot")
        print("Thumbnail position : immediately after destination media")
        print("Source deletion    : only after destination media + thumbnail verification")
        print("Same source/dest    : ALLOWED (destination is created as new messages)")
        if destination_mode == "VIDEO":
            print("Metadata           : REUSE persisted Archive X catalogue; no Metadata & Assets rerun")
            print("Caption            : BUILT from persisted catalogue metadata")
        else:
            print("Caption/text/link  : NOT copied")
        if source_mode == "FILE" and destination_mode == "VIDEO":
            print("Transfer method    : FULL TEMPORARY DOWNLOAD + VIDEO UPLOAD")
        else:
            print("Transfer method    : TELEGRAM-SIDE MEDIA COPY + persisted metadata/caption + Archive X snapshot")

        if not ask_yes_no("Proceed?", default=False):
            print("Cancelled. No Telegram messages were changed.")
            return

        candidates = await scan_source_mode(client, source, source_mode)
        if not candidates:
            print("No matching source media found.")
            return

        print("\n" + "=" * 90)
        print(" PROCESSING PLAN — VERTICAL / MOBILE-RECORDED VIDEOS")
        print("=" * 90)
        for i, msg in enumerate(candidates, 1):
            key = media_mode_key(source, destination, msg.id, source_mode, destination_mode)
            status = state["items"].get(key, {}).get("status", "PENDING")
            print(f"{i:5d}. msg={msg.id:<8d} | {status:<20s} | {safe_video_name(msg)}")

        if not ask_yes_no(f"Process {len(candidates):,} item(s) now?", default=False):
            print("Cancelled. Scan completed, but no Telegram messages were changed.")
            return

        moved = skipped = failed = 0
        for index, message in enumerate(candidates, 1):
            key = media_mode_key(source, destination, message.id, source_mode, destination_mode)
            entry = state["items"].get(key, {})
            if entry.get("status") == "COMPLETE" and entry.get("source_deleted"):
                skipped += 1
                print(f"[{index}/{len(candidates)}] msg={message.id} already COMPLETE; skipping.")
                continue
            print("\n" + "-" * 112)
            print(f"PROCESS {index}/{len(candidates)} | source msg={message.id} | {safe_video_name(message)}")
            try:
                if await media_process_one(client, source, destination, message, state, source_mode, destination_mode, globals().get("MEDIA_MANAGER_DB")):
                    moved += 1
            except ContactSheetUnavailable as exc:
                skipped += 1
                entry = state["items"].setdefault(key, {})
                entry["status"] = "NEEDS_SNAPSHOTS"
                entry["needs_snapshots"] = True
                entry["error"] = str(exc)
                vertical_mobile_save_state(state)
                print(f"  SKIPPED — NEEDS SNAPSHOTS: {exc}")
                print("  Source retained. Continuing to next item.")
            except Exception as exc:
                failed += 1
                entry = state["items"].setdefault(key, {})
                entry["status"] = "ERROR"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                vertical_mobile_save_state(state)
                print(f"  ERROR: {type(exc).__name__}: {exc}")
                print("  Source retained. Continuing to next item.")

        print("\n" + "=" * 90)
        print(" MEDIA MOVER COMPLETE")
        print("=" * 90)
        print(f"Candidates found       : {len(candidates):,}")
        print(f"Moved + source deleted : {moved:,}")
        print(f"Already complete       : {skipped:,}")
        print(f"Failed                 : {failed:,}")
        print(f"State file             : {VERTICAL_MOBILE_STATE_FILE}")
    except KeyboardInterrupt:
        print("\nStopped by user. State has been saved where applicable.")
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        crash_log = config.MEDIA_MANAGER_STATE_DIR / "telegram_vertical_mobile_mover_error.log"
        try: crash_log.write_text(tb, encoding="utf-8")
        except Exception: pass
        print("\n" + "!" * 90)
        print(" FATAL ERROR — PROCESS STOPPED SAFELY")
        print("!" * 90)
        print(f"{type(exc).__name__}: {exc}")
        print(f"State file : {VERTICAL_MOBILE_STATE_FILE}")
        print(f"Error log  : {crash_log}")
    finally:
        if client:
            try: await client.disconnect()
            except Exception: pass
