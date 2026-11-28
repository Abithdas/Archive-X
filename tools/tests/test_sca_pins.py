"""The workflows' third-party actions stay pinned to commits (tools/sca/check_action_pins.py), and so does the LAV download."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "sca"))
import check_action_pins as pins  # noqa: E402


def test_the_real_workflows_are_pinned():
    assert pins.unpinned(ROOT / ".github" / "workflows") == []


def test_a_tag_or_branch_is_caught(tmp_path):
    (tmp_path / "a.yml").write_text("jobs:\n  j:\n    steps:\n      - uses: actions/checkout@v4\n      - uses: x/y@" + "a" * 40 + " # v1\n      - uses: dtolnay/rust-toolchain@stable\n      - uses: ./local\n")
    bad = pins.unpinned(tmp_path)
    assert [b.split(": ")[1] for b in bad] == ["actions/checkout@v4", "dtolnay/rust-toolchain@stable"]


def test_a_short_sha_is_not_enough(tmp_path):
    (tmp_path / "a.yml").write_text("steps:\n  - uses: x/y@abc1234\n")
    assert len(pins.unpinned(tmp_path)) == 1
