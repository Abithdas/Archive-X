"""Synchronous performer catalogue queries shared by web route groups."""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import json
import re


def list_performers_sync(db, query_params):
    """Return the performer catalogue, optionally filtered by the supplied query."""
    q = str(query_params.get("q") or "").strip()

    # Backfill performer links from provider_payload for databases created by
    # earlier builds. This is local-only; the provider data is already cached.
    with db.lock:
        performer_count = db.conn.execute("SELECT COUNT(*) n FROM performers").fetchone()["n"]
    if performer_count == 0:
        from ..metadata.providers import _performer_details

        with db.lock:
            legacy = db.conn.execute(
                "SELECT id,provider_payload FROM media "
                "WHERE provider_payload IS NOT NULL AND provider_payload!='' LIMIT 5000"
            ).fetchall()
            for media_row in legacy:
                try:
                    payload = json.loads(media_row["provider_payload"] or "{}")
                except Exception:
                    _swallowed_note("performer_catalogue.list_performers_sync")
                    continue
                for person in _performer_details(payload):
                    name = str(person.get("name") or "").strip()
                    if not name:
                        continue
                    from ..engines.entity_identity import upsert_performer

                    performer_id = upsert_performer(
                        db,
                        name,
                        provider=str(payload.get("source") or "provider"),
                        provider_id=person.get("id"),
                        image=person.get("image") or person.get("thumbnail"),
                        profile=person.get("profile"),
                    )
                    if performer_id:
                        db.conn.execute(
                            "INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES(?,?)",
                            (media_row["id"], performer_id),
                        )
            db.conn.commit()

    brief = str(query_params.get("brief") or "") == "1"
    try:
        limit = max(1, min(5000, int(query_params.get("limit", "5000"))))
    except ValueError:
        limit = 5000

    count_sql = (
        "SELECT p.*, COUNT(DISTINCT combined.video_id) video_count, "
        "EXISTS(SELECT 1 FROM performer_image_cache pic WHERE pic.performer_id=p.id "
        "AND pic.data IS NOT NULL AND length(pic.data)>0) AS has_cached_image FROM performers p "
        "LEFT JOIN (SELECT performer_id, video_id FROM video_performers "
        "WHERE video_id IN (SELECT id FROM archive_x_library_media) "
        "UNION SELECT performer_id, video_id FROM local_video_performers "
        "WHERE video_id IN (SELECT id FROM archive_x_library_media)) combined "
        "ON combined.performer_id=p.id "
    )
    with db.reading() as read_db:
        rows = None
        if q:
            words = [word for word in re.split(r"[^\w]+", q) if word][:8]
            if words and getattr(db, "fts_available", False):
                match = " ".join(f'"{word}"*' for word in words)
                try:
                    rows = read_db.execute(
                        count_sql
                        + "WHERE p.id IN (SELECT rowid FROM performer_fts WHERE performer_fts MATCH ?) "
                        "GROUP BY p.id HAVING video_count > 0 OR p.name='Unknown Performer' "
                        "ORDER BY (p.name='Unknown Performer') DESC, "
                        "(lower(p.name) LIKE lower(?)) DESC, video_count DESC, p.name LIMIT ?",
                        (match, q + "%", limit),
                    ).fetchall()
                except Exception:
                    rows = None
            if not rows:
                from ..engines.entity_identity import canonical_key

                like = f"%{q}%"
                rows = read_db.execute(
                    count_sql
                    + "WHERE p.name LIKE ? OR COALESCE(p.aliases,'') LIKE ? "
                    "OR COALESCE(p.normalized_name,'') LIKE ? OR COALESCE(p.canonical_key,'') LIKE ? "
                    "GROUP BY p.id HAVING video_count > 0 OR p.name='Unknown Performer' "
                    "ORDER BY (p.name='Unknown Performer') DESC, "
                    "(lower(p.name) LIKE lower(?)) DESC, video_count DESC, p.name LIMIT ?",
                    (
                        like,
                        like,
                        f"%{db._norm_search(q)}%",
                        f"%{canonical_key(q)}%",
                        q + "%",
                        limit,
                    ),
                ).fetchall()
        else:
            rows = read_db.execute(
                count_sql
                + "GROUP BY p.id HAVING video_count > 0 OR p.name='Unknown Performer' "
                "ORDER BY (p.name='Unknown Performer') DESC, video_count DESC,p.name LIMIT ?",
                (limit,),
            ).fetchall()

    # A read connection normally sees the committed performer catalogue. If a
    # just-completed backfill is still outside its snapshot, retry only the
    # empty, unfiltered case through the primary connection.
    if not rows and not q:
        with db.lock:
            primary_count = int(db.conn.execute("SELECT COUNT(*) FROM performers").fetchone()[0] or 0)
            if primary_count:
                rows = db.conn.execute(
                    count_sql
                    + "GROUP BY p.id HAVING video_count > 0 OR p.name='Unknown Performer' "
                    "ORDER BY (p.name='Unknown Performer') DESC, video_count DESC,p.name LIMIT ?",
                    (limit,),
                ).fetchall()

    output = []
    for row in rows:
        performer = dict(row)
        gender = str(performer.get("gender") or "").strip().lower()
        # "transgender_female", "Trans woman" and so on all count as trans; anything else unknown stays blank
        performer["gender"] = "trans" if "trans" in gender else gender if gender in {"male", "female"} else ""
        if brief:
            performer = {
                key: performer.get(key)
                for key in (
                    "id",
                    "name",
                    "video_count",
                    "image",
                    "gender",
                    "favorite",
                    "has_cached_image",
                )
            }
        output.append(performer)
    return output


