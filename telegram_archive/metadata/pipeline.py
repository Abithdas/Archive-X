"""One identification pipeline for a single video, used by both the bulk
Metadata match job (engines/metadata_sync.py) and the background page
enricher (engines/page_enricher.py) -- this logic exists in exactly one
place instead of two, per the "no duplicate implementations" rule.

Order: for an unresolved video, query configured providers by exact OSHASH,
then search with parsed filename/date/cast/studio clues. The local canonical
reference is a fallback only when providers yield no candidate. Every video
already MATCHED/VERIFIED is skipped unless force=True (section 12).
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Local media identification
#   - Identification state
#   - Applying matches
#   - Core identification
#   - Single-flight coordination
#   - Filename entity reconciliation
#   - Public entry point
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.resource_registry import sync_message_state

from datetime import datetime, timezone
import asyncio
import inspect
import json
import re

from .parser import parse_scene_name
from .reference import build_canonical_key, lookup_reference, reference_to_provider_match, upsert_reference
from .matching import identify_with_confidence
from .providers import apply_scene_match, collect_markers_for_message
from .suggestions import save_suggestion, was_rejected_candidate




# ==============================================================================
# SECTION: Local media identification
# Identifies a local file.
# ==============================================================================

async def identify_local_media(db, *, filename: str, oshash: str | None = None) -> dict:
    """Identify a local file without requiring a Telegram catalogue identity.

    Configured providers get the first opportunity: exact file OSHASH, then
    filename/date/cast/studio clues. A saved local reference is a fallback only
    when provider matching produced no candidate; it never masks an exact
    fingerprint match. This function does not mutate a media row.
    """
    outcome = await identify_with_confidence(db, oshash=oshash, filename=filename)
    if outcome.get("best") is not None or outcome.get("matches"):
        return {"source": "provider", "confidence": outcome.get("confidence"),
                "method": outcome.get("reason"), "matches": outcome.get("matches") or [],
                "best": outcome.get("best"), "errors": outcome.get("errors") or [],
                "provider_searches": outcome.get("provider_searches") or {}}
    ref = lookup_reference(db, filename)
    if ref and ref.get("confidence") in ("HIGH", "MEDIUM"):
        match = reference_to_provider_match(ref)
        return {"source": "reference", "confidence": ref.get("confidence"),
                "method": ref.get("method"), "matches": [match], "best": match,
                "errors": outcome.get("errors") or []}
    return {"source": "provider", "confidence": outcome.get("confidence"),
            "method": outcome.get("reason"), "matches": outcome.get("matches") or [],
            "best": outcome.get("best"), "errors": outcome.get("errors") or [],
            "provider_searches": outcome.get("provider_searches") or {}}



class _LocalStagingSkip(Exception):
    """Internal marker: local staging items have no Telegram frame folder yet."""



# ==============================================================================
# SECTION: Identification state
# Reads and writes per-video state.
# ==============================================================================

def get_identification_state(db, channel_id, message_id) -> str | None:
    with db.lock:
        row = db.conn.execute(
            "SELECT identification_state FROM media WHERE source_channel=? AND source_message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
    return row["identification_state"] if row else None


def metadata_result_is_current(db, channel_id, message_id, *, max_age_days: int = 30) -> tuple[bool, str]:
    """Share Metadata Sync's reuse policy with Content Automation's local pipeline.

    MATCHED/VERIFIED results stay final until explicitly forced. A no-match or
    suggestion is reusable for the same 30-day window used by bulk Metadata Sync;
    provider errors don't advance metadata_synced_at and therefore remain retryable.
    """
    with db.lock:
        row = db.conn.execute(
            "SELECT identification_state,metadata_synced_at,metadata_source FROM media "
            "WHERE source_channel=? AND source_message_id=? LIMIT 1",
            (str(channel_id), int(message_id)),
        ).fetchone()
    if not row:
        return False, "media_record_missing"
    state = str(row["identification_state"] or "").strip().upper()
    if state in {"MATCHED", "VERIFIED"}:
        return True, "already_identified"
    raw = str(row["metadata_synced_at"] or "").strip()
    if not raw:
        return False, "never_synced"
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return False, "invalid_sync_timestamp"
    if age_seconds <= max(1, int(max_age_days)) * 86400:
        source = str(row["metadata_source"] or "unknown").strip().lower() or "unknown"
        return True, f"recent_result:{source}"
    return False, "metadata_result_expired"


def set_identification_state(db, channel_id, message_id, state: str) -> None:
    with db.lock:
        db.conn.execute(
            "UPDATE media SET identification_state=? WHERE source_channel=? AND source_message_id=?",
            (state, str(channel_id), int(message_id)),
        )
        db.conn.commit()


def _mark_none(db, channel_id, message_id) -> None:
    with db.lock:
        db.conn.execute(
            "UPDATE media SET metadata_source='none',metadata_synced_at=? "
            "WHERE source_channel=? AND source_message_id=?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), str(channel_id), int(message_id)),
        )
        db.conn.commit()


def _mark_suggested(db, channel_id, message_id) -> None:
    with db.lock:
        db.conn.execute(
            "UPDATE media SET metadata_source='suggested',metadata_synced_at=? "
            "WHERE source_channel=? AND source_message_id=?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), str(channel_id), int(message_id)),
        )
        db.conn.commit()


def _suggestion_is_finished_metadata(db, channel_id, message_id, reason, external_access=None) -> None:
    """V29.4.132: a suggestion finishes the metadata step for the Library.

    The video keeps its Telegram title and stays "needs review" (identification
    PARTIAL, metadata_source 'suggested') until the operator confirms or
    rejects the suggestion. Before, the metadata step was left UNKNOWN, which
    kept every suggested video out of the Library, and metadata_synced_at kept
    it from being looked at again for 30 days.
    """
    sync_message_state(db, channel_id, message_id, metadata="COMPLETE", provider="COMPLETE",
                       reason=reason, external_access=external_access)




# ==============================================================================
# SECTION: Applying matches
# Stores references and locks.
# ==============================================================================

def _upsert_reference_from_match(db, filename, match, channel_id, message_id) -> None:
    """Populate the reference DB from a match that was just applied, so the
    NEXT filename variant of this same scene resolves locally (section 1)."""
    hints = parse_scene_name(filename)
    key = build_canonical_key(hints)
    if not key:
        return
    upsert_reference(
        db, canonical_key=key, filename=filename, title=match.get("title"),
        performer_names=match.get("performers") or [], studio_name=match.get("studio"),
        site_name=match.get("site"), release_date=match.get("date"),
        provider=match.get("source"), provider_record_id=match.get("id"),
        provider_url=match.get("profile"), channel_id=channel_id, message_id=message_id,
    )


def _mapping_locks(db, channel_id: int, message_id: int) -> set[str]:
    """Return explicit operator mapping locks for one video."""
    with db.lock:
        row = db.conn.execute(
            "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
        if not row:
            return set()
        rows = db.conn.execute(
            "SELECT field FROM metadata_mapping_locks WHERE video_id=? AND locked=1",
            (int(row["id"]),),
        ).fetchall()
    return {str(r["field"] or "").strip().lower() for r in rows if str(r["field"] or "").strip()}




# ==============================================================================
# SECTION: Core identification
# Context building and the core flow.
# ==============================================================================

def _identification_context(db, channel_id, message_id) -> dict:
    """Load all reliable local clues for provider matching without mutating metadata.

    A video may have only a title/date, only an existing performer or studio link,
    or any combination. Unknown placeholders and upload dates are deliberately not
    treated as identity evidence. Local aliases are passed separately so they help
    match without artificially increasing the number of independent clues.
    """
    try:
        with db.lock:
            row = db.conn.execute(
                "SELECT * FROM media WHERE source_channel=? AND source_message_id=?",
                (str(channel_id), int(message_id)),
            ).fetchone()
            if not row:
                return {}
            media = dict(row)
            media_id = int(row["id"])
            performer_rows = db.conn.execute(
                """SELECT p.name, p.aliases FROM video_performers vp JOIN performers p ON p.id=vp.performer_id
                   WHERE vp.video_id=? UNION SELECT p.name, p.aliases FROM local_video_performers lvp
                   JOIN performers p ON p.id=lvp.performer_id WHERE lvp.video_id=?""",
                (media_id, media_id),
            ).fetchall()
            studio_rows = db.conn.execute(
                """SELECT s.name, s.aliases FROM video_studios vs JOIN studios s ON s.id=vs.studio_id
                   WHERE vs.video_id=?""", (media_id,),
            ).fetchall()
    except Exception:
        # Older/embedded DB test doubles may not expose the entire catalogue schema.
        return {}

    def usable_name(value, kind):
        name = " ".join(str(value or "").split()).strip()
        folded = re.sub(r"[^a-z0-9]+", "", name.casefold())
        if not name or folded in {"unknown", "none", "null", "na", "unknownperformer", "unknownstudio"}:
            return ""
        if kind == "performer" and folded.startswith("unknownperformer"):
            return ""
        if kind == "studio" and folded.startswith("unknownstudio"):
            return ""
        return name

    def decode_aliases(value):
        try:
            raw = json.loads(value or "[]")
        except Exception:
            raw = []
        if not isinstance(raw, list):
            return []
        return [" ".join(str(x or "").split()).strip() for x in raw if str(x or "").strip()]

    performers = []
    performer_aliases = []
    for entity in performer_rows:
        name = usable_name(entity["name"], "performer")
        if not name:
            continue
        performers.append(name)
        performer_aliases.extend(x for x in decode_aliases(entity["aliases"])
                                 if usable_name(x, "performer"))
    studios = []
    studio_aliases = []
    for entity in studio_rows:
        name = usable_name(entity["name"], "studio")
        if not name:
            continue
        studios.append(name)
        studio_aliases.extend(x for x in decode_aliases(entity["aliases"])
                              if usable_name(x, "studio"))
    site_name = usable_name(media.get("site_name"), "studio")
    if site_name:
        studios.append(site_name)
    titles = []
    filename_label = str(media.get("filename") or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    def label_key(value):
        text = str(value or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        text = re.sub(r"(?i)\.(mp4|mkv|mov|avi|m4v|webm|wmv|flv|ts)$", "", text)
        return re.sub(r"[^a-z0-9]+", "", text.casefold())
    filename_key = label_key(filename_label)
    for value in (media.get("title"), media.get("caption")):
        clean = " ".join(str(value or "").split()).strip()
        if clean and label_key(clean) != filename_key and clean.casefold() not in {x.casefold() for x in titles}:
            titles.append(clean[:300])
    dates = []
    for value in (media.get("release_date"), media.get("production_date"), media.get("date"), media.get("caption")):
        text = str(value or "")
        for match in re.findall(r"(?<!\d)((?:19|20)\d{2}[-./]\d{1,2}[-./]\d{1,2})(?!\d)", text):
            normalized = match.replace("/", "-").replace(".", "-")
            parts = normalized.split("-")
            try:
                normalized = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            except (ValueError, IndexError):
                continue
            if normalized not in dates:
                dates.append(normalized)
        # Release date fields can contain a date without punctuation in some imports.
        if value and re.fullmatch(r"(?:19|20)\d{6}", text.strip()):
            normalized = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
            if normalized not in dates:
                dates.append(normalized)
    context = {
        "title": media.get("title") or "",
        "titles": titles,
        "date": media.get("release_date") or "",
        "production_date": media.get("production_date") or "",
        "dates": dates[:5],
        "code": media.get("scene_code") or media.get("code") or "",
        "performers": list(dict.fromkeys(performers)),
        "performer_aliases": list(dict.fromkeys(performer_aliases))[:20],
        "studios": list(dict.fromkeys(studios)),
        "studio_aliases": list(dict.fromkeys(studio_aliases))[:12],
        "duration": media.get("duration"),
        "video_phash": media.get("video_phash"),
        "source_channel": str(media.get("source_channel") or channel_id),
        "source_message_id": int(media.get("source_message_id") or message_id),
    }
    return context


def _identify_with_context_compat(db, *, oshash, filename, context, hash_only: bool = False, visual_phash: str | None = None):
    """Pass context to the real matcher; keep older narrow test/provider adapters compatible."""
    try:
        signature = inspect.signature(identify_with_confidence)
        accepts_context = "context" in signature.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
        )
        accepts_hash_only = "hash_only" in signature.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
        )
        accepts_visual_phash = "visual_phash" in signature.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
        )
    except (TypeError, ValueError):
        accepts_context = True
        accepts_hash_only = True
        accepts_visual_phash = True
    kwargs = {"db": db, "oshash": oshash, "filename": filename}
    if accepts_context:
        kwargs["context"] = context
    if hash_only and accepts_hash_only:
        kwargs["hash_only"] = True
    if visual_phash and accepts_visual_phash:
        kwargs["visual_phash"] = visual_phash
    return identify_with_confidence(**kwargs)


def _filename_performer_conflict(filename: str, best: dict, outcome: dict, context: dict, db=None) -> bool:
    """Only block when an explicit performer named by the filename is absent from provider cast.

    Provider cast supersets are valid: scenes often list male co-stars or other
    participants that were not encoded in the release filename.
    """
    from difflib import SequenceMatcher
    from .filename_matching import extract_filename_parameters
    try:
        # Use catalog names/aliases solely to detect names literally present in
        # the filename. Do not pass the video's already-linked performer context,
        # which would turn an existing cast into a false filename conflict.
        hints = extract_filename_parameters(db, filename, {})
    except Exception:
        hints = {}
    raw_clues = []
    for raw in (hints.get("performers") or [], hints.get("performer_candidates") or []):
        raw_clues.extend([raw] if isinstance(raw, str) else list(raw) if isinstance(raw, (tuple, list, set)) else [])
    clues = list(dict.fromkeys(str(value or "").strip() for value in raw_clues if str(value or "").strip()))
    cast_raw = best.get("performer_details") or best.get("performers") or []
    cast = []
    for item in cast_raw:
        if isinstance(item, dict):
            cast.extend([str(item.get("name") or "").strip()])
            aliases = item.get("aliases") or []
            if isinstance(aliases, str): aliases = [aliases]
            cast.extend(str(alias or "").strip() for alias in aliases if str(alias or "").strip())
        else:
            cast.append(str(item or "").strip())
    cast = [item for item in cast if item]
    if not clues or not cast:
        return False
    def norm(value):
        return "".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
    normalized_cast = [norm(person) for person in cast if norm(person)]
    for clue in clues:
        key = norm(clue)
        if not key:
            continue
        if not any(key == name or SequenceMatcher(None, key, name).ratio() >= 0.90
                   or (min(len(key), len(name)) >= 8 and SequenceMatcher(None, key, name).ratio() >= 0.94)
                   for name in normalized_cast):
            return True
    return False


async def _identify_video_core(db, *, channel_id, message_id, filename, oshash=None, force=False,
                               suggest_only=False, preflight_result: dict | None = None,
                               visual_scan: bool = False) -> dict:
    """Identify one video.

    ``suggest_only`` is used by the manual channel suggestion scanner: even a
    HIGH-confidence result is written as a pending suggestion instead of being
    auto-applied. Normal metadata sync/recheck callers keep the default False.

    Never re-processes a MATCHED/VERIFIED video unless force=True. Checks
    configured providers by exact file fingerprint first, then filename clues;
    the local reference database is a fallback when providers return no candidate."""
    state = get_identification_state(db, channel_id, message_id)
    locks = _mapping_locks(db, channel_id, message_id)
    # A normal sync skips already-identified videos, but the explicit
    # suggestion scanner may still inspect a manually mapped/locked video so
    # the operator can review a better provider candidate and confirm it.
    if not force and state in ("MATCHED", "VERIFIED") and not (suggest_only and locks):
        return {"action": "skipped", "state": state}

    # Manual video mappings are protected during normal sync. Provider results
    # are still allowed to become PENDING suggestions; they do not change the
    # video until an operator explicitly confirms the suggestion.
    protected_fields = set(locks)
    from ..engines.entity_identity import performer_identity_conflicts

    # Level 6: use the title/date/cast/studio clues already stored in Archive X.
    # Providers are queried sequentially; a confirmed/ambiguous performer identity
    # must never cause an automatic duplicate performer row to be created.
    context = _identification_context(db, channel_id, message_id)
    if isinstance(preflight_result, dict) and preflight_result.get("preflight_complete"):
        # The slow filename search already ran while this torrent downloaded.
        # First make a cheap exact-file identity pass on the completed bytes.
        # If the hash has no scene result, reuse the pre-download scored candidates
        # rather than issuing the same title/studio/performer clue searches twice.
        hash_outcome = await _identify_with_context_compat(
            db, oshash=oshash, filename=filename, context=context, hash_only=True,
        )
        if hash_outcome.get("best") is not None or hash_outcome.get("matches"):
            outcome = hash_outcome
        elif ((preflight_result.get("best") is not None or preflight_result.get("matches"))
              and not preflight_result.get("errors") and not hash_outcome.get("errors")):
            outcome = dict(preflight_result)
            outcome["reason"] = str(outcome.get("reason") or "pre-download filename identity candidates") + "; exact file hash had no provider match"
        elif not preflight_result.get("errors") and not hash_outcome.get("errors"):
            outcome = dict(preflight_result)
            outcome["reason"] = str(outcome.get("reason") or "pre-download filename identity preflight found no candidate") + "; exact file hash had no provider match"
        else:
            # A transient failure during preflight is not evidence of no match.
            # Retry the normal ordered provider flow now that upload is pending.
            outcome = await _identify_with_context_compat(
                db, oshash=oshash, filename=filename, context=context,
            )
    else:
        outcome = await _identify_with_context_compat(db, oshash=oshash, filename=filename, context=context)

    # PHASH is computed and queried only as part of an explicit Metadata & Assets
    # scan. Existing validated 36-frame images are local inputs; no Telegram media
    # is requested by this helper. OSHASH and filename matching always run first.
    if visual_scan:
        frames_dir = None
        visual_hash = str(context.get("video_phash") or "").strip().lower()
        # V29.4.223: local staging namespaces ("local_upload", "content_automation")
        # have no Telegram frame folder yet; skip quietly instead of a ValueError warning.
        local_namespace = not str(channel_id).lstrip("-").isdigit()
        try:
            if local_namespace:
                raise _LocalStagingSkip()
            from ..engines.preview_prefetch import preview_frames_path
            frames_dir = preview_frames_path(int(channel_id), int(message_id))
            if not visual_hash and frames_dir.is_dir():
                from .perceptual import stash_video_phash_from_snapshots
                visual_hash = await asyncio.to_thread(
                    stash_video_phash_from_snapshots, frames_dir, float(context.get("duration") or 0),
                ) or ""
                if visual_hash:
                    with db.lock:
                        db.conn.execute(
                            "UPDATE media SET video_phash=? WHERE source_channel=? AND source_message_id=?",
                            (visual_hash, str(channel_id), int(message_id)),
                        )
                        db.conn.commit()
                    context["video_phash"] = visual_hash
        except _LocalStagingSkip:
            pass
        except Exception as exc:
            try:
                db.log(f"Visual scene fingerprint unavailable for {channel_id}/{message_id}: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                pass
        if visual_hash and outcome.get("confidence") != "HIGH":
            visual_context = dict(context)
            if frames_dir and frames_dir.is_dir():
                visual_context["_allow_provider_image_fetch"] = True
                visual_context["_frames_dir"] = str(frames_dir)
            try:
                visual_outcome = await _identify_with_context_compat(
                    db, oshash=oshash, filename=filename, context=visual_context,
                    visual_phash=visual_hash,
                )
                old_best = outcome.get("best")
                new_best = visual_outcome.get("best")
                old_score = float(outcome.get("score") or 0.0)
                new_score = float(visual_outcome.get("score") or 0.0)
                old_evidence = (old_best or {}).get("_archive_match") or {}
                new_evidence = (new_best or {}).get("_archive_match") or {}
                # Prefer a confident visual result, otherwise prefer candidates
                # improved by PHASH or snapshot-vs-provider still comparison.
                if new_best and (
                    visual_outcome.get("confidence") == "HIGH"
                    or new_score > old_score
                    or float(new_evidence.get("visual_similarity") or 0) > float(old_evidence.get("visual_similarity") or 0)
                    or (not old_best and new_best)
                ):
                    outcome = visual_outcome
                elif new_best and old_best:
                    # Keep the combined candidate pool for review while retaining
                    # the better ranked original candidate as the primary result.
                    known = { (str(x.get("source") or ""), str(x.get("id") or "")) for x in (outcome.get("matches") or []) }
                    merged_pool = list(outcome.get("matches") or [])
                    merged_pool.extend(x for x in (visual_outcome.get("matches") or [])
                                       if (str(x.get("source") or ""), str(x.get("id") or "")) not in known)
                    outcome["matches"] = merged_pool[:12]
                    outcome["reason"] = str(outcome.get("reason") or "Filename match needs review") + "; visual fallback also checked"
            except Exception as exc:
                try:
                    db.log(f"Visual scene lookup failed for {channel_id}/{message_id}: {type(exc).__name__}: {exc}", "WARN")
                except Exception:
                    pass

    confidence, best = outcome["confidence"], outcome["best"]
    score = float(outcome.get("score") or 0.0)
    if best is not None:
        identity_conflicts = performer_identity_conflicts(db, best)
        filename_cast_conflict = _filename_performer_conflict(filename, best, outcome, context, db=db)
        if identity_conflicts or filename_cast_conflict:
            doubtful_names = ", ".join(dict.fromkeys(str(item.get("name") or "") for item in identity_conflicts if item.get("name")))
            identity_reason = (f"Performer identity needs review ({doubtful_names or 'ambiguous cast identity'}); no duplicate performer records were created."
                               if identity_conflicts else
                               "Provider cast conflicts with the explicit filename/local performer clues; automatic application is blocked.")
            if confidence == "HIGH":
                confidence = "MEDIUM"
            best["_archive_confidence"] = confidence
            best["_archive_reason"] = identity_reason
            outcome["reason"] = identity_reason
            outcome["performer_identity_conflicts"] = identity_conflicts
            outcome["filename_performer_conflict"] = filename_cast_conflict
    if best is not None and confidence != "HIGH":
        evidence = best.get("_archive_match") if isinstance(best.get("_archive_match"), dict) else {}
        reason_text = str(best.get("_archive_reason") or outcome.get("reason") or "Candidate requires review")
        try:
            db.log(
                f"Scene match review {channel_id}/{message_id}: {reason_text}", "INFO",
                event="metadata.scene_match_review",
                details={"channel_id": str(channel_id), "message_id": int(message_id),
                         "confidence": confidence, "method": outcome.get("method"),
                         "score": score, "signals": evidence.get("evidence") or [],
                         "dimension_scores": evidence.get("dimension_scores") or {},
                         "reason": reason_text},
            )
        except Exception:
            pass
    if (confidence == "HIGH" and best is not None and not suggest_only
            and not was_rejected_candidate(db, channel_id, message_id, best)):
        if apply_scene_match(db, channel_id, message_id, best, oshash=oshash):
            await collect_markers_for_message(db, channel_id, message_id)
            set_identification_state(db, channel_id, message_id, "MATCHED")
            sync_message_state(db, channel_id, message_id, metadata="COMPLETE", provider="COMPLETE", reason="PROVIDER_REQUIRED", external_access="PROVIDER")
            _upsert_reference_from_match(db, filename, best, channel_id, message_id)
            return {"action": "applied", "confidence": "HIGH", "source": "provider", "best": best}

    matches = list(outcome.get("matches") or [])
    if not matches and best is not None:
        matches = [best]
    # Persist several scored alternatives, not just the winner, so candidates
    # remain reviewable in performer and studio suggestion panels.
    saved = 0
    if confidence in ("HIGH", "MEDIUM", "LOW") and matches:
        for candidate in matches[:8]:
            candidate_confidence = str(candidate.get("_archive_confidence") or confidence)
            candidate_reason = str(candidate.get("_archive_reason") or outcome.get("reason") or "Provider candidate needs review")
            candidate_conflicts = performer_identity_conflicts(db, candidate)
            if candidate_conflicts:
                names = ", ".join(dict.fromkeys(str(item.get("name") or "") for item in candidate_conflicts if item.get("name")))
                if candidate_confidence == "HIGH":
                    candidate_confidence = "MEDIUM"
                candidate_reason += f"; performer identity needs review ({names or 'ambiguous cast identity'}); no duplicate performer records created"
            if protected_fields:
                candidate_reason += "; protected manual mapping — confirmation required"
            evidence = candidate.get("_archive_match") if isinstance(candidate.get("_archive_match"), dict) else {}
            candidate_score = evidence.get("score")
            if candidate_score is None:
                candidate_score = score if candidate is best else None
            suggestion_id = save_suggestion(
                db, channel_id, message_id, candidate, candidate_confidence,
                candidate_reason, score=float(candidate_score) if candidate_score is not None else None,
            )
            if suggestion_id is not None:
                # The provider confidence is not applied to the video's metadata here.
                saved += 1
        if saved:
            set_identification_state(db, channel_id, message_id, "PARTIAL")
            _mark_suggested(db, channel_id, message_id)
            _suggestion_is_finished_metadata(db, channel_id, message_id, "SUGGESTION_PENDING_REVIEW", external_access="PROVIDER")
            return {"action": "suggested", "confidence": confidence, "source": "provider", "best": best,
                    "matches": matches[:8], "suggestion_count": saved, "score": score,
                    "performer_identity_conflicts": outcome.get("performer_identity_conflicts") or []}

    # Local canonical references are a fallback after provider OSHASH and filename lookup.
    ref = lookup_reference(db, filename)
    if ref and ref["confidence"] in ("HIGH", "MEDIUM"):
        match = reference_to_provider_match(ref)
        identity_conflicts = performer_identity_conflicts(db, match)
        identity_reason = ""
        if identity_conflicts:
            doubtful_names = ", ".join(dict.fromkeys(str(item.get("name") or "") for item in identity_conflicts if item.get("name")))
            identity_reason = f"; performer identity needs review ({doubtful_names or 'ambiguous cast identity'}); no new performer records created"
        if (ref["confidence"] == "HIGH" and not suggest_only and not identity_conflicts
                and not was_rejected_candidate(db, channel_id, message_id, match)):
            if apply_scene_match(db, channel_id, message_id, match):
                await collect_markers_for_message(db, channel_id, message_id)
                set_identification_state(db, channel_id, message_id, "MATCHED")
                sync_message_state(db, channel_id, message_id, metadata="COMPLETE", provider="COMPLETE", reason="LOCAL_REFERENCE_HIT")
                _upsert_reference_from_match(db, filename, match, channel_id, message_id)
                return {"action": "applied", "confidence": "HIGH", "method": ref["method"], "source": "reference", "best": match}
        if ref["confidence"] == "HIGH" or ref["confidence"] == "MEDIUM":
            confidence = "MEDIUM" if identity_conflicts else ("HIGH" if ref["confidence"] == "HIGH" else "MEDIUM")
            reason = f"local reference match ({ref['method']})" + identity_reason + ("; protected manual mapping — confirmation required" if protected_fields else ("; manual suggestion scan" if suggest_only else ""))
            match["_archive_confidence"] = confidence
            match["_archive_reason"] = reason
            suggestion_id = save_suggestion(
                db, channel_id, message_id, match, confidence, reason,
                score=0.75 if identity_conflicts or confidence != "HIGH" else 1.0,
            )
            if suggestion_id is not None:
                set_identification_state(db, channel_id, message_id, "PARTIAL")
                _mark_suggested(db, channel_id, message_id)
                _suggestion_is_finished_metadata(db, channel_id, message_id, "SUGGESTION_PENDING_REVIEW")
                return {"action": "suggested", "confidence": confidence, "method": ref["method"], "source": "reference", "best": match,
                        "matches": [match], "performer_identity_conflicts": identity_conflicts}

    # Provider errors are not evidence that the scene does not exist. Keep the
    # video unresolved and retryable rather than marking a permanent no-match.
    errors = outcome.get("errors") or []
    if errors:
        try:
            db.log(f"Scene match review {channel_id}/{message_id}: provider lookup inconclusive; errors={'; '.join(map(str, errors))[:500]}; signals={outcome.get('searched_clues') or []}", "WARN", event="metadata.scene_match_review",
                   details={"channel_id": str(channel_id), "message_id": int(message_id), "result": "provider_error", "errors": errors[:8], "searched_clues": outcome.get("searched_clues") or [], "searched_dates": outcome.get("searched_dates") or []})
        except Exception:
            pass
        set_identification_state(db, channel_id, message_id, "PARTIAL")
        sync_message_state(db, channel_id, message_id, metadata="FAILED", provider="FAILED",
                           reason="PROVIDER_LOOKUP_ERROR", external_access="PROVIDER")
        return {"action": "provider_error", "errors": errors, "source": "provider"}

    try:
        db.log(f"Scene match review {channel_id}/{message_id}: no candidate; searched clues={outcome.get('searched_clues') or []}; dates={outcome.get('searched_dates') or []}; year={outcome.get('searched_year')}", "INFO", event="metadata.scene_match_review",
               details={"channel_id": str(channel_id), "message_id": int(message_id), "result": "no_match", "searched_clues": outcome.get("searched_clues") or [], "searched_dates": outcome.get("searched_dates") or [], "searched_year": outcome.get("searched_year"), "provider_searches": outcome.get("provider_searches") or {}})
    except Exception:
        pass
    _mark_none(db, channel_id, message_id)
    sync_message_state(db, channel_id, message_id, metadata="COMPLETE", provider="COMPLETE", reason="PROVIDER_REQUIRED", external_access="PROVIDER")
    return {"action": "no_match", "searched_clues": outcome.get("searched_clues") or [],
            "searched_dates": outcome.get("searched_dates") or [], "searched_year": outcome.get("searched_year"),
            "provider_searches": outcome.get("provider_searches") or {}}


async def _identify_video_impl(db, *, channel_id, message_id, filename, oshash=None, force=False,
                               suggest_only=False, reconcile_filename_entities=True,
                               preflight_result: dict | None = None, visual_scan: bool = False) -> dict:
    """Run provider identification before any filename-derived entity linking.

    Exact provider OSHASH has priority, then provider filename clue search. Only
    when providers return no candidate are known performer/studio names linked
    from the filename; an applied or reviewable provider result is not overlaid
    by a second, weaker filename-derived pass.
    """
    result = await _identify_video_core(
        db, channel_id=channel_id, message_id=message_id, filename=filename,
        oshash=oshash, force=force, suggest_only=suggest_only,
        preflight_result=preflight_result, visual_scan=visual_scan,
    )
    if reconcile_filename_entities and result.get("action") == "no_match":
        await _log_filename_entity_reconciliation(db, channel_id, message_id, phase="provider_no_match_fallback")
    return result




# ==============================================================================
# SECTION: Single-flight coordination
# Prevents duplicate identification.
# ==============================================================================

class IdentificationCoordinator:
    """Single-flight coordinator for semantically identical video identification."""

    def __init__(self):
        self._tasks: dict[tuple, asyncio.Task] = {}
        self._guard = asyncio.Lock()

    async def run(self, db, *, channel_id, message_id, filename, oshash=None, force=False,
                  suggest_only=False, reconcile_filename_entities=True,
                  preflight_result: dict | None = None, visual_scan: bool = False) -> dict:
        # Forced and suggestion-only requests have different provider side
        # effects, so they use separate keys. Filename reconciliation mode is
        # intentionally not part of the key: batch callers bracket their shared
        # work with bulk pre/post passes, while ordinary concurrent callers still
        # share one provider request and cannot multiply per-video catalog scans.
        key = (str(channel_id), int(message_id), bool(force), bool(suggest_only), bool(preflight_result), bool(visual_scan))
        async with self._guard:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(
                    _identify_video_impl(
                        db, channel_id=channel_id, message_id=message_id,
                        filename=filename, oshash=oshash, force=force,
                        suggest_only=suggest_only,
                        reconcile_filename_entities=reconcile_filename_entities,
                        preflight_result=preflight_result, visual_scan=visual_scan,
                    )
                )
                self._tasks[key] = task
        try:
            # A cancelled caller must not cancel shared identification work
            # that other callers may still need.
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._guard:
                    if self._tasks.get(key) is task:
                        self._tasks.pop(key, None)


def _coordinator_for(db) -> IdentificationCoordinator:
    coordinator = getattr(db, "_archive_x_identification_coordinator", None)
    if coordinator is None:
        coordinator = IdentificationCoordinator()
        try:
            setattr(db, "_archive_x_identification_coordinator", coordinator)
        except Exception:
            # DB wrappers that disallow arbitrary attributes still get a
            # coordinator for the lifetime of this pipeline module.
            coordinator = _FALLBACK_COORDINATORS.setdefault(id(db), IdentificationCoordinator())
    return coordinator


_FALLBACK_COORDINATORS: dict[int, IdentificationCoordinator] = {}




# ==============================================================================
# SECTION: Filename entity reconciliation
# Reconciles entities found in filenames.
# ==============================================================================

def _reconcile_filename_entities_for_message(db, channel_id, message_id) -> dict[str, int]:
    """Resolve the catalogue row and apply exact known-entity filename links."""
    with db.lock:
        row = db.conn.execute(
            "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
    if not row:
        return {"videos_scanned": 0, "performer_links_added": 0, "studio_links_added": 0,
                "unknown_links_replaced": 0, "ambiguous_entities_skipped": 0}
    from .filename_entities import link_known_entities_from_filenames
    return link_known_entities_from_filenames(db, [int(row["id"])])


async def _log_filename_entity_reconciliation(db, channel_id, message_id, *, phase: str) -> dict[str, int]:
    """Run the local filename linker and log only useful/ambiguous outcomes."""
    try:
        stats = await asyncio.to_thread(_reconcile_filename_entities_for_message, db, channel_id, message_id)
        if any(stats.get(k, 0) for k in ("performer_links_added", "studio_links_added", "unknown_links_replaced", "ambiguous_entities_skipped")):
            db.log(
                "Filename entity matching: "
                f"{stats['performer_links_added']} performer link(s), "
                f"{stats['studio_links_added']} studio link(s), "
                f"{stats['unknown_links_replaced']} Unknown placeholder(s) replaced, "
                f"{stats['ambiguous_entities_skipped']} ambiguous alias(es) skipped",
                "INFO" if not stats["ambiguous_entities_skipped"] else "WARN",
                event="metadata.filename_entity_match",
                details={"channel_id": str(channel_id), "message_id": int(message_id), "phase": phase, **stats},
            )
        return stats
    except Exception as exc:
        # Local reconciliation is additive and must not turn a provider result
        # into a failure. A later bulk run can retry transient DB errors.
        try:
            db.log(f"Filename entity matching failed for {channel_id}/{message_id}: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            pass
        return {"videos_scanned": 0, "performer_links_added": 0, "studio_links_added": 0,
                "unknown_links_replaced": 0, "ambiguous_entities_skipped": 0}




# ==============================================================================
# SECTION: Public entry point
# identify_video.
# ==============================================================================

async def _identify_video_unlocked(db, *, channel_id, message_id, filename, oshash=None, force=False,
                         suggest_only=False, reconcile_filename_entities: bool | None = None,
                         preflight_result: dict | None = None, visual_scan: bool = False) -> dict:
    """Identify one video and optionally reconcile exact names from its filename.

    The local pass runs inside the shared identification task before provider I/O,
    so provider outages do not prevent safe local linking. Applied provider matches
    receive one post-pass to restore filename-confirmed links they may replace.
    Suggestion-only requests remain non-mutating unless explicitly opted in.
    """
    should_reconcile = (not suggest_only) if reconcile_filename_entities is None else bool(reconcile_filename_entities)
    return await _coordinator_for(db).run(
        db, channel_id=channel_id, message_id=message_id, filename=filename,
        oshash=oshash, force=force, suggest_only=suggest_only,
        reconcile_filename_entities=should_reconcile, preflight_result=preflight_result,
        visual_scan=visual_scan,
    )


async def identify_video(db, *, channel_id, message_id, filename, oshash=None, force=False,
                         suggest_only=False, reconcile_filename_entities: bool | None = None,
                         preflight_result: dict | None = None, visual_scan: bool = False) -> dict:
    """Identify under the global per-video lock and skip already-published deliveries.

    A verified delivery is prepared at the destination before the manual scan is
    allowed to continue. Explicit Force remains an operator-authorized recheck.
    """
    channel, mid = str(channel_id or "").strip(), int(message_id or 0)
    identity = str(oshash or "").strip()
    try:
        with db.lock:
            row = db.conn.execute("SELECT oshash,identity_key FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1", (channel, mid)).fetchone()
        if row and not identity:
            identity = str(row["oshash"] or row["identity_key"] or "")
    except Exception:
        pass
    from ..core.video_locks import video_operation_lock
    async with video_operation_lock(identity_key=identity, source_channel=channel, source_message_id=mid):
        if not force and not suggest_only and mid > 0:
            try:
                from ..core.resource_registry import ensure_resource_tables
                ensure_resource_tables(db)
                with db.lock:
                    published = db.conn.execute(
                        "SELECT 1 FROM archive_x_published_media pm JOIN media m ON m.id=pm.id "
                        "WHERE m.source_channel=? AND m.source_message_id=? LIMIT 1", (channel, mid),
                    ).fetchone()
                if published:
                    return {"action": "skipped", "reason": "verified_delivery_already_published",
                            "source": "archive_x_delivery_ledger", "channel_id": channel, "message_id": mid}
            except Exception:
                pass
        return await _identify_video_unlocked(
            db, channel_id=channel_id, message_id=message_id, filename=filename, oshash=oshash,
            force=force, suggest_only=suggest_only,
            reconcile_filename_entities=reconcile_filename_entities,
            preflight_result=preflight_result, visual_scan=visual_scan,
        )
