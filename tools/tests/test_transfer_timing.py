"""The per-step timing of a transfer: what each video's steps add up to, what is left out while a video waits, and the job's totals."""
from telegram_archive.engines.transfer_timing import ItemClock, TransferTimer


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def test_each_mark_takes_the_time_since_the_one_before():
    c = Clock()
    clock = ItemClock(c)
    c.advance(0.4); clock.mark("refresh")
    c.advance(1.1); clock.mark("copy")
    c.advance(0.9); clock.mark("picture")
    assert clock.steps == {"refresh": 0.4, "copy": 1.1, "picture": 0.9} or {k: round(v, 2) for k, v in clock.steps.items()} == {"refresh": 0.4, "copy": 1.1, "picture": 0.9}
    assert round(clock.total, 2) == 2.4
    assert clock.text().startswith("2.4 s: refresh source 0.4 · copy video 1.1 · post picture 0.9")


def test_time_spent_waiting_for_others_is_not_counted_against_the_video():
    c = Clock()
    clock = ItemClock(c)
    c.advance(1.0); clock.mark("copy")
    clock.pause()
    c.advance(30.0)                                  # the other videos of the batch are being posted
    clock.resume()
    c.advance(0.5); clock.mark("verify")
    assert round(clock.total, 2) == 1.5


def test_a_batched_step_can_be_shared_out_and_steps_of_the_same_name_add_up():
    clock = ItemClock(Clock())
    clock.add("verify", 0.6 / 20)
    clock.add("verify", 0.2)
    assert round(clock.steps["verify"], 3) == 0.23


def test_the_job_totals_average_the_finished_videos_and_say_what_flood_waits_cost():
    timer = TransferTimer()
    for copy, picture in ((1.0, 0.5), (3.0, 1.5)):
        clock = ItemClock(Clock())
        clock.add("copy", copy)
        clock.add("picture", picture)
        timer.finish(clock)
    timer.finish(ItemClock(Clock()), ok=False)
    timer.flood(12)
    timer.flood(30)
    timer.batch(20)
    s = timer.summary()
    assert s["videos"] == 2 and s["failed"] == 1 and s["average_seconds"] == 3.0
    assert s["steps"]["copy"] == {"average_seconds": 2.0, "share_percent": 66.7}
    assert s["flood_waits"] == 2 and s["flood_wait_seconds"] == 42 and s["average_batch"] == 20
    assert "2 video(s), 3.0 s each" in timer.text() and "slow down 2 time(s), 42 s" in timer.text()


def test_nothing_to_say_before_a_video_has_finished():
    assert TransferTimer().text() == "no video finished yet"
