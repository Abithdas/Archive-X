"""A Telegram transfer's progress must follow the files moved, not the metadata pass that runs first (the log showed 116/116 = 100% from the start)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axmm_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from telegram_archive.media_manager.runner import MediaManagerRunner  # noqa: E402


def feed(r, *lines):
    for line in lines:
        r._commit(line)


def test_the_metadata_pass_is_not_the_transfers_progress_and_files_count_when_they_finish():
    r = MediaManagerRunner()
    feed(r, "[DATABASE CHECK] 116/116 source items already recorded; 0 new/unrecorded", "[PREFETCH] 116/116 | metadata ready: 116")
    feed(r, "[PREFETCH COMPLETE] 116 item(s) | metadata ready: 116 | elapsed: 0.6s")
    assert (r.completed, r.total) == (0, 116)                                   # nothing has been moved yet
    feed(r, "CHANNEL 1/1 | ITEM 1/116 | Non stream able file | msg=35 | a.wmv", "  Step 1/3: downloading source exactly once...")
    assert (r.completed, r.total) == (0, 116)                                   # the first file is in progress, none done
    feed(r, "  MOVE COMPLETE: a.wmv")
    assert (r.completed, r.total) == (1, 116)
    feed(r, "CHANNEL 1/1 | ITEM 2/116 | Non stream able file | msg=36 | b.wmv")
    assert (r.completed, r.total) == (1, 116)
    feed(r, "  MOVE COMPLETE: b.wmv")
    assert r.completed == 2


def test_a_skipped_file_still_moves_on_to_the_next_item_but_is_not_counted_as_done_twice():
    r = MediaManagerRunner()
    feed(r, "CHANNEL 1/1 | ITEM 3/4 | c | msg=1 | c.mkv", "  SKIPPED — NEEDS SNAPSHOTS: x")
    assert (r.completed, r.total) == (2, 4)
    feed(r, "CHANNEL 1/1 | ITEM 4/4 | c | msg=2 | d.mkv")
    assert (r.completed, r.total) == (3, 4)


def test_older_progress_lines_still_work():
    r = MediaManagerRunner()
    feed(r, "PROCESS 5/20 something")
    assert (r.completed, r.total) == (4, 20)
