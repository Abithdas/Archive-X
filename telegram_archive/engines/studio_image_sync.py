"""Studio/site artwork synchronization.

This module is intentionally studio-only. It never writes performer records or
performer image caches. It first uses API metadata already cached for linked
videos, then performs a provider lookup for a small representative set when a
studio has no usable API image URL.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import json
from typing import Any

from ..metadata.providers import identify, studio_image_from_providers
from .thumbnail_cache import prefetch_entity_image
from .entity_image_utils import provider_rank as _provider_rank, http_image_url as _http_image_url, same_name as _same_name, entity_image_cache_valid




def _payload_studio_image(payload: Any) -> tuple[str, str, str]:
    """Studio/site image from a stored provider scene payload.

    V29.4.126: this helper was called but never defined, so taking a studio
    logo from already-matched scene metadata always failed with NameError and
    every studio fell through to a fresh provider lookup.
    """
    if not isinstance(payload, dict):
        return "", "", ""
    source = str(payload.get("source") or payload.get("provider") or "").strip()
    image, provider_id = "", ""
    studio = payload.get("studio")
    if isinstance(studio, dict):
        image = _http_image_url(studio.get("image") or studio.get("image_path") or studio.get("logo") or studio.get("poster"))
        provider_id = str(studio.get("id") or studio.get("_id") or "").strip()
    if not image:
        image = _http_image_url(payload.get("studio_image") or payload.get("site_image") or payload.get("site_logo"))
        provider_id = provider_id or str(payload.get("studio_id") or payload.get("site_id") or "").strip()
    return (image, source, provider_id) if image else ("", "", "")


async def _provider_image_from_linked_media(db, studio_id: int, studio_name: str) -> tuple[str, str, str]:
    with db.lock:
        rows = db.conn.execute(
            "SELECT m.filename,m.provider_payload FROM media m "
            "JOIN video_studios vs ON vs.video_id=m.id "
            "WHERE vs.studio_id=? AND m.provider_payload IS NOT NULL AND TRIM(m.provider_payload)<>'' "
            "ORDER BY m.updated_at DESC,m.id DESC LIMIT 50",
            (int(studio_id),),
        ).fetchall()
    candidates: list[tuple[int, int, str, str, str]] = []
    for order, row in enumerate(rows):
        try:
            payload = json.loads(row["provider_payload"] or "{}")
        except Exception:
            _swallowed_note("studio_image_sync._provider_image_from_linked_media")
            continue
        image, source, provider_id = _payload_studio_image(payload)
        payload_name = payload.get("studio") or payload.get("site")
        if isinstance(payload_name, dict):
            payload_name = payload_name.get("name")
        if image and (not payload_name or _same_name(studio_name, str(payload_name))):
            candidates.append((_provider_rank(source), -order, image, source, provider_id))
    if not candidates:
        return "", "", ""
    _rank, _order, image, source, provider_id = max(candidates, key=lambda x: (x[0], x[1]))
    return image, source, provider_id


async def _provider_image_by_filename(db, studio_id: int, studio_name: str) -> tuple[str, str, str]:
    with db.lock:
        rows = db.conn.execute(
            "SELECT filename FROM media m JOIN video_studios vs ON vs.video_id=m.id "
            "WHERE vs.studio_id=? AND COALESCE(m.filename,'')<>'' "
            "ORDER BY m.updated_at DESC,m.id DESC LIMIT 8",
            (int(studio_id),),
        ).fetchall()
    candidates: list[tuple[int, str, str, str]] = []
    for row in rows:
        try:
            result = await identify(db, oshash=None, filename=str(row["filename"] or ""))
        except Exception:
            _swallowed_note("studio_image_sync._provider_image_by_filename")
            continue
        for match in result.get("matches") or []:
            match_name = match.get("studio") or match.get("site") or ""
            image = _http_image_url(match.get("studio_image"))
            if image and _same_name(studio_name, str(match_name)):
                source = str(match.get("source") or "").strip()
                candidates.append((_provider_rank(source), image, source, str(match.get("studio_id") or "")))
    if not candidates:
        return "", "", ""
    _rank, image, source, provider_id = max(candidates, key=lambda x: x[0])
    return image, source, provider_id


async def refresh_studio_image(db, studio_id: int, *, force_api: bool = False, provider_rows: dict[str, dict] | None = None) -> dict[str, Any]:
    """Find and cache the best available image for a studio."""
    with db.lock:
        row = db.conn.execute("SELECT * FROM studios WHERE id=?", (int(studio_id),)).fetchone()
    if not row:
        return {"ok": False, "outcome": "entity_not_found", "reason": "studio_not_found", "entity_type": "studio", "entity_id": int(studio_id)}
    if int(row["image_locked"] or 0):
        return {"ok": False, "outcome": "manual_image_locked", "reason": "manual_image_locked", "name": str(row["name"] or ""), "entity_type": "studio", "entity_id": int(studio_id)}
    name = str(row["name"] or "").strip()
    current = _http_image_url(row["image"])
    if not force_api and current and entity_image_cache_valid(db, "studio", int(studio_id), current):
        return {"ok": True, "outcome": "already_current", "reason": "cache_valid", "entity_type": "studio", "entity_id": int(studio_id), "name": name, "image": current, "source": "local-cache"}

    image = source = provider_id = ""
    if not force_api and current:
        image = current
    else:
        try:
            provider_ids = json.loads(row["provider_ids"] or "{}") if row["provider_ids"] else {}
            if not isinstance(provider_ids, dict):
                provider_ids = {}
        except Exception:
            provider_ids = {}

        # Reuse the complete provider records already fetched by Entity Data.
        # Only perform a second provider lookup when no reusable response was
        # supplied or the fetched records contained no usable artwork.
        image = source = provider_id = ""
        if provider_rows:
            ranked: list[tuple[int, str, str, str]] = []
            for provider, payload in provider_rows.items():
                if not isinstance(payload, dict):
                    continue
                candidate = _http_image_url(
                    payload.get("image") or payload.get("image_url") or payload.get("image_path")
                    or payload.get("logo") or payload.get("poster")
                )
                if candidate:
                    pid = str(payload.get("id") or payload.get("_id") or payload.get("uuid") or "").strip()
                    ranked.append((_provider_rank(provider), candidate, str(provider), pid))
            if ranked:
                _rank, image, source, provider_id = max(ranked, key=lambda x: x[0])
        if not image and str(row["content_rating"] or "") == "sfw":
            # A movie or TV studio: its logo comes from TMDB. The adult providers are never asked about it.
            from . import screen_identify as _screen
            image = await asyncio.to_thread(lambda: _screen.make_screen(db).company_logo(name))
            source, provider_id = ("tmdb", "") if image else ("", "")
        elif not image:
            image, source, provider_id = await studio_image_from_providers(db, name, provider_ids)

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
            saved = bool(await prefetch_entity_image(db, "studio", int(studio_id), candidate))
            if not saved:
                cache_errors.append("cache_write_rejected")
            else:
                cache_successes.add(candidate)
            return saved
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:500]
            cache_errors.append(message)
            db.log(f"Studio artwork candidate rejected for {name}: {message}", "WARN")
            return False

    if image and not await _try_cache(image):
        image = source = provider_id = ""

    if not image:
        image, source, provider_id = await _provider_image_from_linked_media(db, int(studio_id), name)
        if image and not await _try_cache(image):
            image = source = provider_id = ""
    if not image:
        image, source, provider_id = await _provider_image_by_filename(db, int(studio_id), name)
        if image and not await _try_cache(image):
            image = source = provider_id = ""
    if not image:
        if current:
            if await _try_cache(current):
                return {"ok": True, "outcome": "preserved_existing", "reason": "provider_image_unavailable", "name": name, "entity_type": "studio", "entity_id": int(studio_id), "image": current, "source": "preserved-existing", "preserved_existing": True}
            return {"ok": False, "outcome": "download_failed", "reason": "artwork_cache_failed", "error": (cache_errors[-1] if cache_errors else "cache_write_rejected"), "name": name, "entity_type": "studio", "entity_id": int(studio_id), "image": current, "preserved_existing": True}
        has_candidate_failure = bool(cache_errors)
        return {"ok": False, "outcome": "download_failed" if has_candidate_failure else "no_provider_artwork", "reason": "artwork_cache_failed" if has_candidate_failure else "no_provider_image", "error": (cache_errors[-1] if cache_errors else ""), "name": name, "entity_type": "studio", "entity_id": int(studio_id)}

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
        vals.append(int(studio_id))
        # Re-check the lock at write time. A user may have selected/manual-set
        # an image while the provider lookup was still running. In that case
        # this background result must never overwrite the user's choice.
        cur = db.conn.execute(
            f"UPDATE studios SET {', '.join(fields)} "
            "WHERE id=? AND COALESCE(image_locked,0)=0",
            vals,
        )
        db.conn.commit()
        if cur.rowcount == 0:
            return {"ok": True, "outcome": "manual_image_preserved", "reason": "manual_image_preserved", "entity_type": "studio", "entity_id": int(studio_id), "name": name, "source": "manual-image-preserved", "skipped_overwrite": True}

    return {"ok": True, "outcome": "cached", "reason": "artwork_cached", "entity_type": "studio", "entity_id": int(studio_id), "name": name, "image": image, "source": source or "cached-api-metadata"}
