"""Live English captions from speech, using the optional local faster-whisper engine.

Audio is cut into fixed windows; each window is transcribed once (task "translate" gives English for any spoken language) and cached on disk.
The engine is loaded lazily, so the app runs without it and the player simply reports captions as unavailable.
"""
from __future__ import annotations

import asyncio
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

    def transcribe_wav(self, wav: bytes, task: str = "translate") -> dict:
        import numpy as np  # installed with faster-whisper
        with wave.open(io.BytesIO(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        with self._lock:
            model = self._load()
            segs, info = model.transcribe(
                audio, task="translate" if task == "translate" else "transcribe", beam_size=5, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 400, "threshold": 0.35},
                condition_on_previous_text=False, no_speech_threshold=0.7, temperature=[0.0, 0.2, 0.4])
            cues = [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segs if s.text.strip()]
        return {"language": getattr(info, "language", "") or "", "cues": cues}


def window_start(t: float) -> float:
    return max(0.0, (float(t) // WINDOW) * WINDOW)


class CaptionsPreparing(Exception):
    """The speech model is still downloading or loading."""


class CaptionsStale(Exception):
    """The viewer moved on (seeked away, switched video or left) before this window got its turn; it was skipped."""


class CaptionService:
    """Caches windows on disk and makes sure one window is never transcribed twice at once."""

    def __init__(self, cache_dir: Path, engine: CaptionEngine | None = None):
        self.cache_dir = Path(cache_dir)
        self.engine = engine
        self._inflight: dict[tuple, asyncio.Future] = {}
        self._gate: asyncio.Lock | None = None            # one transcription at a time, so a seek is not stuck behind old windows
        self._latest: dict[str, float] = {}                # newest window start asked for, per video
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
        """A window far from the newest one asked for belongs to a position the viewer already left."""
        latest = self._latest.get(key)
        return latest is not None and abs(start - latest) > 2 * WINDOW

    async def window(self, key: str, start: float, track: int | None, get_wav, task: str = "translate", is_gone=None) -> dict:
        """Cues (absolute seconds) of the window beginning at `start`. `get_wav(start, duration)` is an async callable returning WAV bytes.
        `is_gone` (optional async callable) says the caller disconnected; windows that nobody waits for any more are not transcribed."""
        start = window_start(start)
        self._latest[key] = start
        if len(self._latest) > 64:
            self._latest.pop(next(iter(self._latest)))
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
            return await asyncio.shield(self._inflight[fk])
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[fk] = fut
        try:
            engine = self._engine()
            if self._gate is None:
                self._gate = asyncio.Lock()
            async with self._gate:
                if self._stale(key, start) or (is_gone is not None and await is_gone()):
                    raise CaptionsStale()
                wav = await get_wav(start, WINDOW)
                res = await asyncio.to_thread(engine.transcribe_wav, wav, task)
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
