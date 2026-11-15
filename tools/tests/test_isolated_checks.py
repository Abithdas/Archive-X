"""Run the tests that need a data folder of their own in a process of their own (see conftest.py)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FILES = ["test_inventory_off_loop.py", "test_performer_identity_index.py"]


@pytest.mark.parametrize("name", FILES)
def test_isolated(name):
    env = {**os.environ, "AX_ISOLATED": "1", "ARCHIVE_X_DATA_DIR": tempfile.mkdtemp(prefix="axiso_")}
    done = subprocess.run([sys.executable, "-m", "pytest", str(HERE / name), "-q", "-p", "no:cacheprovider"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=420)
    assert done.returncode == 0, done.stdout[-2500:] + done.stderr[-1000:]
