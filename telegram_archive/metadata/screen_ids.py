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
from typing import Callable

TMDB = "https://api.themoviedb.org/3"
OMDB = "https://www.omdbapi.com/"
OPENSUBTITLES = "https://api.opensubtitles.com/api/v1/subtitles"
IMAGE = "https://image.tmdb.org/t/p"
ACCEPT = 0.85    # at or above: matched without asking
REVIEW = 0.55    # between this and ACCEPT: kept for the person to choose

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


def _clean(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", " ", text)                  # [group] tags
    text = _JUNK.sub(" ", text)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+-\s*$|^\s*-\s+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -.")


def parse_name(path: str) -> dict:
    """What a file's name (and its folders) say: ``{kind: movie|episode, title, year, season, episode}``."""
    path = str(path).replace("\\", "/")
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
    return {"kind": kind, "title": title or _clean(stem), "year": year, "season": season, "episode": episode}


def similarity(a: str, b: str) -> float:
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"^(the|a|an) ", "", str(s).lower().replace("&", "and"))).strip()
    x, y = norm(a), norm(b)
    return difflib.SequenceMatcher(None, x, y).ratio() if x and y else 0.0


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


class Screen:
    """One set of keys and one way of fetching; every lookup returns plain dicts."""

    def __init__(self, fetch: Fetch, *, tmdb: str = "", omdb: str = "", opensubtitles: str = "", language: str = "en-US"):
        self.fetch, self.tmdb, self.omdb, self.os_key, self.language = fetch, tmdb.strip(), omdb.strip(), opensubtitles.strip(), language

    def _t(self, path: str, **params) -> dict:
        base, headers = tmdb_headers(self.tmdb)
        return self.fetch(f"{TMDB}{path}", {**base, "language": self.language, **params}, headers) or {}

    # --- candidates -------------------------------------------------------------------------------------------------------------
    def search(self, parsed: dict) -> list[dict]:
        """Ranked candidates ``{kind: movie|tv, tmdb_id, title, year, score, source}`` for a parsed name."""
        want = "tv" if parsed["kind"] == "episode" else "movie"
        out: list[dict] = []
        if self.tmdb and parsed.get("title"):
            params = {"query": parsed["title"], "include_adult": "false"}
            if parsed.get("year"):
                params["first_air_date_year" if want == "tv" else "year"] = parsed["year"]
            for row in (self._t(f"/search/{want}", **params).get("results") or [])[:8]:
                title = row.get("name") if want == "tv" else row.get("title")
                date = (row.get("first_air_date") if want == "tv" else row.get("release_date")) or ""
                year = int(date[:4]) if date[:4].isdigit() else None
                score = max(similarity(parsed["title"], title or ""), similarity(parsed["title"], row.get("original_name" if want == "tv" else "original_title") or ""))
                if parsed.get("year") and year:
                    score += 0.08 if abs(parsed["year"] - year) == 0 else -0.25 if abs(parsed["year"] - year) > 1 else 0.0
                out.append({"kind": want, "tmdb_id": int(row["id"]), "title": title or "", "year": year, "score": round(min(score, 1.0), 3), "source": "tmdb",
                            "poster": row.get("poster_path") or "", "overview": row.get("overview") or ""})
        if not out and self.omdb and parsed.get("title"):          # TMDB unavailable or empty: OMDb by title
            data = self.fetch(OMDB, {"t": parsed["title"], "type": "series" if want == "tv" else "movie", **({"y": parsed["year"]} if parsed.get("year") and want == "movie" else {}), "apikey": self.omdb}, {}) or {}
            if data.get("Response") == "True" and data.get("imdbID"):
                year = int(data["Year"][:4]) if str(data.get("Year", ""))[:4].isdigit() else None
                out.append({"kind": want, "tmdb_id": 0, "imdb_id": data["imdbID"], "title": data.get("Title", ""), "year": year, "source": "omdb",
                            "score": round(similarity(parsed["title"], data.get("Title", "")), 3), "poster": "", "overview": data.get("Plot") or ""})
        return sorted(out, key=lambda c: -c["score"])

    def by_hash(self, file_hash: str, size: int = 0) -> dict | None:
        """OpenSubtitles knows files by hash: an exact answer when the file is a known release."""
        if not (self.os_key and file_hash):
            return None
        data = self.fetch(OPENSUBTITLES, {"moviehash": file_hash}, {"Api-Key": self.os_key, "Accept": "application/json", "User-Agent": "ArchiveX v1"}) or {}
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
        """Everything kept about a title: text, artwork, genres, studios or networks, cast."""
        d = self._t(f"/{kind}/{int(tmdb_id)}", append_to_response="credits,external_ids")
        if not d:
            return {}
        genres = [g["name"] for g in d.get("genres") or [] if g.get("name")]
        companies = [c["name"] for c in (d.get("networks") if kind == "tv" and d.get("networks") else d.get("production_companies")) or [] if c.get("name")]
        cast = [{"name": p["name"], "tmdb_id": p.get("id"), "character": p.get("character") or "", "profile": p.get("profile_path") or ""}
                for p in ((d.get("credits") or {}).get("cast") or [])[:20] if p.get("name")]
        date = d.get("first_air_date") if kind == "tv" else d.get("release_date")
        return {"kind": kind, "tmdb_id": int(d["id"]), "imdb_id": (d.get("external_ids") or {}).get("imdb_id") or d.get("imdb_id") or "",
                "title": (d.get("name") if kind == "tv" else d.get("title")) or "", "original_title": (d.get("original_name") if kind == "tv" else d.get("original_title")) or "",
                "year": int(date[:4]) if date and date[:4].isdigit() else None, "release_date": date or "", "overview": d.get("overview") or "", "tagline": d.get("tagline") or "",
                "poster": d.get("poster_path") or "", "backdrop": d.get("backdrop_path") or "", "genres": genres, "companies": companies, "cast": cast,
                "rating": d.get("vote_average") or 0, "runtime": (d.get("runtime") or (d.get("episode_run_time") or [0])[0] or 0),
                "seasons": [{"season": s.get("season_number"), "episodes": s.get("episode_count"), "name": s.get("name") or ""} for s in d.get("seasons") or [] if s.get("season_number") is not None],
                "status": d.get("status") or ""}

    def episode(self, tmdb_id: int, season: int, episode: int) -> dict:
        e = self._t(f"/tv/{int(tmdb_id)}/season/{int(season)}/episode/{int(episode)}")
        return {"season": int(season), "episode": int(episode), "name": e.get("name") or "", "overview": e.get("overview") or "", "air_date": e.get("air_date") or "",
                "still": e.get("still_path") or "", "runtime": e.get("runtime") or 0} if e else {}


def image_url(path: str, size: str = "w500") -> str:
    return f"{IMAGE}/{size}{path}" if path else ""


def identify(screen: Screen, path: str, *, file_hash: str = "") -> dict:
    """Name and hash in, an answer out: ``{state: MATCHED|REVIEW|UNMATCHED, best, candidates, parsed, season, episode}``."""
    parsed = parse_name(path)
    hit = screen.by_hash(file_hash) if file_hash else None
    if hit and hit.get("tmdb_id"):
        return {"state": "MATCHED", "best": hit, "candidates": [hit], "parsed": parsed, "season": hit.get("season") or parsed["season"], "episode": hit.get("episode") or parsed["episode"]}
    candidates = screen.search(parsed)
    best = candidates[0] if candidates else None
    state = "UNMATCHED" if not best or best["score"] < REVIEW else "MATCHED" if best["score"] >= ACCEPT else "REVIEW"
    return {"state": state, "best": best if state != "UNMATCHED" else None, "candidates": candidates[:5], "parsed": parsed, "season": parsed["season"], "episode": parsed["episode"]}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
