"""When a live caption appears and goes: it follows the words, not whisper's loose segment times. Plus the speech model choice."""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import pytest

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axct_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from telegram_archive.playback import captions  # noqa: E402
from telegram_archive.playback.captions import cues_from_words  # noqa: E402


def W(text, a, b):
    return (f" {text}", a, b)


# ---- the rule that turns timed words into captions -------------------------------------------------------------------------------------------------------

def test_one_segment_that_holds_three_sentences_becomes_three_captions():
    """What large-v3-turbo really returned for three phrases spoken at 2, 12.5 and 22 s (truth: 2.0-5.8, 12.5-16.1, 22.0-25.6): one segment from 1.8 to 25.3,
    with 'We' timed from 15.6 to 22.4, a one-syllable word 'lasting' seven seconds."""
    starts = dict(The=1.8, quick=2.3, brown=2.5, fox=2.9, jumps=3.2, over=3.5, the=3.9, lazy=4.1, dog=4.4, near=4.7, the2=4.9, riverbank=5.0)
    first = [W(k.rstrip("2"), v, nxt) for (k, v), nxt in zip(starts.items(), [*list(starts.values())[1:], 5.55])]
    second_words = "Please remember to close the door when you leave the house tonight".split()
    second_starts = [11.9, 13.0, 13.4, 13.6, 13.9, 14.1, 14.2, 14.4, 14.5, 14.8, 14.9, 15.2]
    second = [W(t, a, b) for t, a, b in zip(second_words, second_starts, [*second_starts[1:], 15.6])]
    third_words = "We will meet again tomorrow morning at nine o'clock sharp".split()
    third_starts = [15.6, 22.4, 22.6, 22.8, 23.1, 23.5, 24.0, 24.2, 24.4, 24.9]
    third = [W(t, a, b) for t, a, b in zip(third_words, third_starts, [22.4, *third_starts[2:], 25.32])]
    cues = cues_from_words(first + second + third, [(2.01, 5.96), (12.6, 16.17), (22.04, 25.74)])
    assert [c["text"].split()[0] for c in cues] == ["The", "Please", "We"]
    for cue, (a, b) in zip(cues, [(2.0, 5.83), (12.5, 16.05), (22.0, 25.63)]):
        assert abs(cue["start"] - a) <= 0.25, cues       # it comes up when the speech does
        assert abs(cue["end"] - b) <= 0.6, cues          # and goes when the speech stops


def test_a_caption_starts_when_the_voice_starts_not_where_whisper_put_the_first_word():
    """After a long silence the first word is timed early (here 11.9 s for speech that starts at 12.5 s); the voice detector knows better."""
    words = [W("Please", 11.9, 13.0), W("close", 13.0, 13.4), W("door", 13.4, 14.0)]
    cues = cues_from_words(words, [(12.55, 16.1)])
    assert cues[0]["start"] == 12.55 and cues[0]["end"] == 14.0


def test_a_long_pause_makes_a_new_caption_and_a_long_run_is_cut_at_a_sentence_end():
    pause = cues_from_words([W("one", 1.0, 1.4), W("two", 1.4, 1.8), W("three", 3.5, 3.9)], [])
    assert [c["text"] for c in pause] == ["one two", "three"]
    long_run = [W(f"word{i}", i * 0.5, i * 0.5 + 0.45) for i in range(30)]
    long_run[13] = W("end.", 6.5, 6.95)
    cues = cues_from_words(long_run, [])
    assert len(cues) >= 2 and cues[0]["text"].endswith("end.")
    assert all(c["end"] - c["start"] <= 9.5 for c in cues)


def test_a_one_word_caption_stays_long_enough_to_read_but_not_over_the_next():
    a = cues_from_words([W("Yes", 5.0, 5.15)], [])
    assert a[0]["end"] - a[0]["start"] >= 0.7
    b = cues_from_words([W("Yes", 5.0, 5.15), W("indeed", 6.2, 6.8)], [])
    assert b[0]["end"] <= b[1]["start"]


