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
import wave
from pathlib import Path
from typing import Any, Protocol

WINDOW = 30.0  # seconds per caption window


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


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, model: str | None = None, cache_dir: Path | None = None):
        # "small" is the smallest model that handles Chinese/Japanese/Korean reasonably; "base" and "tiny" mostly fail on them.
        # Set ARCHIVE_X_WHISPER_MODEL=medium (or large-v3) for better accuracy on a faster PC.
        self.model_name = model or os.environ.get("ARCHIVE_X_WHISPER_MODEL", "small")
        self.cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()

    def prepare(self) -> None:
        """Load the model, downloading it first time (about 460 MB for "small")."""
        with self._lock:
            self._load()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore
            kw = {"download_root": str(self.cache_dir)} if self.cache_dir else {}
            self._model = WhisperModel(self.model_name, device="auto", compute_type="int8", **kw)
        return self._model

    def transcribe_wav(self, wav: bytes, task: str = "translate", cancelled=None) -> dict:
        import numpy as np  # installed with faster-whisper
        with wave.open(io.BytesIO(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        with self._lock:
            model = self._load()
            segs, info = model.transcribe(
                audio, task="translate" if task == "translate" else "transcribe", beam_size=5, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 400, "threshold": 0.35},
                condition_on_previous_text=False, no_speech_threshold=0.7, temperature=[0.0, 0.2, 0.4])
            cues = []
            for s in segs:   # segments are produced one at a time, so a window the viewer has left can be abandoned between two of them
                if cancelled is not None and cancelled():
                    raise TranscribeCancelled()
                if s.text.strip():
                    cues.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip()})
        return {"language": getattr(info, "language", "") or "", "cues": cues}


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

    def __init__(self, cache_dir: Path, engine: CaptionEngine | None = None):
        self.cache_dir = Path(cache_dir)
        self.engine = engine
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

    def status(self) -> dict:
        eng = self.engine
        return {"available": bool(eng) or available(), "engine": getattr(eng, "name", "faster-whisper") if (eng or available()) else "",
                "install": "" if (eng or available()) else "pip install faster-whisper",
                "model": self.model_state, "model_error": self.model_error}

    def _engine(self) -> CaptionEngine:
        if self.engine is None:
            if not available():
                raise RuntimeError("faster-whisper is not installed")
            self.engine = FasterWhisperEngine(cache_dir=self.cache_dir / "models")
        return self.engine

    def _path(self, key: str, start: float, track: int | None, task: str) -> Path:
        return self.cache_dir / "captions" / key / f"{'en' if task == 'translate' else 'orig'}_{'a' if track is None else track}_{int(start)}.json"

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
                res = await asyncio.to_thread(self._run_engine, engine, wav, task, key, start)
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
