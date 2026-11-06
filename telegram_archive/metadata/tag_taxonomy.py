"""Derived, catalogue-wide tag filtering and category assignment.

The taxonomy is computed from tags attached to published videos. It is deliberately
read-only: category labels are derived for presentation and never invent or mutate
provider tags in the database.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable


# These are format, quality, or near-content-free labels. Specific descriptive tags
# such as "Big Ass" and "Blowjob" are intentionally not in this list.
GENERIC_TAGS = frozenset({
    "adult", "all sex", "full sex", "sex", "sexy", "porn", "porn video",
    "xxx", "video", "movie", "scene", "official", "uncensored", "hd",
    "full hd", "uhd", "sd", "4k", "8k", "h264", "h265", "hevc",
    "x264", "x265", "mp4", "mkv", "avi", "web dl", "webdl", "60fps",
    "50fps", "30fps", "25fps", "24fps", "new", "free", "online",
})

_GENERIC_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"^(?:240|360|480|540|576|720|1080|1440|2160|4320)\s?p$",
    r"^(?:h\.?264|h\.?265|hevc|x26[45])(?:\s+(?:encode|rip))?$",
    r"^(?:mp4|mkv|avi|mov|wmv|webm)$",
    r"^(?:\d{3,4}\s?p)(?:\s+(?:hd|hevc|x26[45]))?$",
))

# Tag-to-category rules. A tag may deliberately map to more than one category.
# Categories are derived labels only; no database records are created from these rules.
_CATEGORY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Anal", ("anal", "rimming", "rimjob", "anal oral", "analingus")),
    ("Oral", ("blowjob", "blow job", "deepthroat", "deep throat", "fellatio", "oral sex", "oral")),
    ("Group", ("threesome", "foursome", "orgy", "gangbang", "gang bang", "group sex", "double penetration", "double penetr", " dp ")),
    ("Lesbian", ("lesbian", "girl girl", "girl-girl", "girls with girls", "tribbing", "scissoring", "female female")),
    ("Masturbation", ("masturbat", "handjob", "hand job", "fingering", "solo play", "dildo", "vibrator")),
    ("BDSM & Fetish", ("bdsm", "bondage", "spanking", "domination", "dominant", "submissive", "fetish", "latex", "stockings", "pegging")),
    ("Foot & Feet", ("footjob", "foot job", "feet", "foot fetish", "foot worship")),
    ("Sex Toys", ("dildo", "vibrator", "sex toy", "toys", "anal plug")),
    ("Cumshot", ("cumshot", "cum shot", "facial", "creampie", "cream pie", "swallowing", "cum inside", "internal cum")),
    ("POV", ("pov", "p o v", "point of view")),
    ("Amateur", ("amateur", "homemade", "home made", "self shot", "selfshot")),
    ("Roleplay", ("roleplay", "role play", "cosplay", "schoolgirl", "teacher", "nurse", "maid", "secretary")),
    ("Outdoor & Public", ("outdoor", "public", "pool", "beach", "car sex", "park", "outdoors")),
    ("Sex Positions", ("doggystyle", "doggy style", "cowgirl", "missionary", "reverse cowgirl", "spooning", "standing position", "69 position")),
    ("Body & Appearance", ("big ass", "big tits", "big boobs", "busty", "petite", "milf", "mature", "bbw", "blonde", "brunette", "redhead", "curvy", "big dick", "small tits", "small boobs")),
)


def normalize_tag_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("&", " and ").replace("_", " ")
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()


_GENERIC_KEYS = frozenset(normalize_tag_key(x) for x in GENERIC_TAGS)


def is_generic_tag(value: Any) -> bool:
    key = normalize_tag_key(value)
    return not key or key in _GENERIC_KEYS or any(p.fullmatch(key) for p in _GENERIC_PATTERNS)


def categories_for_tag(value: Any) -> list[str]:
    key = f" {normalize_tag_key(value)} "
    if not key.strip():
        return []
    found: list[str] = []
    for category, terms in _CATEGORY_TERMS:
        for term in terms:
            norm = f" {normalize_tag_key(term)} "
            if norm.strip() and norm in key:
                found.append(category)
                break
    return found


def build_tag_taxonomy(total_videos: int, rows: Iterable[Any]) -> dict[str, Any]:
    """Build one normalized tag map from rows containing name and video_id.

    Aliased forms differing only in punctuation/case are grouped into a normalized
    key and their video counts are unioned, avoiding duplicate counts for videos
    carrying both variants. If no video_id is supplied (unit-test/adapter input),
    the highest per-variant count is used as a conservative fallback.
    """
    total = max(0, int(total_videos or 0))
    groups: dict[str, dict[str, Any]] = {}
    for row in rows or ():
        if hasattr(row, "keys"):
            name = str(row["name"] or "").strip()
            try:
                video_id = row["video_id"]
            except (KeyError, IndexError):
                video_id = None
            try:
                count = int(row["video_count"] or 0)
            except (KeyError, IndexError, TypeError, ValueError):
                count = 0
        else:
            name = str(getattr(row, "name", "") or "").strip()
            video_id = getattr(row, "video_id", None)
            count = int(getattr(row, "video_count", 0) or 0)
        key = normalize_tag_key(name)
        if not key:
            continue
        group = groups.setdefault(key, {"variant_ids": defaultdict(set), "ids": set(), "counts": defaultdict(int)})
        if video_id is not None:
            try:
                vid = int(video_id)
                group["variant_ids"][name].add(vid)
                group["ids"].add(vid)
            except (TypeError, ValueError):
                pass
        elif count:
            group["counts"][name] = max(group["counts"][name], count)

    tag_map: dict[str, dict[str, Any]] = {}
    ranked: list[dict[str, Any]] = []
    excluded_generic = excluded_common = 0
    for key, group in groups.items():
        variant_counts = {name: len(ids) for name, ids in group["variant_ids"].items()}
        variant_counts.update({name: max(variant_counts.get(name, 0), count) for name, count in group["counts"].items()})
        if not variant_counts:
            continue
        canonical = sorted(variant_counts, key=lambda name: (-variant_counts[name], len(name), name.casefold()))[0]
        count = len(group["ids"]) if group["ids"] else max(variant_counts.values(), default=0)
        count = min(total, max(0, count)) if total else max(0, count)
        prevalence = (count / total) if total else 0.0
        generic = is_generic_tag(canonical)
        common = bool(total and count * 10 >= total * 9)
        categories = categories_for_tag(canonical)
        if total > 1:
            rarity = max(0.0, min(1.0, math.log((total + 1) / (count + 1)) / math.log(total + 1)))
        else:
            rarity = 0.0 if count else 1.0
        usefulness = 0.65 if categories else 0.22
        score = round(0.72 * rarity + 0.28 * usefulness, 5)
        display = not generic and not common
        if generic:
            excluded_generic += 1
        if common:
            excluded_common += 1
        entry = {
            "key": key,
            "name": canonical,
            "aliases": sorted(name for name in variant_counts if name != canonical),
            "video_count": count,
            "prevalence": round(prevalence, 5),
            "rarity_score": round(rarity, 5),
            "usefulness_score": usefulness,
            "rank_score": score,
            "categories": categories,
            "generic": generic,
            "common": common,
            "display": display,
        }
        tag_map[key] = entry
        if display:
            ranked.append(entry)
    ranked.sort(key=lambda x: (-x["rank_score"], x["video_count"], x["name"].casefold()))
    return {
        "total_videos": total,
        "common_threshold": 0.9,
        "tag_map": tag_map,
        "ranked_tags": ranked,
        "excluded_generic_count": excluded_generic,
        "excluded_common_count": excluded_common,
    }


