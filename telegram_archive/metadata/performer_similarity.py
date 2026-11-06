"""Metadata-based performer similarity scoring.

Only the requested profile attributes participate in ranking: gender, country,
age, ethnicity, hair color, breast type, and cup size. Missing values are not
mismatches; they reduce evidence coverage rather than receiving a zero score.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Iterable

ATTRIBUTE_LABELS = {
    "gender": "Gender",
    "country": "Country",
    "age": "Age",
    "ethnicity": "Ethnicity",
    "hair_color": "Hair color",
    "breast_type": "Breast type",
    "cup_size": "Cup size",
}

# Gender is a hard eligibility criterion, and also appears in the match evidence.
FIELD_WEIGHTS = {
    "gender": 1.0,
    "country": 1.2,
    "age": 1.2,
    "ethnicity": 1.2,
    "hair_color": 1.0,
    "breast_type": 1.1,
    "cup_size": 1.1,
}

_FIELD_ALIASES = {
    "gender": {"gender", "sex"},
    "country": {"country", "nationality", "country_of_origin", "country_of_birth", "origin_country"},
    "birth_date": {"birth_date", "birthdate", "date_of_birth", "born"},
    "age": {"age", "current_age", "performer_age"},
    "ethnicity": {"ethnicity", "ethnic_group", "ethnic_origin"},
    "hair_color": {"hair_color", "hair_colour", "haircolor", "hair_colour", "hair"},
    "breast_type": {"breast_type", "breasttype", "breasts", "breast"},
    "cup_size": {"cup_size", "cupsize", "cup"},
}
_ALIAS_TO_FIELD = {alias: field for field, aliases in _FIELD_ALIASES.items() for alias in aliases}

_COUNTRY_ALIASES = {
    "us": "united states", "usa": "united states", "u s a": "united states",
    "united states of america": "united states", "america": "united states",
    "uk": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
    "england": "united kingdom", "scotland": "united kingdom", "wales": "united kingdom",
    "czechia": "czech republic", "russian federation": "russia",
    "republic of korea": "south korea", "korea south": "south korea",
}
_HAIR_ALIASES = {
    "blond": "blonde", "blonde hair": "blonde", "blond hair": "blonde",
    "brunette": "brown", "brown hair": "brown", "brown haired": "brown",
    "redhead": "red", "red hair": "red", "ginger": "red",
    "grey": "gray", "gray hair": "gray", "grey hair": "gray",
    "black hair": "black", "white hair": "white",
}
_BREAST_ALIASES = {
    "fake": "augmented", "implants": "augmented", "implant": "augmented",
    "enhanced": "augmented", "artificial": "augmented", "augmented breasts": "augmented",
    "real": "natural", "natural breasts": "natural", "unaugmented": "natural",
}


def _field_key(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").casefold()
    return _ALIAS_TO_FIELD.get(text, text)


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _provider_priority(row: Any) -> tuple[int, str]:
    try:
        provider = str(row["provider"] or "").strip().casefold()
    except (KeyError, TypeError, IndexError):
        provider = ""
    if provider == "stashdb":
        return (0, provider)
    if provider in {"theporndb", "tpdb", "porn_db"}:
        return (1, provider)
    return (2, provider)


def extract_performer_details(metadata_rows: Iterable[Any]) -> dict[str, Any]:
    """Merge canonical profile clues, preferring StashDB and filling its gaps."""
    result: dict[str, Any] = {}
    rows = sorted(list(metadata_rows or []), key=_provider_priority)
    for row in rows:
        try:
            raw = row["normalized"]
        except (KeyError, TypeError, IndexError):
            raw = row.get("normalized") if isinstance(row, dict) else row
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            continue
        details = payload.get("details") if isinstance(payload.get("details"), dict) else payload
        entries = list(details.items())
        # Some providers expose profile fields directly at the payload root,
        # while others store them under details. Use the root as a fallback.
        if details is not payload:
            entries.extend((k, v) for k, v in payload.items() if k not in {"details", "id", "name", "aliases", "images"})
        for raw_key, value in entries:
            key = _field_key(raw_key)
            if key == "measurements" and isinstance(value, dict):
                for measure_key, measure_value in value.items():
                    canonical = _field_key(measure_key)
                    if canonical in {"cup_size"} and _present(measure_value) and not _present(result.get(canonical)):
                        result[canonical] = measure_value
                continue
            if key not in _FIELD_ALIASES and key not in {"gender", "country", "birth_date", "age", "ethnicity", "hair_color", "breast_type", "cup_size"}:
                continue
            if _present(value) and not _present(result.get(key)):
                result[key] = value
    return result


def normalize_gender(value: object) -> str:
    text = re.sub(r"[^a-z]+", " ", str(value or "").casefold()).strip()
    aliases = {
        "f": "female", "woman": "female", "women": "female", "female": "female",
        "m": "male", "man": "male", "men": "male", "male": "male",
    }
    return aliases.get(text, text)


def _simple_text(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        values = [_simple_text(item) for item in value]
        return " ".join(x for x in values if x)
    if isinstance(value, dict):
        for key in ("name", "label", "value", "title"):
            if _present(value.get(key)):
                return _simple_text(value[key])
        return ""
    text = str(value or "").strip().casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_attribute(field: str, value: object) -> str:
    """Normalize a categorical value for stable comparison across providers."""
    text = _simple_text(value)
    if not text:
        return ""
    if field == "gender":
        return normalize_gender(text)
    if field == "country":
        return _COUNTRY_ALIASES.get(text, text)
    if field == "hair_color":
        return _HAIR_ALIASES.get(text, text)
    if field == "breast_type":
        return _BREAST_ALIASES.get(text, text)
    if field == "cup_size":
        text = re.sub(r"\bcup\b", " ", text)
        text = " ".join(text.split())
        # Handle values such as "34DD" without confusing the band size for
        # the cup size. Plain values such as "DD" remain unchanged.
        m = re.fullmatch(r"\d{1,2}\s*([a-z]{1,3})", text)
        if m:
            text = m.group(1)
        return text.replace(" ", "")
    return text


def _parse_age(value: object) -> float | None:
    if not _present(value):
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace("years", "").replace("year", "").strip())
        if 0 <= number <= 120:
            return number
    except (TypeError, ValueError):
        pass
    match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)", str(value))
    if match:
        try:
            number = float(match.group(1))
            if 0 <= number <= 120:
                return number
        except ValueError:
            return None
    return None


def _age_from_details(details: dict[str, Any], today: _dt.date | None = None) -> float | None:
    today = today or _dt.date.today()
    raw = str(details.get("birth_date") or "").strip()
    if raw:
        raw = raw[:10]
        try:
            born = _dt.date.fromisoformat(raw)
            age = (today - born).days / 365.2425
            if 0 <= age <= 120:
                return age
        except ValueError:
            # A year-only provider field still supplies useful approximate age.
            match = re.fullmatch(r"(18\d{2}|19\d{2}|20\d{2})", raw)
            if match:
                age = today.year - int(match.group(1)) - 0.5
                if 0 <= age <= 120:
                    return age
    return _parse_age(details.get("age"))


def score_performer_similarity(
    target_gender: object,
    target_details: dict[str, Any],
    candidate_gender: object,
    candidate_details: dict[str, Any],
    *,
    today: _dt.date | None = None,
) -> dict[str, Any] | None:
    """Score one candidate on the seven requested attributes.

    A candidate must share the known gender and have at least one meaningful
    match among the other six attributes. Missing data is excluded from the
    comparison, not counted as a mismatch.
    """
    gender_a = normalize_gender(target_gender or target_details.get("gender"))
    gender_b = normalize_gender(candidate_gender or candidate_details.get("gender"))
    if gender_a not in {"male", "female"} or gender_a != gender_b:
        return None

    scores: dict[str, float] = {"gender": 1.0}
    compared = ["gender"]
    matched = ["gender"]
    target_age = _age_from_details(target_details, today)
    candidate_age = _age_from_details(candidate_details, today)

    categorical = ("country", "ethnicity", "hair_color", "breast_type", "cup_size")
    for field in categorical:
        a = normalize_attribute(field, target_details.get(field))
        b = normalize_attribute(field, candidate_details.get(field))
        if not a or not b:
            continue
        similarity = 1.0 if a == b else 0.0
        scores[field] = similarity
        compared.append(field)
        if similarity >= 0.999:
            matched.append(field)

    if target_age is not None and candidate_age is not None:
        age_similarity = max(0.0, 1.0 - abs(target_age - candidate_age) / 12.0)
        scores["age"] = age_similarity
        compared.append("age")
        if age_similarity >= 0.60:
            matched.append("age")

    matched_details = [field for field in matched if field != "gender"]
    compared_details = [field for field in compared if field != "gender"]
    # Gender-only recommendations are not meaningful; require at least one
    # known and matching personal/physical attribute.
    if not compared_details or not matched_details:
        return None

    total_weight = sum(FIELD_WEIGHTS[field] for field in scores)
    score = sum(scores[field] * FIELD_WEIGHTS[field] for field in scores) / total_weight if total_weight else 0.0
    target_known = sum(1 for field in ("country", "age", "ethnicity", "hair_color", "breast_type", "cup_size")
                       if (field == "age" and target_age is not None) or
                          (field != "age" and normalize_attribute(field, target_details.get(field))))
    coverage = len(compared_details) / max(1, target_known)
    return {
        "score": max(0.0, min(1.0, score)),
        "matched_fields": [ATTRIBUTE_LABELS[field] for field in ATTRIBUTE_LABELS if field in matched],
        "compared_fields": [ATTRIBUTE_LABELS[field] for field in ATTRIBUTE_LABELS if field in compared],
        "matched_detail_count": len(matched_details),
        "compared_detail_count": len(compared_details),
        "metadata_coverage": max(0.0, min(1.0, coverage)),
    }
