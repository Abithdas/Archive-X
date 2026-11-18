"""Live English captions from speech, using the optional local faster-whisper engine.

Audio is cut into fixed windows; each window is transcribed once (task "translate" gives English for any spoken language) and cached on disk.
The engine is loaded lazily, so the app runs without it and the player simply reports captions as unavailable.
"""
from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
import threading
import time
import wave
from pathlib import Path
from typing import Any, Protocol

from ..core.safe_paths import inside

WINDOW = 30.0  # seconds per caption window
CACHE_TAG = "t2"   # cached windows made before words were timed (cues that started early and stayed on the screen) are not reused

# The speech models offered in Settings. A bigger model is more accurate, not faster: on a processor "large-v3-turbo" runs at about half the speed of "small"
# (3.7 against 7.6 times real time on a 4-core PC); on an NVIDIA card it is fast. Windows are cached per model, so changing the model never shows the old model's words.
MODELS: dict[str, dict] = {
    "small": {"label": "Fast", "size": "460 MB", "note": "Runs well on any PC. Good for English; fair for other languages."},
    "medium": {"label": "Balanced", "size": "1.5 GB", "note": "Better with accents and other languages, about half as fast as Fast on a processor."},
    "large-v3-turbo": {"label": "Accurate", "size": "1.6 GB", "note": "The best words. Quick with an NVIDIA card; on a processor it is about half as fast as Fast, so it may fall behind on a slow PC."},
}
DEFAULT_MODEL = "small"


class CaptionEngine(Protocol):
    name: str

    def transcribe_wav(self, wav: bytes, task: str = "translate") -> dict: ...
    # An engine may also accept `cancelled` (a callable returning True once the viewer has left the window) and raise
    # TranscribeCancelled between two pieces of work; engines without it simply run to the end.


class TranscribeCancelled(Exception):
    """Raised by an engine, between two segments, when cancelled() says nobody needs this window any more."""


def available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


MAX_GAP = 0.8        # a pause this long inside one segment starts a new caption
MAX_CHARS = 84       # about two lines
MAX_SECONDS = 6.5
MIN_SHOWN = 0.7      # a caption stays at least this long, so a one-word caption can be read (never past the next caption)
ONSET_PAD_MS = 150   # the voice detector hears speech a little after it starts; this much is given back


def _plausible(w: tuple[str, float, float]) -> tuple[str, float, float]:
    """Whisper often times the first word after a silence as if it began long before it was said (a one-syllable word 'lasting' seven seconds). A word cannot
    last that long: its start is moved up to where a word of that length would start, counting back from its end."""
    text, start, end = w
    letters = len(text.strip())
    longest = max(1.0, 0.12 * letters + 0.5)
    if end - start > longest:
        start = max(start, end - (0.1 * letters + 0.25))
    return text, start, end


