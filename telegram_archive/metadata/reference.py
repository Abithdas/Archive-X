"""Canonical filename reference database.

Built during Metadata match (and opportunistically whenever a video is
otherwise identified) so that a Telegram filename can be resolved against
already-known scenes without another provider call, a re-scan, or sending
anything to AI. See the "Canonical Filename Reference Database" engineering
prompt for the full design; this module implements sections 1, 4-7.

One canonical identity (`reference_keys`, keyed by `canonical_key`) can have
many filename aliases (`reference_aliases`) -- multiple Telegram uploads of
the same scene under different names all resolve to the one row, and never
create a duplicate metadata record.
"""
from __future__ import annotations

import hashlib
import json
import re

from .. import config
from .parser import parse_scene_name, canonical_key as normalize_filename_key


def build_canonical_key(hints: dict) -> str:
    """The canonical identity for a scene, derived from parsed filename hints.

    A scene/production code (e.g. "SONE-012") is a strong, fairly unique
    identifier and is always preferred. A title alone is NOT a stable scene
    identity: titles can collide across performers, studios and releases.
    Title-only metadata is therefore handled only as a search/suggestion signal
    and is never written as an authoritative reference key.
    """
    code = str(hints.get("code") or "").strip().upper()
    if code:
        return "code:" + re.sub(r"[^a-z0-9]+", "", code.lower())
    # IMPORTANT: do not create title-only canonical identities. A title such
    # as "The Pizza Can Wait" can legitimately refer to multiple scenes.
    return ""


