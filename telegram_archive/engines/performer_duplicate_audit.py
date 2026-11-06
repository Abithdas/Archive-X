"""Conservative, read-only audit of possible duplicate performer records.

The audit deliberately never merges records. It groups only candidates supported
by exact canonical-name equality, provider-ID evidence, or saved aliases; fuzzy
name similarity is emitted as a separate review-only suggestion.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Record helpers
#   - Pair classification
#   - Audit and cleanup
# ------------------------------------------------------------------
from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any

from .entity_identity import canonical_key, display_name




# ==============================================================================
# SECTION: Record helpers
# JSON, IDs, aliases and formatting.
# ==============================================================================

def _json(value: Any, expected: type, default):
    if value is None or value == "":
        return default
    if isinstance(value, expected):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, expected) else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _id_map(value: Any) -> dict[str, set[str]]:
    source = _json(value, dict, {})
    result: dict[str, set[str]] = {}
    for provider, raw_ids in source.items():
        key = str(provider or "").strip().casefold()
        ids = raw_ids if isinstance(raw_ids, (list, tuple, set)) else [raw_ids]
        clean = {str(item).strip() for item in ids if item is not None and str(item).strip()}
        if key and clean:
            result.setdefault(key, set()).update(clean)
    return result


def _alias_keys(name: str, aliases_value: Any) -> set[str]:
    aliases = _json(aliases_value, list, [])
    return {canonical_key(item) for item in aliases if isinstance(item, str) and canonical_key(item)}


def _format_ids(provider_ids: dict[str, set[str]]) -> dict[str, list[str]]:
    return {key: sorted(values) for key, values in sorted(provider_ids.items()) if values}




# ==============================================================================
# SECTION: Pair classification
# Provider relation, classification and fuzzy candidates.
# ==============================================================================

def _provider_relation(a: dict, b: dict) -> tuple[bool, bool, list[str]]:
    """Return (shared ID, conflicting provider IDs, conflict descriptions)."""
    aid, bid = a["_provider_ids"], b["_provider_ids"]
    shared = False
    conflicts: list[str] = []
    for provider in sorted(set(aid) & set(bid)):
        if aid[provider] & bid[provider]:
            shared = True
        elif aid[provider] and bid[provider]:
            conflicts.append(provider)
    return shared, bool(conflicts), conflicts


def _classify_pair(a: dict, b: dict) -> dict | None:
    """Classify two performer records as a possible duplicate, or return None."""
    akey, bkey = a["_key"], b["_key"]
    if not akey or not bkey or int(a["id"]) == int(b["id"]):
        return None
    same_name = akey == bkey
    shared_id, conflict, conflict_providers = _provider_relation(a, b)
    shared_alias = bool(
        (akey and akey in b["_aliases"])
        or (bkey and bkey in a["_aliases"])
        or (a["_aliases"] & b["_aliases"])
    )

    if conflict and (same_name or shared_id or shared_alias):
        return {
            "kind": "review",
            "evidence": "provider_conflict",
            "auto_merge_eligible": False,
            "reason": "The records share a name or alias, but have different confirmed IDs for "
                      + ", ".join(conflict_providers) + ". Keep separate until identity is verified.",
        }
    if same_name:
        return {
            "kind": "strong_merge",
            "evidence": "canonical_name",
            "auto_merge_eligible": True,
            "reason": "The canonical names match after case, punctuation, spacing and diacritic normalization; no conflicting provider identity was found.",
        }
    if shared_id:
        return {
            "kind": "candidate_merge",
            "evidence": "provider_id",
            "auto_merge_eligible": True,
            "reason": "The names differ, but at least one namespaced provider ID is identical and no provider-ID conflict was found.",
        }
    if shared_alias:
        return {
            "kind": "candidate_merge",
            "evidence": "alias",
            "auto_merge_eligible": False,
            "reason": "The records share a saved alias or one record's canonical name is an alias of the other. Keep in review unless another identity signal confirms the match.",
        }
    return None


def _fuzzy_name_candidates(records: list[dict], excluded_ids: set[int]) -> list[dict]:
    """Generate review-only name pairs using an inverted n-gram index.

    Blocking by character n-grams avoids an unrestricted O(n²) comparison over
    the entire performer catalogue while still catching ordinary spelling
    variants. These pairs never enter the merge-candidate connected components.
    """
    keys: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        if int(row["id"]) not in excluded_ids and len(row["_key"]) >= 6:
            keys[row["_key"]].append(row)
    distinct = sorted(keys)
    grams_to_keys: dict[str, set[str]] = defaultdict(set)
    for key in distinct:
        size = 3 if len(key) >= 8 else 2
        grams = {key[i:i + size] for i in range(len(key) - size + 1)}
        for gram in grams:
            grams_to_keys[gram].add(key)
    compared: set[tuple[str, str]] = set()
    suggestions: list[dict] = []
    for key in distinct:
        size = 3 if len(key) >= 8 else 2
        grams = {key[i:i + size] for i in range(len(key) - size + 1)}
        nearby: set[str] = set()
        for gram in grams:
            nearby.update(grams_to_keys.get(gram, ()))
        for other in nearby:
            if other <= key:
                continue
            pair_key = (key, other)
            if pair_key in compared:
                continue
            compared.add(pair_key)
            if abs(len(key) - len(other)) > max(2, int(max(len(key), len(other)) * 0.18)):
                continue
            ratio = SequenceMatcher(None, key, other).ratio()
            if ratio < 0.84:
                continue
            for a in keys[key]:
                for b in keys[other]:
                    _shared_id, conflict, _conflict_providers = _provider_relation(a, b)
                    # Confirmed conflicting IDs are a reason not to imply these
                    # similar spellings refer to the same person.
                    if conflict:
                        continue
                    suggestions.append({
                        "kind": "suggest_review",
                        "evidence": "fuzzy_name",
                        "auto_merge_eligible": False,
                        "title": "Similar but not identical names",
                        "recommended_treatment": "Suggest for review only",
                        "reason": "Names are similar (" + f"{ratio:.0%}" + ") but there is no reliable identity evidence. Keep separate unless another source confirms the match.",
                        "similarity": round(ratio, 3),
                        "performers": [_public_record(a), _public_record(b)],
                    })
    suggestions.sort(key=lambda x: (-float(x.get("similarity", 0)), x["performers"][0]["name"].casefold(), x["performers"][1]["name"].casefold()))
    return suggestions


def _public_record(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": str(row.get("name") or ""),
        "aliases": sorted({display_name(x) for x in row.get("_aliases_raw", []) if display_name(x)}, key=str.casefold),
        "provider_ids": _format_ids(row.get("_provider_ids") or {}),
        "video_count": int(row.get("video_count") or 0),
        "has_image": bool(str(row.get("image") or "").strip()),
        "name_locked": bool(row.get("name_locked", 0)),
    }




# ==============================================================================
# SECTION: Audit and cleanup
# Read-only audit and automatic cleanup.
# ==============================================================================

def audit_performer_duplicates(db) -> dict:
    """Inspect every performer row and return evidence-based duplicate groups.

    This operation is read-only. It intentionally includes performers with zero
    published video associations so duplicate identities are not hidden by the
    normal performer-page visibility filter.
    """
    with db.lock:
        rows = [dict(row) for row in db.conn.execute("SELECT * FROM performers ORDER BY id").fetchall()]
        counts: dict[int, int] = defaultdict(int)
        link_sql = (
            "SELECT performer_id, video_id FROM video_performers "
            "UNION SELECT performer_id, video_id FROM local_video_performers"
        )
        for row in db.conn.execute(f"SELECT performer_id, COUNT(DISTINCT video_id) AS n FROM ({link_sql}) GROUP BY performer_id").fetchall():
            counts[int(row["performer_id"])] = int(row["n"] or 0)
        # Some older catalogue rows have provider metadata outside provider_ids.
        tables = {str(r[0]) for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        metadata_ids: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        if "entity_metadata" in tables:
            for row in db.conn.execute(
                "SELECT entity_id, provider, provider_id FROM entity_metadata "
                "WHERE entity_type='performer' AND provider_id IS NOT NULL AND TRIM(provider_id)<>''"
            ).fetchall():
                metadata_ids[int(row["entity_id"])][str(row["provider"] or "").strip().casefold()].add(str(row["provider_id"]).strip())

    records: list[dict] = []
    for raw in rows:
        aliases_raw = _json(raw.get("aliases"), list, [])
        provider_ids = _id_map(raw.get("provider_ids"))
        for provider, ids in metadata_ids.get(int(raw["id"]), {}).items():
            provider_ids.setdefault(provider, set()).update(ids)
        for key, ids in _id_map(raw.get("provider_ids")).items():
            provider_ids.setdefault(key, set()).update(ids)
        row = dict(raw)
        row["video_count"] = counts.get(int(raw["id"]), 0)
        row["_key"] = canonical_key(str(raw.get("name") or ""))
        row["_provider_ids"] = provider_ids
        row["_aliases_raw"] = aliases_raw
        row["_aliases"] = _alias_keys(str(raw.get("name") or ""), aliases_raw)
        records.append(row)

    # Build all evidence-backed pair relationships.
    parent = {int(row["id"]): int(row["id"]) for row in records}
    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value
    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    pairs: list[dict] = []
    candidate_ids: set[int] = set()
    by_key: dict[str, list[dict]] = defaultdict(list)
    provider_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    alias_index: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        if row["_key"]:
            by_key[row["_key"]].append(row)
        for provider, ids in row["_provider_ids"].items():
            for provider_id in ids:
                provider_index[(provider, provider_id)].append(row)
        for alias in row["_aliases"]:
            alias_index[alias].append(row)

    pair_ids: set[tuple[int, int]] = set()
    def add_pair(a: dict, b: dict) -> None:
        aid, bid = int(a["id"]), int(b["id"])
        if aid == bid:
            return
        pair = (min(aid, bid), max(aid, bid))
        if pair in pair_ids:
            return
        classified = _classify_pair(a, b)
        if not classified:
            return
        pair_ids.add(pair)
        classified.update({"left_id": pair[0], "right_id": pair[1]})
        pairs.append(classified)
        candidate_ids.update(pair)
        union(*pair)

    for group in by_key.values():
        if len(group) > 1:
            for a, b in combinations(group, 2):
                add_pair(a, b)
    for group in provider_index.values():
        if len(group) > 1:
            for a, b in combinations(group, 2):
                add_pair(a, b)
    for group in alias_index.values():
        if len(group) > 1:
            for a, b in combinations(group, 2):
                add_pair(a, b)
    # Alias spelling can match another record's canonical name even when the
    # alias index group itself contains only the record owning that alias.
    for row in records:
        for alias in row["_aliases"]:
            for other in by_key.get(alias, []):
                add_pair(row, other)

    member_groups: dict[int, list[dict]] = defaultdict(list)
    for row in records:
        root = find(int(row["id"]))
        member_groups[root].append(row)
    priority = {"review": 3, "candidate_merge": 2, "strong_merge": 1}
    pair_by_component: dict[int, list[dict]] = defaultdict(list)
    for pair in pairs:
        pair_by_component[find(pair["left_id"])].append(pair)
    titles = {
        "strong_merge": ("Same canonical name and compatible provider IDs", "Strong candidate for merging"),
        "candidate_merge": ("Shared alias or provider identity", "Candidate for merging"),
        "review": ("Identity evidence conflicts", "Review before merging"),
    }
    groups: list[dict] = []
    grouped_ids: set[int] = set()
    for root, members in member_groups.items():
        if len(members) < 2:
            continue
        relationships = pair_by_component.get(root, [])
        if not relationships:
            continue
        kinds = {item["kind"] for item in relationships}
        kind = "review" if "review" in kinds else "candidate_merge" if "candidate_merge" in kinds else "strong_merge"
        title, treatment = titles[kind]
        public_members = sorted((_public_record(item) for item in members), key=lambda x: (-x["video_count"], -len(x["provider_ids"]), x["name"].casefold(), x["id"]))
        # Auto-cleanup is intentionally stricter than the audit itself. Every
        # pair in the connected component must be supported by a normalized
        # canonical-name match or the same namespaced provider ID. Alias-only
        # and fuzzy-name evidence always stays in review. Unknown Performer is
        # a protected fallback entity and can never be merged away automatically.
        unknown_key = canonical_key("Unknown Performer")
        contains_unknown = any(item["_key"] == unknown_key for item in members)
        auto_merge_eligible = bool(relationships) and all(
            item.get("auto_merge_eligible") is True
            and item.get("evidence") in {"canonical_name", "provider_id"}
            for item in relationships
        ) and not contains_unknown
        evidence_types = sorted({str(item.get("evidence") or "unknown") for item in relationships})
        if contains_unknown:
            treatment = "Keep separate — Unknown Performer is a protected fallback entity"
        elif auto_merge_eligible:
            treatment = "Eligible for automatic high-confidence cleanup"
        elif "alias" in evidence_types and not (set(evidence_types) & {"canonical_name", "provider_id"}):
            treatment = "Keep in performer review/suggestions; alias-only evidence is not enough for automatic merging"
        groups.append({
            "kind": kind,
            "title": title,
            "recommended_treatment": treatment,
            "reason": " ".join(dict.fromkeys(item["reason"] for item in relationships)),
            "performers": public_members,
            "pair_evidence": len(relationships),
            "auto_merge_eligible": auto_merge_eligible,
            "contains_unknown_performer": contains_unknown,
            "evidence_types": evidence_types,
        })
        grouped_ids.update(int(x["id"]) for x in members)

    suggestions = _fuzzy_name_candidates(records, grouped_ids)
    # Exact duplicates already grouped through a different route should not also
    # appear as typo suggestions. Limit payload size while reporting the total.
    groups.sort(key=lambda x: ({"strong_merge": 0, "candidate_merge": 1, "review": 2}.get(x["kind"], 9), x["performers"][0]["name"].casefold()))
    suggestion_total = len(suggestions)
    suggestion_limit = 500
    suggestions = suggestions[:suggestion_limit]
    for item in suggestions:
        grouped_ids.update(int(x["id"]) for x in item["performers"])
    groups.extend(suggestions)

    # Name rows that look like leftover filename/title fragments are surfaced
    # separately from normal duplicate suggestions. They are never auto-deleted.
    release_tags = re.compile(r"\b(?:PRT|XXX|HEVC|X265|X264|H265|H264|1080P|720P|2160P|4K|WEB(?:[- ]?DL)?|UHD|FHD|HDR|AAC|DTS|MP4|MKV|AV1)\b", re.IGNORECASE)
    title_fragment_words = {"sexy", "teen", "schoolgirl", "colombian", "creampie", "gangbang", "threesome", "compilation", "uncensored", "scene", "on1", "on4", "dap", "bts", "fullmovie"}
    def _clean_fragment_name(value: str) -> str:
        value = release_tags.sub(" ", value or "")
        value = re.sub(r"\b(?:official|repack|proper|limited|remastered|extended|unrated|hdtv|bluray|brrip|webrip|xvid|avc)\b", " ", value, flags=re.I)
        value = re.sub(r"[._\-]+", " ", value)
        return re.sub(r"\s+", " ", value).strip(" _.-")
    def _looks_like_title_fragment(value: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()
        tokens = normalized.split()
        return bool(tokens and (release_tags.search(value or "") or (len(tokens) >= 3 and any(t in title_fragment_words for t in tokens))))
    junk_groups = []
    for row in records:
        name = str(row.get("name") or "").strip()
        if not name or canonical_key(name) == canonical_key("Unknown Performer") or not _looks_like_title_fragment(name):
            continue
        cleaned = _clean_fragment_name(name)
        clean_key = canonical_key(cleaned)
        candidates = []
        for other in records:
            if int(other["id"]) == int(row["id"]):
                continue
            other_name = str(other.get("name") or "").strip()
            if not other_name or _looks_like_title_fragment(other_name) or canonical_key(other_name) == canonical_key("Unknown Performer"):
                continue
            other_key = canonical_key(other_name)
            ratio = SequenceMatcher(None, clean_key, other_key).ratio() if clean_key and other_key else 0.0
            if clean_key and clean_key == other_key:
                ratio = 1.0
            elif clean_key and (clean_key in other_key or other_key in clean_key) and min(len(clean_key), len(other_key)) >= 8:
                ratio = max(ratio, min(len(clean_key), len(other_key)) / max(len(clean_key), len(other_key)))
            if ratio >= 0.72:
                candidates.append((ratio, int(other.get("video_count") or 0), other_name.casefold(), other))
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        target = _public_record(candidates[0][3]) if candidates else None
        reason_parts = ["Performer name contains a release/technical tag or likely title fragment."]
        if target:
            reason_parts.append(f"Possible keeper: {target['name']} ({round(candidates[0][0] * 100)}% similarity after cleanup).")
        if int(row.get("video_count") or 0):
            reason_parts.append("This record has linked videos; delete is prohibited until those links are merged.")
        else:
            reason_parts.append("No linked videos; deletion is available after confirmation if no merge target is appropriate.")
        junk_groups.append({
            "kind": "junk_fragment",
            "title": "Likely filename/title fragment",
            "recommended_treatment": f"Merge into {target['name']}" if target else ("Choose a merge target before removing linked records" if int(row.get("video_count") or 0) else "Review, then delete if confirmed junk"),
            "reason": " ".join(reason_parts),
            "performers": [_public_record(row)],
            "suggested_target": target,
            "junk_reason": "release_tag" if release_tags.search(name) else "title_fragment",
            "auto_merge_eligible": False,
            "video_linked": bool(int(row.get("video_count") or 0)),
        })
        grouped_ids.add(int(row["id"]))
    junk_groups.sort(key=lambda group: (-(group["performers"][0]["video_count"]), group["performers"][0]["name"].casefold()))
    groups.extend(junk_groups)

    counts_by_kind = {kind: sum(1 for group in groups if group["kind"] == kind) for kind in ("strong_merge", "candidate_merge", "review", "suggest_review", "junk_fragment")}
    return {
        "ok": True,
        "performer_count": len(records),
        "group_count": len(groups),
        "strong_merge_groups": counts_by_kind["strong_merge"],
        "candidate_merge_groups": counts_by_kind["candidate_merge"],
        "review_groups": counts_by_kind["review"],
        "suggestion_groups": suggestion_total,
        "suggestion_groups_returned": len(suggestions),
        "junk_fragment_groups": counts_by_kind["junk_fragment"],
        "unmatched_performer_count": max(0, len(records) - len(grouped_ids)),
        "suggestions_truncated": suggestion_total > len(suggestions),
        "groups": groups,
    }


def auto_cleanup_performer_duplicates(db) -> dict:
    """Merge only high-confidence duplicate groups and leave all ambiguity untouched.

    Groups qualify only when *every* pair is linked by the same normalized
    canonical name or the same namespaced provider ID, with no conflicting
    IDs. Alias-only groups, conflicting IDs, fuzzy names, and Unknown Performer
    remain available for review. All merges from one run share one transaction.
    """
    from .entity_identity import merge_performers

    merged_groups: list[dict] = []
    merged_ids: list[int] = []
    with db.lock:
        db.conn.execute("SAVEPOINT archive_x_auto_performer_cleanup")
        try:
            # Recompute identities immediately before merging while holding the
            # database lock, so stale UI results cannot select obsolete IDs.
            before = audit_performer_duplicates(db)
            eligible = [group for group in before["groups"] if group.get("auto_merge_eligible") is True]
            for group in eligible:
                members = [dict(person) for person in group.get("performers", [])]
                if len(members) < 2:
                    continue
                # Prefer manually locked names, then existing catalogue links,
                # known provider identities, and artwork. Lowest ID breaks ties.
                keep = sorted(
                    members,
                    key=lambda person: (
                        not bool(person.get("name_locked")),
                        -int(person.get("video_count") or 0),
                        -len(person.get("provider_ids") or {}),
                        not bool(person.get("has_image")),
                        int(person.get("id") or 0),
                    ),
                )[0]
                keep_id = int(keep["id"])
                merge_ids = [int(person["id"]) for person in members if int(person["id"]) != keep_id]
                for merge_id in merge_ids:
                    merge_performers(
                        db, keep_id, merge_id, commit=False,
                        reason="automatic high-confidence duplicate cleanup",
                    )
                merged_ids.extend(merge_ids)
                merged_groups.append({
                    "keep_id": keep_id,
                    "name": str(keep.get("name") or ""),
                    "merged_ids": merge_ids,
                    "merged_count": len(merge_ids),
                    "evidence_types": list(group.get("evidence_types") or []),
                })
            db.conn.execute("RELEASE SAVEPOINT archive_x_auto_performer_cleanup")
            db.conn.commit()
        except Exception:
            db.conn.execute("ROLLBACK TO SAVEPOINT archive_x_auto_performer_cleanup")
            db.conn.execute("RELEASE SAVEPOINT archive_x_auto_performer_cleanup")
            raise

    after = audit_performer_duplicates(db)
    skipped = [group for group in before["groups"] if group.get("auto_merge_eligible") is not True]
    truncated_fuzzy = max(0, int(before.get("suggestion_groups") or 0) - int(before.get("suggestion_groups_returned") or 0))
    return {
        "ok": True,
        "merged_group_count": len(merged_groups),
        "merged_performer_count": len(merged_ids),
        "merged_groups": merged_groups,
        "skipped_review_group_count": len(skipped) + truncated_fuzzy,
        "protected_unknown_group_count": sum(1 for group in skipped if group.get("contains_unknown_performer")),
        "ambiguous_groups_left_for_review": len(skipped) + truncated_fuzzy,
        "similar_name_suggestions_not_rendered": truncated_fuzzy,
        "audit": after,
    }
