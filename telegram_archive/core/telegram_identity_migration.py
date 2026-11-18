"""Repair legacy rows accidentally keyed by Telethon's raw positive channel ID.

Canonical Archive X channel IDs are Telethon marked peer IDs (for channels, -100…).
This migration is run only when a transfer resolves the real channel entity, so the
peer type is known. Canonical rows win collisions; raw-only rows are re-keyed.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Schema helpers
#   - Row and cache rekeying
#   - Migration entry point
# ------------------------------------------------------------------
from __future__ import annotations

import os
import shutil
from pathlib import Path




# ==============================================================================
# SECTION: Schema helpers
# Table and column checks.
# ==============================================================================

def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(conn, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()




# ==============================================================================
# SECTION: Row and cache rekeying
# Merges rows and rewrites IDs, cache paths and registry entries.
# ==============================================================================

def _merge_media_rows(conn, raw: str, marked: str, stats: dict) -> None:
    """Merge media rows under a raw channel ID into the marked ID."""
    if not _table_exists(conn, "media"):
        return
    columns = _columns(conn, "media")
    if not {"id", "source_channel", "source_message_id"}.issubset(columns):
        return
    source_rows = conn.execute("SELECT * FROM media WHERE source_channel=?", (raw,)).fetchall()
    for source_row in source_rows:
        src = dict(source_row)
        mid = int(src.get("source_message_id") or 0)
        target_row = conn.execute(
            "SELECT * FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
            (marked, mid),
        ).fetchone()
        if target_row is None:
            conn.execute("UPDATE media SET source_channel=? WHERE id=?", (marked, int(src["id"])))
            target_id = int(src["id"])
            stats["media_rekeyed"] += 1
        else:
            target = dict(target_row)
            target_id = int(target["id"])
            # The original marked-key record is authoritative. Fill only empty
            # cells, never overwrite known canonical values with a duplicate run.
            assignments, params = [], []
            for col in columns - {"id", "source_channel", "source_message_id"}:
                current = target.get(col)
                incoming = src.get(col)
                empty = current is None or current == "" or (isinstance(current, (int, float)) and current == 0)
                if empty and incoming not in (None, "") and incoming != 0:
                    assignments.append(f'"{col}"=?')
                    params.append(incoming)
            if assignments:
                params.append(target_id)
                conn.execute(f"UPDATE media SET {','.join(assignments)} WHERE id=?", params)
            # Preserve linked performers, studios, tags and locally assigned
            # performers before removing the duplicate media primary key. This
            # is intentionally idempotent and safe if the earlier repair already
            # copied some or all of these relationships.
            for relation_table in ("video_performers", "video_studios", "video_tags", "local_video_performers"):
                if not _table_exists(conn, relation_table):
                    continue
                relation_cols = _columns(conn, relation_table)
                if "video_id" not in relation_cols:
                    continue
                payload_cols = sorted(relation_cols - {"video_id"})
                insert_cols = ["video_id", *payload_cols]
                select_exprs = ["?"] + [f'"{column}"' for column in payload_cols]
                conn.execute(
                    f'INSERT OR IGNORE INTO "{relation_table}"({",".join(insert_cols)}) '
                    f'SELECT {",".join(select_exprs)} FROM "{relation_table}" WHERE video_id=?',
                    (target_id, int(src["id"])),
                )
            # Merge per-media readiness conservatively: the canonical row wins,
            # except an UNKNOWN/PENDING state can inherit a completed legacy state.
            if _table_exists(conn, "resource_state"):
                legacy_state = conn.execute("SELECT * FROM resource_state WHERE media_id=?", (int(src["id"]),)).fetchone()
                current_state = conn.execute("SELECT * FROM resource_state WHERE media_id=?", (target_id,)).fetchone()
                if legacy_state:
                    legacy = dict(legacy_state)
                    if not current_state:
                        conn.execute("UPDATE resource_state SET media_id=? WHERE media_id=?", (target_id, int(src["id"])))
                    else:
                        current = dict(current_state)
                        fields = _columns(conn, "resource_state") - {"media_id", "updated_at"}
                        sets, vals = [], []
                        for field in fields:
                            value, old = current.get(field), legacy.get(field)
                            if str(value or "").upper() in {"", "UNKNOWN", "PENDING", "MISSING"} and str(old or "").upper() not in {"", "UNKNOWN", "PENDING", "MISSING"}:
                                sets.append(f'"{field}"=?'); vals.append(old)
                            elif field in {"content_revision", "metadata_revision"}:
                                sets.append(f'"{field}"=MAX(COALESCE("{field}",0),?)'); vals.append(int(old or 0))
                        if sets:
                            vals.extend([target_id])
                            conn.execute(f"UPDATE resource_state SET {','.join(sets)},updated_at=CURRENT_TIMESTAMP WHERE media_id=?", vals)
                        conn.execute("DELETE FROM resource_state WHERE media_id=?", (int(src["id"]),))
            if _table_exists(conn, "asset_registry"):
                assets = conn.execute("SELECT * FROM asset_registry WHERE media_id=?", (int(src["id"]),)).fetchall()
                asset_cols = _columns(conn, "asset_registry")
                for asset_row in assets:
                    asset = dict(asset_row)
                    new_entity_id = str(asset.get("entity_id") or "").replace(f"{raw}:", f"{marked}:", 1)
                    existing = conn.execute(
                        "SELECT asset_id FROM asset_registry WHERE entity_type=? AND entity_id=? AND asset_type=? LIMIT 1",
                        (asset.get("entity_type"), new_entity_id, asset.get("asset_type")),
                    ).fetchone()
                    if existing:
                        conn.execute("DELETE FROM asset_registry WHERE asset_id=?", (int(asset["asset_id"]),))
                    else:
                        conn.execute("UPDATE asset_registry SET media_id=?,entity_id=? WHERE asset_id=?",
                                     (target_id, new_entity_id, int(asset["asset_id"])))
            if _table_exists(conn, "telegram_document_registry") and "media_id" in _columns(conn, "telegram_document_registry"):
                conn.execute("UPDATE telegram_document_registry SET media_id=? WHERE media_id=?", (target_id, int(src["id"])))
            # Preserve historical resource events by attaching them to the surviving row.
            if _table_exists(conn, "resource_events"):
                conn.execute("UPDATE resource_events SET media_id=? WHERE media_id=?", (target_id, int(src["id"])))
            if _table_exists(conn, "work_registry"):
                conn.execute("UPDATE work_registry SET media_id=? WHERE media_id=?", (target_id, int(src["id"])))
            # Move remaining media-keyed history/pending work where the table
            # has no uniqueness constraint on the media key. For known unique
            # tables, merge or discard only duplicate keys explicitly.
            if _table_exists(conn, "pending_actions") and "media_id" in _columns(conn, "pending_actions"):
                conn.execute("UPDATE pending_actions SET media_id=? WHERE media_id=?", (target_id, int(src["id"])))
            if _table_exists(conn, "automation_items") and "media_id" in _columns(conn, "automation_items"):
                conn.execute("UPDATE automation_items SET media_id=? WHERE media_id=?", (target_id, int(src["id"])))
            if _table_exists(conn, "archive_x_deliveries"):
                cols = _columns(conn, "archive_x_deliveries")
                if "source_media_id" in cols:
                    conn.execute("UPDATE archive_x_deliveries SET source_media_id=? WHERE source_media_id=?", (target_id, int(src["id"])))
                if "library_media_id" in cols:
                    conn.execute("UPDATE archive_x_deliveries SET library_media_id=? WHERE library_media_id=?", (target_id, int(src["id"])))
            if _table_exists(conn, "watch_history"):
                old_history = conn.execute("SELECT * FROM watch_history WHERE media_id=?", (int(src["id"]),)).fetchone()
                new_history = conn.execute("SELECT * FROM watch_history WHERE media_id=?", (target_id,)).fetchone()
                if old_history and not new_history:
                    conn.execute("UPDATE watch_history SET media_id=? WHERE media_id=?", (target_id, int(src["id"])))
                elif old_history and new_history:
                    old_h, new_h = dict(old_history), dict(new_history)
                    updates, values = [], []
                    for column in ("position", "duration", "play_count", "total_watch_seconds"):
                        if column in old_h and column in new_h:
                            updates.append(f'"{column}"=MAX(COALESCE("{column}",0),?)')
                            values.append(old_h[column] or 0)
                    if "last_played_at" in old_h and "last_played_at" in new_h and old_h.get("last_played_at"):
                        updates.append("\"last_played_at\"=CASE WHEN COALESCE(\"last_played_at\",'')<? THEN ? ELSE \"last_played_at\" END")
                        values.extend([old_h["last_played_at"], old_h["last_played_at"]])
                    if "completed" in old_h and "completed" in new_h:
                        updates.append('"completed"=MAX(COALESCE("completed",0),?)')
                        values.append(old_h["completed"] or 0)
                    if "updated_at" in old_h and old_h.get("updated_at"):
                        updates.append("\"updated_at\"=CASE WHEN COALESCE(\"updated_at\",'')<? THEN ? ELSE \"updated_at\" END")
                        values.extend([old_h["updated_at"], old_h["updated_at"]])
                    if updates:
                        values.append(target_id)
                        conn.execute("UPDATE watch_history SET " + ",".join(updates) + " WHERE media_id=?", values)
                    conn.execute("DELETE FROM watch_history WHERE media_id=?", (int(src["id"]),))
            # Migrate media-keyed metadata/user state without duplicating existing
            # canonical records. INSERT OR IGNORE handles table-specific unique
            # constraints; history rows without a unique key are retained.
            for relation_table in ("metadata_suggestions", "scene_markers", "metadata_mapping_history", "provider_field_sources", "metadata_mapping_locks"):
                if not _table_exists(conn, relation_table):
                    continue
                relation_cols = _columns(conn, relation_table)
                key_column = "video_id" if "video_id" in relation_cols else ("media_id" if "media_id" in relation_cols else "")
                if not key_column:
                    continue
                rows = conn.execute(f'SELECT * FROM "{relation_table}" WHERE "{key_column}"=?', (int(src["id"]),)).fetchall()
                insert_columns = sorted(relation_cols - {"id", key_column})
                for relation_row in rows:
                    rec = dict(relation_row)
                    cols = [key_column, *insert_columns]
                    vals = [target_id, *[rec[column] for column in insert_columns]]
                    conn.execute(
                        f'INSERT OR IGNORE INTO "{relation_table}"({",".join(cols)}) VALUES({",".join("?" for _ in cols)})',
                        vals,
                    )
                # Every source row was copied or ignored due to a canonical
                # unique-key match, so now discard its old-media counterpart.
                conn.execute(f'DELETE FROM "{relation_table}" WHERE "{key_column}"=?', (int(src["id"]),))
            conn.execute("DELETE FROM media WHERE id=?", (int(src["id"]),))
            stats["media_merged"] += 1


def _rekey_asset_registry_ids(conn, raw: str, marked: str, stats: dict) -> None:
    if not _table_exists(conn, "asset_registry"):
        return
    rows = conn.execute("SELECT * FROM asset_registry WHERE entity_id LIKE ?", (f"{raw}:%",)).fetchall()
    for row in rows:
        rec = dict(row)
        old_id = str(rec.get("entity_id") or "")
        new_id = old_id.replace(f"{raw}:", f"{marked}:", 1)
        duplicate = conn.execute(
            "SELECT * FROM asset_registry WHERE entity_type=? AND entity_id=? AND asset_type=? LIMIT 1",
            (rec.get("entity_type"), new_id, rec.get("asset_type")),
        ).fetchone()
        if duplicate:
            target = dict(duplicate)
            if not target.get("media_id") and rec.get("media_id"):
                conn.execute("UPDATE asset_registry SET media_id=? WHERE asset_id=?", (int(rec["media_id"]), int(target["asset_id"])))
            if not int(target.get("size_bytes") or 0) and int(rec.get("size_bytes") or 0):
                conn.execute("UPDATE asset_registry SET size_bytes=? WHERE asset_id=?", (int(rec["size_bytes"]), int(target["asset_id"])))
            conn.execute("DELETE FROM asset_registry WHERE asset_id=?", (int(rec["asset_id"]),))
        else:
            conn.execute("UPDATE asset_registry SET entity_id=? WHERE asset_id=?", (new_id, int(rec["asset_id"])))
            stats["asset_registry_rekeyed"] = stats.get("asset_registry_rekeyed", 0) + 1


def _rewrite_cache_paths(db, raw: str, marked: str, stats: dict) -> None:
    """Update stored paths after channel-keyed cache folders are merged."""
    with db.lock:
        conn = db.conn
        pairs = [(f"/{raw}/", f"/{marked}/"), (f"\\{raw}\\", f"\\{marked}\\")]
        for table in ("timeline_preview_cache", "asset_registry"):
            if not _table_exists(conn, table):
                continue
            columns = _columns(conn, table)
            path_columns = [column for column in ("path", "frames_path") if column in columns]
            for path_column in path_columns:
                rows = conn.execute(
                    f'SELECT _rowid_ AS row_id,"{path_column}" AS stored_path FROM "{table}" WHERE "{path_column}" IS NOT NULL'
                ).fetchall()
                for row in rows:
                    old_path = str(row["stored_path"] or "")
                    new_path = old_path
                    for old_part, new_part in pairs:
                        new_path = new_path.replace(old_part, new_part)
                    if new_path != old_path:
                        conn.execute(
                            f'UPDATE "{table}" SET "{path_column}"=? WHERE rowid=?',
                            (new_path, row["row_id"]),
                        )
                        stats["cache_paths_rekeyed"] = stats.get("cache_paths_rekeyed", 0) + 1
        conn.commit()


def _rekey_pair_table(conn, table: str, channel_col: str, message_col: str, raw: str, marked: str,
                      stats: dict) -> None:
    if not _table_exists(conn, table):
        return
    cols = _columns(conn, table)
    if not {channel_col, message_col}.issubset(cols):
        return
    # The comparisons below are written without CAST(): these columns are declared TEXT / INTEGER, and a CAST on the column hides it from the
    # table's (channel, message) index, so every row looked up below read the whole table (thumbnail rows carry the pictures): a minute or more.
    rows = conn.execute(f'SELECT * FROM "{table}" WHERE "{channel_col}"=?', (raw,)).fetchall()
    for row in rows:
        rec = dict(row)
        mid = int(rec.get(message_col) or 0)
        target = conn.execute(
            f'SELECT 1 FROM "{table}" WHERE "{channel_col}"=? AND "{message_col}"=? LIMIT 1',
            (marked, mid),
        ).fetchone()
        if target:
            conn.execute(f'DELETE FROM "{table}" WHERE "{channel_col}"=? AND "{message_col}"=?', (raw, mid))
            stats[f"{table}_duplicates_removed"] += 1
        else:
            conn.execute(f'UPDATE "{table}" SET "{channel_col}"=? WHERE "{channel_col}"=? AND "{message_col}"=?', (marked, raw, mid))
            stats[f"{table}_rekeyed"] += 1


def _rekey_document_locations_and_registry(conn, raw: str, marked: str, stats: dict) -> None:
    """Move document locations and registry rows to the marked channel ID."""
    if not _table_exists(conn, "telegram_document_locations"):
        return
    location_cols = _columns(conn, "telegram_document_locations")
    if not {"channel_id", "message_id", "registry_key"}.issubset(location_cols):
        return
    rows = conn.execute("SELECT * FROM telegram_document_locations WHERE CAST(channel_id AS TEXT)=?", (raw,)).fetchall()
    for row in rows:
        rec = dict(row)
        mid = int(rec.get("message_id") or 0)
        existing = conn.execute(
            "SELECT 1 FROM telegram_document_locations WHERE channel_id=? AND message_id=? LIMIT 1",
            (marked, mid),
        ).fetchone()
        old_key = str(rec.get("registry_key") or "")
        new_key = old_key.replace(f"legacy:{raw}:", f"legacy:{marked}:", 1)
        if existing:
            conn.execute("DELETE FROM telegram_document_locations WHERE channel_id=? AND message_id=?", (raw, mid))
            stats["telegram_document_locations_duplicates_removed"] += 1
            # Canonical location wins. Remove a now-unreferenced positive-key registry item below.
        else:
            conn.execute(
                "UPDATE telegram_document_locations SET channel_id=?,registry_key=? WHERE channel_id=? AND message_id=?",
                (marked, new_key, raw, mid),
            )
            stats["telegram_document_locations_rekeyed"] += 1
    if _table_exists(conn, "telegram_document_registry"):
        cols = _columns(conn, "telegram_document_registry")
        if "registry_key" not in cols:
            return
        legacy_rows = conn.execute(
            "SELECT * FROM telegram_document_registry WHERE registry_key LIKE ?", (f"legacy:{raw}:%",)
        ).fetchall()
        for row in legacy_rows:
            rec = dict(row); old_key = str(rec["registry_key"]); new_key = old_key.replace(f"legacy:{raw}:", f"legacy:{marked}:", 1)
            dest = conn.execute("SELECT * FROM telegram_document_registry WHERE registry_key=? LIMIT 1", (new_key,)).fetchone()
            if dest:
                dest_dict = dict(dest)
                # Preserve PREPARED state if either registry row had finished preparation.
                if str(dest_dict.get("prepared_state") or "").upper() != "PREPARED" and str(rec.get("prepared_state") or "").upper() == "PREPARED":
                    conn.execute(
                        "UPDATE telegram_document_registry SET prepared_state='PREPARED',prepared_at=COALESCE(prepared_at,?),prepared_json=CASE WHEN prepared_json='{}' THEN ? ELSE prepared_json END,oshash=COALESCE(NULLIF(oshash,''),?),phash=COALESCE(NULLIF(phash,''),?) WHERE registry_key=?",
                        (rec.get("prepared_at"), rec.get("prepared_json") or "{}", rec.get("oshash"), rec.get("phash"), new_key),
                    )
                conn.execute("UPDATE telegram_document_locations SET registry_key=? WHERE registry_key=?", (new_key, old_key))
                conn.execute("DELETE FROM telegram_document_registry WHERE registry_key=?", (old_key,))
                stats["telegram_document_registry_duplicates_merged"] = stats.get("telegram_document_registry_duplicates_merged", 0) + 1
            else:
                conn.execute("UPDATE telegram_document_registry SET registry_key=? WHERE registry_key=?", (new_key, old_key))
                conn.execute("UPDATE telegram_document_locations SET registry_key=? WHERE registry_key=?", (new_key, old_key))
                stats["telegram_document_registry_rekeyed"] = stats.get("telegram_document_registry_rekeyed", 0) + 1
        # Sweep unreferenced positive legacy rows left after a canonical location won.
        conn.execute(
            "DELETE FROM telegram_document_registry WHERE registry_key LIKE ? AND NOT EXISTS "
            "(SELECT 1 FROM telegram_document_locations l WHERE l.registry_key=telegram_document_registry.registry_key)",
            (f"legacy:{raw}:%",),
        )


def _rekey_channel_state(conn, table: str, column: str, raw: str, marked: str, stats: dict) -> None:
    if not _table_exists(conn, table) or column not in _columns(conn, table):
        return
    source = conn.execute(f'SELECT * FROM "{table}" WHERE CAST("{column}" AS TEXT)=? LIMIT 1', (raw,)).fetchone()
    if not source:
        return
    source = dict(source)
    target = conn.execute(f'SELECT * FROM "{table}" WHERE CAST("{column}" AS TEXT)=? LIMIT 1', (marked,)).fetchone()
    if not target:
        conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE CAST("{column}" AS TEXT)=?', (marked, raw))
        stats[f"{table}_rekeyed"] += 1
        return
    target = dict(target)
    cols = _columns(conn, table)
    assignments, params = [], []
    if "high_water_mark" in cols:
        assignments.append('"high_water_mark"=MAX(COALESCE("high_water_mark",0),?)')
        params.append(int(source.get("high_water_mark") or 0))
    if "last_message_id" in cols:
        assignments.append('"last_message_id"=MAX(COALESCE("last_message_id",0),?)')
        params.append(int(source.get("last_message_id") or 0))
    if "last_scan_status" in cols and str(target.get("last_scan_status") or "").upper() not in {"COMPLETE"} and str(source.get("last_scan_status") or "").upper() == "COMPLETE":
        assignments.append('"last_scan_status"=?'); params.append(source["last_scan_status"])
    if "last_scan_at" in cols and not target.get("last_scan_at") and source.get("last_scan_at"):
        assignments.append('"last_scan_at"=?'); params.append(source["last_scan_at"])
    if assignments:
        params.append(marked)
        conn.execute(f'UPDATE "{table}" SET {", ".join(assignments)} WHERE CAST("{column}" AS TEXT)=?', params)
    conn.execute(f'DELETE FROM "{table}" WHERE CAST("{column}" AS TEXT)=?', (raw,))
    stats[f"{table}_merged"] += 1


def _merge_cache_directories(raw: str, marked: str, roots: list[Path], stats: dict) -> None:
    """Merge ID-keyed cache folders without overwriting canonical files."""
    for root in roots:
        source = root / raw
        target = root / marked
        if not source.is_dir():
            continue
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                source.replace(target)
                stats["cache_directories_rekeyed"] += 1
                continue
            except OSError:
                target.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(source)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    item.replace(dest)
                    stats["cache_files_rekeyed"] += 1
                except OSError:
                    shutil.copy2(item, dest)
                    stats["cache_files_rekeyed"] += 1
            else:
                # Canonical cache wins. These are reproducible derived assets.
                stats["cache_files_shadowed"] += 1
        try:
            shutil.rmtree(source, ignore_errors=True)
        except OSError:
            pass




# ==============================================================================
# SECTION: Migration entry point
# Runs the full legacy channel ID repair.
# ==============================================================================

def rekey_legacy_channel_rows(db, raw_channel_id: str | int, marked_channel_id: str | int,
                              cache_roots: list[Path] | None = None) -> dict[str, int]:
    """Merge an accidental raw-positive channel key into its canonical marked ID.

    Returns counters suitable for a concise log entry. It is idempotent and does
    nothing when raw and marked IDs are already equal.
    """
    raw, marked = str(raw_channel_id).strip(), str(marked_channel_id).strip()
    stats: dict[str, int] = {
        "media_rekeyed": 0, "media_merged": 0, "thumbnail_cache_rekeyed": 0,
        "thumbnail_cache_duplicates_removed": 0, "timeline_preview_cache_rekeyed": 0,
        "timeline_preview_cache_duplicates_removed": 0, "telegram_document_locations_rekeyed": 0,
        "telegram_document_locations_duplicates_removed": 0, "media_probe_rekeyed": 0,
        "media_probe_duplicates_removed": 0, "telegram_document_registry_rekeyed": 0,
        "telegram_document_registry_duplicates_merged": 0, "asset_registry_rekeyed": 0,
        "cache_paths_rekeyed": 0, "telegram_channel_registry_state_rekeyed": 0,
        "telegram_channel_registry_state_merged": 0, "scan_checkpoints_rekeyed": 0,
        "scan_checkpoints_merged": 0, "cache_directories_rekeyed": 0,
        "cache_files_rekeyed": 0, "cache_files_shadowed": 0,
    }
    if db is None or not raw or not marked or raw == marked:
        return stats
    if not (raw.lstrip("-").isdigit() and marked.lstrip("-").isdigit()):
        return stats
    with db.lock:
        conn = db.conn
        conn.execute("SAVEPOINT archive_x_identity_rekey")
        try:
            _merge_media_rows(conn, raw, marked, stats)
            _rekey_asset_registry_ids(conn, raw, marked, stats)
            _rekey_pair_table(conn, "thumbnail_cache", "source_channel", "video_message_id", raw, marked, stats)
            _rekey_pair_table(conn, "timeline_preview_cache", "source_channel", "video_message_id", raw, marked, stats)
            _rekey_pair_table(conn, "media_probe", "source_channel", "message_id", raw, marked, stats)
            _rekey_document_locations_and_registry(conn, raw, marked, stats)
            _rekey_channel_state(conn, "telegram_channel_registry_state", "channel_id", raw, marked, stats)
            _rekey_channel_state(conn, "scan_checkpoints", "channel_id", raw, marked, stats)
            # Older builds kept the per-channel inventory high-water mark in settings.
            if _table_exists(conn, "settings"):
                raw_key = f"inventory_highwater_v1_{raw}"
                marked_key = f"inventory_highwater_v1_{marked}"
                old = conn.execute("SELECT value FROM settings WHERE key=?", (raw_key,)).fetchone()
                new = conn.execute("SELECT value FROM settings WHERE key=?", (marked_key,)).fetchone()
                if old:
                    try: old_value = int(old[0] or 0)
                    except (TypeError, ValueError): old_value = 0
                    try: new_value = int(new[0] or 0) if new else 0
                    except (TypeError, ValueError): new_value = 0
                    if new:
                        conn.execute("UPDATE settings SET value=? WHERE key=?", (str(max(old_value, new_value)), marked_key))
                        conn.execute("DELETE FROM settings WHERE key=?", (raw_key,))
                    else:
                        conn.execute("UPDATE settings SET key=? WHERE key=?", (marked_key, raw_key))
            # Channel chooser rows are keyed by Telegram ID too. Merge names conservatively.
            if _table_exists(conn, "channels") and "telegram_id" in _columns(conn, "channels"):
                raw_channel = conn.execute("SELECT * FROM channels WHERE CAST(telegram_id AS TEXT)=? LIMIT 1", (raw,)).fetchone()
                if raw_channel:
                    target_channel = conn.execute("SELECT * FROM channels WHERE CAST(telegram_id AS TEXT)=? LIMIT 1", (marked,)).fetchone()
                    if target_channel:
                        target = dict(target_channel); source = dict(raw_channel)
                        if not target.get("title") and source.get("title"):
                            conn.execute("UPDATE channels SET title=? WHERE id=?", (source["title"], int(target["id"])))
                        conn.execute("DELETE FROM channels WHERE id=?", (int(source["id"]),))
                    else:
                        conn.execute("UPDATE channels SET telegram_id=? WHERE id=?", (marked, int(raw_channel["id"])))
            conn.execute("RELEASE SAVEPOINT archive_x_identity_rekey")
            conn.commit()
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT archive_x_identity_rekey")
            conn.execute("RELEASE SAVEPOINT archive_x_identity_rekey")
            conn.rollback()
            raise
    if cache_roots:
        _merge_cache_directories(raw, marked, [Path(x) for x in cache_roots], stats)
    _rewrite_cache_paths(db, raw, marked, stats)
    return stats
