"""Telegram Media Manager: Duplicate finder.

Split out of ``telegram_media_manager.py`` in v29.4.387 without behaviour
changes. The main module executes the parts, in order, inside its own module
namespace, so every function, constant and ``globals()`` lookup is exactly as
when this was one file. Do not import this file directly.
"""
# ============================================================================
# DUPLICATE FINDER
# ============================================================================

DUPLICATE_STATE_FILE = config.MEDIA_MANAGER_STATE_DIR / "telegram_duplicate_finder_state.json"


def duplicate_destination_filename(message):
    if is_video_message(message):
        return safe_video_name(message)
    try:
        name = str(getattr(message.file, "name", "") or "")
    except Exception:
        name = ""
    return name or f"telegram_image_{int(message.id)}.jpg"

async def duplicate_copy_to_trash(client, source, trash, message, tempdir):
    """Copy a duplicate media item to trash without forwarding metadata."""
    if getattr(message, "photo", None):
        # Convert Telegram PHOTO to an image DOCUMENT so the trash is uniform.
        name = f"telegram_photo_{int(message.id)}.jpg"
        return await copy_thumbnail_as_document_canonical(
            client, trash, source, message, name, tempdir
        )
    if is_video_message(message):
        return await forward_without_tag_safe(client, trash, source, message)
    return await copy_thumbnail_as_document_canonical(
        client, trash, source, message, duplicate_destination_filename(message), tempdir
    )

async def duplicate_verify_copy(client, trash, destination_id, source_message):
    try:
        check = await client.get_messages(trash, ids=int(destination_id))
    except Exception as exc:
        return False, f"destination fetch failed: {type(exc).__name__}: {exc}"
    if not check or not getattr(check, "id", None):
        return False, "destination message not found"

    if is_video_message(source_message):
        if not is_video_message(check):
            return False, "destination is not a video/video-document"
        if safe_video_name(check).lower() != safe_video_name(source_message).lower():
            return False, "destination filename mismatch"
        ss = message_size(source_message)
        ds = message_size(check)
        if ss and ds and ss != ds:
            return False, "destination video size mismatch"
    else:
        if getattr(check, "photo", None):
            return False, "destination thumbnail is still a PHOTO"
        if not getattr(check, "document", None):
            return False, "destination thumbnail is not a document"
        ss = message_size(source_message)
        ds = message_size(check)
        if ss and ds and ss != ds:
            return False, "destination thumbnail size mismatch"
    return True, "verified"

async def duplicate_verify_pair(client, trash, video, thumb, destination_video_id, destination_thumb_id):
    if int(destination_thumb_id) != int(destination_video_id) + 1:
        return False, "thumbnail is not immediately after video"
    ok, reason = await duplicate_verify_copy(client, trash, destination_video_id, video)
    if not ok:
        return False, f"video verification failed: {reason}"
    ok, reason = await duplicate_verify_copy(client, trash, destination_thumb_id, thumb)
    if not ok:
        return False, f"thumbnail verification failed: {reason}"
    return True, "video + thumbnail pair verified"

async def duplicate_delete_source(client, source, ids):
    ids = sorted({int(x) for x in ids if x})
    if ids:
        await delete_source_after_verify(client, source, ids)


def duplicate_state_load():
    state = load_json(DUPLICATE_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 2)
    state.setdefault("actions", {})
    return state


def duplicate_state_save(state):
    save_json(DUPLICATE_STATE_FILE, state)

