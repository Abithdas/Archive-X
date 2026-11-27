"""The Collection's rules: which release to take and where the file goes."""
from telegram_archive.engines import collection_rules as r

GB = r.GB


def t(name, height=0, seeders=None, size=0, h="a" * 40, **kw):
    return {"kind": "torrent", "name": name, "height": height, "seeders": seeders, "size": size, "infoHash": h, **kw}


def test_cam_recordings_are_recognised_as_words_only():
    for bad in ("Movie.2024.HDCAM.x264", "Movie 2024 TS 720p", "Movie.2024.Telesync", "Movie.2024.DVDScr", "Movie 2024 CAM"):
        assert r.is_bad_release(bad), bad
    for ok in ("The.Fits.2015.1080p.BluRay", "Camelot.1967.1080p", "Rise.of.the.Tcs.2020", "Movie.2024.1080p.WEB-DL"):
        assert not r.is_bad_release(ok), ok


def test_the_asked_for_quality_wins_then_higher_then_lower():
    assert r.resolution_score(1080, 1080) > r.resolution_score(2160, 1080) > r.resolution_score(720, 1080) > r.resolution_score(0, 1080)
    assert r.resolution_score(2160, 2160) == 100 and r.resolution_score(720, 2160) < r.resolution_score(1080, 2160)


def test_the_best_release_is_the_right_quality_with_people_sharing_it():
    items = [t("A 720p", 720, 500, h="1" * 40), t("B 1080p few", 1080, 3, h="2" * 40), t("C 1080p many", 1080, 400, h="3" * 40), t("D 2160p", 2160, 900, h="4" * 40)]
    assert r.pick_release(items, quality=1080)["infoHash"] == "3" * 40
    assert r.pick_release(items, quality=2160)["infoHash"] == "4" * 40
    assert r.pick_release(items, quality=720)["infoHash"] == "1" * 40


def test_nothing_unfit_is_taken():
    items = [t("Movie HDCAM 1080p", 1080, 900), t("Movie 1080p dead", 1080, 0), t("Movie 1080p huge", 1080, 90, size=40 * GB), {"kind": "play", "name": "direct"}, {"kind": "torrent", "name": "no hash"}]
    assert r.pick_release(items, quality=1080, max_bytes=10 * GB) is None


def test_a_release_that_failed_before_is_not_tried_again_and_the_next_best_is():
    items = [t("A 1080p", 1080, 900, h="a" * 40), t("B 1080p", 1080, 100, h="b" * 40)]
    assert r.pick_release(items, avoid={"A" * 40})["infoHash"] == "b" * 40       # compared without regard to case


def test_a_season_pack_is_not_taken_for_one_episode_unless_it_names_the_file():
    pack = t("Show.S01.COMPLETE.1080p", 1080, 900, h="p" * 40)
    one = t("Show.S01E02.720p", 720, 50, h="e" * 40)
    assert r.pick_release([pack, one], quality=1080, episode=True)["infoHash"] == "e" * 40
    named = {**pack, "fileIdx": 3}
    assert r.pick_release([named, one], quality=1080, episode=True)["infoHash"] == "p" * 40


def test_a_stream_with_no_seeder_count_is_still_possible():
    assert r.pick_release([t("Movie 1080p", 1080, None)], quality=1080) is not None


def test_names_are_safe_and_follow_the_media_server_layout():
    assert r.safe_name('A: B/C?*"<>|  D...') == "A B C D"
    assert r.movie_path("/m", "The Matrix", 1999, ".mkv") == ("Movies", "The Matrix (1999)", "The Matrix (1999).mkv")
    assert r.episode_path("/m", "Dark", 2017, 3, 2, ".mp4") == ("Series", "Dark (2017)", "Season 03", "Dark - S03E02.mp4")
    assert r.extension_for("video/x-matroska; charset=x") == ".mkv" and r.extension_for("application/octet-stream") == ".mkv" and r.extension_for("video/mp4") == ".mp4"
    assert r.release_label(1, 2, "Pilot") == "S01E02 · Pilot" and r.release_label(0, 0, "") == "Movie"