def lookup_entities_sync(db, kind: str, names) -> list[dict]:
    """Performers or studios whose name is one of `names` (case-insensitive), with the few fields the pages need.

    Detail pages know the names on their own videos; looking those up is a few rows instead of the whole catalogue."""
    wanted = list(dict.fromkeys(str(n or "").strip().lower() for n in names if str(n or "").strip()))[:300]
    if not wanted or kind not in {"performers", "studios"}:
        return []
    marks = ",".join("?" for _ in wanted)
    with db.reading() as rdb:
        if kind == "performers":
            rows = rdb.execute(
                "SELECT p.id,p.name,p.image,p.gender,COUNT(DISTINCT combined.video_id) video_count,"
                "EXISTS(SELECT 1 FROM performer_image_cache pic WHERE pic.performer_id=p.id AND pic.data IS NOT NULL AND length(pic.data)>0) AS has_cached_image "
                "FROM performers p LEFT JOIN (SELECT performer_id, video_id FROM video_performers WHERE video_id IN (SELECT id FROM archive_x_library_media) "
                "UNION SELECT performer_id, video_id FROM local_video_performers WHERE video_id IN (SELECT id FROM archive_x_library_media)) combined "
                f"ON combined.performer_id=p.id WHERE lower(p.name) IN ({marks}) GROUP BY p.id", wanted).fetchall()
        else:
            rows = rdb.execute(
                "SELECT s.id,s.name,s.image,COUNT(DISTINCT CASE WHEN vs.video_id IN (SELECT id FROM archive_x_library_media) THEN vs.video_id END) video_count,"
                "(SELECT MAX(sic.updated_at) FROM studio_image_cache sic WHERE sic.studio_id=s.id) AS image_cache_updated_at,"
                "EXISTS(SELECT 1 FROM studio_image_cache sic WHERE sic.studio_id=s.id AND sic.data IS NOT NULL AND length(sic.data)>0 AND (COALESCE(s.image,'')='' OR COALESCE(sic.url,'')=COALESCE(s.image,''))) AS has_cached_image "
                f"FROM studios s LEFT JOIN video_studios vs ON vs.studio_id=s.id WHERE lower(s.name) IN ({marks}) GROUP BY s.id", wanted).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if kind == "performers":
            g = str(d.get("gender") or "").strip().lower()
            d["gender"] = "trans" if "trans" in g else g if g in {"male", "female"} else ""
        out.append(d)
    return out
