"""The snapshot-readiness checks read a video's frame folder once instead of calling the file system dozens of times. They must give exactly
the answers the old per-file checks gave, in every folder state."""
import json
import os
from pathlib import Path

import pytest

import metadata_scan_harness as h
from telegram_archive import config
from telegram_archive.engines import preview_prefetch as pp


@pytest.fixture(autouse=True)
def _folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "PLAYER_CACHE_DIR", tmp_path / "cache" / "player_cache")
    pp._SIBLINGS.clear()
    yield
    pp._SIBLINGS.clear()


# ---- the old implementations, kept here as the reference -----------------------------------------------------------------------------
def old_has_all(frames_dir):
    try:
        return all((Path(frames_dir) / f"frame_{i:02d}.jpg").is_file() and (Path(frames_dir) / f"frame_{i:02d}.jpg").stat().st_size >= pp.MIN_FRAME_BYTES
                   for i in range(1, pp.FRAME_COUNT + 1))
    except OSError:
        return False


def old_receipt(frames_dir):
    try:
        root = Path(frames_dir)
        payload = json.loads((root / "validation.json").read_text(encoding="utf-8"))
        files = payload.get("files") or []
        if (int(payload.get("version") or 0) != pp.PREVIEW_VERSION or payload.get("ready") is not True
                or int(payload.get("frame_count") or 0) < pp.FRAME_COUNT or len(files) != pp.FRAME_COUNT):
            return None
        by_name = {str(item.get("name") or ""): item for item in files if isinstance(item, dict)}
        if len(by_name) != pp.FRAME_COUNT:
            return None
        for idx in range(1, pp.FRAME_COUNT + 1):
            name = f"frame_{idx:02d}.jpg"
            item = by_name.get(name)
            if item is None:
                return None
            path = root / name
            stat = path.stat()
            if (not path.is_file() or stat.st_size < pp.MIN_FRAME_BYTES or stat.st_size != int(item.get("size") or -1)
                    or stat.st_mtime_ns != int(item.get("mtime_ns") or -1)):
                return None
        return payload
    except (OSError, ValueError, TypeError):
        return None


def make(cid, mid, *, frames=36, size=3000, receipt=True, sprite=True, version=None):
    d = pp.preview_frames_path(cid, mid) if version is None else pp.preview_frames_path(cid, mid).parent / f"{mid}_v{version}.frames"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, frames + 1):
        (d / f"frame_{i:02d}.jpg").write_bytes(b"x" * size)
    if receipt:
        files = [{"name": f"frame_{i:02d}.jpg", "size": (d / f"frame_{i:02d}.jpg").stat().st_size, "mtime_ns": (d / f"frame_{i:02d}.jpg").stat().st_mtime_ns}
                 for i in range(1, min(frames, 36) + 1)]
        (d / "validation.json").write_text(json.dumps({"version": pp.PREVIEW_VERSION if version is None else version, "ready": True, "frame_count": frames, "files": files}))
    if sprite:
        (d / "grid_sprite.jpg").write_bytes(b"s" * 2000)
    return d


CASES = {
    "complete": lambda: make(-1001, 1),
    "complete without receipt": lambda: make(-1001, 2, receipt=False),
    "one frame missing": lambda: (lambda d: ((d / "frame_17.jpg").unlink(), d)[1])(make(-1001, 3)),
    "one frame too small": lambda: (lambda d: ((d / "frame_05.jpg").write_bytes(b"x"), d)[1])(make(-1001, 4)),
    "a frame replaced after the receipt": lambda: (lambda d: ((d / "frame_09.jpg").write_bytes(b"y" * 3500), d)[1])(make(-1001, 5)),
    "only 20 frames": lambda: make(-1001, 6, frames=20),
    "receipt wrong version": lambda: (lambda d: ((d / "validation.json").write_text(json.dumps({"version": 1, "ready": True, "frame_count": 36, "files": []})), d)[1])(make(-1001, 7)),
    "receipt not ready": lambda: (lambda d: ((d / "validation.json").write_text(json.dumps({"version": pp.PREVIEW_VERSION, "ready": False, "frame_count": 36, "files": []})), d)[1])(make(-1001, 8)),
    "receipt is not json": lambda: (lambda d: ((d / "validation.json").write_text("{nope"), d)[1])(make(-1001, 9)),
    "a frame is a folder": lambda: (lambda d: ((d / "frame_03.jpg").unlink(), (d / "frame_03.jpg").mkdir(), d)[2])(make(-1001, 10)),
    "extra files": lambda: (lambda d: ((d / "notes.txt").write_text("x"), (d / "frame_99.jpg").write_bytes(b"z" * 5000), d)[2])(make(-1001, 11)),
    "no folder": lambda: pp.preview_frames_path(-1001, 12),
    "empty folder": lambda: (lambda d: (d.mkdir(parents=True, exist_ok=True), d)[1])(pp.preview_frames_path(-1001, 13)),
}