def test_a_stray_word_next_to_the_following_caption_joins_it():
    cues = cues_from_words([W("We", 22.09, 22.31), W("will", 22.36, 22.6), W("meet", 22.6, 22.9), W("again", 22.9, 23.3)], [])
    assert [c["text"] for c in cues] == ["We will meet again"] and cues[0]["start"] == 22.09
    far = cues_from_words([W("Yes", 5.0, 5.2), W("we", 7.0, 7.2), W("will", 7.2, 7.5)], [])
    assert [c["text"] for c in far] == ["Yes", "we will"]                               # a word on its own after a real pause stays on its own


def test_words_without_time_are_left_to_the_loose_segment_times():
    assert cues_from_words([], [(0.0, 1.0)]) == []


# ---- the speech model choice ----------------------------------------------------------------------------------------------------------------------------

class Quick:
    name = "quick"

    def transcribe_wav(self, wav, task="translate"):
        return {"language": "en", "cues": [{"start": 1.0, "end": 2.0, "text": "hi"}]}


def test_each_model_has_its_own_cached_windows_and_the_default_keeps_its_old_names(tmp_path):
    svc = captions.CaptionService(tmp_path, engine=Quick())
    assert svc.model == "small"
    a = svc._path("1_2", 30.0, None, "translate")
    svc.set_model("large-v3-turbo")
    b = svc._path("1_2", 30.0, None, "translate")
    assert a != b and b.name.startswith("large-v3-turbo_") and not a.name.startswith("small_")
    assert a.name.endswith(f"_{captions.CACHE_TAG}.json")                              # windows made before words were timed are not reused


def test_set_model_refuses_an_unknown_name_and_forgets_the_loaded_engine(tmp_path):
    svc = captions.CaptionService(tmp_path)
    with pytest.raises(ValueError):
        svc.set_model("giant")
    svc.engine = Quick()
    svc.set_model("medium")
    assert svc.engine is None and svc.status()["model_name"] == "medium"


def test_speed_is_measured_from_the_windows_that_were_made(tmp_path):
    svc = captions.CaptionService(tmp_path, engine=Quick())

    async def wav(s, d):
        return b"RIFF"
    asyncio.run(svc.window("1_2", 0.0, None, wav))
    assert svc.status()["speed"] > 0                                                     # audio seconds per second of work; Quick is far faster than real time


# ---- the real engine, on speech made at known times ---------------------------------------------------------------------------------------------------

def _speech_file(path: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    texts = ["The quick brown fox jumps over the lazy dog near the river bank.", "Please remember to close the door when you leave the house tonight.", "We will meet again tomorrow morning at nine o'clock sharp."]
    parts = []
    for i, t in enumerate(texts):
        p = path.with_name(f"p{i}.wav")
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"flite=text='{t}':voice=slt", "-ar", "16000", "-ac", "1", str(p)], capture_output=True)
        if done.returncode != 0:
            return False
        parts.append(p)
    done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *sum((["-i", str(p)] for p in parts), []), "-filter_complex",
                           "[0]adelay=2000[a];[1]adelay=12500[b];[2]adelay=22000[c];[a][b][c]amix=inputs=3:normalize=0,apad=whole_dur=32", "-ar", "16000", "-ac", "1", str(path)], capture_output=True)
    return done.returncode == 0


@pytest.mark.skipif(not captions.available(), reason="faster-whisper is not installed")
def test_real_engine_captions_start_and_stop_with_the_speech(tmp_path):
    wavfile = tmp_path / "speech.wav"
    if not _speech_file(wavfile):
        pytest.skip("ffmpeg with the flite voice is not available")
    eng = captions.FasterWhisperEngine(model="small")
    try:
        eng.prepare()
    except Exception as exc:   # no network and no downloaded model
        pytest.skip(f"the speech model is not available here: {type(exc).__name__}")
    cues = eng.transcribe_wav(wavfile.read_bytes(), "translate")["cues"]
    truth = [(2.0, 5.83), (12.5, 16.05), (22.0, 25.63)]
    assert len(cues) >= 3
    for want_start, want_end in truth:
        near = min(cues, key=lambda c: abs(c["start"] - want_start))
        assert abs(near["start"] - want_start) <= 0.4, f"a caption should start near {want_start}s, started at {near['start']}s: {cues}"
        last = max((c for c in cues if want_start - 0.4 <= c["start"] <= want_end), key=lambda c: c["end"])
        assert want_end - 0.5 <= last["end"] <= want_end + 0.9, f"the caption should end near {want_end}s, ended at {last['end']}s: {cues}"
