"""Critics' scores and summary from Wikidata and Wikipedia: read strictly, kept for a while, never blocking the title page, SFW only."""
import json
import urllib.parse

import pytest

from telegram_archive.core.database import Database
from telegram_archive.metadata import addon_client as ac
from telegram_archive.metadata import reviews as rv
from test_screen_home import H, PUBLIC, _client

BINDINGS = [
    {"by": {"value": "Rotten Tomatoes"}, "score": {"value": "92%"}, "when": {"value": "2024-04-01T00:00:00Z"}, "article": {"value": "https://en.wikipedia.org/wiki/Dune:_Part_Two"}},
    {"by": {"value": "Rotten Tomatoes"}, "score": {"value": "88%"}, "when": {"value": "2024-02-01T00:00:00Z"}, "article": {"value": "https://en.wikipedia.org/wiki/Dune:_Part_Two"}},
    {"by": {"value": "Metacritic"}, "score": {"value": "79/100"}, "article": {"value": "https://en.wikipedia.org/wiki/Dune:_Part_Two"}},
    {"by": {"value": "IMDb"}, "score": {"value": "8.4/10"}},
    {"by": {"value": "Rotten Tomatoes"}, "score": {"value": "<script>alert(1)</script>"}},
]
SECTIONS = {"parse": {"sections": [{"index": "1", "line": "Plot"}, {"index": "5", "line": "Critical response"}, {"index": "6", "line": "Accolades"}]}}
SECTION = {"parse": {"text": "<div><p>On <b>Rotten Tomatoes</b>, the film holds 92%.<sup>[12]</sup> Critics praised the visuals.</p><table><tr><td>Rating table</td></tr></table>"
                              "<style>.x{}</style><p>Metacritic gave 79 out of 100, &quot;generally favourable&quot;.</p></div>"}}


class Wiki:
    """A stand-in for the two services: records what was asked."""

    def __init__(self, *, wikidata=None, sections=SECTIONS, section=SECTION, fail=()):
        self.asked: list[str] = []
        self.wikidata = {"results": {"bindings": BINDINGS}} if wikidata is None else wikidata
        self.sections, self.section, self.fail = sections, section, set(fail)

    def __call__(self, url):
        self.asked.append(url)
        parts = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(parts.query)
        if parts.netloc == "query.wikidata.org":
            if "wikidata" in self.fail:
                raise OSError("down")
            return self.wikidata
        if parts.netloc == "en.wikipedia.org":
            if "wikipedia" in self.fail:
                raise OSError("down")
            return self.section if "section" in q else self.sections
        raise AssertionError("an unexpected address was asked: " + url)


@pytest.fixture(autouse=True)
def fresh():
    rv._cache.clear()
    yield
    rv._cache.clear()


def test_scores_are_the_newest_of_each_source_and_nothing_odd_gets_through():
    scores, article = rv.parse_scores([b for b in BINDINGS])
    assert scores == [{"source": "Rotten Tomatoes", "value": "92%"}, {"source": "Metacritic", "value": "79"}]     # the newest RT, Metacritic as a number, no IMDb, no script
    assert article == "Dune: Part Two"
    assert rv.parse_scores([]) == ([], "") and rv.parse_scores(None) == ([], "") and rv.parse_scores([5, "x", {}]) == ([], "")
    assert rv.parse_scores([{"by": {"value": "Metacritic"}, "score": {"value": "1000000"}}]) == ([], "")


def test_the_summary_is_plain_whole_sentences_from_the_reception_section(monkeypatch):
    monkeypatch.setattr(ac, "get_json", Wiki())
    out = rv.reviews_for("tt1160419")
    assert out["scores"][0] == {"source": "Rotten Tomatoes", "value": "92%"}
    assert out["summary"]["text"] == 'On Rotten Tomatoes, the film holds 92%. Critics praised the visuals. Metacritic gave 79 out of 100, "generally favourable".'
    assert out["summary"]["title"] == "Dune: Part Two" and out["summary"]["url"] == "https://en.wikipedia.org/wiki/Dune:_Part_Two"
    long = "<p>" + " ".join(f"Sentence number {i} says something about the film." for i in range(80)) + "</p>"
    text = rv.summary_text(long)
    assert len(text) <= rv.SUMMARY_CHARS and text.endswith(".") and text.startswith("Sentence number 0")


