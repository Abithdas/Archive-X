"""Background API-only performer artwork refresh.

Never uses Telegram/video thumbnails. It resolves performer artwork from
provider metadata already stored on linked media, then falls back to a bounded
provider filename lookup. Manual/local artwork is not synthesized here.
"""
from __future__ import annotations

import json
from typing import Any

from ..metadata.providers import identify, performer_image_from_providers
from .thumbnail_cache import prefetch_entity_image
from .entity_image_utils import provider_rank as _provider_rank, http_image_url as _http_image_url, same_name as _same_name, entity_image_cache_valid




def _payload_performer_image(payload: Any, performer_name: str) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        return "", "", ""
    performers = payload.get("performers") or payload.get("performer") or []
    if isinstance(performers, dict):
        performers = [performers]
    if isinstance(performers, str):
        performers = [performers]
    source = str(payload.get("source") or "").strip()
    for item in performers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("performer") or "").strip()
        image = _http_image_url(item.get("image") or item.get("image_path") or item.get("image_url") or item.get("photo"))
        pid = str(item.get("id") or item.get("performer_id") or "").strip()
        if image and (not name or _same_name(performer_name, name)):
            return image, source, pid
    image = _http_image_url(payload.get("performer_image"))
    if image:
        return image, source, str(payload.get("performer_id") or "").strip()
    return "", "", ""


async def _provider_image_from_linked_media(db, performer_id: int, performer_name: str) -> tuple[str, str, str]:
    with db.lock:
        rows = db.conn.execute(
            "SELECT m.filename,m.provider_payload FROM media m "
            "JOIN video_performers vp ON vp.video_id=m.id "
            "WHERE vp.performer_id=? AND m.provider_payload IS NOT NULL AND TRIM(m.provider_payload)<>'' "
            "ORDER BY m.updated_at DESC,m.id DESC LIMIT 50",
            (int(performer_id),),
        ).fetchall()
    candidates: list[tuple[int, int, str, str, str]] = []
    for order, row in enumerate(rows):
        try:
            payload = json.loads(row["provider_payload"] or "{}")
        except Exception:
            continue
        image, source, pid = _payload_performer_image(payload, performer_name)
        if image:
            candidates.append((_provider_rank(source), -order, image, source, pid))
    if not candidates:
        return "", "", ""
    # StashDB always wins over ThePornDB. Within the same provider keep the
    # newest payload so refreshes do not oscillate between scene records.
    _rank, _order, image, source, pid = max(candidates, key=lambda x: (x[0], x[1]))
    return image, source, pid


async def _provider_image_by_filename(db, performer_id: int, performer_name: str) -> tuple[str, str, str]:
    with db.lock:
        rows = db.conn.execute(
            "SELECT filename FROM media m JOIN video_performers vp ON vp.video_id=m.id "
            "WHERE vp.performer_id=? AND COALESCE(m.filename,'')<>'' "
            "ORDER BY m.updated_at DESC,m.id DESC LIMIT 8",
            (int(performer_id),),
        ).fetchall()
    candidates: list[tuple[int, str, str, str]] = []
    for row in rows:
        try:
            result = await identify(db, oshash=None, filename=str(row["filename"] or ""))
        except Exception:
            continue
        for match in result.get("matches") or []:
            details = match.get("performer_details") or []
            if not isinstance(details, list):
                details = []
            for person in details:
                if not isinstance(person, dict):
                    continue
                pname = str(person.get("name") or "").strip()
                image = _http_image_url(person.get("image") or person.get("thumbnail"))
                if image and _same_name(performer_name, pname):
                    source = str(person.get("provider") or match.get("source") or "").strip()
                    pid = str(person.get("id") or "").strip()
                    candidates.append((_provider_rank(source), image, source, pid))
    if not candidates:
        return "", "", ""
    _rank, image, source, pid = max(candidates, key=lambda x: x[0])
    return image, source, pid


def _provider_image_from_payload(payload: dict[str, Any]) -> str:
    """Extract one canonical HTTP artwork URL from an already-fetched provider row."""
    return _http_image_url(
        payload.get("image") or payload.get("image_url") or payload.get("image_path")
        or payload.get("profile_image") or payload.get("avatar") or payload.get("poster")
    )


