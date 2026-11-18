"""Local file uploader engine.

Ported from telegram_smart_uploader_updated.py.
Scans a local folder for videos, FFprobes each, encodes if needed,
generates a thumbnail, and uploads VIDEO + THUMBNAIL to Telegram.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Discovery and captions
#   - FFmpeg preparation
#   - Pipeline isolation
#   - Delivery commit and catalogue
#   - Main engine entry point
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import gc
import html
import json
import os
import secrets
import stat
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
import shutil
from typing import Optional

from ..core.upload_limits import UploadLimitExceeded, get_upload_policy
from ..core.workspace_guard import validate_local_upload_folder
from ..jobs.context import JobCancelled, JobContext, JobSkipped
from .media_utils import (
    flood_wait_seconds, is_flood_wait, human_size,
    is_telegram_direct_streamable_meta, verify_destination_pair, is_video_message
)
from .telegram_sender import (
    LocalVideoCoverError, send_local_video_with_cover, render_caption,
    LOCAL_UPLOAD_DEFAULT_TEMPLATE,
)
from .transfer_progress import RollingTransferProgress, format_size

LOCAL_UPLOAD_CAPTION_SETTING = "local_upload_caption_template"
LEGACY_CAPTION_SETTING = "send_caption_template"




# ==============================================================================
# SECTION: Discovery and captions
# Finds upload candidates and builds captions.
# ==============================================================================


VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".m4p", ".mov", ".qt", ".mkv", ".mk3d", ".avi",
    ".divx", ".wmv", ".asf", ".flv", ".f4v", ".f4p", ".mpg",
    ".mpeg", ".mpe", ".mpv", ".m2v", ".mpeg1", ".mpeg2", ".webm",
    ".ts", ".mts", ".m2ts", ".m2t", ".3gp", ".3g2", ".vob",
    ".ogv", ".ogx", ".rm", ".rmvb", ".amv", ".mxf", ".roq",
    ".nsv", ".yuv",
}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024   # 4 GiB (Telegram Premium limit)


# ---- helpers ported verbatim from telegram_smart_uploader_updated.py ----

WORK_DIR_NAME = ".archivex_upload_work"
# A file changed within this many seconds may still be copying/downloading.
STABLE_AFTER_SECONDS = 60


def _is_app_artifact(path: Path, folder: Path) -> bool:
    """Files this app creates itself must never be picked up as new videos."""
    try:
        rel_parts = path.relative_to(folder).parts
    except ValueError:
        rel_parts = path.parts
    if any(part.startswith(".") for part in rel_parts):
        return True          # hidden folders incl. the work folder, tombstones
    stem = path.stem.lower()
    return stem.endswith("_upload") or "._telegram_archive_" in path.name.lower()


def find_videos(folder: Path, *, include_subfolders: bool = False) -> list[Path]:
    """Return supported videos from the selected folder.

    By default only files directly inside the selected folder are considered.
    Recursive scanning is opt-in via ``include_subfolders``.
    """
    iterator = folder.rglob("*") if include_subfolders else folder.iterdir()
    return sorted(
        p for p in iterator
        if p.suffix.lower() in VIDEO_EXTENSIONS
        and p.is_file()
        and not _is_app_artifact(p, folder)
    )


def is_still_changing(path: Path, *, now: float | None = None) -> bool:
    """True while a file is probably still being copied into the folder.
    Uploading it now would send a truncated video."""
    try:
        age = (now or time.time()) - path.stat().st_mtime
    except OSError:
        return True
    return age < STABLE_AFTER_SECONDS

async def _latest_destination_message_id(client, destination) -> int:
    """Capture a Telegram history watermark before an idempotent local upload."""
    recent = await client.get_messages(destination, limit=1)
    return max(
        (int(getattr(message, "id", 0) or 0) for message in (recent or [])),
        default=0,
    )


async def _publish_checkpointed_contact_sheet(
    ctx, client, destination, snapshot_path: Path, *, file_name: str,
    video_key: str, pending: dict, pending_delivery: dict, state: dict,
):
    """Persist a stable request identity before publishing the companion image."""
    pending = dict(pending or {})
    request_id = int(pending.get("thumbnail_random_id") or secrets.randbits(63) or 1)
    baseline = pending.get("thumbnail_baseline_message_id")
    if baseline is None:
        baseline = await _latest_destination_message_id(client, destination)
    baseline = int(baseline or 0)
    pending.update({
        "stage": "thumbnail_send_started",
        "thumbnail_random_id": request_id,
        "thumbnail_baseline_message_id": baseline,
    })
    pending_delivery[video_key] = pending
    state["pending_delivery"] = dict(pending_delivery)
    ctx.save_state(state)

    try:
        from .delivery_pipeline import publish_snapshot
        return await ctx.cancellable(lambda: publish_snapshot(
            client, destination, snapshot_path, file_name=file_name,
            message_random_id=request_id, baseline_message_id=baseline,
        ))
    except (JobCancelled, JobSkipped):
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Companion-image upload failed for {Path(file_name).name}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc




async def _delete_verified_source_file(path: Path, log=None, *, attempts: int = 20, delay: float = 0.5) -> bool:
    """Delete a verified local source file robustly, including on Windows.

    The caller must complete destination verification before invoking this
    function. We rename to a same-directory tombstone before deleting so a
    locked file cannot be mistaken for a successful removal. If cleanup fails,
    the tombstone is restored to its original name and the function returns
    False. Success is returned only after both paths are confirmed absent.
    """
    target = Path(path)
    tombstone: Path | None = None
    try:
        if not target.exists():
            return True
        if not target.is_file():
            if log:
                log(f"Source cleanup refused because the target is not a file: {target}", "WARN")
            return False
    except OSError as exc:
        if log:
            log(f"Source cleanup could not inspect {target}: {type(exc).__name__}: {exc}", "WARN")
        return False

    def _gone(p: Path | None) -> bool:
        try:
            return p is None or not p.exists()
        except Exception:
            return False

    def _powershell_delete(live: Path) -> None:
        # Passing a path after `powershell -Command <string>` does not reliably
        # populate $args[0] on Windows PowerShell. Use an environment variable
        # instead so spaces, Unicode and punctuation in paths are preserved.
        env = os.environ.copy()
        env["AX_DELETE_TARGET"] = str(live)
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", "Remove-Item -LiteralPath $env:AX_DELETE_TARGET -Force -ErrorAction Stop"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=True, timeout=8,
        )

    max_attempts = max(1, int(attempts))
    last_error = None
    powershell_attempts = {1, 4, 10, max_attempts}
    for attempt in range(1, max_attempts + 1):
        try:
            gc.collect()
            live = tombstone if (tombstone is not None and tombstone.exists()) else target
            if os.name == "nt":
                try:
                    os.chmod(live, stat.S_IWRITE | stat.S_IREAD)
                except OSError as exc:
                    last_error = exc
            if live is target and target.exists():
                try:
                    candidate = target.with_name(f".{target.name}.telegram_delete_{uuid.uuid4().hex}")
                    os.replace(str(target), str(candidate))
                    tombstone = live = candidate
                except OSError as exc:
                    last_error = exc
            try:
                os.unlink(str(live))
            except FileNotFoundError:
                pass
            except OSError as exc:
                last_error = exc
                if os.name == "nt" and attempt in powershell_attempts and live.exists():
                    try:
                        await asyncio.to_thread(_powershell_delete, live)
                    except Exception as ps_exc:
                        last_error = ps_exc
            if _gone(target) and _gone(tombstone):
                return True
        except Exception as exc:
            last_error = exc
        if attempt < max_attempts:
            await asyncio.sleep(float(delay))

    # Never leave a failed deletion hidden behind the temporary filename.
    if tombstone is not None and tombstone.exists() and not target.exists():
        try:
            os.replace(str(tombstone), str(target))
        except OSError as exc:
            last_error = exc
    success = _gone(target) and _gone(tombstone)
    if log:
        try:
            detail = str(last_error) if last_error else "file remained after deletion attempts"
            log(
                f"Upload Local Files: source deletion {'completed' if success else 'FAILED'} for {target}; "
                f"original_exists={target.exists()}, temporary_exists={not _gone(tombstone)}, "
                f"attempts={max_attempts}, last_error={detail}",
                "INFO" if success else "WARN",
            )
        except Exception:
            _swallowed_note("uploader._delete_verified_source_file")
            pass
    return success

def delivered_filename(video: Path) -> str:
    """Name Telegram receives: anything not already MP4/M4V is converted or remuxed to .mp4."""
    video = Path(video)
    return video.name if video.suffix.lower() in (".mp4", ".m4v") else video.stem + ".mp4"

def _fix_library_extension(db, channel, message_id, delivered_name: str) -> None:
    """The delivery ledger keeps an existing Library filename, so a row created earlier still says .ts.
    Correct a pure extension difference (same name, converted container) so captions show the real file name.
    A name the user edited (different stem) is left alone."""
    with db.lock:
        row = db.conn.execute(
            "SELECT id,filename FROM media WHERE source_channel=? AND source_message_id=?",
            (str(channel), int(message_id)),
        ).fetchone()
        if not row:
            return
        current = str(row["filename"] or "")
        if current != delivered_name and Path(current).stem == Path(delivered_name).stem:
            db.conn.execute("UPDATE media SET filename=? WHERE id=?", (delivered_name, row["id"]))
            db.conn.commit()


async def build_local_upload_caption(db, video: Path, meta: dict, *, oshash_value: str | None = None,
                                     media_id: int | None = None) -> str:
    """Compatibility entry point routed through the canonical local-media caption service."""
    from .telegram_sender import build_local_media_caption
    return await build_local_media_caption(
        db, video, meta, oshash_value=oshash_value, media_id=media_id,
        display_filename=delivered_filename(video),
        template_setting=LOCAL_UPLOAD_CAPTION_SETTING,
        default_template=LOCAL_UPLOAD_DEFAULT_TEMPLATE, limit=4096,
    )




# ==============================================================================
# SECTION: FFmpeg preparation
# Probing, scaling and video preparation.
# ==============================================================================

def probe(ffprobe: str | None, path: Path) -> dict:
    """Probe media with layered fallbacks and actionable errors.

    A transient Windows file-handle/AV-scan race should not turn into a silent
    success. We retry the probe briefly, then expose ffprobe stderr so the
    caller can persist the real reason and keep the source file untouched.
    """
    last_error: Exception | None = None
    # PyAV/internal media is preferred because it already powers the archive's
    # other media flows and avoids shelling out for files it can read directly.
    for attempt in range(3):
        try:
            from ..core import internal_media
            m = internal_media.probe(path)
            return {"width": m["width"], "height": m["height"], "duration": m["duration"],
                    "codec": m["video_codec"], "video_codec": m["video_codec"],
                    "audio_codec": m["audio_codec"], "format_name": m.get("format_name", ""), "bitrate": 0}
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))

    if not ffprobe:
        raise RuntimeError(f"Media probe failed for {path.name}: {last_error}") from last_error

    last_ffprobe_error = ""
    for attempt in range(3):
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                capture_output=True, text=True, check=True
            )
            data = json.loads(r.stdout or "{}")
            vst = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            ast = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
            if not vst:
                raise RuntimeError("ffprobe found no video stream")
            return {"width": int(vst.get("width") or 0), "height": int(vst.get("height") or 0),
                    "duration": float(data.get("format", {}).get("duration") or 0),
                    "codec": vst.get("codec_name", ""), "video_codec": vst.get("codec_name", ""),
                    "audio_codec": ast.get("codec_name", ""),
                    "format_name": data.get("format", {}).get("format_name", ""),
                    "bitrate": int(data.get("format", {}).get("bit_rate") or 0)}
        except subprocess.CalledProcessError as exc:
            last_ffprobe_error = (exc.stderr or exc.stdout or "").strip()
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
                continue
            break
        except Exception as exc:
            last_ffprobe_error = str(exc)
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
                continue
            break

    detail = last_ffprobe_error or str(last_error) or "unknown probe error"
    raise RuntimeError(f"Media probe failed for {path.name}: {detail}") from last_error


def display_rotation(path: Path) -> int:
    """Rotation (degrees) a player applies when showing this file, e.g. 90 for phone portrait video.

    Phones usually store portrait video as landscape pixels plus a rotation flag.
    PyAV exposes the display matrix on decoded frames, so decode the first frame
    only. Falls back to ffprobe side data; 0 when unknown.
    """
    try:
        from ..core.pyav_support import import_pyav
        av = import_pyav()
        with av.open(str(path)) as c:
            for frame in c.decode(video=0):
                return int(getattr(frame, "rotation", 0) or 0) % 360
    except Exception:
        _swallowed_note("uploader.display_rotation")
        pass
    try:
        ffmpeg, ffprobe = ffmpeg_tools()
        if ffprobe:
            r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream_side_data=rotation:stream_tags=rotate", "-of", "json", str(path)],
                               capture_output=True, text=True, timeout=30)
            data = json.loads(r.stdout or "{}")
            st = (data.get("streams") or [{}])[0]
            for sd in st.get("side_data_list") or []:
                if "rotation" in sd:
                    return int(float(sd["rotation"])) % 360
            if (st.get("tags") or {}).get("rotate"):
                return int(float(st["tags"]["rotate"])) % 360
    except Exception:
        _swallowed_note("uploader.display_rotation")
        pass
    return 0


def display_orientation(width, height, rotation=0) -> str:
    """HORIZONTAL/VERTICAL as viewers see it (same rule as Telegram-to-Telegram), or UNKNOWN."""
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if w <= 0 or h <= 0:
        return "UNKNOWN"
    if int(rotation or 0) % 180 == 90:
        w, h = h, w
    return "VERTICAL" if h > w else "HORIZONTAL"


def scale_even(width: int, height: int, target_height: int) -> tuple[int, int]:
    if not width or not height:
        raise RuntimeError("video has no usable width/height; cannot encode it")
    ratio = target_height / height
    w = int(width * ratio)
    h = target_height
    return (w + w % 2), (h + h % 2)


def ffmpeg_tools() -> tuple[Optional[str], Optional[str]]:
    """Same FFmpeg lookup as the player and Media Manager (also finds winget
    installs that are not on this console's PATH)."""
    from ..playback.media_tools import find_ffmpeg, find_ffprobe
    return find_ffmpeg(), find_ffprobe()


def prepare_video(src: Path, ffmpeg: str, ffprobe: str) -> Optional[Path]:
    """Encode or remux to a streamable MP4 if needed."""
    meta = probe(ffprobe, src)
    is_h264 = meta["codec"].lower() in ("h264", "avc")
    is_mp4 = src.suffix.lower() in (".mp4", ".m4v")
    if is_h264 and is_mp4:
        # Just add faststart
        out = src.with_name(src.stem + "_upload.mp4")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src),
                        "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
        return out
    out = src.with_name(src.stem + "_upload.mp4")
    w, h = scale_even(meta["width"], meta["height"], min(meta["height"], 1080))
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-i", str(src),
        "-c:v", "libx264", "-crf", "23", "-preset", "medium",
        "-vf", f"scale={w}:{h}", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(out),
    ], check=True)
    return out



