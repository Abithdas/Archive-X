"""Identify movies and TV episodes (the SFW side): read the file name, ask TMDB (OpenSubtitles by file hash, OMDb as a fallback), keep the result.

Nothing here touches Telegram. Every web call goes through ``Fetch`` so tests can answer for the providers. Keys live in the settings table:
``tmdb_api_key`` (a v3 key, or a v4 read token), ``omdb_api_key``, ``opensubtitles_api_key``.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import struct
import time
from typing import Callable
from urllib.parse import quote, urlencode

PARSER_VERSION = 2   # raised when the name reading improves: unmatched videos are looked at again once, nothing else is redone

TMDB = "https://api.themoviedb.org/3"
TVMAZE = "https://api.tvmaze.com"
CINEMETA = "https://v3-cinemeta.strem.io"   # the Stremio project's open movie and series catalog: no key, ids are IMDb ids
CINEMETA_BASE = 10_000_000_000              # a Cinemeta title is stored as -(BASE + its IMDb number): clear of TMDB ids (positive) and TVmaze ids (small negatives)
OMDB = "https://www.omdbapi.com/"
OPENSUBTITLES = "https://api.opensubtitles.com/api/v1/subtitles"
IMAGE = "https://image.tmdb.org/t/p"
ACCEPT = 0.85    # at or above: matched without asking
REVIEW = 0.55    # between this and ACCEPT: kept for the person to choose
CACHE_HIT_SECONDS = 30 * 24 * 3600    # an answer the provider gave is kept this long
CACHE_MISS_SECONDS = 7 * 24 * 3600    # "nothing found" is kept shorter, so a title added later is found

# fetch(url, params, headers) -> parsed JSON ({} when the provider has nothing)
Fetch = Callable[[str, dict, dict], dict]

_JUNK = re.compile(
    r"\b(2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10?|hdr|dv|dolby|vision|bluray|blu-ray|bdrip|brrip|dvdrip|dvd|webrip|web-dl|web|hdtv|hdrip|remux|"
    r"x264|x265|h264|h265|hevc|avc|xvid|divx|aac|ac3|eac3|dts|dd5|dd2|ddp5|ddp|atmos|truehd|flac|mp3|10bit|8bit|proper|repack|extended|unrated|"
    r"directors|cut|imax|internal|multi|dual|audio|subbed|dubbed|nf|amzn|hmax|dsnp|atvp|yify|yts|rarbg|ettv|eztv|galaxyrg|sparks|fgt)\b", re.I)
_SXXEYY = re.compile(r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?:[ ._-]?e?(\d{1,3}))?(?![a-z0-9])", re.I)
_NXMM = re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?![a-z0-9])", re.I)
_YEAR = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9])")
_SEASON_DIR = re.compile(r"^(?:season|series|s)[ ._-]?(\d{1,2})$", re.I)
# an id written into the name settles the match: {tmdb-603}, [tmdbid-603], tmdbid=603, tt0133093
_ID_TMDB = re.compile(r"[\[{(]?\s*tmdb(?:id)?[-=_ ]?(\d{1,8})\s*[\]})]?", re.I)
_ID_IMDB = re.compile(r"[\[{(]?\s*(?:imdb(?:id)?[-=_ ]?)?(tt\d{6,9})\s*[\]})]?", re.I)

# how good a copy is: the picture size, and whether it is a cinema recording
_RES = [(re.compile(r"(?<![a-z0-9])(?:2160p|4k|uhd)(?![a-z0-9])", re.I), 2160), (re.compile(r"(?<![a-z0-9])1080[pi](?![a-z0-9])", re.I), 1080),
        (re.compile(r"(?<![a-z0-9])720p(?![a-z0-9])", re.I), 720), (re.compile(r"(?<![a-z0-9])576p(?![a-z0-9])", re.I), 576),
        (re.compile(r"(?<![a-z0-9])(?:480p|360p)(?![a-z0-9])", re.I), 480)]
_CAM = re.compile(r"(?<![a-z0-9])(?:cam|hdcam|camrip|hdts|telesync|tsrip|predvd|hdtc|telecine|sprint)(?![a-z0-9])|(?<![a-z])ts(?![a-z])", re.I)
_SOURCES = [(re.compile(r"web[ ._-]?dl|webrip", re.I), "WEB-DL"), (re.compile(r"blu[ ._-]?ray|brrip|bdrip|remux", re.I), "BluRay"), (re.compile(r"hdtv", re.I), "HDTV"),
            (re.compile(r"dvd(?:rip|scr)?", re.I), "DVD")]


def quality_of(name: str, height: int = 0) -> dict:
    """``{resolution, height, cam, source}`` for a file: the name's own tags first, the real picture height when the name says nothing.
    A cinema recording ("CAM", "HDTS", "TS") is flagged so it can be kept out of sight when a better copy exists."""
    text = os.path.splitext(str(name))[0]
    tagged = next((h for rx, h in _RES if rx.search(text)), 0)
    h = int(height or 0)
    picture = 2160 if h >= 2000 else 1080 if h >= 1000 else 720 if h >= 700 else 576 if h >= 560 else 480 if h >= 300 else 0
    best = tagged or picture
    source = next((label for rx, label in _SOURCES if rx.search(text)), "")
    return {"resolution": f"{best}p" if best else "", "height": best, "cam": bool(_CAM.search(text)), "source": source}


def version_sort_key(q: dict) -> tuple:
    """Best copy first: never a cinema recording over a real one, then the larger picture."""
    return (1 if q.get("cam") else 0, -int(q.get("height") or 0))


def _clean(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", " ", text)                  # [group] tags
    text = _JUNK.sub(" ", text)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+-\s*$|^\s*-\s+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -.")


def parse_name(path: str) -> dict:
    """What a file's name (and its folders) say: ``{kind: movie|episode, title, year, season, episode}``."""
    path = str(path).replace("\\", "/")
    ids = {"tmdb": None, "imdb": None}
    m_t = _ID_TMDB.search(path)
    m_i = _ID_IMDB.search(path)
    if m_t:
        ids["tmdb"] = int(m_t.group(1))
        path = path.replace(m_t.group(0), " ")
    if m_i:
        ids["imdb"] = m_i.group(1).lower()
        path = path.replace(m_i.group(0), " ")
    folders = [p for p in path.split("/")[:-1] if p]
    stem = os.path.splitext(path.rsplit("/", 1)[-1])[0]
    season = episode = None
    m = _SXXEYY.search(stem) or _NXMM.search(stem)
    head = stem
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
        head = stem[:m.start()]
    title = _clean(re.sub(r"\(?(?:19|20)\d{2}\)?", " ", head)) if m else ""
    year = None
    if not m:
        years = list(_YEAR.finditer(stem))
        if years:                                              # the last year is the release year ("2001 A Space Odyssey 1968")
            y = years[-1]
            year = int(y.group(1))
            head = stem[:y.start()] if y.start() > 2 else stem
        title = _clean(re.sub(r"[\(\[]\s*$", "", head))
    else:
        y = _YEAR.search(head)
        year = int(y.group(1)) if y else None
    if m and not title:                                        # "S01E02.mkv" inside "Show Name/Season 1/"
        for folder in reversed(folders):
            sm = _SEASON_DIR.match(folder.strip())
            if sm:
                season = season if season is not None else int(sm.group(1))
                continue
            title = _clean(folder)
            break
    if not m:
        sm = _SEASON_DIR.match(folders[-1].strip()) if folders else None
        if sm and re.fullmatch(r"(?:e|ep|episode)?[ ._-]?(\d{1,3})", stem, re.I):   # "Show/Season 2/07.mkv"
            episode = int(re.sub(r"\D", "", stem))
            season = int(sm.group(1))
            for folder in reversed(folders):
                if not _SEASON_DIR.match(folder.strip()):
                    title = _clean(folder)
                    break
    kind = "episode" if season is not None and episode is not None else "movie"
    return {"kind": kind, "title": title or _clean(stem), "year": year, "season": season, "episode": episode, "ids": ids}