def cues_from_words(words: list[tuple[str, float, float]], chunks: list[tuple[float, float]]) -> list[dict]:
    """Captions from timed words. Whisper's own segment times are loose: a segment can start before the speech and stay on through the silence after it
    (and after a long silence the first word is often timed early too). So: split at pauses and at about two lines, and keep each caption inside the stretch of
    speech the voice detector found, `chunks` ((start, end) seconds), when there are any."""
    words = [_plausible(w) for w in words]
    groups: list[list[tuple[str, float, float]]] = []
    cur: list[tuple[str, float, float]] = []
    for w in words:
        if cur:
            text = "".join(x[0] for x in cur).strip()
            gap = w[1] - cur[-1][2]
            span = w[2] - cur[0][1]
            sentence_end = text.endswith((".", "?", "!", "。", "？", "！"))
            if gap > MAX_GAP or ((len(text) > MAX_CHARS or span > MAX_SECONDS) and (sentence_end or len(text) > MAX_CHARS + 24 or span > MAX_SECONDS + 3)):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    cues: list[dict] = []
    for g in groups:
        start, end = g[0][1], g[-1][2]
        if chunks:
            home = next((c for c in chunks if c[1] >= start), None)   # the stretch of speech this caption starts in (or the next one, when it starts in silence)
            if home is not None:
                start = max(start, home[0])
                end = min(end, home[1]) if home[1] > start else end
                if g[-1][1] > home[1]:   # the caption runs on through a pause the detector made: keep the words' own end
                    end = g[-1][2]
        text = "".join(x[0] for x in g).strip()
        if text:
            cues.append({"start": round(float(start), 2), "end": round(float(max(end, start + 0.2)), 2), "text": text})
    merged: list[dict] = []
    for c in cues:   # a fragment of a word or two that ends right where the next caption starts belongs to it ("We" + "will meet again ...")
        prev = merged[-1] if merged else None
        if prev is not None and len(prev["text"].split()) <= 2 and prev["end"] - prev["start"] < 1.0 and c["start"] - prev["end"] <= 0.5:
            c = {"start": prev["start"], "end": c["end"], "text": f"{prev['text']} {c['text']}"}
            merged[-1] = c
        else:
            merged.append(c)
    cues = merged
    for i, c in enumerate(cues):   # short ones stay long enough to read, but never over the next caption
        room = (cues[i + 1]["start"] - c["start"]) if i + 1 < len(cues) else float("inf")
        c["end"] = round(max(c["end"], c["start"] + min(MIN_SHOWN, max(room - 0.05, 0.2))), 2)
    return cues


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, model: str | None = None, cache_dir: Path | None = None):
        # "small" is the smallest model that handles Chinese/Japanese/Korean reasonably; "base" and "tiny" mostly fail on them.
        # Set ARCHIVE_X_WHISPER_MODEL=medium (or large-v3) for better accuracy on a faster PC.
        self.model_name = model or os.environ.get("ARCHIVE_X_WHISPER_MODEL", DEFAULT_MODEL)
        self.cache_dir = cache_dir
        self._model: Any = None
        self._device = os.environ.get("ARCHIVE_X_WHISPER_DEVICE", "auto")   # "auto" uses an NVIDIA card when there is one; "cpu" never does
        self._lock = threading.Lock()
        self._used_device = ""

    @property
    def device(self) -> str:
        """"cuda" or "cpu" once the model is loaded (what it really runs on), else what was asked for."""
        return self._used_device or self._device

    def prepare(self) -> None:
        """Load the model, downloading it first time (about 460 MB for "small"), and run it once on a second of silence, so "ready" means it really runs."""
        with self._lock:
            self._warm_up()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore
            kw = {"download_root": str(self.cache_dir)} if self.cache_dir else {}
            self._model = WhisperModel(self.model_name, device=self._device, compute_type="int8", **kw)
            try:
                self._used_device = "cuda" if getattr(getattr(self._model, "model", None), "device", "") == "cuda" else "cpu"
            except Exception:
                self._used_device = ""
        return self._model

    @staticmethod
    def _gpu_trouble(exc: BaseException) -> bool:
        """An NVIDIA card is found but its libraries are not (cublas, cudnn, a driver too old): the usual way speech recognition fails on a PC that has a card."""
        text = f"{type(exc).__name__} {exc}".lower()
        return any(w in text for w in ("cublas", "cudnn", "cuda", "nvrtc", "cudart"))

    def _warm_up(self) -> None:
        import numpy as np
        silence = np.zeros(16000, dtype="float32")
        for attempt in (0, 1):
            try:
                segs, _info = self._load().transcribe(silence, beam_size=1)
                list(segs)
                return
            except Exception as exc:
                if attempt == 0 and self._device != "cpu" and self._gpu_trouble(exc):
                    self._model, self._device, self._used_device = None, "cpu", ""   # carry on without the card
                    continue
                raise

    @staticmethod
    def _speech(audio) -> list[tuple[float, float]]:
        """Where there is speech, (start, end) seconds, from the voice detector that comes with faster-whisper (a little padding at the start, see ONSET_PAD_MS)."""
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps  # type: ignore
            found = get_speech_timestamps(audio, VadOptions(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=ONSET_PAD_MS))
            return [(c["start"] / 16000.0, c["end"] / 16000.0) for c in found]
        except Exception:
            return []   # captions are then timed by the words alone

    def transcribe_wav(self, wav: bytes, task: str = "translate", cancelled=None) -> dict:
        import numpy as np  # installed with faster-whisper
        with wave.open(io.BytesIO(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        chunks = self._speech(audio)
        with self._lock:
            for attempt in (0, 1):
                try:
                    segs, info = self._load().transcribe(
                        audio, task="translate" if task == "translate" else "transcribe", beam_size=5, vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 400, "threshold": 0.35},
                        condition_on_previous_text=False, no_speech_threshold=0.7, temperature=[0.0, 0.2, 0.4],
                        word_timestamps=True, hallucination_silence_threshold=2.0)
                    words: list[tuple[str, float, float]] = []
                    loose: list[dict] = []
                    for s in segs:   # segments are produced one at a time, so a window the viewer has left can be abandoned between two of them
                        if cancelled is not None and cancelled():
                            raise TranscribeCancelled()
                        if not s.text.strip():
                            continue
                        timed = [(w.word, float(w.start), float(w.end)) for w in (getattr(s, "words", None) or []) if w.start is not None and w.end is not None]
                        if timed:
                            words.extend(timed)
                        else:   # no word times for this segment: its own, loose ones
                            loose.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip()})
                    cues = sorted(cues_from_words(words, chunks) + loose, key=lambda c: c["start"])
                    return {"language": getattr(info, "language", "") or "", "cues": cues}
                except TranscribeCancelled:
                    raise
                except Exception as exc:
                    if attempt == 0 and self._device != "cpu" and self._gpu_trouble(exc):
                        self._model, self._device, self._used_device = None, "cpu", ""
                        continue
                    raise
        return {"language": "", "cues": []}