async def prepare_video_async(ctx: JobContext, src: Path, ffmpeg: str, ffprobe: str, item_index: int, total: int,
                              work_dir: Optional[Path] = None) -> Optional[Path]:
    """Prepare a streamable MP4 while reporting real FFmpeg progress."""
    try:
        meta = await ctx.blocking(probe, ffprobe, src)
    except Exception as exc:
        # Preserve the real probe error; do not let the engine fall through to
        # a job-level success merely because preparation returned no output.
        raise RuntimeError(f"Media preparation probe failed: {exc}") from exc

    duration = max(float(meta.get("duration") or 0.0), 0.0)
    direct_streamable = is_telegram_direct_streamable_meta(meta, filename=src.name)
    if direct_streamable:
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required to place the MP4 index at the start before uploading")
        if work_dir is not None:
            work_dir.mkdir(parents=True, exist_ok=True)
            out = work_dir / (src.stem + "_faststart.mp4")
        else:
            out = src.with_name(src.stem + "_faststart.mp4")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
               "-map", "0", "-c", "copy", "-movflags", "+faststart", str(out)]
        def _remux_faststart():
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "FFmpeg faststart remux failed")[-1200:])
            if not out.is_file() or out.stat().st_size <= 0:
                raise RuntimeError("FFmpeg faststart remux produced no output")
            return out
        try:
            out = await ctx.blocking(_remux_faststart)
        except Exception as exc:
            raise RuntimeError(f"Could not faststart-remux {src.name}: {exc}") from exc
        ctx.report(completed=item_index, total=total, current_item=src.name,
                   operation=f"Remuxed with +faststart (stream copy) — {src.name}",
                   progress=(100.0 * item_index / max(total, 1)), force=True)
        return out

    if not ffmpeg:
        raise RuntimeError("FFmpeg is required only for sources that Telegram cannot stream directly")
    # The converted copy is written to a separate work folder (deleted after
    # each file). It used to sit next to the source as "<name>_upload.mp4",
    # was never removed, and the next scan uploaded it again as a new video.
    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / (src.stem + "_upload.mp4")
    else:
        out = src.with_name(src.stem + "_upload.mp4")
    w, h = scale_even(int(meta.get("width") or 0), int(meta.get("height") or 0), min(int(meta.get("height") or 1080), 1080))
    from ..playback.media_tools import best_h264_encoder, _encoder_args
    encoder = await best_h264_encoder()
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src),
           *_encoder_args(encoder, h, float(meta.get("fps") or 30.0)),
           "-vf", f"scale={w}:{h}", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(out)]
    stage = "Encoding"

    ctx.report(completed=item_index, total=total, current_item=src.name,
               operation=f"{stage} {src.name} (0%)", progress=(100.0 * item_index / max(total, 1)), force=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    assert proc.stdout is not None
    assert proc.stderr is not None
    stderr_task = asyncio.create_task(proc.stderr.read())
    last_pct = -1
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "ignore").strip()
            if text.startswith("out_time_ms="):
                try:
                    out_ms = int(text.split("=", 1)[1])
                    pct = (out_ms / 1_000_000.0 / duration * 100.0) if duration > 0 else 0.0
                    pct = max(0.0, min(100.0, pct))
                    ipct = int(pct)
                    if ipct != last_pct:
                        last_pct = ipct
                        overall = 100.0 * (item_index + pct / 100.0) / max(total, 1)
                        ctx.report(completed=item_index, total=total, current_item=src.name,
                                   operation=f"{stage} {src.name} ({ipct}%)", progress=overall, force=True)
                except Exception:
                    _swallowed_note("uploader.prepare_video_async")
                    pass
        rc = await proc.wait()
        stderr_text = (await stderr_task).decode("utf-8", "ignore").strip()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise

    if rc != 0 or not out.exists():
        raise RuntimeError(stderr_text or f"FFmpeg failed with exit code {rc}")

    ctx.report(completed=item_index, total=total, current_item=src.name,
               operation=f"{stage} complete", progress=(100.0 * (item_index + 1) / max(total, 1)), force=True)
    return out


# ---- pipeline isolation ---------------------------------------------------




# ==============================================================================
# SECTION: Pipeline isolation
# Filters candidates so pipelines do not overlap.
# ==============================================================================

def _norm_source_path(path: Path) -> str:
    try:
        return str(path.resolve()).replace('\\', '/').casefold()
    except Exception:
        return str(path.absolute()).replace('\\', '/').casefold()


def _automation_owned_paths(db) -> set[str]:
    """Return local paths currently/previously owned by Content Automation.

    Local Upload is intentionally kept separate from Content Automation. A
    folder may be shared by the operator, so ownership is determined from the
    automation_items table rather than from the folder name alone.
    """
    try:
        with db.lock:
            rows = db.conn.execute(
                "SELECT local_path FROM automation_items WHERE local_path IS NOT NULL AND TRIM(local_path) <> ''"
            ).fetchall()
        return {_norm_source_path(Path(str(row[0]))) for row in rows if row and row[0]}
    except Exception:
        # If the automation table is unavailable, do not block Local Upload.
        return set()


def _sort_local_upload_candidates(videos: list[Path], manifest: dict[str, dict]) -> list[Path]:
    """Return the upload queue in deterministic smallest-file-first order."""
    return sorted(
        videos,
        key=lambda p: (
            int((manifest.get(str(p)) or {}).get("size") or 0),
            p.name.casefold(),
            str(p).casefold(),
        ),
    )


async def _prefetch_local_upload_manifest(
    ctx: JobContext,
    videos: list[Path],
    ffprobe: str | None,
    state: dict,
    *,
    total: int,
    resolution_filter: str = "ANY",
    orientation: str = "BOTH",
) -> dict[str, dict]:
    """Snapshot local files and prefetch upload metadata before the first upload.

    The manifest is intentionally persisted before any Telegram upload starts.
    It records size/order plus media probe, original OSHASH and the provider-backed
    caption when available. Metadata failures are non-fatal; the upload path can
    fall back to its normal on-demand lookup. A small concurrency limit keeps
    local CPU/metadata APIs from competing with the actual Telegram transfer.
    """
    from ..core.resolution_filter import matches_resolution_filter, normalize_resolution_filter

    resolution_filter = normalize_resolution_filter(resolution_filter)
    manifest = dict(state.get("file_manifest") or {})
    semaphore = asyncio.Semaphore(2)
    started = time.monotonic()

    async def one(video: Path):
        key = str(video)
        try:
            st = await asyncio.to_thread(video.stat)
            size = int(st.st_size)
        except Exception as exc:
            manifest[key] = {"path": key, "name": video.name, "size": 0, "error": f"stat: {exc}"}
            return
        entry = dict(manifest.get(key) or {})
        entry.update({
            "path": key, "name": video.name, "size": size,
            "size_human": human_size(size), "mtime": float(st.st_mtime),
            "metadata_status": entry.get("metadata_status", "PENDING"),
        })
        try:
            async with semaphore:
                meta = await ctx.blocking(probe, ffprobe, video)
                oshash = None
                if matches_resolution_filter(meta.get("width"), meta.get("height"), resolution_filter):
                    try:
                        from ..metadata.fingerprint import oshash_file
                        oshash = await ctx.blocking(oshash_file, key)
                    except Exception as exc:
                        entry["oshash_error"] = str(exc)
                else:
                    entry["oshash_skipped"] = "source_resolution_filter"
                caption = ""  # Filled from the persisted Metadata & Assets record before any upload starts.
            entry["probe"] = {k: meta.get(k) for k in ("width", "height", "duration", "codec", "video_codec", "audio_codec", "format_name", "bitrate")}
            if str(orientation or "BOTH").upper() != "BOTH":
                entry["probe"]["rotation"] = await ctx.blocking(display_rotation, video)
            entry["oshash"] = str(oshash or "")
            entry["caption"] = caption
            entry["metadata_status"] = "READY"
        except Exception as exc:
            entry["metadata_status"] = "PARTIAL"
            entry["metadata_error"] = f"{type(exc).__name__}: {exc}"
        manifest[key] = entry
        state["file_manifest"] = manifest
        ctx.save_state(state)

    await asyncio.gather(*(one(v) for v in videos))
    elapsed = max(time.monotonic() - started, 0.001)
    state["file_manifest"] = manifest
    state["manifest_summary"] = {
        "files": len(videos),
        "total_bytes": sum(int((manifest.get(str(v)) or {}).get("size") or 0) for v in videos),
        "prefetched": sum(1 for v in videos if (manifest.get(str(v)) or {}).get("metadata_status") == "READY"),
        "elapsed_seconds": round(elapsed, 3),
        "order": "size_ascending",
    }
    ctx.save_state(state)
    return manifest




# ==============================================================================
# SECTION: Delivery commit and catalogue
# Asset readiness and verified delivery commit.
# ==============================================================================

