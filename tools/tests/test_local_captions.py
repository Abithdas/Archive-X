"""Audio tracks, subtitles and live captions of a video from a folder on this PC: ffmpeg reads the file itself (the loopback feed only knows Telegram)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axlcap_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0", ARCHIVE_X_LOCAL_RESCAN="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(__file__))

from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.engines import local_scanner as ls  # noqa: E402
from telegram_archive.playback import captions  # noqa: E402

from test_local_folders import _client  # noqa: E402


def _video_with_sound_and_subtitles(path: Path) -> bool:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    srt = path.with_suffix(".srt")
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nHello from the file\n\n", encoding="utf-8")
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                           "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", str(path)], capture_output=True)
    srt.unlink()
    return done.returncode == 0 and path.is_file()


class FakeEngine:
    """Stands in for the speech model: it hears whatever ffmpeg cut from the file, and says how much it heard."""
    name = "fake"

    def __init__(self, *a, **k):
        self.heard = []

    def prepare(self):
        pass

    def transcribe_wav(self, wav, task="translate", cancelled=None):
        self.heard.append(len(wav))
        return {"language": "en", "cues": [{"start": 0.0, "end": 1.0, "text": f"heard {len(wav)} bytes"}]}


def _folder_video(tmp_path, monkeypatch):
    root = tmp_path / "Movies"
    root.mkdir()
    if not _video_with_sound_and_subtitles(root / "Some Film.mp4"):
        pytest.skip("ffmpeg and ffprobe are not available")
    monkeypatch.setattr(captions, "available", lambda: True)
    monkeypatch.setattr(captions, "FasterWhisperEngine", FakeEngine)
    db = Database(tmp_path / "c.db")
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    sid = c.post("/api/local-folders", json={"path": str(root), "content_rating": "nsfw", "holds": "videos"}, headers=H).json()["id"]
    ch = ls.channel_id(sid)
    for _ in range(300):
        with db.reading() as conn:
            row = conn.execute("SELECT message_id FROM local_video_files WHERE channel_id=?", (ch,)).fetchone()
        if row:
            return c, db, ch, int(row["message_id"]), root
        time.sleep(0.1)
    pytest.fail("the folder was not scanned")


def test_tracks_and_subtitles_of_a_folder_video_are_read_from_the_file(tmp_path, monkeypatch):
    c, _db, ch, mid, _root = _folder_video(tmp_path, monkeypatch)
    t = c.get(f"/api/play/tracks/{ch}/{mid}")
    assert t.status_code == 200, t.text
    body = t.json()
    assert len(body["audio"]) == 1 and [s["text"] for s in body["subtitles"]] == [True] and 3 < body["duration"] < 5
    vtt = c.get(f"/api/play/subtitle/{ch}/{mid}/{body['subtitles'][0]['index']}.vtt")
    assert vtt.status_code == 200 and "Hello from the file" in vtt.text


def test_live_captions_of_a_folder_video_cut_the_audio_from_the_file(tmp_path, monkeypatch):
    c, _db, ch, mid, _root = _folder_video(tmp_path, monkeypatch)
    assert c.get("/api/captions/status?prepare=1").json()["available"] is True
    r = None
    for _ in range(50):                                             # the (stand-in) model is prepared in the background first
        r = c.get(f"/api/play/captions/{ch}/{mid}?start=0&at=0&task=transcribe")
        if r.status_code == 200 and not r.json().get("preparing"):      # a 200 with "preparing" still has no cues: the answer is not in yet
            break
        time.sleep(0.1)
    assert r is not None and r.status_code == 200, r.text
    cues = r.json()["cues"]
    assert cues and cues[0]["text"].startswith("heard ") and int(cues[0]["text"].split()[1]) > 10_000       # real audio reached the engine


def test_a_file_that_left_its_folder_is_reported_not_crashed(tmp_path, monkeypatch):
    c, _db, ch, mid, root = _folder_video(tmp_path, monkeypatch)
    (root / "Some Film.mp4").unlink()
    r = c.get(f"/api/play/tracks/{ch}/{mid}")
    assert r.status_code == 404 and "not in its folder" in r.json()["detail"]


# --- the engine itself: a graphics card whose libraries are missing must not mean "no captions" ------------------------------------------------

def test_the_engine_falls_back_to_the_cpu_when_the_graphics_card_path_is_broken(monkeypatch):
    import types
    import numpy as np
    made = []

    class Model:
        def __init__(self, name, device="auto", compute_type="int8", **kw):
            self.device = device
            made.append(device)

        def transcribe(self, audio, **kw):
            if self.device != "cpu":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return iter([types.SimpleNamespace(start=0.0, end=1.0, text=" hello ")]), types.SimpleNamespace(language="en")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.delenv("ARCHIVE_X_WHISPER_DEVICE", raising=False)
    eng = captions.FasterWhisperEngine(model="tiny")
    eng.prepare()                                            # the warm-up meets the broken card, switches to the CPU and succeeds
    assert made == ["auto", "cpu"] and eng._device == "cpu"
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(16000, dtype=np.int16).tobytes())
    out = eng.transcribe_wav(buf.getvalue(), "transcribe")
    assert [c["text"] for c in out["cues"]] == ["hello"] and made == ["auto", "cpu"]      # no second reload


def test_an_error_that_is_not_about_the_card_is_not_hidden(monkeypatch):
    import types

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, *a, **k):
            raise RuntimeError("the model file is damaged")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    with pytest.raises(RuntimeError, match="damaged"):
        captions.FasterWhisperEngine(model="tiny").prepare()


def test_play_info_of_a_folder_video_carries_the_browser_verdict(tmp_path, monkeypatch):
    """The page uses it to start the native player at once for a file the browser cannot play."""
    c, _db, ch, mid, root = _folder_video(tmp_path, monkeypatch)
    info = c.get(f"/api/play/info/{ch}/{mid}").json()
    assert (info["vcodec"], info["acodec"]) == ("h264", "aac") and info["pix_fmt"] == "yuv420p" and "mp4" in info["format"]
    assert info["container_ok"] and info["video_ok"] and info["audio_ok"] and info["direct_likely"] is True
    # a file whose codecs no browser plays gets the opposite verdict
    odd = root / "Old Film.avi"
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=2", "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "mpeg4", "-c:a", "ac3", str(odd)], capture_output=True)
    if done.returncode != 0:
        pytest.skip("ffmpeg cannot make an avi with ac3 here")
    sid = ls.source_id_of(ch)
    from conftest import wait_for_scans
    wait_for_scans()                                                  # the first scan is still preparing its video: a scan asked for now would be ignored
    c.post(f"/api/local-folders/{sid}/scan", headers={"Origin": "https://testserver"})
    for _ in range(100):
        with _db.reading() as conn:
            row = conn.execute("SELECT message_id FROM local_video_files WHERE path LIKE '%Old Film.avi'").fetchone()
        if row:
            break
        time.sleep(0.1)
    assert row
    bad = c.get(f"/api/play/info/{ch}/{int(row['message_id'])}").json()
    assert bad["direct_likely"] is False and bad["container_ok"] is False and bad["video_ok"] is False and bad["audio_ok"] is False
