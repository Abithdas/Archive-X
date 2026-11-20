"""Run the script-style checks as programs and require each to succeed (see conftest.py)."""
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = [
    ("test_image_derivatives.py", []),
    ("test_photo_derivatives.py", []),
    ("test_desktop_mode.py", []),
    ("test_duplicate_delete_thumbnail.py", [str(ROOT)]),
]


@pytest.mark.parametrize("name,args", SCRIPTS, ids=[s[0] for s in SCRIPTS])
def test_script_check(name, args):
    done = subprocess.run([sys.executable, str(HERE / name), *args], cwd=ROOT, capture_output=True, text=True, timeout=420)
    assert done.returncode == 0, (done.stdout[-1500:] + done.stderr[-1500:])
