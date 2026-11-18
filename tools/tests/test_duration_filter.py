"""Only videos shorter than N minutes: the rule, the Telegram duration reader and the local upload filter."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axdur_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)

from telegram_archive.core import duration_filter as d  # noqa: E402


def test_limit_parsing():
    assert d.normalize_max_minutes(None) is None and d.normalize_max_minutes("") is None and d.normalize_max_minutes("  ") is None
    assert d.normalize_max_minutes(0) is None and d.normalize_max_minutes("0") is None          # 0 = no limit
    assert d.normalize_max_minutes(10) == 10 and d.normalize_max_minutes("10") == 10
    assert d.normalize_max_minutes("7,5") == 7.5 and d.normalize_max_minutes(" 2.5 ") == 2.5
    for bad in ("abc", "-3", "nan", "inf", "99999"):
        with pytest.raises(ValueError):
            d.normalize_max_minutes(bad)


def test_under_means_strictly_under():
    assert d.within_limit(599, 10) and d.within_limit(0.5, 10)
    assert not d.within_limit(600, 10) and not d.within_limit(601, 10)      # exactly 10:00 is not under 10 minutes
    assert d.within_limit(99999, None)                                       # no limit keeps everything, even unknown lengths
    assert not d.within_limit(0, 10) and not d.within_limit(None, 10) and not d.within_limit("x", 10)   # unknown is left out


def test_describe():
    assert d.describe(None) == "any length" and d.describe(10) == "under 10 min" and d.describe(7.5) == "under 7.5 min"


def test_reads_a_telegram_duration_from_wherever_it_is():
    assert d.message_duration_seconds(NS(file=NS(duration=95.0))) == 95.0
    assert d.message_duration_seconds(NS(file=NS(duration=None), video=NS(duration=300))) == 300.0
    doc = NS(attributes=[NS(w=1), NS(duration=42)])
    assert d.message_duration_seconds(NS(file=None, media=NS(document=doc))) == 42.0
    assert d.message_duration_seconds(NS()) == 0.0


def test_the_start_route_validates_and_stores_the_limit():
    import inspect
    from telegram_archive.web import routes_jobs
    src = inspect.getsource(routes_jobs)
    assert "normalize_max_minutes(data.get(\"max_duration_minutes\"))" in src and 'detail["max_duration_minutes"]' in src
