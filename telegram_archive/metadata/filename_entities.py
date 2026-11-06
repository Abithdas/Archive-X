"""Conservative local linking of known performers/studios mentioned in filenames.

This is deliberately separate from scene identification. It can fill missing
entity relationships even when a provider cannot identify the exact scene, but
it never creates entities from free-form filename text and never overrides a
field explicitly locked by the operator.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from typing import Iterable

from .parser import split_fused_quality_before_date

_UNKNOWN_PERFORMER = "unknown performer"
_UNKNOWN_STUDIO = "unknown studio"
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(value: object, *, filename: bool = False) -> list[str]:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Some release templates fuse the performer and resolution marker, e.g.
    # MiaKhalifa1080PDate_Starring_Title_Bangbros. Split only this known marker;
    # arbitrary substrings are never fuzzy-matched.
    if filename:
        text = split_fused_quality_before_date(text)
    return _TOKEN_RE.findall(text)


def _aliases(row: dict) -> list[tuple[str, tuple[str, ...]]]:
    values = [str(row.get("name") or "")]
    raw = row.get("aliases")
    try:
        decoded = json.loads(raw or "[]") if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        decoded = []
    if isinstance(decoded, list):
        values.extend(str(v or "") for v in decoded)
    out: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for value in values:
        parts = _tokens(value)
        # A short one-word alias is too likely to be a generic title word.
        if not parts or (len(parts) == 1 and len(parts[0]) < 4):
            continue
        if len(parts) > 1 and any(len(part) < 2 for part in parts):
            continue
        key = "".join(parts)
        if key and key not in seen:
            out.append((key, tuple(parts)))
            seen.add(key)
    return out


def _candidate_index(rows: Iterable[dict]) -> tuple[dict[str, set[int]], int]:
    alias_ids: dict[str, set[int]] = defaultdict(set)
    max_alias_parts = 1
    for row in rows:
        entity_id = int(row["id"])
        for key, parts in _aliases(row):
            alias_ids[key].add(entity_id)
            max_alias_parts = max(max_alias_parts, len(parts))
    return alias_ids, min(max_alias_parts, 8)


def _filename_keys(tokens: list[str], max_parts: int) -> set[str]:
    """Generate compact keys only from whole adjacent filename tokens.

    This is linear in filename length times the bounded alias length, rather
    than comparing every known entity against every filename. It permits both
    "Mia_Khalifa" and "MiaKhalifa", while never matching a substring such as
    "Mia" in "Miami" or "BangBros" inside "Bangbrosevil".
    """
    found: set[str] = set()
    for start in range(len(tokens)):
        compact = ""
        for end in range(start, min(len(tokens), start + max_parts)):
            compact += tokens[end]
            found.add(compact)
    return found


def _match_ids(filename_keys: set[str], alias_ids: dict[str, set[int]]) -> tuple[set[int], int]:
    found: set[int] = set()
    ambiguous = 0
    for key in filename_keys:
        entity_ids = alias_ids.get(key)
        if not entity_ids:
            continue
        if len(entity_ids) != 1:
            ambiguous += 1
            continue
        found.update(entity_ids)
    return found, ambiguous


def link_known_entities_from_filenames(db, media_ids: Iterable[int]) -> dict[str, int]:
    """Link uniquely named existing entities to selected videos.

    The function runs in one SQLite savepoint, is idempotent, ignores source-
    deleted media, replaces default Unknown links only when an unambiguous real
    entity match is found, and respects `performer` / `site` mapping locks.
    Ambiguous duplicate entity names are skipped for manual resolution. Lightweight
    test doubles or unavailable database handles safely return an empty summary.
    """
    empty = {"videos_scanned": 0, "performer_links_added": 0, "studio_links_added": 0,
             "unknown_links_replaced": 0, "ambiguous_entities_skipped": 0}
    if not hasattr(db, "lock") or not hasattr(db, "conn"):
        return empty.copy()
    ids = []
    for value in media_ids or []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in ids:
            ids.append(item)
    if not ids:
        return {"videos_scanned": 0, "performer_links_added": 0, "studio_links_added": 0,
                "unknown_links_replaced": 0, "ambiguous_entities_skipped": 0}

    stats = {"videos_scanned": 0, "performer_links_added": 0, "studio_links_added": 0,
             "unknown_links_replaced": 0, "ambiguous_entities_skipped": 0}
    with db.lock:
        performer_rows = [dict(r) for r in db.conn.execute(
            "SELECT id,name,COALESCE(aliases,'[]') AS aliases FROM performers "
            "WHERE lower(name)<>? ORDER BY id", (_UNKNOWN_PERFORMER,)
        ).fetchall()]
        studio_rows = [dict(r) for r in db.conn.execute(
            "SELECT id,name,COALESCE(aliases,'[]') AS aliases FROM studios "
            "WHERE lower(name)<>? ORDER BY id", (_UNKNOWN_STUDIO,)
        ).fetchall()]
        performer_alias_ids, performer_max_parts = _candidate_index(performer_rows)
        studio_alias_ids, studio_max_parts = _candidate_index(studio_rows)
        unknown_perf_row = db.conn.execute("SELECT id FROM performers WHERE lower(name)=? LIMIT 1", (_UNKNOWN_PERFORMER,)).fetchone()
        unknown_studio_row = db.conn.execute("SELECT id FROM studios WHERE lower(name)=? LIMIT 1", (_UNKNOWN_STUDIO,)).fetchone()
        unknown_perf_id = int(unknown_perf_row["id"]) if unknown_perf_row else None
        unknown_studio_id = int(unknown_studio_row["id"]) if unknown_studio_row else None

        db.conn.execute("SAVEPOINT archive_x_filename_entity_link")
        try:
            rows = []
            # Keep below SQLite builds' historical 999-variable limit.
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                marks = ",".join("?" for _ in chunk)
                rows.extend(db.conn.execute(
                    f"SELECT id,filename,title,status FROM media WHERE id IN ({marks}) "
                    "AND COALESCE(status,'')<>'SOURCE_DELETED' ORDER BY id", tuple(chunk)
                ).fetchall())

            for media in rows:
                media_id = int(media["id"])
                filename = str(media["filename"] or media["title"] or "")
                if not filename:
                    continue
                filename_tokens = _tokens(filename, filename=True)
                if not filename_tokens:
                    continue
                stats["videos_scanned"] += 1
                matched_performers, ambiguous_perf = _match_ids(
                    _filename_keys(filename_tokens, performer_max_parts), performer_alias_ids
                )
                matched_studios, ambiguous_studio = _match_ids(
                    _filename_keys(filename_tokens, studio_max_parts), studio_alias_ids
                )
                stats["ambiguous_entities_skipped"] += ambiguous_perf + ambiguous_studio
                locks = {str(r["field"] or "").strip().casefold() for r in db.conn.execute(
                    "SELECT field FROM metadata_mapping_locks WHERE video_id=? AND locked=1", (media_id,)
                ).fetchall()}

                if matched_performers and "performer" not in locks:
                    existing = {int(r[0]) for r in db.conn.execute(
                        "SELECT performer_id FROM video_performers WHERE video_id=? "
                        "UNION SELECT performer_id FROM local_video_performers WHERE video_id=?",
                        (media_id, media_id),
                    ).fetchall()}
                    real_existing = existing - ({unknown_perf_id} if unknown_perf_id else set())
                    # Filename evidence may fill an Unknown/empty cast, but it
                    # never augments a non-empty provider/operator cast with
                    # unrelated names that merely appear in a scene title.
                    if not real_existing:
                        for performer_id in sorted(matched_performers):
                            if performer_id not in existing:
                                db.conn.execute(
                                    "INSERT OR IGNORE INTO video_performers(video_id,performer_id,confidence) VALUES(?,?,?)",
                                    (media_id, performer_id, 0.80),
                                )
                                stats["performer_links_added"] += 1
                    if unknown_perf_id is not None and unknown_perf_id in existing and (
                        not real_existing or bool(real_existing & matched_performers)
                    ):
                        # The default Unknown relation normally lives in
                        # video_performers, but old/manual data may have it in
                        # local_video_performers. Clear both representations
                        # so the UI cannot continue displaying Unknown.
                        removed = db.conn.execute(
                            "DELETE FROM video_performers WHERE video_id=? AND performer_id=?",
                            (media_id, unknown_perf_id),
                        ).rowcount
                        removed += db.conn.execute(
                            "DELETE FROM local_video_performers WHERE video_id=? AND performer_id=?",
                            (media_id, unknown_perf_id),
                        ).rowcount
                        if removed:
                            stats["unknown_links_replaced"] += 1

                # Studio/site is normally singular. Only use a filename match when
                # exactly one unambiguous existing studio is mentioned, and don't
                # add it alongside another real studio association.
                if len(matched_studios) == 1 and "site" not in locks:
                    studio_id = next(iter(matched_studios))
                    existing_rows = db.conn.execute(
                        "SELECT vs.studio_id,s.name FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=?",
                        (media_id,),
                    ).fetchall()
                    real_existing = {int(r["studio_id"]) for r in existing_rows if str(r["name"] or "").casefold() != _UNKNOWN_STUDIO}
                    unknown_existing = any(str(r["name"] or "").casefold() == _UNKNOWN_STUDIO for r in existing_rows)
                    if not real_existing or real_existing == {studio_id}:
                        db.conn.execute(
                            "INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,?)",
                            (media_id, studio_id, 0.80),
                        )
                        if unknown_existing and unknown_studio_id is not None:
                            db.conn.execute("DELETE FROM video_studios WHERE video_id=? AND studio_id=?", (media_id, unknown_studio_id))
                            stats["unknown_links_replaced"] += 1
                        # Insert rowcount is checked by the relationship lookup on re-runs;
                        # count only newly present canonical relationships in final totals.
                        if studio_id not in real_existing:
                            stats["studio_links_added"] += 1

            db.conn.execute("RELEASE SAVEPOINT archive_x_filename_entity_link")
            db.conn.commit()
        except Exception:
            db.conn.execute("ROLLBACK TO SAVEPOINT archive_x_filename_entity_link")
            db.conn.execute("RELEASE SAVEPOINT archive_x_filename_entity_link")
            raise
    return stats
