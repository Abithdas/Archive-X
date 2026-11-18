"""Confidence-scored, cross-provider scene matching.

After local reference and exact OSHASH lookup, Archive X queries both StashDB
and ThePornDB with whichever scene clues are available: title, date/year,
performer, studio, aliases, or a scene code. Results are scored across independent
evidence dimensions. Cross-provider corroboration increases confidence, while a
single clue, close alternatives, or provider conflicts remain suggestions for
operator review instead of being silently applied.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Text and performer comparison
#   - Scoring signals
#   - Candidate collapsing
#   - Confidence decision
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import re
import unicodedata
import asyncio
import urllib.request
from datetime import date, datetime
from difflib import SequenceMatcher

from .parser import is_generic_scene_code, parse_scene_name
from .providers import _performer_details, identify, scene_sites_conflict

_STOPWORDS = {
    "the", "and", "with", "from", "for", "in", "on", "of", "a", "an",
    "to", "at", "by", "video", "porn", "scene", "hd", "web", "dl",
}




# ==============================================================================
# SECTION: Text and performer comparison
# Tokens, names and performer details.
# ==============================================================================

def _tokens(value: object) -> list[str]:
    raw = re.sub(r"(?i)\b4\s+on\s+1\b", "4on1", str(value or ""))
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Split fused camel-case names before case-folding. Real filenames often
    # contain MiaKhalifa, BangBros, TheBabysitter, etc.; without this boundary
    # normalization they cannot corroborate provider names or title clues.
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
    text = text.casefold()
    # V29.4.132: "Don't" and "Dont" are the same word in a filename.
    text = re.sub(r"['\u2019`]", "", text)
    return [t for t in re.findall(r"[a-z0-9]+", text) if len(t) >= 2 and t not in _STOPWORDS]


def _contains_name(filename: str, name: str) -> bool:
    needle = _tokens(name)
    hay = _tokens(filename)
    if not needle or not hay or len(needle) > len(hay):
        return False
    width = len(needle)
    return any(hay[i:i + width] == needle for i in range(len(hay) - width + 1))






# ==============================================================================
# SECTION: Scoring signals
# Filename, title, date and studio similarity.
# ==============================================================================

def _filename_provider_score(filename: str, best: dict) -> tuple[float, int, list[str]]:
    """Return (score, exact performer count, matched performer names).

    Score intentionally rewards provider-confirmed performer names more than
    generic title-token overlap.  This prevents words such as "porn" or
    "video" from turning an arbitrary TPDB candidate into an auto-match.
    """
    performer_names = [str(p.get("name") or "").strip() for p in _performer_details(best)]
    matched = [name for name in performer_names if _contains_name(filename, name)]
    pscore = min(1.0, len(matched) / max(1, min(len(performer_names), 2))) if performer_names else 0.0

    file_tokens = set(_tokens(filename))
    title_tokens = set(_tokens(best.get("title")))
    title_overlap = (len(file_tokens & title_tokens) / len(title_tokens)) if title_tokens else 0.0
    title_similarity = SequenceMatcher(
        None,
        " ".join(sorted(file_tokens)),
        " ".join(sorted(title_tokens)),
    ).ratio() if file_tokens and title_tokens else 0.0
    title_score = max(title_overlap, title_similarity * 0.85)

    # Provider-confirmed performer names are the strongest filename signal.
    score = min(1.0, 0.70 * pscore + 0.30 * title_score)
    return score, len(matched), matched


# A filename match is applied automatically only when the candidate's own
# title words (its performer names removed) are mostly in the filename too.
TITLE_AGREEMENT_FOR_AUTO_MATCH = 0.6


def _title_overlap(filename: str, best: dict) -> float:
    """Share of the candidate title's words that appear in the filename.

    Performer names are removed from the title first: a performer's name in
    the filename says nothing about WHICH of their scenes this is
    (V29.4.132 - "Riley Reid Pool Party" was auto-applied as her scene
    "Office Affair"). A title made only of performer names gives 0.
    """
    names = {t for p in _performer_details(best) for t in _tokens(p.get("name"))}
    title = [t for t in _tokens(best.get("title")) if t not in names]
    if not title:
        return 0.0
    file_tokens = set(_tokens(filename))
    return sum(1 for t in title if t in file_tokens) / len(title)


def _normalized_scene_date(value: object) -> str:
    text = str(value or "").strip()
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?:[-./](\d{1,2})(?:[-./](\d{1,2}))?)?", text)
    if not match:
        return ""
    year, month, day = match.groups()
    if month and day:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return str(year)


def _clean_scene_title(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?i)\.(mp4|mkv|mov|avi|m4v|webm|wmv|flv|ts)$", "", text)
    text = re.sub(r"(?i)\b(4320p|2160p|1440p|1080p|720p|576p|480p|4k|uhd|fhd|h264|h265|x264|x265|hevc|avc|aac|web|web-dl|webrip|bluray|brrip|10bit|8bit)\b", " ", text)
    text = re.sub(r"(?i)\b(starring|title|date|official|xxx|prt|scene|proper|repack|remux|sample|final|uncensored|censored)\b", " ", text)
    text = re.sub(r"(?<!\d)(?:19|20)\d{2}[._ -]\d{1,2}[._ -]\d{1,2}(?!\d)", " ", text)
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text)
    return " ".join(text.split())


def _similarity(left: object, right: object) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    as_text, bs_text = " ".join(a), " ".join(b)
    sequence = SequenceMatcher(None, as_text, bs_text).ratio()
    overlap = len(set(a) & set(b)) / max(1, len(set(b)))
    jaccard = len(set(a) & set(b)) / max(1, len(set(a) | set(b)))
    return min(1.0, max(sequence, overlap * 0.94, jaccard * 1.02))


def _studio_key(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _studio_similarity(left: str, right: str) -> float:
    a, b = _studio_key(left), _studio_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return max(_similarity(left, right), SequenceMatcher(None, a, b).ratio())




# ==============================================================================
# SECTION: Candidate collapsing
# Detects equivalent scenes across providers.
# ==============================================================================

def _same_scene_candidate(left: dict, right: dict) -> bool:
    if scene_sites_conflict(left, right):
        return False
    def clean(value):
        return " ".join(_tokens(_clean_scene_title(str(value or ""))))
    lt, rt = clean(left.get("title")), clean(right.get("title"))
    if lt and rt and lt == rt:
        return True
    ld = _normalized_scene_date(left.get("date") or left.get("release_date") or left.get("production_date"))
    rd = _normalized_scene_date(right.get("date") or right.get("release_date") or right.get("production_date"))
    if not ld or not rd or ld != rd:
        return False
    def cast(value):
        raw = value.get("performer_details") or value.get("performers") or []
        names = []
        for person in raw:
            if isinstance(person, dict):
                names.append(person.get("name") or "")
                aliases = person.get("aliases") or []
                names.extend(aliases if isinstance(aliases, list) else [aliases])
            else:
                names.append(person)
        return {_studio_key(x) for x in names if _studio_key(x)}
    return bool(cast(left) & cast(right))


def _same_content_different_site(left: dict, right: dict) -> bool:
    """Find plausible cross-site variants without merging their provider records."""
    if not scene_sites_conflict(left, right):
        return False

    def clean(value):
        return " ".join(_tokens(_clean_scene_title(str(value or ""))))

    left_title, right_title = clean(left.get("title")), clean(right.get("title"))
    if left_title and right_title and left_title == right_title:
        return True

    left_date = _normalized_scene_date(left.get("date") or left.get("release_date") or left.get("production_date"))
    right_date = _normalized_scene_date(right.get("date") or right.get("release_date") or right.get("production_date"))
    if not left_date or left_date != right_date:
        return False

    def cast(value):
        raw = value.get("performer_details") or value.get("performers") or []
        names = []
        for person in raw:
            if isinstance(person, dict):
                names.append(person.get("name") or "")
                aliases = person.get("aliases") or []
                names.extend(aliases if isinstance(aliases, list) else [aliases])
            else:
                names.append(person)
        return {_studio_key(name) for name in names if _studio_key(name)}

    left_cast, right_cast = cast(left), cast(right)
    shared = len(left_cast & right_cast)
    return bool(shared and shared / max(1, min(len(left_cast), len(right_cast))) >= 0.6)


def _website_variant_indexes(items: list[dict]) -> set[int]:
    """Return likely same-content candidates whose separate sites need review."""
    indexes: set[int] = set()
    for left_index, left in enumerate(items):
        for right_index in range(left_index + 1, len(items)):
            if _same_content_different_site(left, items[right_index]):
                indexes.update((left_index, right_index))
    return indexes


def _collapse_same_scene_candidates(items: list[dict]) -> list[dict]:
    """Merge corroborated duplicate records, but retain records from distinct sites."""
    out: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        match = next((existing for existing in out if _same_scene_candidate(existing, candidate)), None)
        if match is None:
            out.append(candidate)
            continue
        for name, value in candidate.items():
            if match.get(name) in (None, "", [], {}) and value not in (None, "", [], {}):
                match[name] = value
        details, seen = [], set()
        for person in list(match.get("performer_details") or []) + list(candidate.get("performer_details") or []):
            person = dict(person) if isinstance(person, dict) else {"name": str(person or "")}
            person_key = _studio_key(person.get("name"))
            if person_key and person_key not in seen:
                seen.add(person_key); details.append(person)
        if details:
            match["performer_details"] = details
            match["performers"] = [str(person.get("name") or "") for person in details if person.get("name")]
        providers = list(match.get("merged_providers") or [])
        # Preserve the provider that produced the already-kept candidate as
        # well as the incoming provider; both APIs often identify the same
        # scene with different provider-specific IDs.
        for provider in (match.get("source"), match.get("provider"), candidate.get("source"), candidate.get("provider")):
            if provider and str(provider) not in providers:
                providers.append(str(provider))
        if providers:
            match["merged_providers"] = providers
            match["cross_provider_confirmed"] = len(set(x.casefold() for x in providers)) > 1
    return out




# ==============================================================================
# SECTION: Confidence decision
# Evidence, identification and confidence classes.
# ==============================================================================

def _candidate_evidence(filename: str, candidate: dict, context: dict | None = None, hints: dict | None = None) -> dict:
    """Score a scene using every available independent clue.

    The raw quality is normalized over the available clues. The displayed score
    is then capped by evidence breadth so a perfect title-only/date-only match
    ranks well but can never look like a near-certain scene identification.
    """
    context = context if isinstance(context, dict) else {}
    hints = hints if isinstance(hints, dict) else parse_scene_name(filename)
    filename_leaf = str(filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    def list_values(value):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (tuple, list, set)):
            return []
        return [str(item).strip() for item in value if str(item or "").strip()]

    # Normalize candidate cast/studio early so they can be removed from both
    # sides of the title comparison. A filename consisting mostly of a name
    # should count as performer evidence, not falsely boost the scene-title score.
    candidate_performer_details = candidate.get("performer_details") or candidate.get("performers") or []
    candidate_performers = [str(x.get("name") or "").strip() if isinstance(x, dict) else str(x).strip()
                            for x in candidate_performer_details]
    candidate_performers = list(dict.fromkeys(x for x in candidate_performers if x))
    candidate_performer_aliases = []
    for person in candidate_performer_details:
        if isinstance(person, dict):
            aliases = person.get("aliases") or []
            if isinstance(aliases, str): aliases = [aliases]
            candidate_performer_aliases.extend(str(alias).strip() for alias in aliases if str(alias or "").strip())
    candidate_studio = str(candidate.get("studio") or candidate.get("site") or "").strip()
    candidate_studio_aliases = list_values(candidate.get("studio_aliases"))
    for field in ("parent_studio", "parent_network", "network", "studio_parent", "studio_network", "parent_studio_aliases", "parent_network_aliases", "studio_parent_aliases"):
        candidate_studio_aliases.extend(list_values(candidate.get(field)))
    candidate_title = candidate.get("title") or ""

    titles = list_values(hints.get("title_search_variants")) + list_values(context.get("titles"))
    context_title = str(context.get("title") or "").strip()
    filename_label_key = " ".join(_tokens(_clean_scene_title(filename_leaf)))
    titles = [t for t in titles if " ".join(_tokens(_clean_scene_title(t))) != filename_label_key]
    if context_title and " ".join(_tokens(_clean_scene_title(context_title))) != filename_label_key:
        titles.append(context_title)
    parsed_title = str(hints.get("title") or "").strip()
    # When the parser has removed a scene-code token (e.g. OPAQUE_001), its
    # residue is not a trustworthy scene title. It can still be sent to the
    # providers as a search string, but it must not dilute confidence scoring.
    code_prefix = re.sub(r"[^a-z0-9]", "", str(hints.get("code") or "").casefold())
    code_prefix = re.sub(r"\d.*$", "", code_prefix)
    if parsed_title and not (code_prefix and " ".join(_tokens(parsed_title)) == code_prefix):
        titles.append(parsed_title)
    cleaned_filename = _clean_scene_title(filename_leaf)
    if cleaned_filename:
        removable = set()
        for name in (list_values(context.get("performers")) + list_values(context.get("performer_names")) +
                     list_values(context.get("performer_aliases")) + list_values(context.get("studios")) +
                     list_values(context.get("studio")) + list_values(context.get("studio_aliases")) +
                     list_values(hints.get("performers")) + list_values(hints.get("studio")) +
                     candidate_performers + ([candidate_studio] if candidate_studio else [])):
            removable.update(_tokens(name))
        residual_tokens = [token for token in _tokens(cleaned_filename) if token not in removable]
        residual = " ".join(residual_tokens)
        alphabetic_tokens = [token for token in residual_tokens if re.fullmatch(r"[a-z]{2,}", token)]
        if len(residual_tokens) >= 2 and len(alphabetic_tokens) >= 2:
            titles.append(residual)
    # Remove candidate cast/studio tokens from candidate title and title clues,
    # so title correlation measures the scene's descriptive title itself.
    removable_for_candidate_title = set()
    for name in candidate_performers + ([candidate_studio] if candidate_studio else []):
        removable_for_candidate_title.update(_tokens(name))
    dedup_titles = []
    for title in titles:
        cleaned = _clean_scene_title(title)
        title_tokens = [t for t in _tokens(cleaned) if t not in removable_for_candidate_title]
        cleaned_residual = " ".join(title_tokens)
        key = " ".join(_tokens(cleaned_residual))
        if cleaned_residual and key not in {" ".join(_tokens(x)) for x in dedup_titles}:
            dedup_titles.append(cleaned_residual)
    titles = dedup_titles[:5]

    performer_clues = list(dict.fromkeys(
        list_values(context.get("performers")) + list_values(context.get("performer_names")) +
        list_values(hints.get("performers")) + list_values(hints.get("performer_candidates"))
    ))
    performer_aliases = list_values(context.get("performer_aliases"))
    context_studio_clues = list_values(context.get("studios")) + list_values(context.get("studio"))
    # Delimited filename parsers can mistake a performer prefix for a studio,
    # e.g. "Riley Reid - Office Affair.mp4". If the provider candidate confirms
    # that prefix as cast, don't double-count it as contradictory studio evidence.
    hinted_studio_clues = [
        clue for clue in list_values(hints.get("studio"))
        if not any(_similarity(clue, name) >= 0.88 for name in candidate_performers)
    ]
    studio_clues = context_studio_clues + hinted_studio_clues
    studio_aliases = list_values(context.get("studio_aliases"))
    date_values = list_values([hints.get("date") or hints.get("year"), context.get("date"),
                               context.get("production_date"), context.get("release_date"), context.get("year")]) + list_values(context.get("dates"))
    date_clues = list(dict.fromkeys(_normalized_scene_date(d) for d in date_values if _normalized_scene_date(d)))
    raw_code = str(hints.get("code") or context.get("code") or "").strip()
    code = "" if is_generic_scene_code(raw_code) else raw_code.casefold().replace("-", "")

    candidate_title = candidate.get("title") or ""
    candidate_title_tokens = [token for token in _tokens(_clean_scene_title(candidate_title))
                              if token not in removable_for_candidate_title]
    candidate_title_for_score = " ".join(candidate_title_tokens)
    title_score = max((_similarity(t, candidate_title_for_score) for t in titles), default=0.0)

    matched_performers = []
    matched_context_clues = []
    cast_match_names = candidate_performers + candidate_performer_aliases
    def _person_match(clue, names):
        key = _studio_key(clue)
        return any(_studio_key(name) == key or _similarity(clue, name) >= 0.90
                   or (len(key) >= 9 and SequenceMatcher(None, key, _studio_key(name)).ratio() >= 0.93)
                   for name in names if _studio_key(name))
    for clue in performer_clues:
        if _person_match(clue, cast_match_names):
            matched_context_clues.append(clue)
    alias_matches = [alias for alias in performer_aliases
                     if _person_match(alias, cast_match_names)]
    filename_cast_matches = [name for name in candidate_performers if _contains_name(filename_leaf, name)]
    matched_performers = list(dict.fromkeys(matched_context_clues + alias_matches + filename_cast_matches))
    # A named filename/local performer clue that disagrees with every cast name
    # supplied by the provider is a hard stop for automatic application. The
    # visual/PHASH signal can still surface a review candidate, but must not
    # override an explicit cast conflict.
    explicit_filename_clues = list(dict.fromkeys(list_values(hints.get("performers")) + list_values(hints.get("performer_candidates"))))
    normalized_clues = list(dict.fromkeys(_studio_key(value) for value in explicit_filename_clues if _studio_key(value)))
    unmatched_clues = [clue for clue in normalized_clues if not _person_match(clue, cast_match_names)]
    performer_conflict = bool(normalized_clues and candidate_performers and unmatched_clues)
    if performer_clues:
        performer_score = len({x.casefold() for x in matched_context_clues}) / max(1, len({x.casefold() for x in performer_clues}))
        if alias_matches:
            performer_score = max(performer_score, 0.88)
        if filename_cast_matches:
            performer_score = max(performer_score, 0.80)
    elif alias_matches:
        performer_score = 0.88
    else:
        # An exact provider-confirmed cast member in the filename is useful
        # evidence even when no local performer relationship exists yet. Score
        # against candidate cast size: one exact member in a one-person scene
        # is strong evidence, while one match in a multi-performer scene is not.
        denominator = max(1, min(len(candidate_performers), 2))
        performer_score = min(1.0, len(filename_cast_matches) / denominator) * 0.90 if filename_cast_matches else 0.0

    if studio_clues:
        studio_scores = [_studio_similarity(clue, candidate_studio) for clue in studio_clues]
        all_studio_aliases = list(dict.fromkeys(studio_aliases + candidate_studio_aliases))
        for alias in all_studio_aliases:
            studio_scores.extend(_studio_similarity(clue, alias) * 0.98 for clue in studio_clues)
            if candidate_studio:
                studio_scores.append(_studio_similarity(alias, candidate_studio) * 0.96)
        studio_score = max(studio_scores, default=0.0)
    else:
        studio_score = 0.0
        if candidate_studio and _contains_name(filename_leaf, candidate_studio):
            studio_score = 0.92
        elif any(_similarity(alias, candidate_studio) >= 0.88 for alias in studio_aliases):
            studio_score = 0.88

    candidate_date = _normalized_scene_date(candidate.get("date") or candidate.get("release_date") or candidate.get("production_date"))
    date_score = 0.0
    if date_clues and candidate_date:
        for clue in date_clues:
            if clue == candidate_date:
                date_score = max(date_score, 1.0)
            elif len(clue) == 10 and len(candidate_date) == 10:
                try:
                    delta = abs((date.fromisoformat(clue) - date.fromisoformat(candidate_date)).days)
                except ValueError:
                    delta = 999
                if delta <= 1:
                    date_score = max(date_score, 1.0)
                elif delta <= 3:
                    date_score = max(date_score, 0.65)
            elif len(clue) == 4 and candidate_date.startswith(clue):
                date_score = max(date_score, 0.55)
            elif len(candidate_date) == 4 and clue.startswith(candidate_date):
                date_score = max(date_score, 0.45)

    candidate_code = str(candidate.get("code") or "").casefold().replace("-", "")
    hay = " ".join(str(candidate.get(k) or "") for k in ("title", "studio", "site", "profile")).casefold().replace("-", "")
    code_score = 1.0 if code and (code == candidate_code or code in hay) else 0.0

    video_duration = context.get("duration") or context.get("video_duration")
    candidate_duration = candidate.get("duration") or candidate.get("duration_seconds")
    duration_delta = None
    duration_score = 0.0
    try:
        if float(video_duration or 0) > 0 and float(candidate_duration or 0) > 0:
            duration_delta = abs(float(video_duration) - float(candidate_duration))
            duration_score = 1.0 if duration_delta <= 3.0 else max(0.0, 1.0 - (duration_delta - 3.0) / 30.0)
    except (TypeError, ValueError):
        duration_delta = None

    phash_distance = candidate.get("_archive_phash_distance")
    phash_queried = bool(candidate.get("_archive_phash_query_match"))
    try:
        phash_distance = int(phash_distance) if phash_distance is not None else None
    except (TypeError, ValueError):
        phash_distance = None
    phash_score = (max(0.0, 1.0 - phash_distance / 64.0) if phash_distance is not None else (0.91 if phash_queried else 0.0))
    # A provider PHASH query is corroborating only when the API filtered close
    # results itself or a returned fingerprint is actually within the threshold.
    phash_close = bool((phash_distance is not None and phash_distance <= 8)
                       or (phash_queried and phash_distance is None))
    visual_similarity = 0.0
    visual_result = candidate.get("_archive_visual_comparison")
    if isinstance(visual_result, dict):
        visual_similarity = float(visual_result.get("similarity") or 0.0)

    # Fuzzy full-scene agreement catches small filename typos and fused spelling
    # variants, but is intentionally a separately recorded signal.
    clean_combo = _clean_scene_title(filename_leaf)
    clean_combo = re.sub(r"(?<!\d)(?:19|20)\d{2}[._ -]\d{1,2}[._ -]\d{1,2}(?!\d)", " ", clean_combo)
    clean_combo = re.sub(r"(?<!\d)\d{2}[._ -]\d{2}[._ -]\d{2}(?!\d)", " ", clean_combo)
    file_combo = " ".join(_tokens(clean_combo))
    candidate_combo = " ".join(_tokens(" ".join([str(candidate_title or ""), *candidate_performers, candidate_studio])))
    combo_similarity = SequenceMatcher(None, file_combo, candidate_combo).ratio() if file_combo and candidate_combo else 0.0
    if file_combo and candidate_combo:
        file_set, cand_set = set(file_combo.split()), set(candidate_combo.split())
        token_recall = len(file_set & cand_set) / max(1, len(cand_set))
        combo_similarity = max(combo_similarity, token_recall * 0.96)

    dimensions = {}
    # weights reflect the independent evidence; title remains useful but cannot
    # auto-match alone, and dates are corroborating evidence rather than identity.
    if titles:
        dimensions["title"] = (title_score, 0.42)
    if performer_clues or matched_performers:
        dimensions["performer"] = (performer_score, 0.24)
    if studio_clues or studio_score:
        dimensions["studio"] = (studio_score, 0.20)
    if date_clues:
        dimensions["date"] = (date_score, 0.10)
    if code:
        dimensions["code"] = (code_score, 0.04)
    if duration_delta is not None:
        dimensions["duration"] = (duration_score, 0.12)
    if phash_distance is not None or phash_queried:
        dimensions["phash"] = (phash_score, 0.26)
    if visual_result:
        dimensions["visual"] = (visual_similarity, 0.20)
    denom = sum(weight for _score, weight in dimensions.values()) or 1.0
    quality = sum(score * weight for score, weight in dimensions.values()) / denom
    matched = [key for key, (score, _weight) in dimensions.items()
               if score >= ({"title": 0.72, "performer": 0.80, "studio": 0.85, "date": 0.90, "code": 0.98, "duration": 0.90, "phash": 0.85, "visual": 0.90}.get(key, 0.8))]
    available_count = len(dimensions)
    breadth = {0: 0.0, 1: 0.50, 2: 0.68, 3: 0.84, 4: 0.96}.get(available_count, 1.0)
    display_score = quality * breadth
    evidence = []
    if title_score >= 0.72:
        evidence.append(f"title {round(title_score * 100)}%")
    elif titles:
        evidence.append(f"title {round(title_score * 100)}% weak")
    if matched_performers:
        evidence.append("performer " + ", ".join(matched_performers[:3]))
    if studio_score >= 0.85:
        evidence.append(f"studio {candidate_studio} exact/near-exact")
    elif studio_clues:
        evidence.append(f"studio {round(studio_score * 100)}%")
    if date_score >= 0.9:
        evidence.append("date exact/within ±1 day")
    elif date_clues:
        evidence.append(f"date {round(date_score * 100)}%")
    if code_score >= 0.98:
        evidence.append("scene code exact")
    if duration_delta is not None:
        evidence.append(f"duration delta {duration_delta:.1f}s" + (" strong" if duration_delta <= 3.0 else " mismatch" if duration_delta > 15.0 else " partial"))
    if phash_distance is not None:
        evidence.append(f"video pHash distance {phash_distance} bits" + (" close" if phash_distance <= 8 else " weak"))
    elif phash_queried:
        evidence.append("provider returned a PHASH-index match")
    if visual_result:
        evidence.append(f"provider-image visual similarity {visual_similarity:.0%} (frame {visual_result.get('frame_index')})")
    if combo_similarity >= 0.95:
        evidence.append(f"combined filename similarity {combo_similarity:.0%}")
    if candidate.get("cross_provider_confirmed"):
        evidence.append("corroborated by both provider databases")
        quality = min(1.0, quality + 0.035)
        display_score = min(breadth, display_score + 0.035)
    evidence_text = "; ".join(evidence) if evidence else "no strong clue agreement"
    return {
        "quality": round(quality, 4), "score": round(display_score, 4),
        "matched_dimensions": matched, "available_dimensions": list(dimensions),
        "dimension_scores": {key: round(value[0], 4) for key, value in dimensions.items()},
        "evidence": evidence, "evidence_text": evidence_text,
        "matched_performers": matched_performers,
        "duration_delta": round(duration_delta, 3) if duration_delta is not None else None,
        "duration_match": bool(duration_delta is not None and duration_delta <= 3.0),
        "phash_distance": phash_distance, "phash_match": phash_close,
        "visual_similarity": round(visual_similarity, 4), "combined_similarity": round(combo_similarity, 4),
        "performer_conflict": performer_conflict,
    }


async def identify_with_confidence(db, *, oshash: str | None, filename: str,
                                   context: dict | None = None, hash_only: bool = False,
                                   visual_phash: str | None = None) -> dict:
    """Return scored, cross-provider candidates and a guarded confidence class."""
    from .filename_matching import extract_filename_parameters
    hints = extract_filename_parameters(db, filename, context)
    # Keep compatibility with narrow provider adapters used by tests and
    # integrations; only pass optional controls when their callable supports it.
    try:
        import inspect
        signature = inspect.signature(identify)
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    except (TypeError, ValueError):
        signature = None
        accepts_kwargs = False
    kwargs = {"oshash": oshash, "filename": filename}
    if context and (signature is None or "context" in signature.parameters or accepts_kwargs):
        kwargs["context"] = context
    if hash_only and (signature is None or "hash_only" in signature.parameters or accepts_kwargs):
        kwargs["hash_only"] = True
    if signature is None or "filename_parameters" in signature.parameters or accepts_kwargs:
        kwargs["filename_parameters"] = hints
    if visual_phash and (signature is None or "visual_phash" in signature.parameters or accepts_kwargs):
        kwargs["visual_phash"] = visual_phash
    result = await identify(db, **kwargs)
    matches = result.get("matches") or []
    # Provider scene images are deliberately downloaded only when the explicit
    # Metadata & Assets scan sets this flag. Quick-edit and playback paths remain
    # read-only/local and never trigger image requests.
    if matches and isinstance(context, dict) and context.get("_allow_provider_image_fetch") and context.get("_frames_dir"):
        try:
            from .perceptual import compare_scene_image_to_snapshots
            frames_dir = context.get("_frames_dir")
            async def compare_one(candidate):
                url = str(candidate.get("image") or candidate.get("thumbnail") or "").strip()
                if not url.startswith(("https://", "http://")):
                    return
                def download_compare():
                    req = urllib.request.Request(url, headers={"User-Agent": "Archive-X/scene-match", "Accept": "image/*"})
                    with urllib.request.urlopen(req, timeout=8) as response:
                        payload = response.read(4 * 1024 * 1024 + 1)
                    if not payload or len(payload) > 4 * 1024 * 1024:
                        return None
                    return compare_scene_image_to_snapshots(payload, frames_dir)
                try:
                    candidate["_archive_visual_comparison"] = await asyncio.to_thread(download_compare)
                except Exception as exc:
                    candidate["_archive_visual_error"] = str(exc)[:180]
            await asyncio.gather(*(compare_one(candidate) for candidate in matches[:5] if isinstance(candidate, dict)))
        except Exception:
            _swallowed_note("matching.identify_with_confidence")
            pass
    method = result.get("method")
    errors = result.get("errors") or []
    if not matches:
        return {"hints": hints, "confidence": "NO_MATCH", "reason": "no provider candidate",
                "score": 0.0, "best": None, "matches": [], "errors": errors,
                "searched_clues": result.get("searched_clues") or [], "searched_dates": result.get("searched_dates") or [],
                "searched_year": result.get("searched_year")}

    if method and method.endswith("oshash"):
        best = matches[0]
        scenes = {str(m.get("id") or "") for m in matches if str(m.get("id") or "")}
        if len(scenes) > 1:
            website_variants = _website_variant_indexes(matches)
            reason = (
                "Exact file fingerprint appears in records from different websites; review each site record separately."
                if website_variants else
                "File fingerprint matches multiple different scenes; please confirm."
            )
            for index, candidate in enumerate(matches):
                candidate["_archive_match"] = {"score": 0.80, "quality": 0.80,
                    "matched_dimensions": ["hash"], "available_dimensions": ["hash"],
                    "evidence_text": "exact file fingerprint; multiple possible scenes"}
                candidate["_archive_confidence"] = "MEDIUM"
                candidate["_archive_reason"] = reason
                if index in website_variants:
                    candidate["_archive_website_variant_conflict"] = True
            return {"hints": hints, "confidence": "MEDIUM",
                    "reason": reason, "score": 0.8,
                    "best": best, "matches": matches, "errors": errors,
                    "searched_clues": result.get("searched_clues") or [], "searched_dates": result.get("searched_dates") or [],
                "searched_year": result.get("searched_year")}
        best["_archive_match"] = {"score": 1.0, "quality": 1.0,
            "matched_dimensions": ["hash"], "available_dimensions": ["hash"],
            "evidence_text": "exact file fingerprint (OSHASH), checked against configured providers"}
        best["_archive_confidence"] = "HIGH"
        best["_archive_reason"] = "exact file fingerprint match (OSHASH)"
        return {"hints": hints, "confidence": "HIGH",
                "reason": "exact file fingerprint match (OSHASH)", "score": 1.0,
                "best": best, "matches": matches, "errors": errors,
                "searched_clues": result.get("searched_clues") or [], "searched_dates": result.get("searched_dates") or [],
                "searched_year": result.get("searched_year")}

    if "phash" in str(method or "").casefold():
        for candidate in matches:
            candidate["_archive_phash_query_match"] = True
    matches = _collapse_same_scene_candidates(matches)
    website_variant_indexes = _website_variant_indexes(matches)
    website_variant_reason = (
        "Possible same content is listed under different websites; review each site and provider record separately."
    )
    for index in website_variant_indexes:
        matches[index]["_archive_website_variant_conflict"] = True
        matches[index]["_archive_reason"] = website_variant_reason
    ranked = []
    for index, candidate in enumerate(matches):
        evidence = _candidate_evidence(filename, candidate, context, hints=hints)
        candidate["_archive_match"] = evidence
        # Rank primarily by evidence quality (exactness/corroboration), then by
        # the displayed breadth-adjusted score. This keeps a strong, corroborated
        # title match from being displaced by a weaker candidate with a larger
        # set of low-value clues, while still showing operators the same score.
        ranked.append((evidence["quality"], evidence["score"], -index, candidate, evidence))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    best_quality, best_score, _index, best, best_evidence = ranked[0]
    second_quality = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = best_quality - second_quality if len(ranked) > 1 else 1.0
    matched_dims = best_evidence["matched_dimensions"]
    dims = best_evidence["dimension_scores"]
    title_strong = dims.get("title", 0.0) >= 0.82
    second_signal = any(dims.get(k, 0.0) >= v for k, v in (("performer", 0.80), ("studio", 0.85), ("date", 0.90), ("code", 0.98)))
    enough_identity = len([key for key in matched_dims if key in {"title", "performer", "studio", "date", "code"}]) >= 2
    unique_and_clear = margin >= 0.12
    clue_keys = {key for key in matched_dims if key in {"title", "performer", "studio", "date", "code"}}
    studio_date_cast = dims.get("studio", 0.0) >= 0.85 and dims.get("date", 0.0) >= 0.90 and dims.get("performer", 0.0) >= 0.80
    two_clues_plus_duration = len(clue_keys) >= 2 and best_evidence.get("duration_match")
    one_clue_plus_visual = len(clue_keys) >= 1 and dims.get("visual", 0.0) >= 0.90
    close_phash = bool(best_evidence.get("phash_match"))
    combined_fuzzy = float(best_evidence.get("combined_similarity") or 0.0) >= 0.95 and len(clue_keys) >= 2
    title_rule = title_strong and second_signal and enough_identity
    auto_rule = close_phash or studio_date_cast or bool(two_clues_plus_duration) or one_clue_plus_visual or title_rule or combined_fuzzy
    def _strong_rule(evidence):
        dim = evidence.get("dimension_scores") or {}
        matched_keys = {key for key in evidence.get("matched_dimensions") or [] if key in {"title", "performer", "studio", "date", "code"}}
        title_ok = dim.get("title", 0.0) >= 0.82
        second_ok = any(dim.get(k, 0.0) >= v for k, v in (("performer", 0.80), ("studio", 0.85), ("date", 0.90), ("code", 0.98)))
        return (bool(evidence.get("phash_match")) or
                (dim.get("studio", 0.0) >= 0.85 and dim.get("date", 0.0) >= 0.90 and dim.get("performer", 0.0) >= 0.80) or
                (len(matched_keys) >= 2 and bool(evidence.get("duration_match"))) or
                (len(matched_keys) >= 1 and dim.get("visual", 0.0) >= 0.90) or
                (title_ok and second_ok and len(matched_keys) >= 2) or
                (float(evidence.get("combined_similarity") or 0.0) >= 0.95 and len(matched_keys) >= 2))
    only_best_strong = auto_rule and not any(_strong_rule(item[4]) for item in ranked[1:])
    clear_winner = unique_and_clear or only_best_strong
    auto_match = (clear_winner and auto_rule
                  and (best_quality >= 0.62 or close_phash or one_clue_plus_visual)
                  and not best_evidence.get("performer_conflict")
                  and not best.get("cast_conflict")
                  and not website_variant_indexes)
    # HIGH is a candidate-confidence signal only. The pipeline still performs a
    # separate performer-identity conflict check before any automatic application.
    top_confidence = "HIGH" if auto_match else "MEDIUM" if best_quality >= 0.30 and len(matched_dims) >= 1 else "LOW"

    annotated = []
    for rank, (_quality, _score, _idx, candidate, evidence) in enumerate(ranked):
        candidate_conf = "HIGH" if rank == 0 and auto_match else (
            "MEDIUM" if evidence["quality"] >= 0.30 and len(evidence["matched_dimensions"]) >= 1 else "LOW"
        )
        candidate_reason = (
            f"Match score {round(evidence['score'] * 100)}% (quality {round(evidence['quality'] * 100)}%); "
            f"signals: {evidence['evidence_text']}; top margin {round(margin * 100)} points. "
            + ("Automatic rule satisfied and uniquely ranked; performer conflicts are still blocked by the pipeline." if candidate_conf == "HIGH"
               else f"Review required; {'performer conflict' if evidence.get('performer_conflict') else ('the two providers disagree on the cast' if candidate.get('cast_conflict') else 'ambiguous winner' if margin < 0.12 else 'no automatic rule was satisfied')}. "
                    f"Signals checked: title≥82%={title_strong}, studio+date+performer={studio_date_cast}, "
                    f"two clues+duration≤3s={bool(two_clues_plus_duration)}, one clue+visual≥90%={one_clue_plus_visual}, "
                    f"close PHASH={close_phash}, combined≥95%={combined_fuzzy}.")
        )
        if candidate.get("_archive_website_variant_conflict"):
            candidate_reason += " Possible same content is listed under different websites; review this site/provider record separately."
        candidate["_archive_confidence"] = candidate_conf
        candidate["_archive_reason"] = candidate_reason
        annotated.append(candidate)

    reason = best["_archive_reason"]
    if len(ranked) > 1 and margin < 0.12 and not only_best_strong:
        reason += f" Candidate ranking is ambiguous (top-candidate margin {round(margin * 100)} points)."
        top_confidence = "MEDIUM" if best_quality >= 0.30 else "LOW"
        best["_archive_confidence"] = top_confidence
        best["_archive_reason"] = reason
    if website_variant_indexes:
        reason = website_variant_reason
        best["_archive_reason"] = website_variant_reason
    return {"hints": hints, "confidence": top_confidence, "reason": reason,
            "score": best_evidence["score"], "quality": best_quality, "best": best,
            "matches": annotated[:12], "errors": errors,
            "searched_clues": result.get("searched_clues") or [],
            "searched_dates": result.get("searched_dates") or [],
            "searched_year": result.get("searched_year"),
            "provider_searches": result.get("provider_searches") or {}}


def classify_confidence(
    hints: dict,
    best: dict,
    method: str | None,
    *,
    score: float | None = None,
    performer_count: int = 0,
    matched_performers: list[str] | None = None,
    title_overlap: float = 0.0,
) -> tuple[str, str]:
    """Classify a provider candidate without trusting generic title words.

    HIGH is still hash-authoritative, but a filename candidate can also be
    HIGH when the provider confirms at least one exact performer name and the
    candidate title has meaningful filename correlation, or when two or more
    provider performers are explicitly present in the filename.  MEDIUM
    remains review-required when only performer or code evidence is present.
    """
    if method and method.endswith("oshash"):
        return "HIGH", "exact file fingerprint match (OSHASH)"

    matched_performers = matched_performers or []
    score = float(score or 0.0)
    if method == "tpdb filename parse" and matched_performers:
        # V29.4.132: performer names alone never make a filename match HIGH;
        # the scene's own title has to agree as well (see _title_overlap).
        title_agrees = float(title_overlap or 0.0) >= TITLE_AGREEMENT_FOR_AUTO_MATCH
        if title_agrees and ((performer_count >= 2 and score >= 0.55) or (performer_count >= 1 and score >= 0.62)):
            names = ", ".join(matched_performers[:3])
            return "HIGH", f"filename/provider agreement: performer name matched ({names}) and scene title correlated"
        names = ", ".join(matched_performers[:3])
        return "MEDIUM", f"filename/provider agreement: performer name matched ({names}); confirmation recommended"

    code = str(hints.get("code") or "").strip().upper()
    if code:
        haystacks = [
            str(best.get("title") or ""),
            str(best.get("studio") or ""),
            str(best.get("site") or ""),
        ]
        code_bare = code.replace("-", "")
        for h in haystacks:
            hu = h.upper()
            if code in hu or (code_bare and code_bare in hu.replace("-", "")):
                return "MEDIUM", f"filename scene code {code} matches provider record"

    if method == "tpdb filename parse" and score >= 0.55:
        return "MEDIUM", "provider filename candidate has strong title correlation; confirmation recommended"

    return "LOW", "filename heuristic only — please confirm"
