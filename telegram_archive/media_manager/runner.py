"""Runs the supplied unified Telegram Media Manager inside the web app.

telegram_media_manager.py exposes a small host API - configure() and
run_workflow() - and this adapter uses only that API:

* login     -> a client that reuses the app's logged-in Telegram account
  (no second login, no second session file)
* input/print -> the browser console of the Telegram Media Manager
* state_dir -> state/media_manager/ (resumable between runs)
* on_empty_scan -> explains why a mover found nothing
* its progress output  -> ONE job in Active Jobs (completed / total / current
  operation); its internal stages never become separate jobs.

The workflow runs in its own thread and event loop, so its blocking prompts
and heavy thumbnail/FFmpeg work never freeze the web server.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Module helpers
#   - MediaManagerRunner
#       - Status
#       - Console bridge
#       - Module wiring
#       - Controls and job entry
# ------------------------------------------------------------------
from __future__ import annotations

from ..telegram.client_policy import configure_telethon_client, make_telegram_client, FLOOD_SLEEP_THRESHOLD_SECONDS, REQUEST_RETRIES
import asyncio
import importlib
import json
import queue
import re
import threading
import time
from collections import deque

from .. import config

STATE_DIR = config.MEDIA_MANAGER_STATE_DIR

MODES = {
    "TRANSFER": (
        "Transfer job",
        "choose VIDEO/FILE source and destination, orientation, source-channel count, then process one queued item at a time",
    ),
    "DUPLICATE": (
        "Duplicate job",
        "scan a channel for duplicate pairs/items and move the older copy to trash after verification",
    ),
    "CLEANUP": (
        "Channel cleanup",
        "repair videos missing a companion thumbnail or metadata caption by creating a fresh VIDEO + thumbnail pair",
    ),
    "AUTO": (
        "Smart Auto — all Telegram workflows",
        "legacy combined router, repair, and duplicate workflow",
    ),
    # Backward-compatible expert modes.
    "1": ("Content mover", "VIDEO/FILE source to VIDEO/FILE destination"),
    "2": ("Vertical / mobile mover", "portrait videos only"),
    "3": ("Channel organizer", "repair missing thumbnails, move orphan images to trash"),
    "4": ("Duplicate finder", "move older duplicates to trash"),
    "5": ("Smart router", "streamable / vertical / missing-thumbnail / file routing"),
}

_PROGRESS_RE = re.compile(r"^\s*(?:PROCESS\s+|\[(?:ORPHAN\s+)?)(\d[\d,]*)\s*/\s*(\d[\d,]*)")
_PREFETCH_RE = re.compile(r"^\s*\[PREFETCH\]\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)")
_DECOR_RE = re.compile(r"^[\s=\-#_*~]*$")




# ==============================================================================
# SECTION: Module helpers
# Job mode resolution and the stop signal.
# ==============================================================================

def resolve_job_mode(job_type: str, detail: dict | None) -> str:
    """Resolve the workflow mode for current and legacy queued media-manager jobs."""
    detail = detail if isinstance(detail, dict) else {}
    mode = str(detail.get("mode") or "").strip().upper()
    if mode in MODES:
        return mode
    # Telegram transfer jobs were historically queued by the Transfer page
    # without a mode field. Make those durable jobs restart-safe.
    if not mode and str(job_type or "").strip().upper() == "TELEGRAM_TRANSFER":
        return "TRANSFER"
    raise RuntimeError("Choose a Telegram Media Manager workflow")


class MediaManagerStopped(BaseException):
    """Raised inside the workflow when the operator stops it (BaseException so
    the module's broad `except Exception` handlers do not swallow it)."""




# ==============================================================================
# SECTION: MediaManagerRunner
# Hosts the Media Manager inside the web app.
# ==============================================================================

class MediaManagerRunner:

    # ------------------------------------------------------------------
    # SUBSECTION: Status
    # Runner state and snapshots.
    # ------------------------------------------------------------------
    """Runs the Media Manager on its own thread and bridges console I/O to the UI."""
    def __init__(self):
        self.lock = threading.RLock()
        self.lines: deque = deque(maxlen=4000)
        self.seq = 0
        self.live = ""
        self._partial = ""
        self.prompt = ""
        self.answers: queue.Queue = queue.Queue()
        self.thread = None
        self.loop = None
        self.task = None
        self.client = None
        self.stop_requested = False
        # Thread-safe transfer control. Clear => pause at the next safe boundary;
        # set => transfer may continue. The workflow never interrupts an in-flight
        # Telegram send/copy; it pauses between items/stages.
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.resume_after_restart = False
        self.job_id = None
        self.last_job_id = None
        self.mode = None
        self.last_mode = None
        self.error = None
        self.completed = None
        self.total = None
        self.operation = ""
        self.app_client = None
        self.db = None
        self.job_manager = None
        self.manager_loop = None
        self._module = None
        self._last_log = 0.0
        self.preflight = None
        self.last_heartbeat = time.monotonic()
        self.heartbeat_count = 0

    # ------------------------------------------------------------ status

    def heartbeat(self, *, progress: bool = False):
        with self.lock:
            self.last_heartbeat = time.monotonic()
            self.heartbeat_count += 1

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            lines = [{"n": n, "t": t} for n, t in self.lines if n > since]
            return {
                "running": self.running, "job_id": self.job_id, "last_job_id": self.last_job_id,
                "mode": self.mode, "last_mode": self.last_mode,
                "mode_name": MODES.get(self.mode or self.last_mode or "", ("", ""))[0],
                "prompt": self.prompt, "live": self.live, "seq": self.seq,
                "lines": lines[-1500:], "error": self.error,
                "completed": self.completed, "total": self.total, "operation": self.operation,
                "last_heartbeat_age": round(max(0.0, time.monotonic() - self.last_heartbeat), 2),
                "heartbeat_count": self.heartbeat_count,
            }

    # ------------------------------------------------------------ console bridge

    def _commit(self, line: str):
        self.heartbeat(progress=True)
        if "\r" in line:
            line = line.split("\r")[-1]
        line = line.rstrip()
        self.seq += 1
        self.lines.append((self.seq, line))
        m = _PROGRESS_RE.match(line) or _PREFETCH_RE.match(line)
        if m:
            idx = int(m.group(1).replace(",", ""))
            tot = int(m.group(2).replace(",", ""))
            if tot > 0:
                done = idx if ("already" in line.lower() or "PREFETCH" in line) else idx - 1
                self.completed, self.total = max(0, min(done, tot)), tot
        stripped = line.strip()
        if stripped and not _DECOR_RE.match(stripped):
            self.operation = stripped[:160]
            low = stripped.lower()
            if self.db is not None and (low.startswith("error") or "failed" in low[:40] or low.startswith("timeout")):
                now = time.monotonic()
                if now - self._last_log > 2:
                    self._last_log = now
                    zero_failures = bool(__import__("re").search(r"\bfailed\s*:\s*0\b", low))
                    self.db.log(f"Telegram Media Manager: {stripped[:300]}", "INFO" if zero_failures else "WARN")


    # ------------------------------------------------------------------
    # SUBSECTION: Console bridge
    # Maps print and input to the web UI.
    # ------------------------------------------------------------------
    def _print(self, *args, sep=" ", end="\n", file=None, flush=False):
        text = sep.join(str(a) for a in args) + (end if end is not None else "\n")
        with self.lock:
            buf = self._partial + text
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                self._commit(line)
            self._partial = buf
            self.live = buf.split("\r")[-1].strip() if buf else ""

    def _input(self, prompt: str = "") -> str:
        self.heartbeat()
        if self.stop_requested:
            raise MediaManagerStopped()
        with self.lock:
            text = self._partial + str(prompt)
            self._partial = ""
            self.live = ""
            self.prompt = text.strip() or "Enter a value"
        while True:
            try:
                answer = self.answers.get(timeout=0.5)
            except queue.Empty:
                if self.stop_requested:
                    raise MediaManagerStopped()
                self.heartbeat()
                continue
            if answer is None:
                raise MediaManagerStopped()
            with self.lock:
                self.prompt = ""
                self._commit(f"{text.rstrip()} {answer}")
            return answer

    def answer(self, text: str) -> bool:
        if not self.running or not self.prompt:
            return False
        self.answers.put(str(text))
        return True

    # ------------------------------------------------------------ module wiring


    # ------------------------------------------------------------------
    # SUBSECTION: Module wiring
    # Loads the module, logs in and runs a mode.
    # ------------------------------------------------------------------
    def _load_module(self):
        """Import the Media Manager and connect it to the app through its
        documented configure() API (no functions are swapped at run time)."""
        if self._module is None:
            self._module = importlib.import_module(".telegram_media_manager", __package__)
        mod = self._module
        # Share the application catalogue with Media Manager so VIDEO moves
        # can perform metadata/API identification and build the correct caption.
        mod.MEDIA_MANAGER_DB = self.db
        # Bridge download progress into this existing job's live operation row.
        # No separate UI or progress tracker is created.
        mod.MEDIA_MANAGER_RUNNER = self
        # Large Telegram download telemetry is written back against the
        # existing durable job, matching upload progress and completion events.
        mod.TRANSFER_JOB_ID = str(self.job_id or "").strip() or None
        from ..core import media_deps
        pre = media_deps.preflight()
        self.preflight = pre
        # The module calls "ffmpeg"/"ffprobe" by name: give it the same FFmpeg
        # the rest of the app found (winget installs are often not on PATH).
        import os
        for tool in ("ffmpeg", "ffprobe"):
            path = pre[tool]["path"]
            if path:
                folder = os.path.dirname(path)
                if folder and folder not in os.environ.get("PATH", "").split(os.pathsep):
                    os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        pillow = None
        if pre["pillow"]["ok"]:
            from PIL import Image, ImageDraw, ImageFont
            pillow = (Image, ImageDraw, ImageFont)
        reason = None
        if not pre["thumbnails_ok"]:
            missing = [x for x, k in (("PyAV", "av"), ("Pillow", "pillow")) if not pre[k]["ok"]]
            reason = (f"thumbnail generation unavailable: {' and '.join(missing)} not available/healthy in this Python "
                      f"({'; '.join(pre[k]['error'] for k in ('av', 'pillow') if not pre[k]['ok'])}). "
                      f"Fix: {pre['install_command']}")
        startup_errors = [] if pre["telethon"]["ok"] else [
            "Telethon cannot be imported: " + pre["telethon"]["error"] + ". Fix: " + pre["install_command"]]
        mod.configure(
            input_fn=self._input, print_fn=self._print, state_dir=STATE_DIR,
            login=self._login, thumbnails_unavailable_reason=reason,
            on_empty_scan=self._diagnose_source, startup_errors=startup_errors, pillow=pillow,
            pause_event=self.pause_event,
        )
        return mod

    async def _login(self, _config=None):
        """Reuse the app's authorized account: copy its session keys into an
        in-memory session (no second login, no locked session file)."""
        from telethon import TelegramClient
        from telethon.sessions import MemorySession
        base_client = self.app_client
        if base_client is None:
            raise RuntimeError("Telegram is not connected in the app")
        base = base_client.session
        api_id = getattr(base_client, "api_id", None) or getattr(base_client, "_api_id", None)
        api_hash = getattr(base_client, "api_hash", None) or getattr(base_client, "_api_hash", None)
        if not api_id or not api_hash or base.auth_key is None:
            raise RuntimeError("Telegram login is not complete in the app")
        session = MemorySession()
        session.set_dc(int(base.dc_id), base.server_address, int(base.port))
        session.auth_key = base.auth_key
        client = make_telegram_client(session, int(api_id), api_hash, governor=getattr(self.app_client, "_telegram_archive_governor", None),
                                timeout=30, connection_retries=10, retry_delay=5, auto_reconnect=True,
                                request_retries=REQUEST_RETRIES, flood_sleep_threshold=FLOOD_SLEEP_THRESHOLD_SECONDS)
        await self._module.connect_with_retry(client, label="Telegram connection", require_authorized=True)
        self.client = client
        return client

    async def _diagnose_source(self, client, source, source_mode, any_orientation):
        """Log a sample of source messages to explain empty scans."""
        mod = self._module
        from ..engines.media_utils import is_video_message, is_image_message
        p = self._print
        p("")
        p("SOURCE DIAGNOSTICS (why nothing was selected)")
        title = getattr(source, "title", None) or getattr(source, "username", None) or ""
        p(f"  Channel            : {title} (id {getattr(source, 'id', '?')})")
        counts = {"messages": 0, "native": 0, "files": 0, "images": 0, "other": 0, "no_dims": 0, "portrait": 0}
        limit = 50000
        try:
            async for m in client.iter_messages(source, limit=limit):
                counts["messages"] += 1
                if getattr(m, "video", None):
                    counts["native"] += 1
                elif is_video_message(m):
                    counts["files"] += 1
                elif is_image_message(m):
                    counts["images"] += 1
                elif getattr(m, "media", None):
                    counts["other"] += 1
                else:
                    continue
                if getattr(m, "video", None) or is_video_message(m):
                    w, h, *_ = mod.get_video_dimensions(m)
                    if not w or not h:
                        counts["no_dims"] += 1
                    elif h > w:
                        counts["portrait"] += 1
        except Exception as exc:
            p(f"  Could not read the channel: {type(exc).__name__}: {exc}")
            return
        more = " (first 50,000 messages)" if counts["messages"] >= limit else ""
        p(f"  Messages read      : {counts['messages']:,}{more}")
        p(f"  Native videos      : {counts['native']:,}")
        p(f"  Videos sent as file: {counts['files']:,}")
        p(f"  Images             : {counts['images']:,}")
        p(f"  Other media        : {counts['other']:,}")
        p(f"  Videos without width/height: {counts['no_dims']:,}   portrait: {counts['portrait']:,}")
        if counts["messages"] == 0:
            p("  RESULT: Telegram returned 0 messages - the channel is empty or this account cannot read it.")
        elif counts["native"] + counts["files"] == 0:
            p("  RESULT: the channel has messages but 0 videos.")
        elif source_mode == "VIDEO" and counts["native"] == 0:
            p("  RESULT: all videos are sent as FILES. Start again and choose SOURCE type 2 (FILE).")
        elif source_mode == "FILE" and counts["files"] == 0:
            p("  RESULT: all videos are native Telegram videos. Start again and choose SOURCE type 1 (VIDEO).")
        elif not any_orientation:
            p("  RESULT: videos exist, but none is portrait (or their size is unknown). "
              "Use the Content mover for videos of any orientation.")
        else:
            p("  RESULT: videos exist but none matched the selected source type; check the choice above.")
        if self.db is not None:
            self.db.log(f"Telegram Media Manager: source {title}: {counts['native']} native videos, "
                        f"{counts['files']} videos as files, 0 selected ({source_mode})", "WARN")

    async def _run_mode(self, mod, mode: str, job_detail=None):
        await mod.run_workflow(
            mode,
            job_id=self.job_id,
            resume_job_id=self.job_id if self.resume_after_restart else None,
            job_detail=job_detail,
            job_manager=self.job_manager,
            manager_loop=self.manager_loop,
        )

    def _thread_main(self, mode: str):
        """Thread body: own an event loop, run the chosen mode and clean up."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            mod = self._load_module()
            from ..core.media_deps import summary_lines
            for line in summary_lines(self.preflight):
                self._print(line)
            if not self.preflight["av"]["ok"]:
                self._print("  NOTE: PyAV is unavailable; thumbnail generation will use the FFmpeg fallback.")
                if self.db is not None and self.preflight["ffmpeg"]["ok"]:
                    self.db.log("Telegram Media Manager: PyAV unavailable — using FFmpeg thumbnail fallback", "INFO")
            self._print("")
            if mod.STARTUP_IMPORT_ERRORS:
                raise RuntimeError("; ".join(mod.STARTUP_IMPORT_ERRORS))
            self.task = loop.create_task(self._run_mode(mod, mode, job_detail=getattr(self, "job_detail", None)))
            loop.run_until_complete(self.task)
        except (asyncio.CancelledError, MediaManagerStopped):
            self._print("\nStopped.")
        except BaseException as exc:   # report, never crash the app
            self.error = f"{type(exc).__name__}: {exc}"
            self._print(f"\nERROR: {self.error}")
        finally:
            if self._module is not None:
                try:
                    self._module.TRANSFER_JOB_ID = None
                    self._module.MEDIA_MANAGER_RUNNER = None
                except Exception:
                    pass
            with self.lock:
                self.job_id = None
                self.mode = None
            try:
                if self.client is not None:
                    loop.run_until_complete(asyncio.wait_for(self.client.disconnect(), 10))
            except BaseException:
                pass
            self.client = None
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except BaseException:
                pass
            loop.close()
            with self.lock:
                if self._partial:
                    self._commit(self._partial)
                    self._partial = ""
                self.prompt = ""
                self.live = ""

    # ------------------------------------------------------------ job entry


    # ------------------------------------------------------------------
    # SUBSECTION: Controls and job entry
    # Pause, resume, stop and run_job.
    # ------------------------------------------------------------------
    def pause(self):
        self.pause_event.clear()

    def resume(self):
        self.pause_event.set()

    def stop(self):
        self.stop_requested = True
        self.pause_event.set()
        self.answers.put(None)
        loop, task = self.loop, self.task
        if loop is not None and task is not None and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    async def run_job(self, ctx):
        """JobManager handler for MEDIA_MANAGER jobs (one job per run)."""
        from ..jobs.context import JobCancelled
        if self.running:
            raise RuntimeError("Telegram Media Manager is already running")
        try:
            mode_detail = json.loads(ctx.job.get("detail") or "{}")
        except Exception:
            mode_detail = {}
        mode = resolve_job_mode(str(ctx.job.get("job_type") or ctx.job.get("type") or ""), mode_detail)
        if not ctx.telegram.connected or ctx.telegram.client is None:
            raise RuntimeError("Telegram is not connected")
        with self.lock:
            self.lines.clear()
            self.seq = 0
            self._partial = self.live = self.prompt = self.operation = ""
            self.completed = self.total = None
            self.error = None
        while not self.answers.empty():
            self.answers.get_nowait()
        self.stop_requested = False
        self.pause_event.set()
        self.resume_after_restart = False
        try:
            detail = json.loads(ctx.job.get("detail") or "{}")
            self.resume_after_restart = bool(detail.get("resume_after_restart"))
        except Exception:
            self.resume_after_restart = False
        self.app_client = ctx.telegram.client
        self.db = ctx.db
        self.job_manager = ctx._manager
        self.manager_loop = asyncio.get_running_loop()
        self.job_id = ctx.job_id
        self.last_job_id = ctx.job_id
        self.mode = mode
        self.last_mode = mode
        try:
            self.job_detail = json.loads(ctx.job.get("detail") or "{}")
        except Exception:
            self.job_detail = {}
        self.loop = self.task = None
        name = MODES[mode][0]
        ctx.report(progress=0, operation=f"Starting {name}", force=True)
        self.thread = threading.Thread(target=self._thread_main, args=(mode,), daemon=True,
                                       name="telegram-media-manager")
        self.thread.start()
        last = None
        try:
            while self.thread.is_alive():
                await asyncio.sleep(0.5)
                self.heartbeat()
                await ctx.checkpoint()
                with self.lock:
                    op = f"Waiting for your answer: {self.prompt}" if self.prompt else (self.live or self.operation)
                    snap = (self.completed, self.total, op[:200])
                if snap != last:
                    last = snap
                    ctx.report(completed=snap[0], total=snap[1], operation=snap[2] or name,
                               progress=None if snap[1] else 0)
            # The worker thread is already finished. Do not run another full
            # checkpoint here: a playback/resource gate could otherwise delay
            # publishing completion even though the transfer itself is done.
            if ctx.cancel_requested():
                self.stop()
                raise JobCancelled()
        except JobCancelled:
            self.stop()
            for _ in range(60):
                if not self.thread.is_alive():
                    break
                await asyncio.sleep(0.5)
            raise
        if self.error:
            raise RuntimeError(self.error)
        # Publish completion before returning to JobManager. This closes the
        # small UI/API race where the runner had already finished but the
        # persisted job still briefly appeared active, preventing the next
        # Telegram transfer from being started immediately.
        ctx.report(progress=100, operation="Completed", force=True)


RUNNER = MediaManagerRunner()
