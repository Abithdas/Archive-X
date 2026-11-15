"""One-click assistant setup: pinned downloads that resume and are checked, safe unpacking, and the whole run with fake files and a fake assistant."""
import hashlib
import io
import sys
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core import local_ai_setup as las


class Reply(io.BytesIO):
    def __init__(self, data: bytes, status=200, total=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data) if total is None else total)}

    def __enter__(self): return self
    def __exit__(self, *a): return False


class Cut(Reply):
    """A download that dies part-way."""
    def __init__(self, data, after):
        super().__init__(data)
        self.after = after
        self.sent = 0

    def read(self, n=-1):
        if self.sent >= self.after:
            raise OSError("connection lost")
        chunk = super().read(min(n if n > 0 else 1 << 20, self.after - self.sent))
        self.sent += len(chunk)
        return chunk


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def noop(done, total): pass


def test_a_download_is_kept_only_when_it_matches_its_checksum(tmp_path):
    data = b"x" * 5000
    dest = tmp_path / "m.gguf"
    las.download("https://example.test/f", dest, sha256=sha(data), size_hint=5000, on_bytes=noop, cancel=threading.Event(), opener=lambda req, timeout: Reply(data))
    assert dest.read_bytes() == data and not (tmp_path / "m.gguf.part").exists()
    with pytest.raises(RuntimeError, match="checksum"):
        las.download("https://example.test/f", tmp_path / "bad.gguf", sha256=sha(b"other"), size_hint=5000, on_bytes=noop, cancel=threading.Event(), opener=lambda req, timeout: Reply(data))
    assert not (tmp_path / "bad.gguf").exists() and not (tmp_path / "bad.gguf.part").exists()   # nothing wrong is left behind to be used


def test_a_cut_off_download_carries_on_from_where_it_stopped(tmp_path):
    data = bytes(range(256)) * 40
    dest = tmp_path / "m.gguf"
    with pytest.raises(OSError):
        las.download("https://example.test/f", dest, sha256=sha(data), size_hint=len(data), on_bytes=noop, cancel=threading.Event(), opener=lambda req, timeout: Cut(data, 3000))
    assert (tmp_path / "m.gguf.part").stat().st_size == 3000
    asked = {}

    def second(req, timeout):
        asked["range"] = req.headers.get("Range")
        return Reply(data[3000:], status=206)
    las.download("https://example.test/f", dest, sha256=sha(data), size_hint=len(data), on_bytes=noop, cancel=threading.Event(), opener=second)
    assert asked["range"] == "bytes=3000-" and dest.read_bytes() == data


def test_a_server_that_ignores_the_range_starts_the_file_again(tmp_path):
    data = b"abcdefgh" * 500
    dest = tmp_path / "m.gguf"
    (tmp_path / "m.gguf.part").write_bytes(b"junk")
    las.download("https://example.test/f", dest, sha256=sha(data), size_hint=len(data), on_bytes=noop, cancel=threading.Event(), opener=lambda req, timeout: Reply(data, status=200))
    assert dest.read_bytes() == data


def test_stopping_keeps_the_partial_file_for_next_time(tmp_path):
    data = b"z" * 100000
    cancel = threading.Event()
    seen = []

    def on_bytes(done, total):
        seen.append(done)
        if done >= 50000:
            cancel.set()
    with pytest.raises(las.Cancelled):
        las.download("https://example.test/f", tmp_path / "m.gguf", sha256=sha(data), size_hint=len(data), on_bytes=on_bytes, cancel=cancel, opener=lambda req, timeout: Reply(data))
    assert (tmp_path / "m.gguf.part").exists() and not (tmp_path / "m.gguf").exists()


def test_the_engine_zip_is_unpacked_flat_and_a_path_escape_is_refused(tmp_path):
    z = tmp_path / "e.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("llama-server.exe", "exe")
        f.writestr("ggml.dll", "dll")
    las._unpack(z, tmp_path / "rt", "llama-server.exe")
    assert (tmp_path / "rt" / "llama-server.exe").read_text() == "exe" and (tmp_path / "rt" / "ggml.dll").exists()
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as f:
        f.writestr("llama-server.exe", "exe")
        f.writestr("../outside.txt", "x")
    with pytest.raises(RuntimeError, match="unsafe"):
        las._unpack(evil, tmp_path / "rt2", "llama-server.exe")
    assert not (tmp_path / "outside.txt").exists()
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as f:
        f.writestr("readme.txt", "x")
    with pytest.raises(RuntimeError, match="did not contain"):
        las._unpack(empty, tmp_path / "rt3", "llama-server.exe")


def test_the_linux_tarball_drops_its_top_folder_and_keeps_the_run_bit(tmp_path):
    t = tmp_path / "e.tar.gz"
    with tarfile.open(t, "w:gz") as tf:
        for name, mode in (("llama-b1/llama-server", 0o755), ("llama-b1/libllama.so.1", 0o644)):
            info = tarfile.TarInfo(name); info.size = 3; info.mode = mode
            tf.addfile(info, io.BytesIO(b"abc"))
        link = tarfile.TarInfo("llama-b1/libllama.so"); link.type = tarfile.SYMTYPE; link.linkname = "libllama.so.1"
        tf.addfile(link)
        bad = tarfile.TarInfo("llama-b1/bad.so"); bad.type = tarfile.SYMTYPE; bad.linkname = "../../etc/passwd"
        tf.addfile(bad)
    with pytest.raises(RuntimeError, match="unsafe link"):
        las._unpack(t, tmp_path / "rt", "llama-server")
    t2 = tmp_path / "e2.tar.gz"
    with tarfile.open(t2, "w:gz") as tf:
        info = tarfile.TarInfo("llama-b1/llama-server"); info.size = 3; info.mode = 0o755
        tf.addfile(info, io.BytesIO(b"abc"))
    las._unpack(t2, tmp_path / "rt2", "llama-server")
    assert (tmp_path / "rt2" / "llama-server").stat().st_mode & 0o111


