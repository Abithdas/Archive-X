"""Add-ons: the three that come with the app can be removed and added back, a removed one leaves Discover, and none of it exists in NSFW mode."""
import pytest

from telegram_archive.core.database import Database
from telegram_archive.engines import screen_identify as sid
from test_screen_home import H, _client


@pytest.fixture
def app(tmp_path):
    db = Database(tmp_path / "a.db")
    return db, _client(db)


def ids(view):
    return [b["id"] for b in view["builtin"]]


def test_every_built_in_add_on_can_be_removed_and_added_back(app):
    db, c = app
    view = c.get("/api/screen/addons").json()
    assert ids(view) == ["cinemeta", "streaming", "anime"] and view["removed"] == []
    for key in ("cinemeta", "streaming", "anime"):
        view = c.delete(f"/api/screen/addons/{key}", headers=H).json()
        assert key not in ids(view) and [r["id"] for r in view["removed"]] == [k for k in ("cinemeta", "streaming", "anime") if k not in ids(view)]
    assert ids(view) == [] and len(view["removed"]) == 3
    view = c.post("/api/screen/addons/streaming/restore", headers=H).json()
    assert ids(view) == ["streaming"] and len(view["removed"]) == 2
    assert c.post("/api/screen/addons/nonsense/restore", headers=H).status_code == 404


def test_a_removal_survives_a_restart(app, tmp_path):
    db, c = app
    c.delete("/api/screen/addons/anime", headers=H)
    again = Database(tmp_path / "a.db")
    assert sid.removed_builtins(again) == {"anime"} and sid.builtin_on(again, "anime") is False and sid.builtin_on(again, "streaming") is True


def test_a_removed_add_on_takes_only_its_own_lists_out_of_discover(app, monkeypatch):
    db, c = app
    c.delete("/api/screen/addons/anime", headers=H)
    gone = c.get("/api/screen/discover?type=series&catalog=anime-airing").json()
    assert gone == {"ok": True, "disabled": True, "items": []}
    c.delete("/api/screen/addons/streaming", headers=H)
    assert c.get("/api/screen/discover?type=movie&catalog=netflix").json()["disabled"] is True
    c.delete("/api/screen/addons/cinemeta", headers=H)
    assert c.get("/api/screen/discover?type=movie&catalog=popular").json()["disabled"] is True
    assert sid.cinemeta_on(db) is False


def test_removing_one_does_not_switch_the_others_off(app):
    db, c = app
    c.delete("/api/screen/addons/cinemeta", headers=H)
    assert sid.builtin_on(db, "anime") is True and sid.builtin_on(db, "streaming") is True and sid.cinemeta_on(db) is False
    assert c.get("/api/screen/settings").json()["cinemeta_enabled"] is True        # Settings, Metadata still shows the whole-catalogue switch as on


def test_the_whole_catalogue_switch_still_turns_every_built_in_off(app):
    db, c = app
    c.post("/api/screen/settings", json={"cinemeta_enabled": False}, headers=H)
    assert not any(sid.builtin_on(db, k) for k in sid.BUILTIN_IDS)


def test_in_nsfw_mode_add_ons_are_not_available(app):
    db, c = app
    assert c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H).json()["mode"] == "nsfw"
    for method, url, body in (("get", "/api/screen/addons", None), ("delete", "/api/screen/addons/anime", None), ("post", "/api/screen/addons/anime/restore", None),
                              ("post", "/api/screen/addons", {"url": "https://good.example/manifest.json"}), ("post", "/api/screen/addons/settings", {"allow_local": True}),
                              ("get", "/api/screen/discover?type=movie", None), ("get", "/api/screen/streams/movie/tt0133093", None)):
        r = getattr(c, method)(url, headers=H, **({"json": body} if body is not None else {}))
        assert r.status_code == 409, (url, r.status_code)
    # asking for details or subtitles from add-ons simply finds nothing, and nothing was asked of an add-on
    assert c.get("/api/screen/addon-meta/movie/tt0133093").json() == {"ok": True, "items": []}
    assert c.get("/api/screen/subtitles-for/movie/tt0133093").json() == {"ok": True, "items": []}
    assert sid.removed_builtins(db) == set()                    # and the refused removal changed nothing
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).json()["mode"] == "sfw"
    assert c.get("/api/screen/addons").status_code == 200