def test_the_second_ask_is_answered_from_memory_and_only_the_imdb_number_is_sent(monkeypatch):
    wiki = Wiki()
    monkeypatch.setattr(ac, "get_json", wiki)
    first = rv.reviews_for("tt1160419")
    n = len(wiki.asked)
    assert rv.reviews_for("tt1160419") == first and len(wiki.asked) == n
    assert "tt1160419" in urllib.parse.unquote(wiki.asked[0]) and not any("api_key" in u or "token" in u for u in wiki.asked)
    with pytest.raises(ValueError):
        rv.reviews_for("not-an-id")


def test_a_service_that_is_down_costs_only_its_own_part_and_is_retried_soon(monkeypatch):
    clock = {"t": 1000.0}
    wiki = Wiki(fail=("wikipedia",))
    monkeypatch.setattr(ac, "get_json", wiki)
    out = rv.reviews_for("tt1160419", now=lambda: clock["t"])
    assert out["scores"] and out["summary"] is None                                          # the scores still show
    nothing = Wiki(fail=("wikidata", "wikipedia"))
    monkeypatch.setattr(ac, "get_json", nothing)
    rv._cache.clear()
    assert rv.reviews_for("tt0000001", now=lambda: clock["t"]) == {"scores": [], "summary": None}
    asked = len(nothing.asked)
    clock["t"] += rv.RETRY_SECONDS - 1
    rv.reviews_for("tt0000001", now=lambda: clock["t"])
    assert len(nothing.asked) == asked                                                       # not asked again at once
    clock["t"] += 2
    rv.reviews_for("tt0000001", now=lambda: clock["t"])
    assert len(nothing.asked) > asked                                                        # but soon


def test_a_title_without_scores_or_an_article_is_just_empty(monkeypatch):
    monkeypatch.setattr(ac, "get_json", Wiki(wikidata={"results": {"bindings": []}}))
    assert rv.reviews_for("tt0000002") == {"scores": [], "summary": None}
    monkeypatch.setattr(ac, "get_json", Wiki(sections={"parse": {"sections": [{"index": "1", "line": "Plot"}]}}))
    rv._cache.clear()
    assert rv.reviews_for("tt1160419")["summary"] is None                                    # an article with no reception section


def test_the_route_answers_in_sfw_and_is_empty_in_nsfw(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "get_json", Wiki())
    monkeypatch.setattr(ac, "_resolve", lambda host: PUBLIC)
    c = _client(Database(tmp_path / "r.db"))
    got = c.get("/api/screen/reviews/movie/tt1160419").json()
    assert got["ok"] is True and [s["source"] for s in got["scores"]] == ["Rotten Tomatoes", "Metacritic"] and got["summary"]["url"].startswith("https://en.wikipedia.org/wiki/")
    assert c.get("/api/screen/reviews/movie/nope").status_code == 400 and c.get("/api/screen/reviews/book/tt1160419").status_code == 400
    assert c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H).json()["mode"] == "nsfw"
    rv._cache.clear()
    wiki = Wiki()
    monkeypatch.setattr(ac, "get_json", wiki)
    assert c.get("/api/screen/reviews/movie/tt1160419").json() == {"ok": True, "scores": [], "summary": None} and wiki.asked == []     # nothing was asked of Wikipedia


def test_the_library_title_pages_carry_the_imdb_number_so_reviews_can_be_asked_for_them(tmp_path):
    from test_screen_home import _library
    db = Database(tmp_path / "lib.db")
    _library(db)
    c = _client(db)
    movie = db.conn.execute("SELECT id FROM screen_titles WHERE imdb_id='tt0133093'").fetchone()["id"]
    show = db.conn.execute("SELECT id FROM screen_titles WHERE imdb_id='tt0903747'").fetchone()["id"]
    assert c.get(f"/api/screen/movies/{movie}").json()["imdb"] == "tt0133093"
    assert c.get(f"/api/screen/series/{show}").json()["imdb"] == "tt0903747"
