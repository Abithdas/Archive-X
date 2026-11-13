"""Explicit registration for the video and photo library routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Photo face suggestion helpers
#       - Photo library listing
#       - Photo face groups
#       - Photo scanning
#       - Photo files
#       - Video library actions
#       - Video mapping
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request


def register_library_routes(
    router: APIRouter,
    *,
    db,
    jobs,
    telegram,
    config,
    require_auth,
    _queue_catalogue_caption_sync,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Photo face suggestion helpers
    # Embeddings and suggestion building.
    # ------------------------------------------------------------------
    """Register the video and photo library routes."""
    def _photo_metadata_performer_suggestions(rdb, cluster_ids, performer_rows, limit=3):
        """Suggest names from photo text metadata only; never compare face vectors to named people."""
        import json as _json
        import re as _re
        import unicodedata as _unicodedata
        ids = list(dict.fromkeys(int(value) for value in cluster_ids if int(value) > 0))[:700]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        sources = rdb.execute(
            f"""SELECT DISTINCT COALESCE(p.filename,'') AS filename,
                               COALESCE(p.caption,'') AS caption,
                               COALESCE(c.title,'') AS channel_title
                        FROM photo_face_detections d
                        JOIN photo_library_items p ON p.id=d.photo_id
                        LEFT JOIN channels c ON c.telegram_id=p.channel_id
                        WHERE d.group_id IN ({marks})
                        ORDER BY p.message_date DESC,p.id DESC LIMIT 120""", ids
        ).fetchall()
        def normalize(value):
            value = _unicodedata.normalize('NFKC', str(value or '')).casefold()
            return _re.sub(r"[\W_]+", " ", value, flags=_re.UNICODE).strip()
        contexts = {
            'caption': normalize(' '.join(str(row['caption'] or '') for row in sources)),
            'filename': normalize(' '.join(str(row['filename'] or '') for row in sources)),
            'channel name': normalize(' '.join(str(row['channel_title'] or '') for row in sources)),
        }
        field_scores = [('caption', 100), ('filename', 90), ('channel name', 75)]
        candidates = []
        for person in performer_rows:
            performer_id = int(person['id'])
            name = str(person['name'] or '').strip()
            if not name:
                continue
            terms = [(name, 0)]
            raw_aliases = str(person['aliases'] or '').strip()
            if raw_aliases:
                try:
                    decoded = _json.loads(raw_aliases)
                    aliases = decoded if isinstance(decoded, list) else []
                except Exception:
                    aliases = _re.split(r"[,;|]", raw_aliases)
                terms.extend((str(alias or '').strip(), -10) for alias in aliases)
            best = None
            for term, penalty in terms:
                needle = normalize(term)
                if not needle or len(needle.replace(' ', '')) < 5:
                    continue
                phrase = f" {needle} "
                for source_name, base in field_scores:
                    if phrase in f" {contexts[source_name]} ":
                        score = base + penalty
                        if best is None or score > best[0]:
                            best = (score, source_name, term)
            if best:
                score, source_name, matched = best
                candidates.append({
                    'id': performer_id, 'name': name,
                    'image_url': f"/api/performers/{performer_id}/image",
                    'basis': source_name,
                    'source': 'metadata',
                    'reason': f"Exact text match in {source_name}",
                    'metadata_score': score,
                })
        candidates.sort(key=lambda value: (-int(value['metadata_score']), str(value['name']).casefold()))
        # Keep one suggestion per performer and label the reason clearly so this is
        # not presented as a face-identity confidence score.
        return candidates[:max(1, min(5, int(limit)))]

    _PHOTO_CONFIRMED_SIMILARITY_SUGGESTION_THRESHOLD = 0.52

    def _photo_decode_embedding(raw):
        import json as _json
        import math as _math
        try:
            values = _json.loads(raw or "[]") if isinstance(raw, (str, bytes, bytearray)) else raw
            if not isinstance(values, (list, tuple)) or len(values) != 128:
                return None
            vector = [float(value) for value in values]
            if not all(_math.isfinite(value) for value in vector):
                return None
            norm = _math.sqrt(sum(value * value for value in vector))
            if not norm:
                return None
            return [value / norm for value in vector]
        except Exception:
            return None

    def _photo_confirmed_reference_index(rdb, max_vectors_per_performer=32):
        """Build candidate references only from face groups a user has already confirmed.

        This does not compare to profile/provider artwork. It reuses persisted embeddings
        from prior user-confirmed groups to suggest likely matches for review; it never
        commits an assignment automatically.
        """
        refs = {}
        try:
            rows = rdb.execute(
                """WITH ranked AS (
                       SELECT d.group_id,g.performer_id,d.embedding_json,d.id AS detection_id,
                              ROW_NUMBER() OVER(PARTITION BY d.group_id ORDER BY d.id DESC) AS rn
                       FROM photo_face_groups g
                       JOIN photo_face_detections d ON d.group_id=g.id
                       JOIN photo_face_analysis a ON a.photo_id=d.photo_id
                       WHERE g.performer_id IS NOT NULL AND a.status='COMPLETE'
                   )
                   SELECT group_id,performer_id,embedding_json,detection_id
                   FROM ranked WHERE rn<=8 ORDER BY detection_id DESC LIMIT 12000"""
            ).fetchall()
        except Exception:
            return refs
        for row in rows:
            performer_id = int(row['performer_id'])
            entry = refs.setdefault(performer_id, {'vectors': [], 'group_ids': set()})
            if len(entry['vectors']) >= max_vectors_per_performer:
                continue
            vector = _photo_decode_embedding(row['embedding_json'])
            if vector is None:
                continue
            entry['vectors'].append(vector)
            entry['group_ids'].add(int(row['group_id']))
        return refs

    _NAMED_FACE = "EXISTS (SELECT 1 FROM photo_face_detections fd JOIN photo_face_groups fg ON fg.id=fd.group_id WHERE fd.photo_id=p.id AND fg.performer_id IS NOT NULL)"
    _ANY_FACE = "EXISTS (SELECT 1 FROM photo_face_detections fd WHERE fd.photo_id=p.id)"
    _ANALYSED = "EXISTS (SELECT 1 FROM photo_face_analysis a WHERE a.photo_id=p.id AND a.status='COMPLETE')"
    # Which photos have no named person, and why: faces nobody has named, no face found, or not analysed yet.
    _PHOTO_FACE_SCOPES = {
        "named": _NAMED_FACE,
        "unnamed": f"({_ANY_FACE} AND NOT {_NAMED_FACE})",
        "no_face": f"({_ANALYSED} AND NOT {_ANY_FACE})",
        "pending": f"(NOT {_ANALYSED})",
    }

    _PROFILE_PICTURE_MATCH_THRESHOLD = 0.40   # SFace cosine; OpenCV documents ~0.363 as the same-person cut-off

    def _photo_profile_picture_suggestions(rdb, vectors, performer_rows, rejected_ids, limit=3):
        """Rank performers whose profile-picture face resembles this group's faces. A suggestion only; the user confirms it."""
        if not vectors:
            return []
        try:
            import numpy as _np
            rows = rdb.execute("SELECT performer_id,embedding_json FROM performer_face_embeddings WHERE embedding_json<>'[]'").fetchall()
            names = {int(r['id']): str(r['name'] or '').strip() for r in performer_rows}
            ids, mats = [], []
            for r in rows:
                v = _photo_decode_embedding(r['embedding_json'])
                pid = int(r['performer_id'])
                if v is not None and names.get(pid) and pid not in rejected_ids:
                    ids.append(pid); mats.append(v)
            if not ids:
                return []
            target = _np.asarray(vectors[:32], dtype=_np.float32)
            scores = (target @ _np.asarray(mats, dtype=_np.float32).T).max(axis=0)
            order = _np.argsort(-scores)[:max(1, min(5, int(limit)))]
            return [{
                'id': ids[i], 'name': names[ids[i]], 'image_url': f"/api/performers/{ids[i]}/image",
                'source': 'profile_image', 'basis': 'profile picture',
                'reason': "Face resembles this performer's profile picture",
                'similarity_score': round(float(scores[i]), 4),
            } for i in order if float(scores[i]) >= _PROFILE_PICTURE_MATCH_THRESHOLD]
        except Exception:
            return []

    def _photo_group_embeddings(rdb, cluster_ids, max_per_group=16):
        ids = list(dict.fromkeys(int(value) for value in cluster_ids if int(value) > 0))[:700]
        if not ids:
            return []
        marks = ','.join('?' for _ in ids)
        try:
            rows = rdb.execute(
                f"""WITH ranked AS (
                       SELECT d.group_id,d.embedding_json,d.id,
                              ROW_NUMBER() OVER(PARTITION BY d.group_id ORDER BY d.id DESC) AS rn
                       FROM photo_face_detections d
                       JOIN photo_face_analysis a ON a.photo_id=d.photo_id
                       WHERE a.status='COMPLETE' AND d.group_id IN ({marks})
                   )
                   SELECT embedding_json FROM ranked WHERE rn<=? ORDER BY id DESC""",
                [*ids, int(max_per_group)],
            ).fetchall()
        except Exception:
            return []
        return [vector for row in rows if (vector := _photo_decode_embedding(row['embedding_json'])) is not None]

    def _photo_confirmed_group_suggestions(target_vectors, reference_index, performer_rows, rejected_ids, limit=3):
        """Rank performers from prior confirmed face groups; users must explicitly confirm.

        The numeric score is embedding similarity, not an identity confidence/probability.
        """
        if not target_vectors or not reference_index:
            return []
        try:
            import numpy as _np
            target_matrix = _np.asarray(target_vectors[:32], dtype=_np.float32)
            if target_matrix.ndim != 2 or target_matrix.shape[1] != 128:
                return []
            person_names = {int(row['id']): str(row['name'] or '').strip() for row in performer_rows}
            ranked = []
            for performer_id, entry in reference_index.items():
                if performer_id in rejected_ids or not person_names.get(performer_id) or not entry['vectors']:
                    continue
                reference_matrix = _np.asarray(entry['vectors'], dtype=_np.float32)
                similarities = target_matrix @ reference_matrix.T
                score = float(_np.max(similarities)) if similarities.size else -1.0
                if score < _PHOTO_CONFIRMED_SIMILARITY_SUGGESTION_THRESHOLD:
                    continue
                ranked.append({
                    'id': performer_id,
                    'name': person_names[performer_id],
                    'image_url': f"/api/performers/{performer_id}/image",
                    'source': 'confirmed_assignment',
                    'basis': 'previously confirmed face groups',
                    'reason': f"Visually similar to {len(entry['group_ids'])} face group(s) you previously confirmed",
                    'similarity_score': round(score, 4),
                    'reference_group_count': len(entry['group_ids']),
                })
            ranked.sort(key=lambda value: (-float(value['similarity_score']), str(value['name']).casefold()))
            return ranked[:max(1, min(3, int(limit)))]
        except Exception:
            return []


    # ------------------------------------------------------------------
    # SUBSECTION: Photo library listing
    # Channels and photo lists.
    # ------------------------------------------------------------------
    @router.get("/api/photos/channels")
    async def photo_library_channels(request: Request):
        """Channel cards for the Photos library, including image counts and scan state."""
        require_auth(request)
        def _read():
            with db.reading() as rdb:
                rows = rdb.execute(
                    """SELECT c.telegram_id,c.title,c.username,c.image,c.updated_at,
                              COUNT(p.id) AS photo_count,
                              COALESCE(s.status,'NEVER') AS photo_scan_status,
                              s.last_scan_at AS photo_last_scan_at,
                              COALESCE(s.messages_scanned,0) AS photo_messages_scanned,
                              COALESCE(s.photos_found,0) AS photo_photos_found,
                              COALESCE(s.last_error,'') AS photo_scan_error
                       FROM channels c
                       LEFT JOIN photo_library_items p ON p.channel_id=c.telegram_id
                       LEFT JOIN photo_library_scan_state s ON s.channel_id=c.telegram_id
                       GROUP BY c.telegram_id,c.title,c.username,c.image,c.updated_at,s.status,s.last_scan_at,
                                s.messages_scanned,s.photos_found,s.last_error
                       ORDER BY c.title COLLATE NOCASE"""
                ).fetchall()
                orphan = rdb.execute(
                    """SELECT p.channel_id AS telegram_id,COUNT(*) AS photo_count
                       FROM photo_library_items p
                       WHERE NOT EXISTS(SELECT 1 FROM channels c WHERE c.telegram_id=p.channel_id)
                       GROUP BY p.channel_id"""
                ).fetchall()
            out = [dict(row) for row in rows]
            for row in orphan:
                item = dict(row)
                item.update({"title": "Unknown channel", "username": "", "image": "", "updated_at": "",
                             "photo_scan_status": "UNKNOWN", "photo_last_scan_at": None,
                             "photo_messages_scanned": 0, "photo_photos_found": item.get("photo_count", 0),
                             "photo_scan_error": ""})
                out.append(item)
            return out
        return await asyncio.to_thread(_read)


    @router.get("/api/photos")
    def photo_library_list(request: Request):
        """Return only indexed photos; video media records are never included."""
        require_auth(request)
        q = request.query_params
        try:
            page = max(0, int(q.get("page", "0") or 0))
            per = max(1, min(1000, int(q.get("per", "48") or 48)))
        except ValueError:
            raise HTTPException(400, "page and per must be integers")
        raw_channels = [value.strip() for value in str(q.get("channels", "") or "").split(",") if value.strip()]
        channel_ids = list(dict.fromkeys(raw_channels))[:250]
        search = str(q.get("q", "") or "").strip()[:200]
        face_group_raw = str(q.get("face_group_id", "") or "").strip()
        face_group_id = None
        if face_group_raw:
            try:
                face_group_id = int(face_group_raw)
                if face_group_id <= 0: raise ValueError
            except ValueError:
                raise HTTPException(400, "face_group_id must be a positive integer")
        face_performer_raw = str(q.get("face_performer_id", "") or "").strip()
        face_performer_id = None
        if face_performer_raw:
            try:
                face_performer_id = int(face_performer_raw)
                if face_performer_id <= 0: raise ValueError
            except ValueError:
                raise HTTPException(400, "face_performer_id must be a positive integer")
        raw_face_group_ids = [value.strip() for value in str(q.get("face_group_ids", "") or "").split(",") if value.strip()]
        face_group_ids = []
        for value in raw_face_group_ids[:300]:
            try:
                parsed = int(value)
                if parsed > 0 and parsed not in face_group_ids: face_group_ids.append(parsed)
            except ValueError:
                raise HTTPException(400, "face_group_ids must be comma-separated positive integers")
        sort = str(q.get("sort", "date_desc") or "date_desc")
        sort_map = {
            "date_desc": "COALESCE(NULLIF(p.message_date,''),p.created_at) DESC,p.message_id DESC",
            "date_asc": "COALESCE(NULLIF(p.message_date,''),p.created_at) ASC,p.message_id ASC",
            "name_asc": "p.filename COLLATE NOCASE ASC,p.message_id DESC",
            "size_desc": "p.size DESC,p.message_id DESC",
        }
        if sort not in sort_map:
            sort = "date_desc"
        where, params = ["1=1"], []
        if channel_ids:
            marks = ",".join("?" for _ in channel_ids)
            where.append(f"p.channel_id IN ({marks})")
            params.extend(channel_ids)
        if search:
            where.append("(p.filename LIKE ? OR p.caption LIKE ? OR c.title LIKE ?)")
            params.extend([f"%{search}%"] * 3)
        if str(q.get("favorites", "") or "").strip().lower() in {"1", "true", "yes"}:
            raw_favorites = [value.strip() for value in str(q.get("favorite_ids", "") or "").split(",") if value.strip()]
            favorite_ids: list[int] = []
            for value in raw_favorites[:900]:
                try:
                    parsed = int(value)
                    if parsed > 0 and parsed not in favorite_ids:
                        favorite_ids.append(parsed)
                except ValueError:
                    continue
            if favorite_ids:
                marks = ",".join("?" for _ in favorite_ids)
                where.append(f"p.id IN ({marks})")
                params.extend(favorite_ids)
            else:
                where.append("0=1")
        if face_performer_id is not None:
            where.append("EXISTS (SELECT 1 FROM photo_face_detections fd JOIN photo_face_groups fg ON fg.id=fd.group_id WHERE fd.photo_id=p.id AND fg.performer_id=?)")
            params.append(face_performer_id)
        elif face_group_ids:
            marks = ",".join("?" for _ in face_group_ids)
            where.append(f"EXISTS (SELECT 1 FROM photo_face_detections fd WHERE fd.photo_id=p.id AND fd.group_id IN ({marks}))")
            params.extend(face_group_ids)
        elif face_group_id is not None:
            where.append("EXISTS (SELECT 1 FROM photo_face_detections fd WHERE fd.photo_id=p.id AND fd.group_id=?)")
            params.append(face_group_id)
        from ..engines.photo_content_tags import TAG_LABELS as _CONTENT_TAGS
        for content_tag in [t.strip() for t in str(q.get("content_tags", "") or "").split(",") if t.strip()][:9]:
            if content_tag in _CONTENT_TAGS:   # every ticked tag must be present
                where.append("EXISTS (SELECT 1 FROM photo_content_tags ct WHERE ct.photo_id=p.id AND ct.tag=?)")
                params.append(content_tag)
        face_scope = str(q.get("face_scope", "") or "").strip().lower()
        if face_scope in _PHOTO_FACE_SCOPES:
            where.append(_PHOTO_FACE_SCOPES[face_scope])
        w = " AND ".join(where)
        with db.reading() as rdb:
            total = int(rdb.execute(
                f"SELECT COUNT(*) AS n FROM photo_library_items p LEFT JOIN channels c ON c.telegram_id=p.channel_id WHERE {w}", params
            ).fetchone()["n"] or 0)
            rows = rdb.execute(
                f"""SELECT p.id,p.channel_id,p.message_id,p.filename,p.caption,p.mime_type,p.size,p.width,p.height,
                           p.message_date,p.source_kind,p.updated_at,COALESCE(c.title,'Unknown channel') AS channel_title
                    FROM photo_library_items p LEFT JOIN channels c ON c.telegram_id=p.channel_id
                    WHERE {w} ORDER BY {sort_map[sort]} LIMIT ? OFFSET ?""",
                params + [per, page * per],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            cid = str(item.get("channel_id") or "")
            mid = int(item.get("message_id") or 0)
            cache_key = str(item.get("updated_at") or "0").replace(":", "")
            item["thumbnail_url"] = f"/api/photos/{cid}/{mid}/thumbnail?v={cache_key}"
            item["preview_url"] = f"/api/photos/{cid}/{mid}/preview?v={cache_key}"
            item["image_url"] = f"/api/photos/{cid}/{mid}/image?v={cache_key}"
            items.append(item)
        return {"ok": True, "items": items, "total": total, "page": page, "per": per, "sort": sort}



    # ------------------------------------------------------------------
    # SUBSECTION: Photo face groups
    # Listing, assigning, merging and splitting.
    # ------------------------------------------------------------------
    @router.get("/api/photos/faces/groups")
    async def photo_face_groups_list(request: Request):
        """List image-derived anonymous face clusters and available manual performer assignments."""
        require_auth(request)
        raw_channels = [value.strip() for value in str(request.query_params.get("channels", "") or "").split(",") if value.strip()]
        channel_ids = list(dict.fromkeys(raw_channels))[:250]
        where, params = [], []
        if channel_ids:
            marks = ",".join("?" for _ in channel_ids)
            where.append(f"pi.channel_id IN ({marks})")
            params.extend(channel_ids)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with db.reading() as rdb:
            rows = rdb.execute(
                f"""SELECT CASE WHEN g.performer_id IS NOT NULL THEN -g.performer_id ELSE g.id END AS id,
                           CASE WHEN g.performer_id IS NOT NULL THEN COALESCE(pr.name,MIN(g.label)) ELSE MIN(g.label) END AS label,
                           g.performer_id,COALESCE(pr.name,'') AS performer_name,
                           GROUP_CONCAT(DISTINCT g.id) AS cluster_ids,
                           MIN(d.id) AS representative_detection_id,
                           COUNT(DISTINCT d.photo_id) AS photo_count,COUNT(d.id) AS face_count,
                           GROUP_CONCAT(DISTINCT pi.channel_id) AS channel_ids,
                           COALESCE(MAX(g.updated_at),'') AS updated_at,
                           GROUP_CONCAT(DISTINCT d.id) AS detection_ids
                    FROM photo_face_groups g
                    JOIN photo_face_detections d ON d.group_id=g.id
                    JOIN photo_library_items pi ON pi.id=d.photo_id
                    LEFT JOIN performers pr ON pr.id=g.performer_id
                    {clause}
                    GROUP BY CASE WHEN g.performer_id IS NOT NULL THEN -g.performer_id ELSE g.id END,g.performer_id,pr.name
                    ORDER BY CASE WHEN COALESCE(pr.name,'')<>'' THEN pr.name ELSE MIN(g.label) END COLLATE NOCASE,MIN(g.id)""", params
            ).fetchall()
            if channel_ids:
                marks = ",".join("?" for _ in channel_ids)
                analysis_row = rdb.execute(
                    f"""SELECT COUNT(*) AS total,
                               SUM(CASE WHEN a.status='COMPLETE' THEN 1 ELSE 0 END) AS analyzed,
                               SUM(CASE WHEN a.status='FAILED' THEN 1 ELSE 0 END) AS failed
                        FROM photo_library_items p LEFT JOIN photo_face_analysis a ON a.photo_id=p.id
                        WHERE p.channel_id IN ({marks})""", channel_ids
                ).fetchone()
            else:
                analysis_row = rdb.execute(
                    """SELECT COUNT(*) AS total,
                               SUM(CASE WHEN a.status='COMPLETE' THEN 1 ELSE 0 END) AS analyzed,
                               SUM(CASE WHEN a.status='FAILED' THEN 1 ELSE 0 END) AS failed
                        FROM photo_library_items p LEFT JOIN photo_face_analysis a ON a.photo_id=p.id"""
                ).fetchone()
        groups = []
        for row in rows:
            item = dict(row)
            item["id"] = int(item["id"])
            item["photo_count"] = int(item.get("photo_count") or 0)
            item["face_count"] = int(item.get("face_count") or 0)
            item["cluster_ids"] = [int(value) for value in str(item.get("cluster_ids") or "").split(",") if value.isdigit()]
            item["representative_url"] = f"/api/photos/faces/crops/{int(item['representative_detection_id'])}"
            item["channel_ids"] = [value for value in str(item.get("channel_ids") or "").split(",") if value]
            item["detection_ids"] = [int(value) for value in str(item.get("detection_ids") or "").split(",") if value.isdigit()]
            performer_id = item.get("performer_id")
            item["performer_image_url"] = f"/api/performers/{int(performer_id)}/image" if performer_id else ""
            item["metadata_suggestions"] = []
            groups.append(item)
        # Suggestions may come from exact source-text matches or from groups the user
        # has previously assigned and confirmed. Profile/provider artwork is shown in
        # the manual review dialog, but is not itself used for face-to-name matching.
        try:
            unassigned_group_ids = list(dict.fromkeys(
                int(value) for item in groups if not item.get("performer_id")
                for value in (item.get("cluster_ids") or []) if int(value) > 0
            ))
            with db.reading() as rdb:
                performer_rows = rdb.execute(
                    "SELECT id,name,COALESCE(aliases,'') AS aliases FROM performers ORDER BY name COLLATE NOCASE"
                ).fetchall()
                reference_index = _photo_confirmed_reference_index(rdb)
                rejected_rows = rdb.execute(
                    "SELECT group_id,performer_id,source FROM photo_face_suggestion_feedback WHERE status='REJECTED'"
                ).fetchall()
                rejected = {(int(row['group_id']), int(row['performer_id'])) for row in rejected_rows}
                target_embeddings = {}
                if unassigned_group_ids:
                    marks = ','.join('?' for _ in unassigned_group_ids)
                    rows_embeddings = rdb.execute(
                        f"""WITH ranked AS (
                               SELECT d.group_id,d.embedding_json,d.id,
                                      ROW_NUMBER() OVER(PARTITION BY d.group_id ORDER BY d.id DESC) AS rn
                               FROM photo_face_detections d
                               JOIN photo_face_analysis a ON a.photo_id=d.photo_id
                               WHERE a.status='COMPLETE' AND d.group_id IN ({marks})
                           )
                           SELECT group_id,embedding_json FROM ranked WHERE rn<=16 ORDER BY id DESC""",
                        unassigned_group_ids,
                    ).fetchall()
                    for vector_row in rows_embeddings:
                        vector = _photo_decode_embedding(vector_row['embedding_json'])
                        if vector is not None:
                            target_embeddings.setdefault(int(vector_row['group_id']), []).append(vector)
                for item in groups:
                    if item.get("performer_id"):
                        item["metadata_suggestions"] = []
                        item["confirmed_assignment_suggestions"] = []
                        item["profile_image_suggestions"] = []
                        continue
                    item_cluster_ids = [int(value) for value in (item.get('cluster_ids') or []) if int(value) > 0]
                    feedback_group_ids = list(dict.fromkeys([int(item['id'])] + item_cluster_ids))
                    item["metadata_suggestions"] = [candidate for candidate in _photo_metadata_performer_suggestions(
                        rdb, item_cluster_ids, performer_rows, limit=5
                    ) if not any((gid, int(candidate['id'])) in rejected for gid in feedback_group_ids)][:3]
                    vectors = []
                    for cluster_id in item_cluster_ids:
                        vectors.extend(target_embeddings.get(cluster_id, []))
                    rejected_visual_ids = {
                        performer_id for gid, performer_id in rejected if gid in feedback_group_ids
                    }
                    item["confirmed_assignment_suggestions"] = _photo_confirmed_group_suggestions(
                        vectors, reference_index, performer_rows, rejected_visual_ids, limit=3
                    )
                    item["profile_image_suggestions"] = _photo_profile_picture_suggestions(rdb, vectors, performer_rows, rejected_visual_ids, limit=3)
                    # Keep each performer suggestion once per group. Confirmation is always explicit.
                    already = {int(candidate['id']) for candidate in item['metadata_suggestions']}
                    item["confirmed_assignment_suggestions"] = [candidate for candidate in item["confirmed_assignment_suggestions"] if int(candidate['id']) not in already]
                    already |= {int(candidate['id']) for candidate in item["confirmed_assignment_suggestions"]}
                    item["profile_image_suggestions"] = [candidate for candidate in item["profile_image_suggestions"] if int(candidate['id']) not in already]
        except Exception as exc:
            db.log(f"Photo group suggestions unavailable: {type(exc).__name__}: {exc}", "WARN",
                   event="photo_library.suggestions_failed")
            for item in groups:
                item.setdefault("metadata_suggestions", [])
                item.setdefault("confirmed_assignment_suggestions", [])
                item.setdefault("profile_image_suggestions", [])
        with db.reading() as rdb:
            scope_counts = {name: int(rdb.execute(f"SELECT COUNT(*) AS n FROM photo_library_items p WHERE {cond}").fetchone()["n"] or 0) for name, cond in _PHOTO_FACE_SCOPES.items()}
        return {"ok": True, "groups": groups, "photo_scopes": scope_counts,
                "analysis": {"total_photos": int(analysis_row["total"] or 0),
                             "analyzed_photos": int(analysis_row["analyzed"] or 0),
                             "failed_photos": int(analysis_row["failed"] or 0)},
                "cosine_threshold": 0.42, "grouping_mode": "image_embedding_cluster"}


    @router.get("/api/photos/faces/performers")
    async def photo_face_performer_search(request: Request):
        """Search local performer records for manual face-group assignment."""
        require_auth(request)
        query = str(request.query_params.get("q") or "").strip()[:100]
        try: limit = max(1, min(24, int(request.query_params.get("limit", "12"))))
        except ValueError: limit = 12
        if len(query) < 2:
            return {"ok": True, "items": []}
        with db.reading() as rdb:
            rows = rdb.execute(
                "SELECT id,name,image FROM performers WHERE name LIKE ? COLLATE NOCASE "
                "ORDER BY CASE WHEN name LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,name COLLATE NOCASE LIMIT ?",
                (f"%{query}%", f"{query}%", limit),
            ).fetchall()
        items = [{"id": int(row["id"]), "name": str(row["name"] or ""),
                  "image_url": f"/api/performers/{int(row['id'])}/image" if row["id"] else ""}
                 for row in rows]
        return {"ok": True, "items": items}


    @router.get("/api/photos/faces/performers/provider-search")
    async def photo_face_performer_provider_search(request: Request):
        """Performers available through the metadata APIs (StashDB, ThePornDB) that match a typed name; nothing is imported."""
        require_auth(request)
        query = str(request.query_params.get("q") or "").strip()[:100]
        if len(query) < 2:
            return {"ok": True, "items": [], "configured": True}
        from ..metadata.providers import search_performer_providers
        configured = bool(db.setting("stashdb_api_key") or db.setting("tpdb_api_token"))
        try:
            rows = await search_performer_providers(db, query, limit=8) if configured else []
        except Exception as exc:
            db.log(f"Performer provider search failed for {query!r}: {type(exc).__name__}: {exc}", "WARN")
            rows = []
        items = [{"provider": str(r.get("provider") or ""), "provider_id": str(r.get("provider_id") or ""), "name": str(r.get("name") or ""),
                  "image": str(r.get("image") or ""), "profile": str(r.get("profile") or ""), "aliases": r.get("aliases") or []}
                 for r in rows if r.get("name") and r.get("provider_id")]
        return {"ok": True, "items": items, "configured": configured}

    @router.post("/api/photos/faces/performers/import")
    async def photo_face_performer_import(request: Request):
        """Create (or find) the local performer for a provider candidate and download its picture, so the face can be matched."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Request body must be an object")
        name = str(body.get("name") or "").strip()
        provider = str(body.get("provider") or "").strip().lower()
        provider_id = str(body.get("provider_id") or "").strip()
        image = str(body.get("image") or "").strip()
        if not name or not provider or not provider_id:
            raise HTTPException(400, "name, provider and provider_id are required")
        if image and not (image.startswith("http://") or image.startswith("https://")):
            raise HTTPException(400, "image must be an http(s) URL")
        from ..engines.entity_identity import upsert_entity
        def _make():
            with db.lock:
                pid = upsert_entity(db, "performers", name, provider=provider, provider_id=provider_id,
                                    image=image or None, profile=str(body.get("profile") or "").strip() or None)
                db.conn.commit()
            return pid
        pid = await asyncio.to_thread(_make)
        if not pid:
            raise HTTPException(409, "This performer could not be created locally")
        downloaded = False
        if image:
            try:
                from ..engines.thumbnail_cache import prefetch_entity_image
                downloaded = bool(await prefetch_entity_image(db, "performer", int(pid), image))
            except Exception as exc:
                db.log(f"Imported performer {name}: picture download failed: {type(exc).__name__}: {exc}", "WARN")
        db.log(f"Performer {name} imported from {provider} for face matching (picture {'saved' if downloaded else 'not saved'})", "SUCCESS",
               event="photo_library.performer_imported", details={"performer_id": int(pid), "provider": provider})
        return {"ok": True, "id": int(pid), "name": name, "picture_saved": downloaded}


    @router.post("/api/photos/faces/analyze")
    async def photo_face_analyze_start(request: Request):
        """Queue local image-based face detection and anonymous visual grouping."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        raw = body.get("channel_ids", []) if isinstance(body, dict) else []
        if raw is None: raw = []
        if not isinstance(raw, list):
            raise HTTPException(400, "channel_ids must be an array")
        channel_ids = list(dict.fromkeys(str(value or "").strip() for value in raw if str(value or "").strip()))
        if len(channel_ids) > 100:
            raise HTTPException(413, "Analyze at most 100 channels per job")
        if any(not value.lstrip("-").isdigit() for value in channel_ids):
            raise HTTPException(400, "One or more selected channel IDs are invalid")
        if channel_ids:
            marks = ",".join("?" for _ in channel_ids)
            with db.reading() as rdb:
                saved = {str(row["telegram_id"]) for row in rdb.execute(
                    f"SELECT telegram_id FROM channels WHERE telegram_id IN ({marks})", channel_ids
                ).fetchall()}
            if set(channel_ids) - saved:
                raise HTTPException(400, "One or more selected channels are not saved in Archive X")
            marks = ",".join("?" for _ in channel_ids)
            with db.reading() as rdb:
                indexed = int(rdb.execute(f"SELECT COUNT(*) n FROM photo_library_items WHERE channel_id IN ({marks})", channel_ids).fetchone()["n"] or 0)
        else:
            with db.reading() as rdb:
                indexed = int(rdb.execute("SELECT COUNT(*) n FROM photo_library_items").fetchone()["n"] or 0)
        match_performers = bool(body.get("match_performers")) if isinstance(body, dict) else False
        if not indexed:
            raise HTTPException(400, "No indexed photos found. Scan Telegram channels for images first.")
        detail = {"match_performers": match_performers, "source": "telegram", "workflow": "PHOTO_FACE_ANALYSIS", "channel_ids": channel_ids,
                  "source_count": indexed, "source_mode": "PHOTO_FACE_ANALYSIS", "read_only": True,
                  "image_based": True, "auto_identity_assignment": match_performers}
        jid = db.create_job("TELEGRAM_TRANSFER", ",".join(channel_ids) or "all indexed photos", "", False,
                            detail=json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
        jobs.submit(jid)
        db.log(f"Queued image-based face analysis job {jid} for {indexed} indexed photo(s)", "INFO",
               event="photo_library.face_analysis_queued", job_id=jid,
               details={"channel_ids": channel_ids, "indexed_photos": indexed})
        return {"ok": True, "id": jid, "channel_ids": channel_ids, "indexed_photos": indexed, "status": "QUEUED"}


    @router.post("/api/photos/faces/groups/{group_id}/assign")
    async def photo_face_group_assign(group_id: int, request: Request):
        """Manually associate an anonymous visual group with an existing performer."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Request body must be an object")
        raw_performer_id = body.get("performer_id")
        performer_id = None
        if raw_performer_id not in (None, "", 0, "0"):
            try: performer_id = int(raw_performer_id)
            except (TypeError, ValueError): raise HTTPException(400, "performer_id must be an integer or null")
            with db.reading() as rdb:
                if not rdb.execute("SELECT id FROM performers WHERE id=?", (performer_id,)).fetchone():
                    raise HTTPException(404, "Performer not found")
        raw_group_ids = body.get("group_ids")
        if isinstance(raw_group_ids, list) and raw_group_ids:
            try: group_ids = list(dict.fromkeys(int(value) for value in raw_group_ids if int(value) > 0))
            except (TypeError, ValueError): raise HTTPException(400, "group_ids must be positive integers")
        elif int(group_id) > 0:
            group_ids = [int(group_id)]
        else:
            raise HTTPException(400, "group_ids is required for an aggregated performer group")
        if not group_ids or len(group_ids) > 300:
            raise HTTPException(400, "Provide between 1 and 300 face cluster IDs")
        marks = ",".join("?" for _ in group_ids)
        suggestion_source = str(body.get("suggestion_source") or "manual").strip().lower()
        if suggestion_source not in {"manual", "metadata", "confirmed_assignment", "profile_image"}:
            suggestion_source = "manual"
        suggestion_score = body.get("suggestion_score")
        try: suggestion_score = float(suggestion_score) if suggestion_score not in (None, "") else None
        except (TypeError, ValueError): suggestion_score = None
        suggestion_reason = str(body.get("suggestion_reason") or "User confirmed performer assignment")[:500]
        with db.lock:
            try:
                db.conn.execute("BEGIN IMMEDIATE")
                existing = {int(row["id"]) for row in db.conn.execute(f"SELECT id FROM photo_face_groups WHERE id IN ({marks})", group_ids).fetchall()}
                if existing != set(group_ids): raise HTTPException(404, "One or more face clusters were not found")
                db.conn.execute(f"UPDATE photo_face_groups SET performer_id=?,updated_at=? WHERE id IN ({marks})",
                                [performer_id, config.now_iso(), *group_ids])
                if performer_id is not None:
                    now = config.now_iso()
                    feedback_group_ids = group_ids
                    for feedback_group_id in feedback_group_ids:
                        # An explicit confirmation supersedes any earlier rejection for
                        # this performer/group pair, regardless of which suggestion source
                        # originally produced the candidate.
                        db.conn.execute(
                            "UPDATE photo_face_suggestion_feedback SET status='CONFIRMED',reason=?,updated_at=? WHERE group_id=? AND performer_id=?",
                            (suggestion_reason, now, feedback_group_id, performer_id),
                        )
                        db.conn.execute(
                            """INSERT INTO photo_face_suggestion_feedback
                               (group_id,performer_id,source,status,similarity_score,reason,created_at,updated_at)
                               VALUES(?,?,?,'CONFIRMED',?,?,?,?)
                               ON CONFLICT(group_id,performer_id,source) DO UPDATE SET
                                 status='CONFIRMED',similarity_score=excluded.similarity_score,
                                 reason=excluded.reason,updated_at=excluded.updated_at""",
                            (feedback_group_id, performer_id, suggestion_source, suggestion_score, suggestion_reason, now, now),
                        )
                db.conn.commit()
            except HTTPException:
                db.conn.rollback()
                raise
            except Exception:
                db.conn.rollback()
                raise
        db.log(f"Photo face cluster(s) {group_ids} assigned to {('performer '+str(performer_id)) if performer_id else 'unassigned'}",
               "SUCCESS", event="photo_library.face_group_assigned", details={"group_ids": group_ids, "performer_id": performer_id})
        return {"ok": True, "group_id": int(group_id), "group_ids": group_ids, "performer_id": performer_id}


    @router.post("/api/photos/faces/suggestions/reject")
    async def photo_face_suggestion_reject(request: Request):
        """Remember that a suggested performer is not correct for these face clusters."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Request body must be an object")
        try:
            performer_id = int(body.get("performer_id") or 0)
            group_ids = list(dict.fromkeys(int(value) for value in (body.get("group_ids") or []) if int(value) > 0))
            source = str(body.get("source") or "").strip().lower()
            score = float(body.get("similarity_score")) if body.get("similarity_score") not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "A valid performer_id and group_ids are required")
        if performer_id <= 0 or not group_ids or len(group_ids) > 300:
            raise HTTPException(400, "Select a performer suggestion and one or more face clusters")
        if source not in {"metadata", "confirmed_assignment", "profile_image"}:
            raise HTTPException(400, "Unknown suggestion source")
        with db.reading() as rdb:
            if not rdb.execute("SELECT id FROM performers WHERE id=?", (performer_id,)).fetchone():
                raise HTTPException(404, "Performer not found")
            marks = ','.join('?' for _ in group_ids)
            existing = {int(row['id']) for row in rdb.execute(f"SELECT id FROM photo_face_groups WHERE id IN ({marks})", group_ids).fetchall()}
            if existing != set(group_ids):
                raise HTTPException(404, "One or more face clusters were not found")
        now = config.now_iso()
        reason = str(body.get("reason") or "User rejected this performer suggestion")[:500]
        with db.lock:
            try:
                db.conn.execute("BEGIN IMMEDIATE")
                for group_id in group_ids:
                    db.conn.execute(
                        """INSERT INTO photo_face_suggestion_feedback
                           (group_id,performer_id,source,status,similarity_score,reason,created_at,updated_at)
                           VALUES(?,?,?,'REJECTED',?,?,?,?)
                           ON CONFLICT(group_id,performer_id,source) DO UPDATE SET
                             status='REJECTED',similarity_score=excluded.similarity_score,
                             reason=excluded.reason,updated_at=excluded.updated_at""",
                        (group_id, performer_id, source, score, reason, now, now),
                    )
                db.conn.commit()
            except Exception:
                db.conn.rollback()
                raise
        db.log(f"Rejected photo face suggestion for performer {performer_id} on {len(group_ids)} group(s)",
               "INFO", event="photo_library.face_suggestion_rejected",
               details={"performer_id": performer_id, "group_ids": group_ids, "source": source})
        return {"ok": True, "performer_id": performer_id, "group_ids": group_ids, "source": source}


    @router.post("/api/photos/faces/groups/merge")
    async def photo_face_groups_merge(request: Request):
        """Merge visually similar clusters after user review; no media files are modified."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict) or not isinstance(body.get("group_ids"), list):
            raise HTTPException(400, "group_ids must be an array")
        try:
            from ..engines.photo_face_clustering import _merge_face_groups
            group_ids = list(dict.fromkeys(int(value) for value in body["group_ids"] if int(value) > 0))
            raw_target = body.get("target_group_id")
            target = int(raw_target) if raw_target not in (None, "") else None
            survivor = await asyncio.to_thread(_merge_face_groups, db, group_ids, target)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            db.log(f"Could not merge photo face groups: {type(exc).__name__}: {exc}", "WARN",
                   event="photo_library.face_group_merge_failed")
            raise HTTPException(500, "Could not merge the selected face groups")
        db.log(f"Merged photo face groups {group_ids} into group {survivor}", "SUCCESS",
               event="photo_library.face_groups_merged", details={"group_ids": group_ids, "target_group_id": survivor})
        return {"ok": True, "target_group_id": survivor, "merged_group_ids": [gid for gid in group_ids if gid != survivor]}


    @router.post("/api/photos/faces/detections/{detection_id}/split")
    async def photo_face_detection_split(detection_id: int, request: Request):
        """Separate one detection into its own group to correct a mistaken visual match."""
        require_auth(request)
        try:
            from ..engines.photo_face_clustering import _split_face_detection
            new_group_id = await asyncio.to_thread(_split_face_detection, db, int(detection_id))
        except ValueError as exc:
            raise HTTPException(404 if "not found" in str(exc).lower() else 400, str(exc))
        except Exception as exc:
            db.log(f"Could not split photo face detection: {type(exc).__name__}: {exc}", "WARN",
                   event="photo_library.face_detection_split_failed")
            raise HTTPException(500, "Could not separate the selected face detection")
        db.log(f"Separated face detection {detection_id} into face group {new_group_id}", "SUCCESS",
               event="photo_library.face_detection_split", details={"detection_id": int(detection_id), "new_group_id": new_group_id})
        return {"ok": True, "detection_id": int(detection_id), "new_group_id": new_group_id}


    @router.get("/api/photos/content-tags")
    async def photo_content_tags_summary(request: Request):
        """Which content tags exist, how many photos have each, and how many photos have been analysed."""
        require_auth(request)
        from ..engines import photo_content_tags
        return {"ok": True, **await asyncio.to_thread(photo_content_tags.summary, db)}

    @router.post("/api/photos/content-tags/analyze")
    async def photo_content_tags_start(request: Request):
        """Queue the local content-tagging job (photos not yet analysed; {"redo": true} for all)."""
        require_auth(request)
        from ..engines import photo_content_tags
        if not photo_content_tags.available():
            raise HTTPException(501, "Content tags need the optional package nudenet (pip install nudenet)")
        try:
            body = await request.json()
        except Exception:
            body = {}
        with db.reading() as rdb:
            indexed = int(rdb.execute("SELECT COUNT(*) n FROM photo_library_items").fetchone()["n"] or 0)
        if not indexed:
            raise HTTPException(400, "No indexed photos found. Scan Telegram channels for images first.")
        detail = {"source": "telegram", "workflow": "PHOTO_CONTENT_TAGS", "source_count": indexed, "source_mode": "PHOTO_CONTENT_TAGS",
                  "read_only": True, "redo": bool(isinstance(body, dict) and body.get("redo"))}
        jid = db.create_job("TELEGRAM_TRANSFER", "all indexed photos", "", False, detail=json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
        jobs.submit(jid)
        db.log(f"Queued content tagging job {jid} for {indexed} indexed photo(s)", "INFO", event="photo_library.content_tags_queued", job_id=jid)
        return {"ok": True, "id": jid, "indexed_photos": indexed, "status": "QUEUED"}

    @router.get("/api/photos/faces/review")
    async def photo_face_review(request: Request):
        """Every face in one person (or face group), least similar to the rest first, so wrong ones are easy to spot."""
        require_auth(request)
        q = request.query_params
        performer_id = None
        group_ids: list[int] = []
        try:
            if q.get("performer_id"):
                performer_id = int(q.get("performer_id"))
            for value in str(q.get("group_ids", "") or q.get("group_id", "") or "").split(","):
                if value.strip():
                    group_ids.append(int(value))
        except ValueError:
            raise HTTPException(400, "performer_id and group_ids must be integers")
        if performer_id is None and not group_ids:
            raise HTTPException(400, "Give performer_id or group_ids")
        limit = 600
        def _load():
            import numpy as _np
            with db.reading() as rdb:
                if performer_id is not None:
                    rows = rdb.execute(
                        """SELECT d.id,d.photo_id,d.group_id,d.embedding_json FROM photo_face_detections d
                           JOIN photo_face_groups g ON g.id=d.group_id WHERE g.performer_id=? ORDER BY d.id""", (performer_id,)).fetchall()
                else:
                    marks = ",".join("?" for _ in group_ids)
                    rows = rdb.execute(f"SELECT id,photo_id,group_id,embedding_json FROM photo_face_detections WHERE group_id IN ({marks}) ORDER BY id", group_ids).fetchall()
            vecs, keep = [], []
            for r in rows:
                v = _photo_decode_embedding(r["embedding_json"])
                keep.append(r)
                vecs.append(v)
            good = [v for v in vecs if v is not None]
            centroid = None
            if good:
                m = _np.asarray(good, dtype=_np.float32).mean(axis=0)
                n = float(_np.linalg.norm(m))
                centroid = m / n if n else None
            items = []
            for r, v in zip(keep, vecs):
                sim = float(_np.dot(_np.asarray(v, dtype=_np.float32), centroid)) if (v is not None and centroid is not None) else 0.0
                items.append({"detection_id": int(r["id"]), "photo_id": int(r["photo_id"]), "group_id": int(r["group_id"]),
                              "crop_url": f"/api/photos/faces/crops/{int(r['id'])}", "similarity": round(sim, 4)})
            items.sort(key=lambda x: (x["similarity"], x["detection_id"]))
            return items
        items = await asyncio.to_thread(_load)
        return {"ok": True, "total": len(items), "items": items[:limit]}

    @router.post("/api/photos/faces/detections/move")
    async def photo_face_detections_move(request: Request):
        """Take the chosen faces out of their group together. They land in one new group, optionally already named after a performer."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict) or not isinstance(body.get("detection_ids"), list):
            raise HTTPException(400, "detection_ids must be an array")
        try:
            ids = [int(v) for v in body["detection_ids"]]
            performer_id = int(body["performer_id"]) if body.get("performer_id") not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "detection_ids and performer_id must be integers")
        if len(ids) > 2000:
            raise HTTPException(413, "Move at most 2000 faces at a time")
        from ..engines.photo_face_clustering import _move_detections
        try:
            result = await asyncio.to_thread(_move_detections, db, ids, performer_id)
        except ValueError as exc:
            raise HTTPException(404 if "not found" in str(exc).lower() else 400, str(exc))
        db.log(f"Moved {result['moved']} face(s) to face group {result['group_id']}" + (f" (performer {performer_id})" if performer_id else ""), "SUCCESS",
               event="photo_library.faces_moved", details={"performer_id": performer_id, **result})
        return {"ok": True, **result}

    @router.post("/api/photos/faces/different-person")
    async def photo_face_different_person(request: Request):
        """Remove a photo's detections from the active group(s), keeping photo bytes and Telegram untouched."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Request body must be an object")
        try:
            photo_id = int(body.get("photo_id") or 0)
            raw_group_ids = body.get("group_ids")
            if photo_id <= 0 or not isinstance(raw_group_ids, list):
                raise ValueError
            group_ids = list(dict.fromkeys(int(value) for value in raw_group_ids if int(value) > 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "photo_id and positive group_ids are required")
        if not group_ids or len(group_ids) > 700:
            raise HTTPException(400, "Select the active group before using Different person")
        try:
            from ..engines.photo_face_clustering import _separate_photo_detections
            moved = await asyncio.to_thread(_separate_photo_detections, db, photo_id, group_ids)
        except ValueError as exc:
            raise HTTPException(404 if "not found" in str(exc).lower() else 400, str(exc))
        except Exception as exc:
            db.log(f"Different-person correction failed: {type(exc).__name__}: {exc}", "WARN",
                   event="photo_library.face_detection_different_person_failed",
                   details={"photo_id": photo_id, "group_ids": group_ids})
            raise HTTPException(500, "Could not remove this photo's face detections from the current group")
        db.log(f"Removed {len(moved)} face detection(s) for photo {photo_id} from group(s) {group_ids}", "SUCCESS",
               event="photo_library.face_detection_different_person",
               details={"photo_id": photo_id, "group_ids": group_ids, "moved": moved})
        return {"ok": True, "photo_id": photo_id, "detections_moved": len(moved), "new_groups": [row["new_group_id"] for row in moved],
                "message": f"Moved {len(moved)} face detection(s) into new unassigned group(s). The photo remains in Archive X and Telegram."}


    @router.get("/api/photos/faces/crops/{detection_id}")
    async def photo_face_crop(detection_id: int, request: Request):
        """Serve an aligned face crop from Archive X's private cache."""
        require_auth(request)
        from fastapi.responses import FileResponse as _FileResponse
        with db.reading() as rdb:
            row = rdb.execute("SELECT crop_path FROM photo_face_detections WHERE id=?", (int(detection_id),)).fetchone()
        if not row or not row["crop_path"]:
            raise HTTPException(404, "Face crop not found")
        crop_root = (Path(config.CACHE_DIR) / "photo_face_groups" / "crops").resolve()
        crop_path = Path(str(row["crop_path"])).resolve()
        if not crop_path.is_relative_to(crop_root) or not crop_path.is_file():
            raise HTTPException(404, "Face crop is not available")
        return _FileResponse(crop_path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})



    # ------------------------------------------------------------------
    # SUBSECTION: Photo scanning
    # Scan and reconcile jobs.
    # ------------------------------------------------------------------
    @router.post("/api/photos/scan")
    async def photo_library_scan_start(request: Request):
        """Queue a full, read-only scan of selected Telegram channels for photo media."""
        require_auth(request)
        if not telegram.connected or getattr(telegram, "client", None) is None:
            raise HTTPException(503, "Connect Telegram before scanning channels for photos")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        raw = body.get("channel_ids") if isinstance(body, dict) else None
        if not isinstance(raw, list) or not raw:
            raise HTTPException(400, "Select at least one channel to scan")
        channel_ids = list(dict.fromkeys(str(value or "").strip() for value in raw if str(value or "").strip()))
        if len(channel_ids) > 100:
            raise HTTPException(413, "Scan at most 100 channels per job")
        if any(not value.lstrip("-").isdigit() for value in channel_ids):
            raise HTTPException(400, "One or more selected channel IDs are invalid")
        marks = ",".join("?" for _ in channel_ids)
        with db.reading() as rdb:
            saved = {str(row["telegram_id"]) for row in rdb.execute(
                f"SELECT telegram_id FROM channels WHERE telegram_id IN ({marks})", channel_ids
            ).fetchall()}
        missing = [value for value in channel_ids if value not in saved]
        if missing:
            raise HTTPException(400, "One or more channels are not in Archive X's saved Telegram channel list; refresh channels first")
        detail = {"source": "telegram", "workflow": "PHOTO_CHANNEL_SCAN", "channel_ids": channel_ids,
                  "source_count": len(channel_ids), "source_mode": "PHOTO_SCAN", "read_only": True}
        jid = db.create_job("TELEGRAM_TRANSFER", ",".join(channel_ids), "", False,
                            detail=json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
        jobs.submit(jid)
        db.log(f"Queued photo library scan job {jid} for {len(channel_ids)} channel(s)", "INFO",
               event="photo_library.scan_queued", job_id=jid, details={"channel_ids": channel_ids})
        return {"ok": True, "id": jid, "channel_ids": channel_ids, "status": "QUEUED"}


    @router.post("/api/photos/reconcile")
    async def photo_library_reconcile_start(request: Request):
        """Queue a targeted reconciliation of already-indexed Telegram image messages.

        This checks exact saved message IDs; it does not scan full channel history.
        When also_scan is requested, the full image scan runs first, followed by
        reconciliation in the same job so the two operations cannot race each other.
        """
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Request body must be an object")
        raw = body.get("channel_ids")
        if not isinstance(raw, list) or not raw:
            raise HTTPException(400, "Select at least one channel to reconcile")
        channel_ids = list(dict.fromkeys(str(value or "").strip() for value in raw if str(value or "").strip()))
        if len(channel_ids) > 100:
            raise HTTPException(413, "Reconcile at most 100 channels per job")
        if any(not value.lstrip("-").isdigit() for value in channel_ids):
            raise HTTPException(400, "One or more selected channel IDs are invalid")
        marks = ",".join("?" for _ in channel_ids)
        with db.reading() as rdb:
            saved = {str(row["telegram_id"]) for row in rdb.execute(
                f"SELECT telegram_id FROM channels WHERE telegram_id IN ({marks})", channel_ids
            ).fetchall()}
            indexed = {str(row["channel_id"]): int(row["n"] or 0) for row in rdb.execute(
                f"SELECT channel_id,COUNT(*) AS n FROM photo_library_items WHERE channel_id IN ({marks}) GROUP BY channel_id",
                channel_ids,
            ).fetchall()}
        missing = [value for value in channel_ids if value not in saved]
        if missing:
            raise HTTPException(400, "One or more channels are not in Archive X's saved Telegram channel list; refresh channels first")
        if not telegram.connected or getattr(telegram, "client", None) is None:
            raise HTTPException(503, "Connect Telegram before reconciling indexed image messages. No local items were removed.")

        also_scan = bool(body.get("also_scan", False))
        workflow = "PHOTO_SCAN_AND_RECONCILE" if also_scan else "PHOTO_RECONCILE"
        # Avoid races among image scanning, face analysis, and reconciliation.
        # Face analysis can create detection records for a photo, so deleting its
        # index concurrently would risk orphaning work that was still in flight.
        photo_workflows = {"PHOTO_CHANNEL_SCAN", "PHOTO_RECONCILE", "PHOTO_SCAN_AND_RECONCILE", "PHOTO_FACE_ANALYSIS"}
        for existing in db.active_jobs("TELEGRAM_TRANSFER"):
            try:
                active_detail = json.loads(existing.get("detail") or "{}")
            except Exception:
                active_detail = {}
            active_workflow = str(active_detail.get("workflow") or "").upper()
            if active_workflow not in photo_workflows:
                continue
            active_channels = {str(v) for v in (active_detail.get("channel_ids") or [])}
            if not active_channels:
                active_channels = {str(existing.get("source_channel") or "")}
            overlap = active_channels.intersection(channel_ids)
            if not overlap:
                continue
            desired_same = active_workflow == workflow
            if desired_same:
                return {"ok": True, "already_running": True, "id": str(existing.get("id") or ""),
                        "workflow": active_workflow, "also_scan": active_workflow == "PHOTO_SCAN_AND_RECONCILE",
                        "message": f"This photo operation is already running for channel(s): {', '.join(sorted(overlap))}."}
            raise HTTPException(409, f"Another photo operation ({active_workflow.replace('_', ' ').title()}) is already running for channel(s): {', '.join(sorted(overlap))}. Wait for it to finish before starting reconciliation or another scan.")

        detail = {"source": "telegram", "workflow": workflow, "channel_ids": channel_ids,
                  "source_count": len(channel_ids), "source_mode": "PHOTO_RECONCILE",
                  "also_scan": also_scan, "indexed_items_at_start": indexed, "read_only": False,
                  "cleanup_scope": "app_managed_cache_and_face_records"}
        jid = db.create_job("TELEGRAM_TRANSFER", ",".join(channel_ids), "", False,
                            detail=json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
        jobs.submit(jid)
        db.log(
            f"Queued photo reconciliation job {jid} for {len(channel_ids)} channel(s)" + (" after image scan" if also_scan else ""),
            "INFO", event="photo_library.reconcile_queued", job_id=jid,
            details={"channel_ids": channel_ids, "also_scan": also_scan, "indexed_items": indexed},
        )
        return {"ok": True, "id": jid, "channel_ids": channel_ids, "workflow": workflow,
                "also_scan": also_scan, "indexed_items": indexed, "status": "QUEUED"}


    @router.get("/api/photos/scan-status/{job_id}")
    async def photo_library_scan_status(job_id: str, request: Request):
        """Status endpoint for photo scan jobs and local photo-upload jobs."""
        require_auth(request)
        job = await asyncio.to_thread(db.get_job, str(job_id))
        if not job:
            raise HTTPException(404, "Photo job not found")
        return {"ok": True, "id": str(job.get("id") or job_id), "status": str(job.get("status") or "UNKNOWN"),
                "progress": float(job.get("progress") or 0), "current_item": str(job.get("current_item") or ""),
                "error": str(job.get("error") or ""), "job_type": str(job.get("job_type") or ""),
                "operation": str(job.get("current_operation") or ""),
                "updated_at": str(job.get("updated_at") or "")}



    # ------------------------------------------------------------------
    # SUBSECTION: Photo files
    # Thumbnails, previews, images and delete.
    # ------------------------------------------------------------------
    async def _photo_library_file_path(channel_id: str, message_id: int, *, thumbnail: bool) -> Path:
        """Serve the durable local copy first; fetch from Telegram only on a cache miss."""
        from PIL import Image, ImageOps
        from ..engines.photo_cache import (
            cache_telegram_message, record_photo_cache, resolve_database_cache_path,
            photo_original_path, photo_thumbnail_path, inspect_cached_file,
            render_jpeg_derivative, THUMBNAIL_BOX, THUMBNAIL_QUALITY,
        )

        with db.reading() as rdb:
            row = rdb.execute(
                "SELECT channel_id,message_id,filename,mime_type,local_path,local_sha256,cached_at "
                "FROM photo_library_items WHERE channel_id=? AND message_id=?",
                (str(channel_id), int(message_id)),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Photo is not in the Archive X photo index")

        thumb = photo_thumbnail_path(channel_id, message_id)
        if thumbnail and thumb.is_file() and thumb.stat().st_size > 512:
            return thumb
        original = resolve_database_cache_path(row["local_path"]) or photo_original_path(channel_id, message_id)
        if not original.is_file() or original.stat().st_size <= 0:
            # Legacy indexed rows may not yet have a local copy. Download once,
            # atomically cache it, and persist the cache location/hash with the
            # exact Telegram message mapping for every subsequent request.
            client = getattr(telegram, "client", None)
            if client is None or not getattr(telegram, "connected", False):
                raise HTTPException(503, "Telegram is disconnected and this photo has not been cached locally")
            try:
                try:
                    entity = await client.get_entity(int(channel_id))
                except (TypeError, ValueError):
                    entity = await client.get_entity(channel_id)
                message = await client.get_messages(entity, ids=int(message_id))
                if not message:
                    raise HTTPException(404, "Telegram photo message no longer exists")
                ok, filename, mime = __import__("telegram_archive.engines.photo_channel_scanner", fromlist=["_is_photo_message"])._is_photo_message(message)
                if not ok:
                    raise HTTPException(415, "The indexed Telegram message is no longer an image")
                info = await cache_telegram_message(client, message, str(channel_id), int(message_id))
                info["mime_type"] = str(row["mime_type"] or mime or "image/jpeg")
                info["filename"] = str(row["filename"] or filename or "")
                await asyncio.to_thread(record_photo_cache, db, str(channel_id), int(message_id), info)
                original = Path(info["path"])
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, f"Could not cache Telegram photo: {type(exc).__name__}: {exc}") from exc
        elif not str(row["local_path"] or "").strip():
            # Recover the database mapping if a previous build left a valid
            # deterministic cache file behind without recording its path.
            try:
                info = await asyncio.to_thread(inspect_cached_file, original, fallback_name=str(row["filename"] or ""))
                await asyncio.to_thread(record_photo_cache, db, str(channel_id), int(message_id), info)
            except Exception:
                pass

        if not thumbnail:
            return original

        thumb.parent.mkdir(parents=True, exist_ok=True)
        if thumb.is_file() and thumb.stat().st_size > 512:
            return thumb
        try:
            await asyncio.to_thread(render_jpeg_derivative, original, thumb, THUMBNAIL_BOX, THUMBNAIL_QUALITY)
        except Exception:
            # Unsupported but cached image formats still remain available as
            # their original, even if a JPEG thumbnail can't be built.
            return original
        return thumb


    @router.get("/api/photos/{channel_id}/{message_id}/thumbnail")
    async def photo_library_thumbnail(channel_id: str, message_id: int, request: Request):
        require_auth(request)
        from fastapi.responses import FileResponse as _FileResponse
        import mimetypes as _mimetypes
        path = await _photo_library_file_path(channel_id, message_id, thumbnail=True)
        if path.suffix.lower() == ".jpg":
            mime = "image/jpeg"
        else:
            with db.reading() as rdb:
                row = rdb.execute("SELECT filename,mime_type FROM photo_library_items WHERE channel_id=? AND message_id=?", (str(channel_id), int(message_id))).fetchone()
            mime = str(row["mime_type"] or "") if row else ""
            if not mime.startswith("image/"):
                mime = _mimetypes.guess_type(str(row["filename"] or ""))[0] if row else None
            mime = mime if mime and mime.startswith("image/") else "image/jpeg"
        return _FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


    @router.get("/api/photos/{channel_id}/{message_id}/preview")
    async def photo_library_preview(channel_id: str, message_id: int, request: Request):
        """Serve a cached 1440px JPEG preview, built from the local original off the event loop."""
        require_auth(request)
        import os as _os
        import tempfile as _tempfile
        from PIL import Image as _Image, ImageOps as _ImageOps
        from fastapi.responses import FileResponse as _FileResponse
        from ..engines.photo_cache import photo_preview_path as _photo_preview_path

        original = await _photo_library_file_path(channel_id, message_id, thumbnail=False)
        target = _photo_preview_path(str(channel_id), int(message_id))
        try:
            original_stat = original.stat()
            if target.is_file() and target.stat().st_size > 512 and target.stat().st_mtime_ns >= original_stat.st_mtime_ns:
                return _FileResponse(target, media_type="image/jpeg", headers={"Cache-Control":"private, max-age=86400", "Content-Disposition":"inline", "X-Content-Type-Options":"nosniff"})
        except OSError:
            pass

        def _build_preview() -> Path:
            from ..engines.photo_cache import render_jpeg_derivative as _render, PREVIEW_BOX as _box, PREVIEW_QUALITY as _q
            _render(original, target, _box, _q, (16, 19, 25))
            return target

        try:
            preview = await asyncio.to_thread(_build_preview)
        except Exception:
            # Keep unusual image formats usable: if Pillow cannot render a preview,
            # serve their original and let the browser decode it directly.
            with db.reading() as rdb:
                row = rdb.execute("SELECT filename,mime_type FROM photo_library_items WHERE channel_id=? AND message_id=?", (str(channel_id), int(message_id))).fetchone()
            import mimetypes as _mimetypes
            mime = str(row["mime_type"] or "") if row else ""
            if not mime.startswith("image/"):
                mime = _mimetypes.guess_type(str(row["filename"] or ""))[0] if row else None
            mime = mime if mime and mime.startswith("image/") else "image/jpeg"
            return _FileResponse(original, media_type=mime, headers={"Cache-Control":"private, max-age=3600", "Content-Disposition":"inline", "X-Content-Type-Options":"nosniff"})
        return _FileResponse(preview, media_type="image/jpeg", headers={"Cache-Control":"private, max-age=86400", "Content-Disposition":"inline", "X-Content-Type-Options":"nosniff"})


    @router.get("/api/photos/{channel_id}/{message_id}/image")
    async def photo_library_image(channel_id: str, message_id: int, request: Request):
        require_auth(request)
        from fastapi.responses import FileResponse as _FileResponse
        import mimetypes as _mimetypes
        path = await _photo_library_file_path(channel_id, message_id, thumbnail=False)
        with db.reading() as rdb:
            row = rdb.execute("SELECT filename,mime_type FROM photo_library_items WHERE channel_id=? AND message_id=?", (str(channel_id), int(message_id))).fetchone()
        mime = str(row["mime_type"] or "") if row else ""
        if not mime.startswith("image/"):
            mime = _mimetypes.guess_type(str(row["filename"] or ""))[0] if row else None
        return _FileResponse(path, media_type=mime if mime and mime.startswith("image/") else "image/jpeg",
                             headers={"Cache-Control": "private, max-age=3600", "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})

    @router.post("/api/photos/{channel_id}/{message_id}/delete")
    async def photo_library_delete(channel_id: str, message_id: int, request: Request):
        """Remove one photo from the local index/cache, optionally deleting its Telegram post too.

        Telegram deletion is attempted and verified before the local library record is removed.
        If Telegram refuses deletion, the Archive X record and cached bytes are retained.
        """
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Request body must be an object")
        scope = str(body.get("scope") or "").strip().lower()
        if scope not in {"archive_only", "archive_and_telegram"}:
            raise HTTPException(400, "scope must be archive_only or archive_and_telegram")
        channel_id = str(channel_id or "").strip()
        message_id = int(message_id)
        if not channel_id or message_id <= 0:
            raise HTTPException(400, "A valid Telegram channel and message ID are required")

        with db.reading() as rdb:
            photo = rdb.execute(
                "SELECT id,channel_id,message_id,filename,local_path,source_kind FROM photo_library_items WHERE channel_id=? AND message_id=?",
                (channel_id, message_id),
            ).fetchone()
            if not photo:
                raise HTTPException(404, "Photo is no longer in the Archive X library")
            crop_rows = rdb.execute(
                "SELECT crop_path FROM photo_face_detections WHERE photo_id=? AND COALESCE(crop_path,'')<>''",
                (int(photo["id"]),),
            ).fetchall()

        telegram_deleted = False
        telegram_was_already_missing = False
        if scope == "archive_and_telegram":
            client = getattr(telegram, "client", None)
            if client is None or not getattr(telegram, "connected", False):
                raise HTTPException(503, "Telegram is disconnected. The photo was kept in Archive X.")
            try:
                try:
                    entity = await client.get_entity(int(channel_id))
                except (TypeError, ValueError):
                    entity = await client.get_entity(channel_id)
                source_message = await client.get_messages(entity, ids=message_id)
                if source_message:
                    from ..engines.photo_channel_scanner import _is_photo_message
                    is_photo, _filename, _mime = _is_photo_message(source_message)
                    if not is_photo:
                        raise HTTPException(409, "The Telegram message no longer contains an image. Archive X kept the indexed record.")
                    # Revoke deletes the message from Telegram, not just this client history.
                    await client.delete_messages(entity, [message_id], revoke=True)
                    still_there = await client.get_messages(entity, ids=message_id)
                    if still_there:
                        raise HTTPException(502, "Telegram did not confirm deletion. Archive X kept the indexed record and local cache.")
                    telegram_deleted = True
                else:
                    # The Telegram message was already removed; finish clearing its stale local entry.
                    telegram_deleted = True
                    telegram_was_already_missing = True
            except HTTPException:
                raise
            except Exception as exc:
                try:
                    db.log(
                        f"Photo delete failed before local cleanup for {channel_id}/{message_id}: {type(exc).__name__}: {exc}",
                        "WARN", event="photo_library.delete_telegram_failed",
                        details={"channel_id": channel_id, "message_id": message_id, "scope": scope},
                    )
                except Exception:
                    pass
                raise HTTPException(502, f"Telegram deletion failed; the Archive X photo was kept. {type(exc).__name__}: {exc}") from exc

        # Gather only app-managed paths. A stale/malformed DB path must never be
        # allowed to unlink an arbitrary file outside Archive X's user-data area.
        from ..engines.photo_cache import (
            resolve_database_cache_path, photo_original_path, photo_thumbnail_path, photo_preview_path, legacy_photo_thumbnail_path,
        )
        cache_paths = {photo_original_path(channel_id, message_id), photo_thumbnail_path(channel_id, message_id), legacy_photo_thumbnail_path(channel_id, message_id), photo_preview_path(channel_id, message_id)}
        saved_path = resolve_database_cache_path(str(photo["local_path"] or ""))
        if saved_path is not None:
            cache_paths.add(saved_path)
        for crop_row in crop_rows:
            crop_path = resolve_database_cache_path(str(crop_row["crop_path"] or ""))
            if crop_path is not None:
                cache_paths.add(crop_path)

        try:
            with db.lock:
                current = db.conn.execute(
                    "SELECT id FROM photo_library_items WHERE channel_id=? AND message_id=?",
                    (channel_id, message_id),
                ).fetchone()
                if current:
                    photo_id = int(current["id"])
                    db.conn.execute("BEGIN IMMEDIATE")
                    try:
                        db.conn.execute("DELETE FROM photo_face_detections WHERE photo_id=?", (photo_id,))
                        db.conn.execute("DELETE FROM photo_face_analysis WHERE photo_id=?", (photo_id,))
                        db.conn.execute("DELETE FROM photo_library_items WHERE id=?", (photo_id,))
                        db.conn.execute(
                            "DELETE FROM photo_face_groups WHERE NOT EXISTS (SELECT 1 FROM photo_face_detections d WHERE d.group_id=photo_face_groups.id)"
                        )
                        db.conn.execute(
                            "UPDATE photo_library_scan_state SET photos_found=(SELECT COUNT(*) FROM photo_library_items WHERE channel_id=?),updated_at=? WHERE channel_id=?",
                            (channel_id, __import__("telegram_archive.config", fromlist=["now_iso"]).now_iso(), channel_id),
                        )
                        db.conn.commit()
                    except Exception:
                        db.conn.rollback()
                        raise
        except Exception as exc:
            try:
                db.log(
                    f"Photo DB cleanup failed for {channel_id}/{message_id}: {type(exc).__name__}: {exc}",
                    "ERROR", event="photo_library.delete_database_failed",
                    details={"channel_id": channel_id, "message_id": message_id, "scope": scope,
                             "telegram_deleted": telegram_deleted},
                )
            except Exception:
                pass
            if telegram_deleted:
                raise HTTPException(500, "Telegram deletion succeeded, but local database cleanup failed. The cached library record may still be visible; check Operations logs.") from exc
            raise HTTPException(500, f"Could not remove the photo from Archive X: {type(exc).__name__}: {exc}") from exc

        cache_removed = 0
        cache_cleanup_errors = []
        for cache_path in cache_paths:
            try:
                if cache_path.is_file():
                    cache_path.unlink()
                    cache_removed += 1
            except OSError as exc:
                cache_cleanup_errors.append(f"{cache_path.name}: {type(exc).__name__}: {exc}")
        if cache_cleanup_errors:
            try:
                db.log(
                    f"Photo was removed from the library but {len(cache_cleanup_errors)} cached file(s) could not be deleted for {channel_id}/{message_id}",
                    "WARN", event="photo_library.delete_cache_cleanup_pending",
                    details={"channel_id": channel_id, "message_id": message_id, "errors": cache_cleanup_errors[:10]},
                )
            except Exception:
                pass

        try:
            db.log(
                f"Photo removed from Archive X{ ' and Telegram' if scope == 'archive_and_telegram' else '' }: {channel_id}/{message_id}",
                "SUCCESS", event="photo_library.deleted",
                details={"channel_id": channel_id, "message_id": message_id, "scope": scope,
                         "telegram_deleted": telegram_deleted, "telegram_was_already_missing": telegram_was_already_missing,
                         "cache_files_removed": cache_removed, "cache_cleanup_errors": cache_cleanup_errors[:10]},
            )
        except Exception:
            pass

        message = "Removed from Archive X; the Telegram message was left unchanged."
        if scope == "archive_and_telegram":
            message = "Removed from Archive X and deleted from Telegram."
            if telegram_was_already_missing:
                message = "Removed from Archive X; the Telegram message was already missing."
        return {"ok": True, "scope": scope, "telegram_deleted": telegram_deleted,
                "telegram_was_already_missing": telegram_was_already_missing,
                "cache_files_removed": cache_removed, "cache_cleanup_warning": bool(cache_cleanup_errors),
                "message": message}



    # ------------------------------------------------------------------
    # SUBSECTION: Video library actions
    # Favorites, move, delete and export.
    # ------------------------------------------------------------------
    @router.post("/api/library/{media_id}/favorite")
    async def toggle_favorite(media_id: int, request: Request):
        """POST /api/library/{media_id}/favorite: Toggle favorite."""
        require_auth(request)
        with db.lock:
            row = db.conn.execute(
                "UPDATE media SET favorite = CASE WHEN favorite THEN 0 ELSE 1 END WHERE id=? RETURNING favorite",
                (media_id,)
            ).fetchone()
            if not row: raise HTTPException(404, "Not found")
            db.conn.commit()
        return {"ok": True, "favorite": bool(row[0])}


    @router.post("/api/library/bulk-favorite")
    async def bulk_favorite(request: Request):
        """Set favorites idempotently for a bounded list of library media rows."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        raw_ids, favorite = body.get("media_ids"), body.get("favorite")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(400, "media_ids must be a non-empty list")
        if len(raw_ids) > 500:
            raise HTTPException(413, "At most 500 media IDs may be submitted at once")
        if not isinstance(favorite, bool):
            raise HTTPException(400, "favorite must be a boolean")
        try:
            ids = list(dict.fromkeys(int(value) for value in raw_ids if int(value) > 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "media_ids must contain positive integers")
        if len(ids) > 500:
            raise HTTPException(413, "At most 500 media items may be updated at once")
        if not ids:
            raise HTTPException(400, "media_ids must contain positive integers")
        marks = ",".join("?" for _ in ids)
        with db.lock:
            rows = db.conn.execute(f"SELECT id FROM media WHERE id IN ({marks})", ids).fetchall()
            valid_ids = [int(row["id"]) for row in rows]
            if valid_ids:
                valid_marks = ",".join("?" for _ in valid_ids)
                db.conn.execute(
                    f"UPDATE media SET favorite=?, updated_at=? WHERE id IN ({valid_marks})",
                    [1 if favorite else 0, config.now_iso(), *valid_ids],
                )
            db.conn.commit()
        valid_set = set(valid_ids)
        return {"ok": True, "favorite": favorite, "updated": len(valid_ids),
                "media_ids": valid_ids, "missing_ids": [value for value in ids if value not in valid_set]}

    @router.post("/api/library/move-delete")
    async def library_move_delete(request: Request):
        """Move one selected media message with destination verification before deletion."""
        require_auth(request)
        if not telegram.connected or telegram.client is None:
            raise HTTPException(503, "Telegram is not connected")
        try:
            from ..core.telegram_registry import retry_pending_move_deletions
            await retry_pending_move_deletions(db, telegram.client, limit=5)
        except Exception as cleanup_exc:
            db.log(f"Pending verified-move deletion cleanup deferred: {type(cleanup_exc).__name__}: {cleanup_exc}", "WARN")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        if body.get("confirm_delete") is not True:
            raise HTTPException(400, "confirm_delete=true is required")
        try:
            channel_id = int(body.get("channel_id"))
            message_id = int(body.get("message_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "channel_id and message_id must be integers")
        destination_raw = str(body.get("destination_channel") or "").strip()
        if not destination_raw:
            raise HTTPException(400, "Choose a destination channel")
        with db.lock:
            media = db.conn.execute(
                "SELECT id,source_channel,source_message_id,filename,title,thumbnail_message_id,size,duration,oshash,video_phash FROM media WHERE source_channel=? AND source_message_id=?",
                (str(channel_id), message_id),
            ).fetchone()
        if not media:
            raise HTTPException(404, "This video is no longer in the local index; refresh the Library")
        try:
            source = await telegram.client.get_entity(channel_id)
            dest_arg = int(destination_raw) if destination_raw.lstrip("+-").isdigit() else destination_raw
            destination = await telegram.client.get_entity(dest_arg)
            if int(getattr(source, "id", 0) or 0) == int(getattr(destination, "id", 0) or 0):
                raise HTTPException(400, "Destination must be a different channel")
            # Keep the stored peer ID in Archive X's canonical marked form.
            new_channel = str(getattr(destination, "id", dest_arg))
            try:
                from telethon import utils as telethon_utils
                new_channel = str(telethon_utils.get_peer_id(destination))
            except Exception:
                if not new_channel.startswith("-100"):
                    new_channel = "-100" + new_channel

            # A split logical card must move every physical volume as a group.
            # Copy and verify the complete destination sequence first; only then
            # does the helper attempt any source deletion.
            from ..core.split_files import load_split_group, SplitFileError
            try:
                split_group = load_split_group(db, str(channel_id), message_id)
            except SplitFileError as exc:
                raise HTTPException(409, f"Split group is inconsistent; scan/review it before moving: {exc}") from exc
            if split_group is not None:
                if str(split_group.get("status") or "").upper() != "VERIFIED":
                    raise HTTPException(409, "This split file is incomplete or unverified; its volumes were not moved")
                from ..core.video_locks import video_operation_lock
                from ..engines.safe_message_move import move_split_group_verified
                from ..core.telegram_registry import register_known_message, rekey_location, ensure_telegram_registry
                from ..playback.stream_server import make_record
                from pathlib import Path as _Path

                ensure_telegram_registry(db)
                group_parts = sorted(split_group.get("parts") or [], key=lambda row: int(row.get("part_index") or 0))
                first_source = await telegram.client.get_messages(source, ids=message_id)
                if not first_source:
                    raise HTTPException(404, "The first split volume no longer exists in the source channel")
                first_record = make_record(first_source) or {
                    "id": message_id, "name": str(group_parts[0].get("telegram_filename") or ""),
                    "size": int(group_parts[0].get("size_bytes") or 0), "mime": "application/octet-stream",
                }
                first_registry = register_known_message(
                    db, channel_id=str(channel_id), message_id=message_id, record=first_record,
                    media_id=int(media["id"]), thumbnail_message_id=int(split_group.get("thumbnail_message_id") or 0) or None,
                )
                first_document = (getattr(first_source, "document", None)
                                  or getattr(getattr(first_source, "media", None), "document", None))
                first_document_id = str(first_registry.get("document_id") or first_record.get("document_id")
                                        or getattr(first_document, "id", "") or "")
                if first_document_id:
                    with db.lock:
                        duplicate_volume = db.conn.execute(
                            "SELECT l.message_id FROM telegram_document_locations l "
                            "JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
                            "WHERE l.channel_id=? AND r.document_id=? AND l.location_state='PRESENT' LIMIT 1",
                            (new_channel, first_document_id),
                        ).fetchone()
                    if duplicate_volume:
                        raise HTTPException(409, "The destination already contains this split group's primary volume; verify the existing group before moving it again")
                if not int(split_group.get("thumbnail_message_id") or 0):
                    # Older/foreign split uploads have no saved contact-sheet
                    # link; it is the image posted right after the last volume.
                    from ..engines.safe_message_move import discover_companion_thumbnail
                    from ..core.telegram_registry import thumbnail_message_is_unshared
                    last_part_id = max(int(row.get("message_id") or 0) for row in group_parts)
                    found = await discover_companion_thumbnail(
                        telegram.client, source, last_part_id, str(split_group.get("logical_filename") or ""),
                    )
                    if found and thumbnail_message_is_unshared(
                        db, channel_id=str(channel_id), video_message_id=message_id, thumbnail_message_id=found,
                    ):
                        split_group = dict(split_group, thumbnail_message_id=found)
                async with video_operation_lock(source_channel=str(channel_id), source_message_id=message_id):
                    split_move = await move_split_group_verified(telegram.client, source, destination, split_group)

                moved_by_index = {int(row["part_index"]): row for row in split_move.get("parts") or []}
                destination_thumbnail = dict(split_move.get("thumbnail") or {})
                new_thumbnail_id = int(destination_thumbnail.get("destination_message_id") or 0)
                now = config.now_iso()

                def _registry_record(moved_row: dict, fallback_name: str, fallback_size: int) -> dict:
                    return {
                        "id": int(moved_row.get("destination_message_id") or 0),
                        "name": str(moved_row.get("filename") or fallback_name),
                        "size": int(moved_row.get("size") or fallback_size),
                        "document_id": moved_row.get("document_id"),
                        "access_hash": str(moved_row.get("access_hash") or ""),
                        "file_reference_b64": str(moved_row.get("file_reference_b64") or ""),
                        "caption": str(moved_row.get("caption") or ""),
                        "date": str(moved_row.get("date") or ""),
                        "mime": str(moved_row.get("mime") or "application/octet-stream"),
                    }

                # Re-key all volume identities and register destination copies.
                # This also supports old split groups whose parts were not in the
                # document registry before this version.
                for part in group_parts:
                    index = int(part["part_index"])
                    moved_part = moved_by_index[index]
                    old_part_id = int(part["message_id"])
                    new_part_id = int(moved_part["destination_message_id"])
                    from ..core.split_files import split_part_expected_name
                    part_name = split_part_expected_name(str(split_group.get('logical_filename') or ''), index, str(part.get("telegram_filename") or ""))
                    dest_record = _registry_record(moved_part, part_name, int(part["size_bytes"]))
                    with db.lock:
                        has_old_registry = db.conn.execute(
                            "SELECT 1 FROM telegram_document_locations WHERE channel_id=? AND message_id=?",
                            (str(channel_id), old_part_id),
                        ).fetchone() is not None
                    if has_old_registry:
                        rekey_location(
                            db, old_channel=str(channel_id), old_message=old_part_id,
                            new_channel=new_channel, new_message=new_part_id,
                            thumbnail_message_id=(new_thumbnail_id or None) if index == 1 else None,
                            destination_record=dest_record,
                        )
                    else:
                        register_known_message(
                            db, channel_id=new_channel, message_id=new_part_id, record=dest_record,
                            media_id=int(media["id"]) if index == 1 else None,
                            thumbnail_message_id=(new_thumbnail_id or None) if index == 1 else None,
                        )
                    if not moved_part.get("source_deleted"):
                        with db.lock:
                            db.conn.execute(
                                "UPDATE telegram_document_locations SET location_state='MOVED_PENDING_DELETE',last_seen_at=? WHERE channel_id=? AND message_id=?",
                                (now, str(channel_id), old_part_id),
                            )
                            db.conn.commit()

                if destination_thumbnail:
                    old_thumb_id = int(split_group.get("thumbnail_message_id") or 0)
                    new_thumb_id = int(destination_thumbnail.get("destination_message_id") or 0)
                    thumb_name = str(destination_thumbnail.get("filename") or f"{_Path(str(split_group.get('logical_filename') or 'video')).stem}.jpg")
                    thumb_record = _registry_record(destination_thumbnail, thumb_name, int(destination_thumbnail.get("size") or 0))
                    with db.lock:
                        has_old_thumb_registry = old_thumb_id > 0 and db.conn.execute(
                            "SELECT 1 FROM telegram_document_locations WHERE channel_id=? AND message_id=?",
                            (str(channel_id), old_thumb_id),
                        ).fetchone() is not None
                    if has_old_thumb_registry:
                        rekey_location(db, old_channel=str(channel_id), old_message=old_thumb_id,
                                       new_channel=new_channel, new_message=new_thumb_id,
                                       destination_record=thumb_record)
                    else:
                        register_known_message(db, channel_id=new_channel, message_id=new_thumb_id, record=thumb_record)
                    if old_thumb_id and not destination_thumbnail.get("source_deleted"):
                        with db.lock:
                            db.conn.execute(
                                "UPDATE telegram_document_locations SET location_state='MOVED_PENDING_DELETE',last_seen_at=? WHERE channel_id=? AND message_id=?",
                                (now, str(channel_id), old_thumb_id),
                            )
                            db.conn.commit()

                with db.lock:
                    db.conn.execute(
                        "INSERT OR IGNORE INTO channels(telegram_id,title,username,updated_at) VALUES(?,?,?,?)",
                        (new_channel, str(getattr(destination, "title", None) or getattr(destination, "username", None) or f"Channel {new_channel}"),
                         str(getattr(destination, "username", None) or ""), now),
                    )
                    from ..core.split_files import split_part_expected_name
                    for part in group_parts:
                        index = int(part["part_index"])
                        moved_part = moved_by_index[index]
                        db.conn.execute(
                            "UPDATE split_file_parts SET destination_channel=?,message_id=?,telegram_filename=? WHERE group_id=? AND part_index=?",
                            (new_channel, int(moved_part["destination_message_id"]),
                             split_part_expected_name(str(split_group.get('logical_filename') or ''), index, str(part.get("telegram_filename") or "")),
                             str(split_group["group_id"]), index),
                        )
                    db.conn.execute(
                        "UPDATE split_file_groups SET destination_channel=?,primary_message_id=?,thumbnail_message_id=?,status='VERIFIED',updated_at=? WHERE group_id=?",
                        (new_channel, int(split_move["primary_message_id"]), new_thumbnail_id or None, now, str(split_group["group_id"])),
                    )
                    db.conn.execute(
                        "UPDATE media SET source_channel=?,source_message_id=?,filename=?,size=?,thumbnail_message_id=?,status=CASE WHEN status='SOURCE_DELETED' THEN 'SCANNED' ELSE status END,updated_at=? WHERE id=?",
                        (new_channel, int(split_move["primary_message_id"]), str(split_group.get("logical_filename") or media["filename"] or media["title"] or ""),
                         int(split_group.get("total_size") or media["size"] or 0), new_thumbnail_id or None, now, int(media["id"])),
                    )
                    secondary_ids = [int(part["message_id"]) for part in group_parts if int(part["part_index"]) != 1]
                    if secondary_ids:
                        marks = ",".join("?" for _ in secondary_ids)
                        db.conn.execute(
                            f"UPDATE media SET status='SOURCE_DELETED',updated_at=? WHERE source_channel=? AND source_message_id IN ({marks}) AND id<>?",
                            (now, str(channel_id), *secondary_ids, int(media["id"])),
                        )
                    tables = {str(row[0]) for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                    old_primary_id = int(message_id)
                    new_primary_id = int(split_move["primary_message_id"])
                    for table, channel_col, message_col in (
                        ("thumbnail_cache", "source_channel", "video_message_id"),
                        ("timeline_preview_cache", "source_channel", "video_message_id"),
                    ):
                        if table not in tables:
                            continue
                        cached = db.conn.execute(
                            f"SELECT * FROM {table} WHERE {channel_col}=? AND {message_col}=?",
                            (str(channel_id), old_primary_id),
                        ).fetchone()
                        if cached:
                            cache = dict(cached)
                            cache[channel_col], cache[message_col] = new_channel, new_primary_id
                            columns = list(cache)
                            try:
                                db.conn.execute(
                                    f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                                    [cache[column] for column in columns],
                                )
                                db.conn.execute(
                                    f"DELETE FROM {table} WHERE {channel_col}=? AND {message_col}=?",
                                    (str(channel_id), old_primary_id),
                                )
                            except Exception as cache_exc:
                                db.log(f"Could not re-key {table} for split move {channel_id}/{old_primary_id}: {cache_exc}", "WARN")
                    if "asset_registry" in tables:
                        try:
                            db.conn.execute(
                                "UPDATE asset_registry SET entity_id=?,updated_at=? WHERE entity_type='telegram_video' AND entity_id=?",
                                (f"{new_channel}:{new_primary_id}", now, f"{channel_id}:{old_primary_id}"),
                            )
                        except Exception as asset_exc:
                            db.log(f"Could not re-key split playback assets: {asset_exc}", "WARN")
                    # Each deletion retry is tied to the verified destination
                    # document identity, never a filename-only guess.
                    cleanup_rows = [row for row in split_move.get("parts") or [] if not row.get("source_deleted")]
                    if destination_thumbnail and not destination_thumbnail.get("source_deleted"):
                        cleanup_rows.append(destination_thumbnail)
                    for move_row in cleanup_rows:
                        source_id = int(move_row.get("source_message_id") or 0)
                        if source_id <= 0:
                            continue
                        db.conn.execute(
                            "INSERT INTO telegram_move_cleanup(source_channel,source_message_id,destination_channel,destination_message_id,document_id,last_error,next_attempt_at,status,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,datetime('now','+1 minute'),'PENDING',?,?) "
                            "ON CONFLICT(source_channel,source_message_id) DO UPDATE SET destination_channel=excluded.destination_channel,destination_message_id=excluded.destination_message_id,document_id=excluded.document_id,last_error=excluded.last_error,next_attempt_at=excluded.next_attempt_at,status='PENDING',updated_at=excluded.updated_at",
                            (str(channel_id), source_id, new_channel, int(move_row.get("destination_message_id") or 0),
                             str(move_row.get("document_id") or "") or None,
                             str(move_row.get("deletion_error") or ""), now, now),
                        )
                    db.conn.commit()

                try:
                    from ..core.telegram_registry import advance_channel_high_water
                    moved_ids = [int(row.get("destination_message_id") or 0) for row in split_move.get("parts") or []]
                    if destination_thumbnail:
                        moved_ids.append(int(destination_thumbnail.get("destination_message_id") or 0))
                    advance_channel_high_water(db, new_channel, max(moved_ids or [0]))
                except Exception as watermark_exc:
                    db.log(f"Split group moved but destination quick-scan watermark update needs retry: {type(watermark_exc).__name__}: {watermark_exc}", "WARN")
                db.log(
                    f"Moved split group {channel_id}/{message_id} to {new_channel}/{split_move['primary_message_id']} "
                    f"({len(group_parts)} volumes, {split_move['total_size']} bytes); source_deletion_pending={split_move['source_deletion_pending']}",
                    event="library.move_delete",
                )
                result = {
                    "ok": True, "split_group": True, "moved": split_move,
                    "channel_id": new_channel, "message_id": int(split_move["primary_message_id"]),
                    "logical_filename": str(split_group.get("logical_filename") or ""),
                    "part_count": len(group_parts), "total_size": int(split_group.get("total_size") or 0),
                }
                if split_move.get("thumbnail_warning"):
                    result["companion_warning"] = split_move["thumbnail_warning"]
                    db.log(f"Split move contact sheet for {channel_id}/{message_id}: {split_move['thumbnail_warning']}", "WARN", event="library.move_companion")
                if split_move.get("source_deletion_pending"):
                    result["source_cleanup_pending"] = True
                    result["source_warning"] = "The full destination group is verified; one or more source messages remain and Archive X will retry their deletion."
                return result

            # Register metadata from the source message only; no media bytes are downloaded.
            source_message = await telegram.client.get_messages(source, ids=message_id)
            if not source_message:
                raise HTTPException(404, "Source message no longer exists in Telegram")
            from ..playback.stream_server import make_record
            from ..core.telegram_registry import (register_known_message, document_for_location,
                                                  rekey_location, resolve_linked_thumbnail_message_id)
            source_record = make_record(source_message) or {
                "id": message_id, "name": str(media["filename"] or media["title"] or ""),
                "size": int(media["size"] or 0), "duration": float(media["duration"] or 0),
                "oshash": str(media["oshash"] or ""), "phash": str(media["video_phash"] or ""),
            }
            register_known_message(db, channel_id=str(channel_id), message_id=message_id,
                                  record=source_record, media_id=int(media["id"]),
                                  thumbnail_message_id=int(media["thumbnail_message_id"] or 0) or None)
            source_registry = document_for_location(db, str(channel_id), message_id) or {}
            document_id = str(source_registry.get("document_id") or source_record.get("document_id") or "")
            # Resolve the companion only from explicit Archive X associations.
            # Never guess neighbouring Telegram message IDs, which could refer to
            # an unrelated photo in the source channel.
            associated_thumbnail = resolve_linked_thumbnail_message_id(
                db, channel_id=str(channel_id), video_message_id=message_id,
                media_thumbnail_message_id=media["thumbnail_message_id"],
                location=source_registry,
            )
            if not associated_thumbnail:
                # No saved link (older uploads, other tools, Telegram app):
                # look for the contact sheet posted right after the video.
                from ..engines.safe_message_move import discover_companion_thumbnail
                from ..core.telegram_registry import thumbnail_message_is_unshared
                found = await discover_companion_thumbnail(
                    telegram.client, source, message_id,
                    str(media["filename"] or source_record.get("name") or ""),
                )
                if found and thumbnail_message_is_unshared(
                    db, channel_id=str(channel_id), video_message_id=message_id, thumbnail_message_id=found,
                ):
                    associated_thumbnail = found
            if document_id:
                with db.lock:
                    duplicate = db.conn.execute(
                        "SELECT l.message_id FROM telegram_document_locations l "
                        "JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
                        "WHERE l.channel_id=? AND r.document_id=? AND l.location_state='PRESENT' LIMIT 1",
                        (new_channel, document_id),
                    ).fetchone()
                if duplicate:
                    return {"ok": True, "skipped": True, "reason": "already_in_destination", "document_id": document_id,
                            "channel_id": new_channel, "message_id": int(duplicate["message_id"]),
                            "source_channel": str(channel_id), "source_message_id": message_id}

            from ..engines.safe_message_move import move_message_verified
            from ..core.video_locks import video_operation_lock
            async with video_operation_lock(source_channel=str(channel_id), source_message_id=message_id):
                moved = await move_message_verified(telegram.client, source, destination, message_id)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            db.log(f"Library move/delete failed for {channel_id}/{message_id}: {type(exc).__name__}: {exc}", "ERROR", event="library.move_delete")
            raise HTTPException(502, f"Move not completed: {str(exc)[:240]}") from exc

        # Move the associated contact sheet as a companion message when present.
        old_channel, old_message = str(channel_id), int(message_id)
        old_thumbnail = int(associated_thumbnail or 0)
        new_thumbnail = 0
        companion_error = ""
        if old_thumbnail > 0 and old_thumbnail != old_message:
            try:
                thumbnail_move = await move_message_verified(telegram.client, source, destination, old_thumbnail)
                new_thumbnail = int(thumbnail_move.get("destination_message_id") or 0)
                if not thumbnail_move.get("source_deleted"):
                    with db.lock:
                        db.conn.execute(
                            "INSERT INTO telegram_move_cleanup(source_channel,source_message_id,destination_channel,destination_message_id,document_id,last_error,next_attempt_at,status,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,datetime('now','+1 minute'),'PENDING',?,?) "
                            "ON CONFLICT(source_channel,source_message_id) DO UPDATE SET last_error=excluded.last_error,next_attempt_at=excluded.next_attempt_at,status='PENDING',updated_at=excluded.updated_at",
                            (old_channel, old_thumbnail, new_channel, new_thumbnail,
                             (str(thumbnail_move.get("document_id") or "")
                              or (f"photo:{int(thumbnail_move.get('photo_id') or 0)}" if int(thumbnail_move.get("photo_id") or 0) > 0 else "")
                              or None),
                             str(thumbnail_move.get("deletion_error") or ""), config.now_iso(), config.now_iso()),
                        )
                        db.conn.execute(
                            "UPDATE telegram_document_locations SET location_state='MOVED_PENDING_DELETE',last_seen_at=? "
                            "WHERE channel_id=? AND message_id=?",
                            (config.now_iso(), old_channel, old_thumbnail),
                        )
                        db.conn.commit()
                else:
                    with db.lock:
                        db.conn.execute(
                            "UPDATE telegram_document_locations SET location_state='MOVED_BY_ARCHIVE_X',last_seen_at=? "
                            "WHERE channel_id=? AND message_id=?",
                            (config.now_iso(), old_channel, old_thumbnail),
                        )
                        db.conn.commit()
            except Exception as exc:
                # The video itself has moved; never leave its contact sheet
                # behind in the old channel. Delete it there (the destination
                # copy could not be made, Archive X regenerates contact sheets).
                companion_error = f"Contact sheet could not be copied ({type(exc).__name__}: {exc})"
                try:
                    await telegram.client.delete_messages(source, [old_thumbnail])
                    remaining = await telegram.client.get_messages(source, ids=old_thumbnail)
                    if remaining is None:
                        companion_error += "; it was removed from the source channel"
                        with db.lock:
                            db.conn.execute(
                                "UPDATE telegram_document_locations SET location_state='DELETED_BY_ARCHIVE_X',last_seen_at=? "
                                "WHERE channel_id=? AND message_id=?",
                                (config.now_iso(), old_channel, old_thumbnail),
                            )
                            db.conn.commit()
                    else:
                        companion_error += "; it is still in the source channel"
                except Exception as delete_exc:
                    companion_error += f"; removing it from the source channel failed: {type(delete_exc).__name__}"
                db.log(f"Library contact-sheet move for {old_channel}/{old_thumbnail}: {companion_error}", "WARN", event="library.move_companion")

        now = config.now_iso()
        with db.lock:
            # Destination registration is automatic even when it has never been scanned.
            db.conn.execute(
                "INSERT OR IGNORE INTO channels(telegram_id,title,username,updated_at) VALUES(?,?,?,?)",
                (new_channel, str(getattr(destination, "title", None) or getattr(destination, "username", None) or f"Channel {new_channel}"),
                 str(getattr(destination, "username", None) or ""), now),
            )
            db.conn.execute(
                "UPDATE media SET source_channel=?,source_message_id=?,thumbnail_message_id=?,status=CASE WHEN status='SOURCE_DELETED' THEN 'SCANNED' ELSE status END,updated_at=? WHERE id=?",
                (new_channel, int(moved["destination_message_id"]), new_thumbnail or None, now, int(media["id"])),
            )
            # Preserve cached snapshots/indexes by re-keying their location, rather than deleting them.
            tables = {str(x[0]) for x in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            for table, channel_col, message_col in (
                ("thumbnail_cache", "source_channel", "video_message_id"),
                ("timeline_preview_cache", "source_channel", "video_message_id"),
            ):
                if table not in tables:
                    continue
                cached = db.conn.execute(f"SELECT * FROM {table} WHERE {channel_col}=? AND {message_col}=?", (old_channel, old_message)).fetchone()
                if cached:
                    cache = dict(cached)
                    cache[channel_col], cache[message_col] = new_channel, int(moved["destination_message_id"])
                    columns = list(cache)
                    rekeyed = False
                    try:
                        db.conn.execute(
                            f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                            [cache[c] for c in columns],
                        )
                        rekeyed = True
                    except Exception as exc:
                        db.log(f"Could not re-key {table} for moved video {old_channel}/{old_message}; original cache row was retained: {exc}", "WARN")
                    if rekeyed:
                        # The cache row follows the video to its new Telegram location;
                        # update its companion ID as well so it never points back to A.
                        if table == "thumbnail_cache":
                            db.conn.execute(
                                "UPDATE thumbnail_cache SET thumbnail_message_id=?,updated_at=? "
                                "WHERE source_channel=? AND video_message_id=?",
                                (new_thumbnail or None, now, new_channel, int(moved["destination_message_id"])),
                            )
                        db.conn.execute(f"DELETE FROM {table} WHERE {channel_col}=? AND {message_col}=?", (old_channel, old_message))
            if "asset_registry" in tables:
                old_entity = f"{old_channel}:{old_message}"
                new_entity = f"{new_channel}:{int(moved['destination_message_id'])}"
                try:
                    db.conn.execute(
                        "UPDATE asset_registry SET entity_id=?,updated_at=? WHERE entity_type='telegram_video' AND entity_id=?",
                        (new_entity, now, old_entity),
                    )
                except Exception as exc:
                    db.log(f"Could not re-key media assets for {old_entity}: {exc}", "WARN")
            rekey_location(db, old_channel=old_channel, old_message=old_message,
                           new_channel=new_channel, new_message=int(moved["destination_message_id"]),
                           thumbnail_message_id=new_thumbnail or None, destination_record=moved)
            if not new_thumbnail:
                # Keep the old companion associated with its source location, but
                # never advertise its message ID as a destination-channel thumbnail.
                db.conn.execute(
                    "UPDATE telegram_document_locations SET thumbnail_message_id=NULL WHERE channel_id=? AND message_id=?",
                    (new_channel, int(moved["destination_message_id"])),
                )
            if not moved.get("source_deleted"):
                db.conn.execute(
                    "INSERT INTO telegram_move_cleanup(source_channel,source_message_id,destination_channel,destination_message_id,document_id,last_error,next_attempt_at,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,datetime('now','+1 minute'),'PENDING',?,?) "
                    "ON CONFLICT(source_channel,source_message_id) DO UPDATE SET destination_channel=excluded.destination_channel,destination_message_id=excluded.destination_message_id,document_id=excluded.document_id,last_error=excluded.last_error,next_attempt_at=excluded.next_attempt_at,status='PENDING',updated_at=excluded.updated_at",
                    (old_channel, old_message, new_channel, int(moved["destination_message_id"]),
                     str(moved.get("document_id") or "") or None, str(moved.get("deletion_error") or ""), now, now),
                )
                db.conn.execute(
                    "UPDATE telegram_document_locations SET location_state='MOVED_PENDING_DELETE',last_seen_at=? WHERE channel_id=? AND message_id=?",
                    (now, old_channel, old_message),
                )
            db.conn.commit()
        try:
            from ..core.telegram_registry import advance_channel_high_water
            advance_channel_high_water(db, new_channel, int(moved["destination_message_id"]))
        except Exception as watermark_exc:
            db.log(f"Moved video is registered, but destination quick-scan high-water update needs retry: {type(watermark_exc).__name__}: {watermark_exc}", "WARN")
        db.log(f"Moved video {old_channel}/{old_message} to {new_channel}/{moved['destination_message_id']} after destination verification; source_deleted={bool(moved.get('source_deleted'))}", event="library.move_delete")
        result = {"ok": True, "moved": moved, "channel_id": new_channel, "message_id": int(moved["destination_message_id"])}
        if not moved.get("source_deleted"):
            result["source_cleanup_pending"] = True
            result["source_warning"] = "Destination is verified; the source message remains and Archive X will retry deletion during a later scan or move."
        if companion_error:
            result["companion_warning"] = companion_error
        elif not old_thumbnail:
            db.log(
                f"No explicitly associated Telegram thumbnail message was recorded for {old_channel}/{old_message}; "
                "no separate thumbnail message was touched.",
                "INFO", event="library.move_thumbnail_absent",
            )
        return result

    @router.post("/api/library/delete")
    async def library_delete_video(request: Request):
        """Delete a video from its Telegram source and hide its local Library record."""
        require_auth(request)
        if not telegram.connected or telegram.client is None:
            raise HTTPException(503, "Telegram is not connected")
        from ..core.telegram_registry import (
            resolve_linked_thumbnail_message_id,
            thumbnail_message_is_unshared,
        )
        from ..engines.media_utils import is_image_message
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        if body.get("confirm_delete") is not True:
            raise HTTPException(400, "confirm_delete=true is required")
        try:
            channel_id = int(body.get("channel_id"))
            message_id = int(body.get("message_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "channel_id and message_id must be integers")
        with db.lock:
            media = db.conn.execute(
                "SELECT id,source_channel,source_message_id,filename,title,thumbnail_message_id FROM media "
                "WHERE source_channel=? AND source_message_id=? AND COALESCE(status,'')<>'SOURCE_DELETED'",
                (str(channel_id), message_id),
            ).fetchone()
            table_names = {str(row[0]) for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            location = None
            if "telegram_document_locations" in table_names:
                location_row = db.conn.execute(
                    "SELECT thumbnail_message_id FROM telegram_document_locations "
                    "WHERE channel_id=? AND message_id=?",
                    (str(channel_id), message_id),
                ).fetchone()
                location = dict(location_row) if location_row else None
        if not media:
            raise HTTPException(404, "This video is no longer in the active Library; refresh the Library")
        thumbnail_id = resolve_linked_thumbnail_message_id(
            db,
            channel_id=channel_id,
            video_message_id=message_id,
            media_thumbnail_message_id=media["thumbnail_message_id"],
            location=location,
        )
        try:
            source = await telegram.client.get_entity(channel_id)
        except Exception as exc:
            db.log(
                f"Library delete failed for {channel_id}/{message_id}: {type(exc).__name__}: {exc}",
                "ERROR", event="library.delete",
            )
            raise HTTPException(502, f"Telegram could not confirm deletion: {str(exc)[:220]}") from exc

        delete_ids = [message_id]
        thumbnail_deleted = False
        warnings: list[str] = []
        if thumbnail_id:
            if not thumbnail_message_is_unshared(
                db,
                channel_id=channel_id,
                video_message_id=message_id,
                thumbnail_message_id=thumbnail_id,
            ):
                warnings.append(
                    f"Linked thumbnail message {thumbnail_id} is shared or could not be verified, so it was kept"
                )
            else:
                try:
                    thumbnail_message = await telegram.client.get_messages(source, ids=thumbnail_id)
                except Exception as exc:
                    raise HTTPException(
                        502,
                        f"Could not verify the linked thumbnail; no deletion was attempted: {str(exc)[:180]}",
                    ) from exc
                if not thumbnail_message:
                    warnings.append(
                        f"Linked thumbnail message {thumbnail_id} was already missing; only the video was deleted"
                    )
                elif is_image_message(thumbnail_message):
                    delete_ids.append(thumbnail_id)
                    thumbnail_deleted = True
                else:
                    warnings.append(
                        f"Linked message {thumbnail_id} was not an image thumbnail, so it was kept"
                    )

        try:
            # Delete a verified, exclusive thumbnail alongside its video in one
            # Telegram request; never infer a companion from its filename or ID.
            await telegram.client.delete_messages(source, delete_ids)
        except Exception as exc:
            db.log(
                f"Library delete failed for {channel_id}/{message_id}: {type(exc).__name__}: {exc}",
                "ERROR", event="library.delete",
            )
            raise HTTPException(502, f"Telegram could not confirm deletion: {str(exc)[:220]}") from exc

        now = config.now_iso()
        media_id = int(media["id"])
        entity_id = f"{channel_id}:{message_id}"
        with db.lock:
            db.conn.execute(
                "UPDATE media SET status='SOURCE_DELETED',updated_at=? WHERE id=?",
                (now, media_id),
            )
            table_names = {str(row[0]) for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "resource_state" in table_names:
                db.conn.execute(
                    "UPDATE resource_state SET snapshot_state='UNKNOWN',last_reason='user_deleted_source',updated_at=? WHERE media_id=?",
                    (now, media_id),
                )
            if "asset_registry" in table_names:
                db.conn.execute(
                    "UPDATE asset_registry SET state='MISSING',updated_at=? WHERE media_id=? OR (entity_type='telegram_video' AND entity_id=?)",
                    (now, media_id, entity_id),
                )
            if "telegram_document_locations" in table_names:
                for deleted_id in delete_ids:
                    db.conn.execute(
                        "UPDATE telegram_document_locations SET location_state='DELETED_BY_ARCHIVE_X',"
                        "last_seen_at=?,thumbnail_message_id=NULL WHERE channel_id=? AND message_id=?",
                        (now, str(channel_id), int(deleted_id)),
                    )
            for table in ("thumbnail_cache", "timeline_preview_cache"):
                if table in table_names:
                    db.conn.execute(
                        f"DELETE FROM {table} WHERE source_channel=? AND video_message_id=?",
                        (str(channel_id), message_id),
                    )
            if "resource_events" in table_names:
                db.conn.execute(
                    "INSERT INTO resource_events(media_id,event_type,reason,detail,created_at) VALUES(?,?,?,?,?)",
                    (
                        media_id, "SOURCE_DELETED", "user_requested_delete",
                        f"Deleted from Telegram via Library; associated_thumbnail_deleted={thumbnail_deleted}", now,
                    ),
                )
            db.conn.commit()
        db.log(
            f"Deleted Telegram video {channel_id}/{message_id} from Library on explicit user request; "
            f"thumbnail_deleted={thumbnail_deleted}",
            event="library.delete",
        )
        return {
            "ok": True, "deleted": True, "channel_id": channel_id, "message_id": message_id,
            "media_id": media_id, "thumbnail_deleted": thumbnail_deleted,
            "thumbnail_message_id": thumbnail_id if thumbnail_deleted else None, "warnings": warnings,
        }

    @router.get("/api/metadata/reference/export")
    async def export_reference_database(request: Request):
        """Regenerate the compact TXT reference (section 7) and return it.

        SQLite stays authoritative -- this is a lightweight, regenerate-
        on-demand cache for AI reference/debugging/manual inspection, never
        the other way around."""
        require_auth(request)
        from ..metadata.reference import export_reference_txt
        from fastapi.responses import PlainTextResponse
        path = config.REFERENCE_EXPORT_PATH
        count = await asyncio.to_thread(export_reference_txt, db, path)
        text = await asyncio.to_thread(path.read_text, "utf-8")
        return PlainTextResponse(text, headers={
            "X-Reference-Count": str(count),
            "Content-Disposition": 'attachment; filename="reference_export.txt"',
        })


    # ------------------------------------------------------------------
    # SUBSECTION: Video mapping
    # Lookup, mapping locks and rechecks.
    # ------------------------------------------------------------------
    @router.get("/api/videos/lookup")
    async def lookup_video(request: Request):
        """Find the locally catalogued media ID without blocking on the writer lock."""
        require_auth(request)
        cid = str(request.query_params.get("channel_id") or "").strip()
        mid = int(request.query_params.get("message_id") or 0)
        def _lookup_saved_id():
            with db.reading() as rdb:
                row = rdb.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?", (cid, mid)).fetchone()
                return int(row["id"]) if row else None
        return {"media_id": await asyncio.to_thread(_lookup_saved_id)}

    @router.get("/api/mapping/locks")
    async def mapping_locks(request: Request):
        """GET /api/mapping/locks: Mapping locks."""
        require_auth(request)
        ids=[int(x) for x in request.query_params.get("media_ids","").split(",") if x.strip().isdigit()]
        if not ids: return []
        marks=','.join('?' for _ in ids)
        def _read_locks():
            with db.reading() as rdb:
                return [dict(r) for r in rdb.execute(f"SELECT video_id,field,locked,reason,updated_at FROM metadata_mapping_locks WHERE video_id IN ({marks}) AND locked=1", ids).fetchall()]
        return await asyncio.to_thread(_read_locks)

    @router.post("/api/mapping/bulk")
    async def mapping_bulk(request: Request):
        """POST /api/mapping/bulk: Mapping bulk."""
        require_auth(request); body=await request.json(); ids=[]
        with db.lock:
            # V29.4.131: one request is all-or-nothing. An error part-way
            # (e.g. unknown performer) used to leave earlier deletes pending,
            # and the next commit anywhere saved them.
            try:
                for item in body.get("videos") or []:
                    try:
                        row=db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?",(str(item.get("channel_id") or ""),int(item.get("message_id") or 0))).fetchone()
                        if row: ids.append(int(row["id"]))
                    except Exception: pass
                ids += [int(x) for x in (body.get("media_ids") or []) if str(x).isdigit()]
                ids=list(dict.fromkeys(ids))
                field=str(body.get("field") or "").lower().strip(); action=str(body.get("action") or "move").lower(); target=str(body.get("target_name") or "").strip(); performer_target=str(body.get("performer_name") or target).strip(); studio_target=str(body.get("studio_name") or body.get("site_name") or target).strip(); lock=bool(body.get("lock",True)); now=config.now_iso()
                if field not in {"title","performer","site","performer_site","all"}: raise HTTPException(400,"Invalid mapping field")
                if not ids: raise HTTPException(400,"No videos selected")
                for vid in ids:
                    if field in {"title","all"}:
                        old=db.conn.execute("SELECT title FROM media WHERE id=?",(vid,)).fetchone(); new='' if action=='unknown' else target
                        if action=='unknown' or target: db.conn.execute("UPDATE media SET title=?,updated_at=? WHERE id=?",(new,now,vid))
                        db.conn.execute("INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",(vid,'title',old['title'] if old else '',new,'manual',now))
                    if field in {"performer","performer_site","all"}:
                        old=db.conn.execute("SELECT GROUP_CONCAT(p.name) x FROM local_video_performers l JOIN performers p ON p.id=l.performer_id WHERE l.video_id=?",(vid,)).fetchone()['x'] or ''
                        db.conn.execute("DELETE FROM video_performers WHERE video_id=?",(vid,)); db.conn.execute("DELETE FROM local_video_performers WHERE video_id=?",(vid,))
                        new='Unknown'
                        if action!='unknown' and performer_target:
                            # Bulk mapping must use the exact performer selected by the
                            # picker when an ID is available. Name-only lookup is kept
                            # for create-new/fallback cases, matching the single-video
                            # mapping endpoint's behavior.
                            performer_id = body.get("performer_id")
                            provider = str(body.get("provider") or "").strip().casefold()
                            provider_id = str(body.get("provider_id") or "").strip()
                            performer = None
                            if performer_id is not None and str(performer_id).isdigit():
                                performer = db.conn.execute("SELECT * FROM performers WHERE id=?", (int(performer_id),)).fetchone()
                            if performer is None and provider and provider_id:
                                rows = db.conn.execute("SELECT * FROM performers WHERE provider_ids LIKE ?", (f'%"{provider}": "{provider_id}"%',)).fetchall()
                                performer = rows[0] if rows else None
                            if performer is None:
                                performer = db.conn.execute(
                                    "SELECT * FROM performers WHERE lower(name)=lower(?) OR normalized_name=? LIMIT 1",
                                    (target, db._norm_search(target))).fetchone()
                            if performer is not None and provider and provider_id:
                                from ..engines.entity_identity import upsert_performer
                                pid = upsert_performer(db, performer_target, provider=provider, provider_id=provider_id,
                                                       image=str(body.get("image") or "").strip() or None,
                                                       profile=str(body.get("profile") or "").strip() or None)
                                performer = db.conn.execute("SELECT * FROM performers WHERE id=?", (pid,)).fetchone()
                            elif performer is None and provider and provider_id:
                                from ..engines.entity_identity import upsert_performer
                                pid = upsert_performer(db, performer_target, provider=provider, provider_id=provider_id,
                                                       image=str(body.get("image") or "").strip() or None,
                                                       profile=str(body.get("profile") or "").strip() or None)
                                performer = db.conn.execute("SELECT * FROM performers WHERE id=?", (pid,)).fetchone()
                            if performer is None:
                                raise HTTPException(404, "Performer not found. Select an API performer match or an existing local performer.")
                            db.conn.execute("INSERT OR IGNORE INTO local_video_performers(video_id,performer_id,created_at) VALUES(?,?,?)",(vid,int(performer['id']),now))
                            new=str(performer['name'] or performer_target)
                        db.conn.execute("INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",(vid,'performer',old,new,'manual',now))
                    if field in {"site","performer_site","all"}:
                        old=db.conn.execute("SELECT GROUP_CONCAT(s.name) x FROM video_studios v JOIN studios s ON s.id=v.studio_id WHERE v.video_id=?",(vid,)).fetchone()['x'] or ''
                        db.conn.execute("DELETE FROM video_studios WHERE video_id=?",(vid,)); new='Unknown'
                        if action!='unknown' and studio_target:
                            site_id = body.get("site_id")
                            studio = None
                            if site_id is not None and str(site_id).isdigit():
                                studio = db.conn.execute("SELECT * FROM studios WHERE id=?", (int(site_id),)).fetchone()
                            if studio is None:
                                from ..engines.entity_identity import upsert_studio
                                sid=upsert_studio(db,studio_target)
                                studio = db.conn.execute("SELECT * FROM studios WHERE id=?", (sid,)).fetchone()
                            if studio is None:
                                raise HTTPException(500, "Could not resolve selected site")
                            db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id) VALUES(?,?)",(vid,int(studio['id'])))
                            new=str(studio['name'] or studio_target)
                            # Keep the denormalized media site fields in sync with
                            # the canonical video_studios relationship. Several
                            # catalogue/detail paths read media.site_name directly,
                            # while filters and entity pages read video_studios.
                            # Without this, a successful manual mapping could appear
                            # to disappear until a later provider metadata refresh.
                            db.conn.execute("UPDATE media SET site_name=?, updated_at=? WHERE id=?", (new, now, vid))
                        else:
                            # Unknown/unmapped is an explicit operator action. Clear
                            # both representations so stale site metadata cannot win.
                            db.conn.execute("UPDATE media SET site_name=NULL, site_url=NULL, updated_at=? WHERE id=?", (now, vid))
                        db.conn.execute("INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",(vid,'site',old,new,'manual',now))
                    fields=[field] if field not in {'performer_site','all'} else (['performer','site'] if field=='performer_site' else ['title','performer','site'])
                    for f in fields:
                        if lock: db.conn.execute("INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,reason,updated_at) VALUES(?,?,?,?,?)",(vid,f,1,'manual mapping',now))
                        else: db.conn.execute("DELETE FROM metadata_mapping_locks WHERE video_id=? AND field=?",(vid,f))
                db.conn.commit()
            except BaseException:
                db.conn.rollback()
                raise
        queued = _queue_catalogue_caption_sync(ids) if field in {"title", "performer", "site", "performer_site", "all"} else 0
        return {"ok":True,"changed":len(ids),"locked":lock,
                "caption_sync":{"status":"queued" if queued else "not_needed", "count":queued}}

    def _resolve_mapping_video_ids(body):
        ids=[int(x) for x in (body.get('media_ids') or []) if str(x).isdigit()]
        with db.lock:
            for item in body.get('videos') or []:
                try:
                    row=db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?",(str(item.get('channel_id') or ''),int(item.get('message_id') or 0))).fetchone()
                    if row: ids.append(int(row['id']))
                except Exception:
                    pass
        return list(dict.fromkeys(ids))

    @router.post("/api/mapping/recheck")
    async def mapping_recheck(request: Request):
        """POST /api/mapping/recheck: Mapping recheck."""
        require_auth(request); body=await request.json(); ids=_resolve_mapping_video_ids(body)
        if not ids: raise HTTPException(400,'No local videos selected')
        jid=db.create_job('METADATA_SYNC','','',False,detail=json.dumps({'media_ids':ids,'force':True,'limit':0,'unbounded':True})); jobs.submit(jid)
        return {'ok':True,'id':jid,'count':len(ids)}

    @router.post("/api/mapping/unlock")
    async def mapping_unlock(request: Request):
        """POST /api/mapping/unlock: Mapping unlock."""
        require_auth(request); body=await request.json(); ids=_resolve_mapping_video_ids(body); field=str(body.get('field') or 'all')
        if not ids: raise HTTPException(400,'No local videos selected')
        with db.lock:
            for vid in ids:
                if field=='all': db.conn.execute("DELETE FROM metadata_mapping_locks WHERE video_id=?",(vid,))
                else: db.conn.execute("DELETE FROM metadata_mapping_locks WHERE video_id=? AND field=?",(vid,field))
            db.conn.commit()
        return {"ok":True,"count":len(ids)}

    @router.get("/api/mapping/history/{media_id}")
    async def mapping_history(media_id:int, request:Request):
        """GET /api/mapping/history/{media_id}: Mapping history."""
        require_auth(request)
        def _read_history():
            with db.reading() as rdb:
                return [dict(r) for r in rdb.execute("SELECT * FROM metadata_mapping_history WHERE video_id=? ORDER BY id DESC LIMIT 100", (media_id,)).fetchall()]
        return await asyncio.to_thread(_read_history)


    @router.get("/api/performers/provider-search")
    async def search_performer_provider_candidates(request: Request):
        """Search configured metadata providers for performer mapping candidates.

        This endpoint never creates a local performer. A local entity is only
        imported when the user explicitly selects a provider candidate.
        """
        require_auth(request)
        q = str(request.query_params.get("q") or "").strip()
        try:
            limit = max(1, min(10, int(request.query_params.get("limit", "8"))))
        except ValueError:
            limit = 8
        if len(q) < 2:
            return []
        from ..metadata.providers import search_performer_providers
        try:
            return await search_performer_providers(db, q, limit=limit)
        except Exception as exc:
            db.log(f"Performer provider search failed for {q!r}: {type(exc).__name__}: {exc}")
            return []
