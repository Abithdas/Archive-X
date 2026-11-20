import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core import swallowed


def test_counts_each_cause_and_logs_the_first_only(caplog):
    swallowed.reset()
    with caplog.at_level("WARNING", logger="archive_x.swallowed"):
        for _ in range(3):
            try:
                raise TimeoutError("slow")
            except Exception:
                swallowed.note("sender.cleanup")
        try:
            raise ValueError("odd")
        except Exception:
            swallowed.note("sender.cleanup")
    rows = swallowed.snapshot()
    assert [(r["where"], r["error"], r["count"]) for r in rows] == [("sender.cleanup", "TimeoutError", 3), ("sender.cleanup", "ValueError", 1)]
    assert len(caplog.records) == 2          # once per new cause, not once per occurrence


def test_outside_an_except_block_it_does_nothing():
    swallowed.reset()
    swallowed.note("nowhere")
    assert swallowed.snapshot() == []
