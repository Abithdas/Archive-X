"""The rules of the Collection (Archive X's small take on Sonarr and Radarr): which release to take, and where the file goes. Pure functions: no network, no database.

Sonarr and Radarr are large because they cover everything (usenet, many indexers, upgrade chains, custom formats). This keeps the part people use: one quality
they want, a size they will accept, no cam recordings, and whichever copy has the most people sharing it.
"""
from __future__ import annotations

import math
import re

QUALITIES = (720, 1080, 2160)
DEFAULT_QUALITY = 1080
# Recordings of a cinema screen or a rip nobody should keep: the word has to stand alone ("TS" inside "FITS" is not one).
_BAD = re.compile(r"(?<![a-z0-9])(cam|camrip|hdcam|hd-cam|ts|hdts|hd-ts|telesync|tc|telecine|hdtc|scr|screener|dvdscr|r5|workprint|sample)(?![a-z0-9])", re.I)
_SEASON_PACK = re.compile(r"(?<![a-z0-9])(complete|season\s*\d+\s*(?:complete|pack)|s\d{1,2}(?![e\d])|all\s+seasons)(?![a-z0-9])", re.I)
GB = 1024 ** 3


def is_bad_release(name: str) -> bool:
    return bool(_BAD.search(str(name or "")))


def resolution_score(height: int, target: int) -> float:
    """100 for the quality that was asked for; a higher one is next best (it plays, it only costs more space); a lower one loses more with every step below."""
    if not height:
        return 30.0          # the name says nothing: possible, but behind anything that says what it is
    steps = QUALITIES.index(min(QUALITIES, key=lambda q: abs(q - height)))
    want = QUALITIES.index(min(QUALITIES, key=lambda q: abs(q - target)))
    if steps == want:
        return 100.0
    return 80.0 - 12.0 * (steps - want) if steps > want else 60.0 - 22.0 * (want - steps)


def score(item: dict, target: int) -> float:
    seeders = item.get("seeders")
    s = resolution_score(int(item.get("height") or 0), target)
    if isinstance(seeders, int):
        s += 0.0 if seeders <= 0 else min(25.0, 8.0 * math.log10(seeders + 1))
        if seeders <= 0:
            s -= 60.0      # nobody is sharing it
    if item.get("cached"):
        s += 2.0
    return s


def pick_release(items: list[dict], *, quality: int = DEFAULT_QUALITY, max_bytes: int = 0, avoid: set[str] | None = None, episode: bool = False) -> dict | None:
    """The best torrent among an add-on's streams for one title or episode, or None when none is acceptable.

    Not acceptable: not a torrent, one that failed before (`avoid`: info hashes), a cam recording, one over `max_bytes`, a whole-season pack when one episode is wanted
    (it would download every episode), one nobody is sharing. Among the rest: the asked-for quality first, then the one with the most seeders."""
    avoid = {a.lower() for a in (avoid or set())}
    best: tuple[float, dict] | None = None
    for it in items:
        if it.get("kind") != "torrent" or not it.get("infoHash"):
            continue
        if str(it["infoHash"]).lower() in avoid or is_bad_release(it.get("name", "")):
            continue
        size = int(it.get("size") or 0)
        if max_bytes and size and size > max_bytes:
            continue
        if episode and it.get("fileIdx") is None and _SEASON_PACK.search(str(it.get("name") or "")):
            continue       # a pack with no file named for this episode
        if isinstance(it.get("seeders"), int) and it["seeders"] <= 0 and not it.get("cached"):
            continue
        sc = score(it, quality)
        if best is None or sc > best[0]:
            best = (sc, it)
    return best[1] if best else None


# ---- names ---------------------------------------------------------------------------------------------------------------------------------------
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EXT = {"video/x-matroska": ".mkv", "video/mp4": ".mp4", "video/x-msvideo": ".avi", "video/webm": ".webm", "video/quicktime": ".mov", "video/mpeg": ".mpg", "video/x-m4v": ".m4v"}


def safe_name(text: str, limit: int = 110) -> str:
    """A folder or file name that every file system accepts."""
    cleaned = re.sub(r"\s+", " ", _ILLEGAL.sub(" ", str(text or ""))).strip().rstrip(". ")
    return (cleaned[:limit].rstrip(". ") or "Untitled")


def extension_for(content_type: str, fallback: str = ".mkv") -> str:
    return _EXT.get(str(content_type or "").split(";")[0].strip().lower(), fallback)


def movie_path(root: str, title: str, year: int | None, ext: str) -> tuple[str, ...]:
    """Movies/Title (Year)/Title (Year).ext, as Radarr and every media server expects it."""
    label = safe_name(f"{title} ({year})" if year else title)
    return ("Movies", label, label + ext)


def episode_path(root: str, title: str, year: int | None, season: int, episode: int, ext: str) -> tuple[str, ...]:
    """Series/Title (Year)/Season 01/Title - S01E02.ext"""
    show = safe_name(f"{title} ({year})" if year else title)
    return ("Series", show, f"Season {season:02d}", f"{safe_name(title)} - S{season:02d}E{episode:02d}{ext}")


def release_label(season: int, episode: int, name: str = "") -> str:
    """How an episode is named on screen: "S01E02" with its title when known; a movie has no number."""
    if not season and not episode:
        return name or "Movie"
    return f"S{season:02d}E{episode:02d}" + (f" · {name}" if name else "")