def _require_local_upload_assets_ready(db, media_id: int, video_path: Path) -> dict:
    """Require only the technical assets needed to send and register a local video.

    Provider scene matches and performer/studio links are intentionally not
    prerequisites for Local Upload. The actual Telegram location is recorded
    after delivery verification.
    """
    from ..core.resource_registry import ensure_resource_tables
    from .thumbnail_cache import local_playback_snapshot_frames_path
    from .preview_prefetch import FRAME_COUNT, frame_quality_sync

    path = Path(video_path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Local Upload source is missing or empty: {path.name}")
    ensure_resource_tables(db)
    with db.lock:
        row = db.conn.execute(
            "SELECT m.id,m.source_channel,m.filename,rs.catalogue_state,rs.snapshot_state,"
            "(SELECT ar.path FROM asset_registry ar WHERE ar.media_id=m.id "
            "AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY' "
            "ORDER BY ar.updated_at DESC LIMIT 1) representative_path "
            "FROM media m LEFT JOIN resource_state rs ON rs.media_id=m.id WHERE m.id=? LIMIT 1",
            (int(media_id),),
        ).fetchone()
    if not row:
        raise RuntimeError(f"Local Upload staging record {media_id} is missing; refusing to send.")
    details = dict(row)
    missing = []
    if str(details.get("source_channel") or "") != "local_upload":
        missing.append("local upload source record")
    if not str(details.get("filename") or "").strip():
        missing.append("saved filename")
    if str(details.get("catalogue_state") or "") != "COMPLETE":
        missing.append("local inventory record")
    if str(details.get("snapshot_state") or "") != "READY":
        missing.append("36-frame snapshots")
    representative = Path(str(details.get("representative_path") or "")) if details.get("representative_path") else None
    if representative is None or not representative.is_file() or representative.stat().st_size <= 0:
        missing.append("representative image")
    frames_dir = local_playback_snapshot_frames_path(path)
    quality = frame_quality_sync(frames_dir)
    if (not quality.get("ready") or int(quality.get("frame_count") or 0) != FRAME_COUNT
            or bool(quality.get("corrupt_frames"))):
        missing.append("36 validated snapshots")
    if missing:
        reason = ", ".join(dict.fromkeys(missing))
        try:
            db.log(f"Local Upload preparation is incomplete for media_id={media_id}: {reason}", "WARN")
        except Exception:
            _swallowed_note("uploader._require_local_upload_assets_ready")
            pass
        raise RuntimeError(f"Local Upload is waiting for {reason}; scene identification may finish later.")
    return details


async def _commit_verified_local_delivery(
    ctx: JobContext, *, destination, destination_channel: str, source_path: Path,
    upload_path: Path | None, prepared_media_id: int, source_message_id: int,
    source_oshash: str, video_id: int, thumb_id: int, video_meta: dict,
    caption: str, manifest_entry: dict | None = None,
    representative_path: Path | None = None, destination_message=None,
    split_delivery: dict | object | None = None,
) -> dict:
    """Idempotently finish Library/ledger/caption registration for a verified upload."""
    source_path = Path(source_path)
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        raise RuntimeError(f"Verified Telegram post is still pending registration, but its local source is missing: {source_path.name}")

    manifest = dict(manifest_entry or {})
    if not manifest:
        manifest = dict((ctx.load_state({}).get("file_manifest") or {}).get(str(source_path)) or {})
    prepared_media_id = int(prepared_media_id or manifest.get("media_id") or 0)
    source_oshash = str(source_oshash or manifest.get("oshash") or "").strip().lower()
    if not prepared_media_id and source_oshash:
        with ctx.db.lock:
            row = ctx.db.conn.execute(
                "SELECT id,source_message_id FROM media WHERE source_channel='local_upload' "
                "AND (lower(oshash)=? OR lower(identity_key)=?) ORDER BY id DESC LIMIT 1",
                (source_oshash, source_oshash),
            ).fetchone()
        if row:
            prepared_media_id = int(row["id"] or 0)
            source_message_id = int(source_message_id or row["source_message_id"] or 0)
    if not prepared_media_id:
        raise RuntimeError(f"Verified Telegram post for {source_path.name} has no local staging record to register.")

    if not source_message_id:
        with ctx.db.lock:
            source_row = ctx.db.conn.execute(
                "SELECT source_message_id FROM media WHERE id=? LIMIT 1", (prepared_media_id,)
            ).fetchone()
        source_message_id = int(source_row["source_message_id"] or 0) if source_row else 0

    _require_local_upload_assets_ready(ctx.db, prepared_media_id, source_path)
    signature = manifest.get("metadata_signature") if isinstance(manifest.get("metadata_signature"), dict) else {}
    if signature:
        try:
            stat_now = source_path.stat()
            if (int(signature.get("size") or 0) != int(stat_now.st_size)
                    or int(signature.get("mtime_ns") or 0) != int(stat_now.st_mtime_ns)):
                raise RuntimeError(f"Local source changed after delivery was prepared: {source_path.name}")
        except OSError as exc:
            raise RuntimeError(f"Could not verify the prepared local source {source_path.name}: {exc}") from exc

    meta = dict(video_meta or manifest.get("probe") or {})
    split_info = (split_delivery.to_dict() if hasattr(split_delivery, "to_dict")
                  else dict(split_delivery or {}))
    is_split = bool(split_info.get("parts"))
    delivered_path = Path(upload_path) if upload_path and Path(upload_path).is_file() else source_path
    message = destination_message
    if int(getattr(message, "id", 0) or 0) != int(video_id):
        try:
            message = await ctx.telegram.client.get_messages(destination, ids=int(video_id))
        except Exception:
            message = None
    remote_file = getattr(message, "file", None) if message is not None else None
    remote_video = getattr(message, "video", None) if message is not None else None
    if not meta.get("duration"):
        meta["duration"] = getattr(remote_file, "duration", 0) or 0
    if not meta.get("width"):
        meta["width"] = getattr(remote_video, "w", 0) or getattr(remote_file, "width", 0) or 0
    if not meta.get("height"):
        meta["height"] = getattr(remote_video, "h", 0) or getattr(remote_file, "height", 0) or 0
    meta.setdefault("audio_codec", "")

    rep = Path(representative_path) if representative_path else None
    if rep is None or not rep.is_file() or rep.stat().st_size <= 0:
        from .delivery_pipeline import prepare_local_delivery_assets
        rep, _contact_sheet = await prepare_local_delivery_assets(
            ctx.db, source_path, source_oshash=source_oshash or None, video_meta=meta, ctx=ctx,
        )
    final_caption = str(caption or manifest.get("caption") or "").strip()
    if not final_caption:
        final_caption = await build_local_upload_caption(
            ctx.db, source_path, meta, oshash_value=source_oshash or None,
            media_id=prepared_media_id,
        )

    from .thumbnail_cache import _store_thumb_sync
    await asyncio.to_thread(
        _store_thumb_sync, ctx.db, str(destination_channel), int(video_id), int(thumb_id),
        "image/jpeg", 0, 0, rep.read_bytes(), "playback-snapshot",
    )

    from .delivery_ledger import record_verified_delivery
    file_obj = getattr(message, "file", None) if message is not None else None
    delivered_size = int(split_info.get("total_size") or 0) if is_split else int(getattr(file_obj, "size", 0) or delivered_path.stat().st_size)
    if is_split:
        from ..core.split_files import store_split_group
        store_split_group(
            ctx.db, group_id=str(split_info.get("group_id") or ""), destination_channel=str(destination_channel),
            logical_filename=str(split_info.get("logical_filename") or source_path.name),
            primary_message_id=int(video_id), thumbnail_message_id=int(thumb_id),
            total_size=delivered_size, parts=list(split_info.get("parts") or []),
            metadata={**meta, "mime_type": str(split_info.get("mime_type") or "application/octet-stream"), "caption": final_caption},
            identity_key=source_oshash, status="PREPARING",
        )
    delivery_record = await asyncio.to_thread(
        record_verified_delivery, ctx.db,
        destination_channel=str(destination_channel), destination_video_message_id=int(video_id),
        destination_thumbnail_message_id=int(thumb_id),
        source_channel="local_upload", source_message_id=int(source_message_id or manifest.get("media_message_id") or 0),
        source_media_id=prepared_media_id, identity_key=source_oshash,
        filename=delivered_filename(source_path), file_path=delivered_path, file_size=delivered_size,
        duration=float(meta.get("duration") or 0), width=int(meta.get("width") or 0),
        height=int(meta.get("height") or 0), fps=float(meta.get("fps") or 0),
        bitrate=int(meta.get("bitrate") or 0),
        codec=str(meta.get("video_codec") or meta.get("codec") or ""),
        audio_codec=str(meta.get("audio_codec") or ""), caption=final_caption,
        action="UPLOAD_LOCAL",
    )
    if not is_split:
        await asyncio.to_thread(
            _fix_library_extension, ctx.db, str(destination_channel), int(video_id), delivered_filename(source_path),
        )
    try:   # a video from a local folder arrives with what is already known about it (title, cast, genres, the show it is), so nothing is identified twice
        from .local_upload_bridge import carry_identity
        await asyncio.to_thread(carry_identity, ctx.db, source_path, int(delivery_record.get("library_media_id") or 0))
    except Exception as exc:
        ctx.db.log(f"Could not carry the identification of {source_path.name} onto its Telegram copy: {type(exc).__name__}: {exc}", "WARN")

    try:
        if not is_split:
            from .delivery_pipeline import register_verified_playback_assets
            await register_verified_playback_assets(
                ctx.db, ctx.telegram.client, destination, str(destination_channel), int(video_id),
                source_channel="local_upload",
                source_message_id=int(source_message_id or manifest.get("media_message_id") or 0),
                file_path=delivered_path, original_oshash=source_oshash, video_meta=meta,
                destination_message=message,
            )
    except Exception as exc:
        ctx.db.log(
            f"Verified local upload playback-cache registration incomplete for {source_path.name}: "
            f"{type(exc).__name__}: {exc}", "WARN",
        )

    try:
        from ..playback.stream_server import make_record
        from ..core.telegram_registry import register_known_message, mark_prepared, advance_channel_high_water
        registry_record = make_record(message) if message else None
        if not registry_record:
            registry_record = {
                "id": int(video_id), "name": delivered_filename(source_path), "size": delivered_size,
                "duration": float(meta.get("duration") or 0), "width": int(meta.get("width") or 0),
                "height": int(meta.get("height") or 0), "mime": "video/mp4", "caption": final_caption,
            }
        registry_record["oshash"] = source_oshash
        registry_record["phash"] = str(meta.get("video_phash") or meta.get("phash") or "")
        library_media_id = int(delivery_record.get("library_media_id") or 0)
        register_known_message(
            ctx.db, channel_id=str(destination_channel), message_id=int(video_id), record=registry_record,
            media_id=library_media_id or None, thumbnail_message_id=int(thumb_id),
        )
        mark_prepared(
            ctx.db, channel_id=str(destination_channel), message_id=int(video_id),
            prepared={
                "source": "verified_local_upload", "media_id": library_media_id or None,
                "probe": dict(meta or {}), "uploaded_file": source_path.name,
                "prepared_assets": (["36_snapshots", "sprite", "contact_sheet", "probe_details", "split_file_group"] if is_split else ["36_snapshots", "sprite", "contact_sheet", "mp4_index", "keyframe_index", "probe_details"]),
                "contact_sheet_message_id": int(thumb_id),
                **({"split_group_id": str(split_info.get("group_id") or ""), "logical_filename": str(split_info.get("logical_filename") or source_path.name), "split_part_count": len(split_info.get("parts") or [])} if is_split else {}),
            }, oshash=source_oshash, phash=str(meta.get("video_phash") or meta.get("phash") or ""),
        )
        with ctx.db.lock:
            ctx.db.conn.execute(
                "INSERT OR IGNORE INTO channels(telegram_id,title,username,updated_at) VALUES(?,?,?,?)",
                (str(destination_channel),
                 str(getattr(destination, "title", None) or getattr(destination, "username", None) or f"Channel {destination_channel}"),
                 str(getattr(destination, "username", None) or ""),
                 __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()),
            )
            ctx.db.conn.commit()
        advance_channel_high_water(ctx.db, str(destination_channel), int(video_id), status="DELIVERY_REGISTERED")
    except Exception as exc:
        ctx.db.log(
            f"Verified local upload for {source_path.name} succeeded, but document registry reconciliation needs retry: "
            f"{type(exc).__name__}: {exc}", "WARN",
        )

    caption_sync = {"status": "not_started", "ok": False}
    library_media_id = int(delivery_record.get("library_media_id") or 0)
    if library_media_id:
        from .caption_sync import sync_verified_delivery_caption
        caption_sync = await sync_verified_delivery_caption(ctx.db, ctx.telegram, library_media_id)
        if not caption_sync.get("ok"):
            ctx.activity(
                f"Telegram caption needs retry for {source_path.name}",
                operation="Local Upload · caption sync needs retry", current_item=source_path.name,
                level="WARN", event="local_upload.caption_sync_failed",
                details={"media_id": library_media_id, "status": caption_sync.get("status"),
                         "detail": caption_sync.get("detail", "")[:500]}, progress=True,
            )
    if is_split:
        from ..core.split_files import finalize_split_group
        finalize_split_group(ctx.db, str(split_info.get("group_id") or ""), status="VERIFIED")
    return {"delivery": delivery_record, "caption_sync": caption_sync}


async def _prepare_local_upload_catalogue(
    ctx: JobContext,
    videos: list[Path],
    manifest: dict[str, dict],
    state: dict,
    failed: dict[str, str],
) -> dict[str, dict]:
    """Persist local inventory and verified snapshots before Local Upload.

    The result is a per-file map containing the persisted staging media ID and
    caption generated from that exact record. Scene/provider identification and
    performer/studio resolution are deferred to the destination channel scan.
    """
    if not videos:
        return manifest
    import hashlib as _hashlib
    from ..operations.automation import _wait_for_orchestrated_child

    media_items = []
    signature_parts = []
    for video in videos:
        entry = dict(manifest.get(str(video)) or {})
        try:
            stat = video.stat()
            signature = {"path": str(video.resolve(strict=False)), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
        except OSError as exc:
            raise RuntimeError(f"Cannot prepare local upload source {video}: {exc}") from exc
        oshash = str(entry.get("oshash") or "").strip()
        if not oshash:
            from ..metadata.fingerprint import oshash_file
            oshash = await ctx.blocking(oshash_file, str(video))
        entry["oshash"] = oshash
        entry["metadata_signature"] = signature
        manifest[str(video)] = entry
        media_items.append({
            "path": str(video), "filename": video.name, "oshash": oshash,
            "metadata": dict(entry.get("probe") or {}), "metadata_signature": signature,
        })
        signature_parts.append((str(video.resolve(strict=False)).casefold(), oshash.casefold(), signature["size"], signature["mtime_ns"]))
    prep_signature = _hashlib.sha256(json.dumps(signature_parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    child_id = str(state.get("metadata_assets_job_id") or "").strip()
    child = ctx.db.get_job(child_id) if child_id and str(state.get("metadata_assets_signature") or "") == prep_signature else None
    child_status = str((child or {}).get("status") or "").upper()
    if not child or child_status in {"FAILED", "CANCELLED", "INTERRUPTED", "COMPLETED_WITH_ERRORS"}:
        detail = {
            "workflow": "local_media", "source_namespace": "local_upload",
            "parent_job_id": ctx.job_id, "parent_automation_item": f"local-upload:{ctx.job_id}",
            "media_items": media_items, "force_metadata": False, "force_entity": False,
            "stages": ["inventory", "snapshots"],
        }
        child_id = ctx.db.create_job(
            "METADATA_CHANNEL_PIPELINE", "local_upload", str((ctx.job or {}).get("destination_channel") or (ctx.job or {}).get("channel_id") or ""), False,
            detail=json.dumps(detail, ensure_ascii=False, separators=(",", ":")), priority=35, max_retries=2,
        )
        # Persist the child identity/signature before submitting. A parent restart
        # can resume the same preparation job instead of duplicating provider work.
        state["metadata_assets_job_id"] = child_id
        state["metadata_assets_signature"] = prep_signature
        ctx.save_state(state)
    ctx.activity(
        f"Local inventory and snapshots queued for {len(videos)} upload(s)",
        operation="Local Upload · inventory and snapshots before transfer", current_item="",
        event="local_upload.metadata_assets_started",
        details={"child_job_id": child_id, "video_count": len(videos), "source_namespace": "local_upload"}, progress=True, force=True,
    )
    child_row = await _wait_for_orchestrated_child(
        ctx, child_id, label="Inventory & snapshots", parent_label="Local Upload",
        event_namespace="local_upload", parent_job_type="UPLOAD_LOCAL",
        allow_completed_with_errors=True,
    )
    try:
        child_detail = json.loads(child_row.get("detail") or "{}")
    except Exception:
        child_detail = {}
    queue = child_detail.get("channel_queue") if isinstance(child_detail.get("channel_queue"), list) else []
    prepared_videos = queue[0].get("videos") if queue and isinstance(queue[0], dict) else []
    by_path = {str(Path(str(v.get("path") or "")).resolve(strict=False)).casefold(): v
               for v in prepared_videos if isinstance(v, dict) and v.get("path")}

    for video in videos:
        entry = dict(manifest.get(str(video)) or {})
        prepared = by_path.get(str(video.resolve(strict=False)).casefold())
        media_id = int((prepared or {}).get("media_id") or entry.get("media_id") or 0)
        try:
            if not media_id:
                raise RuntimeError("Local inventory did not create a staging record")
            _require_local_upload_assets_ready(ctx.db, media_id, video)
            caption = await build_local_upload_caption(
                ctx.db, video, dict(entry.get("probe") or {}),
                oshash_value=str(entry.get("oshash") or ""), media_id=media_id,
            )
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            reason = (
                f"Local preflight failed: {type(exc).__name__}: {exc}"
                if str(exc) else f"Local preflight failed: {type(exc).__name__}"
            )
            entry.update({
                "media_id": media_id,
                "media_channel": "local_upload",
                "upload_ready": False,
                "preparation_error": reason[:1000],
            })
            manifest[str(video)] = entry
            failed[str(video)] = reason
            ctx.db.log(
                f"Upload Local Files: {video.name} was not sent because local preparation failed; "
                f"keeping the source and continuing: {reason[:600]}",
                "WARN",
                event="telegram_upload.preflight_skipped",
                job_id=ctx.job_id,
                details={"filename": video.name, "media_id": media_id, "error": reason[:1000]},
            )
            continue

        entry.update({
            "media_id": media_id,
            "media_channel": "local_upload",
            "media_message_id": int((prepared or {}).get("message_id") or 0),
            "identification_status": "PENDING",
            "caption": caption,
            "upload_ready": True,
        })
        manifest[str(video)] = entry
    state["file_manifest"] = manifest
    state["failed"] = dict(failed)
    state["metadata_assets_media_ids"] = {str(Path(str(v.get("path") or "")).resolve(strict=False)).casefold(): int(v.get("media_id") or 0)
                                           for v in prepared_videos if isinstance(v, dict) and v.get("path")}
    ctx.save_state(state)
    ready_count = sum(1 for video in videos if (manifest.get(str(video)) or {}).get("upload_ready"))
    failed_count = len(videos) - ready_count
    ctx.activity(
        f"Local inventory and snapshots ready for {ready_count} of {len(videos)} upload(s)",
        operation=("Local Upload · technical preparation completed with skipped files"
                   if failed_count else "Local Upload · technical preparation ready"),
        current_item="",
        event=("local_upload.metadata_assets_completed_with_errors"
               if failed_count else "local_upload.metadata_assets_completed"),
        details={"child_job_id": child_id, "video_count": len(videos), "snapshots_ready": ready_count,
                 "preparation_failures": failed_count, "scene_identification_deferred": True},
        progress=True, force=True,
    )
    return manifest


def _filter_local_upload_candidates(db, videos: list[Path]) -> tuple[list[Path], list[Path]]:
    owned = _automation_owned_paths(db)
    allowed: list[Path] = []
    blocked: list[Path] = []
    for video in videos:
        if _norm_source_path(video) in owned:
            blocked.append(video)
        else:
            allowed.append(video)
    return allowed, blocked


# ---- main engine entry point ----



# ==============================================================================
# SECTION: Main engine entry point
# Local upload job.
# ==============================================================================

async def run(ctx: JobContext):
    """Upload videos from a local folder to a Telegram channel."""
    import json as _json
    from ..core.resolution_filter import (
        matches_resolution_filter,
        normalize_resolution_filter,
        resolution_tier_for_dimensions,
    )
    job = ctx.job
    try:
        cfg = _json.loads(job.get("detail") or "{}")
    except Exception:
        cfg = {}
    resolution_filter = normalize_resolution_filter(cfg.get("resolution_filter"))
    from ..core import duration_filter
    max_minutes = duration_filter.normalize_max_minutes(cfg.get("max_duration_minutes"))
    orientation = str(cfg.get("orientation") or "BOTH").upper()
    if orientation not in {"HORIZONTAL", "VERTICAL", "BOTH"}:
        orientation = "BOTH"

    folder = Path(cfg.get("folder") or ".")
    try:
        validate_local_upload_folder(ctx.db, folder)
    except ValueError as exc:
        raise RuntimeError(str(exc))
    dest_id = job.get("destination_channel", "")
    include_subfolders = bool(cfg.get("include_subfolders", False))
    if not dest_id:
        raise RuntimeError("Uploader job requires a destination channel.")
    if not folder.is_dir():
        raise RuntimeError(f"Folder not found: {folder}")

    client = ctx.telegram.client
    destination = await client.get_entity(int(dest_id))
    from ..core.telegram_identity import canonical_peer_id
    dest_id = canonical_peer_id(destination)
    delete_local_after_delivery = bool(cfg.get("delete_local_after_delivery", True))
    from ..core.upload_limits import normalize_oversized_file_policy
    oversized_file_policy = normalize_oversized_file_policy(
        cfg.get("oversized_file_policy"), legacy_split_upload=bool(cfg.get("split_upload", False)), default="optimize"
    )
    split_upload = oversized_file_policy == "split"
    ffmpeg, ffprobe = await asyncio.to_thread(ffmpeg_tools)
    # FFmpeg is optional: PyAV is the primary media engine.
    state = ctx.load_state({"uploaded": [], "uploaded_hashes": [], "skipped_upload_limit": [], "failed": {}, "pending_delivery": {}, "file_manifest": {}, "manifest_summary": {}})
    uploaded = set(state.get("uploaded", []))
    uploaded_hashes = {str(x).strip().lower() for x in (state.get("uploaded_hashes", []) or []) if str(x).strip()}
    skipped_limit = set(state.get("skipped_upload_limit", []))
    failed = {str(k): str(v) for k, v in (state.get("failed", {}) or {}).items()}
    pending_delivery = dict(state.get("pending_delivery", {}) or {})
    upload_policy = await get_upload_policy(client)
    ctx.db.log(f"Upload Local Files: {upload_policy.describe()}")
    ctx.report(completed=0, total=0, current_item="", operation="Scanning folder for videos", force=True)
    videos = await ctx.blocking(find_videos, folder, include_subfolders=include_subfolders)
    videos, automation_owned = _filter_local_upload_candidates(ctx.db, videos)
    if automation_owned:
        ctx.db.log(
            f"Upload Local Files: excluded {len(automation_owned)} Content Automation-owned video(s); "
            "torrent downloads stay in the Content Automation pipeline",
            "INFO",
        )
    candidates = [v for v in videos if str(v) not in uploaded and (oversized_file_policy != "skip" or str(v) not in skipped_limit)]
    if oversized_file_policy != "skip":
        # An explicit Optimize or Split policy makes files previously marked as
        # size-skipped eligible again, without clearing unrelated job history.
        skipped_limit.difference_update(str(v) for v in candidates)
        state["skipped_upload_limit"] = sorted(skipped_limit)
        ctx.save_state(state)
    # Snapshot the selected files immediately, including byte size, before any
    # upload work begins. This also gives the UI/state file a durable inventory.
    manifest = dict(state.get("file_manifest") or {})
    for _video in candidates:
        try:
            _size = int(_video.stat().st_size)
        except OSError:
            _size = 0
        manifest[str(_video)] = {
            **dict(manifest.get(str(_video)) or {}),
            "path": str(_video), "name": _video.name, "size": _size,
            "size_human": human_size(_size),
        }
    state["file_manifest"] = manifest
    state["manifest_summary"] = {
        "files": len(candidates),
        "total_bytes": sum(int((manifest.get(str(v)) or {}).get("size") or 0) for v in candidates),
        "order": "size_ascending",
        "captured_before_upload": True,
    }
    ctx.save_state(state)
    # Smallest-first is deliberate: a large upload cannot hold the queue behind
    # it, while the manifest/metadata pass runs once before the first transfer.
    candidates = _sort_local_upload_candidates(candidates, manifest)
    now_ts = time.time()
    busy = [v for v in candidates if is_still_changing(v, now=now_ts)]
    pending = [v for v in candidates if v not in busy]
    state.pop("resolution_filter_summary", None)
    state.pop("orientation_filter_summary", None)
    state.pop("duration_filter_summary", None)
    existing_summary = dict(state.get("manifest_summary") or {})
    existing_summary.pop("resolution_filter", None)
    existing_summary.pop("orientation_filter", None)
    existing_summary.pop("duration_filter", None)
    state["manifest_summary"] = existing_summary
    if busy:
        ctx.db.log(f"Upload Local Files: {len(busy)} file(s) changed in the last {STABLE_AFTER_SECONDS}s "
                   f"(probably still copying) and were left for the next run: "
                   + ", ".join(v.name for v in busy[:5]) + (" ..." if len(busy) > 5 else ""), "WARN")
    # Prefetch metadata for the entire pending queue before the first Telegram
    # upload. This overlaps provider/API latency with local preparation instead
    # of discovering metadata serially after an upload has already started.
    if pending:
        ctx.report(completed=0, total=len(pending), current_item="",
                   operation="Prefetching file metadata before upload", force=True)
        await _prefetch_local_upload_manifest(
            ctx, pending, ffprobe, state, total=len(pending),
            resolution_filter=resolution_filter, orientation=orientation,
        )
        manifest = dict(state.get("file_manifest") or {})
        pending = _sort_local_upload_candidates(pending, manifest)
        if resolution_filter != "ANY":
            checked = len(pending)
            eligible = []
            unknown_dimensions = 0
            for video in pending:
                probe_meta = (manifest.get(str(video)) or {}).get("probe") or {}
                width, height = probe_meta.get("width"), probe_meta.get("height")
                if matches_resolution_filter(width, height, resolution_filter):
                    eligible.append(video)
                elif resolution_tier_for_dimensions(width, height) is None:
                    unknown_dimensions += 1
            pending = eligible
            state["resolution_filter_summary"] = {
                "selection": resolution_filter,
                "checked": checked,
                "selected": len(pending),
                "excluded": checked - len(pending),
                "unknown_dimensions": unknown_dimensions,
            }
            summary = dict(state.get("manifest_summary") or {})
            summary["files_scanned"] = checked
            summary["files"] = len(pending)
            summary["total_bytes"] = sum(
                int((manifest.get(str(video)) or {}).get("size") or 0)
                for video in pending
            )
            summary["resolution_filter"] = dict(state["resolution_filter_summary"])
            state["manifest_summary"] = summary
            ctx.db.log(
                f"Upload Local Files: source-resolution filter {resolution_filter} kept "
                f"{len(pending):,}/{checked:,} video(s); {unknown_dimensions:,} had unknown dimensions."
            )
        if orientation != "BOTH":
            checked = len(pending)
            kept, unknown_orientation = [], 0
            for video in pending:
                probe_meta = (manifest.get(str(video)) or {}).get("probe") or {}
                actual = display_orientation(probe_meta.get("width"), probe_meta.get("height"), probe_meta.get("rotation") or 0)
                if actual == "UNKNOWN":
                    unknown_orientation += 1
                elif actual == orientation:
                    kept.append(video)
            pending = kept
            state["orientation_filter_summary"] = {
                "selection": orientation, "checked": checked, "selected": len(pending),
                "excluded": checked - len(pending), "unknown_dimensions": unknown_orientation,
            }
            summary = dict(state.get("manifest_summary") or {})
            summary["files"] = len(pending)
            summary["total_bytes"] = sum(int((manifest.get(str(v)) or {}).get("size") or 0) for v in pending)
            summary["orientation_filter"] = dict(state["orientation_filter_summary"])
            state["manifest_summary"] = summary
            ctx.db.log(
                f"Upload Local Files: orientation filter {orientation.lower()} kept "
                f"{len(pending):,}/{checked:,} video(s); {unknown_orientation:,} had unknown dimensions."
            )
        if max_minutes is not None:
            checked = len(pending)
            kept, unknown_duration = [], 0
            for video in pending:
                seconds = ((manifest.get(str(video)) or {}).get("probe") or {}).get("duration")
                if duration_filter.within_limit(seconds, max_minutes):
                    kept.append(video)
                elif not duration_filter.known(seconds):
                    unknown_duration += 1
            pending = kept
            state["duration_filter_summary"] = {
                "selection": duration_filter.describe(max_minutes), "checked": checked, "selected": len(pending),
                "excluded": checked - len(pending), "unknown_duration": unknown_duration,
            }
            summary = dict(state.get("manifest_summary") or {})
            summary["files"] = len(pending)
            summary["total_bytes"] = sum(int((manifest.get(str(v)) or {}).get("size") or 0) for v in pending)
            summary["duration_filter"] = dict(state["duration_filter_summary"])
            state["manifest_summary"] = summary
            ctx.db.log(
                f"Upload Local Files: duration filter ({duration_filter.describe(max_minutes)}) kept "
                f"{len(pending):,}/{checked:,} video(s); {unknown_duration:,} had no duration and were left out."
            )
        if pending:
            ctx.db.log(
                f"Upload Local Files: technical preflight captured {len(pending):,} file(s); "
                "local inventory and validated snapshots will finish before the first send. "
                "Scene and performer/studio identification may complete later."
            )
            manifest = await _prepare_local_upload_catalogue(ctx, pending, manifest, state, failed)
        state["file_manifest"] = manifest
        ctx.save_state(state)
        if pending:
            # Recheck each eligible source immediately before the send queue. A
            # single file that lost readiness stays local; its peers still proceed.
            for _path in pending:
                _entry = dict(manifest.get(str(_path)) or {})
                if not _entry.get("upload_ready"):
                    continue
                try:
                    if not int(_entry.get("media_id") or 0):
                        raise RuntimeError("Local inventory did not create a staging record")
                    _require_local_upload_assets_ready(ctx.db, int(_entry["media_id"]), _path)
                except (JobCancelled, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    reason = (
                        f"Local preflight failed: {type(exc).__name__}: {exc}"
                        if str(exc) else f"Local preflight failed: {type(exc).__name__}"
                    )
                    _entry.update({"upload_ready": False, "preparation_error": reason[:1000]})
                    manifest[str(_path)] = _entry
                    failed[str(_path)] = reason
                    ctx.db.log(
                        f"Upload Local Files: {_path.name} lost snapshot readiness before sending; "
                        f"keeping the source and continuing: {reason[:600]}",
                        "WARN",
                        event="telegram_upload.preflight_skipped",
                        job_id=ctx.job_id,
                        details={"filename": _path.name, "media_id": int(_entry.get("media_id") or 0),
                                 "error": reason[:1000]},
                    )
            state["file_manifest"] = manifest
            state["failed"] = dict(failed)
            ctx.save_state(state)
    work_root = folder / WORK_DIR_NAME

    total = len(pending)
    if failed:
        ctx.db.log(
            f"Upload Local Files: resuming {len(failed)} previously failed file(s); "
            "failure state is not treated as successful upload."
        )
    scan_scope = "including subfolders" if include_subfolders else "selected folder only"
    ctx.db.log(
        f"Upload Local Files: {total} local videos to upload from {folder} "
        f"({scan_scope}; {len(uploaded)} previously verified, "
        f"{len(skipped_limit)} skipped by size, {len(uploaded_hashes)} delivered fingerprints, {len(pending_delivery)} pending deliveries, {len(automation_owned)} Content Automation-owned excluded)"
    )
    empty_operation = "No videos found"
    if (
        not total
        and resolution_filter != "ANY"
        and int((state.get("resolution_filter_summary") or {}).get("checked") or 0) > 0
    ):
        empty_operation = f"No videos matched {resolution_filter}"
    if not total and orientation != "BOTH" and int((state.get("orientation_filter_summary") or {}).get("checked") or 0) > 0:
        empty_operation = f"No {orientation.lower()} videos found"
    if not total and max_minutes is not None and int((state.get("duration_filter_summary") or {}).get("checked") or 0) > 0:
        empty_operation = f"No videos {duration_filter.describe(max_minutes)}"
    ctx.report(
        completed=0, total=total,
        operation=("Preparing first file" if total else empty_operation),
        force=True,
    )

    for i, video in enumerate(pending):
        manifest_entry = dict((state.get("file_manifest") or {}).get(str(video)) or {})
        if not manifest_entry.get("upload_ready"):
            reason = failed.get(str(video)) or str(
                manifest_entry.get("preparation_error")
                or "Required local upload preparation was not verified; source retained"
            )
            failed[str(video)] = reason
            state["failed"] = dict(failed)
            ctx.save_state(state)
            ctx.report(
                completed=i + 1,
                total=total,
                current_item=video.name,
                operation=f"Skipped {video.name} — local preparation is not ready; continuing",
                force=True,
            )
            continue

        item_work = None
        optimizer_dir = None
        video_lock_cm = None
        try:
            await ctx.checkpoint()
            ctx.report(completed=i, total=total, current_item=video.name, operation=f"Preparing {video.name}", force=True)

            try:
                file_size = video.stat().st_size
            except FileNotFoundError:
                raise RuntimeError(f"Upload source disappeared: {video}")
            oversized = file_size > upload_policy.safe_limit_bytes
            # Skip policy leaves oversized source files untouched and out of delivery.
            if oversized and oversized_file_policy == "skip":
                skipped_limit.add(str(video))
                failed.pop(str(video), None)
                state["skipped_upload_limit"] = sorted(skipped_limit)
                state["failed"] = dict(failed)
                ctx.save_state(state)
                reason = (f"Skipped {video.name}: {human_size(file_size)} exceeds the "
                          f"{human_size(upload_policy.safe_limit_bytes)} safe Telegram upload limit; "
                          "oversized-file policy is Skip. Source retained.")
                ctx.db.log(f"Upload Local Files: {reason}", "WARN", event="telegram_upload.oversized_skipped",
                           job_id=ctx.job_id, details={"filename": video.name, "size_bytes": file_size,
                                                       "safe_limit_bytes": upload_policy.safe_limit_bytes})
                ctx.report(completed=i + 1, total=total, current_item=video.name, operation="Skipped oversized file", force=True)
                continue
            # Optimize re-encodes a temporary copy; Split bypasses optimization.

            # Crash-safe Telegram delivery recovery. The pending record is written
            # after Telegram returns both message IDs and before verification, so a
            # process crash cannot silently cause a duplicate upload on restart.
            pending = pending_delivery.get(str(video)) or {}
            manifest_entry = dict((state.get("file_manifest") or {}).get(str(video)) or {})
            if pending.get("video_id") and isinstance(pending.get("split_delivery"), dict):
                # A split delivery is a sequence of Telegram documents, not a
                # native video message. Re-verify every volume and finish its
                # thumbnail/Library registration without re-uploading parts.
                from .delivery_pipeline import verify_split_delivery, prepare_local_delivery_assets
                split_info = dict(pending.get("split_delivery") or {})
                pending_destination_verified = False
                try:
                    valid, reason = await verify_split_delivery(client, destination, split_info)
                    if not valid:
                        from .delivery_pipeline import split_verification_is_definite_failure
                        if split_verification_is_definite_failure(reason):
                            try:
                                from ..core.split_files import finalize_split_group
                                await asyncio.to_thread(finalize_split_group, ctx.db, str(split_info.get("group_id") or ""), status="INCOMPLETE")
                            except Exception:
                                _swallowed_note("uploader.run")
                                pass
                        raise RuntimeError(f"Saved split-volume sequence is not currently verifiable: {reason}")
                    primary_message_id = int(pending.get("video_id") or 0)
                    primary_message = await client.get_messages(destination, ids=primary_message_id)
                    if primary_message is None:
                        raise RuntimeError("The primary split volume disappeared during recovery")
                    recover_hash = str(pending.get("oshash") or "").strip() or None
                    recover_meta = dict(manifest_entry.get("probe") or {})
                    with tempfile.TemporaryDirectory(prefix="tg_split_recover_") as td_recover:
                        recover_rep, recover_sheet = await prepare_local_delivery_assets(
                            ctx.db, video, source_oshash=recover_hash, video_meta=recover_meta or None, ctx=ctx,
                        )
                        thumb_id_recovered = int(pending.get("thumb_id") or 0)
                        thumb_message = await client.get_messages(destination, ids=thumb_id_recovered) if thumb_id_recovered else None
                        if thumb_message is None:
                            thumb_message = await _publish_checkpointed_contact_sheet(
                                ctx, client, destination, recover_sheet,
                                file_name=f"{video.stem}.jpg", video_key=str(video),
                                pending=pending, pending_delivery=pending_delivery,
                                state=state,
                            )
                            thumb_id_recovered = int(getattr(thumb_message, "id", 0) or 0)
                        if not thumb_id_recovered or await client.get_messages(destination, ids=thumb_id_recovered) is None:
                            raise RuntimeError("Telegram split-upload contact sheet could not be verified during recovery")
                        if thumb_id_recovered <= max(int(part.get("message_id") or 0) for part in split_info.get("parts", [])):
                            raise RuntimeError("Recovered contact sheet must follow all split volumes")
                        pending.update({"stage": "pair_sent", "video_id": primary_message_id,
                                        "thumb_id": thumb_id_recovered, "split_delivery": split_info,
                                        "filename": video.name,
                                        "caption": str(manifest_entry.get("caption") or pending.get("caption") or "")})
                        pending_delivery[str(video)] = pending
                        state["pending_delivery"] = dict(pending_delivery)
                        ctx.save_state(state)
                        pending_destination_verified = True
                        oshash_pending = str(pending.get("oshash") or "").strip().lower()
                        finalization = await _commit_verified_local_delivery(
                            ctx, destination=destination, destination_channel=str(dest_id),
                            source_path=video, upload_path=None,
                            prepared_media_id=int(manifest_entry.get("media_id") or pending.get("media_id") or 0),
                            source_message_id=int(manifest_entry.get("media_message_id") or pending.get("source_message_id") or 0),
                            source_oshash=oshash_pending, video_id=primary_message_id,
                            thumb_id=thumb_id_recovered, video_meta=recover_meta,
                            caption=str(manifest_entry.get("caption") or pending.get("caption") or ""),
                            manifest_entry=manifest_entry, representative_path=recover_rep,
                            destination_message=primary_message, split_delivery=split_info,
                        )
                        uploaded.add(str(video))
                        if oshash_pending:
                            uploaded_hashes.add(oshash_pending)
                        pending_delivery.pop(str(video), None)
                        failed.pop(str(video), None)
                        state["uploaded"] = list(uploaded)
                        state["uploaded_hashes"] = sorted(uploaded_hashes)
                        state["pending_delivery"] = dict(pending_delivery)
                        state["failed"] = dict(failed)
                        ctx.save_state(state)
                        deleted = await _delete_verified_source_file(video, ctx.db.log) if delete_local_after_delivery else False
                        ctx.db.log(
                            f"Upload Local Files: recovered verified split upload for {video.name}; "
                            + ("source deleted." if deleted else ("source retained by setting." if not delete_local_after_delivery else "source could not be deleted.")),
                            "INFO" if (deleted or not delete_local_after_delivery) else "WARN",
                        )
                        try:
                            from .thumbnail_cache import cleanup_local_delivery_artifacts
                            cleanup_local_delivery_artifacts(video)
                        except Exception:
                            _swallowed_note("uploader.run")
                            pass
                        continue
                except Exception as exc:
                    failed[str(video)] = f"Split upload recovery pending: {type(exc).__name__}: {exc}"
                    state["failed"] = dict(failed)
                    state["pending_delivery"] = dict(pending_delivery)
                    ctx.save_state(state)
                    ctx.db.log(
                        f"Upload Local Files: could not safely recover split delivery for {video.name}; "
                        f"retaining its local source and checkpoint: {type(exc).__name__}: {exc}", "WARN",
                    )
                    raise RuntimeError(
                        f"Split upload for {video.name} has a saved Telegram checkpoint but could not be verified. "
                        "Its source and checkpoint were retained to avoid duplicate delivery. Check the destination channel and retry."
                    ) from exc

            if pending.get("video_id"):
                # Two-phase recovery closes the crash window between Telegram
                # accepting the video and the paired thumbnail upload. If the
                # process died after the video message was created, do not send
                # the video again; verify the existing message and finish only
                # the missing thumbnail.
                pending_destination_verified = False
                pending_recovery_uncertain = True
                try:
                    pending_video_id = int(pending.get("video_id") or 0)
                    pending_thumb_id = int(pending.get("thumb_id") or 0)
                    if pending_video_id and pending_thumb_id:
                        # Repair an older/incomplete native cover in place
                        # before accepting the already-posted pair as complete.
                        from .delivery_pipeline import prepare_local_delivery_assets
                        recover_hash = str(pending.get("oshash") or "").strip() or None
                        recover_meta = dict(manifest_entry.get("probe") or {})
                        with tempfile.TemporaryDirectory(prefix="tg_upload_cover_recover_") as td_recover:
                            _recover_rep, recover_sheet = await prepare_local_delivery_assets(
                                ctx.db, video, source_oshash=recover_hash,
                                video_meta=recover_meta or None, ctx=ctx,
                            )
                            from .telegram_sender import ensure_local_video_cover
                            await ensure_local_video_cover(
                                client, destination, pending_video_id, recover_sheet.read_bytes(),
                            )
                        verified_pending, pending_reason = await verify_destination_pair(
                            client, destination, pending_video_id, pending_thumb_id,
                            expected_video_name=video.name, require_native_video=True,
                        )
                        if not verified_pending:
                            raise RuntimeError(
                                f"Existing Telegram pair is not yet verifiable ({pending_reason}); "
                                "keeping its recovery checkpoint to avoid a duplicate."
                            )
                        pending_recovery_uncertain = False
                        if verified_pending:
                            oshash_pending = str(pending.get("oshash") or "").strip().lower()
                            pending_destination_verified = True
                            await _commit_verified_local_delivery(
                                ctx, destination=destination, destination_channel=str(dest_id),
                                source_path=video, upload_path=None,
                                prepared_media_id=int(manifest_entry.get("media_id") or pending.get("media_id") or 0),
                                source_message_id=int(manifest_entry.get("media_message_id") or pending.get("source_message_id") or 0),
                                source_oshash=oshash_pending,
                                video_id=pending_video_id, thumb_id=pending_thumb_id,
                                video_meta=dict(manifest_entry.get("probe") or {}),
                                caption=str(manifest_entry.get("caption") or pending.get("caption") or ""),
                                manifest_entry=manifest_entry,
                            )
                            uploaded.add(str(video))
                            if oshash_pending:
                                uploaded_hashes.add(oshash_pending)
                            pending_delivery.pop(str(video), None)
                            failed.pop(str(video), None)
                            state["uploaded"] = list(uploaded)
                            state["uploaded_hashes"] = sorted(uploaded_hashes)
                            state["pending_delivery"] = dict(pending_delivery)
                            state["failed"] = dict(failed)
                            ctx.save_state(state)
                            deleted = await _delete_verified_source_file(video, ctx.db.log) if delete_local_after_delivery else False
                            if deleted:
                                ctx.db.log(f"Upload Local Files: recovered verified upload for {video.name}; source deleted.")
                            elif delete_local_after_delivery:
                                ctx.db.log(f"Upload Local Files: recovered verified upload for {video.name}; source could not be deleted.", "WARN")
                            else:
                                ctx.db.log(f"Upload Local Files: recovered verified upload for {video.name}; source retained by setting.")
                            continue
                        ctx.db.log(f"Upload Local Files: pending delivery for {video.name} is not verifiable ({pending_reason}); retrying upload.", "WARN")
                    elif pending_video_id:
                        remote_video = await client.get_messages(destination, ids=pending_video_id)
                        if remote_video and is_video_message(remote_video):
                            pending_recovery_uncertain = True
                            # The video is already safely on Telegram. Finish the
                            # missing paired thumbnail without re-uploading it.
                            with tempfile.TemporaryDirectory(prefix="tg_upload_recover_") as td_recover:
                                from .delivery_pipeline import prepare_local_delivery_assets
                                recover_hash = str(pending.get("oshash") or "").strip() or None
                                recover_meta = dict(manifest_entry.get("probe") or {})
                                ctx.report(completed=i, total=total, current_item=video.name,
                                           operation=f"Preparing 36-frame Telegram contact sheet: {video.name}",
                                           progress=(100.0 * i / max(total, 1)), force=True)
                                recover_rep, recover_thumb = await prepare_local_delivery_assets(
                                    ctx.db, video, source_oshash=recover_hash,
                                    video_meta=recover_meta or None, ctx=ctx,
                                )
                                if not recover_thumb.exists() or recover_thumb.stat().st_size <= 0:
                                    raise RuntimeError("Could not prepare recovery contact sheet")
                                from .telegram_sender import ensure_local_video_cover
                                await ensure_local_video_cover(
                                    client, destination, pending_video_id, recover_thumb.read_bytes(),
                                )
                                thumb_msg = await _publish_checkpointed_contact_sheet(
                                    ctx, client, destination, recover_thumb,
                                    file_name=f"{video.stem}.jpg", video_key=str(video),
                                    pending=pending, pending_delivery=pending_delivery,
                                    state=state,
                                )
                                recovered_thumb_id = int(getattr(thumb_msg, "id", 0) or 0)
                                if not recovered_thumb_id:
                                    raise RuntimeError("Telegram did not return a thumbnail message ID during recovery")
                                pending_delivery[str(video)] = {
                                    "video_id": pending_video_id, "thumb_id": recovered_thumb_id,
                                    "oshash": str(pending.get("oshash") or ""),
                                    "thumbnail_source": str(pending.get("thumbnail_source") or "recovered"),
                                    "media_id": int(manifest_entry.get("media_id") or pending.get("media_id") or 0),
                                    "source_message_id": int(manifest_entry.get("media_message_id") or pending.get("source_message_id") or 0),
                                    "filename": video.name,
                                    "caption": str(manifest_entry.get("caption") or pending.get("caption") or ""),
                                }
                                state["pending_delivery"] = dict(pending_delivery)
                                ctx.save_state(state)
                                ok, reason = await verify_destination_pair(
                                    client, destination, pending_video_id, recovered_thumb_id,
                                    expected_video_name=video.name, require_native_video=True,
                                )
                                if not ok:
                                    raise RuntimeError(f"Recovered video exists but destination verification failed: {reason}")
                                pending_recovery_uncertain = False
                                oshash_pending = str(pending.get("oshash") or "").strip().lower()
                                pending_destination_verified = True
                                await _commit_verified_local_delivery(
                                    ctx, destination=destination, destination_channel=str(dest_id),
                                    source_path=video, upload_path=None,
                                    prepared_media_id=int(manifest_entry.get("media_id") or pending.get("media_id") or 0),
                                    source_message_id=int(manifest_entry.get("media_message_id") or pending.get("source_message_id") or 0),
                                    source_oshash=oshash_pending,
                                    video_id=pending_video_id, thumb_id=recovered_thumb_id,
                                    video_meta=recover_meta, caption=str(manifest_entry.get("caption") or pending.get("caption") or ""),
                                    manifest_entry=manifest_entry,
                                    representative_path=recover_rep, destination_message=remote_video,
                                )
                                uploaded.add(str(video))
                                if oshash_pending:
                                    uploaded_hashes.add(oshash_pending)
                                pending_delivery.pop(str(video), None)
                                failed.pop(str(video), None)
                                state["uploaded"] = list(uploaded)
                                state["uploaded_hashes"] = sorted(uploaded_hashes)
                                state["pending_delivery"] = dict(pending_delivery)
                                state["failed"] = dict(failed)
                                ctx.save_state(state)
                                deleted = await _delete_verified_source_file(video, ctx.db.log) if delete_local_after_delivery else False
                                ctx.db.log(
                                    f"Upload Local Files: recovered previously sent video {video.name}; "
                                    + ("source deleted." if deleted else ("source retained by setting." if not delete_local_after_delivery else "source could not be deleted.")),
                                    "INFO" if (deleted or not delete_local_after_delivery) else "WARN",
                                )
                                continue
                        else:
                            pending_recovery_uncertain = False
                            ctx.db.log(f"Upload Local Files: pending video message {pending_video_id} was not found as a video; it can be retried.", "WARN")
                except Exception as exc:
                    if pending_destination_verified:
                        failed[str(video)] = f"Verified Telegram delivery is awaiting Library registration: {type(exc).__name__}: {exc}"
                        state["failed"] = dict(failed)
                        state["pending_delivery"] = dict(pending_delivery)
                        ctx.save_state(state)
                        ctx.db.log(
                            f"Upload Local Files: {video.name} exists and verifies on Telegram, but Library/ledger registration "
                            f"did not finish; keeping its pending checkpoint and local source for safe retry: "
                            f"{type(exc).__name__}: {exc}", "ERROR",
                        )
                        raise RuntimeError(
                            f"{video.name} is already on Telegram and verified, but Archive X registration is pending. "
                            "The local source and recovery checkpoint were kept; retry this job to finish registration."
                        ) from exc
                    if pending_recovery_uncertain:
                        failed[str(video)] = f"Telegram delivery status is uncertain: {type(exc).__name__}: {exc}"
                        state["failed"] = dict(failed)
                        state["pending_delivery"] = dict(pending_delivery)
                        ctx.save_state(state)
                        ctx.db.log(
                            f"Upload Local Files: could not safely reconcile pending Telegram delivery for {video.name}; "
                            f"keeping the checkpoint and source rather than risking a duplicate: {type(exc).__name__}: {exc}",
                            "WARN",
                        )
                        raise RuntimeError(
                            f"Could not verify the existing Telegram post for {video.name}; the local source and "
                            "recovery checkpoint were kept to avoid uploading a duplicate. Retry after the connection recovers."
                        ) from exc
                    ctx.db.log(f"Upload Local Files: pending delivery recovery failed for {video.name}: {type(exc).__name__}: {exc}; retrying upload.", "WARN")
                if str(pending.get("stage") or "") != "send_started":
                    pending_delivery.pop(str(video), None)
                    state["pending_delivery"] = dict(pending_delivery)
                    ctx.save_state(state)

            # Calculate the source fingerprint once for metadata matching and
            # crash-safe identity tracking. It is deliberately done on the
            # ORIGINAL source file, before any conversion.
            manifest_entry = dict((state.get("file_manifest") or {}).get(str(video)) or {})
            source_oshash = str(manifest_entry.get("oshash") or "").strip() or None
            if not source_oshash:
                try:
                    from ..metadata.fingerprint import oshash_file
                    source_oshash = await ctx.blocking(oshash_file, str(video))
                except Exception as exc:
                    ctx.db.log(f"Upload Local Files: OSHASH unavailable for {video.name}: {exc}", "WARN")

            if source_oshash and str(source_oshash).strip().lower() in uploaded_hashes:
                # Same content was already delivered by this persisted job. Treat
                # it as a restart/rename recovery rather than sending a duplicate.
                uploaded.add(str(video))
                failed.pop(str(video), None)
                state["uploaded"] = list(uploaded)
                state["uploaded_hashes"] = sorted(uploaded_hashes)
                state["failed"] = dict(failed)
                ctx.save_state(state)
                deleted = await _delete_verified_source_file(video, ctx.db.log) if delete_local_after_delivery else False
                if deleted:
                    ctx.db.log(f"Upload Local Files: duplicate fingerprint already delivered for {video.name}; source deleted.", "INFO")
                elif delete_local_after_delivery:
                    ctx.db.log(f"Upload Local Files: duplicate fingerprint already delivered for {video.name}; source retained because deletion failed.", "WARN")
                else:
                    ctx.db.log(f"Upload Local Files: duplicate fingerprint already delivered for {video.name}; source retained by setting.", "INFO")
                continue

            # Lock the original identity through conversion, asset preparation,
            # sending and ledger registration so another lane cannot prepare or
            # deliver the same source at the same time.
            from ..core.video_locks import video_operation_lock
            video_lock_cm = video_operation_lock(identity_key=str(source_oshash or ""), file_path=video)
            await video_lock_cm.__aenter__()

            upload_source = video
            optimizer_result = None
            if oversized and not split_upload:
                if not ffmpeg:
                    raise RuntimeError(f"{video.name} exceeds the {human_size(upload_policy.safe_limit_bytes)} safe limit and FFmpeg is unavailable for size optimization")
                try:
                    from ..core.size_optimizer import optimize_for_limit
                    optimizer_dir = work_root / uuid.uuid4().hex[:12]
                    async def _opt_report(message: str) -> None:
                        ctx.report(completed=i, total=total, current_item=video.name, operation=message,
                                   progress=(100.0 * i / max(total, 1)), force=True)
                    upload_source, optimizer_result = await optimize_for_limit(
                        ffmpeg, video, optimizer_dir, upload_policy.safe_limit_bytes, probe, ffprobe=ffprobe, report=_opt_report
                    )
                    ctx.db.log(
                        f"Upload Local Files: optimized oversized {video.name} from {human_size(file_size)} "
                        f"to {human_size(upload_source.stat().st_size)} using {optimizer_result.get('encoder')} "
                        f"in {optimizer_result.get('attempts')} pass(es); source preserved.", "INFO"
                    )
                except asyncio.CancelledError:
                    raise
                except JobSkipped:
                    raise
                except Exception as exc:
                    # Record the failure and carry on with the rest of the
                    # queue (it used to abort the whole job). The file stays
                    # eligible for the next run; the source is untouched.
                    reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                    failed[str(video)] = reason
                    state["failed"] = dict(failed)
                    ctx.save_state(state)
                    ctx.db.log(f"Upload Local Files: compressing {video.name} failed: {reason[:600]}", "ERROR",
                               event="telegram_upload.optimize_failed", job_id=ctx.job_id)
                    ctx.report(completed=i + 1, total=total, current_item=video.name,
                               operation=f"FAILED — could not compress {video.name}", force=True)
                    continue

            _upload_progress_state = {"last": 0.0, "last_sent": 0, "started": time.monotonic(),
                                      "last_logged_percent": -5, "completion_logged": False}
            upload_progress_meter = RollingTransferProgress(window_seconds=10.0, minimum_seconds=2.0)

            def _upload_progress(sent, size, name=video.name, i=i):
                # Telethon calls the progress callback for every upload part.
                # Updating SQLite/dashboard state for every 512 KiB chunk adds
                # measurable overhead on fast connections, so keep the UI at
                # about 4 updates/sec while still reporting the final chunk.
                now = time.monotonic()
                pct = 100.0 * sent / size if size else 0.0
                is_final = bool(size and sent >= size)
                sample = upload_progress_meter.update(sent, size, now=now)
                rate_mbps = float(sample["rate_mib_per_second"] or 0.0)
                eta_seconds = sample["eta_seconds"]
                eta_label = str(sample["eta_label"])
                if not is_final and (now - _upload_progress_state["last"]) < 0.25:
                    return
                _upload_progress_state["last"] = now
                _upload_progress_state["last_sent"] = sent
                try:
                    ctx.db.set_job_transfer_telemetry(
                        ctx.job_id,
                        {"stage": "Uploading local file", "current_item": name,
                         "file_name": name, "direction": "upload",
                         "bytes_done": int(sent), "bytes_total": int(size or 0),
                         "rate_bytes_per_second": sample.get("rate_bytes_per_second"),
                         "eta_seconds": eta_seconds,
                         "transfer_state": "active" if sample.get("rate_bytes_per_second") is not None else "warming_up"},
                        min_interval=0.25 if is_final else 1.0,
                    )
                except Exception:
                    _swallowed_note("uploader.run._upload_progress")
                    pass
                if ctx.cancel_requested():
                    op = f"Cancel requested — stopping {name}"
                elif ctx.pause_requested():
                    op = f"Pause requested — finishing {name}"
                elif ctx.skip_requested():
                    op = f"Skip requested — finishing {name}"
                else:
                    op = f"Uploading {name} ({pct:.0f}%) · {rate_mbps:.1f} MB/s · ETA {eta_label}"
                ctx.report(completed=i, total=total, current_item=name,
                           operation=op,
                           progress=100.0 * (i + (sent / size if size else 0)) / max(total, 1))
                percent_i = int(pct)
                if is_final or (size and percent_i >= int(_upload_progress_state.get("last_logged_percent", -5)) + 5):
                    _upload_progress_state["last_logged_percent"] = percent_i
                    ctx.activity(
                        f"Local upload {percent_i}% · {sent:,}/{size:,} bytes · {name}",
                        operation=f"Local Upload · {name} · {percent_i}%", current_item=f"{name} · {percent_i}%",
                        event="local_upload.video_upload_progress",
                        details={"filename": name, "percent": percent_i, "bytes_sent": int(sent),
                                 "bytes_total": int(size or 0), "bytes_remaining": max(0, int(size or 0)-int(sent)),
                                 "rate_mib_per_second": round(rate_mbps, 2), "eta_seconds": eta_seconds,
                                 "file_index": int(i), "file_count": int(total)},
                        progress=True,
                    )
                if is_final and not _upload_progress_state["completion_logged"]:
                    _upload_progress_state["completion_logged"] = True
                    final_sample = upload_progress_meter.finish(sent, size)
                    elapsed_total = max(0.001, float(final_sample["elapsed_seconds"]))
                    average_rate = float(final_sample["average_mib_per_second"] or 0.0)
                    message = (f"Local upload complete: {name} — {format_size(size)} in "
                               f"{elapsed_total:.0f} s ({average_rate:.1f} MB/s)")
                    try:
                        ctx.db.log(message, "SUCCESS", event="local_upload.video_upload_complete",
                                   job_id=ctx.job_id,
                                   details={"filename": name, "bytes_total": int(size),
                                            "elapsed_seconds": round(elapsed_total, 2),
                                            "average_rate_mib_per_second": round(average_rate, 2)})
                    except Exception:
                        _swallowed_note("uploader.run._upload_progress")
                        pass
                    try:
                        ctx.db.set_job_transfer_telemetry(
                            ctx.job_id,
                            {"stage": "Upload complete", "current_item": name,
                             "file_name": name, "direction": "upload",
                             "bytes_done": int(size or sent), "bytes_total": int(size or 0),
                             "rate_bytes_per_second": None, "eta_seconds": None,
                             "transfer_state": "complete"},
                            min_interval=0.25,
                        )
                    except Exception:
                        _swallowed_note("uploader.run._upload_progress")
                        pass
                    try:
                        ctx.activity(message, operation=f"Local Upload · {name} · complete",
                                     current_item=f"{name} · complete",
                                     event="local_upload.video_upload_complete",
                                     details={"filename": name, "bytes_total": int(size),
                                              "elapsed_seconds": round(elapsed_total, 2),
                                              "average_rate_mib_per_second": round(average_rate, 2)},
                                     progress=True)
                    except Exception:
                        _swallowed_note("uploader.run._upload_progress")
                        pass

            def _upload_status(message: str, wait_seconds: int = 0, name=video.name) -> None:
                status_event = (
                    "local_upload.upload_wait" if wait_seconds > 0
                    else "local_upload.transfer_status"
                )
                status_level = "WARN" if wait_seconds > 0 else "INFO"
                ctx.report(completed=i, total=total, current_item=f"{name} · {message}",
                           operation=message, progress=True, force=True)
                try:
                    ctx.db.set_job_transfer_telemetry(
                        ctx.job_id,
                        {"stage": "Uploading local file", "current_item": name,
                         "file_name": name, "direction": "upload",
                         "bytes_done": int(_upload_progress_state.get("last_sent", 0)),
                         "bytes_total": int(upload_source.stat().st_size) if upload_source.exists() else 0,
                         "rate_bytes_per_second": None, "eta_seconds": None,
                         "transfer_state": "paused" if wait_seconds > 0 else "active"},
                        min_interval=0.25,
                    )
                except Exception:
                    _swallowed_note("uploader.run._upload_status")
                    pass
                ctx.activity(
                    message, operation=message, current_item=f"{name} · {message}",
                    level=status_level, event=status_event,
                    details={"filename": name, "wait_seconds": int(wait_seconds or 0),
                             "bytes_sent": int(_upload_progress_state.get("last_sent", 0)),
                             "bytes_total": int(upload_source.stat().st_size) if upload_source.exists() else 0},
                    progress=True,
                )
                try:
                    ctx.db.log(message, status_level, event=status_event)
                except Exception:
                    _swallowed_note("uploader.run._upload_status")
                    pass

            with tempfile.TemporaryDirectory(prefix="tg_upload_") as td:
                tempdir = Path(td)
                try:
                    await ctx.checkpoint()
                    ctx.report(completed=i, total=total, current_item=video.name,
                               operation=f"Checking Telegram stream compatibility: {video.name}")
                    item_work = work_root / uuid.uuid4().hex[:12]
                    upload_video = await prepare_video_async(ctx, upload_source, ffmpeg, ffprobe, i, total, work_dir=item_work)
                    if not upload_video:
                        raise RuntimeError(f"Could not prepare {video.name}: no uploadable media was produced")
                    await ctx.checkpoint()
                    meta = await asyncio.to_thread(probe, ffprobe, upload_video)
                    # Prefer the preflight metadata/caption. If prefetch was
                    # partial, fall back to the existing on-demand lookup.
                    prefetched_caption = str(manifest_entry.get("caption") or "").strip()
                    caption_task = None if prefetched_caption else asyncio.create_task(
                        build_local_upload_caption(ctx.db, video, meta, oshash_value=source_oshash,
                                                   media_id=int(manifest_entry.get("media_id") or 0) or None)
                    )
                    # The original local media keeps its canonical individual-frame
                    # set. Telegram receives a separate 36-frame contact sheet, while
                    # the destination Library continues to cache only one representative still.
                    from .delivery_pipeline import prepare_local_delivery_assets
                    from .thumbnail_cache import local_playback_snapshot_path
                    source_meta = dict((manifest_entry.get("probe") or {}))
                    if not source_meta and upload_video.resolve() == video.resolve():
                        source_meta = dict(meta)
                    before_snapshot = local_playback_snapshot_path(video) if local_playback_snapshot_path(video).is_file() else None
                    source_thumb, _source_sheet = await prepare_local_delivery_assets(
                        ctx.db, video, source_oshash=source_oshash, video_meta=source_meta or meta, ctx=ctx,
                    )
                    if before_snapshot is not None and source_thumb.resolve() == before_snapshot.resolve():
                        operation = f"Reusing existing Archive X snapshots: {video.name}"
                    else:
                        operation = f"Prepared Archive X snapshots: {video.name}"
                    ctx.report(completed=i, total=total, current_item=video.name,
                               operation=operation, progress=(100.0 * i / max(total, 1)), force=True)
                    # Generate the contact sheet for the exact bytes that will be uploaded
                    # (post-remux/conversion where applicable), plus a separate representative.
                    delivery_rep, delivery_sheet = await prepare_local_delivery_assets(
                        ctx.db, upload_video,
                        source_oshash=source_oshash if upload_video.resolve() == video.resolve() else None,
                        video_meta=meta, ctx=ctx, display_filename=delivered_filename(video),
                    )
                    thumb_source = "playback-contact-sheet-36"
                    await ctx.checkpoint()
                    caption = prefetched_caption or await caption_task
                    cover_bytes = delivery_sheet.read_bytes()
                    ctx.activity(f"Prepared local upload payload for {video.name}",
                                 operation="Local upload · video and contact sheet ready", current_item=video.name,
                                 event="local_upload.payload_ready",
                                 details={"source_path": str(video), "upload_path": str(upload_video),
                                          "upload_bytes": int(upload_video.stat().st_size),
                                          "representative_path": str(delivery_rep),
                                          "contact_sheet_path": str(delivery_sheet),
                                          "contact_sheet_bytes": int(delivery_sheet.stat().st_size),
                                          "snapshot_frames": 36, "contact_sheet_rows": 6, "contact_sheet_columns": 6,
                                          "metadata": {k: meta.get(k) for k in ("video_codec", "audio_codec", "codec", "width", "height", "fps", "duration", "bitrate") if k in meta},
                                          "caption": str(caption or "")[:2000]}, progress=True)
                    from .delivery_pipeline import send_local_video
                    video_key = str(video)
                    send_checkpoint = dict(pending_delivery.get(video_key) or {})
                    message_random_id = int(
                        send_checkpoint.get("message_random_id") or secrets.randbits(63) or 1
                    )
                    baseline_message_id = send_checkpoint.get("baseline_message_id")
                    if baseline_message_id is None:
                        baseline_message_id = await _latest_destination_message_id(client, destination)
                    baseline_message_id = int(baseline_message_id or 0)
                    send_checkpoint.update({
                        "stage": "send_started",
                        "message_random_id": message_random_id,
                        "baseline_message_id": baseline_message_id,
                        "oshash": str(source_oshash or ""),
                        "thumbnail_source": str(thumb_source),
                        "media_id": int(manifest_entry.get("media_id") or 0),
                        "source_message_id": int(manifest_entry.get("media_message_id") or 0),
                        "filename": video.name,
                        "caption": str(caption or ""),
                    })
                    pending_delivery[video_key] = send_checkpoint
                    state["pending_delivery"] = dict(pending_delivery)
                    ctx.save_state(state)

                    while True:
                        try:
                            new_vid = await ctx.cancellable(lambda: send_local_video(
                                client, destination, upload_video, delivery_sheet,
                                caption=caption, video_meta=meta, file_name=delivered_filename(video),
                                progress_callback=_upload_progress, status_callback=_upload_status,
                                split_upload=bool(split_upload),
                                message_random_id=message_random_id,
                                baseline_message_id=baseline_message_id,
                            ), activity=True)
                            break
                        except LocalVideoCoverError as e:
                            if int(getattr(e, "message_id", 0) or 0) > 0:
                                send_checkpoint.update({
                                    "stage": "cover_verification_pending",
                                    "video_id": int(e.message_id),
                                    "thumb_id": 0,
                                })
                                pending_delivery[video_key] = send_checkpoint
                                state["pending_delivery"] = dict(pending_delivery)
                                ctx.save_state(state)
                            raise
                        except Exception as e:
                            if is_flood_wait(e):
                                # Long FloodWaits must stay responsive to Stop and keep the
                                # watchdog heartbeat alive (a raw sleep did neither).
                                _fw = flood_wait_seconds(e) + 2
                                await ctx.cancellable(lambda: asyncio.sleep(_fw))
                                continue
                            raise
                    from ..core.split_files import SplitDeliveryResult
                    split_info = new_vid.to_dict() if isinstance(new_vid, SplitDeliveryResult) else None
                    primary_message = new_vid.primary_message if isinstance(new_vid, SplitDeliveryResult) else new_vid
                    video_id = int(getattr(primary_message, "id", 0) or 0)
                    if not video_id:
                        raise RuntimeError("Telegram accepted the video but did not return a video message ID; source file was kept.")
                    # Persist the video phase before the paired thumbnail request.
                    # This closes the small process-crash window where Telegram has
                    # accepted the video but the thumbnail has not yet been posted.
                    send_checkpoint.update({
                        "stage": "video_sent", "video_id": video_id, "thumb_id": 0,
                        "oshash": str(source_oshash or ""),
                        "thumbnail_source": str(thumb_source),
                        "media_id": int(manifest_entry.get("media_id") or 0),
                        "source_message_id": int(manifest_entry.get("media_message_id") or 0),
                        "filename": video.name, "caption": str(caption or ""),
                        **({"split_delivery": split_info} if split_info else {}),
                    })
                    pending_delivery[video_key] = send_checkpoint
                    state["pending_delivery"] = dict(pending_delivery)
                    ctx.save_state(state)

                    # Do not checkpoint between the video and paired thumbnail: a
                    # skip/pause requested during the video transfer must not leave
                    # an unpaired archive item. Complete the safe pair first.
                    thumb_msg = None
                    if delivery_sheet:
                        thumb_msg = await _publish_checkpointed_contact_sheet(
                            ctx, client, destination, delivery_sheet,
                            file_name=f"{video.stem}.jpg", video_key=video_key,
                            pending=send_checkpoint, pending_delivery=pending_delivery,
                            state=state,
                        )

                    # SAFETY GATE: the original local source is deleted only after
                    # Telegram confirms that BOTH the destination video and its
                    # paired thumbnail exist and the video is still a native video.
                    # A verification failure leaves the source untouched so the job
                    # can be retried safely without losing the local file.
                    # No checkpoint here: a Cancel/Skip at this point used to stop
                    # the job AFTER the video was posted but BEFORE it was recorded,
                    # so the next run posted the same video a second time.
                    thumb_id = int(getattr(thumb_msg, "id", 0) or 0)
                    if not thumb_id:
                        raise RuntimeError(
                            "Upload completed but Telegram did not return both video and thumbnail message IDs; "
                            "source file was kept."
                        )

                    # Persist the paired remote message IDs before verification so
                    # a process crash cannot lose the delivery checkpoint.
                    send_checkpoint.update({
                        "stage": "pair_sent", "video_id": video_id, "thumb_id": thumb_id,
                        "oshash": str(source_oshash or ""),
                        "thumbnail_source": str(thumb_source),
                        "media_id": int(manifest_entry.get("media_id") or 0),
                        "source_message_id": int(manifest_entry.get("media_message_id") or 0),
                        "filename": video.name, "caption": str(caption or ""),
                        **({"split_delivery": split_info} if split_info else {}),
                    })
                    pending_delivery[video_key] = send_checkpoint
                    state["pending_delivery"] = dict(pending_delivery)
                    ctx.save_state(state)

                    from .delivery_pipeline import verify_delivery, verify_split_delivery
                    if split_info:
                        verified, verification_reason = await verify_split_delivery(client, destination, split_info)
                        thumb_check = await client.get_messages(destination, ids=thumb_id) if verified else None
                        if verified and thumb_check is None:
                            verified, verification_reason = False, "The split upload contact sheet is missing"
                    else:
                        verified, verification_reason = await verify_delivery(
                            client, destination, video_id, thumb_id, expected_name=video.name,
                        )
                    if not verified:
                        raise RuntimeError(
                            f"Destination verification failed: {verification_reason}; source file was kept."
                        )

                    # Register the verified Telegram delivery before clearing its
                    # recovery checkpoint or deleting the local source. This same
                    # idempotent finalizer is used by crash recovery above.
                    try:
                        finalization = await _commit_verified_local_delivery(
                            ctx, destination=destination, destination_channel=str(dest_id),
                            source_path=video, upload_path=upload_video,
                            prepared_media_id=int(manifest_entry.get("media_id") or 0),
                            source_message_id=int(manifest_entry.get("media_message_id") or 0),
                            source_oshash=str(source_oshash or ""),
                            video_id=int(video_id), thumb_id=int(thumb_id), video_meta=meta,
                            caption=str(caption or ""), manifest_entry=manifest_entry,
                            representative_path=delivery_rep,
                            destination_message=primary_message,
                            split_delivery=split_info,
                        )
                        delivery_record = finalization.get("delivery") or {}
                    except Exception as exc:
                        raise RuntimeError(f"Verified upload could not be recorded in delivery history and Library: {type(exc).__name__}: {exc}") from exc

                    # Record the verified upload FIRST, so a crash or a failed
                    # delete can never cause a second upload of this file.
                    video_key = str(video)
                    uploaded.add(video_key)
                    if source_oshash:
                        uploaded_hashes.add(str(source_oshash).strip().lower())
                    pending_delivery.pop(video_key, None)
                    failed.pop(video_key, None)
                    state["uploaded"] = list(uploaded)
                    state["uploaded_hashes"] = sorted(uploaded_hashes)
                    state["pending_delivery"] = dict(pending_delivery)
                    state["failed"] = dict(failed)
                    ctx.save_state(state)

                    # Keep Archive X's canonical 36 individual snapshots and
                    # representative for reuse, but delete the delivery-only
                    # contact sheet after Telegram's video+cover+document pair
                    # and database ledger have both been verified/committed.
                    try:
                        from .thumbnail_cache import (
                            cleanup_local_delivery_artifacts,
                            cleanup_local_generated_snapshot_set,
                        )
                        cleanup_local_delivery_artifacts(video)
                        if Path(upload_video).resolve(strict=False) != Path(video).resolve(strict=False):
                            cleanup_local_generated_snapshot_set(ctx.db, Path(upload_video))
                    except Exception as cleanup_exc:
                        ctx.db.log(
                            f"Upload Local Files: verified delivery but auxiliary-file cleanup was incomplete for {video.name}: "
                            f"{type(cleanup_exc).__name__}: {cleanup_exc}", "WARN"
                        )

                    # The upload is now verified. Delete only the ORIGINAL local
                    # source file, never the temporary converted upload file.
                    # Windows can briefly hold the file after media probing/upload,
                    # so retry and verify the path is actually gone.
                    deleted = await _delete_verified_source_file(video, ctx.db.log) if delete_local_after_delivery else False
                    if deleted:
                        ctx.db.log(
                            f"Upload Local Files: verified destination for {video.name}; source deleted."
                        )
                    elif delete_local_after_delivery:
                        # Never claim successful source deletion when the file is
                        # still present. The Telegram upload remains valid, but the
                        # source is intentionally kept for safety.
                        ctx.db.log(
                            f"Upload Local Files: destination verified but source could not be deleted; "
                            f"source kept: {video}", "WARN"
                        )
                    else:
                        ctx.db.log(f"Upload Local Files: verified destination for {video.name}; source retained by setting.")
                except JobSkipped:
                    raise
                except UploadLimitExceeded as e:
                    video_key = str(video)
                    skipped_limit.add(video_key)
                    failed.pop(video_key, None)
                    state["skipped_upload_limit"] = list(skipped_limit)
                    state["failed"] = dict(failed)
                    ctx.save_state(state)
                    ctx.db.log(f"Upload Local Files: {e}", "WARN")
                    ctx.report(completed=i + 1, total=total, current_item=video.name,
                               operation=str(e), progress=(100.0 * (i + 1) / max(total, 1)), force=True)
                    continue
                except Exception as e:
                    video_key = str(video)
                    reason = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                    failed[video_key] = reason
                    state["failed"] = dict(failed)
                    ctx.save_state(state)
                    ctx.db.log(f"Upload failed for {video.name}: {reason}", "ERROR")
                    ctx.report(
                        completed=i, total=total, current_item=video.name,
                        operation=f"FAILED — {video.name}: {reason}",
                        progress=(100.0 * i / max(total, 1)), force=True
                    )
                    continue
        except JobSkipped:
            ctx.db.log(f"Upload Local Files: skipped current file {video.name}")
            ctx.report(completed=i, total=total, current_item=video.name,
                       operation=f"Skipped {video.name} — continuing queue",
                       progress=(100.0 * i / max(total, 1)), force=True)
            continue
        finally:
            # Always release the cross-lane per-video identity lock.
            try:
                if video_lock_cm is not None:
                    await video_lock_cm.__aexit__(None, None, None)
            except Exception:
                _swallowed_note("uploader.run")
                pass
            # Remove this file's converted copy (if any) whatever happened.
            if item_work is not None:
                await asyncio.to_thread(shutil.rmtree, item_work, True)
            if optimizer_dir is not None:
                await asyncio.to_thread(shutil.rmtree, optimizer_dir, True)
            try:
                work_root.rmdir()      # only succeeds when empty
            except OSError:
                pass

    if failed:
        # A job with one or more unverified upload failures must never be shown
        # as SUCCESS/COMPLETED. This is especially important after an application
        # restart: only files that reached Telegram verification are written to
        # `uploaded`, while failed files remain eligible for the next resume.
        summary = "; ".join(
            f"{Path(k).name}: {v}" for k, v in list(failed.items())[:5]
        )
        if len(failed) > 5:
            summary += f"; +{len(failed) - 5} more"
        ctx.report(
            completed=max(0, total - len(failed)), total=total, current_item="",
            operation=f"Upload incomplete — {len(failed)} file(s) failed verification/upload",
            progress=(100.0 * max(0, total - len(failed)) / max(total, 1)), force=True
        )
        raise RuntimeError(
            f"Upload Local Files incomplete: {len(failed)} file(s) were not verified in Telegram. {summary}"
        )

    skipped_count = len(skipped_limit.intersection({str(v) for v in videos}))
    final_op = (
        f"Upload queue completed with {skipped_count} size-limited skip(s)"
        if skipped_count else "Upload queue completed"
    )
    ctx.report(completed=total, total=total, current_item="", operation=final_op, progress=100, force=True)
