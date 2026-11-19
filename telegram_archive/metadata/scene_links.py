"""The page of a scene at its provider, and every picture the provider gave for it, from what the matcher stored.

The review shows these so a person can open the provider's own page and look at several pictures, not one. Nothing here talks to a provider:
it only reads the stored candidate.
"""
from __future__ import annotations

from typing import Any

# Where a scene lives on each provider's own site. The record id is the one the provider gave.
SCENE_PAGES = {
    "stashdb": "https://stashdb.org/scenes/{id}",
    "theporndb": "https://theporndb.net/scenes/{id}",
    "tpdb": "https://theporndb.net/scenes/{id}",
}
PROVIDER_NAMES = {"stashdb": "StashDB", "theporndb": "ThePornDB", "tpdb": "ThePornDB"}


def _http(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


def provider_scene_url(provider: Any, record_id: Any, payload: dict | None = None) -> str:
    """The provider's own address for the scene when it sent one, else the standard page for its id, else ''."""
    payload = payload if isinstance(payload, dict) else {}
    for key in ("profile", "url"):
        if _http(payload.get(key)):
            return _http(payload[key])
    links = payload.get("links")
    if isinstance(links, dict):
        for value in links.values():
            if _http(value):
                return _http(value)
    pattern = SCENE_PAGES.get(str(provider or "").strip().lower())
    rid = str(record_id or payload.get("id") or "").strip()
    return pattern.format(id=rid) if pattern and rid and "/" not in rid else ""


def _urls(value: Any):
    """Every address inside a string, a list of strings or {url: ...} rows, or a {size: address} dict."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        direct = value.get("url") or value.get("src") or value.get("image")
        if direct:
            yield direct
        else:
            for v in value.values():
                yield from _urls(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _urls(v)


def provider_scene_images(payload: dict | None, limit: int = 6) -> list[str]:
    """Distinct picture addresses for the scene, the main picture first."""
    payload = payload if isinstance(payload, dict) else {}
    out: list[str] = []
    for key in ("image", "thumbnail", "poster", "background", "images"):
        for raw in _urls(payload.get(key)):
            url = _http(raw)
            if url and url not in out:
                out.append(url)
            if len(out) >= limit:
                return out
    return out
