"""Telegram Media Manager: Telegram channel organizer (full-history audit).

Split out of ``telegram_media_manager.py`` in v29.4.387 without behaviour
changes. The main module executes the parts, in order, inside its own module
namespace, so every function, constant and ``globals()`` lookup is exactly as
when this was one file. Do not import this file directly.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Organizer: naming and state
#   - Organizer: scan and repair
#   - Organizer: entry point
#   - Duplicate scan
# ------------------------------------------------------------------
# ============================================================================
# TELEGRAM CHANNEL ORGANIZER
# ============================================================================
# Full-history audit:
#   1. Scan the complete source channel oldest -> newest.
#   2. Find VIDEO and VIDEO-DOCUMENT messages.
#   3. Match companion thumbnail image/document by canonical filename and
#      immediate-next-message fallback.
#   4. For videos with no companion thumbnail, copy the video to the selected
#      repair destination, generate a real frame thumbnail, upload it as a
#      DOCUMENT, verify the pair, then delete the source video.
#   5. Orphan thumbnail-looking image DOCUMENTS are copied to the selected
#      trash destination, verified, then deleted from the source.
#   6. Unnamed Telegram PHOTO messages are never automatically trashed because
#      there is no reliable way to distinguish a thumbnail from a normal photo.
# ============================================================================

ORGANIZER_STATE_FILE = config.MEDIA_MANAGER_STATE_DIR / "telegram_channel_organizer_state.json"
ORGANIZER_STATE_VERSION = 2
organizer_runtime_state = {}
ORGANIZER_THUMB_SCAN_AHEAD = 25




# ==============================================================================
# SECTION: Organizer: naming and state
# Thumbnail matching and persisted state.
# ==============================================================================

def organizer_image_filename(message):
    try:
        return str(getattr(message.file, "name", "") or "").strip()
    except Exception:
        return ""


def organizer_is_thumbnail_document(message):
    if not message or not getattr(message, "document", None):
        return False
    if getattr(message, "photo", None):
        return False
    mime = message_mime_local(message)
    name = organizer_image_filename(message).lower()
    return (
        mime.startswith("image/")
        or Path(name).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    )


def organizer_canonical_key(name):
    value = str(name or "").strip().lower()
    if not value:
        return ""
    value = Path(value).name
    for ext in (".jpeg", ".jpg", ".png", ".webp", ".bmp", ".gif"):
        if value.endswith(ext):
            value = value[:-len(ext)]
            break
    # Common thumbnail suffixes.
    value = re.sub(r"(?:[_ .-]*(?:thumbnail|thumb|poster|preview))$", "", value)
    # Telegram/user conventions: video.mp4.jpg / video_mp4.jpg / video.jpg
    video_suffix_pattern = "|".join(
        re.escape(ext.lstrip(".")) for ext in sorted(VIDEO_EXTENSIONS, key=len, reverse=True)
    )
    value = re.sub(rf"[_ .-]+(?:{video_suffix_pattern})$", "", value)
    value = re.sub(r"\.[a-z0-9]{2,5}$", "", value)
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value


def organizer_thumbnail_matches(video_name, image_name):
    if not image_name:
        return False
    if thumbnail_filename_matches(video_name, image_name):
        return True
    vk = organizer_canonical_key(video_name)
    ik = organizer_canonical_key(image_name)
    return bool(vk and ik and vk == ik)


def organizer_thumbnail_candidate(message):
    """Identify image documents and Telegram PHOTO messages as organizer image candidates."""
    if not message:
        return False
    if getattr(message, "photo", None):
        return True
    return organizer_is_thumbnail_document(message) and bool(organizer_image_filename(message))


def organizer_state_load():
    state = load_json(ORGANIZER_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", ORGANIZER_STATE_VERSION)
    state.setdefault("items", {})
    state.setdefault("orphan_items", {})
    return state


def organizer_state_save(state):
    save_json(ORGANIZER_STATE_FILE, state)




# ==============================================================================
# SECTION: Organizer: scan and repair
# Full-history scan and pair repair.
# ==============================================================================

async def organizer_scan_full_channel(client, source):
    """Audit a channel's full history and pair videos with thumbnails."""
    print("\n" + "=" * 100)
    print(" TELEGRAM CHANNEL ORGANIZER — FULL CHANNEL AUDIT")
    print("=" * 100)
    print("Scanning: OLDEST -> NEWEST")
    print("Relevant messages are retained in memory; unrelated channel history is streamed and discarded.")
    print("Thumbnail matching uses indexed canonical names plus an immediate-next-message fallback.")

    started = time.monotonic()
    videos = []
    images = []
    scanned = 0
    last_status = started

    async for message in client.iter_messages(source, reverse=True):
        scanned += 1
        if not message:
            continue
        if video_candidate_message(message):
            videos.append(message)
        elif organizer_is_thumbnail_document(message) or getattr(message, "photo", None):
            images.append(message)
        now = time.monotonic()
        if now - last_status >= 2:
            print(
                f"\rScanned: {scanned:,} | Videos: {len(videos):,} | Images/photos: {len(images):,}",
                end="", flush=True
            )
            last_status = now

    print()
    image_by_id = {int(x.id): x for x in images}
    used_thumb_ids = set()
    matches = {}

    # Build an O(1)-average lookup index for named thumbnails.
    named_by_key = {}
    for image in images:
        if not organizer_thumbnail_candidate(image):
            continue
        ikey = organizer_canonical_key(organizer_image_filename(image))
        if ikey:
            named_by_key.setdefault(ikey, []).append(image)

    # Pass 1: canonical filename matching anywhere in the channel.
    for video in videos:
        vname = safe_video_name(video)
        candidate_keys = {
            organizer_canonical_key(vname),
            organizer_canonical_key(vname + ".jpg"),
        }
        p = Path(vname)
        if p.suffix:
            candidate_keys.add(organizer_canonical_key(p.with_suffix(".jpg").name))
            candidate_keys.add(organizer_canonical_key(f"{p.stem}_{p.suffix.lstrip('.')}.jpg"))
        candidates = []
        for key in candidate_keys:
            if not key:
                continue
            for image in named_by_key.get(key, []):
                iid = int(image.id)
                if iid not in used_thumb_ids and organizer_thumbnail_matches(vname, organizer_image_filename(image)):
                    candidates.append(image)
        if candidates:
            chosen = min(candidates, key=lambda x: abs(int(x.id) - int(video.id)))
            matches[int(video.id)] = chosen
            used_thumb_ids.add(int(chosen.id))

    # Pass 2: immediate-next message fallback, including anonymous Telegram PHOTO.
    for video in videos:
        vid = int(video.id)
        if vid in matches:
            continue
        next_message = image_by_id.get(vid + 1)
        if next_message and (
            organizer_is_thumbnail_document(next_message) or getattr(next_message, "photo", None)
        ):
            if int(next_message.id) not in used_thumb_ids:
                matches[vid] = next_message
                used_thumb_ids.add(int(next_message.id))

    orphan_named_docs = []
    orphan_photos = []
    for image in images:
        iid = int(image.id)
        if iid in used_thumb_ids:
            continue
        if getattr(image, "photo", None):
            orphan_photos.append(image)
        elif organizer_thumbnail_candidate(image):
            orphan_named_docs.append(image)

    elapsed = time.monotonic() - started
    missing = [v for v in videos if int(v.id) not in matches]
    print("\nSCAN COMPLETE")
    print(f"  Messages scanned             : {scanned:,}")
    print(f"  Video/video-document found  : {len(videos):,}")
    print(f"  Videos with thumbnail       : {len(matches):,}")
    print(f"  Videos without thumbnail    : {len(missing):,}")
    print(f"  Orphan named image documents: {len(orphan_named_docs):,}")
    print(f"  Orphan Telegram photos      : {len(orphan_photos):,}")
    print(f"  Scan time                   : {elapsed:.1f}s")

    return {
        "videos": videos,
        "images": images,
        "matches": matches,
        "missing": missing,
        "orphan_named_docs": orphan_named_docs,
        "orphan_photos": orphan_photos,
        "orphan_images": orphan_named_docs + orphan_photos,
        "stats": {
            "messages_scanned": scanned,
            "videos": len(videos),
            "matched": len(matches),
            "missing": len(missing),
            "orphan_named_docs": len(orphan_named_docs),
            "orphan_photos": len(orphan_photos),
            "orphan_images": len(orphan_named_docs) + len(orphan_photos),
            "elapsed_seconds": elapsed,
        },
    }