def _norm_title(text: str) -> str:
    text = re.sub(r"^(.+),\s*(?:the|a|an)\s*$", r"\1", str(text).strip(), flags=re.I)   # "Matrix, The" is "The Matrix"
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"^(the|a|an) ", "", str(text).lower().replace("&", "and"))).strip()


def similarity(a: str, b: str) -> float:
    """How alike two titles are, 0..1. Word order does not matter ("Matrix, The" = "The Matrix"), a missing article does not matter."""
    x, y = _norm_title(a), _norm_title(b)
    if not x or not y:
        return 0.0
    by_letters = difflib.SequenceMatcher(None, x, y).ratio()
    by_words = difflib.SequenceMatcher(None, " ".join(sorted(x.split())), " ".join(sorted(y.split()))).ratio()
    return max(by_letters, by_words)


def oshash(path: str) -> str:
    """OpenSubtitles' 64-bit file hash: the size plus the sum of the first and last 64 KiB read as numbers."""
    size = os.path.getsize(path)
    if size < 65536 * 2:
        return ""
    h = size
    with open(path, "rb") as f:
        for offset in (0, max(0, size - 65536)):
            f.seek(offset)
            for (v,) in struct.iter_unpack("<Q", f.read(65536)):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def tmdb_headers(key: str) -> tuple[dict, dict]:
    """(params, headers) for TMDB: a v4 read token goes in the header, a v3 key in the query."""
    key = key.strip()
    return ({}, {"Authorization": f"Bearer {key}", "Accept": "application/json"}) if key.startswith("eyJ") else ({"api_key": key}, {"Accept": "application/json"})


