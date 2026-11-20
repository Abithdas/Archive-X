"""The files the Rust code relies on must match what the Python backend does today."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(script):
    return subprocess.run([sys.executable, str(ROOT / "tools/parity" / script), "--check"], capture_output=True, text=True, cwd=ROOT)


def test_the_committed_schema_snapshot_matches_the_database_the_backend_creates():
    r = run("dump_schema.py")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_committed_origin_vectors_match_the_python_guard():
    r = run("gen_origin_vectors.py")
    assert r.returncode == 0, r.stdout + r.stderr