@pytest.mark.parametrize("name", list(CASES))
def test_the_new_checks_answer_exactly_as_the_old_ones_did(name):
    d = CASES[name]()
    assert pp._has_all_local_frame_files(d) == old_has_all(d)
    new, old = pp._validation_receipt_ready(d), old_receipt(d)
    assert (new is None) == (old is None) and new == old


def test_a_listing_can_be_shared_between_the_two_checks():
    d = make(-1001, 20)
    listing = pp._frame_listing(d)
    assert pp._has_all_local_frame_files(d, listing) is True and pp._validation_receipt_ready(d, listing) is not None
    assert pp._frame_listing(pp.preview_frames_path(-1001, 999)) is None


def test_a_valid_videos_check_makes_a_handful_of_file_calls_not_a_hundred(monkeypatch):
    d = make(-1001, 30)
    real_stat, calls = os.stat, {"n": 0}
    monkeypatch.setattr(os, "stat", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real_stat(*a, **k))[1])
    pp._has_all_local_frame_files(d)
    pp._validation_receipt_ready(d)
    # one directory read each; per-file sizes and times come from it (on Windows with no extra call)
    assert calls["n"] <= 2 * (pp.FRAME_COUNT + 6)


def row_for(cid, mid):
    return {"source_channel": str(cid), "source_message_id": mid, "filename": f"v{mid}.mp4", "title": f"v{mid}", "cache_status": None, "path": None,
            "version": None, "frames_path": None, "frame_count": None, "thumbnail_source": None}


def test_the_reason_a_video_is_listed_still_names_the_first_missing_thing():
    assert pp._scan_work_reason(row_for(-1001, 50)) == "no snapshot folder on disk"
    make(-1001, 51, frames=20)
    assert pp._scan_work_reason(row_for(-1001, 51)) == "only 20 of 36 snapshot frames on disk"
    make(-1001, 52, receipt=False)
    assert pp._scan_work_reason(row_for(-1001, 52)) in ("snapshot validation receipt missing or out of date", "representative frame missing")
    make(-1001, 53, sprite=False)
    assert pp._scan_work_reason(row_for(-1001, 53)) in ("grid picture missing", "representative frame missing", "snapshot validation receipt missing or out of date")


def test_older_version_folders_are_found_without_searching_the_channel_folder_each_time():
    make(-1001, 60, version=5)
    make(-1001, 61, version=6)
    make(-1001, 62, version=7)
    parent = pp.preview_frames_path(-1001, 60).parent
    assert pp._sibling_versions(parent, 60) == ["60_v5.frames"]
    assert sorted(pp._sibling_versions(parent, 61)) == ["61_v6.frames"]
    assert pp._sibling_versions(parent, 9999) == []
    assert pp._sibling_versions(parent / "missing", 1) == []
    # a frame set saved by an older version is reused without contacting Telegram, as before
    assert pp.promote_existing_local_frames_sync(-1001, 63) is False


def test_the_directory_is_read_once_for_many_videos(monkeypatch):
    for mid in range(100, 140):
        make(-1001, mid)
    pp._SIBLINGS.clear()
    real, reads = os.scandir, {"parent": 0}
    parent = str(pp.preview_frames_path(-1001, 100).parent)

    def counting(path=".", *a, **k):
        if str(path) == parent:
            reads["parent"] += 1
        return real(path, *a, **k)

    monkeypatch.setattr(os, "scandir", counting)
    for mid in range(140, 200):                                  # sixty videos with no frames at all
        pp.promote_existing_local_frames_sync(-1001, mid)
    assert reads["parent"] == 1                                  # one read of the channel folder, not sixty searches
