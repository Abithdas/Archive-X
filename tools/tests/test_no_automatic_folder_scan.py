"""Opening the app must not scan the local folders by itself: that kept a big library busy and the app slow to open. A scan starts when asked."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = (
    "import os, tempfile, threading;"
    "os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix='axnoscan_'), ARCHIVE_X_AUTO_OPEN_BROWSER='0');"
    "os.environ.pop('ARCHIVE_X_LOCAL_RESCAN', None) if %s else os.environ.update(ARCHIVE_X_LOCAL_RESCAN='1');"
    "from telegram_archive.app import create_app;"
    "from telegram_archive.core.database import Database;"
    "import pathlib;"
    "app = create_app(db=Database(pathlib.Path(os.environ['ARCHIVE_X_DATA_DIR']) / 't.db'));"
    "print(sorted(t.name for t in threading.enumerate() if t.name == 'local-folders-rescan'))"
)


def watcher(default: bool) -> str:
    out = subprocess.run([sys.executable, "-c", CODE % ("True" if default else "False")], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    return out.stdout.strip().splitlines()[-1]


def test_the_folder_watcher_is_off_unless_asked_for():
    assert watcher(True) == "[]"
    assert watcher(False) == "['local-folders-rescan']"      # still available for whoever sets ARCHIVE_X_LOCAL_RESCAN=1
