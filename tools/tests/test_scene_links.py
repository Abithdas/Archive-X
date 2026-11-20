"""The provider page link and the provider pictures of a review candidate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from telegram_archive.metadata import providers as pv  # noqa: E402
from telegram_archive.metadata import scene_links as sl  # noqa: E402


def test_the_providers_own_address_wins_then_the_standard_page_for_its_id():
    assert sl.provider_scene_url("stashdb", "abc", {"profile": "https://stashdb.org/scenes/own"}) == "https://stashdb.org/scenes/own"
    assert sl.provider_scene_url("stashdb", "abc", {"links": {"home": "https://x.example/s"}}) == "https://x.example/s"
    assert sl.provider_scene_url("stashdb", "0a1b-2c", {}) == "https://stashdb.org/scenes/0a1b-2c"
    assert sl.provider_scene_url("theporndb", "42", {"profile": ""}) == "https://theporndb.net/scenes/42"
    assert sl.provider_scene_url("TPDB", None, {"id": "7"}) == "https://theporndb.net/scenes/7"


def test_no_link_is_made_up_for_an_unknown_provider_or_a_missing_or_odd_id():
    assert sl.provider_scene_url("somewhere", "1", {}) == ""
    assert sl.provider_scene_url("stashdb", "", {}) == ""
    assert sl.provider_scene_url("stashdb", "../x", {}) == ""
    assert sl.provider_scene_url("stashdb", "1", {"profile": "javascript:alert(1)"}) == "https://stashdb.org/scenes/1"   # a non-web address is never used


def test_pictures_come_from_every_field_main_first_without_repeats_and_only_web_addresses():
    payload = {"image": "https://p/1.jpg", "thumbnail": "https://p/1.jpg", "images": [{"url": "https://p/2.jpg", "width": 9}, "https://p/3.jpg", "ftp://p/4.jpg"],
               "background": {"large": "https://p/5.jpg", "small": "https://p/6.jpg"}}
    got = sl.provider_scene_images(payload)
    assert got[0] == "https://p/1.jpg" and len(got) == len(set(got)) and all(u.startswith("https://") for u in got) and "https://p/3.jpg" in got and "https://p/2.jpg" in got
    assert len(sl.provider_scene_images({"images": [f"https://p/{i}.jpg" for i in range(20)]})) == 6
    assert sl.provider_scene_images({}) == [] and sl.provider_scene_images(None) == []


def test_the_providers_answers_now_keep_all_their_pictures():
    stash = pv._norm_stashdb({"id": "s1", "title": "T", "images": [{"url": "https://s/a.jpg", "width": 100, "height": 50}, {"url": "https://s/b.jpg", "width": 10, "height": 10}]})
    assert stash["images"] == ["https://s/a.jpg", "https://s/b.jpg"] and stash["image"] == "https://s/a.jpg"
    tpdb = pv._norm_tpdb({"id": "t1", "title": "T", "image": "https://t/main.jpg", "poster": "https://t/poster.jpg", "background": {"full": "https://t/bg.jpg", "small": "https://t/bg-s.jpg"}, "images": {"large": "https://t/main.jpg"}})
    assert tpdb["images"][0] == "https://t/main.jpg" and "https://t/poster.jpg" in tpdb["images"] and "https://t/bg.jpg" in tpdb["images"] and len(tpdb["images"]) == len(set(tpdb["images"]))


def test_a_merged_candidate_has_the_pictures_of_both_providers():
    merged = pv.merge_provider_matches({"title": "T", "images": ["https://s/a.jpg"], "image": "https://s/a.jpg"}, {"images": ["https://s/a.jpg", "https://t/b.jpg"]})
    assert merged["images"] == ["https://s/a.jpg", "https://t/b.jpg"]
