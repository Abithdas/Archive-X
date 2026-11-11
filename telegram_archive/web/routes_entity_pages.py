"""Explicit registration for performer and studio pages and entity images."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Performer pages
#       - Entity images
#       - Studio pages and taxonomy
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request


def register_entity_page_routes(
    router: APIRouter,
    *,
    db,
    jobs,
    require_auth,
    _provider_page_url,
    tag_taxonomy_cache,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Performer pages
    # Similar performers, videos and metadata.
    # ------------------------------------------------------------------
    """Register performer and studio page and image routes."""
    @router.get("/api/performers/{performer_id}/similar")
    async def performer_similar(performer_id: int, request: Request):
        """Return same-gender performers ranked by the seven profile attributes."""
        require_auth(request)
        try:
            limit = max(1, min(40, int(request.query_params.get("limit", "24"))))
        except ValueError:
            limit = 24

        from ..metadata.performer_similarity import (
            extract_performer_details, normalize_gender, score_performer_similarity,
        )

        with db.reading() as _rdb:
            target_row = _rdb.execute("SELECT * FROM performers WHERE id=?", (int(performer_id),)).fetchone()
            if not target_row:
                raise HTTPException(404, "Performer not found")
            target = dict(target_row)
            meta_rows = _rdb.execute(
                "SELECT entity_id,provider,normalized FROM entity_metadata WHERE entity_type='performer'"
            ).fetchall()
            by_entity = {}
            for meta_row in meta_rows:
                by_entity.setdefault(int(meta_row["entity_id"]), []).append(meta_row)
            target_details = extract_performer_details(by_entity.get(int(performer_id), []))
            # Locally selected gender remains authoritative. Provider metadata is
            # a fallback only when the local entity has no usable gender value.
            target_gender = normalize_gender(target.get("gender") or target_details.get("gender"))
            if target_gender not in {"female", "male"}:
                return {"performer": target, "items": [], "reason": "gender_required"}

            gender_aliases = ("female", "f", "woman", "women") if target_gender == "female" else ("male", "m", "man", "men")
            candidates = _rdb.execute(
                "SELECT p.*, COUNT(DISTINCT combined.video_id) video_count "
                "FROM performers p "
                "LEFT JOIN (SELECT performer_id, video_id FROM video_performers WHERE video_id IN (SELECT id FROM archive_x_library_media) "
                "UNION SELECT performer_id, video_id FROM local_video_performers WHERE video_id IN (SELECT id FROM archive_x_library_media)) combined "
                "ON combined.performer_id=p.id "
                "WHERE p.id<>? AND (lower(trim(COALESCE(p.gender,''))) IN (?,?,?,?) OR trim(COALESCE(p.gender,''))='') "
                "GROUP BY p.id HAVING video_count > 0 ORDER BY video_count DESC,p.name",
                (int(performer_id), *gender_aliases),
            ).fetchall()

        ranked = []
        for candidate_row in candidates:
            candidate = dict(candidate_row)
            candidate_details = extract_performer_details(by_entity.get(int(candidate["id"]), []))
            candidate_gender = normalize_gender(candidate.get("gender") or candidate_details.get("gender"))
            comparison = score_performer_similarity(
                target_gender, target_details, candidate_gender, candidate_details,
            )
            if comparison is None:
                continue
            coverage = float(comparison["metadata_coverage"])
            raw_score = float(comparison["score"])
            # Confidence-aware ordering prevents a perfect score from a single
            # known field outranking a well-supported match across many fields.
            ranking_score = raw_score * (0.70 + 0.30 * coverage)
            ranked.append((
                ranking_score,
                raw_score,
                int(comparison["matched_detail_count"]),
                coverage,
                int(comparison["compared_detail_count"]),
                int(candidate.get("video_count") or 0),
                str(candidate.get("name") or "").casefold(),
                candidate,
                comparison,
            ))
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], -item[3], -item[4], -item[5], item[6]))

        items = []
        for _rank_score, _score, _matched, _coverage, _compared, _count, _name, candidate, comparison in ranked[:limit]:
            candidate["gender"] = normalize_gender(candidate.get("gender") or target_gender)
            candidate["similarity_score"] = round(float(comparison["score"]), 3)
            candidate["matched_detail_count"] = int(comparison["matched_detail_count"])
            candidate["compared_detail_count"] = int(comparison["compared_detail_count"])
            candidate["metadata_coverage"] = round(float(comparison["metadata_coverage"]), 3)
            candidate["matched_fields"] = list(comparison["matched_fields"])
            candidate["compared_fields"] = list(comparison["compared_fields"])
            items.append(candidate)
        return {
            "performer": target,
            "items": items,
            "gender": target_gender,
            "algorithm": "same-gender-country-age-ethnicity-hair-breast-cup-v2",
            "similarity_fields": ["gender", "country", "age", "ethnicity", "hair_color", "breast_type", "cup_size"],
        }

    @router.get("/api/performers/{performer_id}/videos")
    async def performer_videos(performer_id: int, request: Request, studio_id: int | None = None):
        """GET /api/performers/{performer_id}/videos: Performer videos."""
        require_auth(request)
        from ..core.resource_registry import ensure_unknown_performer_local_profile
        await asyncio.to_thread(ensure_unknown_performer_local_profile, db)
        with db.reading() as _rdb:
            p = _rdb.execute("SELECT * FROM performers WHERE id=?", (performer_id,)).fetchone()
            if not p:
                raise HTTPException(404, "Not found")
            p = dict(p)
            p["favorite"] = bool(_rdb.execute("SELECT 1 FROM entity_favorites WHERE entity_type='performer' AND entity_id=?", (performer_id,)).fetchone())
            rows = _rdb.execute(
                # Duplicate performer rows that differ only in case/accents
                # (e.g. one from the API, one typed locally) are one person.
                "WITH me AS (SELECT lower(trim(name)) n, COALESCE(normalized_name,'') nn FROM performers WHERE id=?), "
                "same AS (SELECT id FROM performers WHERE id=? OR lower(trim(name))=(SELECT n FROM me) "
                "OR (COALESCE(normalized_name,'')<>'' AND normalized_name=(SELECT nn FROM me))) "
                "SELECT m.*, c.title channel_title, "
                "GROUP_CONCAT(DISTINCT s.name) studios, GROUP_CONCAT(DISTINCT t.name) tags, "
                "GROUP_CONCAT(DISTINCT p2.name) performers, GROUP_CONCAT(DISTINCT lp.name) local_performers "
                "FROM archive_x_library_media m "
                "LEFT JOIN channels c ON c.telegram_id=m.source_channel "
                "LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id "
                "LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id "
                "LEFT JOIN video_performers vp2 ON vp2.video_id=m.id LEFT JOIN performers p2 ON p2.id=vp2.performer_id "
                "LEFT JOIN local_video_performers lvp2 ON lvp2.video_id=m.id LEFT JOIN performers lp ON lp.id=lvp2.performer_id "
                "WHERE (EXISTS (SELECT 1 FROM video_performers vp WHERE vp.video_id=m.id AND vp.performer_id IN (SELECT id FROM same)) "
                "   OR EXISTS (SELECT 1 FROM local_video_performers lvp WHERE lvp.video_id=m.id AND lvp.performer_id IN (SELECT id FROM same))) "
                 + (" AND EXISTS (SELECT 1 FROM video_studios rvs WHERE rvs.video_id=m.id AND rvs.studio_id=?) " if studio_id else "")
                 + " AND COALESCE(m.status,'')<>'SOURCE_DELETED' "
                 + " AND (CAST(m.source_channel AS TEXT) GLOB '-[0-9]*' OR (CAST(m.source_channel AS TEXT)<>'' AND CAST(m.source_channel AS TEXT) NOT GLOB '*[^0-9]*')) "
                + " GROUP BY m.id ORDER BY m.upload_date DESC",
                (performer_id, performer_id, studio_id) if studio_id else (performer_id, performer_id)).fetchall()
        videos=[]
        for r in rows:
            d=dict(r)
            if not d.get("provider_url"):
                d["provider_url"] = _provider_page_url(r)
            d["performers"]=list(dict.fromkeys([x for x in (d.get("performers") or "").split(",") if x] + [x for x in (d.get("local_performers") or "").split(",") if x]))
            d["local_performers"]=[x for x in (d.get("local_performers") or "").split(",") if x]
            d["studios"]=[x for x in (d.get("studios") or "").split(",") if x]
            d["studio"]=d["studios"][0] if d["studios"] else ""
            d["tags"]=[x for x in (d.get("tags") or "").split(",") if x]
            d["media_id"]=d.get("id")   # SQLite row id, needed for favourites and other row-level actions
            d["_cid"]=d.get("source_channel")
            d["id"]=d.get("source_message_id")
            d["name"]=d.get("filename") or d.get("title") or f"video {d['id']}"
            d["thumbnail_id"]=d.get("thumbnail_message_id") or d.get("source_message_id")
            videos.append(d)
        return {"performer": dict(p), "videos": videos}

    async def _queue_entity_data_sync(data: dict, *, scope_override: str | None = None):
        """Submit an entity data job for the chosen scope."""
        scope = str(scope_override or data.get("scope") or "all").strip().casefold()
        if scope not in {"all", "performers", "studios"}:
            scope = "all"
        try:
            limit = max(25, min(5000, int(data.get("limit", 50))))
        except Exception:
            limit = 50
        unbounded = bool(data.get("unbounded", False))
        if unbounded:
            limit = 0
        force_recheck = bool(data.get("force_recheck", False))
        entity_ids: dict[str, list[int]] = {}
        raw_entity_ids = data.get("entity_ids")
        if raw_entity_ids is not None:
            if not isinstance(raw_entity_ids, dict):
                raise HTTPException(400, "Entity IDs must be grouped by performer or studio")
            for kind in ("performer", "studio"):
                values = raw_entity_ids.get(kind) or []
                if not isinstance(values, (list, tuple, set)):
                    values = [values]
                ids: list[int] = []
                for value in values:
                    try:
                        entity_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if entity_id > 0 and entity_id not in ids:
                        ids.append(entity_id)
                if len(ids) > 50:
                    raise HTTPException(400, "A targeted entity sync can include at most 50 IDs per type")
                if ids:
                    entity_ids[kind] = ids
            if scope == "studios" and "performer" in entity_ids:
                raise HTTPException(400, "A studio-only sync cannot include performer IDs")
            if scope == "performers" and "studio" in entity_ids:
                raise HTTPException(400, "A performer-only sync cannot include studio IDs")
            if not entity_ids:
                raise HTTPException(400, "No valid entity IDs were provided")
        if jobs.is_active("ENTITY_IMAGE_SYNC"):
            active = db.active_jobs("ENTITY_IMAGE_SYNC")
            return {"ok": True, "already_running": True, "id": active[0]["id"] if active else None}
        detail_data = {"scope": scope, "limit": limit, "unbounded": unbounded, "force_api": True, "force_recheck": force_recheck, "manual": True}
        if entity_ids:
            detail_data["entity_ids"] = entity_ids
        detail = json.dumps(detail_data)
        jid = db.create_job("ENTITY_IMAGE_SYNC", "", "", False, detail=detail)
        jobs.submit(jid)
        return {"ok": True, "id": jid, "limit": limit, "scope": scope}

    # Details-page lazy sync is idempotent per entity: once queued, the same
    # entity is not re-queried for a while even if the providers returned no
    # details (otherwise every page visit would repeat the same API calls).
    _LAZY_SYNC_COOLDOWN_SECONDS = 1800.0
    _lazy_sync_attempted: dict[tuple[str, int], float] = {}

    async def _ensure_entity_metadata(entity_type: str, entity_id: int, request: Request):
        """Queue a metadata refresh for one entity when it is needed."""
        require_auth(request)
        if entity_type not in {"performer", "studio"} or int(entity_id) <= 0:
            raise HTTPException(400, "Invalid entity")
        table = "performers" if entity_type == "performer" else "studios"
        with db.reading() as _rdb:
            entity = _rdb.execute(f"SELECT id,name,provider_ids FROM {table} WHERE id=?", (int(entity_id),)).fetchone()
            if not entity:
                raise HTTPException(404, f"{entity_type.title()} not found")
            meta = _rdb.execute(
                "SELECT provider,provider_id,normalized FROM entity_metadata WHERE entity_type=? AND entity_id=? ORDER BY provider",
                (entity_type, int(entity_id)),
            ).fetchall()
        if entity_type == "performer" and str(entity["name"] or "").strip().casefold() == "unknown performer":
            from ..core.resource_registry import ensure_unknown_performer_local_profile
            await asyncio.to_thread(ensure_unknown_performer_local_profile, db)
            return {"ok": True, "needed": False, "reason": "protected_local_fallback", "providers": []}
        if not str(entity["provider_ids"] or "{}").strip() or str(entity["provider_ids"]).strip() in {"{}", "null"}:
            return {"ok": True, "needed": False, "reason": "not_provider_linked", "providers": [dict(x) for x in meta]}
        has_details = False
        for item in meta:
            try:
                normalized = json.loads(item["normalized"] or "{}")
            except Exception:
                normalized = {}
            if isinstance(normalized, dict) and isinstance(normalized.get("details"), dict) and normalized.get("details"):
                has_details = True
                break
        if has_details:
            return {"ok": True, "needed": False, "reason": "already_synced", "providers": [dict(x) for x in meta]}
        attempt_key = (entity_type, int(entity_id))
        last_attempt = _lazy_sync_attempted.get(attempt_key)
        if last_attempt is not None and time.monotonic() - last_attempt < _LAZY_SYNC_COOLDOWN_SECONDS:
            return {"ok": True, "needed": False, "reason": "recently_checked", "providers": [dict(x) for x in meta]}
        if jobs.is_active("ENTITY_IMAGE_SYNC"):
            active = db.active_jobs("ENTITY_IMAGE_SYNC")
            return {"ok": True, "needed": True, "already_running": True, "id": active[0]["id"] if active else None}
        _lazy_sync_attempted[attempt_key] = time.monotonic()
        detail = json.dumps({
            "scope": "performers" if entity_type == "performer" else "studios",
            "limit": 50,
            "unbounded": False,
            "force_api": True,
            "force_recheck": False,
            "entity_ids": {entity_type: [int(entity_id)]},
            # Opening an entity details page is an explicit user action. Keep
            # the lazy sync narrowly scoped to this single entity, but mark it
            # manual so the unified manager's bulk-provider safety gate does not
            # cancel it before execution.
            "manual": True,
            "reason": "details_view_lazy_sync",
        })
        jid = db.create_job("ENTITY_IMAGE_SYNC", "", "", False, detail=detail)
        jobs.submit(jid)
        return {"ok": True, "needed": True, "queued": True, "id": jid}

    @router.post("/api/performers/{performer_id}/metadata/ensure")
    async def ensure_performer_metadata(performer_id: int, request: Request):
        return await _ensure_entity_metadata("performer", performer_id, request)

    @router.post("/api/studios/{studio_id}/metadata/ensure")
    async def ensure_studio_metadata(studio_id: int, request: Request):
        return await _ensure_entity_metadata("studio", studio_id, request)

    async def _entity_career_scene_counts(entity_type: str, entity_id: int, request: Request) -> dict:
        """Return source-attributed scene totals without mixing them with local Archive X videos.

        StashDB exposes performer scene_count in the saved entity payload. ThePornDB
        exposes a paginated scene endpoint whose meta.total gives its current catalogued
        scene count for either a performer or a site. Counts are cached to avoid one
        provider request every time a profile is opened.
        """
        require_auth(request)
        if entity_type not in {"performer", "studio"} or int(entity_id) <= 0:
            raise HTTPException(400, "Invalid entity")
        table = "performers" if entity_type == "performer" else "studios"
        with db.reading() as _rdb:
            entity = _rdb.execute(f"SELECT id,name,provider_ids FROM {table} WHERE id=?", (int(entity_id),)).fetchone()
            meta = _rdb.execute(
                "SELECT provider,provider_id,normalized FROM entity_metadata WHERE entity_type=? AND entity_id=? ORDER BY provider",
                (entity_type, int(entity_id)),
            ).fetchall()
        if not entity:
            raise HTTPException(404, f"{entity_type.title()} not found")
        if entity_type == "performer" and str(entity["name"] or "").strip().casefold() == "unknown performer":
            return {"entity_type": entity_type, "career_counts": []}

        def positive_or_zero_count(value):
            if isinstance(value, bool) or value is None:
                return None
            if isinstance(value, int):
                return value if value >= 0 else None
            if isinstance(value, float):
                return int(value) if value >= 0 and value.is_integer() else None
            text = str(value).strip().replace(",", "")
            return int(text) if text.isdigit() else None

        decoded = []
        for item in meta:
            try:
                normalized = json.loads(item["normalized"] or "{}")
            except Exception:
                normalized = {}
            normalized = normalized if isinstance(normalized, dict) else {}
            details = normalized.get("details") if isinstance(normalized.get("details"), dict) else {}
            provider = str(item["provider"] or "").strip().casefold()
            decoded.append({"provider": provider, "provider_id": str(item["provider_id"] or "").strip(),
                            "normalized": normalized, "details": details})

        counts = []
        # This field exists on StashDB Performer, but not on the StashDB Studio type.
        if entity_type == "performer":
            for item in decoded:
                if item["provider"] not in {"stashdb", "stash_db"}:
                    continue
                details = item["details"]
                value = details.get("scene_count", item["normalized"].get("scene_count"))
                count = positive_or_zero_count(value)
                if count is not None:
                    counts.append({"provider": "StashDB", "count": count})
                    break

        # TPDB's entity resource does not itself publish a total; its scene list
        # does. Query only when the linked provider and token are available.
        tpdb_entry = next((item for item in decoded if item["provider"] in {"theporndb", "tpdb"}), None)
        if tpdb_entry:
            details = tpdb_entry["details"]
            explicit = None
            for key in ("scene_count", "scenes_count", "total_scenes", "video_count", "videos_count"):
                explicit = positive_or_zero_count(details.get(key, tpdb_entry["normalized"].get(key)))
                if explicit is not None:
                    break
            if explicit is not None:
                counts.append({"provider": "ThePornDB", "count": explicit})
            else:
                token = db.setting("tpdb_api_token")
                provider_id = tpdb_entry["provider_id"]
                if not provider_id:
                    provider_id = str(tpdb_entry["normalized"].get("id") or tpdb_entry["normalized"].get("_id") or "").strip()
                if not provider_id:
                    try:
                        provider_ids = json.loads(entity["provider_ids"] or "{}")
                    except Exception:
                        provider_ids = {}
                    if isinstance(provider_ids, dict):
                        provider_id = str(provider_ids.get("theporndb") or provider_ids.get("tpdb") or "").strip()
                if token and provider_id:
                    from ..metadata.providers import _cache_get, _cache_put, _tpdb_get
                    cache_key = f"entity:{entity_type}:career-scene-count:{provider_id}"
                    cached, hit = _cache_get(db, "tpdb", cache_key, max_age=24 * 60 * 60)
                    count = positive_or_zero_count(cached.get("count")) if hit and isinstance(cached, dict) else None
                    cache_valid = bool(hit and isinstance(cached, dict) and cached.get("checked"))
                    if not cache_valid:
                        try:
                            from urllib.parse import quote
                            endpoint_name = "performers" if entity_type == "performer" else "sites"
                            path = f"{endpoint_name}/{quote(provider_id, safe='')}/scenes?per_page=1&page=1"
                            result = await asyncio.to_thread(_tpdb_get, db, token, path)
                            meta_payload = result.get("meta") if isinstance(result, dict) else None
                            count = positive_or_zero_count(meta_payload.get("total")) if isinstance(meta_payload, dict) else None
                        except Exception:
                            count = None
                        # Cache unavailable results briefly too, so repeated profile renders
                        # cannot create a burst of provider requests during an outage.
                        _cache_put(db, "tpdb", cache_key, {"checked": True, "count": count})
                    if count is not None:
                        counts.append({"provider": "ThePornDB", "count": count})

        return {"entity_type": entity_type, "career_counts": counts}

    @router.get("/api/performers/{performer_id}/career-counts")
    async def performer_career_counts(performer_id: int, request: Request):
        return await _entity_career_scene_counts("performer", performer_id, request)

    @router.get("/api/studios/{studio_id}/career-counts")
    async def studio_career_counts(studio_id: int, request: Request):
        return await _entity_career_scene_counts("studio", studio_id, request)

    @router.get("/api/performers/{performer_id}/metadata")
    async def performer_metadata(performer_id: int, request: Request):
        require_auth(request)
        from ..core.resource_registry import ensure_unknown_performer_local_profile
        await asyncio.to_thread(ensure_unknown_performer_local_profile, db)
        with db.reading() as _rdb:
            row = _rdb.execute("SELECT * FROM performers WHERE id=?", (performer_id,)).fetchone()
            meta = _rdb.execute("SELECT provider,provider_id,normalized,fetched_at,updated_at FROM entity_metadata WHERE entity_type='performer' AND entity_id=? ORDER BY provider", (performer_id,)).fetchall()
        if not row: raise HTTPException(404, "Performer not found")
        if str(row["name"] or "").strip().casefold() == "unknown performer":
            return {"entity": dict(row), "providers": []}
        return {"entity": dict(row), "providers": [dict(x) for x in meta]}

    @router.get("/api/studios/{studio_id}/metadata")
    async def studio_metadata(studio_id: int, request: Request):
        require_auth(request)
        with db.reading() as _rdb:
            row = _rdb.execute("SELECT * FROM studios WHERE id=?", (studio_id,)).fetchone()
            meta = _rdb.execute("SELECT provider,provider_id,normalized,fetched_at,updated_at FROM entity_metadata WHERE entity_type='studio' AND entity_id=? ORDER BY provider", (studio_id,)).fetchall()
        if not row: raise HTTPException(404, "Studio not found")
        return {"entity": dict(row), "providers": [dict(x) for x in meta]}

    @router.get("/api/studios/{studio_id}/family")
    async def studio_family(studio_id: int, request: Request):
        """Return cached parent/child/sibling studio relationships without provider calls."""
        require_auth(request)
        with db.reading() as _rdb:
            current = _rdb.execute("SELECT id,name,aliases,provider_ids FROM studios WHERE id=?", (studio_id,)).fetchone()
            if not current:
                raise HTTPException(404, "Studio not found")
            cached = _rdb.execute(
                "SELECT s.id AS local_id,s.name AS local_name,em.provider,em.provider_id,em.normalized "
                "FROM entity_metadata em JOIN studios s ON s.id=em.entity_id "
                "WHERE em.entity_type='studio' ORDER BY s.name,em.provider"
            ).fetchall()

        def parse_record(item):
            try:
                normalized = json.loads(item["normalized"] or "{}")
            except Exception:
                normalized = {}
            if not isinstance(normalized, dict):
                normalized = {}
            details = normalized.get("details") if isinstance(normalized.get("details"), dict) else {}
            parent = details.get("parent", normalized.get("parent"))
            if isinstance(parent, str):
                parent = {"name": parent}
            if not isinstance(parent, dict):
                parent = {}
            provider = str(item["provider"] or "").strip().lower()
            root_id = str(normalized.get("id") or item["provider_id"] or "").strip()
            parent_id = str(parent.get("id") or parent.get("provider_id") or "").strip()
            parent_name = str(parent.get("name") or "").strip()
            return {
                "local_id": int(item["local_id"]), "name": str(item["local_name"] or ""),
                "provider": provider, "root_id": root_id,
                "parent_id": parent_id, "parent_name": parent_name,
            }

        records = [parse_record(item) for item in cached]
        current_records = [record for record in records if record["local_id"] == int(studio_id)]
        if not current_records:
            return {"studio_id": int(studio_id), "related": []}
        current_ids = {}
        parent_refs = {}
        for record in current_records:
            if record["root_id"]:
                current_ids.setdefault(record["provider"], set()).add(record["root_id"])
            if record["parent_id"] or record["parent_name"]:
                parent_refs[record["provider"]] = (record["parent_id"], record["parent_name"].casefold())

        matched = {}
        for candidate in records:
            if candidate["local_id"] == int(studio_id):
                continue
            provider = candidate["provider"]
            relation = ""
            if candidate["root_id"] and candidate["root_id"] in current_ids.get(provider, set()):
                relation = "Child label"
            elif provider in parent_refs:
                parent_id, parent_name = parent_refs[provider]
                if candidate["parent_id"] and parent_id:
                    if candidate["parent_id"] == parent_id:
                        relation = "Sister label"
                elif candidate["parent_name"] and parent_name and candidate["parent_name"].casefold() == parent_name:
                    relation = "Sister label"
            # Also include the local parent record itself where that parent has been linked.
            if not relation and candidate["root_id"] and candidate["root_id"] in {
                r["parent_id"] for r in current_records if r["provider"] == provider and r["parent_id"]
            }:
                relation = "Parent company"
            if not relation:
                continue
            existing = matched.get(candidate["local_id"])
            priority = {"Parent company": 0, "Child label": 1, "Sister label": 2}
            if existing is None or priority[relation] < priority[existing["relation"]]:
                matched[candidate["local_id"]] = {
                    "id": candidate["local_id"], "name": candidate["name"],
                    "relation": relation, "provider": candidate["provider"],
                }
        related = sorted(matched.values(), key=lambda x: ({"Parent company": 0, "Child label": 1, "Sister label": 2}.get(x["relation"], 9), x["name"].casefold()))
        return {"studio_id": int(studio_id), "related": related[:40]}


    # ------------------------------------------------------------------
    # SUBSECTION: Entity images
    # Image sync and serving.
    # ------------------------------------------------------------------
    @router.post("/api/entity-image-sync")
    async def entity_image_sync(request: Request):
        """Queue the unified performer/studio entity data refresh job."""
        require_auth(request)
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        return await _queue_entity_data_sync(data)

    @router.post("/api/performers/image-sync")
    async def performer_image_sync(request: Request):
        """Compatibility wrapper: create the unified performer-only entity data job."""
        require_auth(request)
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        return await _queue_entity_data_sync(data, scope_override="performers")

    @router.get("/api/performers/{performer_id}/image")
    async def performer_image(performer_id: int, request: Request):
        """Serve only an already-saved local image; page loads never fetch providers."""
        require_auth(request)
        from fastapi.responses import Response
        def _cached_image():
            with db.reading() as rdb:
                entity = rdb.execute("SELECT id,image FROM performers WHERE id=?", (int(performer_id),)).fetchone()
                if not entity:
                    return None, "missing", ""
                cached = rdb.execute("SELECT url,mime,data FROM performer_image_cache WHERE performer_id=?", (int(performer_id),)).fetchone()
            url = str(entity["image"] or "").strip()
            if cached and cached["data"] and (not url or str(cached["url"] or "") == url):
                return (bytes(cached["data"]), str(cached["mime"] or "image/jpeg")), "ok", url
            return None, "empty", url
        result, state, image_url = await asyncio.to_thread(_cached_image)
        if state == "missing": raise HTTPException(404, "Performer not found")
        if not result and image_url:
            try:
                from ..engines.thumbnail_cache import reconcile_entity_image_cache_from_disk_sync
                result = await asyncio.to_thread(reconcile_entity_image_cache_from_disk_sync, db, "performer", int(performer_id), image_url)
            except Exception:
                result = None
        if result:
            from ..core.image_derivatives import derive, requested_width
            data, mime = await asyncio.to_thread(derive, result[0], result[1], requested_width(request.query_params.get("w")))
            return Response(data, media_type=mime, headers={"Cache-Control":"public,max-age=86400"})
        raise HTTPException(404, "Performer image is not cached locally")

    @router.post("/api/studios/image-sync")
    async def studio_image_sync(request: Request):
        """Compatibility wrapper: create the unified studio-only entity data job."""
        require_auth(request)
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        return await _queue_entity_data_sync(data, scope_override="studios")

    _studio_image_auto_refresh_attempts: dict[tuple[int, str], float] = {}
    _studio_image_auto_refresh_locks: dict[int, asyncio.Lock] = {}
    _studio_image_auto_refresh_cooldown = 900.0

    @router.get("/api/studios/{studio_id}/image")
    async def studio_image(studio_id: int, request: Request):
        """Serve cached studio artwork, repairing a missing cache on demand."""
        require_auth(request)
        from fastapi.responses import Response
        def _cached_image():
            with db.reading() as rdb:
                entity = rdb.execute("SELECT id,image,image_locked,name FROM studios WHERE id=?", (int(studio_id),)).fetchone()
                if not entity:
                    return None, "missing", "", False, ""
                cached = rdb.execute("SELECT url,mime,data FROM studio_image_cache WHERE studio_id=?", (int(studio_id),)).fetchone()
            url = str(entity["image"] or "").strip()
            if cached and cached["data"] and (not url or str(cached["url"] or "") == url):
                return (bytes(cached["data"]), str(cached["mime"] or "image/jpeg")), "ok", url, bool(entity["image_locked"]), str(entity["name"] or "")
            return None, "empty", url, bool(entity["image_locked"]), str(entity["name"] or "")

        result, state, image_url, image_locked, studio_name = await asyncio.to_thread(_cached_image)
        if state == "missing": raise HTTPException(404, "Studio not found")

        if not result:
            # Only one cache repair/provider lookup per studio runs at a time.
            # This matters because cards and profile headers can request the
            # same image concurrently during navigation.
            lock = _studio_image_auto_refresh_locks.setdefault(int(studio_id), asyncio.Lock())
            async with lock:
                # A sibling request may have populated the cache while we waited.
                result, state, image_url, image_locked, studio_name = await asyncio.to_thread(_cached_image)
                if state == "missing": raise HTTPException(404, "Studio not found")
                attempt_key = (int(studio_id), image_url)
                now = time.monotonic()
                last_attempt = _studio_image_auto_refresh_attempts.get(attempt_key, 0.0)
                should_attempt = not result and (now - last_attempt >= _studio_image_auto_refresh_cooldown)
                if should_attempt:
                    _studio_image_auto_refresh_attempts[attempt_key] = now
                    if len(_studio_image_auto_refresh_attempts) > 2000:
                        cutoff = now - 3600.0
                        for old_key, old_time in list(_studio_image_auto_refresh_attempts.items()):
                            if old_time < cutoff:
                                _studio_image_auto_refresh_attempts.pop(old_key, None)
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(str(image_url).strip())
                        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                            # A URL exists, but its local cache is absent or stale.
                            from ..engines.thumbnail_cache import prefetch_entity_image
                            await prefetch_entity_image(db, "studio", int(studio_id), str(image_url))
                        elif not image_locked:
                            # The studio has no image URL yet. Its already-linked
                            # provider metadata or provider records may still have
                            # artwork. Refresh once with cooldown rather than
                            # leaving a permanent placeholder because no <img>
                            # source existed in the directory response.
                            from ..engines.studio_image_sync import refresh_studio_image
                            await refresh_studio_image(db, int(studio_id))
                    except Exception as exc:
                        try:
                            db.log(f"Studio image auto-repair failed for {int(studio_id)} ({studio_name}): {type(exc).__name__}: {exc}", "WARN")
                        except Exception:
                            pass
                    result, state, image_url, image_locked, studio_name = await asyncio.to_thread(_cached_image)

        if not result and image_url:
            try:
                from ..engines.thumbnail_cache import reconcile_entity_image_cache_from_disk_sync
                result = await asyncio.to_thread(reconcile_entity_image_cache_from_disk_sync, db, "studio", int(studio_id), image_url)
            except Exception:
                result = None
        if result:
            # Studio artwork is mutable: a selected image can keep the same URL
            # while its local bytes are refreshed. Revalidate instead of letting
            # browsers keep yesterday's image after an edit or sync.
            from ..core.image_derivatives import derive, requested_width
            data, mime = await asyncio.to_thread(derive, result[0], result[1], requested_width(request.query_params.get("w")))
            return Response(data, media_type=mime, headers={"Cache-Control":"private,no-cache,must-revalidate"})
        raise HTTPException(404, "Studio image is not cached locally")

    _studio_backfill_state = {"last": 0.0, "runs": 0, "changed": 0}


    # ------------------------------------------------------------------
    # SUBSECTION: Studio pages and taxonomy
    # Studios, their videos and tags.
    # ------------------------------------------------------------------
    def _backfill_studios_from_media(force: bool = False):
        """Materialize newly discovered site relationships without running a
        full-library repair on every /api/studios request.

        The old route ran the full scan on every request and repeatedly logged
        hundreds of apparent repairs, adding synchronous SQLite work to the
        HTTP event loop. A short cooldown plus a cheap missing-link probe keeps
        new metadata discoverable without repeatedly blocking the UI/playback
        process.
        """
        now = time.monotonic()
        if not force and now - float(_studio_backfill_state.get("last") or 0.0) < 300.0:
            return
        with db.lock:
            missing = db.conn.execute(
                "SELECT 1 FROM media m WHERE ((m.site_name IS NOT NULL AND TRIM(m.site_name)<>'') "
                "OR (m.provider_payload IS NOT NULL AND TRIM(m.provider_payload)<>'')) "
                "AND NOT EXISTS (SELECT 1 FROM video_studios vs WHERE vs.video_id=m.id) LIMIT 1"
            ).fetchone()
        if not missing:
            _studio_backfill_state["last"] = now
            return
        _studio_backfill_state["last"] = now
        _studio_backfill_state["runs"] += 1
        changed = 0
        with db.lock:
            rows = db.conn.execute(
                "SELECT id,site_name,site_url,provider_payload FROM media "
                "WHERE (site_name IS NOT NULL AND TRIM(site_name)<>'') "
                "OR (provider_payload IS NOT NULL AND TRIM(provider_payload)<>'')"
            ).fetchall()
            for row in rows:
                name = str(row["site_name"] or "").strip()
                url = str(row["site_url"] or "").strip()
                if not name and row["provider_payload"]:
                    try:
                        payload=json.loads(row["provider_payload"] or "{}")
                        candidates=[payload.get("studio"),payload.get("site"),payload.get("studio_name"),payload.get("site_name")]
                        for cand in candidates:
                            if isinstance(cand,dict): cand=cand.get("name")
                            if cand:
                                name=str(cand).strip(); break
                        if not url:
                            u=payload.get("studio_url") or payload.get("site_url") or payload.get("url")
                            if u: url=str(u).strip()
                    except Exception:
                        pass
                if not name:
                    continue
                from ..engines.entity_identity import upsert_studio
                sid = upsert_studio(db, name)
                if not sid:
                    continue
                before=db.conn.total_changes
                db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id) VALUES(?,?)", (int(row["id"]),sid))
                if url:
                    db.conn.execute("UPDATE media SET site_url=COALESCE(NULLIF(site_url,''),?) WHERE id=?", (url,int(row["id"])))
                if db.conn.total_changes!=before: changed+=1
            db.conn.commit()
        _studio_backfill_state["changed"] = changed
        if changed:
            db.log(f"Studios/Sites backfill repaired {changed} media relationships")

    @router.get("/api/studios")
    async def list_studios(request: Request):
        """GET /api/studios: List studios using local, read-only catalogue data."""
        require_auth(request)
        await asyncio.to_thread(_backfill_studios_from_media)
        q = str(request.query_params.get("q") or "").strip()
        brief = str(request.query_params.get("brief") or "") == "1"
        try:
            limit = max(1, min(5000, int(request.query_params.get("limit", "5000"))))
        except ValueError:
            limit = 5000
        def _read_studios():
            with db.reading() as rdb:
                if q:
                    like = f"%{q}%"
                    rows = rdb.execute(
                        "SELECT s.*, COUNT(DISTINCT CASE WHEN vs.video_id IN (SELECT id FROM archive_x_library_media) THEN vs.video_id END) video_count, "
                        "(SELECT MAX(sic.updated_at) FROM studio_image_cache sic WHERE sic.studio_id=s.id) AS image_cache_updated_at, "
                        "EXISTS(SELECT 1 FROM studio_image_cache sic WHERE sic.studio_id=s.id AND sic.data IS NOT NULL AND length(sic.data)>0 AND (COALESCE(s.image,'')='' OR COALESCE(sic.url,'')=COALESCE(s.image,''))) AS has_cached_image FROM studios s "
                        "LEFT JOIN video_studios vs ON vs.studio_id=s.id "
                        "WHERE s.name LIKE ? OR COALESCE(s.normalized_name,'') LIKE ? "
                        "GROUP BY s.id HAVING video_count > 0 OR s.name='Unknown Studio' ORDER BY (s.name='Unknown Studio') DESC, video_count DESC, s.name LIMIT ?",
                        (like, f"%{db._norm_search(q)}%", limit)).fetchall()
                else:
                    rows = rdb.execute(
                        "SELECT s.*, COUNT(DISTINCT CASE WHEN vs.video_id IN (SELECT id FROM archive_x_library_media) THEN vs.video_id END) video_count, "
                        "(SELECT MAX(sic.updated_at) FROM studio_image_cache sic WHERE sic.studio_id=s.id) AS image_cache_updated_at, "
                        "EXISTS(SELECT 1 FROM studio_image_cache sic WHERE sic.studio_id=s.id AND sic.data IS NOT NULL AND length(sic.data)>0 AND (COALESCE(s.image,'')='' OR COALESCE(sic.url,'')=COALESCE(s.image,''))) AS has_cached_image FROM studios s "
                        "LEFT JOIN video_studios vs ON vs.studio_id=s.id "
                        "GROUP BY s.id HAVING video_count > 0 OR s.name='Unknown Studio' ORDER BY (s.name='Unknown Studio') DESC, video_count DESC, s.name LIMIT ?", (limit,)).fetchall()
            result = [dict(r) for r in rows]
            if brief:
                result = [{k: d.get(k) for k in ("id", "name", "video_count", "image", "image_cache_updated_at", "has_cached_image")} for d in result]
            return result
        return await asyncio.to_thread(_read_studios)

    @router.get("/api/studios/{studio_id}/videos")
    async def studio_videos(studio_id: int, request: Request, performer_id: int | None = None):
        """Return catalogue videos linked to a site/studio, mirroring the performer detail API."""
        require_auth(request)
        with db.reading() as _rdb:
            studio = _rdb.execute(
                "SELECT s.*, (SELECT MAX(sic.updated_at) FROM studio_image_cache sic WHERE sic.studio_id=s.id) "
                "AS image_cache_updated_at FROM studios s WHERE s.id=?", (studio_id,)
            ).fetchone()
            if not studio:
                raise HTTPException(404, "Site not found")
            studio = dict(studio)
            studio["favorite"] = bool(_rdb.execute("SELECT 1 FROM entity_favorites WHERE entity_type='studio' AND entity_id=?", (studio_id,)).fetchone())
            rows = _rdb.execute(
                "SELECT m.*, c.title AS channel_title, "
                "GROUP_CONCAT(DISTINCT p.name) performers, "
                "GROUP_CONCAT(DISTINCT lp.name) local_performers, "
                "GROUP_CONCAT(DISTINCT s2.name) studios, "
                "GROUP_CONCAT(DISTINCT t.name) tags "
                 "FROM archive_x_library_media m "
                "LEFT JOIN channels c ON c.telegram_id=m.source_channel "
                "LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id "
                "LEFT JOIN local_video_performers lvp ON lvp.video_id=m.id LEFT JOIN performers lp ON lp.id=lvp.performer_id "
                "LEFT JOIN video_studios vs2 ON vs2.video_id=m.id LEFT JOIN studios s2 ON s2.id=vs2.studio_id "
                "LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id "
                "WHERE EXISTS (SELECT 1 FROM video_studios vs WHERE vs.video_id=m.id AND vs.studio_id=?) "
                + ("AND (EXISTS (SELECT 1 FROM video_performers rvp WHERE rvp.video_id=m.id AND rvp.performer_id=?) "
                   "OR EXISTS (SELECT 1 FROM local_video_performers rlv WHERE rlv.video_id=m.id AND rlv.performer_id=?)) " if performer_id else "")
                 + "AND COALESCE(m.status,'')<>'SOURCE_DELETED' "
                 + "AND (CAST(m.source_channel AS TEXT) GLOB '-[0-9]*' OR (CAST(m.source_channel AS TEXT)<>'' AND CAST(m.source_channel AS TEXT) NOT GLOB '*[^0-9]*')) "
                 + "GROUP BY m.id ORDER BY m.upload_date DESC",
                (studio_id, performer_id, performer_id) if performer_id else (studio_id,)).fetchall()

            site_url = _rdb.execute(
                "SELECT site_url FROM media "
                "WHERE site_url IS NOT NULL AND TRIM(site_url)<>'' "
                "AND id IN (SELECT video_id FROM video_studios WHERE studio_id=?) "
                + ("AND id IN (SELECT video_id FROM video_performers WHERE performer_id=?) " if performer_id else "")
                + "ORDER BY updated_at DESC LIMIT 1",
                (studio_id, performer_id) if performer_id else (studio_id,)).fetchone()

        videos=[]
        for r0 in rows:
            d=dict(r0)
            d["performers"]=list(dict.fromkeys([x for x in (d.get("performers") or "").split(",") if x] + [x for x in (d.get("local_performers") or "").split(",") if x]))
            d["local_performers"]=[x for x in (d.get("local_performers") or "").split(",") if x]
            d["studios"]=[x for x in (d.get("studios") or "").split(",") if x]
            d["studio"]=d["studios"][0] if d["studios"] else ""
            d["tags"]=[x for x in (d.get("tags") or "").split(",") if x]
            d["media_id"]=d.get("id")   # SQLite row id, needed for favourites and other row-level actions
            d["_cid"]=d.get("source_channel")
            d["id"]=d.get("source_message_id")
            d["name"]=d.get("title") or d.get("filename") or f"video {d['id']}"
            d["thumbnail_id"]=d.get("thumbnail_message_id") or d.get("source_message_id")
            videos.append(d)
        result={"site":dict(studio),"videos":videos,"site_url":str(site_url["site_url"] or "").strip() if site_url else ""}
        return result

    @router.get("/api/tag-taxonomy")
    async def tag_taxonomy(request: Request):
        """Catalogue-wide smart tag visibility, ranking, and derived categories."""
        require_auth(request)
        force = str(request.query_params.get("refresh") or "") == "1"
        now = time.monotonic()
        cached = tag_taxonomy_cache.get("value")
        if not force and cached is not None and (now - float(tag_taxonomy_cache.get("at") or 0.0)) < 30.0:
            return cached
        from ..metadata.tag_taxonomy import build_tag_taxonomy, normalize_tag_key
        def _build_saved_taxonomy():
            with db.reading() as rdb:
                total_row = rdb.execute("SELECT COUNT(DISTINCT id) AS n FROM archive_x_library_media").fetchone()
                total_videos = int((total_row["n"] if total_row else 0) or 0)
                tag_rows = list(rdb.execute(
                    "SELECT t.id AS tag_id, t.name AS name, COUNT(DISTINCT vt.video_id) AS video_count "
                    "FROM tags t JOIN video_tags vt ON vt.tag_id=t.id "
                    "JOIN archive_x_library_media m ON m.id=vt.video_id "
                    "GROUP BY t.id, t.name ORDER BY t.name"
                ).fetchall())
                normalized_ids = {}
                for row in tag_rows:
                    normalized_ids.setdefault(normalize_tag_key(row["name"]), []).append(int(row["tag_id"]))
                collision_ids = [tag_id for ids in normalized_ids.values() if len(ids) > 1 for tag_id in ids]
                for offset in range(0, len(collision_ids), 500):
                    batch = collision_ids[offset:offset + 500]
                    placeholders = ",".join("?" for _ in batch)
                    tag_rows.extend(rdb.execute(
                        f"SELECT t.name AS name, vt.video_id AS video_id FROM tags t "
                        f"JOIN video_tags vt ON vt.tag_id=t.id JOIN archive_x_library_media m ON m.id=vt.video_id "
                        f"WHERE t.id IN ({placeholders}) ORDER BY t.name, vt.video_id", batch).fetchall())
            return build_tag_taxonomy(total_videos, tag_rows)
        result = await asyncio.to_thread(_build_saved_taxonomy)
        tag_taxonomy_cache["at"] = now
        tag_taxonomy_cache["value"] = result
        return result