async def duplicate_move(client, source, trash, action, state, tempdir):
    """Copy one duplicate action to trash, verify, then delete the source."""
    key = ",".join(map(str, sorted(action["message_ids"])))
    record = state["actions"].setdefault(key, {
        "type": action["type"],
        "source_message_ids": action["message_ids"],
        "status": "STARTED",
    })
    if record.get("source_deleted"):
        return True

    dest_ids = list(record.get("destination_message_ids") or [])
    if action["type"] == "PAIR_DUPLICATE":
        video, thumb = action["video"], action["thumbnail"]
        if len(dest_ids) < 1:
            new_video = await duplicate_copy_to_trash(client, source, trash, video, tempdir)
            dest_ids = [int(getattr(new_video, "id", new_video))]
            record["destination_message_ids"] = dest_ids
            duplicate_state_save(state)
        if len(dest_ids) < 2:
            new_thumb = await duplicate_copy_to_trash(client, source, trash, thumb, tempdir)
            dest_ids.append(int(getattr(new_thumb, "id", new_thumb)))
            record["destination_message_ids"] = dest_ids
            duplicate_state_save(state)
        ok, reason = await duplicate_verify_pair(client, trash, video, thumb, dest_ids[0], dest_ids[1])
    else:
        message = action["video"] or action["thumbnail"]
        if not dest_ids:
            new_msg = await duplicate_copy_to_trash(client, source, trash, message, tempdir)
            dest_ids = [int(getattr(new_msg, "id", new_msg))]
            record["destination_message_ids"] = dest_ids
            duplicate_state_save(state)
        ok, reason = await duplicate_verify_copy(client, trash, dest_ids[0], message)

    if not ok:
        record["status"] = "DESTINATION_VERIFY_FAILED"
        record["error"] = reason
        duplicate_state_save(state)
        raise RuntimeError(reason)

    await duplicate_delete_source(client, source, action["message_ids"])
    # Confirm source deletion before declaring completion.
    remaining = await client.get_messages(source, ids=[int(x) for x in action["message_ids"]])
    if not isinstance(remaining, list):
        remaining = [remaining] if remaining else []
    remaining = [m for m in remaining if m and getattr(m, "id", None)]
    if remaining:
        record["status"] = "SOURCE_DELETE_UNCONFIRMED"
        record["error"] = "Telegram still returned source message(s) after deletion request"
        duplicate_state_save(state)
        raise RuntimeError(record["error"])

    record["source_deleted"] = True
    record["status"] = "COMPLETE"
    record["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    record.pop("error", None)
    duplicate_state_save(state)
    return True

async def duplicate_finder_main(*, client_override=None, source_override=None, trash_override=None, auto_confirm=False, disconnect_client=True):
    """Run duplicate cleanup across multiple source channels sequentially.

    Interactive flow asks for the source-channel count first, selects all source
    channels, then asks for the destination/trash channel. Each source channel
    is fully scanned and processed before the next source begins.
    """
    if STARTUP_IMPORT_ERRORS:
        for problem in STARTUP_IMPORT_ERRORS:
            print(problem)
        return

    global duplicate_runtime_state
    duplicate_runtime_state = duplicate_state_load()
    print("=" * 94)
    print(" TELEGRAM MEDIA MANAGER — DUPLICATE FINDER")
    print("=" * 94)
    config = load_json(CONFIG_FILE, {})
    client = client_override
    owns_client = client is None
    try:
        if client is None:
            client = await login_and_get_client(config)

        if source_override is not None:
            sources = [source_override]
            catalog = None
            trash = trash_override
            if trash is None:
                catalog = await load_channel_catalog(client)
                trash = await choose_channel_from_catalog(
                    client, catalog, "DESTINATION / TRASH CHANNEL",
                    blocked_ids={int(source_override.id)}, show_catalog=False, source_id=int(source_override.id)
                )
        else:
            catalog = await load_channel_catalog(client)
            try:
                source_count = int(input("\nHow many source channels? ").strip())
            except ValueError:
                source_count = 0
            if source_count < 1:
                print("Source channel count must be at least 1.")
                return

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

            # Destination/trash is asked only after all source channels are selected.
            trash = trash_override or await choose_channel_from_catalog(
                client, catalog, "DESTINATION / TRASH CHANNEL",
                blocked_ids=set(), show_catalog=False
            )

        print("\n" + "=" * 94)
        print(" DUPLICATE JOB CONFIGURATION")
        print("=" * 94)
        print(f"Source channels     : {len(sources)}")
        for n, source in enumerate(sources, 1):
            print(f"  {n}. {channel_display_name(source)}")
        print(f"Destination / trash : {channel_display_name(trash)}")
        print("Processing           : ONE SOURCE CHANNEL AT A TIME")
        print("Duplicate policy     : pair first; older duplicate is moved to trash; newest copy is retained")
        print("Safety               : COPY -> VERIFY -> DELETE SOURCE")

        if not auto_confirm and not ask_yes_no("Proceed with this duplicate job?", default=False):
            print("Cancelled. No Telegram messages were changed.")
            return

        grand_scanned = grand_actions = grand_success = grand_failed = 0

        for source_index, source in enumerate(sources, 1):
            print("\n" + "#" * 96)
            print(f" DUPLICATE SOURCE CHANNEL {source_index}/{len(sources)}")
            print(f" {channel_display_name(source)}")
            print("#" * 96)

            audit = await duplicate_scan(client, source)
            state = duplicate_state_load()
            pending = []
            for action in audit["actions"]:
                # Namespace by source channel so identical message IDs in separate
                # channels never collide in the persisted duplicate state.
                key = f"{int(source.id)}:" + ",".join(map(str, sorted(action["message_ids"])))
                legacy_key = ",".join(map(str, sorted(action["message_ids"])))
                record = state["actions"].get(key) or state["actions"].get(legacy_key, {})
                if not record.get("source_deleted"):
                    pending.append((action, key))

            grand_scanned += int(audit["stats"]["messages_scanned"])
            grand_actions += len(pending)

            print("\nDUPLICATE ACTION SUMMARY")
            print(f"  Messages scanned : {audit['stats']['messages_scanned']:,}")
            print(f"  Pending actions  : {len(pending):,}")
            if not pending:
                print("  No duplicate actions are pending in this channel.")
                continue

            for i, (action, _) in enumerate(sorted(pending, key=lambda x: min(x[0]["message_ids"])), 1):
                print(f"{i:5d}. {action['type']:<28} source={action['message_ids']} | {action['reason']}")

            if not auto_confirm and not ask_yes_no(
                f"Move {len(pending):,} duplicate action(s) from this channel to trash?", default=False
            ):
                print("Skipped this source channel.")
                continue

            success = failed = 0
            with tempfile.TemporaryDirectory(prefix="telegram_media_manager_dup_") as td:
                # One source channel at a time; no second source is scanned until
                # every pending action from the current source is complete.
                for i, (action, key) in enumerate(sorted(pending, key=lambda x: min(x[0]["message_ids"])), 1):
                    print(f"\n[{i}/{len(pending)}] {action['type']} | {action['message_ids']}")
                    try:
                        await duplicate_move(client, source, trash, action, state, td)
                        # duplicate_move currently persists its own action record;
                        # namespace it for multi-source resumability as well.
                        raw_key = ",".join(map(str, sorted(action["message_ids"])))
                        rec = state["actions"].get(raw_key, {}).copy()
                        state["actions"][key] = rec
                        if raw_key != key:
                            # Keep legacy compatibility but prevent another source
                            # from accidentally treating this record as its own.
                            state["actions"].pop(raw_key, None)
                        duplicate_state_save(state)
                        success += 1
                        grand_success += 1
                    except Exception as exc:
                        failed += 1
                        grand_failed += 1
                        rec = state["actions"].setdefault(key, {})
                        rec["status"] = "ERROR"
                        rec["error"] = f"{type(exc).__name__}: {exc}"
                        duplicate_state_save(state)
                        print(f"  ERROR: {type(exc).__name__}: {exc}")
                        print("  Source retained.")

            print("\n" + "=" * 94)
            print(f" DUPLICATE SOURCE CHANNEL {source_index}/{len(sources)} COMPLETE")
            print("=" * 94)
            print(f"Successful moves : {success:,}")
            print(f"Failed            : {failed:,}")
            print("Next source channel starts only now.")

        print("\n" + "=" * 94)
        print(" DUPLICATE JOB COMPLETE")
        print("=" * 94)
        print(f"Source channels     : {len(sources)}")
        print(f"Messages scanned    : {grand_scanned:,}")
        print(f"Pending actions     : {grand_actions:,}")
        print(f"Successful moves    : {grand_success:,}")
        print(f"Failed moves        : {grand_failed:,}")
        print("Processing model    : one source channel at a time")
    finally:
        if client and disconnect_client and owns_client:
            try:
                await client.disconnect()
            except Exception:
                pass
