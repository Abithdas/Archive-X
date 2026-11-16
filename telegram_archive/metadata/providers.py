"""External metadata lookups with deterministic provider precedence:

  1. Exact fingerprint lookup first (OSHASH): StashDB is primary.
  2. ThePornDB is queried sequentially only after a primary miss or to enrich a
     unique primary result with important missing fields.
  3. Text clues are searched against StashDB first; ThePornDB is fallback/enrichment.
  4. Cached results follow the configured freshness policy; provider failures remain retryable.
  5. Uncertain scenes or performer identities are suggestions, not automatic entity creation.

Order and endpoints follow ThePornDB's MIT-licensed `namer` tool and the
public StashDB (stash-box) GraphQL API. Both require a free API key/token
from the operator; without keys, lookups are simply skipped.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Cache, throttle and HTTP
#   - Result normalization
#   - Scene lookups
#   - Scene markers
#   - Provider result merging
#   - Entity metadata
#   - Entity search and images
#   - Scene identification
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
from .. import config
import json
import logging
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from hashlib import sha256
from typing import Optional

from ..core.http_client import request_json
from .api_broker import provider_for_url, request_json as broker_request_json

STASHDB_URL = "https://stashdb.org/graphql"
TPDB_URL = "https://api.theporndb.net"
MIN_INTERVAL = 1.0  # seconds between calls to the same provider
_CACHE_MAX_AGE_SECONDS = max(0.0, float(getattr(config, "METADATA_CACHE_TTL_SECONDS", 90 * 86400)))

_STASHDB_PHASH_QUERY = """
query($fps: [[FingerprintQueryInput!]!]!) {
  findScenesBySceneFingerprints(fingerprints: $fps) {
    id title date release_date production_date code duration
    fingerprints { algorithm hash duration }
    studio { id name aliases parent { id name aliases } urls { url } images { url width height } }
    performers { performer { id name gender urls { url } images { url width height } } }
    tags { name }
    images { url width height }
  }
}"""

# Title/term and date queries are separate from the exact-fingerprint query.
# StashDB's StashBox schema exposes searchScenes for full-text candidates and
# queryScenes for exact date searches; both are read-only and cached.
_STASHDB_SCENE_SEARCH_QUERY = """
query($term: String!, $limit: Int!) {
  searchScenes(term: $term, limit: $limit) {
    count
    scenes {
      id title date release_date production_date code
      studio { id name aliases parent { id name aliases } urls { url } images { url width height } }
      performers { performer { id name gender urls { url } images { url width height } } }
      tags { name }
      images { url width height }
    }
  }
}"""

_STASHDB_SCENE_DATE_QUERY = """
query($date: String!, $limit: Int!) {
  queryScenes(input: {
    date: { value: $date, modifier: EQUALS }
    per_page: $limit
    page: 1
    sort: DATE
    direction: DESC
  }) {
    count
    scenes {
      id title date release_date production_date code
      studio { id name aliases parent { id name aliases } urls { url } images { url width height } }
      performers { performer { id name gender urls { url } images { url width height } } }
      tags { name }
      images { url width height }
    }
  }
}"""


_STASHDB_SCENE_YEAR_QUERY = """
query($start: String!, $end: String!, $limit: Int!) {
  queryScenes(input: {
    date: { value: $start, value2: $end, modifier: BETWEEN }
    per_page: $limit
    page: 1
    sort: DATE
    direction: DESC
  }) {
    count
    scenes {
      id title date release_date production_date code
      studio { id name aliases parent { id name aliases } urls { url } images { url width height } }
      performers { performer { id name gender urls { url } images { url width height } } }
      tags { name }
      images { url width height }
    }
  }
}"""

_next_allowed: dict[str, float] = {}
_rate_lock = threading.Lock()
_CACHE_READY_ATTR = "_metadata_cache_ready"




# ==============================================================================
# SECTION: Cache, throttle and HTTP
# Provider cache, stats, throttling and HTTP.
# ==============================================================================

def _ensure_cache(db):
    # Database.init_schema() creates this table once. Keep a defensive one-time
    # fallback for databases opened by older builds, but never execute a
    # CREATE/COMMIT pair for every metadata lookup.
    if getattr(db, _CACHE_READY_ATTR, False):
        return
    with db.lock:
        db.conn.execute("""CREATE TABLE IF NOT EXISTS metadata_cache (
            provider TEXT NOT NULL, query_key TEXT NOT NULL, response TEXT,
            fetched_at REAL NOT NULL, PRIMARY KEY (provider, query_key))""")
        db.conn.execute("CREATE INDEX IF NOT EXISTS idx_metadata_cache_fetched ON metadata_cache(provider, fetched_at)")
        db.conn.commit()
        try:
            setattr(db, _CACHE_READY_ATTR, True)
        except Exception:
            _swallowed_note("providers._ensure_cache")
            pass


def _provider_stat(db, provider: str, field: str, amount: int = 1):
    now = time.time()
    try:
        with db.lock:
            db.conn.execute(
                "INSERT INTO api_cache_stats(provider,hits,misses,throttled,errors,last_request_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(provider) DO UPDATE SET " + field + "=" + field + "+?, last_request_at=?, updated_at=?",
                (provider, 0, 0, 0, 0, None, config.now_iso(), amount, config.now_iso(), config.now_iso())
            )
            db.conn.commit()
    except Exception:
        _swallowed_note("providers._provider_stat")
        pass


def _record_provider_call(db, provider: str, *, status: str = "OK", error: str = "", latency_ms: float | None = None, entity_type: str = "scene"):
    now = config.now_iso()
    try:
        with db.lock:
            db.conn.execute(
                "INSERT INTO provider_sync_state(provider,entity_type,last_sync_at,status,error,request_count,last_latency_ms,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,entity_type) DO UPDATE SET last_sync_at=excluded.last_sync_at,status=excluded.status,error=excluded.error,request_count=provider_sync_state.request_count+1,last_latency_ms=excluded.last_latency_ms,updated_at=excluded.updated_at",
                (provider, entity_type, now, status, error[:500], 1, latency_ms, now),
            )
            db.conn.commit()
    except Exception:
        _swallowed_note("providers._record_provider_call")
        pass


def _cache_get(db, provider, key, max_age=None):
    if max_age is None:
        max_age = _CACHE_MAX_AGE_SECONDS
    with db.lock:
        row = db.conn.execute("SELECT response, fetched_at FROM metadata_cache WHERE provider=? AND query_key=?",
                              (provider, key)).fetchone()
    if row and time.time() - row["fetched_at"] < max_age:
        _provider_stat(db, provider, 'hits', 1)
        return json.loads(row["response"]) if row["response"] else None, True
    _provider_stat(db, provider, 'misses', 1)
    return None, False


def _cache_put(db, provider, key, value):
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO metadata_cache(provider,query_key,response,fetched_at) VALUES(?,?,?,?)",
                        (provider, key, json.dumps(value) if value is not None else None, time.time()))
        db.conn.commit()


async def _throttle(provider: str):
    """Compatibility yield; provider pacing is enforced by api_broker."""
    await asyncio.sleep(0)


def _http(url: str, *, data: Optional[bytes] = None, headers: dict, timeout=20) -> dict:
    """Persistent pooled provider request routed through the metadata broker."""
    provider = provider_for_url(url)
    method = "POST" if data else "GET"
    def _request(target_url, *, data, headers, timeout):
        try:
            return request_json(target_url, method=method, data=data, headers=headers, timeout=timeout)
        except Exception as e:
            host = urllib.parse.urlsplit(target_url).netloc
            message = str(e)
            response = getattr(e, "response", None)
            provider_body = ""
            if response is not None:
                try:
                    provider_body = str(getattr(response, "text", "") or "")
                    if not provider_body and getattr(response, "content", None):
                        provider_body = bytes(response.content).decode("utf-8", "replace")
                except Exception:
                    provider_body = ""
            provider_body = provider_body.strip()[:1200]
            if not provider_body and "response_body=" in message:
                provider_body = message.split("response_body=", 1)[1].strip()[:1200]
            # A provider 404 is a valid metadata outcome (no match), not an
            # application failure. Returning an empty payload lets the existing
            # SQLite metadata cache persist the negative result and prevents
            # retry/error storms. Other HTTP statuses retain their existing
            # error semantics; 422 is deliberately non-retryable in the broker.
            if "HTTP 404" in message:
                return {}
            if "HTTP 429" in message:
                raise RuntimeError(f"{host} HTTP 429") from e
            if "HTTP 422" in message:
                logging.getLogger(__name__).warning(
                    "Metadata provider rejected request host=%s status=422 body=%s",
                    host, provider_body or "<no response body exposed by HTTP client>"
                )
                raise RuntimeError(f"{host} HTTP 422 invalid provider request; provider_body={provider_body or '<unavailable>'}") from e
            if "HTTP " in message:
                raise RuntimeError(f"{host} {message}") from e
            raise RuntimeError(f"{host} unreachable: {message}") from e
    return broker_request_json(provider, url, data=data, headers=headers, timeout=timeout, request_fn=_request)




# ==============================================================================
# SECTION: Result normalization
# StashDB and TPDB normalization and applying matches.
# ==============================================================================

def _first_provider_url(value: object) -> str | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
            if isinstance(item, str) and item.strip():
                return item.strip()
    if isinstance(value, dict):
        return str(value.get("url") or "") or None
    if value:
        return str(value).strip() or None
    return None


def _dicts(value) -> list:
    """The dict items of a provider list; anything else (None, text, numbers) is ignored rather than crashing the reader."""
    return [x for x in value if isinstance(x, dict)] if isinstance(value, (list, tuple)) else []


def _as_dict(value, name_key: str = "name") -> dict:
    """A provider sub-record that may arrive as a dict, a bare name, or nothing."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        return {name_key: value.strip()}
    return {}


def _norm_stashdb(scene: dict) -> dict:
    """Convert a StashDB scene into the shared scene shape."""
    scene = scene if isinstance(scene, dict) else {}
    performers = []
    for p in _dicts(scene.get("performers")):
        person = _as_dict(p.get("performer"))
        name = person.get("name")
        if name:
            performers.append({
                "name": name,
                "id": person.get("id"),
                "image": person.get("image_path") or person.get("image") or _entity_image_from_images(person.get("images")),
                "profile": person.get("url") or person.get("profile") or _first_provider_url(person.get("urls")),
                "gender": person.get("gender") or person.get("sex"),
                "provider": "stashdb",
            })
    studio = _as_dict(scene.get("studio"))
    parent_studio = _as_dict(studio.get("parent"))
    studio_aliases = [str(value).strip() for value in (studio.get("aliases") if isinstance(studio.get("aliases"), list) else []) if str(value or "").strip()]
    parent_aliases = [str(value).strip() for value in (parent_studio.get("aliases") if isinstance(parent_studio.get("aliases"), list) else []) if str(value or "").strip()]
    studio_url = studio.get("url") or studio.get("profile") or _first_provider_url(studio.get("urls"))
    image_rows = scene.get("images") if isinstance(scene.get("images"), list) else []
    scene_image = scene.get("image") or scene.get("thumbnail") or _entity_image_from_images(image_rows)
    return {
        "source": "stashdb", "id": scene.get("id"), "title": scene.get("title"),
        "date": scene.get("release_date") or scene.get("date") or scene.get("production_date"),
        "production_date": scene.get("production_date"), "code": scene.get("code"),
        "duration": scene.get("duration") or scene.get("duration_seconds"),
        "fingerprints": [dict(fp) for fp in (scene.get("fingerprints") or []) if isinstance(fp, dict)],
        "updated_at": scene.get("updated_at") or scene.get("updatedAt"),
        "studio": studio.get("name"), "studio_id": studio.get("id"),
        "studio_aliases": studio_aliases, "parent_studio": parent_studio.get("name"),
        "parent_network": parent_studio.get("name"), "parent_studio_aliases": parent_aliases,
        "studio_url": studio_url,
        "studio_image": studio.get("image_path") or studio.get("image") or _entity_image_from_images(studio.get("images")),
        "site": studio.get("name"), "site_url": studio_url,
        "scene_type": scene.get("type") or "Scene",
        "performers": [p.get("name") for p in performers if p.get("name")],
        "performer_details": performers,
        "tags": [t.get("name") for t in _dicts(scene.get("tags")) if t.get("name")],
        "image": scene_image, "thumbnail": scene.get("thumbnail") or scene_image,
        "profile": scene.get("url") or _first_provider_url(scene.get("urls")),
    }