def window_start(t: float) -> float:
    return max(0.0, (float(t) // WINDOW) * WINDOW)


class CaptionsPreparing(Exception):
    """The speech model is still downloading or loading."""


class CaptionsStale(Exception):
    """The viewer moved on (seeked away, switched video or left) before this window got its turn; it was skipped."""


class _Waiter:
    __slots__ = ("key", "start", "seq", "fut")

    def __init__(self, key: str, start: float, seq: int, fut: asyncio.Future):
        self.key, self.start, self.seq, self.fut = key, start, seq, fut


class CaptionService:
    """Caches windows on disk and makes sure one window is never transcribed twice at once."""

    def __init__(self, cache_dir: Path, engine: CaptionEngine | None = None, model: str | None = None):
        self.cache_dir = Path(cache_dir)
        self.engine = engine
        self.model = model if model in MODELS else os.environ.get("ARCHIVE_X_WHISPER_MODEL", DEFAULT_MODEL)
        self.speed = 0.0                                   # audio seconds transcribed per second of work, over the last windows (1.0 is real time)
        self._inflight: dict[tuple, asyncio.Future] = {}
        self._askers: dict[tuple, list] = {}               # per window in flight: the is_gone checks of everyone waiting for it
        self._busy = False                                 # one transcription at a time ...
        self._waiting: list[_Waiter] = []                  # ... and the others wait here; the viewer's own window goes first
        self._seq = 0
        self._latest: dict[str, float] = {}                # newest window start asked for, per video
        self._pos: dict[str, float] = {}                   # window the viewer is in, per video, when the player says so (`at`)
        self.model_state = "ready" if engine is not None and not hasattr(engine, "prepare") else "idle"   # idle | preparing | ready | error
        self.model_error = ""
        self._prep: asyncio.Task | None = None

    def ensure_ready(self) -> bool:
        """Start loading/downloading the model in the background if needed. True once it is ready."""
        if self.model_state == "ready":
            return True
        if self.model_state in ("idle", "error") and (self._prep is None or self._prep.done()):
            self.model_state, self.model_error = "preparing", ""
            self._prep = asyncio.get_running_loop().create_task(self._prepare())
        return False

    async def _prepare(self) -> None:
        try:
            await asyncio.to_thread(self._engine().prepare)   # type: ignore[attr-defined]
            self.model_state = "ready"
        except Exception as exc:
            self.model_state, self.model_error = "error", f"{type(exc).__name__}: {exc}"[:300]

    def set_model(self, name: str) -> None:
        """Use another speech model from now on: the next window loads it (downloading it the first time). Windows already made are kept, per model."""
        if name not in MODELS:
            raise ValueError(f"unknown speech model {name!r}")
        if name == self.model:
            return
        self.model = name
        self.engine = None
        self.speed = 0.0
        if self._prep is not None and not self._prep.done():
            self._prep.cancel()
        self.model_state, self.model_error = "idle", ""

    def status(self) -> dict:
        eng = self.engine
        return {"available": bool(eng) or available(), "engine": getattr(eng, "name", "faster-whisper") if (eng or available()) else "",
                "install": "" if (eng or available()) else "pip install faster-whisper",
                "model": self.model_state, "model_error": self.model_error,
                "model_name": self.model, "device": str(getattr(eng, "device", "") or ""), "speed": round(self.speed, 1)}

    def _engine(self) -> CaptionEngine:
        if self.engine is None:
            if not available():
                raise RuntimeError("faster-whisper is not installed")
            self.engine = FasterWhisperEngine(model=self.model, cache_dir=self.cache_dir / "models")
        return self.engine

    def _path(self, key: str, start: float, track: int | None, task: str) -> Path:
        model = "" if self.model == DEFAULT_MODEL else f"{self.model}_"   # the default model's windows keep their old names
        return inside(Path(self.cache_dir) / "captions", key, f"{model}{'en' if task == 'translate' else 'orig'}_{'a' if track is None else int(track)}_{int(start)}_{CACHE_TAG}.json")

    def _stale(self, key: str, start: float) -> bool:
        """Nobody needs this window any more. When the player says where the viewer is (`at`), that is a window behind them or more than one
        ahead; otherwise (an older player) one far from the newest window asked for."""
        pos = self._pos.get(key)
        if pos is not None:
            return start < pos or start > pos + WINDOW
        latest = self._latest.get(key)
        return latest is not None and abs(start - latest) > 2 * WINDOW

    def _outranked(self, key: str, start: float) -> bool:
        """A window that is not the viewer's own (the lookahead) gives way as soon as the viewer's own window is waiting for the engine."""
        pos = self._pos.get(key)
        return pos is not None and start != pos and any(w.key == key and w.start == pos for w in self._waiting)

    def _rank(self, w: "_Waiter") -> tuple:
        """Who goes next: the window the viewer is in, then the one after it, then whatever is nearest; first come first served among equals."""
        pos = self._pos.get(w.key)
        if pos is not None:
            return (0 if w.start == pos else 1 if w.start == pos + WINDOW else 2, abs(w.start - pos), w.seq)
        return (2, abs(w.start - self._latest.get(w.key, w.start)), w.seq)

    def _dispatch(self) -> None:
        """Give the turn to the best waiting window (dropping the ones nobody needs any more) when nothing is running."""
        while not self._busy and self._waiting:
            for w in list(self._waiting):
                if self._stale(w.key, w.start):
                    self._waiting.remove(w)
                    if not w.fut.done():
                        w.fut.set_exception(CaptionsStale())
            if not self._waiting:
                return
            best = min(self._waiting, key=self._rank)
            self._waiting.remove(best)
            if best.fut.done():   # its request went away while it waited
                continue
            self._busy = True
            best.fut.set_result(True)

    def _give_turn(self) -> None:
        self._busy = False
        self._dispatch()

    async def _take_turn(self, key: str, start: float, is_gone) -> None:
        waiter = _Waiter(key, start, self._seq, asyncio.get_running_loop().create_future())
        self._seq += 1
        self._waiting.append(waiter)
        self._dispatch()
        try:
            await waiter.fut
        except BaseException:
            if waiter in self._waiting:
                self._waiting.remove(waiter)
            elif waiter.fut.done() and not waiter.fut.cancelled() and waiter.fut.exception() is None:
                self._give_turn()   # the turn had been granted, but this request went away before using it
            raise
        try:
            if is_gone is not None and await is_gone():
                raise CaptionsStale()
        except BaseException:
            self._give_turn()
            raise

    async def _all_gone(self, fk: tuple) -> bool:
        """True when every request waiting for this window has disconnected (a request without a way to tell counts as still here)."""
        for gone in list(self._askers.get(fk, [])):
            if gone is None or not await gone():
                return False
        return True

    def _run_engine(self, engine, wav: bytes, task: str, key: str, start: float) -> dict:
        """Runs in a worker thread. An engine that can be cancelled is told to stop as soon as the viewer has left this window."""
        if "cancelled" in inspect.signature(engine.transcribe_wav).parameters:
            try:
                return engine.transcribe_wav(wav, task, cancelled=lambda: self._stale(key, start) or self._outranked(key, start))
            except TranscribeCancelled:
                raise CaptionsStale() from None
        return engine.transcribe_wav(wav, task)

    async def window(self, key: str, start: float, track: int | None, get_wav, task: str = "translate", is_gone=None, at: float | None = None) -> dict:
        """Cues (absolute seconds) of the window beginning at `start`. `get_wav(start, duration)` is an async callable returning WAV bytes.
        `is_gone` (optional async callable) says the caller disconnected; windows that nobody waits for any more are not transcribed.
        `at` (optional) is the playback time of the viewer: their window is served first and windows behind or far ahead of it are dropped."""
        start = window_start(start)
        self._latest[key] = start
        if len(self._latest) > 64:
            self._latest.pop(next(iter(self._latest)))
        if at is not None:
            self._pos[key] = window_start(at)
            if len(self._pos) > 64:
                self._pos.pop(next(iter(self._pos)))
            self._dispatch()   # waiting windows the viewer has just left are dropped now, not when their turn comes
        if not self.ensure_ready():
            raise CaptionsPreparing(self.model_state)
        path = self._path(key, start, track, task)
        if path.exists():
            try:
                return json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                pass
        fk = (key, start, track, task)
        if fk in self._inflight:
            self._askers.setdefault(fk, []).append(is_gone)   # one more request wants this window: it is not abandoned while any of them is here
            return await asyncio.shield(self._inflight[fk])
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[fk] = fut
        self._askers[fk] = [is_gone]
        try:
            engine = self._engine()
            await self._take_turn(key, start, lambda: self._all_gone(fk))
            try:
                wav = await get_wav(start, WINDOW)
                began = time.monotonic()
                res = await asyncio.to_thread(self._run_engine, engine, wav, task, key, start)
                took = max(time.monotonic() - began, 0.01)
                rate = WINDOW / took
                self.speed = rate if self.speed == 0.0 else 0.6 * self.speed + 0.4 * rate
            finally:
                self._give_turn()
            out = {"start": start, "end": start + WINDOW, "language": res.get("language", ""),
                   "cues": [{"start": start + c["start"], "end": start + c["end"], "text": c["text"]} for c in res.get("cues", [])]}
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(out), "utf-8")
            tmp.replace(path)
            fut.set_result(out)
            return out
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
                fut.exception()  # mark retrieved so unwatched failures don't log noise
            raise
        finally:
            self._inflight.pop(fk, None)
            self._askers.pop(fk, None)
