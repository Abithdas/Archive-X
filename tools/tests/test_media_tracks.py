import asyncio, shutil, subprocess, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.playback import tracks

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("m")
    (d / "a.srt").write_text("1\n00:00:00,500 --> 00:00:01,500\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\nWorld\n")
    (d / "b.srt").write_text("1\n00:00:00,500 --> 00:00:01,500\nHola\n")
    out = d / "t.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3", "-f", "lavfi", "-i", "sine=f=880:d=3",
                    "-i", str(d / "a.srt"), "-i", str(d / "b.srt"), "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3", "-map", "4",
                    "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=spa", "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=spa",
                    "-c:v", "mpeg4", "-c:a", "aac", "-c:s", "srt", str(out)], check=True)
    return str(out)


def test_probe_lists_tracks(media):
    t = asyncio.run(tracks.probe_tracks(media))
    assert [a["language"] for a in t["audio"]] == ["eng", "spa"]
    assert [s["label"] for s in t["subtitles"]] == ["English", "Spanish"]
    assert all(s["text"] for s in t["subtitles"])


def test_subtitle_vtt(media):
    t = asyncio.run(tracks.probe_tracks(media))
    vtt = asyncio.run(tracks.subtitle_vtt(media, t["subtitles"][0]["index"]))
    assert vtt.startswith(b"WEBVTT") and b"Hello" in vtt


def test_audio_window_and_extract(media, tmp_path):
    wav = asyncio.run(tracks.audio_window_wav(media, 0.5, 1.0))
    assert wav[:4] == b"RIFF"
    t = asyncio.run(tracks.probe_tracks(media))
    p = asyncio.run(tracks.extract_audio_track(media, t["audio"][1]["index"], tmp_path / "x.m4a"))
    assert p.stat().st_size > 256


def test_vtt_seconds():
    assert tracks.vtt_seconds("01:02:03.500") == 3723.5
    assert tracks.vtt_seconds("02:03.250") == 123.25


class StubEngine:
    name = "stub"
    calls = 0

    def transcribe_wav(self, wav, task="translate"):
        StubEngine.calls += 1
        return {"language": "es", "cues": [{"start": 1.0, "end": 2.0, "text": "Hello"}]}


def test_caption_window_cached_and_single_flight(media, tmp_path):
    from telegram_archive.playback import captions
    svc = captions.CaptionService(tmp_path, StubEngine())

    async def get_wav(s, d):
        await asyncio.sleep(0.05)
        return await tracks.audio_window_wav(media, s, 1.0)

    async def go():
        a, b = await asyncio.gather(svc.window("k", 31.0, None, get_wav), svc.window("k", 40.0, None, get_wav))
        c = await svc.window("k", 35.0, None, get_wav)
        return a, b, c
    a, b, c = asyncio.run(go())
    assert a == b == c and a["start"] == 30.0
    assert a["cues"][0]["start"] == 31.0 and a["language"] == "es"
    assert StubEngine.calls == 1


def test_original_language_is_cached_separately(media, tmp_path):
    from telegram_archive.playback import captions
    seen = []

    class E:
        name = "e"
        def transcribe_wav(self, wav, task="translate"):
            seen.append(task)
            return {"language": "ja", "cues": []}
    svc = captions.CaptionService(tmp_path, E())

    async def get_wav(s, d):
        return await tracks.audio_window_wav(media, s, 1.0)

    async def go():
        await svc.window("k", 0, None, get_wav, "translate")
        await svc.window("k", 0, None, get_wav, "transcribe")
        await svc.window("k", 0, None, get_wav, "transcribe")
    asyncio.run(go())
    assert seen == ["translate", "transcribe"]


def test_window_waits_for_model_then_works(media, tmp_path):
    from telegram_archive.playback import captions

    class Slow:
        name = "slow"
        def prepare(self):
            import time; time.sleep(0.2)
        def transcribe_wav(self, wav, task="translate"):
            return {"language": "en", "cues": []}
    svc = captions.CaptionService(tmp_path, Slow())

    async def get_wav(s, d):
        return await tracks.audio_window_wav(media, s, 1.0)

    async def go():
        try:
            await svc.window("k", 0, None, get_wav)
            first = "ready"
        except captions.CaptionsPreparing:
            first = "preparing"
        for _ in range(40):
            if svc.model_state == "ready":
                break
            await asyncio.sleep(0.05)
        await svc.window("k", 0, None, get_wav)
        return first, svc.status()["model"]
    assert asyncio.run(go()) == ("preparing", "ready")


def test_a_seek_is_not_stuck_behind_windows_the_viewer_already_left(media, tmp_path):
    from telegram_archive.playback import captions
    import time

    class Slow:
        name = "slow"
        def transcribe_wav(self, wav, task="translate"):
            time.sleep(0.3)
            return {"language": "en", "cues": []}
    svc = captions.CaptionService(tmp_path, Slow())

    async def get_wav(s, d):
        return await tracks.audio_window_wav(media, 0.0, 1.0)

    async def go():
        a = asyncio.create_task(svc.window("k", 0, None, get_wav))        # running
        await asyncio.sleep(0.05)
        b = asyncio.create_task(svc.window("k", 30, None, get_wav))       # queued behind it
        await asyncio.sleep(0.05)
        d = asyncio.create_task(svc.window("k", 900, None, get_wav))      # the viewer seeks far away
        return await asyncio.gather(a, b, d, return_exceptions=True)
    a, b, d = asyncio.run(go())
    assert isinstance(a, dict)
    assert isinstance(b, captions.CaptionsStale)                          # skipped, not transcribed
    assert isinstance(d, dict) and d["start"] == 900                      # the new position got its turn


def test_a_window_nobody_waits_for_is_skipped(media, tmp_path):
    from telegram_archive.playback import captions
    svc = captions.CaptionService(tmp_path, StubEngine())

    async def get_wav(s, d):
        return await tracks.audio_window_wav(media, 0.0, 1.0)

    async def gone():
        return True
    before = StubEngine.calls
    with pytest.raises(captions.CaptionsStale):
        asyncio.run(svc.window("k2", 0, None, get_wav, is_gone=gone))
    assert StubEngine.calls == before
