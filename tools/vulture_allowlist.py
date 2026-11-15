"""Names vulture would call unused but that are used or must stay (run: vulture telegram_archive tools/vulture_allowlist.py --min-confidence 80).

Each line says why. Delete a line when the thing it names goes; a new line needs a reason, not just a silenced warning.
"""
# Parameters kept so existing callers (some pass them by keyword) and subclasses keep working.
stale_after  # core/resource_manager.py: cooperative_background_checkpoint(...)
original_oshash  # engines/delivery_pipeline.py: persist_destination_location(...)
exc_type  # engines/telegram_sender.py: __aexit__(self, exc_type, exc, tb), the signature Python requires
thumbnail_source  # engines/telegram_sender.py: _register_copy(...)
explicit_thumbnail_id  # media_manager/mm_smart_router.py: _select_manager_thumbnail(...)
download_only  # operations/automation.py: _advance_active_item(...)
head_segments  # playback/stream_server.py: prepare_video(...), a compatibility no-op
# An import that only probes whether the optional Telethon speed-up is installed.
cryptg  # telegram/service.py