def _is_key_refusal(value) -> bool:
    """A provider's answer that is about the key, not about the question (OMDb says so with a normal-looking reply)."""
    return isinstance(value, dict) and value.get("Response") == "False" and "api key" in str(value.get("Error", "")).lower()


def _is_miss(value) -> bool:
    """An answer that says "nothing": empty, or a search with no results."""
    if not value:
        return True
    if isinstance(value, dict) and value.get("Response") == "False":    # OMDb: "Movie not found!"
        return True
    return isinstance(value, dict) and set(value) <= {"results", "data", "page", "total_pages", "total_results", "movie_results", "tv_results", "person_results"} and not any(
        value.get(k) for k in ("results", "data", "movie_results", "tv_results", "person_results"))


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html or ""))).strip()


def _year_of(date: str) -> int | None:
    return int(date[:4]) if date and str(date)[:4].isdigit() else None


def cinemeta_id(imdb: str) -> int:
    """The id a Cinemeta title is kept under (negative, above every TVmaze id)."""
    m = re.fullmatch(r"tt(\d{1,9})", str(imdb or ""))
    return -(CINEMETA_BASE + int(m.group(1))) if m else 0


def imdb_of_cinemeta(stored: int) -> str:
    return f"tt{-int(stored) - CINEMETA_BASE:07d}" if int(stored) <= -CINEMETA_BASE else ""


def is_cinemeta(stored: int) -> bool:
    return int(stored) <= -CINEMETA_BASE