async def organizer_verify_repair_pair(client, destination, video_id, thumb_id, expected_name):
    """Verify either a native Telegram VIDEO or a Telegram VIDEO-DOCUMENT plus its thumbnail."""
    if not video_id or not thumb_id:
        return False, "missing destination message IDs"
    try:
        video = await client.get_messages(destination, ids=int(video_id))
        thumb = await client.get_messages(destination, ids=int(thumb_id))
    except Exception as exc:
        return False, f"destination fetch failed: {type(exc).__name__}: {exc}"
    if not video or not getattr(video, "id", None):
        return False, "destination video/document not found"
    if not thumb or not getattr(thumb, "id", None):
        return False, "destination thumbnail not found"
    if int(thumb.id) != int(video.id) + 1:
        return False, f"thumbnail is not immediately after media (video={video.id}, thumb={thumb.id})"
    if not video_candidate_message(video):
        return False, "destination media is not a Telegram video or video-document"
    actual_name = safe_video_name(video)
    if actual_name.lower() != str(expected_name).lower():
        return False, f"video filename mismatch: expected={expected_name}, actual={actual_name}"
    if not getattr(thumb, "document", None) or getattr(thumb, "photo", None):
        return False, "destination thumbnail is not an image DOCUMENT"
    thumb_name = ""
    try:
        thumb_name = str(getattr(thumb.file, "name", "") or "")
    except Exception:
        pass
    if not thumbnail_filename_matches(expected_name, thumb_name):
        return False, f"thumbnail filename mismatch: expected for {expected_name}, actual={thumb_name or '[none]'}"
    return True, "video/video-document + thumbnail pair verified"


