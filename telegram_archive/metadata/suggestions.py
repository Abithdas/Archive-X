"""metadata_suggestions: PENDING matches awaiting user Confirm/Reject.

Only HIGH-confidence matches (matching.py) are ever auto-linked via
providers.apply_scene_match. MEDIUM/LOW matches are written here instead —
nothing about the video changes until the operator confirms it.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Candidate helpers
#   - Saving and listing
#   - Confirm and reject
# ------------------------------------------------------------------
from __future__ import annotations

import json

from .. import config




# ==============================================================================
# SECTION: Candidate helpers
# Display fields, conflicts and identity.
# ==============================================================================

def candidate_display_fields(suggestion: dict) -> dict:
    """Extract review-safe candidate metadata for suggestion list/detail UIs.

    The source media title is kept separate from the provider candidate title so
    reviewers can see both without confusing a filename-derived title with a
    provider-identifed scene. No canonical entity rows are created here.
    """
    try:
        payload = json.loads(suggestion.get("payload") or "{}")
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    raw_people = payload.get("performer_details") or payload.get("performers") or []
    if isinstance(raw_people, str):
        raw_people = [raw_people]
    names = []
    seen = set()
    if isinstance(raw_people, (list, tuple, set)):
        for item in raw_people:
            name = str(item.get("name") or "").strip() if isinstance(item, dict) else str(item or "").strip()
            key = " ".join(name.casefold().split())
            if name and key not in seen:
                seen.add(key)
                names.append(name)

    provider_url = str(payload.get("profile") or payload.get("url") or "").strip()
    if not provider_url and isinstance(payload.get("links"), dict):
        links = payload["links"]
        provider_url = str(links.get("scene") or links.get("url") or "").strip()
    if not provider_url.lower().startswith(("https://", "http://")):
        provider_url = ""

    return {
        "candidate_title": str(payload.get("title") or "").strip() or None,
        "candidate_performers": names,
        "candidate_studio": str(payload.get("studio") or payload.get("site") or "").strip() or None,
        "provider_site": str(payload.get("site") or payload.get("studio") or "").strip() or None,
        "provider_site_url": str(payload.get("site_url") or payload.get("studio_url") or "").strip() or None,
        "website_variant_conflict": bool(payload.get("_archive_website_variant_conflict")),
        "release_date": str(payload.get("date") or payload.get("release_date") or payload.get("production_date") or "").strip() or None,
        "provider_record_id": str(suggestion.get("provider_record_id") or payload.get("id") or "").strip() or None,
        "provider_url": provider_url or None,
    }


def candidate_mapping_conflicts(db, video_id: int, candidate: dict, *,
                                mapping_context: dict | None = None,
                                fields: set[str] | None = None) -> list[str]:
    """Return known entity fields that explicitly conflict with a candidate.

    Missing provider fields are not treated as conflicts. A scene with several
    performers is compatible when at least one listed performer matches a known
    local performer; mapped studio aliases/provider IDs are accepted as well.
    """
    from ..engines.entity_identity import canonical_key, mapped_video_entity_context

    candidate = candidate if isinstance(candidate, dict) else {}
    context = mapping_context or mapped_video_entity_context(db, int(video_id))
    fields = {str(value).strip().lower() for value in fields} if fields is not None else {"performer", "studio"}
    conflicts = []

    def values(raw):
        if isinstance(raw, (list, tuple, set)):
            return list(raw)
        return [raw] if raw not in (None, "") else []

    def add_ids(target, source, raw_ids):
        source_key = str(source or "").strip().casefold()
        if not source_key:
            return
        for raw_id in values(raw_ids):
            value = str(raw_id or "").strip()
            if value:
                target.setdefault(source_key, set()).add(value)

    def id_maps_overlap(known, candidate_ids):
        return any(set(known.get(source, [])).intersection(ids)
                   for source, ids in candidate_ids.items())

    if "performer" in fields and context.get("performer_names"):
        known_names = {canonical_key(name) for name in context.get("performer_names") or []}
        provider = str(candidate.get("source") or candidate.get("provider") or "").strip()
        raw_people = candidate.get("performer_details") or candidate.get("performers") or []
        if isinstance(raw_people, (str, dict)):
            raw_people = [raw_people]
        candidate_names, candidate_ids = set(), {}
        for raw_person in raw_people if isinstance(raw_people, (list, tuple, set)) else []:
            person = raw_person if isinstance(raw_person, dict) else {"name": raw_person}
            name = str(person.get("name") or "").strip()
            if name:
                candidate_names.add(canonical_key(name))
            aliases = person.get("aliases") or []
            for alias in values(aliases):
                key = canonical_key(str(alias or ""))
                if key:
                    candidate_names.add(key)
            raw_provider_ids = person.get("provider_ids")
            if isinstance(raw_provider_ids, dict):
                for source, raw_id in raw_provider_ids.items():
                    add_ids(candidate_ids, source, raw_id)
            add_ids(candidate_ids, person.get("provider") or provider,
                    person.get("provider_id") or person.get("id"))
        has_clues = bool(candidate_names or candidate_ids)
        if has_clues and not (known_names.intersection(candidate_names)
                              or id_maps_overlap(context.get("performer_provider_ids") or {}, candidate_ids)):
            conflicts.append("performer")

    if "studio" in fields and context.get("studio_names"):
        known_names = {canonical_key(name) for name in context.get("studio_names") or []}
        candidate_names, candidate_ids = set(), {}
        for field in ("studio", "site"):
            raw_site = candidate.get(field)
            site_items = values(raw_site)
            for raw_item in site_items:
                item = raw_item if isinstance(raw_item, dict) else {"name": raw_item}
                name = str(item.get("name") or "").strip()
                if name:
                    candidate_names.add(canonical_key(name))
                for alias in values(item.get("aliases") or []):
                    key = canonical_key(str(alias or ""))
                    if key:
                        candidate_names.add(key)
        provider = str(candidate.get("source") or candidate.get("provider") or "").strip()
        provider_ids = candidate.get("studio_provider_ids")
        if isinstance(provider_ids, dict):
            for source, raw_id in provider_ids.items():
                add_ids(candidate_ids, source, raw_id)
        provenance = candidate.get("field_provenance") if isinstance(candidate.get("field_provenance"), dict) else {}
        studio_provider = str(provenance.get("studio_id") or provider)
        add_ids(candidate_ids, studio_provider, candidate.get("studio_id"))
        site_provider = str(provenance.get("site_id") or provider)
        add_ids(candidate_ids, site_provider, candidate.get("site_id"))
        has_clues = bool(candidate_names or candidate_ids)
        if has_clues and not (known_names.intersection(candidate_names)
                              or id_maps_overlap(context.get("studio_provider_ids") or {}, candidate_ids)):
            conflicts.append("studio")

    return conflicts



def _video_id(db, channel_id, message_id) -> int | None:
    with db.lock:
        row = db.conn.execute(
            "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
    return int(row["id"]) if row else None


def _candidate_identity(match: dict) -> tuple[str, str]:
    provider = str(match.get("source") or "unknown").strip() or "unknown"
    record_id = str(match.get("id") or "").strip()
    return provider, record_id or _fallback_candidate_record_id(provider, match)


def was_rejected_candidate(db, channel_id, message_id, match: dict) -> bool:
    """Whether this exact provider candidate was rejected for this local video."""
    video_id = _video_id(db, channel_id, message_id)
    if video_id is None:
        return False
    provider, record_id = _candidate_identity(match)
    with db.lock:
        row = db.conn.execute(
            """SELECT 1 FROM metadata_suggestions
               WHERE video_id=? AND provider=? AND provider_record_id=? AND status='REJECTED'""",
            (video_id, provider, record_id),
        ).fetchone()
    return row is not None


def _fallback_candidate_record_id(provider: str, match: dict) -> str:
    """Build a stable identity when a provider candidate has no record ID."""
    import hashlib
    import unicodedata

    def normalize(value) -> str:
        text = unicodedata.normalize("NFKC", str(value or ""))
        return " ".join(text.casefold().split())

    raw_people = match.get("performer_details") or match.get("performers") or []
    if isinstance(raw_people, (str, dict)):
        raw_people = [raw_people]
    people = set()
    for item in raw_people if isinstance(raw_people, (list, tuple, set)) else []:
        name = item.get("name") if isinstance(item, dict) else item
        normalized_name = normalize(name)
        if normalized_name:
            people.add(normalized_name)

    identity = {
        "provider": normalize(provider),
        "title": normalize(match.get("title")),
        "date": normalize(match.get("date") or match.get("release_date") or match.get("production_date")),
        "studio": normalize(match.get("studio") or match.get("site")),
        "performers": sorted(people),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "candidate:" + hashlib.sha256(encoded.encode("utf-8", "replace")).hexdigest()[:24]




# ==============================================================================
# SECTION: Saving and listing
# Stores and lists suggestions.
# ==============================================================================

def save_suggestion(db, channel_id, message_id, match: dict, confidence: str,
                     reason: str, score: float | None = None) -> int | None:
    """Save a scored candidate and associate it with relevant performer/studio pages.

    Candidates are provider evidence, not applied video metadata. Pending saves
    never create performer/studio rows: they associate only entities already in
    the local catalogue, while unknown candidate names stay in the payload and
    video picker. Canonical entities are created/upserted only after explicit
    confirmation. Suggestions already confirmed/rejected stay final.
    """
    video_id = _video_id(db, channel_id, message_id)
    if video_id is None:
        return None
    provider, record_id = _candidate_identity(match)

    raw_people = match.get("performer_details") or match.get("performers") or []
    people = []
    for item in raw_people:
        if isinstance(item, str):
            person = {"name": item}
        elif isinstance(item, dict):
            person = item
        else:
            continue
        name = " ".join(str(person.get("name") or "").split()).strip()
        if name and name.casefold() not in {x["name"].casefold() for x in people}:
            people.append({**person, "name": name})

    # Pending candidates are evidence only. Do not create canonical performer or
    # studio rows until the operator confirms the scene; otherwise every low-score
    # candidate can manufacture another copy of the same cast/studio.
    from ..engines.entity_identity import assess_performer_identity, _find_existing
    now = config.now_iso()
    payload = json.dumps(match, ensure_ascii=False)
    with db.lock:
        existing = db.conn.execute(
            "SELECT id, status FROM metadata_suggestions WHERE video_id=? AND provider=? AND provider_record_id=?",
            (video_id, provider, record_id),
        ).fetchone()
        if existing and existing["status"] == "REJECTED":
            return None
        if existing and existing["status"] == "CONFIRMED":
            return int(existing["id"])

        performer_ids: list[int] = []
        primary_performer_id = None
        primary_performer_name = None
        for index, person in enumerate(people):
            pname = person["name"]
            person_provider = str(person.get("provider") or provider).strip() or provider
            person_provider_id = person.get("id")
            assessment = assess_performer_identity(
                db, pname, provider=person_provider, provider_id=person_provider_id,
            )
            pid = int(assessment["id"]) if assessment.get("status") == "existing" and assessment.get("id") else None
            if index == 0:
                # Only expose a single suggested-performer ID when identity is
                # unambiguous. Ambiguous candidates keep their name/payload and
                # are linked to each possible existing record via the relation
                # table so review is visible from the affected performer cards.
                primary_performer_id, primary_performer_name = pid, pname
            candidate_ids = [pid] if pid else []
            if assessment.get("status") == "ambiguous":
                candidate_ids.extend(
                    int(candidate["id"]) for candidate in assessment.get("candidates") or []
                    if candidate.get("id") is not None
                )
            for candidate_id in candidate_ids:
                if candidate_id not in performer_ids:
                    performer_ids.append(candidate_id)

        studio_name = " ".join(str(match.get("studio") or match.get("site") or "").split()).strip()
        studio_ids: list[int] = []
        if studio_name:
            studio_provider_id = match.get("studio_id") or match.get("site_id")
            studio_provider = str((match.get("field_provenance") or {}).get("studio_id") or provider)
            provider_studio_ids = match.get("studio_provider_ids") if isinstance(match.get("studio_provider_ids"), dict) else {}
            if studio_provider_id and provider_studio_ids:
                studio_provider = next((str(src) for src, pid_value in provider_studio_ids.items()
                                         if str(pid_value or "").strip() == str(studio_provider_id)), studio_provider)
            found_studio = _find_existing(db, "studios", studio_name, studio_provider, studio_provider_id)
            if found_studio:
                studio_ids.append(int(found_studio["id"]))

        if existing:
            suggestion_id = int(existing["id"])
            db.conn.execute(
                """UPDATE metadata_suggestions SET suggested_performer_id=?, suggested_performer_name=?,
                   confidence=?, score=?, reason=?, payload=?, updated_at=? WHERE id=?""",
                (primary_performer_id, primary_performer_name, confidence, score, reason, payload, now, suggestion_id),
            )
        else:
            cur = db.conn.execute(
                """INSERT INTO metadata_suggestions(video_id,provider,provider_record_id,
                   suggested_performer_id,suggested_performer_name,confidence,score,reason,
                   payload,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'PENDING',?,?)""",
                (video_id, provider, record_id, primary_performer_id, primary_performer_name,
                 confidence, score, reason, payload, now, now),
            )
            suggestion_id = int(cur.lastrowid)

        # Refresh associations when a pending candidate is rescored. This lets
        # suggestions surface under every relevant already-known cast member and
        # under their provider-confirmed studio, not just the first performer.
        db.conn.execute("DELETE FROM metadata_suggestion_performers WHERE suggestion_id=?", (suggestion_id,))
        db.conn.execute("DELETE FROM metadata_suggestion_studios WHERE suggestion_id=?", (suggestion_id,))
        db.conn.executemany(
            "INSERT OR IGNORE INTO metadata_suggestion_performers(suggestion_id,performer_id) VALUES(?,?)",
            [(suggestion_id, pid) for pid in performer_ids],
        )
        db.conn.executemany(
            "INSERT OR IGNORE INTO metadata_suggestion_studios(suggestion_id,studio_id) VALUES(?,?)",
            [(suggestion_id, sid) for sid in studio_ids],
        )
        db.conn.commit()
        return suggestion_id


def list_for_video(db, video_id: int, status: str = "PENDING") -> list[dict]:
    with db.lock:
        rows = db.conn.execute(
            "SELECT * FROM metadata_suggestions WHERE video_id=? AND status=? ORDER BY score DESC, id",
            (video_id, status),
        ).fetchall()
    out = []
    context = None
    from ..engines.entity_identity import mapped_video_entity_context
    for row in rows:
        if context is None:
            context = mapped_video_entity_context(db, int(video_id))
        try:
            candidate = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            candidate = {}
        if not isinstance(candidate, dict):
            candidate = {}
        if candidate_mapping_conflicts(
            db, int(video_id), candidate, mapping_context=context,
        ):
            continue
        out.append(dict(row))
    return out


def list_for_performer(db, performer_id: int, status: str = "PENDING") -> list[dict]:
    with db.lock:
        rows = db.conn.execute(
            """SELECT DISTINCT ms.* FROM metadata_suggestions ms
               LEFT JOIN metadata_suggestion_performers msp ON msp.suggestion_id=ms.id
               WHERE ms.status=? AND (ms.suggested_performer_id=? OR msp.performer_id=?)
                 AND NOT EXISTS (
                     SELECT 1 FROM metadata_entity_suggestion_decisions d
                     WHERE d.video_id=ms.video_id AND d.entity_type='performer'
                       AND d.entity_id=? AND d.decision='REJECTED'
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM metadata_mapping_locks ml
                     WHERE ml.video_id=ms.video_id AND ml.field='performer' AND ml.locked=1
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM video_performers vp
                     WHERE vp.video_id=ms.video_id AND vp.performer_id=?
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM local_video_performers lvp
                     WHERE lvp.video_id=ms.video_id AND lvp.performer_id=?
                 )
               ORDER BY COALESCE(ms.score,0) DESC, ms.id DESC""",
            (status, int(performer_id), int(performer_id), int(performer_id),
             int(performer_id), int(performer_id)),
        ).fetchall()
    # Entity pages ask one relationship question per video, not one question
    # for every provider record. The highest-ranked candidate is representative;
    # the full provider candidate list remains available in Scene Match Review.
    out, seen_video_ids = [], set()
    for row in rows:
        video_id = int(row["video_id"])
        if video_id in seen_video_ids:
            continue
        seen_video_ids.add(video_id)
        out.append(dict(row))
    return out


def list_for_site(db, site_id: int, status: str = "PENDING", *, include_linked: bool = False) -> list[dict]:
    """Return pending metadata suggestions whose provider studio/site matches a local site.

    Suggestions pre-date site-level grouping, so the provider studio identity is kept in
    the JSON payload rather than a dedicated suggested_studio_id column. Match first on
    provider IDs stored on the local studio, then fall back to an exact normalized name.
    """
    import unicodedata

    def norm(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or "")).casefold()
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return " ".join(text.split())

    with db.lock:
        site = db.conn.execute(
            "SELECT id, name, provider_ids FROM studios WHERE id=?",
            (int(site_id),),
        ).fetchone()
        if not site:
            return []
        rows = db.conn.execute(
            "SELECT * FROM metadata_suggestions WHERE status=? ORDER BY score DESC, id",
            (status,),
        ).fetchall()
        associated_rows = db.conn.execute(
            """SELECT suggestion_id FROM metadata_suggestion_studios
               WHERE studio_id=?""", (int(site_id),),
        ).fetchall()
        associated_suggestion_ids = {int(r["suggestion_id"]) for r in associated_rows}
        rejected_video_ids = {
            int(r["video_id"]) for r in db.conn.execute(
                """SELECT video_id FROM metadata_entity_suggestion_decisions
                   WHERE entity_type='studio' AND entity_id=? AND decision='REJECTED'""",
                (int(site_id),),
            ).fetchall()
        }
        mapped_entity_video_ids = {
            int(r["video_id"]) for r in db.conn.execute(
                """SELECT video_id FROM metadata_mapping_locks
                   WHERE field='site' AND locked=1"""
            ).fetchall()
        }
        mapped_video_ids = set()
        if not include_linked:
            mapped_video_ids = {
                int(r["video_id"]) for r in db.conn.execute(
                    "SELECT video_id FROM video_studios WHERE studio_id=?",
                    (int(site_id),),
                ).fetchall()
            }

    try:
        site_provider_ids = json.loads(site["provider_ids"] or "{}")
    except Exception:
        site_provider_ids = {}
    if not isinstance(site_provider_ids, dict):
        site_provider_ids = {}

    site_name = norm(site["name"])
    out, seen_video_ids = [], set()
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        provider = str(row["provider"] or "").strip()
        candidate_id = str(payload.get("studio_id") or payload.get("site_id") or "").strip()
        candidate_name = norm(payload.get("studio") or payload.get("site") or "")
        matched = int(row["id"]) in associated_suggestion_ids
        if not matched and provider and candidate_id and str(site_provider_ids.get(provider) or "") == candidate_id:
            matched = True
        elif not matched and candidate_id and candidate_id in {str(v) for v in site_provider_ids.values() if v is not None}:
            matched = True
        elif not matched and site_name and candidate_name and site_name == candidate_name:
            matched = True
        video_id = int(row["video_id"])
        if (matched and video_id not in mapped_video_ids
                and video_id not in rejected_video_ids
                and video_id not in mapped_entity_video_ids
                and video_id not in seen_video_ids):
            seen_video_ids.add(video_id)
            out.append(dict(row))
    return out




# ==============================================================================
# SECTION: Confirm and reject
# Applies user decisions.
# ==============================================================================

def confirm(db, suggestion_id: int) -> bool:
    """Apply the stored provider match for real, then mark CONFIRMED.

    Creates/updates the video_performers relationship via the existing
    apply_scene_match -- never a duplicate video record, only relationships.
    Also advances identification_state to VERIFIED and feeds the canonical
    filename reference database, so this same scene resolves locally the
    next time a different filename of it turns up (see metadata/pipeline.py)."""
    from .providers import apply_scene_match
    from ..engines.entity_identity import performer_identity_conflicts, mapped_video_entity_context
    with db.lock:
        row = db.conn.execute("SELECT * FROM metadata_suggestions WHERE id=? AND status='PENDING'",
                               (suggestion_id,)).fetchone()
    if not row:
        return False
    with db.lock:
        vrow = db.conn.execute("SELECT source_channel, source_message_id, filename FROM media WHERE id=?",
                                (row["video_id"],)).fetchone()
    if not vrow:
        return False
    try:
        match = json.loads(row["payload"] or "{}")
    except Exception:
        match = {}
    # A confirmed scene may enrich a partially mapped video, but already-mapped
    # local performer/studio relationships remain authoritative. In particular,
    # StashDB and ThePornDB IDs must enrich one canonical performer where possible,
    # not trigger a second performer or require re-mapping existing cast.
    mapping_context = mapped_video_entity_context(db, int(row["video_id"]))
    if candidate_mapping_conflicts(
        db, int(row["video_id"]), match, mapping_context=mapping_context,
    ):
        return False
    if performer_identity_conflicts(db, match, ignore_names=mapping_context.get("performer_names")):
        return False
    channel_id, message_id = int(vrow["source_channel"]), int(vrow["source_message_id"])
    # The scene approval is explicit for missing fields. Do not remove a manual
    # mapping lock when that field already has a known local mapping.
    override_fields = set()
    if match.get("title"):
        override_fields.add("title")
    if (match.get("performers") or match.get("performer_details")) and not mapping_context.get("performer_names"):
        override_fields.add("performer")
    if (match.get("studio") or match.get("site")) and not mapping_context.get("has_known_studio"):
        override_fields.add("site")
    with db.lock:
        for field in override_fields:
            db.conn.execute("DELETE FROM metadata_mapping_locks WHERE video_id=? AND field=?", (int(row["video_id"]), field))
        db.conn.commit()
    ok = apply_scene_match(db, channel_id, message_id, match, preserve_existing_mappings=True)
    if not ok:
        return False
    with db.lock:
        db.conn.execute("UPDATE metadata_suggestions SET status='CONFIRMED', updated_at=? WHERE id=?",
                         (config.now_iso(), suggestion_id))
        db.conn.execute(
            "UPDATE media SET identification_state='VERIFIED' WHERE source_channel=? AND source_message_id=?",
            (str(channel_id), message_id),
        )
        db.conn.commit()
    # V29.4.132: a confirmed match is finished metadata (it was left UNKNOWN,
    # which kept the video out of the Library).
    try:
        from ..core.resource_registry import sync_message_state
        sync_message_state(db, channel_id, message_id, metadata="COMPLETE", provider="COMPLETE", reason="SUGGESTION_CONFIRMED")
    except Exception:
        pass
    try:
        from .pipeline import _upsert_reference_from_match
        _upsert_reference_from_match(db, vrow["filename"] or "", match, channel_id, message_id)
    except Exception:
        pass  # reference-DB population is best-effort; confirming the match itself must not fail on it
    return True


def reject(db, suggestion_id: int) -> bool:
    with db.lock:
        row = db.conn.execute("SELECT video_id FROM metadata_suggestions WHERE id=? AND status='PENDING'",
                               (suggestion_id,)).fetchone()
        if not row:
            return False
        cur = db.conn.execute("UPDATE metadata_suggestions SET status='REJECTED', updated_at=? WHERE id=? AND status='PENDING'",
                               (config.now_iso(), suggestion_id))
        db.conn.execute("UPDATE media SET identification_state='REJECTED' WHERE id=?", (row["video_id"],))
        db.conn.commit()
        return cur.rowcount > 0


def confirm_entity(db, suggestion_id: int, entity_type: str, entity_id: int) -> dict | None:
    """Confirm only one performer/studio relationship from an entity-page suggestion.

    This deliberately leaves the provider scene suggestion PENDING. Full scene
    metadata is still confirmed only through the complete Scene Match flow.
    """
    entity_type = str(entity_type or "").strip().lower()
    if entity_type not in {"performer", "studio"}:
        return None
    try:
        suggestion_id, entity_id = int(suggestion_id), int(entity_id)
    except (TypeError, ValueError):
        return None
    if suggestion_id < 1 or entity_id < 1:
        return None

    with db.lock:
        suggestion = db.conn.execute(
            """SELECT id, video_id, status, suggested_performer_id
               FROM metadata_suggestions WHERE id=?""",
            (suggestion_id,),
        ).fetchone()
        if not suggestion or suggestion["status"] != "PENDING":
            return None
        video_id = int(suggestion["video_id"])
        media = db.conn.execute(
            "SELECT id, site_name FROM media WHERE id=?",
            (video_id,),
        ).fetchone()
        if not media:
            return None
        already_linked = False

        if entity_type == "performer":
            entity = db.conn.execute(
                "SELECT id, name FROM performers WHERE id=?",
                (entity_id,),
            ).fetchone()
            association = db.conn.execute(
                """SELECT 1 FROM metadata_suggestion_performers
                   WHERE suggestion_id=? AND performer_id=?
                   UNION SELECT 1 WHERE ?=?""",
                (suggestion_id, entity_id, int(suggestion["suggested_performer_id"] or 0), entity_id),
            ).fetchone()
            if not entity or not association:
                return None
            rejected = db.conn.execute(
                """SELECT 1 FROM metadata_entity_suggestion_decisions
                   WHERE video_id=? AND entity_type='performer' AND entity_id=?
                     AND decision='REJECTED'""",
                (video_id, entity_id),
            ).fetchone()
            if rejected:
                return None
            existing_provider_link = db.conn.execute(
                "SELECT 1 FROM video_performers WHERE video_id=? AND performer_id=?",
                (video_id, entity_id),
            ).fetchone()
            existing_local_link = db.conn.execute(
                "SELECT 1 FROM local_video_performers WHERE video_id=? AND performer_id=?",
                (video_id, entity_id),
            ).fetchone()
            if existing_local_link:
                return {"video_id": video_id, "entity_type": entity_type,
                        "entity_id": entity_id, "entity_name": str(entity["name"] or ""),
                        "already_linked": True}
            old_names = [
                str(row["name"] or "") for row in db.conn.execute(
                    """SELECT DISTINCT p.name FROM performers p JOIN (
                           SELECT performer_id FROM video_performers WHERE video_id=?
                           UNION SELECT performer_id FROM local_video_performers WHERE video_id=?
                       ) linked ON linked.performer_id=p.id ORDER BY p.name COLLATE NOCASE""",
                    (video_id, video_id),
                ).fetchall()
            ]
            now = config.now_iso()
            db.conn.execute(
                "INSERT OR IGNORE INTO local_video_performers(video_id, performer_id, created_at) VALUES(?,?,?)",
                (video_id, entity_id, now),
            )
            new_names = sorted(set(old_names + [str(entity["name"] or "")]), key=str.casefold)
            field = "performer"
            new_value = ", ".join(name for name in new_names if name)
            entity_name = str(entity["name"] or "")
            already_linked = bool(existing_provider_link)
        else:
            entity = db.conn.execute(
                "SELECT id, name FROM studios WHERE id=?",
                (entity_id,),
            ).fetchone()
            if not entity:
                return None
            # Reuse the same provider-ID/name matching used to populate Studio
            # suggestions, so a caller cannot link an unrelated studio.
            if not any(
                int(row["id"]) == suggestion_id
                for row in list_for_site(db, entity_id, status="PENDING", include_linked=True)
            ):
                return None
            existing = db.conn.execute(
                "SELECT 1 FROM video_studios WHERE video_id=? AND studio_id=?",
                (video_id, entity_id),
            ).fetchone()
            other_studios = db.conn.execute(
                """SELECT s.id, s.name FROM video_studios vs JOIN studios s ON s.id=vs.studio_id
                   WHERE vs.video_id=? ORDER BY s.name COLLATE NOCASE""",
                (video_id,),
            ).fetchall()
            entity_name = str(entity["name"] or "")
            target_is_unknown = " ".join(entity_name.casefold().split()) in {"unknown", "unknown studio"}
            # Unknown Studio is a placeholder, not a genuine competing studio.
            # When the operator confirms a real studio on its suggestion page,
            # replace those placeholder rows; still refuse to overwrite a known
            # studio mapping without an explicit Edit Video operation.
            placeholder_studios = [
                row for row in other_studios
                if " ".join(str(row["name"] or "").casefold().split()) in {"unknown", "unknown studio"}
            ]
            placeholder_ids = {int(row["id"]) for row in placeholder_studios}
            known_other_studios = [row for row in other_studios if int(row["id"]) not in placeholder_ids]
            if known_other_studios and not existing:
                return {
                    "conflict": "already_linked_to_another_studio",
                    "existing_names": [str(row["name"] or "") for row in known_other_studios],
                    "entity_name": entity_name,
                }
            now = config.now_iso()
            removed_placeholder = False
            if placeholder_studios and not target_is_unknown:
                db.conn.executemany(
                    "DELETE FROM video_studios WHERE video_id=? AND studio_id=?",
                    [(video_id, int(row["id"])) for row in placeholder_studios],
                )
                removed_placeholder = True
            db.conn.execute(
                "INSERT OR IGNORE INTO video_studios(video_id, studio_id, confidence) VALUES(?,?,1.0)",
                (video_id, entity_id),
            )
            current_site_name = str(media["site_name"] or "")
            current_site_key = " ".join(current_site_name.casefold().split())
            if (not existing or removed_placeholder or not current_site_key
                    or current_site_key in {"unknown", "unknown studio"}):
                db.conn.execute(
                    "UPDATE media SET site_name=?, updated_at=? WHERE id=?",
                    (entity_name, now, video_id),
                )
            field = "site"
            new_value = entity_name
            already_linked = bool(existing) and not removed_placeholder

        db.conn.execute(
            "INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,reason,updated_at) VALUES(?,?,?,?,?)",
            (video_id, field, 1, f"confirmed {entity_type} suggestion", now),
        )
        db.conn.execute(
            "INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",
            (video_id, field, ", ".join(old_names) if entity_type == "performer" else str(media["site_name"] or ""),
             new_value, "suggestion_entity_confirmed", now),
        )
        db.conn.commit()
        return {"video_id": video_id, "entity_type": entity_type,
                "entity_id": entity_id, "entity_name": entity_name,
                "already_linked": bool(already_linked)}


def reject_entity(db, suggestion_id: int, entity_type: str, entity_id: int) -> dict | None:
    """Reject only an entity/video relationship, leaving the scene candidate pending.

    Entity pages ask whether a video belongs to one local performer or studio.
    A "no" answer must not discard provider evidence needed for later Scene Match
    Review, so this decision is stored separately from metadata_suggestions.status.
    """
    entity_type = str(entity_type or "").strip().lower()
    if entity_type not in {"performer", "studio"}:
        return None
    try:
        suggestion_id, entity_id = int(suggestion_id), int(entity_id)
    except (TypeError, ValueError):
        return None
    if suggestion_id < 1 or entity_id < 1:
        return None

    # Only allow rejecting a relationship that is currently offered on that
    # entity page. This also enforces one visible question per video/entity.
    if entity_type == "performer":
        visible = list_for_performer(db, entity_id, status="PENDING")
    else:
        visible = list_for_site(db, entity_id, status="PENDING")
    if not any(int(row.get("id") or 0) == suggestion_id for row in visible):
        return None

    with db.lock:
        suggestion = db.conn.execute(
            """SELECT ms.video_id, e.name AS entity_name
               FROM metadata_suggestions ms
               JOIN performers e ON e.id=?
               WHERE ms.id=? AND ms.status='PENDING'"""
            if entity_type == "performer" else
            """SELECT ms.video_id, e.name AS entity_name
               FROM metadata_suggestions ms
               JOIN studios e ON e.id=?
               WHERE ms.id=? AND ms.status='PENDING'""",
            (entity_id, suggestion_id),
        ).fetchone()
        if not suggestion:
            return None
        video_id = int(suggestion["video_id"])
        now = config.now_iso()
        db.conn.execute(
            """INSERT OR REPLACE INTO metadata_entity_suggestion_decisions
               (video_id,entity_type,entity_id,decision,created_at)
               VALUES(?,?,?,'REJECTED',?)""",
            (video_id, entity_type, entity_id, now),
        )
        db.conn.execute(
            """INSERT INTO metadata_mapping_history
               (video_id,field,old_value,new_value,action,created_at)
               VALUES(?,?,?,?,?,?)""",
            (video_id, "performer" if entity_type == "performer" else "site",
             "", str(suggestion["entity_name"] or ""), "suggestion_entity_rejected", now),
        )
        db.conn.commit()
        return {"video_id": video_id, "entity_type": entity_type,
                "entity_id": entity_id, "entity_name": str(suggestion["entity_name"] or ""),
                "decision": "REJECTED"}