async def stashdb_scenes_by_year(db, api_key: str, year: str | int, limit: int = 20) -> list[dict]:
    """Find StashDB candidates in a scene year when no exact date is known."""
    clean_year = str(year or "").strip()
    if not re.fullmatch(r"(?:19|20)\d{2}", clean_year):
        return []
    limit = max(1, min(50, int(limit or 20)))
    key = f"scene-year:{clean_year}:{limit}"
    cached, hit = _cache_get(db, "stashdb", key, max_age=30 * 86400)
    if hit:
        return cached or []
    await _throttle("stashdb")
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(
            _stashdb_graphql, db, api_key, _STASHDB_SCENE_YEAR_QUERY,
            {"start": f"{clean_year}-01-01", "end": f"{clean_year}-12-31", "limit": limit},
        )
        if data.get("errors"):
            raise RuntimeError("StashDB year search: " + str(data["errors"][0].get("message", "error"))[:200])
        root = (data.get("data") or {}).get("queryScenes") or {}
        raw_scenes = root.get("scenes") if isinstance(root, dict) else []
        scenes = [_norm_stashdb(row) for row in (raw_scenes or []) if isinstance(row, dict)]
        _record_provider_call(db, "stashdb", latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_year_search")
    except Exception as exc:
        _provider_stat(db, "stashdb", "errors", 1)
        _record_provider_call(db, "stashdb", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_year_search")
        raise
    for scene in scenes:
        scene.setdefault("_search_terms", []).append(f"year:{clean_year}")
    _cache_put(db, "stashdb", key, scenes)
    return scenes


def _norm_tpdb(scene: dict) -> dict:
    """Convert a ThePornDB scene into the shared scene shape."""
    scene = scene if isinstance(scene, dict) else {}
    site = _as_dict(scene.get("site"))
    parent_site = _as_dict(site.get("parent") or site.get("network") or site.get("parent_site"))
    performers = []
    for p in _dicts(scene.get("performers")):
        name = p.get("name") or p.get("full_name")
        if name:
            extras = p.get("extras") if isinstance(p.get("extras"), dict) else (p.get("extra") if isinstance(p.get("extra"), dict) else {})
            performers.append({
                "name": name,
                "id": p.get("id") or p.get("_id"),
                "image": p.get("image") or p.get("thumbnail"),
                "thumbnail": p.get("thumbnail"),
                "profile": p.get("url") or p.get("profile"),
                # ThePornDB keeps a performer's gender under "extras"; it was dropped, so those performers had none until a later refresh.
                "gender": p.get("gender") or p.get("sex") or extras.get("gender"),
                "provider": "theporndb",
            })
    images = scene.get("images") if isinstance(scene.get("images"), dict) else {}
    site_images = site.get("images") if isinstance(site.get("images"), dict) else {}
    return {
        "source": "theporndb", "id": scene.get("id") or scene.get("_id"),
        "title": scene.get("title"), "date": scene.get("date"),
        "duration": scene.get("duration") or scene.get("duration_seconds") or scene.get("length"),
        "fingerprints": [dict(fp) for fp in _dicts(scene.get("fingerprints") or scene.get("hashes"))],
        "updated_at": scene.get("last_updated") or scene.get("updated_at"),
        "studio": site.get("name"), "studio_id": site.get("id") or site.get("_id"),
        "studio_aliases": list(site.get("aliases") if isinstance(site.get("aliases"), (list, tuple)) else []),
        "parent_studio": parent_site.get("name"), "parent_network": parent_site.get("name"),
        "parent_studio_aliases": list(parent_site.get("aliases") if isinstance(parent_site.get("aliases"), (list, tuple)) else []),
        "studio_url": site.get("url"), "site": site.get("name"),
        "site_url": site.get("url"), "scene_type": scene.get("type") or "Scene",
        # Site/studio logo, when ThePornDB supplies one. Best-effort/optional —
        # never fabricated: left None (never displayed) when absent.
        "studio_image": (site.get("logo") or site.get("image") or site.get("poster")
                          or site_images.get("logo") or site_images.get("poster")),
        "performers": [p.get("name") for p in performers if p.get("name")],
        "performer_details": performers,
        "tags": [t.get("name") for t in _dicts(scene.get("tags")) if t.get("name")],
        "image": scene.get("image") or images.get("large") or images.get("medium") or images.get("small"),
        "thumbnail": scene.get("thumbnail") or images.get("medium") or images.get("small"),
        "profile": scene.get("url"),
        "description": scene.get("description"),
        "links": scene.get("links"),
    }


def _performer_details(best: dict):
    raw = best.get("performer_details") or best.get("performers") or []
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": item})
        elif isinstance(item, dict) and item.get("name"):
            out.append(item)
    return out


def _add_provider_ids_to_entity(db, table: str, entity_id: int, provider_ids: object) -> None:
    """Attach missing provider IDs only when they do not conflict with another entity.

    Cross-provider scene corroboration may expose two source IDs for one person
    or studio. Existing conflicting IDs in the same provider namespace are a hard
    stop: names alone must not merge potentially distinct entities.
    """
    if table not in {"performers", "studios"} or not isinstance(provider_ids, dict):
        return
    with db.lock:
        target = db.conn.execute(f"SELECT provider_ids FROM {table} WHERE id=?", (int(entity_id),)).fetchone()
        if not target:
            return
        try:
            mapping = json.loads(target["provider_ids"] or "{}")
        except Exception:
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}
        changed = False
        rows = db.conn.execute(f"SELECT id,provider_ids FROM {table} WHERE provider_ids IS NOT NULL AND provider_ids<>''").fetchall()
        for source, raw_id in provider_ids.items():
            source = str(source or "").strip()
            ident = str(raw_id or "").strip()
            if not source or not ident:
                continue
            current = mapping.get(source)
            current_values = current if isinstance(current, (list, tuple, set)) else [current]
            current_values = [str(value or "").strip() for value in current_values if str(value or "").strip()]
            if current_values and ident not in current_values:
                continue
            owned_elsewhere = False
            for row in rows:
                if int(row["id"]) == int(entity_id):
                    continue
                try:
                    other = json.loads(row["provider_ids"] or "{}")
                except Exception:
                    other = {}
                value = other.get(source) if isinstance(other, dict) else None
                values = value if isinstance(value, (list, tuple, set)) else [value]
                if ident in {str(item or "").strip() for item in values if str(item or "").strip()}:
                    owned_elsewhere = True
                    break
            if owned_elsewhere:
                continue
            if not current_values:
                mapping[source] = ident
                changed = True
        if changed:
            db.conn.execute(f"UPDATE {table} SET provider_ids=? WHERE id=?",
                            (json.dumps(mapping, ensure_ascii=False, sort_keys=True), int(entity_id)))


def apply_scene_match(db, channel_id: int, message_id: int, best: dict, oshash: str | None = None, *, preserve_existing_mappings: bool = False) -> bool:
    """Persist provider metadata without duplicating existing local mappings.

    Interactive suggestion approval sets ``preserve_existing_mappings=True``:
    known performer/studio mappings stay authoritative, and only missing/Unknown
    mappings are filled from the accepted candidate. Automatic high-confidence
    matches retain their normal replacement behavior.
    """
    now = config.now_iso()
    performers = _performer_details(best)
    provider = str(best.get("source") or "").strip() or "unknown"
    payload = json.dumps(best, ensure_ascii=False)
    with db.lock:
        row = db.conn.execute(
            "SELECT id, filename FROM media WHERE source_channel=? AND source_message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
        if not row:
            return False
        media_id = int(row["id"])
        from ..engines.entity_identity import performer_identity_conflicts, mapped_video_entity_context
        mapping_context = mapped_video_entity_context(db, media_id) if preserve_existing_mappings else {"performer_names": [], "studio_names": [], "has_known_studio": False}
        identity_conflicts = performer_identity_conflicts(
            db, best, default_provider=provider,
            ignore_names=mapping_context.get("performer_names") if preserve_existing_mappings else None,
        )
        if identity_conflicts:
            names = ", ".join(dict.fromkeys(str(item.get("name") or "") for item in identity_conflicts if item.get("name")))
            db.log(
                f"Scene match withheld: ambiguous performer identity for {names or 'one or more cast members'}; kept as a review suggestion",
                "WARN",
            )
            return False
        locked = {r["field"] for r in db.conn.execute("SELECT field FROM metadata_mapping_locks WHERE video_id=? AND locked=1", (media_id,)).fetchall()}
        preserve_performers = bool(preserve_existing_mappings and mapping_context.get("performer_names"))
        preserve_site = bool(preserve_existing_mappings and mapping_context.get("has_known_studio"))
        title_value = None if "title" in locked else best.get("title")
        site_name_value = None if "site" in locked or preserve_site else (best.get("site") or best.get("studio") or "")
        site_url_value = None if "site" in locked or preserve_site else (best.get("site_url") or best.get("studio_url") or "")
        db.conn.execute(
            """UPDATE media SET title=COALESCE(?,title), release_date=COALESCE(?,release_date),
               metadata_source=?, provider_scene_id=?, provider_updated_at=?, metadata_synced_at=?,
               scene_type=?, site_name=COALESCE(?,site_name), site_url=COALESCE(?,site_url), provider_url=?, provider_payload=?,
               identity_key=COALESCE(?,identity_key), updated_at=? WHERE id=?""",
            (title_value, best.get("date"), provider, str(best.get("id") or ""),
             best.get("updated_at") or "", now, best.get("scene_type") or "Scene",
             site_name_value, site_url_value, best.get("profile") or "", payload, oshash, now, media_id),
        )
        from ..engines.entity_identity import upsert_studio, upsert_performer
        # This function is called only for an authoritative HIGH-confidence
        # match. Replace previous provider-managed performer/studio links so a
        # bad title-only match from an older build cannot survive a correction.
        # Explicit operator mappings live in local_video_performers and are kept.
        # Delete FIRST: older code inserted the new studio relationship and
        # then deleted the entire video_studios set, losing the correct site.
        locked = {r["field"] for r in db.conn.execute("SELECT field FROM metadata_mapping_locks WHERE video_id=? AND locked=1", (media_id,)).fetchall()}
        studio = str(best.get("studio") or best.get("site") or "").strip()
        # Replace the earlier links only when this match actually names a cast / a studio. A provider record that simply lacks its cast
        # or studio used to wipe what the video already had (from its filename or an operator), leaving "Unknown Performer".
        if "performer" not in locked and not preserve_performers and performers:
            db.conn.execute("DELETE FROM video_performers WHERE video_id=?", (media_id,))
        if "site" not in locked and not preserve_site and studio:
            db.conn.execute("DELETE FROM video_studios WHERE video_id=?", (media_id,))
        if studio and "site" not in locked and not preserve_site:
            field_provenance = best.get("field_provenance") if isinstance(best.get("field_provenance"), dict) else {}
            studio_provider = str(field_provenance.get("studio_id") or provider).strip() or provider
            studio_id = str(best.get("studio_id") or best.get("site_id") or "").strip()
            provider_studio_ids = best.get("studio_provider_ids") if isinstance(best.get("studio_provider_ids"), dict) else {}
            if studio_id and provider_studio_ids:
                studio_provider = next((str(src) for src, sid_value in provider_studio_ids.items()
                                        if str(sid_value or "").strip() == studio_id), studio_provider)
            from ..engines.entity_identity import resolve_existing_by_provider_ids
            existing_studio = resolve_existing_by_provider_ids(
                db, "studios", provider_studio_ids, provider=studio_provider, provider_id=studio_id or None)
            if existing_studio:
                studio = str(existing_studio.get("name") or studio)
                studio_provider = str(existing_studio.get("_matched_provider") or studio_provider)
                studio_id = str(existing_studio.get("_matched_provider_id") or studio_id)
            sid = upsert_studio(db, studio, provider=studio_provider, provider_id=studio_id or None,
                                image=best.get("studio_image"))
            if sid:
                _add_provider_ids_to_entity(db, "studios", sid, provider_studio_ids)
                db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id) VALUES(?,?)", (media_id, sid))
        from ..engines.entity_identity import resolve_existing_by_provider_ids
        from ..engines.entity_identity import canonical_key
        already_mapped_keys = {canonical_key(value) for value in mapping_context.get("performer_names", [])} if preserve_performers else set()
        for person in performers:
            if "performer" in locked:
                break
            name = str(person.get("name") or "").strip()
            if not name or (preserve_performers and canonical_key(name) in already_mapped_keys):
                continue
            person_provider = str(person.get("provider") or "").strip() or provider
            person_provider_id = person.get("id")
            existing_person = resolve_existing_by_provider_ids(
                db, "performers", person.get("provider_ids"), provider=person_provider, provider_id=person_provider_id)
            if existing_person:
                person_provider = str(existing_person.get("_matched_provider") or person_provider)
                person_provider_id = str(existing_person.get("_matched_provider_id") or person_provider_id or "")
                name = str(existing_person.get("name") or name)
            pid = upsert_performer(
                db, name, provider=person_provider, provider_id=person_provider_id,
                image=person.get("image") or person.get("thumbnail"),
                profile=person.get("profile"),
                bio=person.get("bio") or person.get("description"),
                gender=person.get("gender") or person.get("sex"),
            )
            if pid:
                _add_provider_ids_to_entity(db, "performers", pid, person.get("provider_ids"))
                db.conn.execute(
                    "INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES(?,?)",
                    (media_id, pid),
                )
        for tag in best.get("tags") or []:
            tname = str(tag or "").strip()
            if not tname:
                continue
            db.conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tname,))
            tid = db.conn.execute("SELECT id FROM tags WHERE name=?", (tname,)).fetchone()["id"]
            db.conn.execute("INSERT OR IGNORE INTO video_tags(video_id,tag_id) VALUES(?,?)", (media_id, tid))

        # V5.5: retain field-level provenance so the UI can show which provider
        # supplied each effective value without replacing the local catalogue.
        provenance = {
            "title": best.get("title"),
            "release_date": best.get("date"),
            "site": best.get("site") or best.get("studio"),
            "scene_type": best.get("scene_type") or "Scene",
            "description": best.get("description"),
        }
        for field, value in provenance.items():
            if value in (None, ""):
                continue
            value_hash = sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            db.conn.execute(
                "INSERT OR REPLACE INTO provider_field_sources(media_id,field,provider,provider_record_id,value_hash,confidence,is_effective,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (media_id, field, provider, str(best.get("id") or ""), value_hash, 1.0, 1, now)
            )
        for person in performers:
            pname = str(person.get("name") or "").strip()
            if not pname:
                continue
            value_hash = sha256(pname.encode("utf-8")).hexdigest()
            db.conn.execute(
                "INSERT OR REPLACE INTO provider_field_sources(media_id,field,provider,provider_record_id,value_hash,confidence,is_effective,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (media_id, "performer:" + pname.casefold(), provider, str(person.get("id") or ""), value_hash, 1.0, 1, now)
            )
        db.conn.commit()
    return True




# ==============================================================================
# SECTION: Scene lookups
# StashDB and TPDB hash and search lookups.
# ==============================================================================

def normalize_stashdb_oshash(value: object) -> str:
    """Return the canonical StashDB OSHASH (16 lowercase hexadecimal digits)."""
    clean = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{16}", clean):
        raise ValueError("StashDB OSHASH must be exactly 16 lowercase hexadecimal characters")
    return clean


async def stashdb_by_oshash(db, api_key: str, oshash: str) -> list[dict]:
    oshash = normalize_stashdb_oshash(oshash)
    _ensure_cache(db)
    cached, hit = _cache_get(db, "stashdb", f"oshash:{oshash}")
    if hit:
        return cached or []
    await _throttle("stashdb")
    # StashDB removed findScenesByFullFingerprints (every lookup answered HTTP 422); the same query as the PHASH lookup now serves OSHASH too.
    body = json.dumps({"query": _STASHDB_PHASH_QUERY,
                       "variables": {"fps": [[{"hash": oshash, "algorithm": "OSHASH"}]]}}).encode()
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(_http, STASHDB_URL, data=body,
                                       headers={"Content-Type": "application/json", "ApiKey": api_key})
        if data.get("errors"):
            # Preserve the provider's GraphQL error body for diagnosis (commonly
            # the exact fingerprint input/algorithm issue behind HTTP 422s).
            provider_errors = json.dumps(data.get("errors"), ensure_ascii=False)[:1200]
            logging.getLogger(__name__).warning("StashDB GraphQL error body: %s", provider_errors)
            raise RuntimeError("StashDB GraphQL errors: " + provider_errors)
        _record_provider_call(db, "stashdb", latency_ms=(time.perf_counter()-started)*1000.0)
    except Exception as exc:
        _provider_stat(db, "stashdb", "errors", 1)
        _record_provider_call(db, "stashdb", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0)
        raise
    root = (data.get("data") or {}).get("findScenesBySceneFingerprints") or []
    raw_scenes = []
    for group in root if isinstance(root, list) else []:
        if isinstance(group, list):
            raw_scenes.extend(item for item in group if isinstance(item, dict))
        elif isinstance(group, dict):
            raw_scenes.append(group)
    scenes = [_norm_stashdb(row) for row in raw_scenes]
    _cache_put(db, "stashdb", f"oshash:{oshash}", scenes)
    return scenes