async def organizer_copy_and_repair_video(client, source, destination, video, state_entry, tempdir):
    """Copy a video to repair with a thumbnail, verify, then delete the source."""
    vid = int(video.id)
    name = safe_video_name(video)
    print(f"\nREPAIR VIDEO | msg={vid} | {name}")
    print(f"  Source      : {channel_display_name(source)}")
    print(f"  Destination : {channel_display_name(destination)}")

    existing_dest_video = state_entry.get("destination_video_id")
    existing_dest_thumb = state_entry.get("destination_thumbnail_id")

    if not existing_dest_video:
        print("  Copying source media without forwarding tags...")
        new_video = await forward_without_tag_safe(client, destination, source, video)
        existing_dest_video = int(getattr(new_video, "id", new_video))
        state_entry["destination_video_id"] = existing_dest_video
        state_entry["status"] = "VIDEO_COPIED"
        organizer_state_save(organizer_runtime_state)

    thumb_path = Path(tempdir) / thumbnail_name_for_video(name)
    if not existing_dest_thumb:
        print("  Generating thumbnail from actual video bytes...")
        await generate_thumbnail_with_timeout(client, source, video, tempdir)
        if not thumb_path.exists() or thumb_path.stat().st_size <= 0:
            raise RuntimeError("Thumbnail generation produced no usable JPEG")
        print(f"  Uploading thumbnail DOCUMENT: {thumb_path.name}")
        new_thumb = await upload_thumbnail_document(client, destination, thumb_path)
        existing_dest_thumb = int(getattr(new_thumb, "id", new_thumb))
        state_entry["destination_thumbnail_id"] = existing_dest_thumb
        state_entry["status"] = "PAIR_COPIED"
        organizer_state_save(organizer_runtime_state)

    ok, reason = await organizer_verify_repair_pair(
        client, destination, existing_dest_video, existing_dest_thumb, name
    )
    if not ok:
        state_entry["status"] = "DESTINATION_VERIFY_FAILED"
        state_entry["error"] = reason
        organizer_state_save(organizer_runtime_state)
        raise RuntimeError(f"Destination verification failed: {reason}")

    print(f"  DESTINATION VERIFIED: video={existing_dest_video}, thumbnail={existing_dest_thumb}")
    await delete_source_after_verify(client, source, [vid])
    state_entry["source_deleted"] = True
    state_entry["status"] = "COMPLETE"
    state_entry.pop("error", None)
    organizer_state_save(organizer_runtime_state)
    print(f"  SOURCE VIDEO DELETED: {vid}")


