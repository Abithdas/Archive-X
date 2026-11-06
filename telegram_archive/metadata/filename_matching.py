"""Conservative four-field identity extraction from video filenames.

The parser returns *clues*, not trusted metadata. Existing local performer and
studio rows (including known aliases) can be recognized irrespective of order;
ambiguous names are exposed for review rather than guessed. Provider results are
still scored by metadata/matching.py before anything is applied.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Text normalization
#   - Entity catalog matching
#   - Field extraction
# ------------------------------------------------------------------
from __future__ import annotations

import json
import re
import unicodedata
import threading
import time
from collections import Counter
from difflib import SequenceMatcher
from datetime import date
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .parser import clean_text, parse_scene_name, split_fused_quality_before_date

_VIDEO_EXT_RE = re.compile(r"\.(?:mp4|m4v|mkv|mov|avi|wmv|webm|ts|flv|mpg|mpeg)$", re.I)
_FULL_DATE_YMD = re.compile(r"(?<!\d)((?:19|20)\d{2})[._/ -](\d{1,2})[._/ -](\d{1,2})(?!\d)")
_FULL_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[._/ -](\d{1,2})[._/ -]((?:19|20)\d{2})(?!\d)")
_SHORT_DATE = re.compile(r"(?<!\d)(\d{2})[._/ -](\d{2})[._/ -](\d{2})(?!\d)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_CAMEL_1 = re.compile(r"(?<=[a-z])(?=[A-Z])")
_CAMEL_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_NOISE_RE = re.compile(
    r"(?i)\b(?:2160p|1440p|1080p|720p|576p|480p|4320p|4k|uhd|fhd|hd|"
    r"x264|x265|h264|h265|hevc|avc|aac|web[-_ ]?dl|bluray|brrip|webrip|"
    r"hdr|10bit|8bit|proper|repack|remux|xxx|prt|sample|final|uncensored|censored|web|webrip|brrip|mp4|mkv|avi|mov|wmv|webm)\b"
)
_LABEL_RE = re.compile(r"(?i)(?:^|[\s._\-\[\]()])(?:performers?|starring|cast|studio|site|title|release[ _-]?date|date|year)\s*[:=_-]\s*")
_LABEL_HEAD_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:performers?|starring|cast|studio|site|title|release[ _-]?date|date|year)\s*[:=_-]\s*")




# ==============================================================================
# SECTION: Text normalization
# Tokens, folding and dates.
# ==============================================================================

def _split_camel(value: str) -> str:
    return _CAMEL_2.sub(" ", _CAMEL_1.sub(" ", value or ""))


def _fold(value: object) -> str:
    raw = _split_camel(str(value or ""))
    raw = re.sub(r"(?i)\b4\s+on\s+1\b", "4on1", raw)
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _compact(value: object) -> str:
    return "".join(_fold(value).split())


def _tokens(value: object) -> list[str]:
    folded = _fold(value)
    return folded.split() if folded else []


def _leaf(filename: str) -> str:
    raw = str(filename or "").strip()
    # Handles both Windows paths and Unix paths regardless of host OS.
    return PureWindowsPath(raw).name if "\\" in raw else PurePosixPath(raw).name


def _valid_date(year: int, month: int, day: int) -> str | None:
    try:
        if not 1990 <= year <= 2100:
            return None
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_dates(stem: str, parsed: dict) -> tuple[str | None, int | None, list[int]]:
    explicit = _LABEL_HEAD_RE.search(stem)
    if explicit and re.search(r"(?i)\b(?:release[ _-]?date|date)\s*[:=_-]\s*$", explicit.group(0)):
        after = stem[explicit.end():]
        candidate = _FULL_DATE_YMD.search(after) or _FULL_DATE_DMY.search(after)
    else:
        candidate = _FULL_DATE_YMD.search(stem) or _FULL_DATE_DMY.search(stem)
    full_date = None
    if candidate:
        try:
            groups = candidate.groups()
            if len(groups) == 3 and len(groups[0]) == 4:
                full_date = _valid_date(int(groups[0]), int(groups[1]), int(groups[2]))
            else:
                full_date = _valid_date(int(groups[2]), int(groups[1]), int(groups[0]))
        except (ValueError, TypeError):
            full_date = None
    if not full_date:
        old = str(parsed.get("date") or "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", old):
            full_date = old
    year_values = sorted({int(v) for v in _YEAR_RE.findall(stem) if 1990 <= int(v) <= 2100})
    if full_date:
        year = int(full_date[:4])
    else:
        year_label = re.search(r"(?i)\b(?:release[ _-]?date|date|release|year)\s*[:=_-]*\s*((?:19|20)\d{2})\b", stem)
        bracket_year = re.search(r"[\[(]\s*((?:19|20)\d{2})\s*[\])]", stem)
        # An unlabelled year is safe only when there is one distinct year in the filename.
        year = int(year_label.group(1)) if year_label else (
            int(bracket_year.group(1)) if bracket_year else (year_values[0] if len(year_values) == 1 else None)
        )
    return full_date, year, year_values


def _decode_aliases(raw: object) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (TypeError, ValueError):
            raw = []
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(value).strip() for value in raw if str(value or "").strip()]


_ENTITY_CACHE_LOCK = threading.RLock()
_ENTITY_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_ENTITY_CACHE_TTL_SECONDS = 30.0




# ==============================================================================
# SECTION: Entity catalog matching
# Finds known entities in filenames.
# ==============================================================================

def _entity_catalog(db, table: str) -> dict[str, Any]:
    """Cache parsed entity aliases briefly; a 9,000-video scan must not reload
    every performer and studio row for every filename. The TTL allows edits to
    become visible quickly without tying catalog mutations to this parser."""
    key = (id(db), table)
    now = time.monotonic()
    with _ENTITY_CACHE_LOCK:
        cached = _ENTITY_CACHE.get(key)
        if cached and now - cached[0] < _ENTITY_CACHE_TTL_SECONDS:
            return cached[1]
    lock = getattr(db, "lock", None)
    try:
        if lock is None:
            raw_rows = db.conn.execute(
                f"SELECT id,name,COALESCE(aliases,'[]') AS aliases FROM {table} ORDER BY id"
            ).fetchall()
        else:
            with lock:
                raw_rows = db.conn.execute(
                    f"SELECT id,name,COALESCE(aliases,'[]') AS aliases FROM {table} ORDER BY id"
                ).fetchall()
    except Exception:
        return {"rows": {}, "aliases": [], "by_key": {}, "owners": {}, "trigrams": {}}

    rows: dict[int, dict] = {}
    alias_entries: list[dict] = []
    by_key: dict[tuple[str, ...], list[tuple[int, str]]] = {}
    owners: dict[str, list[int]] = {}
    trigrams: dict[str, list[int]] = {}
    for raw in raw_rows:
        row = dict(raw)
        name = str(row.get("name") or "").strip()
        if not name or _compact(name) in {"unknownperformer", "unknownstudio", "unknown", "none", "null"}:
            continue
        try:
            entity_id = int(row["id"])
        except (ValueError, TypeError, KeyError):
            continue
        rows[entity_id] = row
        aliases = list(dict.fromkeys([name, *_decode_aliases(row.get("aliases"))]))
        for alias in aliases:
            alias_tokens = tuple(_tokens(alias))
            if not alias_tokens or (len(alias_tokens) == 1 and len(alias_tokens[0]) < 4):
                continue
            alias_key = " ".join(alias_tokens)
            by_key.setdefault(alias_tokens, []).append((entity_id, alias))
            owners.setdefault(alias_key, [])
            if entity_id not in owners[alias_key]:
                owners[alias_key].append(entity_id)
            norm = " ".join(alias_tokens)
            if len("".join(alias_tokens)) >= 6:
                idx = len(alias_entries)
                grams = set(norm[i:i + 3] for i in range(max(0, len(norm) - 2)))
                alias_entries.append({"id": entity_id, "alias": alias, "tokens": alias_tokens,
                                      "norm": norm, "grams": grams})
                for gram in grams:
                    trigrams.setdefault(gram, []).append(idx)
    result = {"rows": rows, "aliases": alias_entries, "by_key": by_key,
              "owners": owners, "trigrams": trigrams}
    with _ENTITY_CACHE_LOCK:
        _ENTITY_CACHE[key] = (now, result)
        # Avoid stale references to closed/short-lived database objects.
        if len(_ENTITY_CACHE) > 32:
            for old_key, (timestamp, _value) in list(_ENTITY_CACHE.items()):
                if now - timestamp > _ENTITY_CACHE_TTL_SECONDS * 4 or len(_ENTITY_CACHE) > 64:
                    _ENTITY_CACHE.pop(old_key, None)
    return result


def _match_catalog_entities(db, table: str, filename: str) -> tuple[list[dict], list[str]]:
    """Find known performers or studios in a filename; returns matches and clues."""
    if db is None or not hasattr(db, "conn"):
        return [], []
    catalog = _entity_catalog(db, table)
    rows = catalog["rows"]
    if not rows:
        return [], []
    tokens = _tokens(filename)
    if not tokens:
        return [], []
    matched_aliases: dict[int, list[str]] = {}
    found_phrase_keys: set[str] = set()
    # Exact phrase lookup via token windows avoids scanning every alias for every
    # filename. CamelCase, underscores, aliases and separator variants all fold
    # to the same token sequence before lookup.
    max_width = min(8, max((len(key_tokens) for key_tokens in catalog["by_key"]), default=1))
    for start in range(len(tokens)):
        for width in range(1, min(max_width, len(tokens) - start) + 1):
            phrase = tuple(tokens[start:start + width])
            aliases = catalog["by_key"].get(phrase)
            if not aliases:
                continue
            phrase_key = " ".join(phrase)
            found_phrase_keys.add(phrase_key)
            for entity_id, alias in aliases:
                matched_aliases.setdefault(entity_id, []).append(alias)

    # Repair small misspellings using a character-trigram candidate index, then
    # apply a high SequenceMatcher threshold to the small candidate set. This is
    # much cheaper than comparing every catalog name against every filename
    # window, and still catches misspelled surnames and one-token changes.
    fuzzy_hits: dict[int, tuple[float, str]] = {}
    alias_entries = catalog["aliases"]
    trigram_index = catalog["trigrams"]
    if alias_entries:
        for start in range(len(tokens)):
            for width in range(1, min(5, len(tokens) - start) + 1):
                sample = " ".join(tokens[start:start + width])
                if len(sample.replace(" ", "")) < 6:
                    continue
                grams = set(sample[i:i + 3] for i in range(max(0, len(sample) - 2)))
                if not grams:
                    continue
                overlap_counts: Counter[int] = Counter()
                for gram in grams:
                    for alias_index in trigram_index.get(gram, ()):
                        entry = alias_entries[alias_index]
                        if abs(len(entry["tokens"]) - width) <= 1 and entry["id"] not in matched_aliases:
                            overlap_counts[alias_index] += 1
                min_overlap = max(2, int(len(grams) * 0.22))
                for alias_index, overlap in overlap_counts.items():
                    if overlap < min_overlap:
                        continue
                    entry = alias_entries[alias_index]
                    score = SequenceMatcher(None, sample, entry["norm"]).ratio()
                    threshold = 0.86 if len(entry["tokens"]) == 1 else 0.90
                    entity_id = int(entry["id"])
                    if score >= threshold and score > fuzzy_hits.get(entity_id, (0.0, ""))[0]:
                        fuzzy_hits[entity_id] = (score, str(entry["alias"]))
    ambiguous_names: list[str] = []
    if fuzzy_hits:
        ranked_hits = sorted(fuzzy_hits.items(), key=lambda item: item[1][0], reverse=True)
        top_score = ranked_hits[0][1][0]
        winners = [item for item in ranked_hits if top_score - item[1][0] < 0.035]
        if len(winners) == 1:
            entity_id, (_score, alias) = winners[0]
            matched_aliases.setdefault(entity_id, []).append(alias)
        else:
            ambiguous_names.extend(str(rows[item[0]].get("name") or "") for item in winners)

    ambiguous_ids: set[int] = set()
    for key, ids in catalog["owners"].items():
        if len(ids) > 1 and key in found_phrase_keys:
            ambiguous_ids.update(ids)
    result = []
    for entity_id, aliases in matched_aliases.items():
        row = rows[entity_id]
        alias_keys = {" ".join(_tokens(alias)) for alias in aliases}
        is_unique_signal = any(len(catalog["owners"].get(key, [])) == 1 for key in alias_keys)
        if entity_id in ambiguous_ids and not is_unique_signal:
            ambiguous_names.append(str(row.get("name") or ""))
            continue
        result.append({"id": entity_id, "name": str(row.get("name") or ""),
                       "aliases": _decode_aliases(row.get("aliases")), "matched_aliases": aliases})
    result.sort(key=lambda item: item["id"])
    return result, list(dict.fromkeys(ambiguous_names))



# ==============================================================================
# SECTION: Field extraction
# Named values, titles and the public extractor.
# ==============================================================================

def _label_value(stem: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label).replace(r"\ ", r"[ _-]") for label in labels)
    match = re.search(rf"(?i)(?:^|[\s._\-\[\]()])(?:{label_pattern})\s*[:=_-]\s*", stem)
    if not match:
        return ""
    value = stem[match.end():]
    next_label = _LABEL_HEAD_RE.search(value)
    if next_label:
        value = value[:next_label.start()]
    value = re.split(r"[\[\](){}]", value, maxsplit=1)[0]
    value = _VIDEO_EXT_RE.sub("", value)
    return " ".join(_split_camel(value).replace("_", " ").replace(".", " ").split()).strip(" -_:")


def _strip_named_values(text: str, names: list[str]) -> str:
    result = _split_camel(text)
    # Longest first prevents a short alias from fragmenting a longer one.
    for name in sorted((x for x in names if str(x or "").strip()), key=len, reverse=True):
        compact_name = _compact(name)
        if not compact_name:
            continue
        pattern = r"(?<![A-Za-z0-9])" + r"[\W_]*".join(re.escape(ch) for ch in compact_name) + r"(?![A-Za-z0-9])"
        result = re.sub(pattern, " ", result, flags=re.I)
    return result


def _clean_title(stem: str, names: list[str], selected_year: int | None, full_date: str | None) -> str:
    text = _strip_named_values(stem, names)
    text = _split_camel(text).replace("_", " ").replace(".", " ")
    text = _VIDEO_EXT_RE.sub("", text)
    text = _FULL_DATE_YMD.sub(" ", text)
    text = _FULL_DATE_DMY.sub(" ", text)
    if full_date:
        for value in (full_date, full_date.replace("-", "."), full_date.replace("-", "_"), full_date.replace("-", " ")):
            text = re.sub(r"(?<!\d)" + re.escape(value) + r"(?!\d)", " ", text, flags=re.I)
        # The parser accepts YY_MM_DD (e.g. 23_01_17 => 2023-01-17), but the
        # title cleaner historically removed only four-digit dates. Remove a
        # short date only when it resolves to the already-validated date clue.
        for short in list(_SHORT_DATE.finditer(text))[::-1]:
            yy, month, day = short.groups()
            if _valid_date(2000 + int(yy), int(month), int(day)) == full_date:
                text = text[:short.start()] + " " + text[short.end():]
    if selected_year:
        text = re.sub(r"(?<!\d)" + str(int(selected_year)) + r"(?!\d)", " ", text)
    text = split_fused_quality_before_date(text)
    text = _NOISE_RE.sub(" ", text)
    text = _LABEL_HEAD_RE.sub(" ", text)
    # Strip obvious technical metadata, sizes and generic labels, not arbitrary digits
    # which may be part of a real scene title.
    text = re.sub(r"(?i)\b(?:1080|720|2160|480|576|1440)\s*p\b|\b\d+(?:\.\d+)?\s*(?:gb|mb)\b", " ", text)
    text = re.sub(r"\b(?:proper|repack|sample|final|uncensored|censored|official)\b", " ", text, flags=re.I)
    # A conjunction left behind after removing a cast list is not part of the title.
    text = re.sub(r"(?i)^\s*(?:and|with|featuring|feat)\b\s*|\s*\b(?:and|with|featuring|feat)\s*$", " ", text)
    text = re.sub(r"[\[\](){}]+", " ", text)
    text = re.sub(r"[^\w\s'-]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"[_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_[]()")
    # Remove trailing residual labels or punctuation.
    return text if len(_tokens(text)) >= 2 else ""


def _structured_performer_clue(stem: str, parsed: dict, full_date: str | None) -> str | None:
    """Return a search-only leading name clue for Studio_Date_Name_Title filenames.

    This is never treated as a verified performer or used to create an entity.
    Provider-returned cast must confirm it before matching can gain performer
    evidence. It helps with releases such as
    ``Studio_23_01_17_Daniela_Garcia_Sexy_Teen.mp4`` when the performer is not
    yet present in the local catalogue.
    """
    studio = str(parsed.get("studio") or "").strip()
    if not studio or not full_date:
        return None
    date_match = _FULL_DATE_YMD.search(stem) or _FULL_DATE_DMY.search(stem) or _SHORT_DATE.search(stem)
    if not date_match:
        return None
    prefix = stem[:date_match.start()].strip(" .-_[]()")
    if _compact(prefix) != _compact(studio):
        return None
    suffix = stem[date_match.end():].strip(" .-_[]()")
    suffix = _split_camel(suffix).replace("_", " ").replace(".", " ")
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*", suffix)
    # Require enough trailing words that a leading two-word phrase is only a
    # tentative clue, not the whole scene title. It stays reviewable.
    if len(words) < 4 or len(words[0]) < 2 or len(words[1]) < 2:
        return None
    if words[0].casefold().strip(" .-") in {"a", "an", "the", "part", "episode", "ep", "volume", "vol", "chapter", "clip", "scene"}:
        return None
    return " ".join(word.strip(" .-") for word in words[:2]).strip() or None


def extract_filename_parameters(db: Any, filename: str, context: dict | None = None) -> dict:
    """Extract title, performer(s), studio and release date/year as normalized clues.

    Known local catalog names and aliases are used only when they match complete
    token sequences; ambiguous name ownership is surfaced rather than guessed.
    The return value is safe to use as search evidence but not to create entities.
    """
    raw = clean_text(_leaf(filename))
    stem = _VIDEO_EXT_RE.sub("", raw)
    parsed = parse_scene_name(raw)
    context = context if isinstance(context, dict) else {}
    full_date, year, years = _parse_dates(stem, parsed)

    performers, ambiguous_performers = _match_catalog_entities(db, "performers", stem)
    studios, ambiguous_studios = _match_catalog_entities(db, "studios", stem)
    labelled_performers = _label_value(stem, ("performer", "performers", "starring", "cast"))
    labelled_studio = _label_value(stem, ("studio", "site"))
    labelled_title = _label_value(stem, ("title",))

    performer_names = [str(item["name"]) for item in performers]
    performer_names.extend(str(value).strip() for value in (parsed.get("performers") or []) if str(value or "").strip())
    if labelled_performers:
        # Use explicit role labels as search clues. Do not promote them to local
        # records; provider identity and confidence rules still govern creation.
        values = re.split(r"\s*(?:,|&|\band\b|\+|;)\s*", labelled_performers, flags=re.I)
        performer_names.extend(" ".join(_split_camel(value).replace("_", " ").replace(".", " ").split()).strip()
                               for value in values if value.strip())
    performer_names = list(dict.fromkeys(name for name in performer_names if name))
    inferred_performer = _structured_performer_clue(stem, parsed, full_date)
    performer_candidates = []
    if inferred_performer and not any(_compact(inferred_performer) == _compact(value) for value in performer_names):
        performer_candidates.append(inferred_performer)

    studio_name = None
    studio_candidates = [str(item["name"]) for item in studios]
    if len(studios) == 1:
        studio_name = studio_candidates[0]
    elif len(studios) > 1:
        # Multiple known studio mentions cannot establish which one produced the scene.
        studio_name = None
    elif labelled_studio:
        studio_name = labelled_studio
    else:
        parsed_studio = str(parsed.get("studio") or "").strip()
        # A dash-prefix often is a performer, not a studio. Suppress that fallback
        # if it resolves to a performer already detected from the catalog.
        same_as_performer = any(_compact(parsed_studio) == _compact(value) for value in performer_names)
        if parsed_studio and not same_as_performer:
            studio_name = parsed_studio
            if studio_name not in studio_candidates:
                studio_candidates.append(studio_name)
        else:
            # A leading bracket is a common studio convention. Treat it as a
            # searchable clue, not a newly trusted entity: later candidate scoring
            # checks whether provider studio or cast metadata agrees. Ignore obvious
            # technical labels such as [1080p], [WEB-DL], and bracketed years.
            bracket = re.match(r"^\s*\[([^\]]{2,70})\]", stem)
            bracket_value = str(bracket.group(1) or "").strip() if bracket else ""
            if (bracket_value and not _NOISE_RE.search(bracket_value)
                    and not _YEAR_RE.search(bracket_value)
                    and not _FULL_DATE_YMD.search(bracket_value)
                    and not _FULL_DATE_DMY.search(bracket_value)
                    and not any(_compact(bracket_value) == _compact(value) for value in performer_names)):
                studio_name = bracket_value
                if studio_name not in studio_candidates:
                    studio_candidates.append(studio_name)

    names_to_strip = list(performer_names)
    names_to_strip.extend(performer_candidates)
    names_to_strip.extend(studio_candidates)
    names_to_strip.extend(str(x) for x in parsed.get("performers") or [] if x)
    if parsed.get("studio"):
        names_to_strip.append(str(parsed["studio"]))
    cleaned_residual = _clean_title(stem, list(dict.fromkeys(names_to_strip)), year, full_date)
    title_search_variants = []
    if cleaned_residual and performer_candidates:
        # Prioritize a query with the tentative leading name removed, but also
        # retain the full title so titles that happen to start with two words
        # are not silently truncated by a filename heuristic.
        first, second = performer_candidates[0].split(" ", 1)
        prefix_pattern = rf"(?i)^\s*{re.escape(first)}[\W_]+{re.escape(second)}(?:\b|$)"
        stripped_title = re.sub(prefix_pattern, " ", cleaned_residual, count=1).strip(" .-_[]()")
        if len(_tokens(stripped_title)) >= 2 and _compact(stripped_title) != _compact(cleaned_residual):
            title_search_variants.append(stripped_title)
    if cleaned_residual:
        title_search_variants.append(cleaned_residual)
    if labelled_title:
        title = labelled_title
    elif cleaned_residual:
        # Keep the inclusive residual as the display clue. Alternative cleaner
        # variants are used to prioritize provider queries without losing words.
        title = cleaned_residual
    else:
        parsed_title = str(parsed.get("title") or "").strip()
        entity_keys = {_compact(value) for value in [*performer_names, *studio_candidates] if value}
        title = None if parsed_title and _compact(parsed_title) in entity_keys else (parsed_title or None)

    code = parsed.get("code")
    if code:
        prefix = str(code).split("-", 1)[0]
        if any(_compact(value).startswith(_compact(prefix)) for value in [*performer_names, *studio_candidates] if value):
            code = None

    return {
        **parsed,
        "original": raw,
        "code": code,
        "title": title,
        "performers": performer_names,
        "performer_candidates": performer_candidates,
        "title_search_variants": list(dict.fromkeys(title_search_variants)),
        "studio": studio_name,
        "studio_candidates": list(dict.fromkeys(studio_candidates)),
        "date": full_date,
        "year": year,
        "years": years,
        "ambiguous_performer_names": ambiguous_performers,
        "ambiguous_studio_names": ambiguous_studios,
        "known_performer_ids": [int(item["id"]) for item in performers],
        "known_studio_id": int(studios[0]["id"]) if len(studios) == 1 else None,
        "parameters_found": {
            "title": bool(title), "performer": bool(performer_names or performer_candidates),
            "studio": bool(studio_name), "date_or_year": bool(full_date or year),
        },
        "labelled_fields": {"performers": bool(labelled_performers), "studio": bool(labelled_studio), "title": bool(labelled_title)},
    }