def test_the_pinned_downloads_are_complete():
    for e in las.ENGINES.values():
        assert len(e.sha256) == 64 and e.url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/")
    for m in las.MODELS.values():
        assert len(m.sha256) == 64 and m.url.startswith("https://huggingface.co/") and m.file.endswith(".gguf")
    assert las.RECOMMENDED in las.MODELS


def test_recommendation_follows_memory_and_options_say_what_fits():
    assert las.recommend(4) == "small" and las.recommend(6.9) == "balanced" and las.recommend(8) == "balanced" and las.recommend(32) == "balanced" and las.recommend(0) == "balanced"
    fits = {o["id"]: o["fits"] for o in las.options(8)}
    assert fits == {"small": True, "balanced": True, "strong": False}
    assert {o["id"]: o["fits"] for o in las.options(15.7)}["strong"] is True        # a 16 GB PC reports 15.7
    assert [o["id"] for o in las.options(8) if o["recommended"]] == ["balanced"]
    assert las.memory_gb() >= 0


class FakeAI:
    def __init__(self): self.calls = []
    def configure(self, **kw): self.calls.append(("configure", kw))
    def start(self): self.calls.append(("start", {}))
    def test(self): self.calls.append(("test", {})); return {"latency_ms": 12.5}


class FakeDB:
    def __init__(self): self.lines = []
    def log(self, msg, level="INFO", **kw): self.lines.append(msg)


def tiny_world(monkeypatch, tmp_path):
    exe_zip = io.BytesIO()
    with zipfile.ZipFile(exe_zip, "w") as f:
        f.writestr("llama-server.exe", "exe")
    exe_bytes, model_bytes = exe_zip.getvalue(), b"M" * 3000
    engine = las.Engine("win-cpu", "e.zip", sha(exe_bytes), 1, "Processor", "llama-server.exe", 0)
    model = las.Model("balanced", "Balanced", "Tiny-Q4", "tiny.gguf", "https://example/tiny.gguf", sha(model_bytes), 1, 1, "x")
    monkeypatch.setattr(las, "config", type("C", (), {"AI_DIR": tmp_path / "ai", "AI_MODELS_DIR": tmp_path / "ai" / "models"}))
    blobs = {engine.url: exe_bytes, model.url: model_bytes}
    fetched = []

    def opener(req, timeout):
        fetched.append(req.full_url)
        return Reply(blobs[req.full_url])
    return engine, model, opener, fetched


def test_a_whole_setup_runs_every_step_in_order_and_points_the_assistant_at_the_files(monkeypatch, tmp_path):
    engine, model, opener, fetched = tiny_world(monkeypatch, tmp_path)
    ai, db, setup = FakeAI(), FakeDB(), las.Setup()
    setup.run(db, ai, model, engine, opener)
    s = setup.snapshot()
    assert s["state"] == "done" and [x["state"] for x in s["steps"]] == ["done"] * 5
    assert [c[0] for c in ai.calls] == ["configure", "start", "test"]
    kw = ai.calls[0][1]
    assert kw["runtime_path"].endswith("llama-server.exe") and kw["model_path"].endswith("tiny.gguf") and kw["model_id"] == "Tiny-Q4" and kw["context"] == las.CONTEXT_TOKENS
    assert (tmp_path / "ai" / "runtime" / "llama-server.exe").is_file() and (tmp_path / "ai" / "models" / "tiny.gguf").is_file()
    assert not (tmp_path / "ai" / "runtime" / "e.zip").exists()          # the archive is not kept
    assert any("12.5" in line for line in s["log"]) and any("set up" in m for m in db.lines)
    again = las.Setup()
    fetched.clear()
    again.run(FakeDB(), FakeAI(), model, engine, opener)
    assert fetched == [] and again.snapshot()["state"] == "done"         # a second run downloads nothing


def test_a_failed_step_is_marked_and_the_reason_is_kept(monkeypatch, tmp_path):
    engine, model, opener, _ = tiny_world(monkeypatch, tmp_path)

    class Broken(FakeAI):
        def start(self): raise las.RuntimeError if False else RuntimeError("The assistant engine stopped while starting (exit code 3).")
    db, setup = FakeDB(), las.Setup()
    setup.run(db, Broken(), model, engine, opener)
    s = setup.snapshot()
    assert s["state"] == "error" and "exit code 3" in s["error"]
    states = {x["id"]: x["state"] for x in s["steps"]}
    assert states["engine"] == states["model"] == states["check"] == "done" and states["start"] == "error" and states["test"] == "pending"
    assert any("failed" in m for m in db.lines)


def test_only_one_setup_runs_at_a_time_and_unknown_choices_are_refused(monkeypatch, tmp_path):
    setup = las.Setup()
    with pytest.raises(ValueError):
        setup.start(FakeDB(), FakeAI(), "huge", "win-cpu")
    with pytest.raises(ValueError):
        setup.start(FakeDB(), FakeAI(), "balanced", "nope")
