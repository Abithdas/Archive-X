"""Internal media engine.

Prefer PyAV (the PyAV wheel bundled in the application's Python environment)
for probing, remuxing, transcoding and thumbnail extraction.  External
ffmpeg/ffprobe are compatibility fallbacks only when PyAV cannot perform a
requested operation.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Direct PyAV: probing and remuxing
#   - Direct: thumbnails and snapshot frames
#   - Native-process boundary
#   - Public API
# ------------------------------------------------------------------
from __future__ import annotations

from .swallowed import note as _swallowed_note
import math
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .pyav_support import import_pyav

VIDEO_OK = {"h264", "avc1", "hevc", "h265"}
AUDIO_OK = {"aac", "mp4a"}
CONTAINER_OK = {"mp4", "mov", "m4v"}
SNAPSHOT_PROGRESS_HEARTBEAT_SECONDS = 1.0




# ==============================================================================
# SECTION: Direct PyAV: probing and remuxing
# In-process probing, streamability checks, remux and transcode.
# ==============================================================================

def _is_attached_picture_stream(stream) -> bool:
    """True for cover-art streams that FFmpeg also labels as video."""
    disposition = getattr(stream, "disposition", None)
    if disposition is None:
        return False
    try:
        attached_flag = getattr(type(disposition), "attached_pic", None)
        if attached_flag is not None:
            return bool(int(disposition) & int(attached_flag))
    except (TypeError, ValueError):
        pass
    try:
        # AV_DISPOSITION_ATTACHED_PIC is 0x0400 in libavformat.
        return bool(int(disposition) & 0x0400)
    except (TypeError, ValueError):
        return False


def _primary_video_stream(streams):
    video_streams = [stream for stream in streams if getattr(stream, "type", None) == "video"]
    return next((stream for stream in video_streams if not _is_attached_picture_stream(stream)),
                video_streams[0] if video_streams else None)


def _duration(container, stream) -> float:
    try:
        if stream.duration is not None and stream.time_base:
            return max(0.0, float(stream.duration * stream.time_base))
    except Exception:
        _swallowed_note("internal_media._duration")
        pass
    try:
        if container.duration:
            return max(0.0, float(container.duration) / 1_000_000.0)
    except Exception:
        _swallowed_note("internal_media._duration")
        pass
    return 0.0


def _probe_direct(path: str | Path) -> dict:
    av = import_pyav()
    c = av.open(str(path), mode="r")
    try:
        vs = _primary_video_stream(c.streams)
        aus = next((s for s in c.streams if s.type == "audio"), None)
        fmt = str(getattr(c.format, "name", "") or "")
        vcodec = str(getattr(vs.codec_context, "name", "") or "") if vs else ""
        acodec = str(getattr(aus.codec_context, "name", "") or "") if aus else ""
        fps = 0.0
        try:
            rate = vs.average_rate or vs.base_rate if vs else None
            if rate:
                fps = float(rate)
        except Exception:
            _swallowed_note("internal_media._probe_direct")
            pass
        return {
            "duration": _duration(c, vs) if vs else 0.0,
            "format_name": fmt,
            "video_codec": vcodec,
            "audio_codec": acodec,
            "width": int(getattr(vs, "width", 0) or 0) if vs else 0,
            "height": int(getattr(vs, "height", 0) or 0) if vs else 0,
            "fps": fps,
            "pix_fmt": str(getattr(vs.codec_context, "format", "") or "") if vs else "",
            "has_audio": bool(aus),
        }
    finally:
        c.close()


def _open_output(av, path: Path):
    # faststart is handled by the MOV/MP4 muxer.  If a PyAV/FFmpeg build does
    # not accept the option, retry without it; correctness is more important
    # than failing an otherwise valid internal conversion.
    try:
        return av.open(str(path), mode="w", format="mp4", options={"movflags": "+faststart"})
    except Exception:
        return av.open(str(path), mode="w", format="mp4")


def _add_stream_from_template(output_container, source_stream):
    """Create a packet-copy output stream across supported PyAV APIs.

    PyAV 14 exposes ``add_stream_from_template``; passing only ``template=``
    to ``add_stream`` is not accepted by that API and raises a TypeError. The
    fallbacks retain compatibility with older wheels while preferring the API
    that copies codec parameters and extradata correctly.
    """
    add_from_template = getattr(output_container, "add_stream_from_template", None)
    if callable(add_from_template):
        return add_from_template(source_stream)
    try:
        return output_container.add_stream(template=source_stream)
    except TypeError:
        codec_name = str(getattr(getattr(source_stream, "codec_context", None), "name", "") or "")
        if not codec_name:
            raise RuntimeError("source stream has no codec name for PyAV stream-copy setup")
        stream_type = str(getattr(source_stream, "type", "") or "")
        rate = _fps(source_stream) if stream_type == "video" else None
        target = output_container.add_stream(codec_name, rate=rate)
        source_context = getattr(source_stream, "codec_context", None)
        target_context = getattr(target, "codec_context", None)
        for attr in ("width", "height", "pix_fmt", "sample_rate", "layout", "bit_rate", "extradata"):
            try:
                value = getattr(source_context, attr)
                if value is not None:
                    setattr(target_context, attr, value)
            except (AttributeError, TypeError, ValueError):
                continue
        try:
            target.time_base = source_stream.time_base
        except (AttributeError, TypeError, ValueError):
            pass
        return target


def _remux_direct(input_path: str | Path, output_path: str | Path) -> dict:
    """Remux compatible H264/H265 + AAC streams without re-encoding."""
    av = import_pyav()
    inp = av.open(str(input_path), mode="r")
    out = None
    try:
        out = _open_output(av, Path(output_path))
        mapping = {}
        seen_video = seen_audio = False
        for s in inp.streams:
            if s.type == "video" and not seen_video:
                codec_name = str(getattr(s.codec_context, "name", "") or "")
                if not codec_name:
                    raise RuntimeError("video codec name unavailable for PyAV remux")
                ns = _add_stream_from_template(out, s)
                mapping[s.index] = ns
                seen_video = True
            elif s.type == "audio" and not seen_audio:
                codec_name = str(getattr(s.codec_context, "name", "") or "")
                if not codec_name:
                    raise RuntimeError("audio codec name unavailable for PyAV remux")
                ns = _add_stream_from_template(out, s)
                mapping[s.index] = ns
                seen_audio = True
        if not seen_video or not seen_audio:
            raise RuntimeError("remux requires one video and one audio stream")
        for packet in inp.demux():
            if packet.stream.index not in mapping:
                continue
            packet.stream = mapping[packet.stream.index]
            out.mux(packet)
        return _probe_direct(output_path)
    finally:
        try:
            if out:
                out.close()
        finally:
            inp.close()


def _fps(stream):
    try:
        return stream.average_rate or stream.base_rate or 30
    except Exception:
        return 30


def _transcode_direct(input_path: str | Path, output_path: str | Path, *, preserve_video: bool = True,
              on_progress: Optional[Callable[[float], None]] = None) -> dict:
    """Internal transcode using codecs bundled with the PyAV wheel.

    H.264/H.265 video can be stream-copied while audio is encoded to AAC. For
    unsupported video codecs, PyAV's libx264 encoder is used. Synthetic silent
    AAC is intentionally delegated to the external fallback because PyAV does
    not provide a simple cross-version lavfi source API.
    """
    av = import_pyav()
    inp = av.open(str(input_path), mode="r")
    out = None
    try:
        vs = next((s for s in inp.streams if s.type == "video"), None)
        aus = next((s for s in inp.streams if s.type == "audio"), None)
        if vs is None:
            raise RuntimeError("no video stream")
        meta = _probe_direct(input_path)
        if aus is None:
            raise RuntimeError("internal PyAV path does not synthesize a silent AAC source")
        out = _open_output(av, Path(output_path))

        vcodec = meta["video_codec"].lower()
        if preserve_video and vcodec in VIDEO_OK:
            vout = _add_stream_from_template(out, vs)
            encode_video = False
        else:
            try:
                vout = out.add_stream("libx264", rate=_fps(vs))
            except Exception as exc:
                raise RuntimeError(f"PyAV H.264 encoder unavailable: {exc}")
            vout.width = int(vs.width or 0)
            vout.height = int(vs.height or 0)
            vout.pix_fmt = "yuv420p"
            vout.options = {"crf": "23", "preset": "medium"}
            encode_video = True

        try:
            aout = out.add_stream("aac", rate=48000)
            aout.layout = "stereo"
            aout.bit_rate = 128000
        except Exception as exc:
            raise RuntimeError(f"PyAV AAC encoder unavailable: {exc}")

        total = float(meta.get("duration") or 0.0)
        for packet in inp.demux():
            if packet.stream.type == "video":
                if not encode_video:
                    packet.stream = vout
                    out.mux(packet)
                else:
                    for frame in packet.decode():
                        for op in vout.encode(frame):
                            out.mux(op)
                        if on_progress and frame.time is not None and total:
                            on_progress(max(0.0, min(1.0, float(frame.time) / total)))
            elif packet.stream.type == "audio":
                for frame in packet.decode():
                    for op in aout.encode(frame):
                        out.mux(op)

        if encode_video:
            for op in vout.encode(None):
                out.mux(op)
        for op in aout.encode(None):
            out.mux(op)
        return _probe_direct(output_path)
    finally:
        try:
            if out:
                out.close()
        finally:
            inp.close()



# ==============================================================================
# SECTION: Direct: thumbnails and snapshot frames
# Thumbnail and snapshot frame extraction backends.
# ==============================================================================

def _make_thumbnail_direct(input_path: str | Path, output_path: str | Path, timestamp: Optional[float] = None,
                   width: int = 1536, height: int = 864) -> Path:
    """Decode one representative frame with PyAV and write a JPEG via Pillow."""
    av = import_pyav()
    from PIL import Image
    c = av.open(str(input_path), mode="r")
    try:
        s = _primary_video_stream(c.streams)
        if s is None:
            raise RuntimeError("no video stream")
        dur = _duration(c, s)
        target = float(timestamp if timestamp is not None else max(0.75, min(max(dur - 0.25, 0.75), dur * 0.35 if dur else 0.75)))
        try:
            c.seek(int(target / float(s.time_base)), stream=s, backward=True, any_frame=False)
        except Exception:
            c.seek(0)
        frame = next(c.decode(s), None)
        if frame is None:
            raise RuntimeError("PyAV decoded no frame")
        img = frame.to_image().convert("RGB")
        scale = min(width / max(1, img.width), height / max(1, img.height), 1.0)
        if scale < 1:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.Resampling.LANCZOS)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=92, optimize=True)
        return out
    finally:
        c.close()


class _LocalFileByteRangeSource:
    """Small thread-safe range reader used by the shared MP4 sample-table path.

    It exposes the same ``size`` / ``get_range_sync`` contract as Telegram's
    range source, but each request reads only the requested local file bytes.
    It never copies the entire video into memory.
    """

    def __init__(self, path: str | Path):
        import threading
        self.path = Path(path)
        self.size = int(self.path.stat().st_size)
        self.bytes_received = 0
        self.rpc_requests = 0
        self._stats_lock = threading.Lock()

    def get_range_sync(self, offset: int, length: int) -> bytes:
        start, amount = int(offset), int(length)
        if start < 0 or amount < 0 or start + amount > self.size:
            raise ValueError(f"Invalid local media byte range offset={start} length={amount} size={self.size}")
        with self.path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(amount)
        if len(data) != amount:
            raise RuntimeError(f"Short local media range read: {len(data)}/{amount} bytes at {start}")
        with self._stats_lock:
            self.bytes_received += len(data)
            self.rpc_requests += 1
        return data


def _make_snapshot_frames_indexed_direct(input_path: str | Path, output_dir: str | Path, *,
                                         count: int = 36, width: int = 640, quality: int = 92,
                                         on_progress: Optional[Callable[[float], None]] = None) -> Optional[dict]:
    """Use the MP4 sample index to decode only the GOP needed for each target.

    This shares Telegram Media Manager's H.264/HEVC MP4 parser and bounded GOP
    decoder, but sources sample bytes from the already-downloaded file. Most
    importantly, this avoids reopening a full local video and decoding forward
    through a long HEVC GOP independently for every one of the 36 timestamps.
    Unsupported containers/indexes return ``None`` so the existing isolated
    PyAV extraction remains the compatibility fallback.
    """
    import hashlib
    import shutil
    import time
    from PIL import Image

    source_path = Path(input_path)
    if int(count or 36) != 36 or source_path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        return None
    mode = str(os.environ.get("ARCHIVE_X_LOCAL_SNAPSHOT_MODE", "indexed") or "indexed").strip().lower()
    if mode in {"pyav", "accurate", "legacy", "off", "disabled"}:
        return None
    if mode not in {"indexed", "auto", "fast"}:
        return None

    try:
        # This module already owns the well-tested MP4 sample-table parser and
        # minimal GOP-to-JPEG decoder used by Telegram channel snapshots.
        from ..media_manager import telegram_media_manager as media_manager
        source = _LocalFileByteRangeSource(source_path)
        moov_bytes, _moov_offset = media_manager._load_moov(source)
        index = media_manager._parse_visual_config(moov_bytes)
        if not index:
            return None
        video_width = int(index.get("width") or 0)
        video_height = int(index.get("height") or 0)
        duration = float(index.get("duration") or 0.0)
        if video_width <= 0 or video_height <= 0 or duration <= 0:
            return None

        cpu_count = max(1, int(os.cpu_count() or 2))
        default_workers = min(4, max(1, cpu_count // 2))
        try:
            workers = int(os.environ.get("ARCHIVE_X_SNAPSHOT_WORKERS", str(default_workers)))
        except (TypeError, ValueError):
            workers = default_workers
        workers = max(1, min(4, int(count), workers))

        outdir = Path(output_dir)
        outdir.parent.mkdir(parents=True, exist_ok=True)
        build_dir = outdir.with_name(outdir.name + ".indexed.building")
        representative = outdir.with_name(outdir.name + ".indexed-representative.jpg")
        shutil.rmtree(build_dir, ignore_errors=True)
        representative.unlink(missing_ok=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        last_fraction = [0.0]

        def report(done: int, total: int) -> None:
            fraction = min(1.0, max(0.0, float(done) / max(1, int(total))))
            # Shared decoder calls this once per completed frame on its
            # coordinator thread. Keep it monotonic and do not let logging or
            # heartbeat callback errors compromise asset creation.
            last_fraction[0] = max(last_fraction[0], fraction)
            if on_progress is not None:
                try:
                    on_progress(last_fraction[0])
                except Exception:
                    _swallowed_note("internal_media._make_snapshot_frames_indexed_direct.report")
                    pass

        result = media_manager._smart_thumbnail_from_telegram_once(
            source, source_path.name, str(representative), frames_dir=str(build_dir),
            keyframe_only=False, worker_count=workers, progress_callback=report,
            frame_width=width, frame_quality=quality,
            prepared_index=index, prepared_moov_bytes=moov_bytes, quiet=True,
        )
        if not result:
            shutil.rmtree(build_dir, ignore_errors=True)
            return None

        signatures = set()
        frame_paths = [build_dir / f"frame_{i:02d}.jpg" for i in range(1, 37)]
        if not all(path.is_file() and path.stat().st_size >= 256 for path in frame_paths):
            raise RuntimeError("MP4 indexed snapshot path did not produce 36 valid JPEG files")
        for frame_path in frame_paths:
            with Image.open(frame_path) as image:
                image.verify()
            with Image.open(frame_path) as image:
                sample = image.convert("RGB").resize((24, 14), Image.Resampling.BILINEAR)
                signatures.add(hashlib.sha256(sample.tobytes()).digest())
        if len(signatures) < min(4, int(count)):
            raise RuntimeError(f"MP4 indexed snapshot path produced only {len(signatures)} visually distinct frames")

        # Replace an existing frame set with rollback protection. This avoids
        # losing the previous usable snapshot cache if Windows antivirus or a
        # player briefly holds a file in the target directory.
        backup_dir = outdir.with_name(outdir.name + ".indexed.previous")
        shutil.rmtree(backup_dir, ignore_errors=True)
        had_previous = outdir.exists()
        if had_previous:
            os.replace(str(outdir), str(backup_dir))
        try:
            os.replace(str(build_dir), str(outdir))
        except Exception:
            if had_previous and backup_dir.exists() and not outdir.exists():
                os.replace(str(backup_dir), str(outdir))
            raise
        else:
            shutil.rmtree(backup_dir, ignore_errors=True)
        elapsed = time.monotonic() - started
        if on_progress is not None:
            try:
                on_progress(1.0)
            except Exception:
                _swallowed_note("internal_media._make_snapshot_frames_indexed_direct")
                pass
        return {
            "frames_path": str(outdir), "frames": 36, "distinct_frames": len(signatures),
            "width": video_width, "height": video_height, "duration": duration,
            "workers": workers, "codec_threads_per_worker": 1,
            "extraction_method": "mp4_sample_index_gop",
            "source_bytes_read": source.bytes_received,
            "source_range_reads": source.rpc_requests,
            "elapsed_seconds": round(elapsed, 3), "codec": str(index.get("codec_name") or "unknown"),
        }
    except Exception as exc:
        # Preserve the old PyAV decoder as a compatibility route for malformed
        # MP4 indexes or local FFmpeg decoder failures. Log to stderr because
        # stdout from the native worker is its machine-readable progress stream.
        try:
            print(
                f"Indexed local snapshot extraction unavailable for {source_path.name}: "
                f"{type(exc).__name__}: {exc}; falling back to PyAV timestamp seeks",
                file=sys.stderr, flush=True,
            )
        except Exception:
            _swallowed_note("internal_media._make_snapshot_frames_indexed_direct")
            pass
        try:
            shutil.rmtree(Path(output_dir).with_name(Path(output_dir).name + ".indexed.building"), ignore_errors=True)
            Path(output_dir).with_name(Path(output_dir).name + ".indexed-representative.jpg").unlink(missing_ok=True)
        except Exception:
            _swallowed_note("internal_media._make_snapshot_frames_indexed_direct")
            pass
        return None
    finally:
        try:
            representative.unlink(missing_ok=True)
        except Exception:
            _swallowed_note("internal_media._make_snapshot_frames_indexed_direct")
            pass


def _make_snapshot_frames_pyav_direct(input_path: str | Path, output_dir: str | Path, *,
                                     count: int = 36, width: int = 640, quality: int = 92,
                                     on_progress: Optional[Callable[[float], None]] = None) -> dict:
    """Extract independent JPEG snapshots using bounded parallel timestamp seeks.

    Each decoder owns its PyAV container because seeking a shared container from
    multiple threads is unsafe. Frames are still sampled at the same 2%-98%
    timestamps and written as the same numbered 36-frame set. Bounded parallel
    seeks reduce wall-clock time for long-GOP/high-resolution HEVC files without
    decoding the entire video from beginning to end.

    ``on_progress`` is best-effort activity telemetry. It is also called while a
    decoder is traversing a long GOP, not only after a requested JPEG is saved,
    so watchdogs do not mistake legitimate decode work for a dead worker.
    """
    import hashlib
    import shutil
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Reuse the same sample-table/GOP decode strategy as Telegram-channel
    # snapshots for supported MP4-family local files. On malformed or unsupported
    # files, keep the established PyAV seek implementation below as a fallback.
    def _indexed_progress(value):
        if on_progress is not None:
            try:
                on_progress(max(0.0, min(1.0, float(value))))
            except Exception:
                _swallowed_note("internal_media._make_snapshot_frames_pyav_direct._indexed_progress")
                pass
    indexed = _make_snapshot_frames_indexed_direct(
        input_path, output_dir, count=count, width=width, quality=quality,
        on_progress=_indexed_progress,
    )
    if indexed is not None:
        if on_progress is not None:
            try:
                on_progress(1.0)
            except Exception:
                _swallowed_note("internal_media._make_snapshot_frames_pyav_direct")
                pass
        return indexed
    if on_progress is not None:
        try:
            # The indexed attempt may have emitted partial-frame progress before
            # falling back to timestamp decoding, which starts a fresh frame set.
            on_progress(0.0)
        except Exception:
            _swallowed_note("internal_media._make_snapshot_frames_pyav_direct")
            pass

    av = import_pyav()
    from PIL import Image
    source = str(input_path)
    outdir = Path(output_dir)
    inp = av.open(source, mode="r")
    try:
        stream = _primary_video_stream(inp.streams)
        if stream is None:
            raise RuntimeError("no video stream")
        duration = _duration(inp, stream)
        sw, sh = int(getattr(stream, "width", 0) or 0), int(getattr(stream, "height", 0) or 0)
        if sw <= 0 or sh <= 0 or duration <= 0:
            raise RuntimeError("video has no usable dimensions or duration")
        count = max(1, min(100, int(count or 36)))
        targets = [duration * (0.02 + 0.96 * i / max(1, count - 1)) for i in range(count)]
        cell_w = min(max(160, int(width)), sw)
        cell_h = max(90, int(cell_w * sh / max(1, sw)))
        # Snapshot worker is the only active PyAV operation in the child; the
        # initial container is closed before the bounded worker pool starts.
    finally:
        inp.close()

    # Default to half the logical CPU count, capped at four independent seeks.
    # Operators can force serial seeks on rotational/network storage by setting
    # ARCHIVE_X_SNAPSHOT_WORKERS=1. Each codec is similarly bounded to avoid
    # multiplying FFmpeg's automatic thread count by the number of workers.
    cpu_count = max(1, int(os.cpu_count() or 2))
    default_workers = min(4, max(1, cpu_count // 2))
    try:
        workers = int(os.environ.get("ARCHIVE_X_SNAPSHOT_WORKERS", str(default_workers)))
    except (TypeError, ValueError):
        workers = default_workers
    workers = max(1, min(4, count, workers))
    codec_threads = max(1, cpu_count // workers)

    outdir.parent.mkdir(parents=True, exist_ok=True)
    build_dir = outdir.with_name(outdir.name + ".building")
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    signatures: set[bytes] = set()
    signature_lock = threading.Lock()
    progress_lock = threading.Lock()
    completed_target_indexes: set[int] = set()

    def notify_progress(*, completed: bool = False, target_index: int | None = None) -> None:
        if on_progress is None:
            return
        with progress_lock:
            if completed and target_index is not None:
                completed_target_indexes.add(int(target_index))
            fraction = min(1.0, len(completed_target_indexes) / count)
            try:
                on_progress(fraction)
            except Exception:
                _swallowed_note("internal_media._make_snapshot_frames_pyav_direct.notify_progress")
                pass

    target_errors: dict[int, str] = {}

    def decode_batch(indices: list[int]) -> None:
        """Decode each requested frame independently so one bad seek cannot
        abandon the remaining timestamps assigned to this worker.

        The caller retries every missing/corrupt frame with a fresh container
        after the parallel pass. This is important for long-GOP HEVC sources,
        where an individual timestamp seek may fail even though other targets
        in the same file are perfectly decodable.
        """
        container = av.open(source, mode="r")
        try:
            video_stream = _primary_video_stream(container.streams)
            if video_stream is None:
                raise RuntimeError("no video stream in snapshot worker")
            try:
                video_stream.codec_context.thread_count = codec_threads
            except Exception:
                _swallowed_note("internal_media._make_snapshot_frames_pyav_direct.decode_batch")
                pass
            worker_tb = float(video_stream.time_base or 0.0)
            worker_start = getattr(video_stream, "start_time", None)
            worker_start_seconds = float(worker_start * video_stream.time_base) if worker_start is not None and video_stream.time_base else 0.0
            for zero_index in indices:
                target = targets[zero_index]
                try:
                    frame = None
                    reached_target = False
                    last_notify = time.monotonic()
                    decoded_count = 0
                    if worker_tb > 0:
                        offset = int(round(target / worker_tb))
                        if worker_start is not None:
                            offset += int(worker_start)
                        container.seek(offset, stream=video_stream, backward=True, any_frame=False)
                    for decoded in container.decode(video_stream):
                        frame = decoded
                        decoded_count += 1
                        target_time = target + worker_start_seconds
                        decoded_time = None
                        if decoded.pts is not None and video_stream.time_base:
                            decoded_time = float(decoded.pts * video_stream.time_base)
                        elif getattr(decoded, "time", None) is not None:
                            decoded_time = float(decoded.time) + worker_start_seconds
                        if decoded_time is not None and decoded_time >= target_time:
                            reached_target = True
                            break
                        # Native decoders may traverse an unusually long GOP before
                        # producing the selected frame. Refresh activity periodically.
                        now = time.monotonic()
                        if decoded_count % 64 == 0 or now - last_notify >= 0.5:
                            notify_progress()
                            last_notify = now
                    if frame is None:
                        raise RuntimeError(f"PyAV decoded no frame at target {target:.3f}s")
                    if not reached_target and (frame.pts is not None or getattr(frame, "time", None) is not None):
                        raise RuntimeError(f"PyAV could not reach requested snapshot timestamp {target:.3f}s")
                    img = frame.reformat(width=cell_w, height=cell_h, format="rgb24").to_image().convert("RGB")
                    image_path = build_dir / f"frame_{zero_index + 1:02d}.jpg"
                    img.save(image_path, "JPEG", quality=int(quality), optimize=True)
                    sample = img.resize((24, 14), Image.Resampling.BILINEAR)
                    with signature_lock:
                        signatures.add(hashlib.sha256(sample.tobytes()).digest())
                    target_errors.pop(zero_index, None)
                    notify_progress(completed=True, target_index=zero_index)
                except Exception as exc:
                    target_errors[zero_index] = f"{type(exc).__name__}: {exc}"
                    try:
                        (build_dir / f"frame_{zero_index + 1:02d}.jpg").unlink(missing_ok=True)
                    except OSError:
                        pass
                    notify_progress()
                    continue
        finally:
            container.close()

    # Assign interleaved timestamps to workers so each performs a balanced
    # number of independent seeks. Individual target failures are isolated and
    # retried below with a fresh decoder; no partial set is ever published.
    batches = [list(range(worker_index, count, workers)) for worker_index in range(workers)]
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ax-snapshot") as pool:
            future_batches = {pool.submit(decode_batch, batch): batch for batch in batches if batch}
            for future in as_completed(future_batches):
                batch = future_batches[future]
                try:
                    future.result()
                except Exception as exc:
                    # A worker-level failure (for example av.open failing) is
                    # recorded against its own batch; other batches continue.
                    detail = f"{type(exc).__name__}: {exc}"
                    for zero_index in batch:
                        target_errors[zero_index] = detail

        from PIL import Image
        def _frame_is_valid(path: Path) -> bool:
            try:
                if not path.is_file() or path.stat().st_size < 256:
                    return False
                with Image.open(path) as image:
                    image.verify()
                return True
            except Exception:
                return False

        # Retry missing or incomplete targets serially with a fresh container.
        # This recovers transient seek/decoder errors without copying neighbour
        # frames into missing positions or discarding the other 35 successes.
        retry_indexes = [idx for idx in range(count)
                         if not _frame_is_valid(build_dir / f"frame_{idx + 1:02d}.jpg")]
        for zero_index in retry_indexes:
            path = build_dir / f"frame_{zero_index + 1:02d}.jpg"
            path.unlink(missing_ok=True)
            try:
                decode_batch([zero_index])
            except Exception as exc:
                target_errors[zero_index] = f"{type(exc).__name__}: {exc}"

        missing_indexes = [idx for idx in range(count)
                           if not _frame_is_valid(build_dir / f"frame_{idx + 1:02d}.jpg")]
        if missing_indexes:
            labels = ", ".join(str(idx + 1) for idx in missing_indexes[:10])
            if len(missing_indexes) > 10:
                labels += ", …"
            reason = next((target_errors.get(idx) for idx in missing_indexes if target_errors.get(idx)), "decoder did not produce a valid JPEG")
            raise RuntimeError(
                f"Snapshot extraction incomplete: {count - len(missing_indexes)}/{count} valid frames; "
                f"missing/corrupt indexes: {labels}; last error: {reason}"
            )
        if len(signatures) < min(4, count):
            raise RuntimeError(f"snapshot extraction yielded only {len(signatures)} visually distinct frames")
        shutil.rmtree(outdir, ignore_errors=True)
        os.replace(str(build_dir), str(outdir))
        return {"frames_path": str(outdir), "frames": count, "distinct_frames": len(signatures),
                "width": sw, "height": sh, "duration": duration, "workers": workers,
                "codec_threads_per_worker": codec_threads,
                "extraction_method": "parallel_pyav_timestamp_seek_with_target_retry"}

    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise


def _make_snapshot_frames_ffmpeg_direct(input_path: str | Path, output_dir: str | Path, *,
                                        count: int = 36, width: int = 640, quality: int = 92,
                                        on_progress: Optional[Callable[[float], None]] = None) -> dict:
    """Extract and validate a complete local snapshot set with FFmpeg.

    This compatibility path is intentionally independent of PyAV so it can
    recover from both decoder errors and a crashed native PyAV worker.
    """
    import hashlib
    import io
    import shutil
    import time
    from PIL import Image
    from ..playback.media_tools import find_ffmpeg, find_ffprobe

    source = Path(input_path)
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()
    if not ffmpeg:
        raise FileNotFoundError("FFmpeg was not found; cannot use the snapshot fallback")
    if not ffprobe:
        raise FileNotFoundError("FFprobe was not found; cannot determine snapshot timestamps")

    probe = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "V:0", "-show_entries",
         "format=duration,start_time:stream=codec_type,width,height,duration,start_time,avg_frame_rate",
         "-of", "json", str(source)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=60, check=False,
    )
    if probe.returncode != 0:
        detail = probe.stderr.decode("utf-8", "replace").strip()[-500:]
        raise RuntimeError(f"FFprobe could not inspect {source.name}: {detail or probe.returncode}")
    try:
        probe_data = json.loads(probe.stdout.decode("utf-8", "replace"))
    except Exception as exc:
        raise RuntimeError(f"FFprobe returned invalid metadata for {source.name}") from exc
    streams = probe_data.get("streams") if isinstance(probe_data, dict) else []
    video_stream = next(
        (stream for stream in streams or [] if isinstance(stream, dict) and stream.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        raise RuntimeError(f"FFprobe found no decodable video stream in {source.name}")
    try:
        stream_duration = float(video_stream.get("duration") or 0)
    except (TypeError, ValueError):
        stream_duration = 0.0
    try:
        format_duration = float((probe_data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        format_duration = 0.0
    try:
        duration = stream_duration or format_duration
        source_width = int(video_stream.get("width") or 0)
        source_height = int(video_stream.get("height") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"FFprobe returned invalid video dimensions or duration for {source.name}") from exc
    if duration <= 0 or source_width <= 0 or source_height <= 0:
        raise RuntimeError(f"FFprobe found unusable video dimensions or duration in {source.name}")

    count = max(1, min(100, int(count or 36)))
    cell_width = min(max(160, int(width)), source_width)
    # Keep dimensions even and the scaler argument portable across FFmpeg builds.
    cell_width = max(2, cell_width - (cell_width % 2))
    targets = [duration * (0.02 + 0.96 * i / max(1, count - 1)) for i in range(count)]
    outdir = Path(output_dir)
    outdir.parent.mkdir(parents=True, exist_ok=True)
    build_dir = outdir.with_name(outdir.name + ".ffmpeg.building")
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    signatures: set[bytes] = set()
    completed_indexes: set[int] = set()
    started = time.monotonic()
    errors: dict[int, str] = {}
    extraction_method = "ffmpeg_timestamp_fallback"

    def _report_progress(value: float) -> None:
        if on_progress is None:
            return
        try:
            on_progress(max(0.0, min(1.0, float(value))))
        except Exception:
            _swallowed_note("internal_media._make_snapshot_frames_ffmpeg_direct._report_progress")
            pass

    def valid_signature(path: Path) -> bytes | None:
        try:
            if not path.is_file() or path.stat().st_size < 256:
                return None
            with Image.open(path) as image:
                image.load()
                sample = image.convert("RGB").resize((24, 14), Image.Resampling.BILINEAR)
                return hashlib.sha256(sample.tobytes()).digest()
        except Exception:
            return None

    def _stderr_detail(value) -> str:
        text = value.decode("utf-8", "replace").strip() if isinstance(value, bytes) else str(value or "").strip()
        if len(text) > 900:
            text = text[:350] + "\n…\n" + text[-500:]
        return text

    def _write_png_as_jpeg(png_bytes: bytes, frame_path: Path) -> tuple[bytes | None, str | None]:
        if not png_bytes:
            return None, "FFmpeg returned no PNG bytes"
        try:
            with Image.open(io.BytesIO(png_bytes)) as image:
                image.load()
                image.convert("RGB").save(
                    frame_path, format="JPEG",
                    quality=max(60, min(95, int(quality))),
                    optimize=False, progressive=False,
                )
            signature = valid_signature(frame_path)
            if signature is None:
                return None, "Pillow could not validate the JPEG produced from FFmpeg's PNG"
            return signature, None
        except Exception as exc:
            frame_path.unlink(missing_ok=True)
            return None, f"PNG decode or JPEG conversion failed: {type(exc).__name__}: {str(exc)[:240]}"

    def _extract_sequentially() -> tuple[set[bytes] | None, str]:
        """Decode one frame pass when random seeking returns no usable frames."""
        import threading

        sequence_dir = outdir.with_name(outdir.name + ".ffmpeg-sequential.building")
        png_dir = sequence_dir / "png"
        jpeg_dir = sequence_dir / "jpg"
        shutil.rmtree(sequence_dir, ignore_errors=True)
        png_dir.mkdir(parents=True, exist_ok=True)
        jpeg_dir.mkdir(parents=True, exist_ok=True)
        fps = count / duration
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
            "-i", str(source), "-map", "0:V:0", "-an", "-sn", "-dn",
            "-vf", f"setpts=PTS-STARTPTS,fps={fps:.12g},scale={cell_width}:-2:flags=lanczos,format=rgb24",
            "-frames:v", str(count), "-c:v", "png", "-pix_fmt", "rgb24",
            "-start_number", "1", "-y", str(png_dir / "frame_%02d.png"),
        ]
        try:
            completed_indexes.clear()
            _report_progress(0.0)
            progress_stop = threading.Event()

            def _heartbeat() -> None:
                while not progress_stop.wait(SNAPSHOT_PROGRESS_HEARTBEAT_SECONDS):
                    # FFmpeg is still decoding, but no JPEG has been validated yet.
                    _report_progress(0.0)

            progress_thread = threading.Thread(
                target=_heartbeat, name="snapshot-ffmpeg-heartbeat", daemon=True,
            )
            if on_progress is not None:
                progress_thread.start()
            try:
                result = subprocess.run(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=900, check=False,
                )
            finally:
                progress_stop.set()
                if on_progress is not None:
                    progress_thread.join(timeout=2.0)
            png_paths = sorted(png_dir.glob("frame_*.png"))
            if len(png_paths) < count:
                detail = _stderr_detail(result.stderr)
                return None, (
                    f"sequential FFmpeg pass produced {len(png_paths)}/{count} PNG frames "
                    f"(exit {result.returncode})" + (f": {detail}" if detail else "")
                )

            sequence_signatures: set[bytes] = set()
            for index, png_path in enumerate(png_paths[:count]):
                try:
                    with Image.open(png_path) as image:
                        image.load()
                        image.convert("RGB").save(
                            jpeg_dir / f"frame_{index + 1:02d}.jpg", format="JPEG",
                            quality=max(60, min(95, int(quality))),
                            optimize=False, progressive=False,
                        )
                except Exception as exc:
                    return None, f"sequential PNG {index + 1} conversion failed: {type(exc).__name__}: {str(exc)[:240]}"
                signature = valid_signature(jpeg_dir / f"frame_{index + 1:02d}.jpg")
                if signature is None:
                    return None, f"sequential JPEG {index + 1} failed validation"
                sequence_signatures.add(signature)
                _report_progress((index + 1) / count)

            if len(sequence_signatures) < min(4, count):
                return None, f"sequential pass produced only {len(sequence_signatures)} distinct frames"
            shutil.rmtree(build_dir, ignore_errors=True)
            os.replace(str(jpeg_dir), str(build_dir))
            return sequence_signatures, ""
        except Exception as exc:
            return None, f"sequential FFmpeg pass failed: {type(exc).__name__}: {str(exc)[:400]}"
        finally:
            shutil.rmtree(sequence_dir, ignore_errors=True)

    try:
        for index, target in enumerate(targets):
            frame_path = build_dir / f"frame_{index + 1:02d}.jpg"
            last_error = ""
            for attempt in range(2):
                frame_path.unlink(missing_ok=True)
                if attempt == 0:
                    # Fast input-side seek for the common MP4 case.
                    seek_args = ["-ss", f"{target:.6f}", "-i", str(source)]
                    seek_label = "fast-seek"
                else:
                    # Some HEVC/MP4 streams have unusual timestamps. Anchor a
                    # few seconds before the sample then decode accurately.
                    anchor = max(0.0, target - 3.0)
                    seek_args = [
                        "-ss", f"{anchor:.6f}", "-i", str(source),
                        "-ss", f"{target - anchor:.6f}",
                    ]
                    seek_label = "accurate-seek-retry"
                command = [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-threads", "1", *seek_args, "-map", "0:V:0",
                    "-frames:v", "1", "-an", "-sn", "-dn",
                    "-vf", f"scale={cell_width}:-2:flags=lanczos",
                    # Windows FFmpeg builds can fail to initialize MJPEG
                    # (encoder error -22). PNG image2pipe is more portable;
                    # Pillow converts that single frame to our cached JPEG.
                    "-pix_fmt", "rgb24", "-f", "image2pipe", "-c:v", "png", "pipe:1",
                ]
                result = subprocess.run(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=120, check=False,
                )
                signature, image_error = (
                    _write_png_as_jpeg(result.stdout, frame_path) if result.returncode == 0 else
                    (None, f"FFmpeg exited with code {result.returncode}")
                )
                if signature is not None:
                    signatures.add(signature)
                    completed_indexes.add(index)
                    last_error = ""
                    break
                detail = _stderr_detail(result.stderr)
                last_error = f"{seek_label}: {image_error or 'no valid image'}"
                if detail:
                    last_error += f"; ffmpeg: {detail}"
            if last_error:
                errors[index] = last_error
            _report_progress(len(completed_indexes) / count)

        missing = [index for index in range(count) if valid_signature(build_dir / f"frame_{index + 1:02d}.jpg") is None]
        sequential_error = ""
        if missing or len(signatures) < min(4, count):
            sequential_signatures, sequential_error = _extract_sequentially()
            if sequential_signatures is not None:
                signatures = sequential_signatures
                completed_indexes = set(range(count))
                errors.clear()
                extraction_method = "ffmpeg_sequential_png_fallback"
                missing = []
                _report_progress(1.0)
            else:
                missing = [
                    index for index in range(count)
                    if valid_signature(build_dir / f"frame_{index + 1:02d}.jpg") is None
                ]
        if missing:
            labels = ", ".join(str(index + 1) for index in missing[:10])
            if len(missing) > 10:
                labels += ", …"
            reason = next((errors.get(index) for index in missing if errors.get(index)), "FFmpeg did not produce a valid JPEG")
            if sequential_error:
                reason += f"; {sequential_error}"
            raise RuntimeError(
                f"FFmpeg snapshot fallback incomplete: {count - len(missing)}/{count} valid frames; "
                f"missing indexes: {labels}; last error: {reason}"
            )
        if len(signatures) < min(4, count):
            raise RuntimeError(f"FFmpeg snapshot fallback yielded only {len(signatures)} visually distinct frames")

        backup_dir = outdir.with_name(outdir.name + ".ffmpeg.previous")
        shutil.rmtree(backup_dir, ignore_errors=True)
        had_previous = outdir.exists()
        if had_previous:
            os.replace(str(outdir), str(backup_dir))
        try:
            os.replace(str(build_dir), str(outdir))
        except Exception:
            if had_previous and backup_dir.exists() and not outdir.exists():
                os.replace(str(backup_dir), str(outdir))
            raise
        else:
            shutil.rmtree(backup_dir, ignore_errors=True)
        if on_progress is not None:
            try:
                on_progress(1.0)
            except Exception:
                _swallowed_note("internal_media._make_snapshot_frames_ffmpeg_direct")
                pass
        return {
            "frames_path": str(outdir), "frames": count, "distinct_frames": len(signatures),
            "width": source_width, "height": source_height, "duration": duration,
            "workers": 1, "codec_threads_per_worker": 1,
            "extraction_method": extraction_method,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise


def _make_snapshot_frames_direct(input_path: str | Path, output_dir: str | Path, *,
                                 count: int = 36, width: int = 640, quality: int = 92,
                                 on_progress: Optional[Callable[[float], None]] = None) -> dict:
    """Retry PyAV extraction once, then fall back to FFmpeg."""
    pyav_error = None

    def report_progress(value: float) -> None:
        if on_progress is None:
            return
        try:
            on_progress(max(0.0, min(1.0, float(value))))
        except Exception:
            _swallowed_note("internal_media._make_snapshot_frames_direct.report_progress")
            pass

    for attempt in range(2):
        report_progress(0.0)
        try:
            result = _make_snapshot_frames_pyav_direct(
                input_path, output_dir, count=count, width=width, quality=quality,
                on_progress=on_progress,
            )
            report_progress(1.0)
            return result
        except Exception as exc:
            pyav_error = exc
            try:
                print(
                    f"PyAV snapshot attempt {attempt + 1}/2 failed for {Path(input_path).name}: "
                    f"{type(exc).__name__}: {exc}; "
                    + ("retrying PyAV" if attempt == 0 else "trying FFmpeg fallback"),
                    file=sys.stderr, flush=True,
                )
            except Exception:
                _swallowed_note("internal_media._make_snapshot_frames_direct")
                pass
    report_progress(0.0)
    try:
        result = _make_snapshot_frames_ffmpeg_direct(
            input_path, output_dir, count=count, width=width, quality=quality,
            on_progress=on_progress,
        )
        result["fallback_reason"] = f"{type(pyav_error).__name__}: {pyav_error}"[:500]
        report_progress(1.0)
        return result
    except Exception as ffmpeg_error:
        raise RuntimeError(
            f"Snapshot extraction failed after 2 PyAV attempts "
            f"({type(pyav_error).__name__}: {pyav_error}); FFmpeg fallback failed "
            f"({type(ffmpeg_error).__name__}: {ffmpeg_error})"
        ) from ffmpeg_error


# ---------------------------------------------------------------------------
# Native-process boundary
# ---------------------------------------------------------------------------
# PyAV is a native extension. A Windows access violation inside its FFmpeg
# DLLs cannot be caught by Python and would otherwise terminate the whole web
# application. Public operations therefore execute in a short-lived child
# process. The child is the only place that imports/executes PyAV. Set by the
# worker itself so the direct implementations are used there without recursion.
_NATIVE_WORKER_ENV = "TELEGRAM_ARCHIVE_NATIVE_WORKER"




# ==============================================================================
# SECTION: Native-process boundary
# Decides direct versus worker execution and runs the worker.
# ==============================================================================

def _native_direct_mode() -> bool:
    return os.environ.get(_NATIVE_WORKER_ENV) == "1"


def _worker_cmd(operation: str, payload: dict) -> list[str]:
    args = [operation, json.dumps(payload, separators=(",", ":"))]
    if getattr(sys, "frozen", False):
        # The installed backend ignores "-m" and would start a second copy of the app; entry.py answers this flag instead.
        return [sys.executable, "--ax-native-worker", *args]
    return [sys.executable, "-m", "telegram_archive.core.native_media_worker", *args]


_WORKER_TIMEOUTS = {"probe": 120, "thumbnail": 300, "snapshot_frames": 900}


def _run_worker(operation: str, payload: dict, on_progress: Optional[Callable[[float], None]] = None,
                timeout: Optional[float] = None) -> dict:
    """Run one native media operation in a child process.

    stderr is drained on a background thread: FFmpeg can be chatty, and if
    the pipe buffer filled up while only stdout was read, the child would
    block forever. Short operations also get a hard time limit so a stuck
    file can never leave orphan worker processes behind.
    """
    import threading
    import time as _time

    if timeout is None:
        timeout = _WORKER_TIMEOUTS.get(operation)
    env = os.environ.copy()
    env[_NATIVE_WORKER_ENV] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        _worker_cmd(operation, payload), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env, creationflags=creationflags,
    )
    from .child_processes import tracked as _tracked
    _track = _tracked(proc, f"PyAV worker · {operation}", kind="pyav")
    _handle = _track.__enter__()
    stderr_chunks: list[str] = []

    def _drain_stderr():
        try:
            assert proc.stderr is not None
            for chunk in proc.stderr:
                stderr_chunks.append(chunk)
                if len(stderr_chunks) > 400:
                    del stderr_chunks[:200]
        except Exception:
            _swallowed_note("internal_media._run_worker._drain_stderr")
            pass

    drain = threading.Thread(target=_drain_stderr, daemon=True, name=f"media-worker-{operation}-stderr")
    drain.start()
    timed_out = [False]
    watchdog = None
    if timeout:
        def _kill():
            if proc.poll() is None:
                timed_out[0] = True
                try:
                    proc.kill()
                except Exception:
                    _swallowed_note("internal_media._run_worker._kill")
                    pass
        watchdog = threading.Timer(float(timeout), _kill)
        watchdog.daemon = True
        watchdog.start()
    result = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                _swallowed_note("internal_media._run_worker")
                continue
            if item.get("type") == "progress":
                _handle.set_progress(float(item.get("value") or 0.0))
                if on_progress:
                    try:
                        on_progress(float(item.get("value") or 0.0))
                    except Exception:
                        _swallowed_note("internal_media._run_worker")
                        pass
            elif item.get("type") == "result":
                result = item.get("value")
        rc = proc.wait()
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(5)
            except Exception:
                _swallowed_note("internal_media._run_worker")
                pass
        drain.join(5)
        _track.__exit__(None, None, None)
    if timed_out[0]:
        raise RuntimeError(f"PyAV worker '{operation}' took longer than {int(timeout)}s and was stopped")
    if rc != 0 or result is None:
        detail = "".join(stderr_chunks).strip() or "native media worker failed"
        raise RuntimeError(f"PyAV worker exited with code {rc}: {detail[-1500:]}")
    return result




# ==============================================================================
# SECTION: Public API
# Probe, remux, transcode, snapshot, thumbnail and contact sheet entry points.
# ==============================================================================

def probe(path: str | Path) -> dict:
    if _native_direct_mode():
        return _probe_direct(path)
    return _run_worker("probe", {"path": str(path)})


def remux(input_path: str | Path, output_path: str | Path) -> dict:
    if _native_direct_mode():
        return _remux_direct(input_path, output_path)
    return _run_worker("remux", {"input_path": str(input_path), "output_path": str(output_path)})


def transcode(input_path: str | Path, output_path: str | Path, *, preserve_video: bool = True,
              on_progress: Optional[Callable[[float], None]] = None) -> dict:
    if _native_direct_mode():
        return _transcode_direct(input_path, output_path, preserve_video=preserve_video, on_progress=on_progress)
    return _run_worker(
        "transcode",
        {"input_path": str(input_path), "output_path": str(output_path), "preserve_video": bool(preserve_video)},
        on_progress=on_progress,
    )


def make_snapshot_frames(input_path: str | Path, output_dir: str | Path, *, count: int = 36,
                         width: int = 640, quality: int = 92,
                         on_progress: Optional[Callable[[float], None]] = None) -> dict:
    """Create individual snapshots using indexed GOP decoding where possible.

    ``on_progress`` reports the validated-frame fraction for the active attempt
    (0 to 1) and may reset when a retry or fallback starts. PyAV is retried once;
    FFmpeg can recover from decoder errors and from a native worker crash without
    importing PyAV in the app process.
    """
    if _native_direct_mode():
        return _make_snapshot_frames_direct(input_path, output_dir, count=count, width=width,
                                            quality=quality, on_progress=on_progress)
    payload = {
        "input_path": str(input_path), "output_dir": str(output_dir),
        "count": int(count), "width": int(width), "quality": int(quality),
    }
    worker_errors = []

    def report_progress(value: float) -> None:
        if on_progress is None:
            return
        try:
            on_progress(max(0.0, min(1.0, float(value))))
        except Exception:
            _swallowed_note("internal_media.make_snapshot_frames.report_progress")
            pass

    for attempt in range(2):
        report_progress(0.0)
        try:
            result = _run_worker(
                "snapshot_frames", payload,
                on_progress=on_progress, timeout=900,
            )
            report_progress(1.0)
            return result
        except Exception as exc:
            worker_errors.append(f"{type(exc).__name__}: {exc}")
            # A normal worker exception means the child already retried PyAV
            # and attempted its FFmpeg fallback. Re-running the whole child
            # would repeat hours of decoding and obscure the useful error.
            # Retry only when the native worker itself crashed before that
            # managed recovery path could complete.
            recovery_exhausted = "Snapshot extraction failed after 2 PyAV attempts" in str(exc)
            retry_worker = attempt == 0 and not recovery_exhausted
            try:
                print(
                    f"Snapshot worker attempt {attempt + 1}/2 failed for {Path(input_path).name}: "
                    f"{worker_errors[-1]}; "
                    + ("retrying isolated worker" if retry_worker else "using parent-process FFmpeg fallback"),
                    file=sys.stderr, flush=True,
                )
            except Exception:
                _swallowed_note("internal_media.make_snapshot_frames")
                pass
            if not retry_worker:
                break
    report_progress(0.0)
    try:
        result = _make_snapshot_frames_ffmpeg_direct(
            input_path, output_dir, count=count, width=width, quality=quality,
            on_progress=on_progress,
        )
        result["fallback_reason"] = " | ".join(worker_errors)[-1000:]
        result["extraction_method"] = "ffmpeg_native_worker_fallback"
        report_progress(1.0)
        return result
    except Exception as exc:
        raise RuntimeError(
            f"Snapshot worker failed twice ({' | '.join(worker_errors)[-1000:]}); "
            f"FFmpeg fallback failed ({type(exc).__name__}: {exc})"
        ) from exc


def make_thumbnail(input_path: str | Path, output_path: str | Path, timestamp: Optional[float] = None,
                   width: int = 1536, height: int = 864) -> Path:
    if _native_direct_mode():
        return _make_thumbnail_direct(input_path, output_path, timestamp=timestamp, width=width, height=height)
    _run_worker("thumbnail", {
        "input_path": str(input_path), "output_path": str(output_path),
        "timestamp": timestamp, "width": int(width), "height": int(height),
    })
    return Path(output_path)


CONTACT_SHEET_MARKER = b"ArchiveX-36-frame-contact-sheet-v6"


def make_contact_sheet(frames_dir: str | Path, output_path: str | Path, *,
                       filename: str = "video", properties: dict | None = None,
                       rows: int = 6, cols: int = 6, width: int = 1920,
                       quality: int = 90) -> Path:
    """Compose a clean, white-background 6x6 storyboard from 36 snapshots.

    Each frame receives a readable HH:MM:SS timecode badge. The three-line
    header shows the filename and concise technical metadata. Source frames
    remain untouched; this image is only the Telegram delivery contact sheet.
    """
    import hashlib
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    if int(rows) != 6 or int(cols) != 6:
        raise ValueError("Archive X Telegram contact sheets require a 6x6 (36-frame) grid")
    base = Path(frames_dir)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    props = dict(properties or {})
    frame_paths = [base / f"frame_{i:02d}.jpg" for i in range(1, 37)]
    if not all(p.is_file() and p.stat().st_size >= 256 for p in frame_paths):
        raise RuntimeError("A complete 36-frame snapshot set is required for Telegram contact-sheet delivery")

    images = []
    fingerprints = set()
    try:
        for p in frame_paths:
            with Image.open(p) as frame:
                frame.verify()
            with Image.open(p) as frame:
                image = frame.convert("RGB")
                sample = image.resize((24, 14), Image.Resampling.BILINEAR)
                fingerprints.add(hashlib.sha256(sample.tobytes()).digest())
                images.append(image.copy())
                sample.close()
    except Exception as exc:
        for image in images:
            image.close()
        raise RuntimeError(f"Contact-sheet frame is unreadable: {type(exc).__name__}: {exc}") from exc
    if len(images) != 36 or len(fingerprints) < 4:
        for image in images:
            image.close()
        raise RuntimeError(f"Contact-sheet frame set is not diverse enough ({len(fingerprints)} distinct)")

    width = max(1200, min(2400, int(width or 1920)))
    pad, gap = 20, 8
    cell_w = (width - 2 * pad - (cols - 1) * gap) // cols
    cell_h = max(90, int(round(cell_w * 9 / 16)))
    header_h = 128
    sheet_h = header_h + 2 * pad + rows * cell_h + (rows - 1) * gap
    # Use an actual white canvas so the sheet reads cleanly when displayed in
    # Telegram, a browser, or a desktop image viewer.
    canvas = Image.new("RGB", (width, sheet_h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    def font(size: int, bold: bool = False):
        candidates = (
            ([r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
             [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"])
        )
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
            except Exception:
                _swallowed_note("internal_media.make_contact_sheet.font")
                continue
        return ImageFont.load_default()

    title_font = font(25, True)
    metadata_font = font(16, False)
    stamp_font = font(15, True)

    def text_width(text: str, selected_font) -> int:
        box = draw.textbbox((0, 0), text, font=selected_font)
        return int(box[2] - box[0])

    def fit_line(text: str, selected_font, max_width: int) -> str:
        value = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        if text_width(value, selected_font) <= max_width:
            return value
        ellipsis = "…"
        while value and text_width(value + ellipsis, selected_font) > max_width:
            value = value[:-1].rstrip()
        return (value + ellipsis) if value else ellipsis

    def as_float(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def as_int(value) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def duration_text(seconds: float) -> str:
        total = max(0, int(seconds))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    def size_text(value) -> str:
        if isinstance(value, str):
            raw = value.strip()
            if any(unit in raw.lower() for unit in ("kb", "mb", "gb", "bytes")):
                return raw
        size = as_int(value)
        if size <= 0:
            return "Unknown"
        if size >= 1_000_000_000:
            return f"{size / 1_000_000_000:.2f} GB"
        if size >= 1_000_000:
            return f"{size / 1_000_000:.2f} MB"
        if size >= 1_000:
            return f"{size / 1_000:.1f} KB"
        return f"{size} bytes"

    def bitrate_text(value) -> str:
        if isinstance(value, str):
            raw = value.strip()
            if any(unit in raw.lower() for unit in ("kb/s", "kbps", "mb/s", "mbps")):
                return raw.replace("kbps", "kb/s").replace("Mbps", "Mb/s").replace("mbps", "Mb/s")
        bitrate = as_float(value)
        if bitrate <= 0:
            return "Unknown"
        # Probe/DB values are commonly bps. Small integers are already kb/s.
        kbps = bitrate / 1000.0 if bitrate >= 100_000 else bitrate
        return f"{kbps:,.0f} kb/s"

    duration = as_float(props.get("duration"))
    file_size = props.get("size", props.get("file_size", 0))
    w, h = as_int(props.get("width")), as_int(props.get("height"))
    codec = str(props.get("video_codec") or props.get("codec") or "Unknown").strip()
    codec_key = codec.casefold().replace(" ", "")
    codec_labels = {"h264": "H.264", "avc1": "H.264", "hevc": "HEVC / H.265",
                    "h265": "HEVC / H.265", "mpeg4": "MPEG-4", "av1": "AV1", "vp9": "VP9"}
    video_label = codec_labels.get(codec_key, codec.upper() if codec_key not in {"", "unknown"} else "Unknown")
    audio = str(props.get("audio_codec") or "Unknown").strip()
    audio_key = audio.casefold().replace(" ", "")
    audio_label = {"aac": "AAC", "mp3": "MP3", "ac3": "AC-3", "eac3": "E-AC-3",
                   "opus": "Opus", "flac": "FLAC", "truehd": "TrueHD", "dts": "DTS"}.get(audio_key, audio.upper())
    fps = as_float(props.get("fps"))
    resolution = f"{w:,} × {h:,}" if w > 0 and h > 0 else "Unknown"
    fps_label = f"{fps:.2f} fps" if fps > 0 else "Unknown"
    title = fit_line(f"File name: {Path(str(filename or 'video')).name}", title_font, width - 2 * pad)
    row1 = (f"Size: {size_text(file_size)}    •    Duration: {duration_text(duration)}    •    "
            f"Resolution: {resolution}")
    row2 = (f"Video: {video_label}    •    Bitrate: {bitrate_text(props.get('bitrate'))}    •    "
            f"Frame rate: {fps_label}    •    Audio: {audio_label}")
    row1 = fit_line(row1, metadata_font, width - 2 * pad)
    row2 = fit_line(row2, metadata_font, width - 2 * pad)

    draw.text((pad, 12), title, fill=(28, 32, 36), font=title_font)
    draw.text((pad, 52), row1, fill=(65, 71, 78), font=metadata_font)
    draw.text((pad, 79), row2, fill=(65, 71, 78), font=metadata_font)
    draw.line((pad, 111, width - pad, 111), fill=(224, 228, 232), width=2)

    for index, image in enumerate(images):
        row, col = divmod(index, cols)
        x = pad + col * (cell_w + gap)
        y = header_h + pad + row * (cell_h + gap)
        tile = Image.new("RGB", (cell_w, cell_h), (245, 247, 249))
        # Preserve the complete frame; letterbox only when the source aspect
        # ratio is different from 16:9 instead of cropping away scene detail.
        contained = ImageOps.contain(image, (cell_w, cell_h), method=Image.Resampling.LANCZOS)
        tile.paste(contained, ((cell_w - contained.width) // 2, (cell_h - contained.height) // 2))
        canvas.paste(tile, (x, y))
        draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline=(222, 226, 230), width=1)

        # The displayed time follows the same 2%-to-98% sample schedule used
        # by Archive X's 36-frame snapshot extractor.
        sample_seconds = duration * (0.02 + 0.96 * index / 35) if duration > 0 else 0
        stamp = duration_text(sample_seconds)
        tw = text_width(stamp, stamp_font)
        label_x, label_y = x + 9, y + 8
        box = (label_x, label_y, label_x + tw + 14, label_y + 25)
        draw.rounded_rectangle(box, radius=5, fill=(22, 26, 30), outline=(255, 255, 255), width=1)
        draw.text((label_x + 7, label_y + 3), stamp, fill=(255, 255, 255), font=stamp_font)
        tile.close(); contained.close(); image.close()

    try:
        canvas.save(out, "JPEG", quality=max(76, min(95, int(quality))), optimize=True,
                    progressive=True, comment=CONTACT_SHEET_MARKER)
    finally:
        canvas.close()
    if not out.is_file() or out.stat().st_size < 512:
        raise RuntimeError("Contact-sheet encoding produced an empty image")
    return out
