"""Starting Stremio's streaming server for torrents from the app: found only in its install places, started detached, waited for until it answers."""
from telegram_archive.metadata import streaming_server as ss

BASE = "http://127.0.0.1:11470"


def test_the_service_is_found_in_the_places_its_installers_use():
    env = {"ProgramFiles": "C:/Program Files", "LOCALAPPDATA": "C:/Users/me/AppData/Local"}
    seen = []
    assert ss.installed_program(env, exists=lambda p: seen.append(p) or p.replace("\\", "/").endswith("StremioService/stremio-service.exe")).replace("\\", "/") == "C:/Program Files/StremioService/stremio-service.exe"
    local_only = ss.installed_program({"LOCALAPPDATA": "C:/L"}, exists=lambda p: p.replace("\\", "/").endswith("Programs/LNV/Stremio-4/stremio.exe"))
    assert local_only.replace("\\", "/") == "C:/L/Programs/LNV/Stremio-4/stremio.exe"          # Stremio itself runs the same server
    assert ss.installed_program({}, exists=lambda p: True) == ""                                # no system folders to look in: nothing is guessed


def test_it_is_not_started_when_it_already_answers():
    launched = []
    out = ss.start(BASE, program="x.exe", launch=lambda: launched.append(1), probe=lambda b: True)
    assert out["up"] is True and out["started"] is False and not launched


def test_it_says_so_when_it_is_not_installed():
    out = ss.start(BASE, program="", launch=lambda: None, probe=lambda b: False)
    assert out["up"] is False and out["found"] is False and "not set up" in out["reason"]


def test_it_is_started_and_waited_for_until_it_answers():
    answers = iter([False, False, False, True])
    launched = []
    out = ss.start(BASE, program="stremio-service.exe", launch=lambda: launched.append(1), probe=lambda b: next(answers), sleep=lambda s: None)
    assert out["up"] is True and out["started"] is True and launched == [1]


def test_a_server_that_never_answers_is_reported_not_raised():
    out = ss.start(BASE, program="stremio-service.exe", launch=lambda: None, probe=lambda b: False, wait=2.0, sleep=lambda s: None)
    assert out["up"] is False and out["started"] is True and "not answering" in out["reason"]


def test_a_failed_start_is_reported():
    def boom():
        raise OSError(2, "No such file")
    out = ss.start(BASE, program="gone.exe", launch=boom, probe=lambda b: False)
    assert out["up"] is False and out["started"] is False and "could not be started" in out["reason"]


def test_a_server_on_another_computer_is_never_launched_here():
    launched = []
    out = ss.start("http://192.168.1.5:11470", program="stremio-service.exe", launch=lambda: launched.append(1), probe=lambda b: False)
    assert out["up"] is False and not launched and "another computer" in out["reason"]