def _metadata_hash(title, performer_names, studio_name, release_date) -> str:
    raw = json.dumps([title, sorted(performer_names or []), studio_name, release_date], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _write_scene_snapshot(canonical_key: str, *, title=None, performer_names=None,
                          studio_name=None, site_name=None, release_date=None,
                          provider=None, provider_record_id=None, provider_url=None,
                          channel_id=None, message_id=None) -> None:
    """Write a JSON snapshot of a scene's reference data."""
    if not canonical_key:
        return
    scene_id = hashlib.sha256(str(canonical_key).encode("utf-8")).hexdigest()[:20]
    payload = {
        "canonical_key": canonical_key,
        "title": title,
        "performers": list(performer_names or []),
        "studio": studio_name,
        "site": site_name,
        "release_date": release_date,
        "provider": provider,
        "provider_record_id": provider_record_id,
        "provider_url": provider_url,
        "source_channel": str(channel_id) if channel_id is not None else None,
        "source_message_id": int(message_id) if message_id is not None else None,
        "updated_at": config.now_iso(),
    }
    path = config.SCENE_METADATA_DIR / f"{scene_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)

    # Keep an easy-to-discover site metadata copy when the scene resolves to a
    # provider/site. The scene JSON remains the canonical full mapping.
    if site_name:
        site_id = hashlib.sha256(str(site_name).strip().casefold().encode("utf-8")).hexdigest()[:20]
        config.SITE_METADATA_DIR.mkdir(parents=True, exist_ok=True)
        site_path = config.SITE_METADATA_DIR / f"{site_id}.json"
        site_tmp = site_path.with_suffix(site_path.suffix + ".tmp")
        site_tmp.write_text(json.dumps({
            "name": site_name,
            "scene_count_last_seen": 1,
            "provider": provider,
            "provider_url": provider_url,
            "updated_at": config.now_iso(),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        site_tmp.replace(site_path)


def upsert_reference(db, *, canonical_key: str, filename: str, title=None,
                      performer_names=None, studio_name=None, site_name=None,
                      release_date=None, provider=None, provider_record_id=None,
                      provider_url=None, channel_id=None, message_id=None) -> None:
    """Create/update the canonical identity and add this filename as an alias.

    Incremental by design (section 18): if nothing about the metadata
    actually changed, the reference_keys row is left untouched (metadata_hash
    matches) -- only the alias (if new) and updated_at move. Never creates a
    second reference_keys row for the same canonical_key.
    """
    if not canonical_key:
        return
    now = config.now_iso()
    performer_names = list(performer_names or [])
    mhash = _metadata_hash(title, performer_names, studio_name, release_date)
    with db.lock:
        existing = db.conn.execute(
            "SELECT metadata_hash FROM reference_keys WHERE canonical_key=?", (canonical_key,)
        ).fetchone()
        if existing is None:
            db.conn.execute(
                """INSERT INTO reference_keys(canonical_key,title,performer_names,studio_name,
                   site_name,release_date,provider,provider_record_id,provider_url,
                   metadata_hash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (canonical_key, title, json.dumps(performer_names, ensure_ascii=False), studio_name,
                 site_name, release_date, provider, str(provider_record_id or ""), provider_url,
                 mhash, now),
            )
        elif existing["metadata_hash"] != mhash:
            # Something about the metadata genuinely changed -- update in place
            # (never a second row for the same canonical_key).
            db.conn.execute(
                """UPDATE reference_keys SET title=?, performer_names=?, studio_name=?, site_name=?,
                   release_date=?, provider=?, provider_record_id=?, provider_url=?,
                   metadata_hash=?, updated_at=? WHERE canonical_key=?""",
                (title, json.dumps(performer_names, ensure_ascii=False), studio_name, site_name,
                 release_date, provider, str(provider_record_id or ""), provider_url, mhash, now,
                 canonical_key),
            )
        # else: unchanged -- SKIP (section 18), nothing to write for the key row.

        if filename:
            norm_filename = normalize_filename_key(filename)
            if norm_filename:
                db.conn.execute(
                    """INSERT INTO reference_aliases(canonical_key,normalized_filename,original_filename,
                       source_channel,source_message_id,created_at) VALUES(?,?,?,?,?,?)
                       ON CONFLICT(canonical_key,normalized_filename) DO NOTHING""",
                    (canonical_key, norm_filename, filename,
                     str(channel_id) if channel_id is not None else None,
                     int(message_id) if message_id is not None else None, now),
                )
        db.conn.commit()
    try:
        _write_scene_snapshot(canonical_key, title=title, performer_names=performer_names,
                              studio_name=studio_name, site_name=site_name, release_date=release_date,
                              provider=provider, provider_record_id=provider_record_id,
                              provider_url=provider_url, channel_id=channel_id, message_id=message_id)
    except Exception as exc:
        # Snapshot files are an auxiliary export; never make metadata matching
        # fail, but record why the export is missing.
        db.log(f"Scene reference export failed for {canonical_key}: {type(exc).__name__}: {exc}", "WARN")


def lookup_reference(db, filename: str) -> dict | None:
    """Deterministic local lookup only -- no network, no AI (sections 3, 11 L1-L3).

    Returns {'canonical_key','confidence','method','title','performer_names',
    'studio_name','site_name','release_date','provider','provider_record_id',
    'provider_url'} or None if nothing local matches.
    """
    hints = parse_scene_name(filename)
    norm_filename = normalize_filename_key(filename)

    with db.lock:
        # Level 2 first: an exact filename we've literally seen before is the
        # strongest possible signal, regardless of how the key was derived.
        if norm_filename:
            row = db.conn.execute(
                "SELECT canonical_key FROM reference_aliases WHERE normalized_filename=? LIMIT 1",
                (norm_filename,),
            ).fetchone()
            if row:
                key_row = db.conn.execute(
                    "SELECT * FROM reference_keys WHERE canonical_key=?", (row["canonical_key"],)
                ).fetchone()
                if key_row:
                    confidence = "HIGH" if str(row["canonical_key"]).startswith("code:") else "MEDIUM"
                    return _reference_result(key_row, confidence, "ALIAS_KEY")

        # Level 1: exact canonical key derived the same way from this filename.
        key = build_canonical_key(hints)
        if key:
            key_row = db.conn.execute(
                "SELECT * FROM reference_keys WHERE canonical_key=?", (key,)
            ).fetchone()
            if key_row:
                confidence = "HIGH" if key.startswith("code:") else "MEDIUM"
                return _reference_result(key_row, confidence, "EXACT_CANONICAL_KEY")

        # Level 3: same scene code, different title wording -- still a solid
        # local signal, but not as strong as an exact key/alias hit.
        code = str(hints.get("code") or "").strip().upper()
        if code:
            code_key = "code:" + re.sub(r"[^a-z0-9]+", "", code.lower())
            key_row = db.conn.execute(
                "SELECT * FROM reference_keys WHERE canonical_key=?", (code_key,)
            ).fetchone()
            if key_row:
                return _reference_result(key_row, "MEDIUM", "CODE_TITLE")

    return None


def _reference_result(key_row, confidence: str, method: str) -> dict:
    try:
        performer_names = json.loads(key_row["performer_names"] or "[]")
    except Exception:
        performer_names = []
    return {
        "canonical_key": key_row["canonical_key"], "confidence": confidence, "method": method,
        "title": key_row["title"], "performer_names": performer_names,
        "studio_name": key_row["studio_name"], "site_name": key_row["site_name"],
        "release_date": key_row["release_date"], "provider": key_row["provider"],
        "provider_record_id": key_row["provider_record_id"], "provider_url": key_row["provider_url"],
    }


def reference_to_provider_match(ref: dict) -> dict:
    """Reshape a lookup_reference() result into the same match-dict shape
    metadata/providers.apply_scene_match() already expects, so applying a
    local-reference match reuses that existing, tested code path (title
    tagging, video_studios/video_performers relationships, no duplicate
    video record) instead of a second implementation."""
    return {
        "source": "local_reference", "id": ref.get("provider_record_id") or ref["canonical_key"],
        "title": ref.get("title"), "date": ref.get("release_date"),
        "studio": ref.get("studio_name"), "site": ref.get("site_name") or ref.get("studio_name"),
        "performers": ref.get("performer_names") or [],
        "performer_details": [{"name": n} for n in (ref.get("performer_names") or [])],
        "tags": [],
        "profile": ref.get("provider_url"),
    }


def export_reference_txt(db, path) -> int:
    """Compact pipe-delimited TXT export (section 7) -- KEY|TITLE|PERFORMERS|
    SITE|DATE|PROVIDER|PROVIDER_ID. No provider payloads, never the full DB;
    this is a lightweight cache for AI reference/debugging/manual inspection,
    SQLite stays authoritative. Returns the number of lines written."""
    with db.lock:
        rows = db.conn.execute(
            "SELECT canonical_key,title,performer_names,site_name,studio_name,release_date,"
            "provider,provider_record_id FROM reference_keys ORDER BY canonical_key"
        ).fetchall()
    lines = []
    for r in rows:
        try:
            performers = json.loads(r["performer_names"] or "[]")
        except Exception:
            performers = []
        site = r["site_name"] or r["studio_name"] or ""
        fields = [
            r["canonical_key"] or "", r["title"] or "", ",".join(performers),
            site, r["release_date"] or "", r["provider"] or "", r["provider_record_id"] or "",
        ]
        # Pipes/newlines in any field would corrupt the line format -- strip them.
        lines.append("|".join(f.replace("|", "/").replace("\n", " ") for f in fields))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)
