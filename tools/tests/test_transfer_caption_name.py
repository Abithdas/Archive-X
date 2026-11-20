"""A converted transfer's caption names the new .mp4, not the source's old extension."""
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axcap_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def _f():
    from telegram_archive.media_manager import runner  # noqa: F401
    from telegram_archive.media_manager import telegram_media_manager as tmm
    return tmm.caption_with_converted_name


def test_extension_swapped():
    assert _f()("File: a b .mkv · 720p", "a b .mkv", "a b .mp4") == "File: a b .mp4 · 720p"


def test_noop_cases():
    f = _f()
    assert f("x", "a.mp4", "a.mp4") == "x"
    assert f("", "a.mkv", "a.mp4") == ""
