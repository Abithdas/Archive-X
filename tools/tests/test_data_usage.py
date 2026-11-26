"""The size of the data folder shown in Settings: the same totals as before, found faster, and kept between visits."""
from __future__ import annotations

import os
import threading

import pytest

from telegram_archive.core import data_usage


def _walk_the_old_way(root):
    count = total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                count += 1
                total += p.stat().st_size
        except OSError:
            pass
    return count, total


def _tree(root):
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "empty").mkdir()
    for i, where in enumerate(("", "a", "a/b", "a/b/c", "a/b/c")):
        (root / where / f"f{i}.bin").write_bytes(b"x" * (10 + i * 7))
    (root / "a" / "zero.bin").write_bytes(b"")


@pytest.fixture(autouse=True)
def _fresh():
    data_usage.clear()


def test_the_totals_are_those_of_the_old_walk(tmp_path):
    _tree(tmp_path)
    assert data_usage.measure(tmp_path) == _walk_the_old_way(tmp_path) == (6, 10 + 17 + 24 + 31 + 38)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symbolic links")
def test_a_link_to_a_file_counts_as_before_and_a_link_to_a_folder_is_not_followed(tmp_path):
    _tree(tmp_path)
    try:
        os.symlink(tmp_path / "f0.bin", tmp_path / "link-to-file")
        os.symlink(tmp_path / "a", tmp_path / "link-to-folder", target_is_directory=True)
    except OSError:
        pytest.skip("links need a privilege here")
    assert data_usage.measure(tmp_path) == _walk_the_old_way(tmp_path)


def test_a_missing_folder_is_zero_not_an_error(tmp_path):
    assert data_usage.measure(tmp_path / "nowhere") == (0, 0)


def test_the_second_ask_is_answered_from_memory_even_after_the_folder_changed(tmp_path):
    _tree(tmp_path)
    first = data_usage.usage(tmp_path)
    (tmp_path / "new.bin").write_bytes(b"y" * 1000)
    assert data_usage.usage(tmp_path) == first


def test_an_old_answer_is_returned_at_once_and_measured_again_in_the_background(tmp_path, monkeypatch):
    _tree(tmp_path)
    clock = {"t": 1000.0}
    monkeypatch.setattr(data_usage.time, "monotonic", lambda: clock["t"])
    first = data_usage.usage(tmp_path)
    (tmp_path / "new.bin").write_bytes(b"y" * 1000)
    clock["t"] += data_usage.REFRESH_AFTER + 1
    gate = threading.Event()
    real = data_usage.measure
    monkeypatch.setattr(data_usage, "measure", lambda root: (gate.wait(5), real(root))[1])
    assert data_usage.usage(tmp_path) == first               # the old answer, without waiting for the new measurement
    gate.set()
    for thread in [t for t in threading.enumerate() if t.name == "data-usage-refresh"]:
        thread.join(5)
    assert data_usage.usage(tmp_path) == (first[0] + 1, first[1] + 1000)


def test_only_one_background_measurement_runs_at_a_time(tmp_path, monkeypatch):
    _tree(tmp_path)
    clock = {"t": 1000.0}
    monkeypatch.setattr(data_usage.time, "monotonic", lambda: clock["t"])
    data_usage.usage(tmp_path)
    clock["t"] += data_usage.REFRESH_AFTER + 1
    started = []
    release = threading.Event()
    real = data_usage.measure
    monkeypatch.setattr(data_usage, "measure", lambda root: (started.append(1), release.wait(5), real(root))[2])
    for _ in range(5):
        data_usage.usage(tmp_path)
    release.set()
    for thread in [t for t in threading.enumerate() if t.name == "data-usage-refresh"]:
        thread.join(5)
    assert len(started) == 1
