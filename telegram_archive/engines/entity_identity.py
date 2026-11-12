"""Canonical identity and safe deduplication for performers and studios/sites.

Names are a lookup signal, not proof of identity. Provider IDs, existing
canonical IDs and known aliases take precedence over normalized names. The
normalization intentionally removes only formatting differences such as case,
spacing and punctuation/separators; it does not fuzzy-match arbitrary names.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Name normalization
#   - Provider and alias merging
#   - Finding existing entities
#   - Upsert
#   - Identity assessment
#   - Public upserts and merges
# ------------------------------------------------------------------
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional


_SEP_RE = re.compile(r"[\W_]+", re.UNICODE)
_MULTI_UNDER_RE = re.compile(r"_+")




# ==============================================================================
# SECTION: Name normalization
# Display names, normalized names and canonical keys.
# ==============================================================================

def _clean_gender(value) -> str:
    """Providers say 'Female', 'transgender_female', 'Trans Woman'...; the app keeps female, male or trans."""
    text = str(value or '').strip().lower()
    if 'trans' in text:
        return 'trans'
    return text if text in {'male', 'female'} else ''


def display_name(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def normalized_name(value: str) -> str:
    """Case/diacritic/whitespace normalized search value."""
    text = unicodedata.normalize("NFKD", display_name(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def canonical_key(value: str) -> str:
    """Stable key for harmless formatting differences.

    Separator characters and whitespace are ignored so e.g. ``Sarah Vandella``,
    ``SarahVandella`` and ``SARAH-VANDELLA`` resolve to the same key.
    """
    text = unicodedata.normalize("NFKD", display_name(value)).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Keep unicode letters/numbers but intentionally drop separators and
    # punctuation. This is a comparison key, not the display value.
    text = _SEP_RE.sub("", text)
    text = _MULTI_UNDER_RE.sub("", text)
    return text




# ==============================================================================
# SECTION: Provider and alias merging
# JSON maps, provider IDs and alias merges.
# ==============================================================================

def _load_json(value, default):
    if not value:
        return default
    try:
        out = json.loads(value)
        return out if isinstance(out, type(default)) else default
    except Exception:
        return default


def _provider_id_values(value) -> list[str]:
    """Normalize one or many IDs associated with a provider."""
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    out = []
    for item in raw:
        text = str(item or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def _merge_json_maps(left: str | None, right: str | None) -> str:
    """Merge provider identities without discarding confirmed alternate IDs.

    Ordinary upserts still keep a single ID for a provider. When a user has
    explicitly merged two entity rows, however, the same real person can have
    multiple IDs from one provider; keep all of them so later imports resolve
    back to the chosen canonical performer.
    """
    a = _load_json(left, {})
    b = _load_json(right, {})
    out = {}
    for key in dict.fromkeys([*a.keys(), *b.keys()]):
        values = _provider_id_values(a.get(key)) + _provider_id_values(b.get(key))
        values = list(dict.fromkeys(values))
        if len(values) == 1:
            out[str(key)] = values[0]
        elif values:
            out[str(key)] = values
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


def _merge_aliases(existing: str | None, new_name: str | None) -> str:
    values = _load_json(existing, [])
    seen = {display_name(v).casefold() for v in values if display_name(v)}
    value = display_name(new_name)
    if value and value.casefold() not in seen:
        values.append(value)
    return json.dumps(values, ensure_ascii=False)




# ==============================================================================
# SECTION: Finding existing entities
# Provider matching and name or key conflicts.
# ==============================================================================

def _provider_match(db, table: str, provider: str, provider_id: Optional[str]) -> Optional[dict]:
    if not provider_id:
        return None
    pid = str(provider_id)
    provider_key = str(provider or "").strip().casefold()
    rows = db.conn.execute(f"SELECT * FROM {table} WHERE provider_ids IS NOT NULL AND provider_ids<>''").fetchall()
    for row in rows:
        payload = _load_json(row["provider_ids"], {})
        namespaced_values = []
        for key, raw_value in payload.items():
            if str(key or "").strip().casefold() == provider_key:
                namespaced_values.extend(_provider_id_values(raw_value))
        if provider_key and pid in namespaced_values:
            return dict(row)
        # Legacy rows may have stored only the provider ID value. Keep the
        # fallback for compatibility, but never use it when a provider namespace
        # is explicitly available because numeric IDs can overlap across sources.
        if not provider_key and any(pid in _provider_id_values(v) for v in payload.values()):
            return dict(row)
    return None


def _conflicting_provider_id(row: dict, provider: str, provider_id) -> bool:
    """True when the row is already a DIFFERENT entity at the same provider:
    same name, but the provider gave it another ID (two people called "Anna").
    V29.4.133: such a name match used to merge them into one performer."""
    if not provider or provider_id is None or str(provider_id) == "":
        return False
    payload = _load_json(row.get("provider_ids"), {})
    values = []
    namespace = str(provider or "").strip().casefold()
    for key, raw_value in payload.items():
        if str(key or "").strip().casefold() == namespace:
            values.extend(_provider_id_values(raw_value))
    return bool(values) and str(provider_id) not in values


def _find_existing(db, table: str, name: str, provider: str, provider_id: Optional[str]) -> Optional[dict]:
    """Resolve provider ID, canonical name, normalized name, then a unique alias.

    Alias matches are only accepted when exactly one non-conflicting entity owns
    the alias. This prevents re-imports under a former/provider-specific name from
    creating a copy while preserving distinct same-name people with conflicting
    IDs in the same provider namespace.
    """
    by_provider = _provider_match(db, table, provider, provider_id)
    if by_provider:
        return by_provider
    key = canonical_key(name)
    if key:
        rows = db.conn.execute(
            f"SELECT * FROM {table} WHERE canonical_key=? ORDER BY id", (key,)
        ).fetchall()
        eligible = [dict(row) for row in rows if not _conflicting_provider_id(dict(row), provider, provider_id)]
        if len(eligible) == 1:
            return eligible[0]
    n = normalized_name(name)
    if n:
        rows = db.conn.execute(f"SELECT * FROM {table} WHERE normalized_name=? ORDER BY id", (n,)).fetchall()
        eligible = [dict(row) for row in rows if not _conflicting_provider_id(dict(row), provider, provider_id)]
        if len(eligible) == 1:
            return eligible[0]
    # Legacy records created before canonical/normalized fields were backfilled
    # still have a UNIQUE display name. Recognize that exact name once, rather
    # than creating a duplicate (notably when Quick Edit enriches a selected
    # local performer with a newly-known provider ID).
    rows = db.conn.execute(f"SELECT * FROM {table} WHERE name=? COLLATE NOCASE ORDER BY id", (name,)).fetchall()
    eligible = [dict(row) for row in rows if not _conflicting_provider_id(dict(row), provider, provider_id)]
    if len(eligible) == 1:
        return eligible[0]
    if key:
        alias_matches = []
        for row in db.conn.execute(f"SELECT * FROM {table} WHERE aliases IS NOT NULL AND aliases<>'' ORDER BY id").fetchall():
            item = dict(row)
            if _conflicting_provider_id(item, provider, provider_id):
                continue
            aliases = _load_json(item.get("aliases"), [])
            if any(canonical_key(alias) == key for alias in aliases if isinstance(alias, str) and display_name(alias)):
                alias_matches.append(item)
        if len(alias_matches) == 1:
            return alias_matches[0]
    return None


def _name_taken(db, table: str, name: str, own_id: int | None = None) -> bool:
    row = db.conn.execute(f"SELECT id FROM {table} WHERE name=? LIMIT 1", (name,)).fetchone()
    return bool(row) and (own_id is None or int(row["id"]) != int(own_id))


def _free_name(db, table: str, name: str) -> str:
    """The name column is UNIQUE: a second, different person with the same
    name is shown as "Anna (2)" (the plain name is kept as an alias)."""
    if not _name_taken(db, table, name):
        return name
    n = 2
    while _name_taken(db, table, f"{name} ({n})"):
        n += 1
    return f"{name} ({n})"


def _free_key(db, table: str, key: str, provider: str, provider_id, own_id: int | None = None) -> str:
    """A canonical key no other row owns (the column is UNIQUE). A second
    entity with the same name gets the provider ID appended to its key; its
    display name is unchanged."""
    if not key:
        return key
    clash = db.conn.execute(f"SELECT id FROM {table} WHERE canonical_key=? LIMIT 1", (key,)).fetchone()
    if not clash or (own_id is not None and int(clash["id"]) == int(own_id)):
        return key
    alt = f"{key}#{provider or 'local'}:{provider_id}" if provider_id not in (None, "") else ""
    if alt and not db.conn.execute(f"SELECT 1 FROM {table} WHERE canonical_key=? AND id<>?", (alt, int(own_id or -1))).fetchone():
        return alt
    return ""




# ==============================================================================
# SECTION: Upsert
# Generic entity upsert.
# ==============================================================================

def upsert_entity(db, table: str, name: str, *, provider: str = "",
                  provider_id: Optional[str] = None, image: Optional[str] = None,
                  profile: Optional[str] = None, bio: Optional[str] = None, gender: Optional[str] = None) -> Optional[int]:
    """Resolve/create one canonical entity and return its integer ID."""
    clean = display_name(name)
    if not clean:
        return None
    with db.lock:
        row = _find_existing(db, table, clean, provider, provider_id)
        key = canonical_key(clean)
        norm = normalized_name(clean)
        provider_map = {str(provider): str(provider_id)} if provider and provider_id is not None else {}
        if row:
            pid = int(row["id"])
            merged_ids = _merge_json_maps(row.get("provider_ids"), json.dumps(provider_map))
            # Preserve both the pre-update display name and the incoming spelling.
            # Future imports under either spelling then resolve to this entity.
            aliases = _merge_aliases(row.get("aliases"), str(row.get("name") or ""))
            aliases = _merge_aliases(aliases, clean)
            # V29.4.133: never take a key another row owns (UNIQUE index):
            # a provider rename to an existing name used to fail the update.
            own_key = str(row.get("canonical_key") or "")
            new_key = _free_key(db, table, key, provider, provider_id, own_id=pid) or own_key
            fields = ["normalized_name=?", "canonical_key=?", "aliases=?", "provider_ids=?"]
            vals = [norm, new_key, aliases, merged_ids]
            image_locked = bool(row.get("image_locked", 0))
            name_locked = bool(row.get("name_locked", 0))
            profile_locked = bool(row.get("profile_locked", 0)) if table == "performers" else False
            if image and not image_locked:
                fields.append("image=COALESCE(NULLIF(image,''),?)")
                vals.append(image)
            if clean and not name_locked and not _name_taken(db, table, clean, own_id=pid):
                # V29.4.133: a rename never takes another row's name (UNIQUE).
                fields.append("name=?")
                vals.append(clean)
            if profile and table == "performers" and not profile_locked:
                fields.append("profile=COALESCE(NULLIF(profile,''),?)")
                vals.append(profile)
            if bio and table == "performers":
                fields.append("bio=COALESCE(NULLIF(bio,''),?)")
                vals.append(bio)
            if gender and table == "performers" and _clean_gender(gender):
                fields.append("gender=COALESCE(NULLIF(gender,''),?)")
                vals.append(_clean_gender(gender))
            vals.append(pid)
            db.conn.execute(f"UPDATE {table} SET {', '.join(fields)} WHERE id=?", vals)
            return pid

        shown = _free_name(db, table, clean)
        columns = ["name", "canonical_key", "normalized_name", "aliases", "provider_ids"]
        values = [shown, _free_key(db, table, key, provider, provider_id), norm,
                  json.dumps([clean] if shown != clean else []), json.dumps(provider_map)]
        if table == "performers":
            columns += ["image", "profile", "bio", "gender"]
            values += [image, profile, bio, (str(gender).strip().lower() if gender and str(gender).strip().lower() in {"male", "female"} else None)]
        else:
            columns += ["image"]
            values += [image]
        cur = db.conn.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in values)})",
            values,
        )
        return int(cur.lastrowid)





# ==============================================================================
# SECTION: Identity assessment
# Performer identity decisions and conflict checks.
# ==============================================================================

def assess_performer_identity(db, name: str, *, provider: str = "", provider_id=None) -> dict:
    """Resolve exact performer identity before considering possible duplicates.

    Namespace-qualified provider IDs and a unique canonical name/alias beat
    similar spelling. Near-name records are audit evidence only. Blocking is
    reserved for duplicate exact canonical names, a non-unique alias identity,
    or a provider-ID conflict in the same namespace.
    """
    clean = display_name(name)
    if not clean:
        return {"status": "invalid", "id": None, "candidates": []}
    provider = str(provider or "").strip().casefold()
    with db.lock:
        exact = _provider_match(db, "performers", provider, provider_id)
        if exact:
            return {"status": "existing", "id": int(exact["id"]), "candidates": [
                {"id": int(exact["id"]), "name": str(exact.get("name") or "")}
            ], "matched_by": "provider_id", "possible_duplicate": False}

        key = canonical_key(clean)
        norm = normalized_name(clean)
        rows = [dict(raw) for raw in db.conn.execute("SELECT * FROM performers ORDER BY id").fetchall()]
        canonical_matches: dict[int, dict] = {}
        alias_matches: dict[int, dict] = {}
        for row in rows:
            rid = int(row["id"])
            row_name_key = canonical_key(str(row.get("name") or ""))
            row_canonical = str(row.get("canonical_key") or "")
            row_norm = str(row.get("normalized_name") or "")
            # Derive from display name as well because legacy imports may not
            # have backfilled canonical_key/normalized_name yet.
            if ((key and (row_name_key == key or row_canonical == key))
                    or (norm and row_norm == norm)):
                canonical_matches[rid] = row
                continue
            alias_values = _load_json(row.get("aliases"), [])
            if key and any(isinstance(alias, str) and canonical_key(alias) == key for alias in alias_values):
                alias_matches[rid] = row

        # A literal canonical name beats a row that merely lists it as an alias.
        matched = list(canonical_matches.values()) if canonical_matches else list(alias_matches.values())
        match_method = "exact_name" if canonical_matches else "unique_alias"

        matched_ids = {int(row["id"]) for row in matched}
        near = []
        if len(key) >= 7:
            for row in rows:
                if int(row["id"]) in matched_ids:
                    continue
                variants = [str(row.get("name") or "")]
                variants.extend(x for x in _load_json(row.get("aliases"), []) if isinstance(x, str))
                best_ratio, best_variant = 0.0, ""
                for variant in variants:
                    variant_key = canonical_key(variant)
                    if len(variant_key) < 7 or abs(len(key)-len(variant_key)) > max(2, int(max(len(key),len(variant_key))*0.12)):
                        continue
                    ratio = SequenceMatcher(None, key, variant_key).ratio()
                    if ratio > best_ratio:
                        best_ratio, best_variant = ratio, variant
                if best_ratio >= 0.80:
                    near.append((best_ratio, row, best_variant))
        near.sort(key=lambda item: (-item[0], int(item[1]["id"])))
        possible_duplicates = [
            {"id": int(row["id"]), "name": str(row.get("name") or ""),
             "similarity": round(ratio, 3), "matched_variant": variant}
            for ratio, row, variant in near[:8]
        ]

        if len(matched) == 1:
            row = matched[0]
            if _conflicting_provider_id(row, provider, provider_id):
                return {"status": "ambiguous", "id": None, "candidates": [
                    {"id": int(row["id"]), "name": str(row.get("name") or ""),
                     "provider_ids": _load_json(row.get("provider_ids"), {})}
                ], "reason": "An exact performer name/alias exists, but its ID conflicts within the same provider namespace.",
                "possible_duplicates": possible_duplicates}
            return {"status": "existing", "id": int(row["id"]), "candidates": [
                {"id": int(row["id"]), "name": str(row.get("name") or "")}
            ], "matched_by": match_method, "possible_duplicates": possible_duplicates}
        if len(matched) > 1:
            return {"status": "ambiguous", "id": None,
                    "candidates": [
                        {"id": int(row["id"]), "name": str(row.get("name") or ""),
                         "provider_ids": _load_json(row.get("provider_ids"), {})}
                        for row in matched
                    ],
                    "reason": "Multiple existing records resolve to the same exact canonical name or alias.",
                    "possible_duplicates": possible_duplicates}
        return {"status": "new", "id": None, "candidates": [],
                "possible_duplicate": bool(possible_duplicates),
                "possible_duplicates": possible_duplicates,
                "reason": "No exact identity exists; similar names are audit suggestions only." if possible_duplicates else "No existing identity found."}


def mapped_video_entity_context(db, video_id: int) -> dict:
    """Return already-mapped performer names and whether a known studio is linked.

    Unknown placeholders are deliberately excluded. This lets a scene suggestion
    fill only genuinely missing mappings while preserving links the operator or
    an earlier provider pass has already established.
    """
    unknown_names = {"unknown", "unknown performer", "unknown studio", "unknown site", "unknown channel", "n/a", "none"}
    performer_names: list[str] = []
    studio_names: list[str] = []
    performer_provider_ids: dict[str, list[str]] = {}
    studio_provider_ids: dict[str, list[str]] = {}

    def collect_provider_ids(target, raw):
        identities = _load_json(raw, {})
        if not isinstance(identities, dict):
            return
        for source, raw_ids in identities.items():
            source_key = str(source or "").strip().casefold()
            if not source_key:
                continue
            values = _provider_id_values(raw_ids)
            if values:
                target.setdefault(source_key, [])
                target[source_key].extend(value for value in values if value not in target[source_key])

    with db.lock:
        try:
            rows = db.conn.execute(
                """SELECT DISTINCT p.id, p.name, p.aliases, p.provider_ids FROM performers p WHERE p.id IN (
                     SELECT performer_id FROM video_performers WHERE video_id=?
                     UNION SELECT performer_id FROM local_video_performers WHERE video_id=?
                   ) ORDER BY p.name""",
                (int(video_id), int(video_id)),
            ).fetchall()
        except Exception:
            rows = []
        for row in rows:
            name = display_name(str(row["name"] or ""))
            if name and normalized_name(name) not in unknown_names:
                performer_names.append(name)
                aliases = _load_json(row["aliases"], [])
                if isinstance(aliases, list):
                    performer_names.extend(display_name(value) for value in aliases
                                           if isinstance(value, str) and display_name(value)
                                           and normalized_name(value) not in unknown_names)
                collect_provider_ids(performer_provider_ids, row["provider_ids"])
        try:
            rows = db.conn.execute(
                """SELECT DISTINCT s.id, s.name, s.aliases, s.provider_ids
                   FROM studios s JOIN video_studios vs ON vs.studio_id=s.id
                   WHERE vs.video_id=? ORDER BY s.name""", (int(video_id),)
            ).fetchall()
        except Exception:
            rows = []
        studio_names.extend(display_name(str(row["name"] or "")) for row in rows
                            if display_name(str(row["name"] or ""))
                            and normalized_name(str(row["name"] or "")) not in unknown_names)
        for row in rows:
            aliases = _load_json(row["aliases"], [])
            if isinstance(aliases, list):
                studio_names.extend(display_name(value) for value in aliases
                                    if isinstance(value, str) and display_name(value)
                                    and normalized_name(value) not in unknown_names)
            collect_provider_ids(studio_provider_ids, row["provider_ids"])
        try:
            media = db.conn.execute("SELECT site_name FROM media WHERE id=?", (int(video_id),)).fetchone()
        except Exception:
            media = None
        if media:
            site_name = display_name(str(media["site_name"] or ""))
            if site_name and normalized_name(site_name) not in unknown_names:
                studio_names.append(site_name)
    # Stable dedupe by canonical identity, preserving display names.
    def unique(values):
        out, seen = [], set()
        for value in values:
            key = canonical_key(value)
            if key and key not in seen:
                seen.add(key)
                out.append(value)
        return out
    return {"performer_names": unique(performer_names), "studio_names": unique(studio_names),
            "has_known_studio": bool(studio_names),
            "performer_provider_ids": performer_provider_ids,
            "studio_provider_ids": studio_provider_ids}


def resolve_existing_by_provider_ids(db, table: str, provider_ids: object, *, provider: str = "", provider_id=None) -> dict | None:
    """Resolve one existing canonical entity from any namespaced provider ID.

    Cross-provider matches often carry both StashDB and ThePornDB IDs. A local
    row may already be mapped by either source. Return that row only when all
    supplied IDs that resolve locally point to the same canonical row; callers
    must handle conflicts before creating or relinking entities.
    """
    if table not in {"performers", "studios"}:
        return None
    identities: dict[str, str] = {}
    if isinstance(provider_ids, dict):
        for source, raw_id in provider_ids.items():
            src, ident = str(source or "").strip(), str(raw_id or "").strip()
            if src and ident:
                identities[src] = ident
    src, ident = str(provider or "").strip(), str(provider_id or "").strip()
    if src and ident:
        identities.setdefault(src, ident)
    found: dict[int, dict] = {}
    with db.lock:
        for source, ident in identities.items():
            row = _provider_match(db, table, source, ident)
            if row:
                found[int(row["id"])] = {**dict(row), "_matched_provider": source, "_matched_provider_id": ident}
    if len(found) == 1:
        return next(iter(found.values()))
    return None


def performer_identity_conflicts(db, match_or_people, *, default_provider: str = "", ignore_names=None) -> list[dict]:
    """Find cast identities that must be reviewed before applying a scene.

    Provider IDs are namespaced. When a merged StashDB/ThePornDB person carries
    both IDs, one exact existing provider-ID mapping can resolve the person to
    its canonical local row. IDs from the two providers that point to different
    local rows remain a real conflict and are not silently merged. Names already
    mapped on this video can be ignored by confirmation because those links are
    preserved rather than rewritten.
    """
    if isinstance(match_or_people, dict):
        people = match_or_people.get("performer_details") or match_or_people.get("performers") or []
        default_provider = str(match_or_people.get("source") or default_provider or "").strip()
    elif isinstance(match_or_people, (list, tuple)):
        people = match_or_people
    else:
        people = []

    ignored = {canonical_key(str(value or "")) for value in (ignore_names or []) if canonical_key(str(value or ""))}
    conflicts: list[dict] = []
    incoming_by_key: dict[str, dict[str, str]] = {}
    seen_conflicts: set[tuple[str, str, str]] = set()
    for raw in people:
        if isinstance(raw, str):
            person = {"name": raw}
        elif isinstance(raw, dict):
            person = raw
        else:
            continue
        name = display_name(str(person.get("name") or ""))
        key = canonical_key(name)
        if not name or not key or key in ignored:
            continue

        identities: dict[str, str] = {}
        raw_provider_ids = person.get("provider_ids")
        if isinstance(raw_provider_ids, dict):
            for source, raw_id in raw_provider_ids.items():
                src, ident = str(source or "").strip(), str(raw_id or "").strip()
                if src and ident:
                    identities[src] = ident
        provider = str(person.get("provider") or default_provider or "").strip()
        provider_id = str(person.get("id") or "").strip()
        if provider and provider_id:
            identities.setdefault(provider, provider_id)
        if not identities:
            identities[provider or ""] = ""

        per_name = incoming_by_key.setdefault(key, {})
        same_provider_conflict = False
        for source, ident in identities.items():
            source_key = source.casefold()
            prev_id = per_name.get(source_key)
            if prev_id and ident and prev_id != ident:
                identity = (key, source_key, ident)
                if identity not in seen_conflicts:
                    conflicts.append({"name": name, "provider": source, "provider_id": ident,
                                      "reason": "The provider supplied multiple IDs for the same performer name in one scene."})
                    seen_conflicts.add(identity)
                same_provider_conflict = True
            elif ident:
                per_name[source_key] = ident
        if same_provider_conflict:
            continue

        resolved_ids: set[int] = set()
        ambiguous_assessments = []
        # Provider-ID checks are performed independently per source, not by
        # treating a ThePornDB ID as if it belonged to StashDB (or vice versa).
        for source, ident in identities.items():
            assessment = assess_performer_identity(db, name, provider=source, provider_id=ident or None)
            if assessment.get("status") == "existing" and assessment.get("id") is not None:
                resolved_ids.add(int(assessment["id"]))
            elif assessment.get("status") == "ambiguous":
                ambiguous_assessments.append((source, ident, assessment))

        if len(resolved_ids) > 1:
            identity = (key, "cross-provider", ",".join(map(str, sorted(resolved_ids))))
            if identity not in seen_conflicts:
                conflicts.append({"name": name, "provider": "StashDB/ThePornDB",
                                  "provider_id": None,
                                  "reason": "Provider IDs for this performer point to different local performer records. Merge or map the existing records before confirming.",
                                  "candidates": [{"id": rid} for rid in sorted(resolved_ids)]})
                seen_conflicts.add(identity)
            continue
        if len(resolved_ids) == 1:
            # One authoritative provider ID resolves the identity. An exact-name
            # ambiguity from the other source is not a reason to duplicate or
            # block this already-mapped canonical performer.
            continue
        for source, ident, assessment in ambiguous_assessments:
            identity = (key, source, ident)
            if identity not in seen_conflicts:
                conflicts.append({"name": name, "provider": source, "provider_id": ident,
                                  "reason": assessment.get("reason") or "Ambiguous existing performer identity.",
                                  "candidates": assessment.get("candidates") or []})
                seen_conflicts.add(identity)
    return conflicts




# ==============================================================================
# SECTION: Public upserts and merges
# Performer and studio upserts and merges.
# ==============================================================================

def upsert_performer(db, name: str, *, provider: str = "", provider_id=None,
                     image=None, profile=None, bio=None, gender=None) -> Optional[int]:
    return upsert_entity(db, "performers", name, provider=provider,
                         provider_id=provider_id, image=image, profile=profile, bio=bio, gender=gender)


def upsert_studio(db, name: str, *, provider: str = "", provider_id=None,
                  image=None) -> Optional[int]:
    return upsert_entity(db, "studios", name, provider=provider,
                         provider_id=provider_id, image=image)


def merge_performers(db, keep_id: int, merge_id: int, *, commit: bool = True, reason: str = "explicit user-confirmed merge") -> dict:
    """Explicitly merge one performer record into another, preserving links.

    This primitive does not decide whether two records are the same person.
    Callers must establish identity evidence before using it. The UI uses it for
    operator-confirmed merges; catalogue auto-cleanup invokes it only for groups
    that pass strict canonical-name/provider-ID checks with no identity conflicts.
    """
    keep_id, merge_id = int(keep_id), int(merge_id)
    if keep_id == merge_id:
        raise ValueError("Choose two different performer records")
    from .. import config

    with db.lock:
        db.conn.execute("SAVEPOINT archive_x_merge_performer")
        try:
            keep_row = db.conn.execute("SELECT * FROM performers WHERE id=?", (keep_id,)).fetchone()
            merge_row = db.conn.execute("SELECT * FROM performers WHERE id=?", (merge_id,)).fetchone()
            if not keep_row or not merge_row:
                raise ValueError("Performer not found; refresh the list and try again")
            keep = dict(keep_row)
            source = dict(merge_row)

            # The keeper's display name remains canonical. Keep other spelling
            # variants as aliases so search and later matching can still find them.
            aliases = []
            for value in (_load_json(keep.get("aliases"), []), _load_json(source.get("aliases"), []),
                          [source.get("name"), keep.get("name")]):
                for item in value if isinstance(value, list) else []:
                    cleaned = display_name(item)
                    if cleaned and cleaned.casefold() != display_name(keep.get("name")).casefold() and                             cleaned.casefold() not in {x.casefold() for x in aliases}:
                        aliases.append(cleaned)
            provider_ids = _merge_json_maps(keep.get("provider_ids"), source.get("provider_ids"))
            assignments = ["provider_ids=?", "aliases=?"]
            values = [provider_ids, json.dumps(aliases, ensure_ascii=False)]
            columns = {str(r["name"]) for r in db.conn.execute("PRAGMA table_info(performers)").fetchall()}

            # Prefer the keeper's selected artwork/profile, but fill holes from
            # the source and preserve the source lock when that value was copied.
            for col in ("image", "profile", "bio", "gender", "image_source"):
                if col in columns and not str(keep.get(col) or "").strip() and str(source.get(col) or "").strip():
                    assignments.append(f"{col}=?")
                    values.append(source.get(col))
                    keep[col] = source.get(col)
            if "image_crop_version" in columns and not int(keep.get("image_crop_version") or 0) and int(source.get("image_crop_version") or 0):
                assignments.append("image_crop_version=?")
                values.append(int(source.get("image_crop_version") or 0))
            for lock_col, source_value_col in (("image_locked", "image"), ("name_locked", "name"), ("profile_locked", "profile")):
                if lock_col in columns and not bool(keep.get(lock_col)) and bool(source.get(lock_col)):
                    has_target_value = bool(str(keep.get(source_value_col) or "").strip())
                    has_source_value = bool(str(source.get(source_value_col) or "").strip())
                    if not has_target_value and has_source_value:
                        assignments.append(f"{lock_col}=?")
                        values.append(1)

            values.append(keep_id)
            db.conn.execute(f"UPDATE performers SET {', '.join(assignments)} WHERE id=?", values)

            # Repoint canonical and locally tagged video associations. INSERT OR
            # IGNORE prevents duplicate (video, performer) pairs during merge.
            db.conn.execute(
                "INSERT OR IGNORE INTO video_performers(video_id,performer_id,confidence) "
                "SELECT video_id,?,confidence FROM video_performers WHERE performer_id=?",
                (keep_id, merge_id),
            )
            db.conn.execute("DELETE FROM video_performers WHERE performer_id=?", (merge_id,))
            db.conn.execute(
                "INSERT OR IGNORE INTO local_video_performers(video_id,performer_id,created_at) "
                "SELECT video_id,?,created_at FROM local_video_performers WHERE performer_id=?",
                (keep_id, merge_id),
            )
            db.conn.execute("DELETE FROM local_video_performers WHERE performer_id=?", (merge_id,))

            tables = {str(r[0]) for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "metadata_suggestions" in tables:
                db.conn.execute("UPDATE metadata_suggestions SET suggested_performer_id=? WHERE suggested_performer_id=?", (keep_id, merge_id))
            if "entity_metadata" in tables:
                db.conn.execute(
                    "INSERT OR IGNORE INTO entity_metadata(entity_type,entity_id,provider,provider_id,payload,normalized,fetched_at,updated_at) "
                    "SELECT entity_type,?,provider,provider_id,payload,normalized,fetched_at,updated_at FROM entity_metadata "
                    "WHERE entity_type='performer' AND entity_id=?", (keep_id, merge_id),
                )
                db.conn.execute("DELETE FROM entity_metadata WHERE entity_type='performer' AND entity_id=?", (merge_id,))
            if "entity_favorites" in tables:
                db.conn.execute(
                    "INSERT OR IGNORE INTO entity_favorites(entity_type,entity_id,created_at) "
                    "SELECT entity_type,?,created_at FROM entity_favorites WHERE entity_type='performer' AND entity_id=?",
                    (keep_id, merge_id),
                )
                db.conn.execute("DELETE FROM entity_favorites WHERE entity_type='performer' AND entity_id=?", (merge_id,))
            if "performer_image_cache" in tables:
                db.conn.execute(
                    "INSERT OR IGNORE INTO performer_image_cache(performer_id,url,mime,data,updated_at) "
                    "SELECT ?,url,mime,data,updated_at FROM performer_image_cache WHERE performer_id=?",
                    (keep_id, merge_id),
                )
                db.conn.execute("DELETE FROM performer_image_cache WHERE performer_id=?", (merge_id,))
            if "entity_merge_audit" in tables:
                db.conn.execute(
                    "INSERT INTO entity_merge_audit(entity_type,canonical_key,survivor_id,merged_id,survivor_name,merged_name,reason,created_at) "
                    "VALUES('performers',?,?,?,?,?,?,?)",
                    (str(keep.get("canonical_key") or canonical_key(keep.get("name") or "")), keep_id, merge_id,
                     str(keep.get("name") or ""), str(source.get("name") or ""), str(reason or "explicit user-confirmed merge"), config.now_iso()),
                )
            db.conn.execute("DELETE FROM performers WHERE id=?", (merge_id,))
            db.conn.execute("RELEASE SAVEPOINT archive_x_merge_performer")
            if commit:
                db.conn.commit()
            videos = db.conn.execute(
                "SELECT COUNT(DISTINCT video_id) FROM (SELECT video_id FROM video_performers WHERE performer_id=? "
                "UNION SELECT video_id FROM local_video_performers WHERE performer_id=?)", (keep_id, keep_id),
            ).fetchone()[0]
            return {"keep_id": keep_id, "merged_id": merge_id, "name": str(keep.get("name") or ""),
                    "video_count": int(videos or 0), "provider_ids": json.loads(provider_ids or "{}")}
        except Exception:
            db.conn.execute("ROLLBACK TO SAVEPOINT archive_x_merge_performer")
            db.conn.execute("RELEASE SAVEPOINT archive_x_merge_performer")
            raise


def merge_performers_batch(db, keep_id: int, merge_ids) -> dict:
    """Atomically merge multiple explicitly selected duplicates into one keeper."""
    keep_id = int(keep_id)
    try:
        requested = [int(value) for value in merge_ids]
    except (TypeError, ValueError) as exc:
        raise ValueError("Every duplicate must have a valid performer ID") from exc
    if not requested:
        raise ValueError("Select at least one duplicate performer")
    if len(requested) > 500:
        raise ValueError("Select no more than 500 duplicate performers at once")
    if keep_id in requested:
        raise ValueError("The canonical performer cannot also be selected as a duplicate")
    if len(set(requested)) != len(requested):
        raise ValueError("A duplicate performer was selected more than once")

    from .. import config
    with db.lock:
        db.conn.execute("SAVEPOINT archive_x_merge_batch")
        try:
            all_ids = [keep_id, *requested]
            placeholders = ",".join("?" for _ in all_ids)
            found = {int(row[0]) for row in db.conn.execute(
                f"SELECT id FROM performers WHERE id IN ({placeholders})", all_ids
            ).fetchall()}
            missing = [entity_id for entity_id in all_ids if entity_id not in found]
            if missing:
                raise ValueError("One or more selected performers no longer exist; refresh the list and try again")

            merged = []
            for merge_id in requested:
                result = merge_performers(db, keep_id, merge_id, commit=False)
                merged.append(result)
            db.conn.execute("RELEASE SAVEPOINT archive_x_merge_batch")
            db.conn.commit()
            row = db.conn.execute("SELECT name FROM performers WHERE id=?", (keep_id,)).fetchone()
            return {"keep_id": keep_id, "name": str(row[0] if row else ""),
                    "merged_ids": requested, "merged_count": len(merged), "merges": merged,
                    "video_count": max((int(item.get("video_count", 0)) for item in merged), default=0)}
        except Exception:
            db.conn.execute("ROLLBACK TO SAVEPOINT archive_x_merge_batch")
            db.conn.execute("RELEASE SAVEPOINT archive_x_merge_batch")
            raise
