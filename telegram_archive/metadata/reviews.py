"""Critics' scores and what critics said, for a title, from Wikidata and Wikipedia: nothing to sign up for, no key.

Wikidata keeps a "review score" for many films and shows (Rotten Tomatoes' percentage, Metacritic's number), found by the title's IMDb number, and the
title's English Wikipedia article. The scores are shown beside the IMDb rating. The summary is the opening of the article's "Critical response" (or
"Reception") section, with a link to read the rest there.

Both services are public HTTPS and are asked through the same checked fetch the add-ons use. Only the IMDb number leaves this PC. An answer is kept for
half a day, a failed one for ten minutes.
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
from html.parser import HTMLParser

from . import addon_client as ac

WIKIDATA = "https://query.wikidata.org/sparql"
WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
KEEP_SECONDS = 12 * 3600
RETRY_SECONDS = 600
SUMMARY_CHARS = 900
SOURCES = ("Rotten Tomatoes", "Metacritic")
_SECTIONS = re.compile(r"^(critical (response|reception)|reception)$", re.I)
_cache: dict[str, tuple[float, dict]] = {}


def sparql_for(imdb: str) -> str:
    """The Wikidata question: the review scores (with who gave them and when) and the English Wikipedia article of the item whose IMDb number this is."""
    return (
        "SELECT ?by ?score ?when ?article WHERE { "
        f'?item wdt:P345 "{imdb}". '
        "OPTIONAL { ?item p:P444 ?st. ?st ps:P444 ?score; pq:P447 ?source. ?source rdfs:label ?by. FILTER(LANG(?by)=\"en\") OPTIONAL { ?st pq:P585 ?when } } "
        "OPTIONAL { ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. } "
        "} LIMIT 60")


def _value(binding: dict, key: str) -> str:
    v = binding.get(key)
    return str(v.get("value") or "") if isinstance(v, dict) else ""


def _shown(source: str, raw: str) -> str:
    """"92%" stays "92%"; Metacritic's "79/100" is shown as "79"."""
    text = raw.strip()
    m = re.fullmatch(r"(\d{1,3})\s*/\s*100", text)
    if m:
        return m.group(1)
    return text[:12]


def parse_scores(bindings) -> tuple[list[dict], str]:
    """(the newest score of each source, the title of the English Wikipedia article); either may be empty."""
    best: dict[str, tuple[str, str]] = {}
    article = ""
    for b in bindings if isinstance(bindings, list) else []:
        if not isinstance(b, dict):
            continue
        link = _value(b, "article")
        if link and not article and link.startswith("https://en.wikipedia.org/wiki/"):
            article = urllib.parse.unquote(link[len("https://en.wikipedia.org/wiki/"):]).replace("_", " ")[:200]
        by, score, when = _value(b, "by"), _value(b, "score"), _value(b, "when")
        if by in SOURCES and re.fullmatch(r"\d{1,3}(\s*/\s*100|%)?", score.strip()):
            if by not in best or when > best[by][0]:       # the newest statement wins (an empty date is the oldest)
                best[by] = (when, score)
    return [{"source": s, "value": _shown(s, best[s][1])} for s in SOURCES if s in best], article


class _Text(HTMLParser):
    """Paragraph text of a Wikipedia section: footnote marks, tables, scripts and styles are left out."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._skip = 0
        self._in_p = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("sup", "style", "script", "table", "figure", "ul", "ol"):
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._in_p, self._buf = True, []

    def handle_endtag(self, tag):
        if tag in ("sup", "style", "script", "table", "figure", "ul", "ol"):
            self._skip = max(0, self._skip - 1)
        elif tag == "p" and self._in_p:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if text:
                self.paragraphs.append(text)
            self._in_p = False

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buf.append(data)


def summary_text(section_html: str) -> str:
    """The opening of a section as plain text: whole sentences, at most about SUMMARY_CHARS characters."""
    parser = _Text()
    parser.feed(section_html or "")
    text = " ".join(parser.paragraphs)
    text = re.sub(r"\[\d+\]|\[[a-z]\]", "", html.unescape(text))
    if len(text) <= SUMMARY_CHARS:
        return text.strip()
    cut = text[:SUMMARY_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > 200 else cut.rstrip() + "…").strip()


def _wikipedia(params: dict) -> dict:
    got = ac.get_json(WIKIPEDIA + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2", "redirects": "1"}))
    return got if isinstance(got, dict) else {}


def critics_summary(article: str) -> dict | None:
    """What critics said, from the article's reception section, with the link to the article; None when it has none."""
    if not article:
        return None
    sections = (_wikipedia({"action": "parse", "page": article, "prop": "sections"}).get("parse") or {}).get("sections") or []
    pick = next((s for s in sections if isinstance(s, dict) and _SECTIONS.match(str(s.get("line") or "").strip())), None)
    if not pick or not str(pick.get("index") or "").isdigit():
        return None
    body = (_wikipedia({"action": "parse", "page": article, "section": pick["index"], "prop": "text", "disabletoc": "1"}).get("parse") or {}).get("text")
    text = summary_text(body if isinstance(body, str) else "")
    if not text:
        return None
    return {"text": text, "title": article, "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(article.replace(" ", "_"), safe="_:(),'!-.")}


def reviews_for(imdb: str, *, now=time.monotonic) -> dict:
    """{"scores": [{"source", "value"}], "summary": {"text", "title", "url"} | None} for an IMDb number; empty parts when nothing is known or a service does not answer."""
    if not re.fullmatch(r"tt\d{5,10}", imdb or ""):
        raise ValueError("Unknown title")
    hit = _cache.get(imdb)
    if hit and now() < hit[0]:
        return hit[1]
    scores: list[dict] = []
    summary = None
    failed = False
    article = ""
    try:
        got = ac.get_json(WIKIDATA + "?" + urllib.parse.urlencode({"query": sparql_for(imdb), "format": "json"}))
        scores, article = parse_scores(((got or {}).get("results") or {}).get("bindings") if isinstance(got, dict) else [])
    except Exception:
        failed = True
    try:
        summary = critics_summary(article)
    except Exception:
        failed = failed or bool(article)
    out = {"scores": scores, "summary": summary}
    if len(_cache) > 300:
        _cache.clear()
    _cache[imdb] = (now() + (RETRY_SECONDS if failed and not scores and not summary else KEEP_SECONDS), out)
    return out
