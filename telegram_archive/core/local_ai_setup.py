"""One-click setup of the local assistant: download the llama.cpp engine and a chat model, check both, and point the assistant at them.

What is downloaded is pinned: one llama.cpp release, and one GGUF file per model size, each with the SHA-256 it must have. A file that does not
match is deleted, never used. Downloads resume if they are cut off. Nothing here talks to any service except github.com (the engine) and
huggingface.co (the model), and only when the person presses "Set up".
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import tarfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import config

ENGINE_TAG = "b12400"
_RELEASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{ENGINE_TAG}/"
CONTEXT_TOKENS = 8192
# With every layer on a graphics card the weights, the context memory and the working buffers all have to fit in its own memory. The 4B model is
# about 2.4 GB and its context memory grows with the window (about 0.6 GB at 4,096 tokens, about 1.2 GB at 8,192), so a 4 GB card gets the shorter
# window; a bigger card can raise it under Models, Advanced.
GPU_CONTEXT_TOKENS = 4096


@dataclass(frozen=True)
class Engine:
    key: str
    file: str
    sha256: str
    size_mb: int
    label: str
    exe: str
    gpu_layers: int

    @property
    def url(self) -> str:
        return _RELEASE + self.file


ENGINES: dict[str, Engine] = {
    "win-cpu": Engine("win-cpu", f"llama-{ENGINE_TAG}-bin-win-cpu-x64.zip", "080810522a80ab61025ffd4e85a876a506cba67009dcbe8f8056fd4b9b2e43d8", 24, "Processor", "llama-server.exe", 0),
    "win-gpu": Engine("win-gpu", f"llama-{ENGINE_TAG}-bin-win-vulkan-x64.zip", "0574451721e7084bec76ceef65d28c351bc6fc19ca395efd4394c046e25a1f4a", 37, "Graphics card (Vulkan)", "llama-server.exe", 99),
    "linux-cpu": Engine("linux-cpu", f"llama-{ENGINE_TAG}-bin-ubuntu-x64.tar.gz", "2fc619d367f6a6bed8a0d2436c25d33eab1b60167ccef4906147d7d3a6ad3020", 23, "Processor", "llama-server", 0),
}


@dataclass(frozen=True)
class Model:
    key: str
    label: str
    model_id: str
    file: str
    url: str
    sha256: str
    size_mb: int
    min_ram_gb: int
    blurb: str


MODELS: dict[str, Model] = {
    "small": Model(
        "small", "Small", "Qwen3-1.7B-Q4_K_M", "Qwen_Qwen3-1.7B-Q4_K_M.gguf",
        "https://huggingface.co/bartowski/Qwen_Qwen3-1.7B-GGUF/resolve/main/Qwen_Qwen3-1.7B-Q4_K_M.gguf",
        "72c5c3cb38fa32d5256e2fe30d03e7a64c6c79e668ad84057e3bd66e250b24fb", 1223, 4,
        "Quick and light. Fine for short questions; it follows the Archive X tools less reliably.",
    ),
    "balanced": Model(
        "balanced", "Balanced", "Qwen3-4B-Instruct-2507-Q4_K_M", "Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "2fde00ce69dd4899c70d020845e2638353015bba0fdf161b3eb965f2bca4464e", 2381, 8,
        "The recommended one: good at using the Archive X tools, and answers in a few seconds on a modern processor.",
    ),
    "strong": Model(
        "strong", "Strong", "Qwen3-8B-Q4_K_M", "Qwen_Qwen3-8B-Q4_K_M.gguf",
        "https://huggingface.co/bartowski/Qwen_Qwen3-8B-GGUF/resolve/main/Qwen_Qwen3-8B-Q4_K_M.gguf",
        "54fffa050078e984116639c83dfb64b5aa6d4cd474e018b076777c632bbccccd", 4794, 16,
        "Best answers, and the slowest. Wants 16 GB of memory or more, and is much nicer with a graphics card.",
    ),
}

RECOMMENDED = "balanced"
STEPS = [("engine", "Download the engine"), ("model", "Download the model"), ("check", "Check the files"), ("start", "Start the assistant"), ("test", "Ask it a test question")]


class Cancelled(Exception):
    pass


def supported_engines() -> list[Engine]:
    machine = platform.machine().lower()
    if os.name == "nt":
        return [ENGINES["win-cpu"], ENGINES["win-gpu"]] if machine in {"amd64", "x86_64"} else []
    if platform.system() == "Linux" and machine in {"x86_64", "amd64"}:
        return [ENGINES["linux-cpu"]]
    return []


def memory_gb() -> float:
    """Installed memory in GB; 0 when it cannot be read."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return round(st.ullTotalPhys / 1024**3, 1)
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024**2, 1)
    except Exception:
        pass
    return 0.0


