"""Filename/caption cleanup and scene-name parsing.

Broken characters: repaired with the `ftfy` library (Apache-2.0, by
Robyn Speer — used as an installed dependency, see THIRD_PARTY.md).
If ftfy isn't installed the parser still works, just without mojibake repair.

Parsing follows the common "studio.YY.MM.DD.title" / "studio - date - title"
conventions that ThePornDB's MIT-licensed `namer` tool relies on; the result
is a set of HINTS for lookup, never authoritative metadata.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

try:
    import ftfy  # type: ignore
    _HAS_FTFY = True
except ImportError:
    _HAS_FTFY = False

_VIDEO_EXT = r"(?:mp4|m4v|mkv|mov|avi|wmv|webm|ts|flv|mpg|mpeg)"
_THUMB_SUFFIX = re.compile(rf"(?:[._ ]{_VIDEO_EXT})?(?:[._ ](?:jpe?g|png|webp))$", re.I)
_EXT = re.compile(rf"\.{_VIDEO_EXT}$", re.I)
_RES = re.compile(r"(?<![A-Za-z0-9])(2160p|1440p|1080p|720p|576p|480p|4k|uhd|fhd|hd)(?![A-Za-z0-9])", re.I)
_FUSED_QUALITY_BEFORE_DATE = re.compile(
    r"(?i)(2160p|1440p|1080p|720p|576p|480p|4k|uhd|fhd|hd)(?=date(?:[^a-z0-9]|$))"
)


def split_fused_quality_before_date(value: str) -> str:
    """Separate resolution flags glued to a literal Date label in filenames."""
    return _FUSED_QUALITY_BEFORE_DATE.sub(lambda m: f" {m.group(1)} ", str(value or ""))

_DATE_FULL = re.compile(r"(?<!\d)((?:19|20)\d{2})[.\-_ ](\d{1,2})[.\-_ ](\d{1,2})(?!\d)")
_DATE_SHORT = re.compile(r"(?<!\d)(\d{2})[.\-_](\d{2})[.\-_](\d{2})(?!\d)")
_NOISE = re.compile(r"\b(xxx|prt|x264|x265|h264|h265|hevc|avc|aac|web|web[-_ ]?dl|webrip|bluray|brrip|proper|repack|remux|sample|final|uncensored|censored|4320p|2160p|1440p|1080p|720p|576p|480p|4k|uhd|fhd|10bit|8bit|mp4|mkv|avi|mov|wmv|webm|split[-_ ]?scenes?)\b", re.I)
# Scene/studio production codes, e.g. "SONE-012", "SONE.012", "SONE012",
# "FC2-PPV-1234567". Separator-insensitive on purpose: filenames for the
# same scene commonly vary between hyphen, dot, underscore or nothing at
# all between the letters and the number (canonical-key matching depends
# on all of these normalizing to one code). This is only a SEARCH SIGNAL
# for the matching service -- never proof of a studio or performer by itself.
_SCENE_CODE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,10})[.\-_]?(\d{2,6})(?!\d)")
_GENERIC_SCENE_CODE_PREFIXES = frozenset({
    "VID", "VIDEO", "CLIP", "MOVIE", "FILE", "UNKNOWN", "OPAQUE", "DOWNLOAD", "PART", "CHUNK",
})


def is_generic_scene_code(value: object) -> bool:
    """Whether a code-like token is a generic export ID rather than scene identity."""
    compact = re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
    prefix = re.sub(r"\d.*$", "", compact).upper()
    return bool(prefix and prefix in _GENERIC_SCENE_CODE_PREFIXES)


def clean_text(s: str) -> str:
    """Repair mojibake, normalize Unicode, collapse whitespace."""
    if not s:
        return ""
    if _HAS_FTFY:
        s = ftfy.fix_text(s)
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def canonical_key(name: str) -> str:
    """Matching key for pairing a video with its thumbnail despite
    character/extension differences:
        'Scéne 01.mp4' / 'Sce\u0301ne 01.mp4.jpg' / 'scene_01_mp4.jpg' -> 'scene 01'
    Accents are stripped only for the KEY (display names keep them)."""
    s = clean_text(name)
    s = _THUMB_SUFFIX.sub("", s)
    s = _EXT.sub("", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = re.sub(r"[\W_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_scene_name(name: str) -> dict:
    """Best-effort hints: studio, date (YYYY-MM-DD), title, resolution, code.

    `code` is a scene/production code token (e.g. "SONE-012") pulled out as
    a separate lookup signal — it is intentionally never treated as a
    performer or used to auto-create one; see metadata/matching.py."""
    raw = clean_text(name)
    base = _THUMB_SUFFIX.sub("", _EXT.sub("", raw))
    out: dict = {"original": raw, "studio": None, "date": None, "year": None, "title": None,
                 "resolution": None, "code": None, "performers": [], "performer_candidates": []}

    # Split resolution flags that are fused to names by some release templates,
    # e.g. "MiaKhalifa1080PDate_Starring_Title_...". Without this cleanup the
    # strict resolution regex misses 1080p and the scene-code regex can mistake
    # the performer prefix plus 1080 for a production code.
    base = split_fused_quality_before_date(base)

    m = _RES.search(base)
    if m:
        r = m.group(1).lower()
        out["resolution"] = {"4k": "2160p", "uhd": "2160p", "fhd": "1080p", "hd": "720p"}.get(r, r)
        base = _RES.sub(" ", base)

    # Look for a scene code, but never inside a full date-like token (e.g.
    # "Studio.2023.05.14...") -- a bare year is not a scene code. Mask any
    # date span out of the search text first (same length, so positions in
    # `base` still line up for the removal below).
    date_peek = _DATE_FULL.search(base) or _DATE_SHORT.search(base)
    code_search_text = base
    if date_peek:
        ds, de = date_peek.span()
        code_search_text = base[:ds] + (" " * (de - ds)) + base[de:]
    code_m = _SCENE_CODE.search(code_search_text.upper())
    if code_m:
        letters, digits = code_m.groups()
        if not is_generic_scene_code(f"{letters}-{digits}"):
            out["code"] = f"{letters}-{digits}"
        # Remove the identifier-looking token from the title parse either way.
        # Generic export IDs (VID_001, Opaque_001, File-001) are not a scene
        # code or a title, and should not reduce/boost provider match confidence.
        start, end = code_m.span(0)
        base = base[:start] + " " + base[end:]

    # Bracketed studio/performer convention: [Studio] Performer - Title
    # Borrow the useful structural rule from the open-source namer project
    # without adding a new dependency.
    bracket = re.match(r"^\[([^\]]{1,60})\]\s*([^\-]{1,80})\s+-\s+(.+)$", base)
    if bracket:
        studio_text, performer_text, remainder = [x.strip() for x in bracket.groups()]
        out["studio"] = studio_text
        if performer_text:
            out["performers"] = [p.strip() for p in re.split(r"\s*(?:,|&|\band\b)\s*", performer_text, flags=re.I) if p.strip()]
        base = f"{performer_text} - {remainder}"

    year_m = re.search(r"(?<!\d)[(\[]((?:19|20)\d{2})[)\]](?!\d)", base)
    if year_m:
        out["year"] = int(year_m.group(1))
        base = base[:year_m.start()] + " " + base[year_m.end():]

    date_m = _DATE_FULL.search(base)
    if date_m:
        y, mo, d = date_m.groups()
        out["date"] = _valid_date(int(y), int(mo), int(d))
    else:
        date_m = _DATE_SHORT.search(base)
        if date_m:
            yy, mo, d = date_m.groups()
            out["date"] = _valid_date(2000 + int(yy), int(mo), int(d))
    if date_m and out["date"]:
        before = base[:date_m.start()].strip(" .-_[]()")
        after = base[date_m.end():].strip(" .-_[]()")
        # A prefix before a date is only a reliable studio hint when a title
        # follows the date (e.g. Studio.2023.05.14.Scene). If the date is the
        # final component, the prefix may instead be the scene title
        # (Scene_2023-05-14); do not fabricate a studio from that ambiguity.
        if before and after and not out.get("studio") and len(before.split(".")) <= 2 and len(before) <= 40:
            out["studio"] = before.replace(".", " ").replace("_", " ").strip()
        title_src = after or ("" if out["studio"] else before)
        # Common release convention: Studio_YY_MM_DD_Performer_Name_Title.
        # When the studio/date prefix is unambiguous and at least four usable
        # words follow, expose the leading two-word cast as a tentative clue and
        # keep it out of the title. Provider cast agreement is still required.
        if before and after and out.get("studio") and out.get("date"):
            suffix = re.sub(r"[._]+", " ", after)
            suffix = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", suffix)
            technical_markers = bool(_NOISE.search(suffix))
            suffix = re.sub(r"(?i)\b(?:4320p|2160p|1440p|1080p|720p|576p|480p|4k|uhd|fhd|x264|x265|h264|h265|hevc|avc|web|web[-_ ]?dl|webrip|bluray|brrip|xxx|prt)\b", " ", suffix)
            words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*", suffix)
            if (len(words) >= 4 or (len(words) == 2 and technical_markers)) and all(len(word.strip(".-")) >= 2 for word in words[:2]):
                tentative = " ".join(word.strip(".-") for word in words[:2])
                out["performer_candidates"] = [tentative]
                title_src = re.sub(r"(?i)^\s*" + r"[\W_]+".join(re.escape(word) for word in tentative.split()) + r"(?=[^A-Za-z0-9]|$)", " ", title_src, count=1)
    else:
        parts = re.split(r"\s+-\s+", base, maxsplit=1)
        if len(parts) == 2 and len(parts[0]) <= 40:
            if not out.get("studio"):
                out["studio"] = parts[0].strip()
            title_src = parts[1]
            if not out.get("performers") and out.get("studio") and parts[0].strip() != out.get("studio"):
                out["performers"] = [parts[0].strip()]
        else:
            title_src = base
    title = _NOISE.sub(" ", title_src.replace(".", " ").replace("_", " "))
    title = re.sub(r"\s+", " ", title).strip(" -")
    out["title"] = title or None
    return out


def _valid_date(y: int, m: int, d: int) -> Optional[str]:
    if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
        return f"{y:04d}-{m:02d}-{d:02d}"
    return None