async def refresh_performer_image(db, performer_id: int, *, force_api: bool = True, provider_rows: dict[str, dict] | None = None) -> dict[str, Any]:
    """Find and cache the best available image for a performer."""
    with db.lock:
        row = db.conn.execute("SELECT * FROM performers WHERE id=?", (int(performer_id),)).fetchone()
    if not row:
        return {"ok": False, "outcome": "entity_not_found", "reason": "performer_not_found", "entity_type": "performer", "entity_id": int(performer_id)}
    if str(row["name"] or "").strip().casefold() == "unknown performer":
        return {"ok": False, "outcome": "protected_local_fallback", "reason": "protected_local_fallback", "name": str(row["name"] or ""), "entity_type": "performer", "entity_id": int(performer_id)}
    if int(row["image_locked"] or 0):
        return {"ok": False, "outcome": "manual_image_locked", "reason": "manual_image_locked", "name": str(row["name"] or ""), "entity_type": "performer", "entity_id": int(performer_id)}
    name = str(row["name"] or "").strip()
    current = _http_image_url(row["image"])
    if not force_api and current and entity_image_cache_valid(db, "performer", int(performer_id), current):
        return {"ok": True, "outcome": "already_current", "reason": "cache_valid", "entity_type": "performer", "entity_id": int(performer_id), "name": str(row["name"] or ""), "image": current, "source": "local-cache"}
    try:
        provider_ids = json.loads(row["provider_ids"] or "{}") if row["provider_ids"] else {}
        if not isinstance(provider_ids, dict):
            provider_ids = {}
    except Exception:
        provider_ids = {}

    cache_errors: list[str] = []
    cache_attempts: set[str] = set()
    cache_successes: set[str] = set()

    async def _try_cache(candidate: str) -> bool:
        if not candidate:
            return False
        candidate = str(candidate)
        if candidate in cache_successes:
            return True
        if candidate in cache_attempts:
            return False
        cache_attempts.add(candidate)
        try:
            saved = bool(await prefetch_entity_image(
                db, "performer", int(performer_id), candidate, smart_crop_initial=True
            ))
            if not saved:
                cache_errors.append("cache_write_rejected")
            else:
                cache_successes.add(candidate)
            return saved
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:500]
            cache_errors.append(message)
            db.log(f"Performer artwork candidate rejected for {name}: {message}", "WARN")
            return False

    # Reuse the complete provider records already fetched by Entity Data.
    # This avoids immediately issuing the same StashDB/ThePornDB request again
    # merely to obtain the image that was already present in the response.
    image = source = provider_id = ""
    if not force_api and current:
        image = current
    elif provider_rows:
        ranked: list[tuple[int, str, str, str]] = []
        for provider, payload in provider_rows.items():
            if not isinstance(payload, dict):
                continue
            candidate = _http_image_url(_provider_image_from_payload(payload))
            if candidate:
                pid = str(payload.get("id") or payload.get("_id") or payload.get("uuid") or "").strip()
                ranked.append((_provider_rank(provider), candidate, str(provider), pid))
        if ranked:
            _rank, image, source, provider_id = max(ranked, key=lambda x: x[0])
    if not image:
        # Query both configured providers only when the caller did not already
        # supply their freshly fetched entity records.
        image, source, provider_id = await performer_image_from_providers(db, name, provider_ids)
    if image and not await _try_cache(image):
        image = source = provider_id = ""
    if not image:
        image, source, provider_id = await _provider_image_from_linked_media(db, int(performer_id), name)
        if image and not await _try_cache(image):
            image = source = provider_id = ""
    if not image:
        image, source, provider_id = await _provider_image_by_filename(db, int(performer_id), name)
        if image and not await _try_cache(image):
            image = source = provider_id = ""
    if not image:
        # Keep the prior URL when provider lookups fail, but report whether the
        # local cache is actually usable so a later scan can retry a failed download.
        if current:
            if await _try_cache(current):
                return {"ok": True, "outcome": "preserved_existing", "reason": "provider_image_unavailable", "name": name, "entity_type": "performer", "entity_id": int(performer_id), "image": current, "source": "preserved-existing", "preserved_existing": True}
            return {"ok": False, "outcome": "download_failed", "reason": "artwork_cache_failed", "error": (cache_errors[-1] if cache_errors else "cache_write_rejected"), "name": name, "entity_type": "performer", "entity_id": int(performer_id), "image": current, "preserved_existing": True}
        has_candidate_failure = bool(cache_errors)
        return {"ok": False, "outcome": "download_failed" if has_candidate_failure else "no_provider_artwork", "reason": "artwork_cache_failed" if has_candidate_failure else "no_provider_image", "error": (cache_errors[-1] if cache_errors else ""), "name": name, "entity_type": "performer", "entity_id": int(performer_id)}

    with db.lock:
        fields = ["image=?"]
        vals: list[Any] = [image]
        if source and provider_id:
            try:
                ids = json.loads(row["provider_ids"] or "{}") if row["provider_ids"] else {}
                if not isinstance(ids, dict):
                    ids = {}
            except Exception:
                ids = {}
            ids[source] = provider_id
            fields.append("provider_ids=?")
            vals.append(json.dumps(ids, ensure_ascii=False))
        vals.append(int(performer_id))
        # Re-check the lock at write time. A user may have selected/manual-set
        # an image while the provider lookup was still running. In that case
        # this background result must never overwrite the user's choice.
        cur = db.conn.execute(
            f"UPDATE performers SET {', '.join(fields)} "
            "WHERE id=? AND COALESCE(image_locked,0)=0",
            vals,
        )
        db.conn.commit()
        if cur.rowcount == 0:
            return {"ok": True, "outcome": "manual_image_preserved", "reason": "manual_image_preserved", "entity_type": "performer", "entity_id": int(performer_id), "name": name, "source": "manual-image-preserved", "skipped_overwrite": True}

    return {"ok": True, "outcome": "cached", "reason": "artwork_cached", "entity_type": "performer", "entity_id": int(performer_id), "name": name, "image": image, "source": source or "cached-api-metadata"}