def recommend(ram_gb: float) -> str:
    """Balanced unless the PC has less than about 7 GB (a PC sold as 7 reports a bit less); the person can pick any size."""
    return "small" if 0 < ram_gb < 6.3 else RECOMMENDED


def options(ram_gb: float) -> list[dict[str, Any]]:
    rec = recommend(ram_gb)
    out = []
    for m in MODELS.values():
        out.append({"id": m.key, "label": m.label, "model": m.model_id, "size_mb": m.size_mb, "min_ram_gb": m.min_ram_gb, "blurb": m.blurb,
                    "recommended": m.key == rec, "fits": ram_gb == 0 or ram_gb >= m.min_ram_gb * 0.9})
    return out


def runtime_dir() -> Path:
    return Path(config.AI_DIR) / "runtime"


def _sha(path: Path, on_chunk: Callable[[int], None] | None = None, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
            if on_chunk:
                on_chunk(len(chunk))
    return h.hexdigest()


def download(url: str, dest: Path, *, sha256: str, size_hint: int, on_bytes: Callable[[int, int], None], cancel: threading.Event, opener: Callable[..., Any] | None = None) -> None:
    """Fetch `url` into `dest`, resuming a `.part` file if one is there, and keep it only if its SHA-256 is `sha256`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    opener = opener or urllib.request.urlopen
    have = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": "ArchiveX-local-ai-setup/1.0"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with opener(req, timeout=60) as resp:
        status = getattr(resp, "status", 200)
        if have and status != 206:
            have = 0   # the server ignored the range: start again
        total = have + int(resp.headers.get("Content-Length") or 0) if status in (200, 206) else size_hint
        with part.open("ab" if have else "wb") as out:
            done = have
            on_bytes(done, total or size_hint)
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                on_bytes(done, total or size_hint)
    if _sha(part, cancel=cancel) != sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{dest.name} did not match its checksum, so it was deleted. Try again.")
    part.replace(dest)


def _unpack(archive: Path, into: Path, exe: str) -> None:
    """Unpack the engine archive flat into `into`, refusing any path that would land outside it."""
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()

    def target(name: str) -> Path | None:
        parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
        if archive.suffix == ".gz" and parts:
            parts = parts[1:]      # the tar has one top folder
        if not parts:
            return None
        t = (into / Path(*parts)).resolve()
        if root != t and root not in t.parents:
            raise RuntimeError("The engine archive contained an unsafe path.")
        return t

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                t = target(info.filename)
                if t is None or info.is_dir():
                    continue
                t.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, t.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                t = target(member.name)
                if t is None or not (member.isfile() or member.issym()):
                    continue
                if member.issym():
                    if t.is_symlink() or t.exists():
                        t.unlink()
                    link = member.linkname
                    if "/" in link or ".." in link:
                        raise RuntimeError("The engine archive contained an unsafe link.")
                    t.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(link, t)
                    continue
                t.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, t.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                t.chmod(0o755 if member.mode & 0o111 else 0o644)
    if not (into / exe).is_file():
        raise RuntimeError(f"The engine archive did not contain {exe}.")


class Setup:
    """The state of one setup run, readable at any moment (the page polls it) and changed by `run`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._reset("idle")

    def _reset(self, state: str, model: str = "", engine: str = "") -> None:
        self._state: dict[str, Any] = {
            "state": state, "model": model, "engine": engine, "steps": [{"id": i, "label": l, "state": "pending", "detail": ""} for i, l in STEPS],
            "bytes_done": 0, "bytes_total": 0, "speed_bps": 0, "log": [], "error": "", "started_at": 0.0,
        }
        self._rate: tuple[float, int] = (0.0, 0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            s = dict(self._state)
            s["steps"] = [dict(x) for x in self._state["steps"]]
            s["log"] = list(self._state["log"])
            return s

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _step(self, step_id: str, state: str, detail: str = "") -> None:
        with self._lock:
            for s in self._state["steps"]:
                if s["id"] == step_id:
                    s["state"], s["detail"] = state, detail

    def _log(self, line: str) -> None:
        with self._lock:
            self._state["log"].append(line)
            del self._state["log"][:-40]

    def _bytes(self, done: int, total: int) -> None:
        now = time.monotonic()
        with self._lock:
            t0, b0 = self._rate
            if now - t0 >= 1.0:
                self._state["speed_bps"] = int(max(0, done - b0) / (now - t0)) if t0 else 0
                self._rate = (now, done)
            self._state["bytes_done"], self._state["bytes_total"] = done, total

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, db, ai, model_key: str, engine_key: str, *, opener: Callable[..., Any] | None = None) -> bool:
        """Begin in the background. False when one is already running."""
        with self._lock:
            if self.running:
                return False
        if model_key not in MODELS:
            raise ValueError("Unknown model size")
        engines = {e.key: e for e in supported_engines()}
        if engine_key not in engines:
            raise ValueError("That engine is not available on this computer")
        self._cancel = threading.Event()
        self._reset("running", model_key, engine_key)
        self._state["started_at"] = time.time()
        self._thread = threading.Thread(target=self.run, args=(db, ai, MODELS[model_key], engines[engine_key], opener), name="ai-setup", daemon=True)
        self._thread.start()
        return True

    def run(self, db, ai, model: Model, engine: Engine, opener: Callable[..., Any] | None = None) -> None:
        cancel = self._cancel
        current = "engine"
        try:
            rt = runtime_dir()
            marker = rt / "ENGINE"
            exe = rt / engine.exe
            self._step("engine", "active", f"{engine.label}, about {engine.size_mb} MB")
            if exe.is_file() and marker.is_file() and marker.read_text().strip() == f"{ENGINE_TAG} {engine.key}":
                self._log("The engine is already here.")
                self._step("engine", "done", "Already installed")
            else:
                self._log(f"Downloading llama.cpp {ENGINE_TAG} ({engine.label.lower()})…")
                archive = rt / engine.file
                download(engine.url, archive, sha256=engine.sha256, size_hint=engine.size_mb << 20, on_bytes=self._bytes, cancel=cancel, opener=opener)
                self._log("Unpacking the engine…")
                _unpack(archive, rt, engine.exe)
                marker.write_text(f"{ENGINE_TAG} {engine.key}\n")
                archive.unlink(missing_ok=True)
                self._step("engine", "done", f"llama.cpp {ENGINE_TAG}")
            self._bytes(0, 0)
            current = "model"
            dest = Path(config.AI_MODELS_DIR) / model.file
            self._step("model", "active", f"{model.label}, about {model.size_mb / 1000:.1f} GB")
            if dest.is_file() and self._verified(dest, model.sha256, cancel):
                self._log("The model is already here and checks out.")
            else:
                self._log(f"Downloading {model.model_id}…")
                download(model.url, dest, sha256=model.sha256, size_hint=model.size_mb << 20, on_bytes=self._bytes, cancel=cancel, opener=opener)
            self._step("model", "done", model.model_id)
            current = "check"
            self._step("check", "active")
            if not exe.is_file() or not dest.is_file():
                raise RuntimeError("A file is missing after the download.")
            self._log("Both files match their checksums.")
            self._step("check", "done", "Checksums match")
            ai.configure(runtime_path=str(exe), model_path=str(dest), model_id=model.model_id, context=GPU_CONTEXT_TOKENS if engine.gpu_layers else CONTEXT_TOKENS, gpu_layers=engine.gpu_layers)
            current = "start"
            self._step("start", "active", "Loading the model into memory (the first time can take a minute)")
            self._log("Starting the assistant…")
            ai.start()
            self._step("start", "done", "Running")
            current = "test"
            self._step("test", "active")
            result = ai.test()
            self._log(f"It answered in {result.get('latency_ms')} ms.")
            self._step("test", "done", f"Answered in {result.get('latency_ms')} ms")
            with self._lock:
                self._state["state"] = "done"
            db.log(f"Local assistant set up ({model.model_id}, {engine.key})", "SUCCESS", event="ai.setup.completed")
        except Cancelled:
            self._step(current, "error", "Stopped")
            self._log("Stopped. What was downloaded is kept, so the next try carries on from there.")
            with self._lock:
                self._state["state"] = "cancelled"
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"[:400] if not isinstance(exc, RuntimeError) else str(exc)[:400]
            self._step(current, "error", msg)
            self._log(f"Failed: {msg}")
            with self._lock:
                self._state["state"] = "error"
                self._state["error"] = msg
            db.log(f"Local assistant setup failed at '{current}': {msg}", "WARN", event="ai.setup.failed")

    def _verified(self, path: Path, sha256: str, cancel: threading.Event) -> bool:
        self._log("Checking the model that is already here…")
        try:
            return _sha(path, cancel=cancel) == sha256
        except OSError:
            return False


SETUP = Setup()
