"""StashDB and ThePornDB are never asked about content marked SFW: the scans that ask them leave SFW channels and folders out."""
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axnoadult_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)

import pytest  # noqa: E402

from telegram_archive.core import content_mode as cm  # noqa: E402
from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.engines.metadata_channel_pipeline import ADULT_PHASES, PHASES  # noqa: E402
from telegram_archive.engines.page_enricher import PageEnricher  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "a.db")
    now = "2026-01-01T00:00:00"
    with d.lock:
        d.conn.executemany("INSERT INTO channels(telegram_id,title,updated_at,in_library,content_rating) VALUES(?,?,?,?,?)",
                           [("1", "Adult", now, 1, "nsfw"), ("2", "Movies", now, 1, "sfw")])
        d.conn.executemany("INSERT INTO media(source_channel,source_message_id,filename) VALUES(?,?,?)", [("1", 1, "a.mp4"), ("2", 1, "b.mkv"), ("9", 1, "c.mp4")])
        d.conn.commit()
    return d


def test_the_sfw_marks_are_read_from_the_channels_not_from_the_mode(db):
    assert cm.sfw_channels(db) == {"2"}
    cm.set_mode(db, "nsfw")
    assert cm.sfw_channels(db) == {"2"}


def test_the_scan_filter_drops_sfw_sources_and_keeps_adult_and_unmarked_ones(db):
    with db.reading() as c:
        ids = [r[0] for r in c.execute(f"SELECT source_channel FROM media WHERE source_channel {cm.NOT_SFW_SOURCE} ORDER BY id")]
    assert ids == ["1", "9"]


def test_the_page_enricher_does_not_load_the_videos_of_an_sfw_channel(db):
    e = PageEnricher(db, telegram=None)
    assert set(e._rows("1", [1])) == {1}
    assert e._rows("2", [1]) == {}


def test_the_channel_pipeline_leaves_out_the_phases_that_ask_the_adult_providers():
    assert set(ADULT_PHASES) == {"metadata", "performers", "channel_data"}
    assert {"inventory", "snapshots"} <= set(PHASES) - set(ADULT_PHASES)