async def organizer_copy_orphan_to_trash(client, source, trash, image, state_entry, tempdir):
    iid = int(image.id)
    name = organizer_image_filename(image)
    kind = "PHOTO" if getattr(image, "photo", None) else "IMAGE DOCUMENT"
    print(f"\nORPHAN IMAGE | {kind} | msg={iid} | {name or '[anonymous photo]'}")
    print(f"  Source : {channel_display_name(source)}")
    print(f"  Trash  : {channel_display_name(trash)}")

    dest_id = state_entry.get("destination_message_id")
    if not dest_id:
        # Existing Telegram image documents are copied without forwarding tags.
        new_msg = await copy_existing_thumbnail(
            client, trash, source, image, name or "orphan_thumbnail.jpg", tempdir
        )
        dest_id = int(getattr(new_msg, "id", new_msg))
        state_entry["destination_message_id"] = dest_id
        state_entry["status"] = "COPIED"
        organizer_state_save(organizer_runtime_state)

    check = await client.get_messages(trash, ids=dest_id)
    if not check or not getattr(check, "id", None):
        state_entry["status"] = "TRASH_VERIFY_FAILED"
        state_entry["error"] = "Trash destination message could not be fetched"
        organizer_state_save(organizer_runtime_state)
        raise RuntimeError("Trash destination verification failed: message not found")
    if not getattr(check, "document", None) or getattr(check, "photo", None):
        state_entry["status"] = "TRASH_VERIFY_FAILED"
        state_entry["error"] = "Trash copy is not an image DOCUMENT"
        organizer_state_save(organizer_runtime_state)
        raise RuntimeError("Trash destination verification failed: not a document")

    await delete_source_after_verify(client, source, [iid])
    state_entry["source_deleted"] = True
    state_entry["status"] = "COMPLETE"
    state_entry.pop("error", None)
    organizer_state_save(organizer_runtime_state)
    print(f"  SOURCE ORPHAN THUMBNAIL DELETED: {iid}")




# ==============================================================================
# SECTION: Organizer: entry point
# Interactive organizer flow.
# ==============================================================================

