"""The live connection sends the status only when something other than a heartbeat age changed."""
from telegram_archive.web.routes_settings import steady_status_json


def _status(tick_age=0.12, job_state="RUNNING", jobs=3):
    return {"jobs": jobs, "watchdogs": {"event_loop": {"last_tick_age": tick_age, "ok": True},
                                        "monitor": {"loop_heartbeat_age": tick_age, "watchdog_success_age": tick_age},
                                        "jobs": [{"id": "j1", "state": job_state, "heartbeat_age": tick_age}]}}


def test_two_snapshots_that_differ_only_in_heartbeat_ages_are_the_same():
    assert steady_status_json(_status(0.12)) == steady_status_json(_status(37.5))


def test_a_changed_job_state_or_count_is_still_a_change():
    base = steady_status_json(_status())
    assert steady_status_json(_status(job_state="STALLED")) != base
    assert steady_status_json(_status(jobs=4)) != base


def test_a_status_without_watchdogs_is_left_alone():
    assert steady_status_json({"a": 1}) == '{"a":1}'
    assert steady_status_json({"a": 1, "watchdogs": "off"}) == '{"a":1,"watchdogs":"off"}'