class Screen:
    """One set of keys and one way of fetching; every lookup returns plain dicts.

    ``cache`` (optional) keeps provider answers between runs: it has ``get(provider, key) -> (value, age_seconds) | None`` and
    ``put(provider, key, value)``. Within one Screen the same question is never asked twice.
    """

    def __init__(self, fetch: Fetch, *, tmdb: str = "", omdb: str = "", opensubtitles: str = "", tvmaze: bool = False, cinemeta: bool = False, language: str = "en-US", cache=None):
        self.fetch, self.tmdb, self.omdb, self.os_key, self.language = fetch, tmdb.strip(), omdb.strip(), opensubtitles.strip(), language
        self.tvmaze = bool(tvmaze)
        self.cinemeta = bool(cinemeta)
        self.cache = cache
        self._memo: dict = {}
        self.calls = 0   # requests really sent to a provider (cache and memo answers are not counted)

    # --- one question, asked at most once -----------------------------------------------------------------------------------------
    def _ask(self, provider: str, url: str, params: dict, headers: dict) -> dict:
        key = url + "?" + urlencode(sorted((k, str(v)) for k, v in params.items() if k != "api_key"))
        if key in self._memo:
            return self._memo[key]
        value = None
        if self.cache is not None:
            hit = self.cache.get(provider, key)
            if hit is not None:
                value, age = hit
                if age > (CACHE_MISS_SECONDS if _is_miss(value) else CACHE_HIT_SECONDS):
                    value = None
        if value is None:
            self.calls += 1
            value = self.fetch(url, params, headers) or {}   # an error is raised, never cached: it may pass
            if self.cache is not None and not _is_key_refusal(value):   # "invalid key" is never kept: it must not outlive the key being fixed or activated
                self.cache.put(provider, key, value)
        self._memo[key] = value
        return value

    def _t(self, path: str, **params) -> dict:
        base, headers = tmdb_headers(self.tmdb)
        return self._ask("tmdb", f"{TMDB}{path}", {**base, "language": self.language, **params}, headers) or {}

    def company_logo(self, name: str) -> str:
        """A studio's logo from TMDB by its name (SFW studios have no adult-provider record to take one from). Empty when there is no key or no exact match."""
        if not self.tmdb or not name.strip():
            return ""
        for r in (self._t("/search/company", query=name.strip()).get("results") or [])[:10]:
            if r.get("logo_path") and _norm_title(r.get("name", "")) == _norm_title(name):
                return image_url(r["logo_path"], "w185")
        return ""

    def person_photo(self, name: str) -> str:
        """A person's picture from TMDB by name: the best-known person with exactly that name, or empty."""
        if not self.tmdb or not name.strip():
            return ""
        results = [r for r in (self._t("/search/person", query=name.strip()).get("results") or []) if r.get("profile_path") and _norm_title(r.get("name", "")) == _norm_title(name)]
        results.sort(key=lambda r: -float(r.get("popularity") or 0))
        return image_url(results[0]["profile_path"], "w185") if results else ""

    # --- candidates -------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _score(want: dict, title: str, original: str, year: int | None, popularity: float, query: str | None = None) -> float:
        asked = query or want["title"]
        score = max(similarity(asked, title), similarity(asked, original or ""))
        if want.get("year") and year:
            score += 0.08 if want["year"] == year else -0.25 if abs(want["year"] - year) > 1 else 0.0
        score = max(0.0, min(score + min(float(popularity or 0) / 1000.0, 0.05), 1.0))   # a popular title wins a tie
        if query and query != want["title"]:                          # found only after dropping words: a person must confirm it, it is never taken on its own
            score = min(score, ACCEPT - 0.01)
        return round(score, 3)

    def _tmdb_candidates(self, want: dict, query: str, year: int | None, kind: str) -> list[dict]:
        params = {"query": query, "include_adult": "false"}
        if year:
            params["first_air_date_year" if kind == "tv" else "year"] = year
        out = []
        for row in (self._t(f"/search/{kind}", **params).get("results") or [])[:10]:
            title = (row.get("name") if kind == "tv" else row.get("title")) or ""
            date = (row.get("first_air_date") if kind == "tv" else row.get("release_date")) or ""
            y = _year_of(date)
            out.append({"kind": kind, "tmdb_id": int(row["id"]), "title": title, "year": y, "source": "tmdb", "poster": row.get("poster_path") or "", "overview": row.get("overview") or "",
                        "score": self._score(want, title, (row.get("original_name") if kind == "tv" else row.get("original_title")) or "", y, row.get("popularity") or 0, query)})
        return out

    def _tvmaze_candidates(self, want: dict, query: str) -> list[dict]:
        out = []
        for row in self._tvmaze_search(query)[:8]:
            show = row.get("show") or {}
            y = _year_of(show.get("premiered") or "")
            out.append({"kind": "tv", "tmdb_id": -int(show["id"]), "imdb_id": (show.get("externals") or {}).get("imdb") or "", "title": show.get("name") or "", "year": y, "source": "tvmaze",
                        "poster": (show.get("image") or {}).get("medium") or "", "overview": _plain(show.get("summary")), "score": self._score(want, show.get("name") or "", "", y, 0, query)})
        return out

    def _cinemeta_meta(self, kind: str, imdb: str) -> dict:
        got = self._ask("cinemeta", f"{CINEMETA}/meta/{'series' if kind == 'tv' else 'movie'}/{imdb}.json", {}, {})
        return (got or {}).get("meta") or {}

    def _cinemeta_candidates(self, want: dict, query: str, kind: str) -> list[dict]:
        got = self._ask("cinemeta", f"{CINEMETA}/catalog/{'series' if kind == 'tv' else 'movie'}/top/search={quote(query, safe='')}.json", {}, {})
        out = []
        for row in ((got or {}).get("metas") or [])[:10]:
            sid = cinemeta_id(row.get("imdb_id") or row.get("id") or "")
            if not sid:
                continue
            y = _year_of(str(row.get("releaseInfo") or row.get("year") or ""))
            out.append({"kind": kind, "tmdb_id": sid, "imdb_id": row.get("imdb_id") or row.get("id"), "title": row.get("name") or "", "year": y, "source": "cinemeta",
                        "poster": row.get("poster") or "", "overview": row.get("description") or "", "score": self._score(want, row.get("name") or "", "", y, 0, query)})
        return out

    def _tvmaze_search(self, query: str) -> list:
        got = self._ask("tvmaze", f"{TVMAZE}/search/shows", {"q": query}, {})
        return got if isinstance(got, list) else []

    def find_imdb(self, imdb_id: str) -> list[dict]:
        """TMDB knows IMDb ids: ``tt0133093`` becomes the movie or show it names."""
        if not self.tmdb:
            return []
        got = self._t(f"/find/{imdb_id}", external_source="imdb_id")
        out = []
        for kind, key in (("movie", "movie_results"), ("tv", "tv_results")):
            for row in got.get(key) or []:
                date = (row.get("first_air_date") if kind == "tv" else row.get("release_date")) or ""
                out.append({"kind": kind, "tmdb_id": int(row["id"]), "title": (row.get("name") if kind == "tv" else row.get("title")) or "", "year": _year_of(date), "score": 1.0,
                            "source": "id", "poster": row.get("poster_path") or "", "overview": row.get("overview") or ""})
        return out

    def search(self, parsed: dict) -> list[dict]:
        """Ranked candidates ``{kind: movie|tv, tmdb_id, title, year, score, source}`` for a parsed name.

        An id written in the name settles it. Otherwise the title is tried as it is, then without its last words (release junk left over),
        then without the year; the search stops as soon as something plausible turns up, so a clear name costs one request.
        """
        want_kind = "tv" if parsed["kind"] == "episode" else "movie"
        ids = parsed.get("ids") or {}
        if ids.get("tmdb") and self.tmdb:
            return [{"kind": want_kind, "tmdb_id": int(ids["tmdb"]), "title": parsed.get("title", ""), "year": parsed.get("year"), "score": 1.0, "source": "id", "poster": "", "overview": ""}]
        if ids.get("imdb"):
            found = [c for c in self.find_imdb(ids["imdb"]) if c["kind"] == want_kind] or self.find_imdb(ids["imdb"])
            if found:
                return found[:1]
        title = (parsed.get("title") or "").strip()
        if not title:
            return []
        words = title.split()
        queries = [title] + [" ".join(words[:-drop]) for drop in (1, 2) if len(words) - drop >= 2]
        years = [parsed["year"], None] if parsed.get("year") else [None]
        found: dict[tuple, dict] = {}

        def best() -> float:
            return max((c["score"] for c in found.values()), default=0.0)

        def add(rows: list[dict]) -> None:
            for c in rows:
                k = (c["kind"], c["tmdb_id"])
                if k not in found or c["score"] > found[k]["score"]:
                    found[k] = c
        if self.tmdb:
            for q in queries:
                for y in years:
                    add(self._tmdb_candidates(parsed, q, y, want_kind))
                    if best() >= REVIEW:
                        break
                if best() >= REVIEW:
                    break
        if self.tvmaze and want_kind == "tv" and best() < ACCEPT:
            for q in queries:
                add(self._tvmaze_candidates(parsed, q))
                if best() >= REVIEW:
                    break
        if self.cinemeta and best() < ACCEPT:
            for q in queries:
                add(self._cinemeta_candidates(parsed, q, want_kind))
                if best() >= REVIEW:
                    break
        if not found and self.omdb:                                  # nothing from TMDB: OMDb by title
            data = self._ask("omdb", OMDB, {"t": title, "type": "series" if want_kind == "tv" else "movie", **({"y": parsed["year"]} if parsed.get("year") and want_kind == "movie" else {}), "apikey": self.omdb}, {}) or {}
            if data.get("Response") == "True" and data.get("imdbID"):
                y = _year_of(str(data.get("Year", "")))
                found[(want_kind, 0)] = {"kind": want_kind, "tmdb_id": 0, "imdb_id": data["imdbID"], "title": data.get("Title", ""), "year": y, "source": "omdb",
                                         "score": round(similarity(title, data.get("Title", "")), 3), "poster": "", "overview": data.get("Plot") or ""}
        return sorted(found.values(), key=lambda c: -c["score"])

    def by_hash(self, file_hash: str, size: int = 0) -> dict | None:
        """OpenSubtitles knows files by hash: an exact answer when the file is a known release."""
        if not (self.os_key and file_hash):
            return None
        data = self._ask("opensubtitles", OPENSUBTITLES, {"moviehash": file_hash}, {"Api-Key": self.os_key, "Accept": "application/json", "User-Agent": "ArchiveX v1"}) or {}
        for row in data.get("data") or []:
            attrs = row.get("attributes") or {}
            feat = attrs.get("feature_details") or {}
            if not feat.get("title") and not feat.get("parent_title"):
                continue
            episode = str(feat.get("feature_type", "")).lower() == "episode"
            return {"kind": "tv" if episode else "movie", "tmdb_id": int((feat.get("parent_tmdb_id") if episode else feat.get("tmdb_id")) or 0),
                    "title": (feat.get("parent_title") if episode else feat.get("title")) or "", "year": feat.get("year"), "score": 1.0, "source": "opensubtitles",
                    "season": feat.get("season_number") if episode else None, "episode": feat.get("episode_number") if episode else None}
        return None

    # --- details ----------------------------------------------------------------------------------------------------------------
    def details(self, kind: str, tmdb_id: int) -> dict:
        """Everything kept about a title: text, artwork, genres, studios or networks, cast. A negative id is a TVmaze show."""
        if is_cinemeta(tmdb_id):
            return self._cinemeta_details(kind, int(tmdb_id))
        if int(tmdb_id) < 0:
            return self._tvmaze_details(-int(tmdb_id))
        d = self._t(f"/{kind}/{int(tmdb_id)}", append_to_response="credits,external_ids")
        if not d:
            return {}
        genres = [g["name"] for g in d.get("genres") or [] if g.get("name")]
        made_by = [c for c in (d.get("networks") if kind == "tv" and d.get("networks") else d.get("production_companies")) or [] if c.get("name")]
        companies = [c["name"] for c in made_by]
        company_info = [{"name": c["name"], "id": c.get("id"), "logo": c.get("logo_path") or ""} for c in made_by]   # the logos come with the answer: studios get a picture
        cast = [{"name": p["name"], "tmdb_id": p.get("id"), "character": p.get("character") or "", "profile": p.get("profile_path") or ""}
                for p in ((d.get("credits") or {}).get("cast") or [])[:20] if p.get("name")]
        date = d.get("first_air_date") if kind == "tv" else d.get("release_date")
        return {"kind": kind, "tmdb_id": int(d["id"]), "imdb_id": (d.get("external_ids") or {}).get("imdb_id") or d.get("imdb_id") or "",
                "title": (d.get("name") if kind == "tv" else d.get("title")) or "", "original_title": (d.get("original_name") if kind == "tv" else d.get("original_title")) or "",
                "year": _year_of(date), "release_date": date or "", "overview": d.get("overview") or "", "tagline": d.get("tagline") or "",
                "poster": d.get("poster_path") or "", "backdrop": d.get("backdrop_path") or "", "genres": genres, "companies": companies, "company_info": company_info, "cast": cast,
                "rating": d.get("vote_average") or 0, "runtime": (d.get("runtime") or (d.get("episode_run_time") or [0])[0] or 0),
                "seasons": [{"season": s.get("season_number"), "episodes": s.get("episode_count"), "name": s.get("name") or ""} for s in d.get("seasons") or [] if s.get("season_number") is not None],
                "status": d.get("status") or ""}

    def _cinemeta_details(self, kind: str, stored: int) -> dict:
        imdb = imdb_of_cinemeta(stored)
        d = self._cinemeta_meta(kind, imdb)
        if not d:
            return {}
        counts: dict[int, int] = {}
        for v in d.get("videos") or []:
            n = int(v.get("season") or 0)
            counts[n] = counts.get(n, 0) + 1
        runtime = re.search(r"\d+", str(d.get("runtime") or ""))
        try:
            rating = float(d.get("imdbRating") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        date = str(d.get("released") or "")[:10]
        return {"kind": kind, "tmdb_id": stored, "imdb_id": imdb, "title": d.get("name") or "", "original_title": "", "year": _year_of(str(d.get("releaseInfo") or d.get("year") or date)),
                "release_date": date, "overview": d.get("description") or "", "tagline": "", "poster": d.get("poster") or "", "backdrop": d.get("background") or "",
                "genres": list(d.get("genres") or d.get("genre") or []), "companies": [], "cast": [{"name": n, "tmdb_id": None, "character": "", "profile": ""} for n in (d.get("cast") or [])[:20] if n],
                "rating": rating, "runtime": int(runtime.group(0)) if runtime else 0,
                "seasons": [{"season": k, "episodes": v, "name": f"Season {k}" if k else "Specials"} for k, v in sorted(counts.items())] if kind == "tv" else [],
                "status": d.get("status") or ""}

    def _tvmaze_show(self, show_id: int) -> dict:
        """A show with its cast and every episode: one request, whatever is needed of it."""
        return self._ask("tvmaze", f"{TVMAZE}/shows/{int(show_id)}", {"embed[]": ["cast", "episodes"]}, {}) or {}

    def _tvmaze_details(self, show_id: int) -> dict:
        d = self._tvmaze_show(show_id)
        if not d:
            return {}
        eps = ((d.get("_embedded") or {}).get("episodes")) or []
        counts: dict[int, int] = {}
        for e in eps:
            counts[int(e.get("season") or 0)] = counts.get(int(e.get("season") or 0), 0) + 1
        net = (d.get("network") or d.get("webChannel") or {}).get("name") or ""
        cast = [{"name": (c.get("person") or {}).get("name") or "", "tmdb_id": None, "character": (c.get("character") or {}).get("name") or "", "profile": ((c.get("person") or {}).get("image") or {}).get("medium") or ""}
                for c in ((d.get("_embedded") or {}).get("cast") or [])[:20] if (c.get("person") or {}).get("name")]
        image = d.get("image") or {}
        return {"kind": "tv", "tmdb_id": -int(d["id"]), "imdb_id": (d.get("externals") or {}).get("imdb") or "", "title": d.get("name") or "", "original_title": "", "year": _year_of(d.get("premiered") or ""),
                "release_date": d.get("premiered") or "", "overview": _plain(d.get("summary")), "tagline": "", "poster": image.get("original") or image.get("medium") or "", "backdrop": "",
                "genres": list(d.get("genres") or []), "companies": [net] if net else [], "cast": cast, "rating": (d.get("rating") or {}).get("average") or 0, "runtime": d.get("runtime") or d.get("averageRuntime") or 0,
                "seasons": [{"season": k, "episodes": v, "name": f"Season {k}" if k else "Specials"} for k, v in sorted(counts.items())], "status": d.get("status") or ""}

    def season(self, tmdb_id: int, season: int) -> dict[int, dict]:
        """Every episode of one season, by number, in one request (not one request per episode)."""
        out: dict[int, dict] = {}
        if is_cinemeta(tmdb_id):
            for v in self._cinemeta_meta("tv", imdb_of_cinemeta(tmdb_id)).get("videos") or []:
                n = v.get("episode") if v.get("episode") is not None else v.get("number")
                if int(v.get("season") or 0) == int(season) and n is not None:
                    out[int(n)] = {"season": int(season), "episode": int(n), "name": v.get("name") or v.get("title") or "", "overview": v.get("overview") or v.get("description") or "",
                                   "air_date": str(v.get("released") or v.get("firstAired") or "")[:10], "still": v.get("thumbnail") or "", "runtime": 0}
            return out
        if int(tmdb_id) < 0:
            for e in ((self._tvmaze_show(-int(tmdb_id)).get("_embedded") or {}).get("episodes")) or []:
                if int(e.get("season") or 0) == int(season) and e.get("number") is not None:
                    img = e.get("image") or {}
                    out[int(e["number"])] = {"season": int(season), "episode": int(e["number"]), "name": e.get("name") or "", "overview": _plain(e.get("summary")), "air_date": e.get("airdate") or "",
                                             "still": img.get("medium") or "", "runtime": e.get("runtime") or 0}
            return out
        for e in (self._t(f"/tv/{int(tmdb_id)}/season/{int(season)}").get("episodes") or []):
            if e.get("episode_number") is not None:
                out[int(e["episode_number"])] = {"season": int(season), "episode": int(e["episode_number"]), "name": e.get("name") or "", "overview": e.get("overview") or "",
                                                 "air_date": e.get("air_date") or "", "still": e.get("still_path") or "", "runtime": e.get("runtime") or 0}
        return out

    def episode(self, tmdb_id: int, season: int, episode: int) -> dict:
        return self.season(tmdb_id, season).get(int(episode)) or {}


def missing_episodes(total: int, present: list[int]) -> list[int]:
    """The episode numbers of a season that are not here (1..total, minus what the library holds)."""
    return sorted(set(range(1, int(total or 0) + 1)) - {int(n) for n in present})


def image_url(path: str, size: str = "w500") -> str:
    """A picture's address: TMDB keeps a path (sized here), TVmaze already gives a whole address."""
    if not path:
        return ""
    return path if str(path).startswith("http") else f"{IMAGE}/{size}{path}"


def show_key(parsed: dict) -> tuple:
    """Files of one show or one movie share a key, so the providers are asked once for all of them."""
    ids = parsed.get("ids") or {}
    return (parsed["kind"] == "episode", _norm_title(parsed.get("title", "")), parsed.get("year"), ids.get("tmdb"), ids.get("imdb"))


def identify(screen: Screen, path: str, *, file_hash: str = "", memo: dict | None = None) -> dict:
    """Name and hash in, an answer out: ``{state: MATCHED|REVIEW|UNMATCHED, best, candidates, parsed, season, episode}``.

    ``memo`` lets a whole run share one answer per show: the episodes of a series are looked up once, not once each.
    """
    parsed = parse_name(path)
    hit = screen.by_hash(file_hash) if file_hash else None
    if hit and hit.get("tmdb_id"):
        return {"state": "MATCHED", "best": hit, "candidates": [hit], "parsed": parsed, "season": hit.get("season") or parsed["season"], "episode": hit.get("episode") or parsed["episode"]}
    key = show_key(parsed)
    if memo is not None and key in memo:
        return {**memo[key], "parsed": parsed, "season": parsed["season"], "episode": parsed["episode"]}
    candidates = screen.search(parsed)
    best = candidates[0] if candidates else None
    state = "UNMATCHED" if not best or best["score"] < REVIEW else "MATCHED" if best["score"] >= ACCEPT else "REVIEW"
    result = {"state": state, "best": best if state != "UNMATCHED" else None, "candidates": candidates[:5]}
    if memo is not None:
        memo[key] = result
    return {**result, "parsed": parsed, "season": parsed["season"], "episode": parsed["episode"]}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