async def telegram_channel_organizer_main(*, client_override=None, source_override=None, repair_override=None, trash_override=None, auto_confirm=False, disconnect_client=True):
    """Organizer entry: choose channels, scan and apply repairs."""
    global organizer_runtime_state
    print("=" * 100)
    print(" TELEGRAM CHANNEL ORGANIZER")
    print("=" * 100)
    print("Purpose:")
    print("  • Audit the ENTIRE channel history.")
    print("  • Find VIDEO and VIDEO-DOCUMENT messages without a companion thumbnail.")
    print("  • Copy missing-thumbnail videos to a repair destination and generate a real thumbnail.")
    print("  • Verify destination video + thumbnail before deleting the source video.")
    print("  • Move orphan image DOCUMENTS and unmatched Telegram PHOTO messages to a trash channel, verify, then delete source.")

    config = load_json(CONFIG_FILE, {})
    organizer_runtime_state = organizer_state_load()
    client = client_override
    owns_client = client is None

    try:
        if client is None:
            client = await login_and_get_client(config)
        me = await client.get_me()
        print(f"\nLogged in as: {getattr(me, 'first_name', '') or getattr(me, 'username', '') or 'account'}")

        catalog = None if source_override is not None else await load_channel_catalog(client)
        if catalog is not None:
            print("\nChannel list is loaded ONCE. Channel numbers remain fixed for this run.")

        source = source_override or await choose_channel_from_catalog(
            client, catalog, "SOURCE", blocked_ids=set(), source_id=None, show_catalog=True
        )
        source_id = int(source.id)

        repair = repair_override or await choose_channel_from_catalog(
            client, catalog, "REPAIR DESTINATION", blocked_ids=set(), source_id=None, show_catalog=False
        )
        repair_id = int(repair.id)

        trash = trash_override or await choose_channel_from_catalog(
            client, catalog, "TRASH DESTINATION", blocked_ids={source_id, repair_id}, source_id=source_id, show_catalog=False
        )
        trash_id = int(trash.id)

        organizer_runtime_state["source_channel_id"] = source_id
        organizer_runtime_state["source_title"] = channel_display_name(source)
        organizer_runtime_state["repair_destination"] = {"id": repair_id, "title": channel_display_name(repair)}
        organizer_runtime_state["trash_destination"] = {"id": trash_id, "title": channel_display_name(trash)}
        organizer_state_save(organizer_runtime_state)

        print("\n" + "=" * 100)
        print(" CONFIGURATION")
        print("=" * 100)
        print(f"Source            : {channel_display_name(source)}")
        print(f"Repair destination: {channel_display_name(repair)}")
        print(f"Trash destination : {channel_display_name(trash)}")
        print("Source deletion   : ONLY after destination verification")
        print("Scan               : ENTIRE channel, oldest -> newest")

        audit = await organizer_scan_full_channel(client, source)
        stats = audit["stats"]

        pending_missing = [
            v for v in audit["missing"]
            if not organizer_runtime_state["items"].get(str(int(v.id)), {}).get("source_deleted")
        ]
        pending_orphans = [
            x for x in audit["orphan_images"]
            if not organizer_runtime_state["orphan_items"].get(str(int(x.id)), {}).get("source_deleted")
        ]

        print("\n" + "=" * 100)
        print(" ACTION SUMMARY")
        print("=" * 100)
        print(f"Videos with existing thumbnail : {stats['matched']:,}  -> leave in place")
        print(f"Videos missing thumbnail       : {len(pending_missing):,}  -> repair + verify + delete source")
        print(f"Orphan image documents/photos : {len(pending_orphans):,}  -> trash + verify + delete source")

        if not pending_missing and not pending_orphans:
            print("\nNothing requires action.")
            return

        answer = "y" if auto_confirm else input(
            f"\nProcess {len(pending_missing) + len(pending_orphans):,} item(s)? "
            "Only verified destination copies will be deleted from source. [y/N]: "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled. No Telegram messages were changed.")
            return

        success = failed = 0
        with tempfile.TemporaryDirectory(prefix="telegram_channel_organizer_") as td:
            for index, video in enumerate(pending_missing, 1):
                vid = int(video.id)
                entry = organizer_runtime_state["items"].setdefault(str(vid), {})
                entry.update({
                    "source_message_id": vid,
                    "source_filename": safe_video_name(video),
                    "status": entry.get("status", "STARTED"),
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                organizer_state_save(organizer_runtime_state)
                print(f"\n[{index}/{len(pending_missing)}] Missing-thumbnail video")
                try:
                    await organizer_copy_and_repair_video(client, source, repair, video, entry, td)
                    success += 1
                except Exception as exc:
                    failed += 1
                    entry["status"] = "ERROR"
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    organizer_state_save(organizer_runtime_state)
                    print(f"  ERROR: {type(exc).__name__}: {exc}")
                    print("  Source video retained.")

            for index, image in enumerate(pending_orphans, 1):
                iid = int(image.id)
                entry = organizer_runtime_state["orphan_items"].setdefault(str(iid), {})
                entry.update({
                    "source_message_id": iid,
                    "source_filename": organizer_image_filename(image),
                    "status": entry.get("status", "STARTED"),
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                organizer_state_save(organizer_runtime_state)
                print(f"\n[ORPHAN {index}/{len(pending_orphans)}]")
                try:
                    await organizer_copy_orphan_to_trash(client, source, trash, image, entry, td)
                    success += 1
                except Exception as exc:
                    failed += 1
                    entry["status"] = "ERROR"
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    organizer_state_save(organizer_runtime_state)
                    print(f"  ERROR: {type(exc).__name__}: {exc}")
                    print("  Source thumbnail retained.")

        print("\n" + "=" * 100)
        print(" TELEGRAM CHANNEL ORGANIZER COMPLETE")
        print("=" * 100)
        print(f"Messages scanned            : {stats['messages_scanned']:,}")
        print(f"Videos found                : {stats['videos']:,}")
        print(f"Videos already paired       : {stats['matched']:,}")
        print(f"Missing-thumbnail videos    : {stats['missing']:,}")
        print(f"Orphan image documents/photos: {stats['orphan_images']:,}")
        print(f"  ├─ Named image documents    : {stats['orphan_named_docs']:,}")
        print(f"  └─ Telegram PHOTO messages  : {stats['orphan_photos']:,}")
        print(f"Successful actions          : {success:,}")
        print(f"Failed actions              : {failed:,}")
        print(f"State file                  : {ORGANIZER_STATE_FILE}")
        if failed:
            print("Failed items remain in source and can be retried on the next run.")

    except KeyboardInterrupt:
        print("\nStopped by user. Completed verified actions remain recorded in state.")
    except Exception as exc:
        print(f"\nFATAL ERROR: {type(exc).__name__}: {exc}")
    finally:
        if client and disconnect_client and owns_client:
            try:
                await client.disconnect()
            except Exception:
                pass






# ==============================================================================
# SECTION: Duplicate scan
# Signatures, pairing and the duplicate scan.
# ==============================================================================

def dup_is_thumbnail_message(message):
    if not message:
        return False
    if getattr(message, "photo", None):
        return True
    document = getattr(message, "document", None)
    if not document:
        return False
    mime = str(getattr(document, "mime_type", "") or "").lower()
    if mime.startswith("video/"):
        return False
    name = dup_get_file_name(message).lower()
    return mime.startswith("image/") or Path(name).suffix.lower() in IMAGE_EXTENSIONS


def dup_get_file_name(message):
    try:
        return str(getattr(message.file, "name", "") or "")
    except Exception:
        return ""


def dup_safe_video_name(message):
    name = dup_get_file_name(message)
    if not name:
        name = f"telegram_{message.id}.mp4"
    if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
        name += ".mp4"
    return name


def dup_thumbnail_name(message):
    name = dup_get_file_name(message)
    if name:
        return name
    return f"telegram_photo_{message.id}.jpg"


def dup_normalize_text(value):
    value = str(value or "").lower()
    value = re.sub(r"\.[a-z0-9]{1,8}$", "", value)
    value = re.sub(r"(?:[_ .-]*(?:thumbnail|thumb|poster|preview))$", "", value)
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def dup_canonical_key(filename):
    name = Path(str(filename or "")).name
    return dup_normalize_text(name)




def dup_video_dimensions(message):
    width = height = 0
    duration = 0.0
    media = getattr(message, "video", None)
    document = getattr(message, "document", None)
    attrs = getattr(document, "attributes", None) or getattr(media, "attributes", None) or []
    for attr in attrs:
        if hasattr(attr, "w") and hasattr(attr, "h"):
            width = int(getattr(attr, "w", 0) or 0)
            height = int(getattr(attr, "h", 0) or 0)
        if hasattr(attr, "duration"):
            try:
                duration = float(getattr(attr, "duration", 0) or 0)
            except Exception:
                duration = 0.0
    if not width and media:
        width = int(getattr(media, "w", 0) or 0)
    if not height and media:
        height = int(getattr(media, "h", 0) or 0)
    if not duration and media:
        try:
            duration = float(getattr(media, "duration", 0) or 0)
        except Exception:
            pass
    return width, height, duration


def dup_image_dimensions(message):
    photo = getattr(message, "photo", None)
    if photo:
        return int(getattr(photo, "w", 0) or 0), int(getattr(photo, "h", 0) or 0)
    document = getattr(message, "document", None)
    for attr in getattr(document, "attributes", None) or []:
        if hasattr(attr, "w") and hasattr(attr, "h"):
            return int(getattr(attr, "w", 0) or 0), int(getattr(attr, "h", 0) or 0)
    return 0, 0


def dup_video_signature(message):
    width, height, duration = dup_video_dimensions(message)
    return (
        dup_canonical_key(dup_safe_video_name(message)),
        message_size(message),
        width,
        height,
        round(duration, 2),
    )


def dup_thumbnail_signature(message):
    width, height = dup_image_dimensions(message)
    # Telegram PHOTO messages normally have no filename. Use a stable
    # anonymous-photo marker plus exposed media metadata so paired duplicate
    # photos can still be classified as a pair when their media metadata
    # matches. Named image DOCUMENTs continue to use their canonical name.
    if getattr(message, "photo", None):
        key = "__telegram_photo__"
    else:
        key = dup_canonical_key(dup_thumbnail_name(message))
    return (
        key,
        message_size(message),
        width,
        height,
    )


def dup_pair_records(videos, thumbnails):
    """Pair thumbnails efficiently: indexed filename matching, then immediate-next fallback."""
    ordered_videos = sorted(videos, key=lambda m: int(m.id))
    used = set()
    pairs = {}

    # O(V + T) index instead of repeatedly comparing every video with every thumbnail.
    by_key = {}
    for thumb in thumbnails:
        key = dup_canonical_key(dup_thumbnail_name(thumb))
        if key:
            by_key.setdefault(key, []).append(thumb)

    for video in ordered_videos:
        vkey = dup_canonical_key(dup_safe_video_name(video))
        candidates = []
        for key in {
            vkey,
            dup_canonical_key(dup_safe_video_name(video) + ".jpg"),
            dup_canonical_key(Path(dup_safe_video_name(video)).with_suffix(".jpg").name),
            dup_canonical_key(
                f"{Path(dup_safe_video_name(video)).stem}_{Path(dup_safe_video_name(video)).suffix.lstrip('.')}.jpg"
            ).replace(" ", "") if Path(dup_safe_video_name(video)).suffix else "",
        }:
            if not key:
                continue
            for thumb in by_key.get(key, []):
                if int(thumb.id) not in used:
                    candidates.append(thumb)
        if candidates:
            chosen = min(candidates, key=lambda x: abs(int(x.id) - int(video.id)))
            pairs[int(video.id)] = chosen
            used.add(int(chosen.id))

    # Fallback: exact next Telegram message, which also handles anonymous PHOTO thumbnails.
    thumb_by_id = {int(t.id): t for t in thumbnails}
    for video in ordered_videos:
        vid = int(video.id)
        if vid in pairs:
            continue
        next_thumb = thumb_by_id.get(vid + 1)
        if next_thumb and int(next_thumb.id) not in used:
            pairs[vid] = next_thumb
            used.add(int(next_thumb.id))
    return pairs, used


async def duplicate_scan(client, source):
    """Scan a whole channel and group duplicate video/thumbnail records."""
    print("\n" + "=" * 100)
    print(" TELEGRAM DUPLICATE FINDER — FULL CHANNEL SCAN")
    print("=" * 100)
    print("Scan direction: TOP -> BOTTOM (oldest -> newest)")
    print("Step 1: identify video/thumbnail pairs.")
    print("Step 2: classify duplicate pairs, duplicate videos, and duplicate thumbnails.")
    print("No source message is changed during scanning.")

    started = time.monotonic()
    videos = []
    thumbnails = []
    scanned = 0
    last_print = started

    async for message in client.iter_messages(source, reverse=True):
        scanned += 1
        if is_video_message(message):
            videos.append(message)
        elif dup_is_thumbnail_message(message):
            thumbnails.append(message)

        now = time.monotonic()
        if now - last_print >= 1.0:
            print(
                f"\rScanned {scanned:,} messages | videos {len(videos):,} | thumbnails/photos {len(thumbnails):,}",
                end="", flush=True
            )
            last_print = now

    pairs, used_thumb_ids = dup_pair_records(videos, thumbnails)

    # Build exact duplicate groups using media metadata, not filename alone.
    video_groups = {}
    for video in videos:
        video_groups.setdefault(dup_video_signature(video), []).append(video)

    thumb_groups = {}
    for thumb in thumbnails:
        thumb_groups.setdefault(dup_thumbnail_signature(thumb), []).append(thumb)

    pair_groups = {}
    for video in videos:
        thumb = pairs.get(int(video.id))
        if not thumb:
            continue
        key = (dup_video_signature(video), dup_thumbnail_signature(thumb))
        pair_groups.setdefault(key, []).append((video, thumb))

    actions = []
    pair_video_ids = set()
    pair_thumb_ids = set()

    # A duplicate pair means the exact video signature AND exact thumbnail
    # signature occur together at least twice. Keep the newest pair and move
    # all older pairs, as requested by the "delete the first duplicate" rule.
    for group_key, group in pair_groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda p: (int(p[0].id), int(p[1].id)))
        for video, thumb in group[:-1]:
            actions.append({
                "type": "PAIR_DUPLICATE",
                "video": video,
                "thumbnail": thumb,
                "message_ids": [int(video.id), int(thumb.id)],
                "reason": "older duplicate video + thumbnail pair",
            })
            pair_video_ids.add(int(video.id))
            pair_thumb_ids.add(int(thumb.id))

    # Video-only duplicates: duplicate video signatures not already consumed
    # as part of an older duplicate pair. Keep newest; move older copies.
    for sig, group in video_groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda m: int(m.id))
        for video in group[:-1]:
            if int(video.id) in pair_video_ids:
                continue
            actions.append({
                "type": "VIDEO_ONLY_DUPLICATE",
                "video": video,
                "thumbnail": None,
                "message_ids": [int(video.id)],
                "reason": "older duplicate video; no duplicate thumbnail pair",
            })

    # Thumbnail-only duplicates: duplicate thumbnail signatures not already
    # consumed as part of an older duplicate pair. Keep newest.
    for sig, group in thumb_groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda m: int(m.id))
        for thumb in group[:-1]:
            if int(thumb.id) in pair_thumb_ids:
                continue
            actions.append({
                "type": "THUMBNAIL_ONLY_DUPLICATE",
                "video": None,
                "thumbnail": thumb,
                "message_ids": [int(thumb.id)],
                "reason": "older duplicate thumbnail; no duplicate pair",
            })

    # Remove any accidental duplicate action references.
    unique = {}
    for action in sorted(actions, key=lambda x: min(x["message_ids"])):
        key = tuple(sorted(action["message_ids"]))
        unique.setdefault(key, action)
    actions = list(unique.values())

    elapsed = time.monotonic() - started
    pair_count = sum(a["type"] == "PAIR_DUPLICATE" for a in actions)
    video_only_count = sum(a["type"] == "VIDEO_ONLY_DUPLICATE" for a in actions)
    thumb_only_count = sum(a["type"] == "THUMBNAIL_ONLY_DUPLICATE" for a in actions)

    print("\n\nSCAN COMPLETE")
    print(f"  Messages scanned       : {scanned:,}")
    print(f"  Videos/video-documents : {len(videos):,}")
    print(f"  Images/photos          : {len(thumbnails):,}")
    print(f"  Valid video-thumbnail pairs: {len(pairs):,}")
    print(f"  Duplicate pairs to move: {pair_count:,}")
    print(f"  Duplicate videos only  : {video_only_count:,}")
    print(f"  Duplicate thumbnails only: {thumb_only_count:,}")
    print(f"  Source messages to move: {sum(len(a['message_ids']) for a in actions):,}")
    print(f"  Scan time              : {elapsed:.1f}s")

    return {
        "videos": videos,
        "thumbnails": thumbnails,
        "pairs": pairs,
        "actions": actions,
        "stats": {
            "messages_scanned": scanned,
            "videos": len(videos),
            "thumbnails": len(thumbnails),
            "pairs": len(pairs),
            "pair_duplicates": pair_count,
            "video_only_duplicates": video_only_count,
            "thumbnail_only_duplicates": thumb_only_count,
            "source_messages_to_move": sum(len(a["message_ids"]) for a in actions),
            "elapsed_seconds": elapsed,
        },
    }