async def stashdb_by_phash(db, api_key: str, phash: str) -> list[dict]:
    """Query StashBox's PHASH distance index using its Stash-compatible hex hash."""
    clean = re.sub(r"[^0-9a-f]", "", str(phash or "").casefold())
    if not clean or len(clean) > 16:
        return []
    _ensure_cache(db)
    key = f"phash:{clean}"
    cached, hit = _cache_get(db, "stashdb", key, max_age=30 * 86400)
    if hit:
        return cached or []
    body = json.dumps({"query": _STASHDB_PHASH_QUERY, "variables": {
        "fps": [[{"hash": clean, "algorithm": "PHASH"}]]
    }}).encode()
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(_http, STASHDB_URL, data=body,
                                       headers={"Content-Type": "application/json", "ApiKey": api_key})
        if data.get("errors"):
            raise RuntimeError("StashDB PHASH: " + str(data["errors"][0].get("message", "error"))[:200])
        root = (data.get("data") or {}).get("findScenesBySceneFingerprints") or []
        raw_scenes = []
        if isinstance(root, list):
            for group in root:
                if isinstance(group, list): raw_scenes.extend(item for item in group if isinstance(item, dict))
                elif isinstance(group, dict): raw_scenes.append(group)
        scenes = [_norm_stashdb(row) for row in raw_scenes]
        for scene in scenes:
            scene["_archive_phash_distance"] = min(
                (int(str(fp.get("hash") or ""), 16) ^ int(clean, 16)).bit_count()
                for fp in scene.get("fingerprints") or []
                if str(fp.get("algorithm") or "").upper() == "PHASH" and re.fullmatch(r"[0-9a-fA-F]{1,16}", str(fp.get("hash") or ""))
            ) if any(str(fp.get("algorithm") or "").upper() == "PHASH" and re.fullmatch(r"[0-9a-fA-F]{1,16}", str(fp.get("hash") or "")) for fp in scene.get("fingerprints") or []) else None
        _record_provider_call(db, "stashdb", latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_phash")
    except Exception as exc:
        _provider_stat(db, "stashdb", "errors", 1)
        _record_provider_call(db, "stashdb", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_phash")
        raise
    _cache_put(db, "stashdb", key, scenes)
    return scenes


async def stashdb_search_scenes(db, api_key: str, term: str, limit: int = 10) -> list[dict]:
    """Search StashDB scene records by a filename-derived clue.

    Search results are candidates only; Archive X's scorer decides whether a
    candidate can be applied automatically or must remain a suggestion.
    """
    term = " ".join(str(term or "").split())[:180]
    if len(term) < 2:
        return []
    limit = max(1, min(20, int(limit or 10)))
    key = f"scene-search:{term.casefold()}:{limit}"
    cached, hit = _cache_get(db, "stashdb", key, max_age=30 * 86400)
    if hit:
        return cached or []
    await _throttle("stashdb")
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(
            _stashdb_graphql, db, api_key, _STASHDB_SCENE_SEARCH_QUERY,
            {"term": term, "limit": limit},
        )
        if data.get("errors"):
            raise RuntimeError("StashDB scene search: " + str(data["errors"][0].get("message", "error"))[:200])
        root = (data.get("data") or {}).get("searchScenes") or {}
        raw_scenes = root.get("scenes") if isinstance(root, dict) else root
        scenes = [_norm_stashdb(row) for row in (raw_scenes or []) if isinstance(row, dict)]
        _record_provider_call(db, "stashdb", latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_search")
    except Exception as exc:
        _provider_stat(db, "stashdb", "errors", 1)
        _record_provider_call(db, "stashdb", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_search")
        raise
    for scene in scenes:
        scene.setdefault("_search_terms", []).append(term)
    _cache_put(db, "stashdb", key, scenes)
    return scenes


async def stashdb_scenes_by_date(db, api_key: str, date: str, limit: int = 20) -> list[dict]:
    """Find possible StashDB scenes from an exact date clue."""
    date = str(date or "").strip()[:10]
    if not re.fullmatch(r"(?:19|20)\d{2}-\d{2}-\d{2}", date):
        return []
    limit = max(1, min(50, int(limit or 20)))
    key = f"scene-date:{date}:{limit}"
    cached, hit = _cache_get(db, "stashdb", key, max_age=30 * 86400)
    if hit:
        return cached or []
    await _throttle("stashdb")
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(
            _stashdb_graphql, db, api_key, _STASHDB_SCENE_DATE_QUERY,
            {"date": date, "limit": limit},
        )
        if data.get("errors"):
            raise RuntimeError("StashDB date search: " + str(data["errors"][0].get("message", "error"))[:200])
        root = (data.get("data") or {}).get("queryScenes") or {}
        raw_scenes = root.get("scenes") if isinstance(root, dict) else []
        scenes = [_norm_stashdb(row) for row in (raw_scenes or []) if isinstance(row, dict)]
        _record_provider_call(db, "stashdb", latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_date_search")
    except Exception as exc:
        _provider_stat(db, "stashdb", "errors", 1)
        _record_provider_call(db, "stashdb", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_date_search")
        raise
    for scene in scenes:
        scene.setdefault("_search_terms", []).append(f"date:{date}")
    _cache_put(db, "stashdb", key, scenes)
    return scenes


async def tpdb_by_hash(db, token: str, oshash: str) -> list[dict]:
    _ensure_cache(db)
    cached, hit = _cache_get(db, "tpdb", f"oshash:{oshash}")
    if hit:
        return cached or []
    await _throttle("tpdb")
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(_http, f"{TPDB_URL}/scenes/hash/{oshash}",
                                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        raw = data.get("data")
        scenes = [_norm_tpdb(s) for s in (raw if isinstance(raw, list) else [raw] if raw else [])]
        _record_provider_call(db, "tpdb", latency_ms=(time.perf_counter()-started)*1000.0)
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            _provider_stat(db, "tpdb", "errors", 1); _record_provider_call(db, "tpdb", status="ERROR", error=str(e), latency_ms=(time.perf_counter()-started)*1000.0)
            raise
        scenes = []
        _record_provider_call(db, "tpdb", status="MISS", latency_ms=(time.perf_counter()-started)*1000.0)
    _cache_put(db, "tpdb", f"oshash:{oshash}", scenes)
    return scenes


async def tpdb_by_phash(db, token: str, phash: str) -> list[dict]:
    """Query ThePornDB's public scene PHASH endpoint; the provider applies its configured distance."""
    clean = re.sub(r"[^0-9a-f]", "", str(phash or "").casefold())
    if not clean or len(clean) > 16:
        return []
    _ensure_cache(db)
    key = f"phash:{clean}"
    cached, hit = _cache_get(db, "tpdb", key, max_age=30 * 86400)
    if hit:
        return cached or []
    query = urllib.parse.urlencode({"hash": clean, "hashType": "PHASH"})
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(_http, f"{TPDB_URL}/scenes?{query}",
                                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        raw = data.get("data")
        rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        scenes = [_norm_tpdb(row) for row in rows if isinstance(row, dict)]
        for scene in scenes:
            for fp in scene.get("fingerprints") or []:
                if str(fp.get("algorithm") or fp.get("type") or "").upper() == "PHASH":
                    try: scene["_archive_phash_distance"] = (int(str(fp.get("hash") or fp.get("value") or ""), 16) ^ int(clean, 16)).bit_count()
                    except Exception:
                        _swallowed_note("providers.tpdb_by_phash")
                        pass
        _record_provider_call(db, "tpdb", latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_phash")
    except Exception as exc:
        _provider_stat(db, "tpdb", "errors", 1)
        _record_provider_call(db, "tpdb", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0, entity_type="scene_phash")
        raise
    _cache_put(db, "tpdb", key, scenes)
    return scenes


async def tpdb_parse(db, token: str, filename: str, *, year: str | int | None = None) -> list[dict]:
    """Search ThePornDB's scene parser with an optional year hint.

    The provider accepts ``year`` alongside ``parse``. This narrows title-only
    and date-assisted queries without treating year alone as an identity match.
    """
    _ensure_cache(db)
    clean_year = str(year or "").strip()
    if not re.fullmatch(r"(?:19|20)\d{2}", clean_year):
        clean_year = ""
    key = f"parse:{filename.lower()}:year:{clean_year or '*'}"
    cached, hit = _cache_get(db, "tpdb", key)
    if hit:
        return cached or []
    await _throttle("tpdb")
    params = {"parse": filename, "limit": 5}
    if clean_year:
        params["year"] = clean_year
    q = urllib.parse.urlencode(params)
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(_http, f"{TPDB_URL}/scenes?{q}",
                                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        scenes = [_norm_tpdb(s) for s in data.get("data") or []]
        _record_provider_call(db, "tpdb", latency_ms=(time.perf_counter()-started)*1000.0)
    except Exception as exc:
        _provider_stat(db, "tpdb", "errors", 1)
        _record_provider_call(db, "tpdb", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0)
        raise
    _cache_put(db, "tpdb", key, scenes)
    return scenes




# ==============================================================================
# SECTION: Scene markers
# Fetches and syncs scene markers.
# ==============================================================================

def _tpdb_marker_rows(payload: object) -> list[dict]:
    """Normalize ThePornDB scene marker payload into Archive X markers.

    TPDB's public scene endpoint exposes ``data.markers`` with ``title`` and
    ``start_time``. Keep this adapter deliberately tolerant because older TPDB
    responses have varied slightly in their envelope shape.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    raw = data.get("markers") or []
    if not isinstance(raw, list):
        return []
    rows = []
    for idx, marker in enumerate(raw):
        if not isinstance(marker, dict):
            continue
        try:
            seconds = float(marker.get("start_time") if marker.get("start_time") is not None else marker.get("seconds"))
        except (TypeError, ValueError):
            continue
        if seconds < 0:
            continue
        title = str(marker.get("title") or marker.get("name") or "Marker").strip()[:300]
        provider_id = str(marker.get("id") or marker.get("_id") or idx).strip()
        rows.append({
            "provider_marker_id": provider_id,
            "position": seconds,
            "title": title or "Marker",
            "note": str(marker.get("description") or marker.get("note") or "").strip()[:1000],
            "tag": str(marker.get("tag") or "").strip()[:100],
        })
    rows.sort(key=lambda x: (float(x["position"]), str(x["provider_marker_id"])))
    return rows


async def tpdb_markers_for_scene(db, token: str, scene_id: str) -> list[dict]:
    """Fetch and cache TPDB markers for one TPDB scene identifier."""
    ident = str(scene_id or "").strip()
    if not ident:
        return []
    _ensure_cache(db)
    key = f"markers:scene:{ident}"
    cached, hit = _cache_get(db, "tpdb", key)
    if hit:
        return cached or []
    await _throttle("tpdb")
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(_tpdb_get, db, token, f"scenes/{urllib.parse.quote(ident, safe='')}")
        rows = _tpdb_marker_rows(data)
        _record_provider_call(db, "tpdb", entity_type="scene_markers", latency_ms=(time.perf_counter()-started)*1000.0)
    except Exception as exc:
        _provider_stat(db, "tpdb", "errors", 1)
        _record_provider_call(db, "tpdb", entity_type="scene_markers", status="ERROR", error=str(exc), latency_ms=(time.perf_counter()-started)*1000.0)
        raise
    _cache_put(db, "tpdb", key, rows)
    return rows


async def resolve_tpdb_scene_id_for_media(db, media_row: dict) -> str | None:
    """Resolve a TPDB scene ID from stored metadata without an uncached lookup.

    If the current match is TPDB, its provider record ID is authoritative. For a
    StashDB-primary match, the existing TPDB OSHASH lookup is reused from the
    metadata cache, so marker loading does not create a second fingerprint call.
    """
    payload = {}
    try:
        payload = json.loads(media_row.get("provider_payload") or "{}")
    except Exception:
        payload = {}
    source = str(payload.get("source") or media_row.get("metadata_source") or "").casefold()
    ident = str(payload.get("id") or "").strip()
    if source in {"theporndb", "tpdb"} and ident:
        return ident
    ident = str(media_row.get("provider_scene_id") or "").strip()
    if source in {"theporndb", "tpdb"} and ident:
        return ident
    token = db.setting("tpdb_api_token")
    oshash = str(media_row.get("oshash") or "").strip()
    if not token or not oshash:
        return None
    matches = await tpdb_by_hash(db, token, oshash)
    if not matches:
        return None
    first = matches[0]
    return str(first.get("id") or "").strip() or None


async def sync_media_markers(db, media_id: int, *, force: bool = False) -> dict:
    """Import provider markers into the local timeline-marker store once.

    Local markers always win. Provider data is enrichment only; an explicit
    refresh never deletes locally created markers.
    """
    with db.lock:
        row = db.conn.execute("SELECT * FROM media WHERE id=?", (int(media_id),)).fetchone()
        local_count = db.conn.execute("SELECT COUNT(*) AS c FROM scene_markers WHERE media_id=?", (int(media_id),)).fetchone()["c"]
    if not row:
        return {"ok": False, "status": "NOT_FOUND", "media_id": int(media_id), "markers": []}
    media = dict(row)
    with db.lock:
        sync_row = db.conn.execute(
            "SELECT status FROM provider_sync_state WHERE provider=? AND entity_type=?",
            ("theporndb", f"scene_markers:{int(media_id)}"),
        ).fetchone()
    sync_status = str(sync_row["status"] or "").upper() if sync_row else ""
    imported_before = sync_status in {"SYNCED", "NO_MARKERS"}
    if not force and sync_status == "USER_MODIFIED":
        with db.lock:
            rows = [dict(r) for r in db.conn.execute("SELECT * FROM scene_markers WHERE media_id=? ORDER BY position,id", (int(media_id),)).fetchall()]
        return {"ok": True, "status": "USER_MODIFIED", "media_id": int(media_id), "markers": rows, "source": "local"}
    if local_count and not force:
        with db.lock:
            rows = [dict(r) for r in db.conn.execute("SELECT * FROM scene_markers WHERE media_id=? ORDER BY position,id", (int(media_id),)).fetchall()]
        return {"ok": True, "status": "LOCAL", "media_id": int(media_id), "markers": rows, "source": "local"}
    provider_name, scene_id, provider_rows, tried = await _collect_provider_markers(db, media, force=force)
    if provider_name == "":
        return {"ok": True, "status": "NO_PROVIDER_TOKEN", "media_id": int(media_id), "markers": []}
    if imported_before and not force and not (sync_status == "SYNCED" and int(local_count or 0) == 0):
        with db.lock:
            rows = [dict(r) for r in db.conn.execute("SELECT * FROM scene_markers WHERE media_id=? ORDER BY position,id", (int(media_id),)).fetchall()]
        return {"ok": True, "status": "SYNCED_LOCAL_EMPTY", "media_id": int(media_id), "markers": rows, "source": provider_name, "provider_scene_id": scene_id}
    if not provider_rows:
        now = config.now_iso()
        with db.lock:
            db.conn.execute(
                "INSERT INTO provider_sync_state(provider,entity_type,last_sync_at,status,error,request_count,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,entity_type) DO UPDATE SET last_sync_at=excluded.last_sync_at,status=excluded.status,error=excluded.error,request_count=provider_sync_state.request_count+1,updated_at=excluded.updated_at",
                ("theporndb", f"scene_markers:{int(media_id)}", now, "NO_MARKERS" if tried else "NO_TPDB_SCENE", "", 1, now),
            )
            db.conn.commit()
        return {"ok": True, "status": "NO_MARKERS" if tried else "NO_TPDB_SCENE", "media_id": int(media_id), "markers": [], "providers_tried": tried}
    now = config.now_iso()
    with db.lock:
        # Provider refresh is additive and idempotent. It never overwrites or
        # deletes user-created local markers.
        for m in provider_rows:
            db.conn.execute(
                "INSERT OR IGNORE INTO scene_markers(media_id,position,title,note,tag,source,provider,provider_marker_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (int(media_id), float(m["position"]), m["title"], m["note"], m["tag"], "provider", provider_name, str(m["provider_marker_id"]), now, now),
            )
        db.conn.execute(
            "INSERT INTO provider_sync_state(provider,entity_type,last_sync_at,status,error,request_count,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(provider,entity_type) DO UPDATE SET last_sync_at=excluded.last_sync_at,status=excluded.status,error=excluded.error,request_count=provider_sync_state.request_count+1,updated_at=excluded.updated_at",
            ("theporndb", f"scene_markers:{int(media_id)}", now, "SYNCED", "", 1, now),
        )
        db.conn.commit()
        rows = [dict(r) for r in db.conn.execute("SELECT * FROM scene_markers WHERE media_id=? ORDER BY position,id", (int(media_id),)).fetchall()]
    return {"ok": True, "status": "SYNCED", "media_id": int(media_id), "markers": rows, "source": provider_name, "provider_scene_id": scene_id, "provider_count": len(provider_rows), "providers_tried": tried}


_STASHDB_MARKERS_QUERY = """
query($id: ID!) { findScene(id: $id) { id markers { id title seconds } } }"""


def _payload_marker_rows(media_row: dict) -> list[dict]:
    """Markers already carried by the stored provider payload (any provider)."""
    try:
        payload = json.loads(media_row.get("provider_payload") or "{}")
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    rows = _tpdb_marker_rows({"data": {"markers": payload.get("markers") or payload.get("chapters") or []}})
    return rows


async def _stashdb_markers_for_scene(db, api_key: str, scene_id: str) -> list[dict]:
    """Best-effort StashDB marker lookup. Never raises or records a provider error:
    not every StashDB deployment exposes markers, and an unsupported field must not
    break marker collection or pollute Provider status."""
    ident = str(scene_id or "").strip()
    if not ident or not api_key:
        return []
    _ensure_cache(db)
    key = f"markers:scene:{ident}"
    cached, hit = _cache_get(db, "stashdb", key, max_age=30 * 86400)
    if hit:
        return cached or []
    rows: list[dict] = []
    try:
        await _throttle("stashdb")
        data = await asyncio.to_thread(_stashdb_graphql, db, api_key, _STASHDB_MARKERS_QUERY, {"id": ident})
        scene = ((data or {}).get("data") or {}).get("findScene") if not (data or {}).get("errors") else None
        raw = (scene or {}).get("markers") if isinstance(scene, dict) else None
        rows = _tpdb_marker_rows({"data": {"markers": raw or []}})
    except Exception:
        return []
    _cache_put(db, "stashdb", key, rows)
    return rows


async def _collect_provider_markers(db, media: dict, *, force: bool = False) -> tuple[str, str, list[dict], list[str]]:
    """Try each provider in turn until one returns markers.

    Order: ThePornDB, markers already stored with the match, then StashDB.
    Returns (provider, scene_id, rows, providers_tried); provider is "" when no
    provider is configured at all.
    """
    tried: list[str] = []
    tpdb_token = db.setting("tpdb_api_token")
    stash_key = db.setting("stashdb_api_key")
    if not tpdb_token and not stash_key:
        return "", "", [], tried
    scene_id = ""
    if tpdb_token:
        try:
            scene_id = await resolve_tpdb_scene_id_for_media(db, media) or ""
        except Exception:
            scene_id = ""
        if scene_id:
            tried.append("theporndb")
            try:
                rows = await tpdb_markers_for_scene(db, tpdb_token, scene_id)
            except Exception:
                rows = []
            if rows:
                return "theporndb", scene_id, rows, tried
    embedded = _payload_marker_rows(media)
    if embedded:
        source = str(media.get("metadata_source") or "provider").casefold() or "provider"
        tried.append(source)
        return source, str(media.get("provider_scene_id") or scene_id), embedded, tried
    if stash_key:
        stash_id = ""
        try:
            payload = json.loads(media.get("provider_payload") or "{}")
        except Exception:
            payload = {}
        source = str((payload or {}).get("source") or media.get("metadata_source") or "").casefold()
        if source == "stashdb":
            stash_id = str((payload or {}).get("id") or media.get("provider_scene_id") or "").strip()
        if stash_id:
            tried.append("stashdb")
            rows = await _stashdb_markers_for_scene(db, stash_key, stash_id)
            if rows:
                return "stashdb", stash_id, rows, tried
    return (tried[-1] if tried else "theporndb"), scene_id, [], tried


async def collect_markers_for_message(db, channel_id, message_id) -> None:
    """Metadata-scan hook: fetch scene markers for a freshly matched video.

    Failures are swallowed; marker collection must never fail a metadata scan.
    """
    try:
        with db.lock:
            row = db.conn.execute(
                "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                (str(channel_id), int(message_id)),
            ).fetchone()
        if row:
            await sync_media_markers(db, int(row["id"]), force=False)
    except Exception as exc:
        try:
            db.log(f"Scene marker collection skipped for {channel_id}/{message_id}: {type(exc).__name__}", "INFO", event="metadata.scene_markers")
        except Exception:
            _swallowed_note("providers.collect_markers_for_message")
            pass


# Metadata field-enrichment policy is deterministic: StashDB remains primary
# and ThePornDB fills missing fields. Scene identification is deliberately
# separate: both catalogs are searched independently and their candidate
# evidence is scored before an automatic mapping is allowed.
PRIMARY_PROVIDER = "stashdb"
FALLBACK_PROVIDER = "theporndb"




# ==============================================================================
# SECTION: Provider result merging
# Merges primary and fallback results.
# ==============================================================================

def _norm_entity_name(value: object) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _merge_dict_prefer_primary(primary: dict | None, fallback: dict | None) -> dict:
    out = dict(primary or {})
    for key, value in (fallback or {}).items():
        if value in (None, "", [], {}):
            continue
        if out.get(key) in (None, "", [], {}):
            out[key] = value
    return out


def _merge_named_people(primary: list, fallback: list) -> list[dict]:
    """Merge cross-provider cast data without losing provider-scoped identities.

    Equivalent scene records can represent the same performer under StashDB and
    ThePornDB IDs. Keep both IDs on the merged person; conflicting IDs within one
    provider namespace remain separate people for explicit review.
    """
    out: list[dict] = []
    index: dict[str, int] = {}

    def normalized_people_key(item):
        return _norm_entity_name(item.get("name"))

    def add(item: object, primary_row: bool) -> None:
        """Merge one person into the result by normalized name."""
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or not item.get("name"):
            return
        clean = dict(item)
        key = normalized_people_key(clean)
        if not key:
            return
        provider = str(clean.get("provider") or "").strip().casefold()
        ident = str(clean.get("id") or "").strip()
        # Same display name but different IDs in the same source are not enough
        # evidence to merge distinct provider identities.
        if key in index:
            pos = index[key]
            current = out[pos]
            current_provider = str(current.get("provider") or "").strip().casefold()
            current_id = str(current.get("id") or "").strip()
            if provider and current_provider == provider and ident and current_id and ident != current_id:
                unique_key = f"{key}|{provider}:{ident}"
                if unique_key not in index:
                    index[unique_key] = len(out)
                    out.append(clean)
                return
            merged = _merge_dict_prefer_primary(current, clean) if primary_row else _merge_dict_prefer_primary(clean, current)
            provider_ids: dict[str, str] = {}
            for person in (current, clean):
                raw_ids = person.get("provider_ids")
                if isinstance(raw_ids, dict):
                    for src, pid_value in raw_ids.items():
                        pid = str(pid_value or "").strip()
                        if src and pid and src not in provider_ids:
                            provider_ids[str(src)] = pid
                src = str(person.get("provider") or "").strip()
                pid = str(person.get("id") or "").strip()
                if src and pid:
                    # Never overwrite a distinct ID already represented for the
                    # same provider: such rows were kept separate above.
                    provider_ids.setdefault(src, pid)
            if provider_ids:
                merged["provider_ids"] = provider_ids
            out[pos] = merged
        else:
            index[key] = len(out)
            initial = dict(clean)
            provider_ids = initial.get("provider_ids") if isinstance(initial.get("provider_ids"), dict) else {}
            provider = str(initial.get("provider") or "").strip()
            ident = str(initial.get("id") or "").strip()
            if provider and ident:
                provider_ids = {**provider_ids, provider: ident}
            if provider_ids:
                initial["provider_ids"] = provider_ids
            out.append(initial)

    for item in primary or []:
        add(item, True)
    for item in fallback or []:
        add(item, False)
    return out

def _merge_string_list(primary: list, fallback: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in list(primary or []) + list(fallback or []):
        clean = str(value or "").strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def _needs_fallback(match: dict) -> bool:
    # StashDB's current fingerprint query can omit artwork/description and
    # occasionally other descriptive fields, so use TPDB only when there is
    # something genuinely missing to fill.
    for key in ("title", "date", "studio", "studio_id", "studio_url", "scene_type",
                "image", "thumbnail", "profile", "description"):
        if match.get(key) in (None, "", [], {}):
            return True
    return not (match.get("performer_details") or match.get("performers"))


def merge_provider_matches(primary: dict, fallback: dict | None) -> dict:
    """Merge provider records using primary-provider precedence field-by-field."""
    merged = dict(primary)
    if not fallback:
        return merged
    for key in ("title", "date", "duration", "fingerprints", "updated_at", "studio", "studio_id", "studio_url", "site",
                "site_url", "scene_type", "image", "thumbnail", "profile", "description"):
        if merged.get(key) in (None, "", [], {}) and fallback.get(key) not in (None, "", [], {}):
            merged[key] = fallback[key]
    merged["performers"] = _merge_string_list(primary.get("performers") or [], fallback.get("performers") or [])
    merged["performer_details"] = _merge_named_people(primary.get("performer_details") or [], fallback.get("performer_details") or [])
    studio_provider_ids = {}
    for item in (primary, fallback):
        src = str(item.get("source") or "").strip()
        sid = str(item.get("studio_id") or item.get("site_id") or "").strip()
        if src and sid:
            studio_provider_ids.setdefault(src, sid)
        raw_ids = item.get("studio_provider_ids")
        if isinstance(raw_ids, dict):
            for provider_name, provider_id in raw_ids.items():
                if provider_name and str(provider_id or "").strip():
                    studio_provider_ids.setdefault(str(provider_name), str(provider_id).strip())
    if studio_provider_ids:
        merged["studio_provider_ids"] = studio_provider_ids
    merged["tags"] = _merge_string_list(primary.get("tags") or [], fallback.get("tags") or [])
    p_links = primary.get("links") if isinstance(primary.get("links"), dict) else {}
    f_links = fallback.get("links") if isinstance(fallback.get("links"), dict) else {}
    if p_links or f_links:
        merged["links"] = _merge_dict_prefer_primary(p_links, f_links)
    provenance = dict(primary.get("field_provenance") or {})
    for key in ("title", "date", "updated_at", "studio", "studio_id", "studio_url", "site", "site_url",
                "scene_type", "image", "thumbnail", "profile", "description", "performers", "performer_details", "tags", "links"):
        if primary.get(key) in (None, "", [], {}) and fallback.get(key) not in (None, "", [], {}):
            provenance[key] = FALLBACK_PROVIDER
        elif merged.get(key) not in (None, "", [], {}):
            provenance.setdefault(key, PRIMARY_PROVIDER)
    merged["field_provenance"] = provenance
    merged["fallback_provider"] = str(fallback.get("source") or FALLBACK_PROVIDER)
    merged["source"] = str(primary.get("source") or PRIMARY_PROVIDER)
    return merged


# Entity Data queries intentionally request the complete public StashBox
# performer/studio profile surface used by Archive X Details. Keeping the
# selection explicit avoids an introspection round-trip while retaining the
# raw provider payload for any provider fields not yet promoted to the UI.
_STASHDB_PERFORMER_FIELDS = """
      id name disambiguation aliases gender urls { url }
      birth_date death_date age ethnicity country eye_color hair_color
      height measurements { cup_size band_size waist hip }
      cup_size band_size waist_size hip_size breast_type
      career_start_year career_end_year tattoos { location description }
      piercings { location description }
      images { url width height }
      deleted scene_count merged_ids merged_into_id is_favorite created updated
"""

_STASHDB_PERFORMER_SEARCH_QUERY = f"""
query($term: String!, $limit: Int!) {{
  searchPerformers(term: $term, limit: $limit) {{
    performers {{
{_STASHDB_PERFORMER_FIELDS}    }}
  }}
}}
"""

_STASHDB_PERFORMER_BY_ID_QUERY = f"""
query($id: ID!) {{
  findPerformer(id: $id) {{
{_STASHDB_PERFORMER_FIELDS}  }}
}}
"""

_STASHDB_STUDIO_FIELDS = """
    id name aliases urls { url }
    parent { id name aliases urls { url } images { url width height } }
    images { url width height }
    deleted is_favorite created updated
"""

_STASHDB_STUDIO_BY_NAME_QUERY = f"""
query($name: String!) {{
  findStudio(name: $name) {{
{_STASHDB_STUDIO_FIELDS}  }}
}}
"""

_STASHDB_STUDIO_BY_ID_QUERY = f"""
query($id: ID!) {{
  findStudio(id: $id) {{
{_STASHDB_STUDIO_FIELDS}  }}
}}
"""




# ==============================================================================
# SECTION: Entity metadata
# Fetches and stores performer and studio metadata.
# ==============================================================================

def _entity_image_from_images(images: object) -> str:
    if not isinstance(images, list):
        return ""
    candidates: list[tuple[int, int, str]] = []
    for index, item in enumerate(images):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("image") or "").strip()
        if not url:
            continue
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except Exception:
            width = height = 0
        candidates.append((width * height, -index, url))
    if not candidates:
        return ""
    return max(candidates)[2]


def _provider_entity_rows(payload: object, key_names: tuple[str, ...]) -> list[dict]:
    """Normalize provider list/single-entity response envelopes.

    ThePornDB has used both ``data: {performers: [...]}`` and
    ``data: {performer: {...}}`` style envelopes. The previous implementation
    returned the outer ``data`` object as an entity row, which made valid
    responses look like records without an ``id``/``name``. Walk the known
    envelope keys before treating a dictionary as a single entity.
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []

    def rows_from(value: object) -> list[dict] | None:
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if not isinstance(value, dict):
            return None
        for nested in ("performers", "studios", "sites", "results", "items", "data"):
            child = value.get(nested)
            if isinstance(child, list):
                return [x for x in child if isinstance(x, dict)]
            if isinstance(child, dict):
                # Single-resource envelopes such as data.performer.
                if any(k in child for k in ("id", "_id", "uuid", "name", "full_name")):
                    return [child]
                nested_rows = rows_from(child)
                if nested_rows is not None:
                    return nested_rows
        if any(k in value for k in ("id", "_id", "uuid", "name", "full_name")):
            return [value]
        return None

    # Prefer explicit keys first, then inspect the common top-level data/result
    # envelopes. This keeps provider-specific response shapes compatible.
    for key in key_names:
        value = payload.get(key)
        if value is None:
            continue
        rows = rows_from(value)
        if rows is not None:
            return rows
    for envelope in ("data", "result", "results"):
        value = payload.get(envelope)
        rows = rows_from(value)
        if rows is not None:
            return rows
    return rows_from(payload) or []


def _tpdb_entity_image(row: dict, *, performer: bool) -> tuple[str, str]:
    image = row.get("image") or row.get("thumbnail") or row.get("poster")
    if isinstance(image, dict):
        image = image.get("url") or image.get("src")
    image = str(image or "").strip()
    if not image:
        posters = row.get("posters") or row.get("images") or []
        if isinstance(posters, list):
            for item in posters:
                if isinstance(item, dict):
                    image = str(item.get("url") or item.get("image") or item.get("poster") or "").strip()
                    if image:
                        break
    pid = str(row.get("id") or row.get("_id") or row.get("uuid") or "").strip()
    return image, pid


def _stashdb_rows(data: dict, container: str) -> list[dict]:
    root = (data.get("data") or {}) if isinstance(data, dict) else {}
    value = root.get(container) if isinstance(root, dict) else None
    if isinstance(value, dict):
        rows = value.get("performers") or value.get("studios") or []
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
    return []


def _best_named_entity(rows: list[dict], local_name: str, *, performer: bool) -> tuple[str, str, str]:
    target = _norm_entity_name(local_name)
    matches: list[tuple[int, int, str, str, str]] = []
    for idx, row in enumerate(rows):
        name = str(row.get("name") or row.get("full_name") or "").strip()
        aliases = row.get("aliases") or []
        names = [name] + [str(x).strip() for x in aliases if str(x).strip()]
        exact = any(_norm_entity_name(x) == target for x in names) if target else False
        if not exact:
            continue
        image = _http_entity_image(row, performer=performer)
        pid = str(row.get("id") or row.get("_id") or "").strip()
        if image:
            # Exact name beats alias; earlier result wins ties for stability.
            exact_name = _norm_entity_name(name) == target
            matches.append((1 if exact_name else 0, -idx, image, "", pid))
    if not matches:
        return "", "", ""
    _exact, _idx, image, _source, pid = max(matches, key=lambda x: (x[0], x[1]))
    return image, "", pid


def _http_entity_image(row: dict, *, performer: bool) -> str:
    direct = row.get("image") or row.get("image_path") or row.get("image_url") or row.get("photo")
    if direct:
        return _http_image_url(direct)
    return _entity_image_from_images(row.get("images"))


def _http_image_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            return url
    except Exception:
        _swallowed_note("providers._http_image_url")
        pass
    return ""


def _entity_payload_for_storage(row: dict, *, provider: str, entity_type: str) -> dict:
    """Create a stable Details payload while retaining provider-specific data."""
    if not isinstance(row, dict):
        return {}
    aliases = row.get("aliases") or row.get("alias_list") or row.get("aliases_list") or []
    if not isinstance(aliases, list):
        aliases = [aliases] if aliases else []
    urls = row.get("urls") or row.get("links") or []
    if isinstance(urls, str): urls = [urls]
    if not isinstance(urls, list): urls = []
    images = _image_candidates_from_images(row.get("images"), limit=48)
    normalized = {
        "id": row.get("id") or row.get("_id") or row.get("uuid"),
        "name": row.get("name") or row.get("full_name"),
        "aliases": [str(x).strip() for x in aliases if str(x).strip()],
        "urls": [str(x.get("url") if isinstance(x, dict) else x).strip() for x in urls if str(x.get("url") if isinstance(x, dict) else x).strip()],
        "images": images,
        "profile": row.get("profile") or row.get("url") or row.get("website"),
        "entity_type": entity_type,
        "provider": provider,
        "details": {},
    }
    for key, value in row.items():
        if key in {"images", "aliases", "alias_list", "aliases_list", "urls", "links"}:
            continue
        if value not in (None, "", [], {}):
            normalized["details"][str(key)] = value
    return normalized


async def fetch_entity_metadata(db, entity_type: str, name: str, provider_ids: object = None) -> dict[str, dict]:
    """Fetch complete provider entity records with stale-ID recovery.

    Provider IDs persisted by older metadata passes can become stale or belong
    to a provider endpoint that has since changed. An ID lookup is therefore
    only an optimization: when it returns no entity or a non-retryable provider
    validation/not-found response, the provider is queried once by the local
    canonical name. Negative ID-cache entries are never treated as proof that
    the entity does not exist.
    """
    target = str(name or "").strip()
    if not target or entity_type not in {"performer", "studio"}:
        return {}
    ids = _provider_ids(provider_ids)
    out: dict[str, dict] = {}

    async def stash():
        """Fetch this entity from StashDB when a key is configured."""
        api_key = db.setting("stashdb_api_key")
        if not api_key:
            return
        provider_errors: list[str] = []
        ident = ids.get("stashdb")

        async def by_id() -> list[dict]:
            cache_key = f"entity:{entity_type}:id:{ident}"
            cached, hit = _cache_get(db, "stashdb", cache_key)
            if hit:
                return [x for x in (cached or []) if isinstance(x, dict)]
            await _throttle("stashdb")
            if entity_type == "performer":
                data = await asyncio.to_thread(
                    _stashdb_graphql, db, api_key, _STASHDB_PERFORMER_BY_ID_QUERY, {"id": ident}
                )
                node = ((data.get("data") or {}).get("findPerformer") or {}) if isinstance(data, dict) else {}
            else:
                data = await asyncio.to_thread(
                    _stashdb_graphql, db, api_key, _STASHDB_STUDIO_BY_ID_QUERY, {"id": ident}
                )
                node = ((data.get("data") or {}).get("findStudio") or {}) if isinstance(data, dict) else {}
            rows = [node] if isinstance(node, dict) and node else []
            # Do not persist an empty ID lookup as a long-lived negative result;
            # the name fallback below is the authoritative recovery path.
            if rows:
                _cache_put(db, "stashdb", cache_key, rows)
            return rows

        async def by_name() -> list[dict]:
            cache_key = f"entity:{entity_type}:name:{_norm_entity_name(target)}"
            cached, hit = _cache_get(db, "stashdb", cache_key)
            if hit:
                return [x for x in (cached or []) if isinstance(x, dict)]
            await _throttle("stashdb")
            if entity_type == "performer":
                data = await asyncio.to_thread(
                    _stashdb_graphql, db, api_key, _STASHDB_PERFORMER_SEARCH_QUERY,
                    {"term": target, "limit": 10},
                )
                rows = _stashdb_rows(data, "searchPerformers")
            else:
                data = await asyncio.to_thread(
                    _stashdb_graphql, db, api_key, _STASHDB_STUDIO_BY_NAME_QUERY, {"name": target}
                )
                node = ((data.get("data") or {}).get("findStudio") or {}) if isinstance(data, dict) else {}
                rows = [node] if isinstance(node, dict) and node else []
            _cache_put(db, "stashdb", cache_key, rows or [])
            return rows

        rows: list[dict] = []
        if ident:
            try:
                rows = await by_id()
            except Exception as exc:
                provider_errors.append(f"id lookup: {type(exc).__name__}: {exc}")
        if not rows:
            try:
                rows = await by_name()
            except Exception as exc:
                provider_errors.append(f"name lookup: {type(exc).__name__}: {exc}")
        if rows:
            wanted = _norm_entity_name(target)
            explicit = str(ident or "").strip()
            best = next((r for r in rows if str(r.get("id") or "").strip() == explicit), None) if explicit else None
            if best is None:
                best = next((r for r in rows if _norm_entity_name(str(r.get("name") or "")) == wanted), rows[0])
            if isinstance(best, dict):
                out["stashdb"] = best
        elif provider_errors:
            db.log(f"StashDB entity lookup failed for {entity_type} '{target}': {'; '.join(provider_errors)}", "WARN")

    async def tpdb():
        """Fetch this entity from ThePornDB when a token is configured."""
        token = db.setting("tpdb_api_token")
        if not token:
            return
        provider_errors: list[str] = []
        ident = ids.get("theporndb") or ids.get("tpdb")

        async def by_id() -> list[dict]:
            cache_key = f"entity:{entity_type}:id:{ident}"
            cached, hit = _cache_get(db, "tpdb", cache_key)
            if hit:
                return [x for x in (cached or []) if isinstance(x, dict)]
            await _throttle("tpdb")
            endpoint = (
                f"performers/{urllib.parse.quote(ident, safe='')}" if entity_type == "performer"
                else f"sites/{urllib.parse.quote(ident, safe='')}"
            )
            keys = ("data", "performer") if entity_type == "performer" else ("data", "site")
            raw = await asyncio.to_thread(_tpdb_get, db, token, endpoint)
            return _provider_entity_rows(raw, keys)

        async def by_name() -> list[dict]:
            cache_key = f"entity:{entity_type}:name:{_norm_entity_name(target)}"
            cached, hit = _cache_get(db, "tpdb", cache_key)
            if hit:
                return [x for x in (cached or []) if isinstance(x, dict)]
            await _throttle("tpdb")
            if entity_type == "performer":
                endpoint = f"performers?{urllib.parse.urlencode({'q': target})}"
                keys = ("data", "performers", "results")
            else:
                endpoint = f"sites?{urllib.parse.urlencode({'q': target})}"
                keys = ("data", "sites", "results")
            raw = await asyncio.to_thread(_tpdb_get, db, token, endpoint)
            rows = _provider_entity_rows(raw, keys)
            _cache_put(db, "tpdb", cache_key, rows or [])
            return rows

        rows: list[dict] = []
        if ident:
            try:
                rows = await by_id()
            except Exception as exc:
                provider_errors.append(f"id lookup: {type(exc).__name__}: {exc}")
        if not rows:
            try:
                rows = await by_name()
            except Exception as exc:
                provider_errors.append(f"name lookup: {type(exc).__name__}: {exc}")
        if rows:
            wanted = _norm_entity_name(target)
            explicit = str(ident or "").strip()
            best = next((r for r in rows if str(r.get("id") or r.get("_id") or "").strip() == explicit), None) if explicit else None
            if best is None:
                best = next((r for r in rows if _norm_entity_name(str(r.get("name") or r.get("full_name") or "")) == wanted), rows[0])
            if isinstance(best, dict):
                out["theporndb"] = best
        elif provider_errors:
            db.log(f"ThePornDB entity lookup failed for {entity_type} '{target}': {'; '.join(provider_errors)}", "WARN")

    await asyncio.gather(stash(), tpdb())
    return out


def _merge_entity_local_fields(db, entity_type: str, entity_id: int, rows: dict[str, dict]) -> None:
    """Promote safe provider identity/profile fields into the local entity.

    Provider data remains authoritative in entity_metadata. Local fields are
    only filled when empty, so operator edits are never overwritten.
    """
    if not rows:
        return
    if entity_type == "performer":
        with db.lock:
            fallback = db.conn.execute("SELECT name FROM performers WHERE id=?", (int(entity_id),)).fetchone()
        if fallback and str(fallback["name"] or "").strip().casefold() == "unknown performer":
            return
    table = "performers" if entity_type == "performer" else "studios"
    with db.lock:
        # V29.4.126: studios have no profile/gender columns; selecting them
        # made every studio entity sync fail with "no such column: profile".
        columns = "provider_ids, aliases, image, profile, gender" if table == "performers" else "provider_ids, aliases, image"
        current = db.conn.execute(
            f"SELECT {columns} FROM {table} WHERE id=?",
            (int(entity_id),),
        ).fetchone()
        if not current:
            return
        provider_ids = {}
        try:
            provider_ids = json.loads(current["provider_ids"] or "{}") if current["provider_ids"] else {}
        except Exception:
            provider_ids = {}
        aliases = []
        try:
            decoded = json.loads(current["aliases"] or "[]") if current["aliases"] else []
            aliases = decoded if isinstance(decoded, list) else []
        except Exception:
            aliases = []
        updates: dict[str, object] = {}
        for provider, row in rows.items():
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or row.get("_id") or row.get("uuid") or "").strip()
            if pid and not provider_ids.get(provider):
                provider_ids[provider] = pid
            if not aliases:
                vals = row.get("aliases") or row.get("alias_list") or []
                if isinstance(vals, list):
                    aliases = [str(v).strip() for v in vals if str(v).strip()]
            if entity_type == "performer":
                profile = str(row.get("url") or row.get("profile") or row.get("website") or "").strip()
                gender = str(row.get("gender") or "").strip()
                if not str(current["profile"] or "").strip() and profile:
                    updates["profile"] = profile
                if not str(current["gender"] or "").strip() and gender:
                    updates["gender"] = gender
            if not str(current["image"] or "").strip():
                image = _http_entity_image(row, performer=(entity_type == "performer"))
                if image:
                    updates["image"] = image
        if provider_ids:
            updates["provider_ids"] = json.dumps(provider_ids, ensure_ascii=False, sort_keys=True)
        if aliases and not str(current["aliases"] or "").strip():
            updates["aliases"] = json.dumps(aliases, ensure_ascii=False)
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            db.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*updates.values(), int(entity_id)))
            db.conn.commit()


def store_entity_metadata(db, entity_type: str, entity_id: int, rows: dict[str, dict]) -> int:
    if entity_type == "performer":
        with db.lock:
            fallback = db.conn.execute("SELECT name FROM performers WHERE id=?", (int(entity_id),)).fetchone()
        if fallback and str(fallback["name"] or "").strip().casefold() == "unknown performer":
            return 0
    now = time.time()
    changed = 0
    with db.lock:
        for provider, row in rows.items():
            if not isinstance(row, dict): continue
            provider_id = str(row.get("id") or row.get("_id") or row.get("uuid") or "").strip() or None
            normalized = _entity_payload_for_storage(row, provider=provider, entity_type=entity_type)
            db.conn.execute(
                "INSERT OR REPLACE INTO entity_metadata(entity_type,entity_id,provider,provider_id,payload,normalized,fetched_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (entity_type, int(entity_id), provider, provider_id,
                 json.dumps(row, ensure_ascii=False, sort_keys=True),
                 json.dumps(normalized, ensure_ascii=False, sort_keys=True), now, __import__("telegram_archive.config", fromlist=["now_iso"]).now_iso())
            )
            changed += 1
        db.conn.commit()
    return changed




# ==============================================================================
# SECTION: Entity search and images
# Provider searches and image candidates.
# ==============================================================================

def _provider_ids(provider_ids: object) -> dict[str, str]:
    """Return one lookup ID per provider; merged alternate IDs stay indexed by identity resolution."""
    if isinstance(provider_ids, str):
        raw = provider_ids.strip()
        if raw:
            try:
                provider_ids = json.loads(raw)
            except Exception:
                provider_ids = {}
    if isinstance(provider_ids, dict):
        out = {}
        for key, value in provider_ids.items():
            values = value if isinstance(value, (list, tuple, set)) else [value]
            for item in values:
                text = str(item or '').strip()
                if text:
                    out[str(key).casefold()] = text
                    break
        return out
    return {}


def _image_candidates_from_images(images: object, limit: int = 12) -> list[dict]:
    out: list[dict] = []
    if not isinstance(images, list):
        return out
    seen = set()
    for index, item in enumerate(images):
        if isinstance(item, dict):
            raw_url = item.get('url') or item.get('image') or item.get('src') or item.get('poster')
            width = item.get('width') or 0
            height = item.get('height') or 0
        else:
            raw_url = item
            width = height = 0
        url = _http_image_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            width = int(width or 0)
            height = int(height or 0)
        except Exception:
            width = height = 0
        out.append({'url': url, 'width': width, 'height': height, 'rank': index})
        if len(out) >= max(1, int(limit)):
            break
    return out


def _stashdb_graphql(db, api_key: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    return _http(STASHDB_URL, data=body, headers={"Content-Type": "application/json", "APIKey": api_key})


def _tpdb_get(db, token: str, path: str) -> dict:
    url = f"{TPDB_URL}/{path.lstrip('/')}"
    return _http(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})


async def search_performer_providers(db, name: str, limit: int = 8) -> list[dict]:
    """Search configured performer providers without creating local entities.

    Returns provider candidates for an explicit mapping picker. Results are
    cached and queried concurrently so interactive searches do not block the
    FastAPI event loop.
    """
    target = str(name or "").strip()
    if len(target) < 2:
        return []
    limit = max(1, min(10, int(limit or 8)))
    # Ask each provider for more than will be shown, then rank here: a provider's own order puts famous namesakes first, which can bury the
    # person being typed ("stacy" returning eight other Stacys before Stacy Cruz).
    fetch = max(limit * 3, 20)

    async def stash_candidates():
        """Search StashDB for performer candidates."""
        api_key = db.setting("stashdb_api_key")
        if not api_key:
            return []
        key = f"entity-search:performer:{_norm_entity_name(target)}:{fetch}"
        cached, hit = _cache_get(db, "stashdb", key, max_age=30 * 86400)
        if hit:
            rows = cached or []
        else:
            await _throttle("stashdb")
            data = await asyncio.to_thread(
                _stashdb_graphql, db, api_key, _STASHDB_PERFORMER_SEARCH_QUERY,
                {"term": target, "limit": fetch},
            )
            rows = _stashdb_rows(data, "searchPerformers")
            if rows:
                _cache_put(db, "stashdb", key, rows)
        out = []
        wanted = _norm_entity_name(target)
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            pname = str(row.get("name") or row.get("full_name") or "").strip()
            if not pname:
                continue
            aliases = row.get("aliases") or []
            aliases = [str(a).strip() for a in aliases if str(a).strip()]
            norm = _norm_entity_name(pname)
            alias_exact = any(_norm_entity_name(a) == wanted for a in aliases)
            exact = norm == wanted
            out.append({
                "provider": "stashdb",
                "provider_id": str(row.get("id") or ""),
                "name": pname,
                "aliases": aliases,
                "image": _http_entity_image(row, performer=True),
                "profile": str(row.get("url") or row.get("profile") or "").strip(),
                "exact": bool(exact),
                "alias_exact": bool(alias_exact),
                "rank": idx,
            })
        return out

    async def tpdb_candidates():
        token = db.setting("tpdb_api_token")
        if not token:
            return []
        key = f"entity-search:performer:{_norm_entity_name(target)}:{fetch}"
        cached, hit = _cache_get(db, "tpdb", key, max_age=30 * 86400)
        if hit:
            rows = cached or []
        else:
            await _throttle("tpdb")
            q = urllib.parse.urlencode({"q": target, "limit": fetch})
            data = await asyncio.to_thread(_tpdb_get, db, token, f"performers?{q}")
            rows = _provider_entity_rows(data, ("data", "performers", "results"))
            if rows:
                _cache_put(db, "tpdb", key, rows)
        out = []
        wanted = _norm_entity_name(target)
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            pname = str(row.get("name") or row.get("full_name") or "").strip()
            if not pname:
                continue
            aliases = row.get("aliases") or []
            aliases = [str(a).strip() for a in aliases if str(a).strip()] if isinstance(aliases, list) else []
            exact = _norm_entity_name(pname) == wanted
            alias_exact = any(_norm_entity_name(a) == wanted for a in aliases)
            out.append({
                "provider": "theporndb",
                "provider_id": str(row.get("id") or row.get("_id") or row.get("uuid") or ""),
                "name": pname,
                "aliases": aliases,
                "image": _tpdb_entity_image(row, performer=True)[0],
                "profile": str(row.get("url") or row.get("profile") or "").strip(),
                "exact": bool(exact),
                "alias_exact": bool(alias_exact),
                "rank": idx,
            })
        return out

    results = await asyncio.gather(stash_candidates(), tpdb_candidates(), return_exceptions=True)
    merged: list[dict] = []
    for item in results:
        if isinstance(item, Exception):
            continue
        merged.extend(item)

    # Best matches first: the exact name or alias, then names whose words start with what was typed, then names containing every typed word,
    # then the provider's own order.
    import re as _re

    def spaced(value: object) -> str:
        return _re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    words = spaced(target).split()

    def closeness(item: dict) -> int:
        names = [spaced(item.get("name"))] + [spaced(a) for a in item.get("aliases") or []]
        if item.get("exact") or item.get("alias_exact"):
            return 0
        if any(n.startswith(" ".join(words)) for n in names if n):
            return 1
        if any(words and all(any(tok.startswith(w) for tok in n.split()) for w in words) for n in names if n):
            return 2
        if any(words and all(w in n for w in words) for n in names if n):
            return 3
        return 4

    seen = set()
    unique = []
    for item in sorted(merged, key=lambda x: (closeness(x), x.get("rank", 999), 0 if x.get("provider") == "stashdb" else 1)):
        key = (item.get("provider"), item.get("provider_id"))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


async def search_studio_providers(db, name: str, limit: int = 8) -> list[dict]:
    """Search configured StashDB and ThePornDB studio catalogs for picker candidates.

    This is read-only: provider candidates are returned for explicit user review,
    never imported or linked merely because the user typed a search term.
    """
    target = str(name or "").strip()
    if len(target) < 2:
        return []
    limit = max(1, min(10, int(limit or 8)))
    wanted = _norm_entity_name(target)

    def normalize_stash_rows(payload: object) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        root = payload.get("data") or {}
        if not isinstance(root, dict):
            return []
        value = root.get("searchStudio") or root.get("searchStudios")
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            rows = value.get("studios") or value.get("results")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
            if any(key in value for key in ("id", "name")):
                return [value]
        return []

    async def stash_candidates() -> list[dict]:
        """Search StashDB for studio candidates."""
        api_key = db.setting("stashdb_api_key")
        if not api_key:
            return []
        cache_key = f"entity-search:studio:{wanted}:{limit}"
        cached, hit = _cache_get(db, "stashdb", cache_key, max_age=30 * 86400)
        if hit:
            rows = cached or []
        else:
            await _throttle("stashdb")
            query = """
query($term: String!, $limit: Int!) {
  searchStudio(term: $term, limit: $limit) {
    id name aliases urls { url } images { url width height } deleted
  }
}"""
            data = await asyncio.to_thread(
                _stashdb_graphql, db, api_key, query,
                {"term": target, "limit": limit},
            )
            rows = normalize_stash_rows(data)
            # Some compatible StashBox servers expose only an exact lookup.
            # Use it as a read-only fallback when text search is unavailable/empty.
            if not rows and isinstance(data, dict) and data.get("errors"):
                exact_query = """
query($name: String!) {
  findStudio(name: $name) { id name aliases urls { url } images { url width height } deleted }
}"""
                try:
                    exact_data = await asyncio.to_thread(
                        _stashdb_graphql, db, api_key, exact_query, {"name": target}
                    )
                    node = ((exact_data.get("data") or {}).get("findStudio") or {}) if isinstance(exact_data, dict) else {}
                    if isinstance(node, dict) and node:
                        rows = [node]
                except Exception:
                    rows = []
            _cache_put(db, "stashdb", cache_key, rows)
        out = []
        for idx, row in enumerate(rows):
            name_value = str(row.get("name") or row.get("title") or "").strip()
            if not name_value:
                continue
            raw_aliases = row.get("aliases") or []
            aliases = [str(alias.get("name") if isinstance(alias, dict) else alias).strip()
                       for alias in (raw_aliases if isinstance(raw_aliases, list) else [raw_aliases])
                       if str(alias.get("name") if isinstance(alias, dict) else alias).strip()]
            exact = _norm_entity_name(name_value) == wanted
            alias_exact = any(_norm_entity_name(alias) == wanted for alias in aliases)
            image = _http_entity_image(row, performer=False)
            if not image:
                image = _http_image_url(row.get("logo") or row.get("poster"))
            profile = str(row.get("url") or row.get("profile") or "").strip()
            if not profile:
                profile = str(_first_provider_url(row.get("urls")) or "").strip()
            out.append({
                "provider": "stashdb", "provider_id": str(row.get("id") or ""),
                "name": name_value, "aliases": aliases, "image": image,
                "profile": profile, "exact": bool(exact),
                "alias_exact": bool(alias_exact), "rank": idx,
            })
        return out

    async def tpdb_candidates() -> list[dict]:
        token = db.setting("tpdb_api_token") or db.setting("tpdb_token")
        if not token:
            return []
        cache_key = f"entity-search:studio:{wanted}:{limit}"
        cached, hit = _cache_get(db, "tpdb", cache_key, max_age=30 * 86400)
        if hit:
            rows = cached or []
        else:
            await _throttle("tpdb")
            query = urllib.parse.urlencode({"q": target, "limit": limit})
            raw = await asyncio.to_thread(_tpdb_get, db, token, f"sites?{query}")
            rows = _provider_entity_rows(raw, ("sites", "data", "results"))
            _cache_put(db, "tpdb", cache_key, rows)
        out = []
        for idx, row in enumerate(rows):
            name_value = str(row.get("name") or row.get("title") or row.get("site_name") or "").strip()
            if not name_value:
                continue
            raw_aliases = row.get("aliases") or row.get("alternative_names") or []
            aliases = [str(alias.get("name") if isinstance(alias, dict) else alias).strip()
                       for alias in (raw_aliases if isinstance(raw_aliases, list) else [raw_aliases])
                       if str(alias.get("name") if isinstance(alias, dict) else alias).strip()]
            exact = _norm_entity_name(name_value) == wanted
            alias_exact = any(_norm_entity_name(alias) == wanted for alias in aliases)
            image, _ = _tpdb_entity_image(row, performer=False)
            image = _http_image_url(image or row.get("logo") or row.get("image_url"))
            profile = str(row.get("url") or row.get("profile") or row.get("website") or "").strip()
            out.append({
                "provider": "theporndb", "provider_id": str(row.get("id") or row.get("_id") or row.get("uuid") or ""),
                "name": name_value, "aliases": aliases, "image": image,
                "profile": profile, "exact": bool(exact),
                "alias_exact": bool(alias_exact), "rank": idx,
            })
        return out

    fetched = await asyncio.gather(stash_candidates(), tpdb_candidates(), return_exceptions=True)
    merged: list[dict] = []
    for rows in fetched:
        if not isinstance(rows, Exception):
            merged.extend(rows)
    # Keep the best name/alias matches first and preserve provider identity.
    # A same-named StashDB and ThePornDB candidate can be valuable because their
    # IDs are provider-scoped; do not collapse those into an unverified identity.
    merged.sort(key=lambda item: (
        not item.get("exact"), not item.get("alias_exact"),
        0 if item.get("provider") == "stashdb" else 1,
        int(item.get("rank") or 0), str(item.get("name") or "").casefold(),
    ))
    seen: set[tuple[str, str]] = set()
    output = []
    for item in merged:
        provider_id = str(item.get("provider_id") or "")
        provider_key = str(item.get("provider") or "")
        identity = (provider_key, provider_id)
        if provider_id and identity in seen:
            continue
        if provider_id:
            seen.add(identity)
        output.append(item)
        if len(output) >= limit:
            break
    return output


async def performer_image_candidates_from_providers(db, name: str, provider_ids: object = None, limit: int = 10, entity_id: int | None = None, offset: int = 0) -> list[dict]:
    """Return multiple profile-image candidates for one performer.

    Provider IDs are preferred when available. Results are returned without
    modifying the local performer so the UI can let the operator choose the
    desired artwork explicitly.
    """
    target = str(name or "").strip()
    if not target:
        return []
    # `limit` is the per-provider page size for the picker. The UI intentionally
    # renders provider lanes independently so one provider can never consume
    # the other provider's visible quota.
    limit = max(1, min(20, int(limit or 10)))
    offset = max(0, int(offset or 0))
    ids = _provider_ids(provider_ids)
    results: list[dict] = []
    seen_urls: set[str] = set()

    # Reuse successfully synchronized provider artwork before contacting a
    # remote provider again. Entity Data is authoritative for already stored
    # provider responses, so an image-picker must not report "no images" just
    # because a provider is temporarily unavailable or its API shape changed.
    if entity_id is not None:
        try:
            with db.reading() as rdb:
                stored = rdb.execute(
                    "SELECT provider,provider_id,normalized FROM entity_metadata "
                    "WHERE entity_type='performer' AND entity_id=? ORDER BY provider",
                    (int(entity_id),),
                ).fetchall()
            for row in stored:
                provider_name = str(row["provider"] or "local").strip().lower()
                try:
                    normalized = json.loads(row["normalized"] or "{}")
                except Exception:
                    normalized = {}
                images = _image_candidates_from_images(normalized.get("images"), limit=100)
                for idx, img in enumerate(images):
                    url = _http_image_url(img.get("url"))
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    results.append({
                        "provider": provider_name,
                        "provider_id": str(row["provider_id"] or ""),
                        "name": target,
                        "url": url,
                        "width": int(img.get("width") or 0),
                        "height": int(img.get("height") or 0),
                        "rank": (0, 0 if provider_name == "stashdb" else 1, idx),
                        "source": "local-entity-metadata",
                    })
        except Exception:
            # Local artwork recovery is best-effort; remote provider lookup
            # remains the fallback path.
            _swallowed_note("providers.performer_image_candidates_from_providers")
            pass

    async def stash_rows() -> list[dict]:
        key = db.setting("stashdb_api_key")
        if not key:
            return []
        cache_key = (f"entity:performer:id:{ids['stashdb']}" if ids.get('stashdb')
                     else f"entity:performer:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "stashdb", cache_key)
        if hit:
            return cached or []
        await _throttle("stashdb")
        if ids.get("stashdb"):
            data = await asyncio.to_thread(
                _stashdb_graphql, db, key, _STASHDB_PERFORMER_BY_ID_QUERY,
                {"id": ids["stashdb"]},
            )
            node = ((data.get("data") or {}).get("findPerformer") or {}) if isinstance(data, dict) else {}
            rows = [node] if isinstance(node, dict) and node else []
        else:
            data = await asyncio.to_thread(
                _stashdb_graphql, db, key, _STASHDB_PERFORMER_SEARCH_QUERY,
                {"term": target, "limit": 10},
            )
            rows = _stashdb_rows(data, "searchPerformers")
        _cache_put(db, "stashdb", cache_key, rows)
        return rows

    async def tpdb_rows() -> list[dict]:
        token = db.setting("tpdb_api_token")
        if not token:
            return []
        ident = ids.get("theporndb") or ids.get("tpdb")
        cache_key = (f"entity:performer:id:{ident}" if ident
                     else f"entity:performer:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "tpdb", cache_key)
        if hit:
            return cached or []
        await _throttle("tpdb")
        if ident:
            raw = await asyncio.to_thread(
                _tpdb_get, db, token,
                f"performers/{urllib.parse.quote(ident, safe='')}",
            )
            rows = _provider_entity_rows(raw, ("data", "performer"))
        else:
            q = urllib.parse.urlencode({"q": target})
            raw = await asyncio.to_thread(_tpdb_get, db, token, f"performers?{q}")
            rows = _provider_entity_rows(raw, ("data", "performers", "results"))
        _cache_put(db, "tpdb", cache_key, rows)
        return rows

    # Do not let a full StashDB local cache suppress ThePornDB. The picker is
    # explicitly a multi-provider surface, so each configured provider must get
    # a chance to contribute artwork even when another provider already filled
    # the requested card count.
    local_providers = {str(item.get("provider") or "").strip().lower() for item in results}
    need_stash = "stashdb" not in local_providers and bool(db.setting("stashdb_api_key"))
    need_tpdb = "theporndb" not in local_providers and bool(db.setting("tpdb_api_token"))
    fetched = await asyncio.gather(
        stash_rows() if need_stash else asyncio.sleep(0, result=[]),
        tpdb_rows() if need_tpdb else asyncio.sleep(0, result=[]),
        return_exceptions=True,
    )
    provider_rows = (("stashdb", fetched[0]) if not isinstance(fetched[0], Exception) else ("stashdb", []),
                     ("theporndb", fetched[1]) if not isinstance(fetched[1], Exception) else ("theporndb", []))
    target_norm = _norm_entity_name(target)

    for provider, rows in provider_rows:
        for row_index, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            pname = str(row.get("name") or row.get("full_name") or "").strip()
            aliases = row.get("aliases") or []
            aliases = aliases if isinstance(aliases, list) else []
            exact = _norm_entity_name(pname) == target_norm
            alias_exact = any(_norm_entity_name(str(a)) == target_norm for a in aliases)
            # When an explicit provider ID exists, trust that entity even if
            # the provider does not echo the same display-name spelling.
            explicit_id = ids.get(provider) or ids.get("tpdb") if provider == "theporndb" else ids.get("stashdb")
            row_id = str(row.get("id") or row.get("_id") or row.get("uuid") or "").strip()
            if explicit_id and row_id and row_id == explicit_id:
                exact = True
            if not (exact or alias_exact or explicit_id):
                continue

            image_items = _image_candidates_from_images(row.get("images"), limit=100)
            top_image = row.get("image") or row.get("image_url") or row.get("image_path") or row.get("photo")
            if top_image:
                top_url = _http_image_url(top_image)
                if top_url and top_url not in {x['url'] for x in image_items}:
                    image_items.insert(0, {'url': top_url, 'width': 0, 'height': 0, 'rank': -1})
            if provider == "theporndb" and not image_items:
                posters = row.get("posters") or []
                image_items = _image_candidates_from_images(posters, limit=100)
                if not image_items:
                    image, _pid = _tpdb_entity_image(row, performer=True)
                    if image:
                        image_items = [{'url': _http_image_url(image), 'width': 0, 'height': 0, 'rank': -1}]

            for img_index, img in enumerate(image_items):
                url = _http_image_url(img.get("url"))
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append({
                    "provider": provider,
                    "provider_id": row_id,
                    "name": pname or target,
                    "url": url,
                    "width": int(img.get("width") or 0),
                    "height": int(img.get("height") or 0),
                    "rank": (0 if exact else 1 if alias_exact else 2, row_index, img_index),
                })
                if len(results) >= limit * 2:
                    break
    # Keep provider lanes independent: first page is 10 StashDB + 10 ThePornDB,
    # subsequent pages expose the next 10 from each provider. This avoids the
    # old global cap where StashDB could crowd ThePornDB out of the picker.
    provider_results = {
        "stashdb": [x for x in results if str(x.get("provider") or "").strip().lower() == "stashdb"],
        "theporndb": [x for x in results if str(x.get("provider") or "").strip().lower() == "theporndb"],
    }
    start = offset
    end = start + limit
    return provider_results["stashdb"][start:end] + provider_results["theporndb"][start:end]


async def performer_image_from_providers(db, name: str, provider_ids: object = None, skip_sources: set[str] | None = None) -> tuple[str, str, str]:
    """Direct performer lookup across both configured image providers. StashDB is preferred, TPDB fills gaps."""
    target = str(name or "").strip()
    if not target:
        return "", "", ""
    ids = _provider_ids(provider_ids)
    stash_key = db.setting("stashdb_api_key")
    # Canonical settings use tpdb_api_token; accept the legacy tpdb_token key too.
    tpdb_key = db.setting("tpdb_api_token") or db.setting("tpdb_token")
    candidates: list[tuple[int, str, str, str]] = []
    skipped = {str(x).casefold() for x in (skip_sources or set())}

    if stash_key:
        cache_key = (f"entity:performer:id:{ids['stashdb']}" if ids.get("stashdb") else f"entity:performer:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "stashdb", cache_key)
        try:
            if not hit:
                await _throttle("stashdb")
                if ids.get("stashdb"):
                    data = await asyncio.to_thread(_stashdb_graphql, db, stash_key, _STASHDB_PERFORMER_BY_ID_QUERY, {"id": ids["stashdb"]})
                    node = ((data.get("data") or {}).get("findPerformer") or {}) if isinstance(data, dict) else {}
                    cached = [node] if isinstance(node, dict) and node else []
                else:
                    data = await asyncio.to_thread(_stashdb_graphql, db, stash_key, _STASHDB_PERFORMER_SEARCH_QUERY, {"term": target, "limit": 10})
                    cached = _stashdb_rows(data, "searchPerformers")
                _cache_put(db, "stashdb", cache_key, cached)
            rows = cached or []
            image, _source, pid = _best_named_entity(rows, target, performer=True)
            if image and "stashdb" not in skipped:
                candidates.append((2, image, "stashdb", pid))
        except Exception:
            _swallowed_note("providers.performer_image_from_providers")
            pass

    if tpdb_key:
        tpdb_ident = ids.get("theporndb") or ids.get("tpdb")
        cache_key = (f"entity:performer:id:{tpdb_ident}" if tpdb_ident else f"entity:performer:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "tpdb", cache_key)
        try:
            if not hit:
                await _throttle("tpdb")
                if ids.get("theporndb") or ids.get("tpdb"):
                    ident = ids.get("theporndb") or ids.get("tpdb")
                    cached = _provider_entity_rows(await asyncio.to_thread(_tpdb_get, db, tpdb_key, f"performers/{urllib.parse.quote(ident, safe='')}"), ("data", "performer"))
                else:
                    q = urllib.parse.urlencode({"q": target})
                    cached = _provider_entity_rows(await asyncio.to_thread(_tpdb_get, db, tpdb_key, f"performers?{q}"), ("data", "performers", "results"))
                _cache_put(db, "tpdb", cache_key, cached)
            rows = cached or []
            image, _source, pid = _best_named_entity(rows, target, performer=True)
            if image and "theporndb" not in skipped and "tpdb" not in skipped:
                candidates.append((1, image, "theporndb", pid))
        except Exception:
            _swallowed_note("providers.performer_image_from_providers")
            pass

    if not candidates:
        return "", "", ""
    _rank, image, source, pid = max(candidates, key=lambda x: x[0])
    return image, source, pid


async def studio_image_candidates_from_providers(db, name: str, provider_ids: object = None, limit: int = 10, entity_id: int | None = None, offset: int = 0) -> list[dict]:
    """Return multiple studio artwork candidates without changing the local studio."""
    target = str(name or "").strip()
    if not target:
        return []
    # `limit` is the per-provider page size for the picker.
    limit = max(1, min(20, int(limit or 10)))
    offset = max(0, int(offset or 0))
    ids = _provider_ids(provider_ids)
    results: list[dict] = []
    seen_urls: set[str] = set()

    # Reuse successfully synchronized provider artwork before contacting a
    # remote provider again. Entity Data is authoritative for already stored
    # provider responses, so an image-picker must not report "no images" just
    # because a provider is temporarily unavailable or its API shape changed.
    if entity_id is not None:
        try:
            with db.reading() as rdb:
                stored = rdb.execute(
                    "SELECT provider,provider_id,normalized FROM entity_metadata "
                    "WHERE entity_type='studio' AND entity_id=? ORDER BY provider",
                    (int(entity_id),),
                ).fetchall()
            for row in stored:
                provider_name = str(row["provider"] or "local").strip().lower()
                try:
                    normalized = json.loads(row["normalized"] or "{}")
                except Exception:
                    normalized = {}
                images = _image_candidates_from_images(normalized.get("images"), limit=100)
                for idx, img in enumerate(images):
                    url = _http_image_url(img.get("url"))
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    results.append({
                        "provider": provider_name,
                        "provider_id": str(row["provider_id"] or ""),
                        "name": target,
                        "url": url,
                        "width": int(img.get("width") or 0),
                        "height": int(img.get("height") or 0),
                        "rank": (0, 0 if provider_name == "stashdb" else 1, idx),
                        "source": "local-entity-metadata",
                    })
        except Exception:
            # Local artwork recovery is best-effort; remote provider lookup
            # remains the fallback path.
            _swallowed_note("providers.studio_image_candidates_from_providers")
            pass

    async def stash_rows() -> list[dict]:
        key = db.setting("stashdb_api_key")
        if not key:
            return []
        cache_key = (f"entity:studio:id:{ids['stashdb']}" if ids.get('stashdb') else f"entity:studio:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "stashdb", cache_key)
        if hit:
            return cached or []
        await _throttle("stashdb")
        if ids.get("stashdb"):
            data = await asyncio.to_thread(_stashdb_graphql, db, key, _STASHDB_STUDIO_BY_ID_QUERY, {"id": ids["stashdb"]})
            node = ((data.get("data") or {}).get("findStudio") or {}) if isinstance(data, dict) else {}
            rows = [node] if isinstance(node, dict) and node else []
        else:
            data = await asyncio.to_thread(_stashdb_graphql, db, key, _STASHDB_STUDIO_BY_NAME_QUERY, {"name": target})
            node = ((data.get("data") or {}).get("findStudio") or {}) if isinstance(data, dict) else {}
            rows = [node] if isinstance(node, dict) and node else []
        _cache_put(db, "stashdb", cache_key, rows)
        return rows

    async def tpdb_rows() -> list[dict]:
        token = db.setting("tpdb_api_token")
        if not token:
            return []
        ident = ids.get("theporndb") or ids.get("tpdb")
        cache_key = (f"entity:studio:id:{ident}" if ident else f"entity:studio:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "tpdb", cache_key)
        if hit:
            return cached or []
        await _throttle("tpdb")
        if ident:
            raw = await asyncio.to_thread(_tpdb_get, db, token, f"sites/{urllib.parse.quote(ident, safe='')}")
            rows = _provider_entity_rows(raw, ("data", "site"))
        else:
            q = urllib.parse.urlencode({"q": target})
            raw = await asyncio.to_thread(_tpdb_get, db, token, f"sites?{q}")
            rows = _provider_entity_rows(raw, ("data", "sites", "results"))
        _cache_put(db, "tpdb", cache_key, rows)
        return rows

    # Do not let a full StashDB local cache suppress ThePornDB. The picker is
    # explicitly a multi-provider surface, so each configured provider must get
    # a chance to contribute artwork even when another provider already filled
    # the requested card count.
    local_providers = {str(item.get("provider") or "").strip().lower() for item in results}
    need_stash = "stashdb" not in local_providers and bool(db.setting("stashdb_api_key"))
    need_tpdb = "theporndb" not in local_providers and bool(db.setting("tpdb_api_token"))
    fetched = await asyncio.gather(
        stash_rows() if need_stash else asyncio.sleep(0, result=[]),
        tpdb_rows() if need_tpdb else asyncio.sleep(0, result=[]),
        return_exceptions=True,
    )
    provider_rows = (("stashdb", fetched[0]) if not isinstance(fetched[0], Exception) else ("stashdb", []),
                     ("theporndb", fetched[1]) if not isinstance(fetched[1], Exception) else ("theporndb", []))
    seen_urls: set[str] = set()
    target_norm = _norm_entity_name(target)
    for provider, rows in provider_rows:
        for row_index, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            sname = str(row.get("name") or row.get("title") or "").strip()
            aliases = row.get("aliases") or []
            aliases = aliases if isinstance(aliases, list) else []
            explicit_id = ids.get(provider) if provider == "stashdb" else (ids.get("theporndb") or ids.get("tpdb"))
            row_id = str(row.get("id") or row.get("_id") or row.get("uuid") or "").strip()
            exact = _norm_entity_name(sname) == target_norm
            alias_exact = any(_norm_entity_name(str(a)) == target_norm for a in aliases)
            if explicit_id and row_id and row_id == explicit_id:
                exact = True
            if not (exact or alias_exact or explicit_id):
                continue
            image_items = _image_candidates_from_images(row.get("images"), limit=100)
            top_image = row.get("image") or row.get("image_url") or row.get("image_path") or row.get("logo") or row.get("poster")
            if top_image:
                top_url = _http_image_url(top_image)
                if top_url and top_url not in {x.get('url') for x in image_items}:
                    image_items.insert(0, {"url": top_url, "width": 0, "height": 0, "rank": -1})
            if provider == "theporndb" and not image_items:
                posters = row.get("posters") or []
                image_items = _image_candidates_from_images(posters, limit=100)
                if not image_items:
                    image, _pid = _tpdb_entity_image(row, performer=False)
                    if image:
                        image_items = [{"url": _http_image_url(image), "width": 0, "height": 0, "rank": -1}]
            for img_index, img in enumerate(image_items):
                url = _http_image_url(img.get("url"))
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append({"provider": provider, "provider_id": row_id, "name": sname or target,
                                "url": url, "width": int(img.get("width") or 0), "height": int(img.get("height") or 0),
                                "rank": (0 if exact else 1 if alias_exact else 2, row_index, img_index)})
                if len(results) >= limit * 2:
                    break
    provider_results = {
        "stashdb": [x for x in results if str(x.get("provider") or "").strip().lower() == "stashdb"],
        "theporndb": [x for x in results if str(x.get("provider") or "").strip().lower() == "theporndb"],
    }
    start = offset
    end = start + limit
    return provider_results["stashdb"][start:end] + provider_results["theporndb"][start:end]

async def studio_image_from_providers(db, name: str, provider_ids: object = None, skip_sources: set[str] | None = None) -> tuple[str, str, str]:
    """Direct studio/site lookup across both configured image providers. StashDB is preferred, TPDB fills gaps."""
    target = str(name or "").strip()
    if not target:
        return "", "", ""
    ids = _provider_ids(provider_ids)
    stash_key = db.setting("stashdb_api_key")
    tpdb_key = db.setting("tpdb_api_token")
    candidates: list[tuple[int, str, str, str]] = []
    skipped = {str(x).casefold() for x in (skip_sources or set())}

    if stash_key:
        cache_key = (f"entity:studio:id:{ids['stashdb']}" if ids.get("stashdb") else f"entity:studio:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "stashdb", cache_key)
        try:
            if not hit:
                await _throttle("stashdb")
                if ids.get("stashdb"):
                    data = await asyncio.to_thread(_stashdb_graphql, db, stash_key, _STASHDB_STUDIO_BY_ID_QUERY, {"id": ids["stashdb"]})
                    node = ((data.get("data") or {}).get("findStudio") or {}) if isinstance(data, dict) else {}
                    cached = [node] if isinstance(node, dict) and node else []
                else:
                    data = await asyncio.to_thread(_stashdb_graphql, db, stash_key, _STASHDB_STUDIO_BY_NAME_QUERY, {"name": target})
                    node = ((data.get("data") or {}).get("findStudio") or {}) if isinstance(data, dict) else {}
                    cached = [node] if isinstance(node, dict) and node else []
                _cache_put(db, "stashdb", cache_key, cached)
            rows = cached or []
            image, _source, pid = _best_named_entity(rows, target, performer=False)
            if image and "stashdb" not in skipped:
                candidates.append((2, image, "stashdb", pid))
        except Exception:
            _swallowed_note("providers.studio_image_from_providers")
            pass

    if tpdb_key:
        tpdb_ident = ids.get("theporndb") or ids.get("tpdb")
        cache_key = (f"entity:studio:id:{tpdb_ident}" if tpdb_ident else f"entity:studio:name:{_norm_entity_name(target)}")
        cached, hit = _cache_get(db, "tpdb", cache_key)
        try:
            if not hit:
                await _throttle("tpdb")
                if ids.get("theporndb") or ids.get("tpdb"):
                    ident = ids.get("theporndb") or ids.get("tpdb")
                    cached = _provider_entity_rows(await asyncio.to_thread(_tpdb_get, db, tpdb_key, f"sites/{urllib.parse.quote(ident, safe='')}"), ("data", "site"))
                else:
                    q = urllib.parse.urlencode({"q": target})
                    cached = _provider_entity_rows(await asyncio.to_thread(_tpdb_get, db, tpdb_key, f"sites?{q}"), ("data", "sites", "results"))
                _cache_put(db, "tpdb", cache_key, cached)
            rows = cached or []
            image, _source, pid = _best_named_entity(rows, target, performer=False)
            if image and "theporndb" not in skipped and "tpdb" not in skipped:
                candidates.append((1, image, "theporndb", pid))
        except Exception:
            _swallowed_note("providers.studio_image_from_providers")
            pass

    if not candidates:
        return "", "", ""
    _rank, image, source, pid = max(candidates, key=lambda x: x[0])
    return image, source, pid




# ==============================================================================
# SECTION: Scene identification
# Candidate equivalence, search terms and identify.
# ==============================================================================

def _scene_site_host(candidate: dict) -> str:
    for key in ("site_url", "studio_url"):
        value = str(candidate.get(key) or "").strip()
        if not value:
            continue
        try:
            address = value if "://" in value else f"//{value}"
            host = str(urllib.parse.urlsplit(address).hostname or "").casefold().rstrip(".")
            if host.startswith("www."):
                host = host[4:]
            if host:
                return host
        except (TypeError, ValueError):
            continue
    return ""


def scene_sites_conflict(left: dict, right: dict) -> bool:
    """Return true when two candidate records explicitly name different sites."""
    left_host, right_host = _scene_site_host(left), _scene_site_host(right)
    if left_host and right_host:
        return left_host != right_host

    def site_name(candidate: dict) -> str:
        return _norm_entity_name(candidate.get("site") or candidate.get("studio"))

    left_name, right_name = site_name(left), site_name(right)
    return bool(left_name and right_name and left_name != right_name)


def _scene_candidate_equivalent(left: dict, right: dict) -> bool:
    """Conservatively identify a scene shared by the two provider catalogs."""
    from difflib import SequenceMatcher
    # Same content on different sites is a reviewable variant, not a merge.
    if scene_sites_conflict(left, right):
        return False
    def norm(value):
        return _norm_entity_name(value)
    lt, rt = norm(left.get("title")), norm(right.get("title"))
    if not lt or not rt:
        return False
    if lt != rt and SequenceMatcher(None, lt, rt).ratio() < 0.94:
        return False
    ld, rd = str(left.get("date") or "")[:10], str(right.get("date") or "")[:10]
    date_match = bool(ld and rd and ld == rd)
    if ld and rd and not date_match:
        try:
            from datetime import date as _date
            date_match = abs((_date.fromisoformat(ld) - _date.fromisoformat(rd)).days) <= 1
        except ValueError:
            pass
    ls, rs = norm(left.get("studio") or left.get("site")), norm(right.get("studio") or right.get("site"))
    studio_match = bool(ls and rs and ls == rs)
    lp = {norm(x) for x in (left.get("performers") or []) if norm(x)}
    rp = {norm(x) for x in (right.get("performers") or []) if norm(x)}
    performer_match = bool(lp and rp and len(lp & rp) / max(1, min(len(lp), len(rp))) >= 0.6)
    return sum((date_match, studio_match, performer_match)) >= 2


def _merge_search_candidates(candidates: list[dict]) -> list[dict]:
    """Deduplicate provider query results and merge independently corroborated scenes."""
    out: list[dict] = []
    exact_index: dict[tuple[str, str], int] = {}
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        source = str(candidate.get("source") or "unknown")
        ident = str(candidate.get("id") or "").strip()
        exact = (source, ident) if ident else None
        if exact and exact in exact_index:
            current = out[exact_index[exact]]
            current["_search_terms"] = list(dict.fromkeys(
                list(current.get("_search_terms") or []) + list(candidate.get("_search_terms") or [])
            ))
            continue
        duplicate_index = next((i for i, current in enumerate(out)
                                if source != str(current.get("source") or "unknown")
                                and _scene_candidate_equivalent(current, candidate)), None)
        if duplicate_index is not None:
            current = out[duplicate_index]
            if current.get("source") == "stashdb":
                merged = merge_provider_matches(current, candidate)
            elif candidate.get("source") == "stashdb":
                merged = merge_provider_matches(candidate, current)
            else:
                merged = merge_provider_matches(current, candidate)
            providers = list(current.get("cross_provider_candidates") or [])
            for item in (current, candidate):
                source_id = {"provider": str(item.get("source") or "unknown"), "id": str(item.get("id") or "")}
                if source_id not in providers:
                    providers.append(source_id)
            merged["cross_provider_candidates"] = providers
            merged["cross_provider_confirmed"] = True
            merged["_search_terms"] = list(dict.fromkeys(
                list(current.get("_search_terms") or []) + list(candidate.get("_search_terms") or [])
            ))
            out[duplicate_index] = merged
            if exact:
                exact_index[exact] = duplicate_index
            continue
        if exact:
            exact_index[exact] = len(out)
        out.append(candidate)
    return out


def _scene_search_terms(filename: str, context: dict | None = None,
                       parameters: dict | None = None) -> tuple[list[str], list[str], list[str]]:
    """Build bounded search phrases from all available filename parameters.

    The most informative combinations are sent first (title + performer(s) +
    studio + date/year), followed by smaller combinations for providers whose
    search endpoints do not parse compound phrases well. Entity clues come from
    exact local names/aliases or explicit filename labels, not fuzzy guesses.
    """
    from .parser import is_generic_scene_code, parse_scene_name
    hints = parameters if isinstance(parameters, dict) else parse_scene_name(filename)
    context = context if isinstance(context, dict) else {}
    quick_lookup = bool(context.get("_quick_lookup"))

    def values(raw):
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple, set)):
            return []
        out = []
        seen = set()
        for item in raw:
            value = " ".join(str(item or "").split()).strip(" .-_[]()")
            key = _norm_entity_name(value)
            if len(value) >= 2 and key and key not in seen:
                out.append(value[:140])
                seen.add(key)
        return out

    filename_leaf = str(filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    context_title = str(context.get("title") or "").strip()
    filename_title_variants = values(hints.get("title_search_variants"))
    if quick_lookup:
        # User-edited title is first when genuinely changed. The route replaces
        # the untouched raw filename with a parsed title before setting context.
        titles = values([context_title] + values(context.get("titles")) + filename_title_variants + [hints.get("title")])
    else:
        titles = values(filename_title_variants + [hints.get("title"), context_title] + values(context.get("titles")))
    filename_key = _norm_entity_name(filename_leaf.rsplit(".", 1)[0])
    titles = [title for title in titles if _norm_entity_name(title.rsplit(".", 1)[0]) != filename_key]

    if quick_lookup:
        if context.get("_quick_lookup_user_studio"):
            studios = values([*values(context.get("studios")), context.get("studio"),
                              hints.get("studio"), *values(hints.get("studio_candidates")),
                              *values(context.get("studio_aliases"))])
        else:
            studios = values([hints.get("studio"), *values(hints.get("studio_candidates")),
                              *values(context.get("studios")), context.get("studio"),
                              *values(context.get("studio_aliases"))])
        if context.get("_quick_lookup_user_performer"):
            performers = values([*values(context.get("performers")), *values(hints.get("performer_candidates")),
                                 *values(hints.get("performers")), *values(context.get("performer_names")),
                                 *values(context.get("performer_aliases"))])
        else:
            performers = values([*values(hints.get("performer_candidates")), *values(hints.get("performers")),
                                 *values(context.get("performers")), *values(context.get("performer_names")),
                                 *values(context.get("performer_aliases"))])
    else:
        studios = values([hints.get("studio"), *values(hints.get("studio_candidates")),
                          *values(context.get("studios")), context.get("studio"),
                          *values(context.get("studio_aliases"))])
        performers = values([*values(hints.get("performers")), *values(hints.get("performer_candidates")),
                             *values(context.get("performers")), *values(context.get("performer_names")),
                             *values(context.get("performer_aliases"))])
    raw_code = str(hints.get("code") or context.get("code") or "").strip()
    code = "" if is_generic_scene_code(raw_code) else raw_code
    raw_dates = values([hints.get("date"), context.get("date"), context.get("production_date"),
                        *values(context.get("dates"))])
    dates = list(dict.fromkeys(d[:10] for d in raw_dates
                               if re.fullmatch(r"(?:19|20)\d{2}-\d{2}-\d{2}", d[:10])))
    year = str(hints.get("year") or context.get("release_date") or context.get("year") or "").strip()
    year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", year)
    year = year_match.group(1) if year_match else ""
    date_or_year = dates[0] if dates else year

    terms: list[str] = []
    def add(value):
        term = " ".join(str(value or "").split()).strip(" .-_[]()")
        key = _norm_entity_name(term)
        if len(term) >= 2 and key and all(_norm_entity_name(x) != key for x in terms):
            terms.append(term[:180])

    # Combinations with more independent evidence have priority. This makes
    # providers compare all available fields together rather than seeing only
    # one isolated piece of the filename.
    for title in titles[:2]:
        add(" ".join([*studios[:1], *performers[:2], title, date_or_year]).strip())
        add(" ".join([*performers[:2], title, studios[0] if studios else "", date_or_year]).strip())
        add(" ".join([studios[0] if studios else "", title, date_or_year]).strip())
        add(" ".join([*performers[:2], title]).strip())
        add(title)
    if code:
        add(code)
    if studios and performers and not titles:
        add(" ".join([studios[0], *performers[:2], date_or_year]).strip())
    if studios:
        add(" ".join([studios[0], *performers[:2], date_or_year]).strip())
        add(studios[0])
    for performer in performers[:3]:
        add(" ".join([performer, date_or_year]).strip())
        add(performer)
    if dates:
        add(dates[0])
    elif year:
        add(year)
    if not terms:
        add(filename_leaf)

    tpdb_terms: list[str] = []
    def add_tp(value):
        term = " ".join(str(value or "").split()).strip(" .-_[]()")
        key = _norm_entity_name(term)
        if len(term) >= 2 and key and all(_norm_entity_name(x) != key for x in tpdb_terms):
            tpdb_terms.append(term[:180])

    # ThePornDB's parser still receives the raw filename, but use focused
    # multi-clue variants ahead of title-only variants when we know identities.
    if titles:
        add_tp(" ".join([*studios[:1], *performers[:2], titles[0], date_or_year]).strip())
        add_tp(" ".join([*performers[:2], titles[0], studios[0] if studios else ""]).strip())
        add_tp(" ".join([studios[0] if studios else "", titles[0], date_or_year]).strip())
        add_tp(titles[0])
    if not tpdb_terms or len(tpdb_terms) < 3:
        add_tp(filename_leaf)
    if len(tpdb_terms) < 3 and studios:
        add_tp(studios[0])
    if len(tpdb_terms) < 3 and performers:
        add_tp(performers[0])
    if dates and len(tpdb_terms) < 3:
        add_tp(dates[0])
    elif year and len(tpdb_terms) < 3:
        add_tp(year)
    return terms[:8], tpdb_terms[:3], dates[:2]

def _scene_year_hint(filename: str, context: dict | None = None, dates: list[str] | None = None) -> str | None:
    """Return a scene-year clue from release/production metadata, never upload time."""
    from .parser import parse_scene_name
    context = context if isinstance(context, dict) else {}
    hints = parse_scene_name(filename)
    sources = [hints.get("date"), hints.get("year"), *(dates or []),
               context.get("date"), context.get("production_date"), context.get("release_date"),
               context.get("year")]
    raw_context_dates = context.get("dates")
    if isinstance(raw_context_dates, (list, tuple, set)):
        sources.extend(raw_context_dates)
    raw_context_years = context.get("years")
    if isinstance(raw_context_years, (list, tuple, set)):
        sources.extend(raw_context_years)
    for source in sources:
        match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(source or ""))
        if match:
            return match.group(1)
    return None


async def identify(db, *, oshash: Optional[str], filename: str, context: dict | None = None,
                   hash_only: bool = False, filename_parameters: dict | None = None,
                   visual_phash: str | None = None) -> dict:
    """Identify a scene with ordered provider fallback instead of always fanning out.

    StashDB is the primary provider. ThePornDB is queried only when StashDB
    has no usable scene result, or when one exact/unique primary result is
    missing important fields and needs enrichment. Requests are sequential, not
    parallel. Weak, conflicting or performer-ambiguous results remain reviewable
    suggestions in the matching pipeline.
    """
    stash_key = db.setting("stashdb_api_key")
    tpdb_key = db.setting("tpdb_api_token")
    errors: list[str] = []
    provider_searches = {"stashdb": False, "theporndb": False}
    if not stash_key and not tpdb_key:
        return {"matches": [], "method": None,
                "errors": ["No StashDB API key or ThePornDB token saved — add one in Metadata settings."],
                "provider_searches": provider_searches}

    from .filename_matching import extract_filename_parameters
    context = context if isinstance(context, dict) else {}
    quick_lookup = bool(context.get("_quick_lookup"))
    hints = filename_parameters if isinstance(filename_parameters, dict) else extract_filename_parameters(db, filename, context)
    terms, tpdb_terms, dates = _scene_search_terms(filename, context, parameters=hints)
    year_hint = _scene_year_hint(filename, context, dates)
    if not year_hint and hints.get("year"):
        year_hint = str(hints["year"])

    async def stash_hash() -> list[dict]:
        if not (stash_key and oshash):
            return []
        provider_searches["stashdb"] = True
        try:
            found = await stashdb_by_oshash(db, stash_key, oshash)
            for item in found:
                item["confidence"] = "exact file match (OSHASH)"
                item.setdefault("_search_terms", []).append("oshash")
            return found
        except Exception as exc:
            errors.append(f"StashDB OSHASH: {exc}")
            return []

    async def tpdb_hash() -> list[dict]:
        if not (tpdb_key and oshash):
            return []
        provider_searches["theporndb"] = True
        try:
            found = await tpdb_by_hash(db, tpdb_key, oshash)
            for item in found:
                item["confidence"] = "exact file match (OSHASH)"
                item.setdefault("_search_terms", []).append("oshash")
            return found
        except Exception as exc:
            errors.append(f"ThePornDB OSHASH: {exc}")
            return []

    async def search_stash_clues() -> list[dict]:
        """Search StashDB using filename clues and merge the candidates."""
        if not stash_key:
            return []
        found_all: list[dict] = []
        date_results: list[dict] = []
        if quick_lookup:
            # Interactive lookup should return something useful quickly: query
            # the two highest-information combinations concurrently, alongside
            # the exact date (or year) when available. Normal channel scans keep
            # the broader, conservative clue sweep below.
            jobs = []
            labels = []
            for term in terms[:2]:
                labels.append(("term", term))
                jobs.append(stashdb_search_scenes(db, stash_key, term, limit=8))
            if dates[:1]:
                labels.append(("date", dates[0]))
                jobs.append(stashdb_scenes_by_date(db, stash_key, dates[0], limit=12))
            elif year_hint:
                labels.append(("year", year_hint))
                jobs.append(stashdb_scenes_by_year(db, stash_key, year_hint, limit=12))
            if jobs:
                provider_searches["stashdb"] = True
                results = await asyncio.gather(*jobs, return_exceptions=True)
                for (kind, value), result in zip(labels, results):
                    if isinstance(result, Exception):
                        errors.append(f"StashDB {kind} search ({value}): {result}")
                        continue
                    if kind == "date":
                        date_results = list(result or [])
                    found_all.extend(result or [])
            return _merge_search_candidates(found_all)

        for term in terms[:5]:
            provider_searches["stashdb"] = True
            try:
                found_all.extend(await stashdb_search_scenes(db, stash_key, term, limit=10))
            except Exception as exc:
                errors.append(f"StashDB scene search ({term}): {exc}")
        for date in dates[:1]:
            provider_searches["stashdb"] = True
            try:
                date_results = await stashdb_scenes_by_date(db, stash_key, date, limit=20)
                found_all.extend(date_results)
            except Exception as exc:
                errors.append(f"StashDB date search ({date}): {exc}")
        if year_hint and (not dates or not date_results):
            provider_searches["stashdb"] = True
            try:
                found_all.extend(await stashdb_scenes_by_year(db, stash_key, year_hint, limit=20))
            except Exception as exc:
                errors.append(f"StashDB year search ({year_hint}): {exc}")
        return _merge_search_candidates(found_all)

    async def search_tpdb_clues() -> list[dict]:
        if not tpdb_key:
            return []

        async def search_one(term: str) -> list[dict]:
            import inspect
            params = inspect.signature(tpdb_parse).parameters
            if year_hint and ("year" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())):
                found = await tpdb_parse(db, tpdb_key, term, year=year_hint)
            else:
                found = await tpdb_parse(db, tpdb_key, term)
            for item in found:
                item.setdefault("_search_terms", []).append(term)
            return list(found or [])

        found_all: list[dict] = []
        selected_terms = tpdb_terms[:2] if quick_lookup else tpdb_terms[:3]
        if quick_lookup:
            provider_searches["theporndb"] = True
            results = await asyncio.gather(*(search_one(term) for term in selected_terms), return_exceptions=True)
            for term, result in zip(selected_terms, results):
                if isinstance(result, Exception):
                    errors.append(f"ThePornDB filename search ({term}): {result}")
                else:
                    found_all.extend(result or [])
        else:
            for term in selected_terms:
                provider_searches["theporndb"] = True
                try:
                    found_all.extend(await search_one(term))
                except Exception as exc:
                    errors.append(f"ThePornDB filename search ({term}): {exc}")
        return _merge_search_candidates(found_all)

    # Exact file identity is strongest. Ask StashDB first, then use ThePornDB
    # only if StashDB has no exact result or the single result lacks core data.
    if oshash and stash_key:
        stash_matches = _merge_search_candidates(await stash_hash())
        if stash_matches:
            method = "stashdb oshash"
            # Exact StashDB identity is authoritative. If its record is sparse,
            # try the same exact fingerprint in ThePornDB, then query StashDB's
            # richer clue surface, and only after that use ThePornDB clue search
            # to fill fields missing from the primary result.
            # A unique, complete primary hash match is enough. Ambiguous or sparse
            # hash results still get an exact ThePornDB hash lookup, even during
            # hash-only preflight, so provider priority does not suppress useful
            # disambiguation. Hash-only mode still never fans out into filename
            # clue searches.
            if hash_only and len(stash_matches) == 1 and not _needs_fallback(stash_matches[0]):
                return {"matches": stash_matches, "method": method, "errors": errors,
                        "provider_searches": provider_searches, "searched_clues": [],
                        "searched_dates": [], "searched_year": year_hint}

            if len(stash_matches) != 1 or any(_needs_fallback(item) for item in stash_matches):
                fallback_hash_matches = await tpdb_hash() if tpdb_key else []
                if len(stash_matches) == 1 and len(fallback_hash_matches) == 1:
                    if scene_sites_conflict(stash_matches[0], fallback_hash_matches[0]):
                        # The same file can be listed under separate websites.
                        # Keep both provider records intact for operator review.
                        stash_matches = _merge_search_candidates(stash_matches + fallback_hash_matches)
                        method = "cross-provider oshash"
                    else:
                        stash_matches = [merge_provider_matches(stash_matches[0], fallback_hash_matches[0])]
                elif fallback_hash_matches:
                    # Multiple exact-fingerprint scenes remain reviewable; do not
                    # pick an arbitrary cast/studio from the fallback provider.
                    stash_matches = _merge_search_candidates(stash_matches + fallback_hash_matches)
                    method = "cross-provider oshash"

                if hash_only:
                    return {"matches": stash_matches, "method": method, "errors": errors,
                            "provider_searches": provider_searches, "searched_clues": [],
                            "searched_dates": [], "searched_year": year_hint}

                # A fingerprint endpoint can expose a deliberately small record.
                # Re-query StashDB by its normal scene clues first and merge only
                # the same StashDB scene ID, never a similarly named scene.
                if len(stash_matches) != 1 or any(_needs_fallback(item) for item in stash_matches):
                    fuller_stash = await search_stash_clues()
                    if fuller_stash:
                        for index, primary in enumerate(stash_matches):
                            if str(primary.get("source") or "").casefold() != "stashdb":
                                continue
                            primary_id = str(primary.get("id") or "")
                            same_scene = [candidate for candidate in fuller_stash
                                          if str(candidate.get("source") or "").casefold() == "stashdb"
                                          and primary_id and str(candidate.get("id") or "") == primary_id]
                            if len(same_scene) == 1:
                                stash_matches[index] = merge_provider_matches(primary, same_scene[0])

                # Only then ask ThePornDB for missing fields. Cross-provider clue
                # results are merged only when scene identity is corroborated;
                # unmatched alternatives are not allowed to overwrite an exact
                # fingerprint result.
                if tpdb_key and (len(stash_matches) != 1 or any(_needs_fallback(item) for item in stash_matches)):
                    fallback_clues = await search_tpdb_clues()
                    if fallback_clues:
                        for index, primary in enumerate(stash_matches):
                            if str(primary.get("source") or "").casefold() != "stashdb":
                                continue
                            equivalent = [candidate for candidate in fallback_clues
                                          if _scene_candidate_equivalent(primary, candidate)]
                            if len(equivalent) == 1:
                                merged = merge_provider_matches(primary, equivalent[0])
                                merged["cross_provider_confirmed"] = True
                                merged["cross_provider_candidates"] = [
                                    {"provider": "stashdb", "id": str(primary.get("id") or "")},
                                    {"provider": "theporndb", "id": str(equivalent[0].get("id") or "")},
                                ]
                                stash_matches[index] = merged
            return {"matches": stash_matches, "method": method, "errors": errors,
                    "provider_searches": provider_searches, "searched_clues": [],
                    "searched_dates": [], "searched_year": year_hint}

    if oshash and tpdb_key and not stash_key:
        tpdb_matches = _merge_search_candidates(await tpdb_hash())
        if tpdb_matches:
            return {"matches": tpdb_matches, "method": "theporndb oshash", "errors": errors,
                    "provider_searches": provider_searches, "searched_clues": [],
                    "searched_dates": [], "searched_year": year_hint}
    elif oshash and tpdb_key and stash_key and not provider_searches["theporndb"]:
        # A primary exact hash miss is the first condition that permits the
        # fallback hash provider to run.
        tpdb_matches = _merge_search_candidates(await tpdb_hash())
        if tpdb_matches:
            return {"matches": tpdb_matches, "method": "theporndb oshash", "errors": errors,
                    "provider_searches": provider_searches, "searched_clues": [],
                    "searched_dates": [], "searched_year": year_hint}

    # Content Automation can start filename-based matching while a torrent is
    # downloading. Once the bytes are available, this mode checks the exact
    # OSHASH first and deliberately avoids repeating the same filename clue
    # searches; the persisted preflight result is used only when no hash match
    # exists. With hash_only=False the normal ordered clue fallback is unchanged.
    # Visual-fingerprint fallback is deliberately after OSHASH and before filename
    # searches. The caller supplies a hash only from the explicit metadata scan;
    # page loads, quick edits and playback never trigger it.
    clean_phash = re.sub(r"[^0-9a-f]", "", str(visual_phash or "").casefold())
    if clean_phash and not hash_only:
        phash_candidates: list[dict] = []
        if stash_key:
            provider_searches["stashdb"] = True
            try:
                phash_candidates.extend(await stashdb_by_phash(db, stash_key, clean_phash))
            except Exception as exc:
                errors.append(f"StashDB PHASH: {exc}")
        if tpdb_key:
            provider_searches["theporndb"] = True
            try:
                phash_candidates.extend(await tpdb_by_phash(db, tpdb_key, clean_phash))
            except Exception as exc:
                errors.append(f"ThePornDB PHASH: {exc}")
        if phash_candidates:
            phash_candidates = _merge_search_candidates(phash_candidates)
            for candidate in phash_candidates:
                candidate["_search_terms"] = list(dict.fromkeys(list(candidate.get("_search_terms") or []) + ["phash"]))
            return {"matches": phash_candidates, "method": "cross-provider phash" if any(x.get("cross_provider_confirmed") for x in phash_candidates) else "provider phash",
                    "errors": errors, "searched_clues": [], "searched_dates": [], "searched_year": year_hint,
                    "provider_searches": provider_searches, "visual_phash": clean_phash}

    if hash_only:
        attempted = any(provider_searches.values())
        return {"matches": [], "method": "oshash lookup" if attempted else None,
                "errors": errors, "searched_clues": [], "searched_dates": [],
                "searched_year": year_hint, "provider_searches": provider_searches,
                "attempted": attempted}

    # Try all bounded clues against the primary provider. Do not call the
    # fallback just to corroborate a complete primary result. If the primary
    # has a unique scene but misses core fields/cast, query ThePornDB for that
    # missing information and merge only when the candidates are equivalent.
    stash_matches = await search_stash_clues() if stash_key else []
    if stash_matches:
        fallback_used_for_enrichment = False
        # A partial candidate set is still useful, but if any primary scene lacks
        # important details we should give ThePornDB a chance to fill them. This
        # applies to multi-candidate StashDB results as well; equivalent scenes
        # merge, while conflicting alternatives remain available for review.
        if tpdb_key and (len(stash_matches) != 1 or any(_needs_fallback(candidate) for candidate in stash_matches)):
            fallback_matches = await search_tpdb_clues()
            fallback_used_for_enrichment = bool(provider_searches["theporndb"])
            if fallback_matches:
                stash_matches = _merge_search_candidates(stash_matches + fallback_matches)
        return {"matches": stash_matches,
                "method": "cross-provider fallback clue search" if fallback_used_for_enrichment else "stashdb clue search",
                "errors": errors, "searched_clues": terms, "searched_dates": dates,
                "searched_year": year_hint, "provider_searches": provider_searches}

    # Primary returned no scene data (or errored). Only now query the fallback.
    tpdb_matches = await search_tpdb_clues() if tpdb_key else []
    if tpdb_matches:
        return {"matches": tpdb_matches, "method": "tpdb filename parse", "errors": errors,
                "searched_clues": terms, "searched_dates": dates, "searched_year": year_hint,
                "provider_searches": provider_searches}

    attempted = any(provider_searches.values())
    return {"matches": [], "method": "stashdb clue search" if provider_searches["stashdb"] else (
                "tpdb filename parse" if provider_searches["theporndb"] else None),
            "errors": errors if errors else [], "searched_clues": terms, "searched_dates": dates,
            "searched_year": year_hint, "provider_searches": provider_searches,
            "attempted": attempted}

