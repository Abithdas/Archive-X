"""The Rust front door runs the same visibility views as Python: crates/ax-db/src/visibility.sql must be what content_mode.py generates."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]


def test_rust_visibility_sql_is_up_to_date():
    r = subprocess.run([sys.executable, str(root / "tools" / "gen_visibility_sql.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, "run: python tools/gen_visibility_sql.py"
