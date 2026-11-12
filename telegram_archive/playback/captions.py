"""Live English captions from speech, using the optional local faster-whisper engine.

Audio is cut into fixed windows; each window is transcribed once (task "translate" gives English for any spoken language) and cached on disk.
The engine is loaded lazily, so the app runs without it and the player simply reports captions as unavailable.
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
import wave
from pathlib import Path
from typing import Any, Protocol

WINDOW = 30.0  # seconds per caption window


class CaptionEngine(Protocol):
    name: str

    def transcribe_wav(self, wav: bytes) -> dict: ...


def available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, model: str = "base", cache_dir: Path | None = None):
        self.model_name = model
        self.cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore
            kw = {"download_root": str(self.cache_dir)} if self.cache_dir else {}
            self._model = WhisperModel(self.model_name, device="auto", compute_type="int8", **kw)
        return self._model

    def transcribe_wav(self, wav: bytes) -> dict:
        import numpy as np  # installed with faster-whisper
        with wave.open(io.BytesIO(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
        with self._lock:
            model = self._load()
            segs, info = model.transcribe(audio, task="translate", vad_filter=True, beam_size=1)
            cues = [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segs if s.text.strip()]
        return {"language": getattr(info, "language", "") or "", "cues": cues}


def window_start(t: float) -> float:
    return max(0.0, (float(t) // WINDOW) * WINDOW)


class CaptionService:
    """Caches windows on disk and makes sure one window is never transcribed twice at once."""

    def __init__(self, cache_dir: Path, engine: CaptionEngine | None = None):
        self.cache_dir = Path(cache_dir)
        self.engine = engine
        self._inflight: dict[tuple, asyncio.Future] = {}

    def status(self) -> dict:
        eng = self.engine
        return {"available": bool(eng) or available(), "engine": getattr(eng, "name", "faster-whisper") if (eng or available()) else "",
                "install": "" if (eng or available()) else "pip install faster-whisper"}

    def _engine(self) -> CaptionEngine:
        if self.engine is None:
            if not available():
                raise RuntimeError("faster-whisper is not installed")
            self.engine = FasterWhisperEngine(cache_dir=self.cache_dir / "models")
        return self.engine

    def _path(self, key: str, start: float, track: int | None) -> Path:
        return self.cache_dir / "captions" / key / f"{'a' if track is None else track}_{int(start)}.json"

    async def window(self, key: str, start: float, track: int | None, get_wav) -> dict:
        """Cues (absolute seconds) of the window beginning at `start`. `get_wav(start, duration)` is an async callable returning WAV bytes."""
        start = window_start(start)
        path = self._path(key, start, track)
        if path.exists():
            try:
                return json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                pass
        fk = (key, start, track)
        if fk in self._inflight:
            return await asyncio.shield(self._inflight[fk])
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[fk] = fut
        try:
            engine = self._engine()
            wav = await get_wav(start, WINDOW)
            res = await asyncio.to_thread(engine.transcribe_wav, wav)
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
