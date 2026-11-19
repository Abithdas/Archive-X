"""Local-only llama.cpp runtime integration for Archive X AI.

The application talks only to a loopback local llama.cpp chat server.
No cloud endpoint or API credential is accepted by this module.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .. import config

DEFAULT_MODEL_ID = "Qwen3-4B-Instruct-2507-Q4_K_M"
DEFAULT_MODEL_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
DEFAULT_MODEL_REPO = "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_CONTEXT = 8192
START_TIMEOUT_S = 240          # a 2 to 5 GB model can take a while to load from a slow disk
IDLE_MINUTES = 20              # the server is stopped after this long unused, to give the memory back
DEFAULT_MAX_TOKENS = 900


class LocalAIError(RuntimeError):
    """The local AI runtime failed or is unavailable."""
    pass


def _clean_path(value: str | Path | None) -> Path:
    return Path(str(value or "").strip()).expanduser().resolve()


def _valid_model_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._:-]{2,120}", str(value or "").strip()))


class LocalAIRuntime:
    """Owns configuration, optional llama-server process, and local inference."""

    def __init__(self, db):
        self.db = db
        self._process: subprocess.Popen | None = None
        self._started_at = 0.0
        self._reach_cache = None
        self._last_used = time.monotonic()
        self._watch: threading.Thread | None = None

    def touch(self) -> None:
        """Note that the model was just used (it is stopped when left alone for a while)."""
        self._last_used = time.monotonic()

    @property
    def idle_minutes(self) -> int:
        try:
            return max(0, min(240, int(self.db.setting("ai_idle_minutes", IDLE_MINUTES))))
        except Exception:
            return IDLE_MINUTES

    @property
    def log_path(self) -> Path:
        return Path(config.AI_DIR) / "llama-server.log"

    def log_tail(self, lines: int = 12) -> str:
        try:
            return "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    @property
    def endpoint(self) -> str:
        value = str(self.db.setting("ai_local_endpoint", f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1") or "").strip()
        if not value:
            value = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1"
        if not self._is_loopback_url(value):
            return f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1"
        return value.rstrip("/")

    @property
    def runtime_path(self) -> Path | None:
        value = os.getenv("ARCHIVE_X_LLAMA_SERVER") or self.db.setting(
            "ai_local_runtime_path", str(config.AI_DIR / "runtime" / ("llama-server.exe" if os.name == "nt" else "llama-server"))
        )
        return _clean_path(value) if value else None

    @property
    def model_path(self) -> Path:
        value = os.getenv("ARCHIVE_X_AI_MODEL_PATH") or self.db.setting(
            "ai_local_model_path", str(config.AI_MODELS_DIR / DEFAULT_MODEL_FILE)
        )
        return _clean_path(value)

    @property
    def model_id(self) -> str:
        value = os.getenv("ARCHIVE_X_AI_MODEL_ID") or self.db.setting("ai_local_model_id", DEFAULT_MODEL_ID)
        value = str(value or DEFAULT_MODEL_ID).strip()
        return value if _valid_model_id(value) else DEFAULT_MODEL_ID

    @property
    def context_size(self) -> int:
        try:
            return max(1024, min(16384, int(self.db.setting("ai_local_context", DEFAULT_CONTEXT))))
        except Exception:
            return DEFAULT_CONTEXT

    @property
    def thinking(self) -> bool:
        """Whether the model may reason before answering (slower; the reasoning is then shown in the chat as it is written)."""
        return str(self.db.setting("ai_local_thinking", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}

    @property
    def gpu_layers(self) -> int:
        try:
            return max(0, min(128, int(self.db.setting("ai_local_gpu_layers", 0))))
        except Exception:
            return 0

    @staticmethod
    def _is_loopback_url(value: str) -> bool:
        try:
            from urllib.parse import urlparse
            p = urlparse(value)
            if p.scheme not in {"http", "https"}:
                return False
            host = (p.hostname or "").lower()
            return host in {"127.0.0.1", "localhost", "::1"}
        except Exception:
            return False

    def configure(self, *, runtime_path: str | None = None, model_path: str | None = None,
                  model_id: str | None = None, endpoint: str | None = None,
                  context: int | None = None, gpu_layers: int | None = None, thinking: bool | None = None) -> dict[str, Any]:
        if runtime_path is not None:
            value = str(runtime_path or "").strip()
            self.db.set_setting("ai_local_runtime_path", value)
        if model_path is not None:
            value = str(model_path or "").strip()
            self.db.set_setting("ai_local_model_path", value)
        if model_id is not None:
            value = str(model_id or "").strip()
            if not _valid_model_id(value):
                raise ValueError("Invalid local AI model id")
            self.db.set_setting("ai_local_model_id", value)
        if endpoint is not None:
            value = str(endpoint or "").strip().rstrip("/")
            if not self._is_loopback_url(value):
                raise ValueError("Local AI endpoint must use localhost, 127.0.0.1, or ::1")
            self.db.set_setting("ai_local_endpoint", value)
        if context is not None:
            value = max(1024, min(16384, int(context)))
            self.db.set_setting("ai_local_context", value)
        if gpu_layers is not None:
            value = max(0, min(128, int(gpu_layers)))
            self.db.set_setting("ai_local_gpu_layers", value)
        if thinking is not None:
            self.db.set_setting("ai_local_thinking", "1" if thinking else "0")
        self._reach_cache = None
        return self.status(fresh=True)

    def _request(self, method: str, path: str, payload: dict | None = None, timeout: float = 8.0) -> dict[str, Any]:
        url = self.endpoint + (path if path.startswith("/") else "/" + path)
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method.upper(), headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer no-key-required",
            "User-Agent": "Archive-X-Local-AI/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise LocalAIError("Local AI response exceeded 8 MiB")
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(4000).decode("utf-8", "replace")
            except Exception:
                body = ""
            raise LocalAIError(f"Local AI HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise LocalAIError(f"Local AI connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LocalAIError("Local AI request timed out") from exc
        except json.JSONDecodeError as exc:
            raise LocalAIError(f"Local AI returned invalid JSON: {exc}") from exc

    # Asking the model server "are you there?" when it is not running is slow on Windows (a refused connection to this PC can take a second or two
    # to give up), and the AI page asks on every visit. A recent answer is reused for a few seconds, and a first look does not wait the full time;
    # anything that starts, stops or configures the server asks afresh.
    _REACH_TTL = 5.0
    _reach_cache: tuple[float, tuple[bool, dict[str, Any] | None, str | None]] | None = None

    def _reachable(self, fresh: bool = False) -> tuple[bool, dict[str, Any] | None, str | None]:
        cached = self._reach_cache
        if not fresh and cached and time.monotonic() - cached[0] < self._REACH_TTL:
            return cached[1]
        try:
            result = self._request("GET", "/models", timeout=2.5 if fresh else 1.0)
            model = None
            data = result.get("data") if isinstance(result, dict) else None
            if isinstance(data, list) and data:
                model = data[0] if isinstance(data[0], dict) else None
            answer: tuple[bool, dict[str, Any] | None, str | None] = (True, model, None)
        except Exception as exc:
            answer = (False, None, str(exc))
        self._reach_cache = (time.monotonic(), answer)
        return answer

    def status(self, fresh: bool = False) -> dict[str, Any]:
        """Report runtime and model files, process state and reachability."""
        runtime = self.runtime_path
        model = self.model_path
        reachable, loaded_model, error = self._reachable(fresh=fresh)
        process_alive = bool(self._process and self._process.poll() is None)
        if self._process and not process_alive:
            self._process = None
        return {
            "local_only": True,
            "runtime": {
                "name": "llama.cpp / llama-server",
                "path": str(runtime) if runtime else "",
                "exists": bool(runtime and runtime.is_file()),
                "process_owned": process_alive,
                "pid": self._process.pid if process_alive else None,
                "started_at": self._started_at or None,
            },
            "model": {
                "id": self.model_id,
                "path": str(model),
                "exists": model.is_file(),
                "size_bytes": model.stat().st_size if model.is_file() else 0,
                "recommended_repo": DEFAULT_MODEL_REPO,
                "quantization": "Q4_K_M",
                "license": "Apache-2.0 (base Qwen3 model; verify quantizer metadata before redistribution)",
                "loaded": reachable,
                "loaded_id": (loaded_model or {}).get("id") if loaded_model else None,
            },
            "server": {
                "endpoint": self.endpoint,
                "reachable": reachable,
                "error": error,
                "context": self.context_size,
                "gpu_layers": self.gpu_layers,
                "thinking": self.thinking,
                "starting": bool(process_alive and not reachable),
                "idle_minutes": self.idle_minutes,
            },
            "hardware": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "cpu": platform.processor() or "unknown",
                "hostname": socket.gethostname(),
                "inference_mode": "CPU-first; GPU layers are optional",
            },
            "ready": bool(reachable and model.is_file()),
        }

    def start(self, on_wait=None) -> dict[str, Any]:
        """Start llama-server and wait until the model is loaded. `on_wait(seconds)` is called about twice a second while waiting."""
        state = self.status(fresh=True)
        if state["server"]["reachable"]:
            self.touch()
            return state
        runtime = self.runtime_path
        model = self.model_path
        if runtime is None or not runtime.is_file():
            raise LocalAIError("The assistant engine is not set up yet")
        if not model.is_file():
            raise LocalAIError("The assistant model is not set up yet")
        config.AI_DIR.mkdir(parents=True, exist_ok=True)
        self._use_a_free_port()
        args = [
            str(runtime), "-m", str(model), "--host", DEFAULT_HOST,
            "--port", str(self._port()), "--alias", self.model_id,
            "-c", str(self.context_size), "-ngl", str(self.gpu_layers), "--jinja",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            log = self.log_path.open("wb")
        except OSError:
            log = None
        self._process = subprocess.Popen(
            args, cwd=str(runtime.parent), stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags,
        )
        if log:
            log.close()   # the child keeps its own handle
        self._started_at = time.time()
        deadline = time.monotonic() + START_TIMEOUT_S
        began = time.monotonic()
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                code = self._process.returncode
                self._process = None
                tail = self.log_tail(6)
                raise LocalAIError(f"The assistant engine stopped while starting (exit code {code})." + (f" Its last words: {tail}" if tail else ""))
            reachable, _, _ = self._reachable(fresh=True)
            if reachable:
                self.touch()
                self._watch_idle()
                return self.status(fresh=True)
            if on_wait:
                on_wait(int(time.monotonic() - began))
            time.sleep(0.5)
        self.stop()
        raise LocalAIError(f"The assistant did not finish loading within {START_TIMEOUT_S // 60} minutes.")

    def _watch_idle(self) -> None:
        """Stop the server we started once it has been left alone for `idle_minutes`, so a model is not holding gigabytes of memory all day."""
        if self._watch and self._watch.is_alive():
            return

        def loop() -> None:
            while True:
                time.sleep(30)
                proc = self._process
                if proc is None or proc.poll() is not None:
                    return
                limit = self.idle_minutes * 60
                if limit and time.monotonic() - self._last_used > limit:
                    self.stop()
                    return

        self._watch = threading.Thread(target=loop, name="ai-idle-stop", daemon=True)
        self._watch.start()

    def _use_a_free_port(self) -> None:
        """Something else may already own the port (8080 is qBittorrent's usual one, and then the engine cannot start). Another free port is
        chosen and kept as the endpoint, so the next start uses it too. This runs only when nothing of ours answers there."""
        port = self._port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            try:
                probe.bind((DEFAULT_HOST, port))
                return
            except OSError:
                pass
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as pick:
            pick.bind((DEFAULT_HOST, 0))
            free = int(pick.getsockname()[1])
        self.db.set_setting("ai_local_endpoint", f"http://{DEFAULT_HOST}:{free}/v1")
        self._reach_cache = None

    def _port(self) -> int:
        try:
            from urllib.parse import urlparse
            port = urlparse(self.endpoint).port
            return int(port or DEFAULT_PORT)
        except Exception:
            return DEFAULT_PORT

    def stop(self) -> dict[str, Any]:
        proc = self._process
        self._process = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    _swallowed_note("local_ai_runtime.LocalAIRuntime.stop")
                    pass
        self._started_at = 0.0
        self._reach_cache = None
        return self.status(fresh=True)

    def test(self) -> dict[str, Any]:
        started = time.monotonic()
        response = self._request("POST", "/chat/completions", {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": "You are Archive X local AI. Answer briefly."},
                {"role": "user", "content": "Reply with exactly: Archive X local AI ready"},
            ],
            "max_tokens": 32,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }, timeout=120.0)
        text = ""
        try:
            text = str(response["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            _swallowed_note("local_ai_runtime.LocalAIRuntime.test")
            pass
        return {"ok": True, "text": text, "model": str(response.get("model") or self.model_id), "latency_ms": round((time.monotonic() - started) * 1000, 1)}

    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
             response_format: dict[str, Any] | None = None, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max(64, min(4096, int(max_tokens))),
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        payload["chat_template_kwargs"] = {"enable_thinking": False}   # models that can think at length (Qwen3 8B) answer straight away; others ignore this
        self.touch()
        return self._request("POST", "/chat/completions", payload, timeout=300.0)

    def chat_stream(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None, max_tokens: int = DEFAULT_MAX_TOKENS, plain: bool = False):
        """Like `chat`, but yields the server's streamed chunks (parsed JSON) as they arrive, so words can be shown while the rest is still being written."""
        payload: dict[str, Any] = {
            "model": self.model_id, "messages": messages, "max_tokens": max(64, min(4096, int(max_tokens))), "temperature": 0.2, "stream": True,
        }
        if not plain:     # `plain` leaves out the optional template switch, for a server build that refuses it
            payload["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        req = urllib.request.Request(self.endpoint + "/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json", "Accept": "text/event-stream", "Authorization": "Bearer no-key-required", "User-Agent": "Archive-X-Local-AI/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=300.0) as resp:
                for raw in resp:
                    self.touch()
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(2000).decode("utf-8", "replace")
            except Exception:
                body = ""
            raise LocalAIError(f"Local AI HTTP {exc.code}: {body[:600]}") from exc
        except urllib.error.URLError as exc:
            raise LocalAIError(f"Local AI connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LocalAIError("Local AI request timed out") from exc

    def close(self) -> None:
        self.stop()
