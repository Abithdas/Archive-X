import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.safe_paths import inside  # noqa: E402


def test_stays_inside_and_refuses_escapes():
    base = tempfile.mkdtemp()
    assert inside(base, "12_34", "audio_1.m4a") == Path(os.path.realpath(base)) / "12_34" / "audio_1.m4a"
    for bad in ("../x", "a/../../x", "/etc/passwd"):
        with pytest.raises(ValueError):
            inside(base, bad)
