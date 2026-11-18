"""ffmpeg helpers for the web player (v18.8).

* find_ffmpeg()/find_ffprobe(): also finds a winget/zip install that is not on
  the PATH of the console that started the app (a common reason why the
  browser "compatibility" fallback silently did nothing).
* probe(): codec/duration information, cached in memory and in SQLite.
* best_h264_encoder(): uses the graphics card (NVIDIA / Intel / AMD) for the
  compatibility conversion when it works, otherwise the CPU (libx264).
* compat_command(): one continuous fragmented-MP4 stream starting at t0
  (desktop/Android browsers).
* hls_segment_command(): one independent 6-second MPEG-TS piece (iPhone/iPad,
  which only accept seekable HLS for converted video).
* preview_frame(): small JPEG of the key-frame at a position, used for the
  seek-bar thumbnails. Cached on disk.

Nothing here talks to Telegram directly: every input is a signed
http://127.0.0.1 URL of the shared playback engine.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Tool discovery
#   - Probing
#   - Encoders and compatibility plans
#   - FFmpeg commands
#   - Previews
#   - Preview cleanup
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import glob
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Optional

from .. import config
from ..core.external_tools import find_tool

PREVIEW_DIR = config.PLAYER_CACHE_DIR / "previews"

# Interactive player-preview index/cache. The browser playback path already
# owns a sparse Telegram range cache; live preview reuses that cache instead
# of starting FFmpeg against the localhost stream for every hover position.
_DIRECT_PREVIEW_INDEX_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_DIRECT_PREVIEW_INDEX_LOCK = threading.RLock()
_DIRECT_PREVIEW_INDEX_TTL = 30 * 60
_DIRECT_PREVIEW_INDEX_MAX = 8

HLS_SEGMENT_SECONDS = 2.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# Codecs every current browser decodes.
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}
MP4_COPY_AUDIO = {"aac", "mp3"}


# ---------------------------------------------------------------- discovery



# ==============================================================================
# SECTION: Tool discovery
# Finds ffmpeg and ffprobe.
# ==============================================================================


_found: dict[str, str] = {}


def _find(name: str) -> Optional[str]:
    cached = _found.get(name)
    if cached and os.path.exists(cached):
        return cached
    path = find_tool(name)
    if path:
        _found[name] = path
    return path


def find_ffmpeg() -> Optional[str]:
    return _find("ffmpeg")


def find_ffprobe() -> Optional[str]:
    path = _find("ffprobe")
    if not path:
        ff = find_ffmpeg()
        if ff:
            alt = os.path.join(os.path.dirname(ff), "ffprobe" + (".exe" if ff.lower().endswith(".exe") else ""))
            if os.path.exists(alt):
                path = alt
                _found["ffprobe"] = alt
    return path


async def _run(cmd: list[str], timeout: float, want_stdout=True) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if want_stdout else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, creationflags=_NO_WINDOW)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except Exception:
            _swallowed_note("media_tools._run")
            pass
        raise
    return proc.returncode, out or b"", err or b""


# ---------------------------------------------------------------- probing



# ==============================================================================
# SECTION: Probing
# Probe summaries and browser verdicts.
# ==============================================================================

def summarize_probe(raw: dict) -> dict:
    fmt = raw.get("format") or {}
    v = a = None
    for s in raw.get("streams") or []:
        kind = s.get("codec_type")
        disposition = s.get("disposition") or {}
        if kind == "video" and v is None and not disposition.get("attached_pic"):
            v = s
        elif kind == "audio" and a is None:
            a = s
    try:
        duration = float(fmt.get("duration") or (v or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    fps = 0.0
    try:
        num, den = str((v or {}).get("avg_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        _swallowed_note("media_tools.summarize_probe")
        pass
    return {
        "duration": round(duration, 3),
        "format": fmt.get("format_name") or "",
        "bit_rate": int(fmt.get("bit_rate") or 0) if str(fmt.get("bit_rate") or "").isdigit() else 0,
        "vcodec": (v or {}).get("codec_name") or "",
        "vprofile": (v or {}).get("profile") or "",
        "vlevel": int((v or {}).get("level") or 0) if str((v or {}).get("level") or "").lstrip("-").isdigit() else 0,
        "pix_fmt": (v or {}).get("pix_fmt") or "",
        "width": int((v or {}).get("width") or 0),
        "height": int((v or {}).get("height") or 0),
        "fps": round(fps, 3),
        "acodec": (a or {}).get("codec_name") or "",
        "aprofile": (a or {}).get("profile") or "",
        "achannels": int((a or {}).get("channels") or 0),
        "probed_at": int(time.time()),
    }


def browser_verdict(info: dict, mime: str = "") -> dict:
    """What a typical desktop browser can do with this file. The browser
    double-checks with canPlayType() (e.g. HEVC works in Edge/Safari)."""
    fmt = (info.get("format") or "").lower()
    vc = (info.get("vcodec") or "").lower()
    ac = (info.get("acodec") or "").lower()
    pix = (info.get("pix_fmt") or "").lower()
    ten_bit = "10" in pix or "12" in pix
    container_ok = any(x in fmt for x in ("mp4", "mov", "webm", "matroska")) or \
        (mime or "").lower() in ("video/mp4", "video/webm", "video/quicktime", "video/x-matroska")
    video_ok = vc in BROWSER_VIDEO and not (vc == "h264" and ten_bit)
    audio_ok = (not ac) or ac in BROWSER_AUDIO
    return {"container_ok": bool(container_ok), "video_ok": bool(video_ok), "audio_ok": bool(audio_ok),
            "hevc": vc in ("hevc", "h265"), "ten_bit": ten_bit,
            "matroska": "matroska" in fmt, "direct_likely": bool(container_ok and video_ok and audio_ok)}


async def probe(url: str, timeout: float = 40.0) -> dict:
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise FileNotFoundError("ffprobe was not found")
    rc, out, err = await _run([ffprobe, "-v", "error", "-print_format", "json",
                               "-show_format", "-show_streams", url], timeout)
    if rc != 0 and not out:
        raise RuntimeError((err.decode("utf-8", "ignore") or "ffprobe failed").strip()[-300:])
    return summarize_probe(json.loads(out.decode("utf-8", "ignore") or "{}"))


# ---------------------------------------------------------------- encoders

_encoder: Optional[str] = None
_encoder_lock: Optional[asyncio.Lock] = None




# ==============================================================================
# SECTION: Encoders and compatibility plans
# H.264 encoder choice and compat planning.
# ==============================================================================

def _encoder_args(encoder: str, height: int, fps: float) -> list[str]:
    h = height or 1080
    if h > 1080:
        h = 1080
    kbps = 6000 if h >= 1000 else 3500 if h >= 700 else 2000
    gop = str(int(round(max(fps or 30.0, 10.0) * 2)))
    rate = ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "fast", "-pix_fmt", "yuv420p", "-g", gop, *rate]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "faster", "-pix_fmt", "nv12", "-g", gop, *rate]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-pix_fmt", "yuv420p", "-g", gop, *rate]
    return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "22", "-pix_fmt", "yuv420p",
            "-g", gop, "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"]


async def best_h264_encoder() -> str:
    """Pick the fastest H.264 encoder that really works on this PC."""
    global _encoder, _encoder_lock
    if _encoder:
        return _encoder
    if _encoder_lock is None:
        _encoder_lock = asyncio.Lock()
    async with _encoder_lock:
        if _encoder:
            return _encoder
        ffmpeg = find_ffmpeg()
        chosen = "libx264"
        if ffmpeg:
            try:
                rc, out, _ = await _run([ffmpeg, "-hide_banner", "-encoders"], 15)
                listed = out.decode("utf-8", "ignore")
            except Exception:
                listed = ""
            for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
                if enc not in listed:
                    continue
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                       "testsrc2=size=640x360:rate=30:duration=1", *_encoder_args(enc, 360, 30),
                       "-f", "null", "-"]
                try:
                    rc, _, _ = await _run(cmd, 20, want_stdout=False)
                except Exception:
                    rc = 1
                if rc == 0:
                    chosen = enc
                    break
        _encoder = chosen
        return chosen


def _scale_filter(info: dict) -> list[str]:
    h = int(info.get("height") or 0)
    if h > 1080:
        return ["-vf", "scale=-2:1080"]
    return []


def plan_compat(info: dict) -> dict:
    """Which streams can be copied unchanged and which must be converted."""
    vc = (info.get("vcodec") or "").lower()
    pix = (info.get("pix_fmt") or "").lower()
    ac = (info.get("acodec") or "").lower()
    copy_video = vc == "h264" and pix in ("yuv420p", "yuvj420p", "")
    copy_audio = ac in MP4_COPY_AUDIO
    return {"copy_video": copy_video, "copy_audio": copy_audio, "has_audio": bool(ac)}


def resolve_mse_seek(target_time: float, *, copy_video: bool, keyframes=None,
                     source_size: int | None = None, max_keyframe_gap: float = 30.0) -> dict:
    """Select an indexed keyframe or an accurate transcode for MSE seeking.

    Returns the FFmpeg input seek anchor and the absolute timestamp represented
    by local MSE time zero. Copied MP4 fragments may retain a small non-zero first
    PTS; the origin is adjusted using the first known keyframe so player time stays
    aligned with source time. Missing/untrusted indexes use accurate transcode.
    """
    try: target = max(0.0, float(target_time))
    except (TypeError, ValueError, OverflowError): target = 0.0
    try: max_gap = max(0.0, float(max_keyframe_gap))
    except (TypeError, ValueError, OverflowError): max_gap = 30.0
    size = max(0, int(source_size or 0))
    valid = []
    for raw in keyframes or []:
        try: timestamp, offset = float(raw[0]), int(raw[1])
        except (TypeError, ValueError, IndexError, OverflowError): continue
        if math.isfinite(timestamp) and timestamp >= 0 and offset >= 0 and (not size or offset < size):
            valid.append((timestamp, offset))
    if target <= 0.02:
        return {"stream_start": target, "timeline_origin": target, "force_transcode_video": False, "mode": "initial"}
    if not copy_video:
        return {"stream_start": target, "timeline_origin": target, "force_transcode_video": False, "mode": "accurate-transcode"}
    before = [item for item in valid if item[0] <= target + 0.001]
    if not before:
        return {"stream_start": target, "timeline_origin": target, "force_transcode_video": True, "mode": "accurate-transcode-no-index"}
    anchor_time = max(before, key=lambda item: (item[0], item[1]))[0]
    if target - anchor_time > max_gap:
        return {"stream_start": target, "timeline_origin": target, "force_transcode_video": True, "mode": "accurate-transcode-long-gop"}
    first_pts = min((item[0] for item in valid), default=0.0)
    return {"stream_start": anchor_time, "timeline_origin": max(0.0, anchor_time - first_pts),
            "force_transcode_video": False, "mode": "indexed-keyframe"}




# ==============================================================================
# SECTION: FFmpeg commands
# Compat and HLS command lines.
# ==============================================================================

def hwaccel_input_args(hardware_profile: dict | None, encoder: str = "") -> list[str]:
    """Build the native GPU decode side of the MPC-style compatibility path.

    On Windows, MPC-HC/MPCVR uses D3D11 decoding/rendering. Archive X cannot
    embed MPCVR into an HTML video element, but it can keep decoded frames on
    the GPU and hand them directly to a hardware H.264 encoder where FFmpeg's
    backend supports that surface path. This avoids an unnecessary GPU→CPU
    round-trip. If the encoder/surface pair is not one of the verified paths,
    omit the output format and let FFmpeg perform its normal transfer/fallback.
    """
    profile = hardware_profile or {}
    accel_info = profile.get("hardware_acceleration") or {}
    accel = accel_info.get("preferred_transcode_decoder") or accel_info.get("preferred_decoder")
    if accel not in {"cuda", "qsv", "d3d11va"}:
        return []
    args = ["-hwaccel", accel]
    if accel == "cuda" and encoder == "h264_nvenc":
        # CUDA decode -> NVENC encode: keep the decoded frames on the GPU.
        args += ["-hwaccel_output_format", "cuda"]
    elif accel == "qsv" and encoder == "h264_qsv":
        # QSV is FFmpeg's native zero-copy Windows transcoding path.
        args += ["-hwaccel_output_format", "qsv"]
    elif accel == "d3d11va" and encoder in {"h264_nvenc", "h264_amf"}:
        # Native D3D11 surfaces are consumable by the Windows GPU encoders.
        args += ["-hwaccel_output_format", "d3d11"]
    elif accel == "d3d11va" and encoder == "libx264":
        # MPC-HC/LAV-style decode path: decode HEVC through D3D11VA and let
        # FFmpeg transfer frames back to system memory for the CPU H.264
        # encoder. This is intentionally supported as a stable fallback when
        # QSV/NVENC/AMF initialization is broken on a specific driver.
        pass
    return args

def compat_command(ffmpeg: str, url: str, info: dict, t0: float, encoder: str, hardware_profile: dict | None = None, *, force_transcode_video: bool = False, force_transcode_audio: bool = False) -> list[str]:
    """Continuous fragmented MP4 from position t0 (seconds).

    ``force_transcode_video`` enables accurate decode/discard when copying from
    an unknown keyframe would violate the preview timestamp selected by the user.
    """
    plan = plan_compat(info)
    if force_transcode_video:
        plan["copy_video"] = False
    if force_transcode_audio:
        plan["copy_audio"] = False
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "4"]
    if t0 > 0:
        cmd += ["-ss", f"{t0:.3f}"]
    cmd += (hwaccel_input_args(hardware_profile, encoder) if encoder != "copy" else [])
    cmd += ["-i", url, "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1"]
    if plan["copy_video"]:
        cmd += ["-c:v", "copy"]
    else:
        cmd += _scale_filter(info) + _encoder_args(encoder, int(info.get("height") or 0), float(info.get("fps") or 0))
        cmd += ["-profile:v", "main", "-level:v", "4.1"]
    if plan["copy_audio"]:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000"]
    cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration", "500000", "-flush_packets", "1",
            "-muxdelay", "0", "-muxpreload", "0", "-f", "mp4", "pipe:1"]
    return cmd


def compat_file_command(ffmpeg: str, url: str, info: dict, output_path: str, encoder: str, hardware_profile: dict | None = None) -> list[str]:
    """Build a persistent local MP4 compatibility conversion.

    Unlike ``compat_command`` this writes one seekable MP4 file to disk. The
    web player can then use ordinary HTTP Range requests against the local
    file, so playback and arbitrary seeking no longer start a fresh FFmpeg
    process or Telegram range read.
    """
    plan = plan_compat(info)
    hw_args = hwaccel_input_args(hardware_profile, encoder) if encoder != "copy" else []
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "4",
           *hw_args,
           "-i", url, "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
           "-map_metadata", "-1"]
    if plan["copy_video"]:
        cmd += ["-c:v", "copy"]
    else:
        cmd += _scale_filter(info) + _encoder_args(encoder, int(info.get("height") or 0), float(info.get("fps") or 0))
    if plan["copy_audio"]:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", "-avoid_negative_ts", "make_zero",
            "-progress", "pipe:1", "-nostats", "-f", "mp4", output_path]
    return cmd


def hls_segment_command(ffmpeg: str, url: str, info: dict, index: int, encoder: str,
                        seg_seconds: float = HLS_SEGMENT_SECONDS, hardware_profile: dict | None = None) -> list[str]:
    start = index * seg_seconds
    fps = float(info.get("fps") or 0) or 30.0
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "4"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += hwaccel_input_args(hardware_profile, encoder)
    cmd += ["-i", url, "-t", f"{seg_seconds:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
            "-sn", "-dn", "-map_metadata", "-1"]
    cmd += _scale_filter(info) + _encoder_args(encoder, int(info.get("height") or 0), fps)
    cmd += ["-force_key_frames", "expr:gte(t,0)",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000",
            "-output_ts_offset", f"{start:.3f}", "-muxdelay", "0", "-muxpreload", "0",
            "-f", "mpegts", "pipe:1"]
    return cmd


def hls_playlist(duration: float, base: str, token_query: str = "",
                 seg_seconds: float = HLS_SEGMENT_SECONDS) -> str:
    count = max(1, int((duration + seg_seconds - 0.001) // seg_seconds))
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-PLAYLIST-TYPE:VOD",
             f"#EXT-X-TARGETDURATION:{int(seg_seconds + 1)}", "#EXT-X-MEDIA-SEQUENCE:0"]
    for i in range(count):
        length = min(seg_seconds, max(0.1, duration - i * seg_seconds))
        lines.append(f"#EXTINF:{length:.3f},")
        lines.append(f"{base}/{i}.ts{token_query}")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- previews



# ==============================================================================
# SECTION: Previews
# Seek preview frames from playback cache or FFmpeg.
# ==============================================================================

def preview_path(channel_id: int, message_id: int, bucket: int, *, width: int = 480, version: int = 3) -> Path:
    """Return a seek-preview cache path that includes rendering parameters.

    Width/version are part of the key so performer/site verification previews
    can never overwrite the browser player's seek-preview image for the same
    time bucket.
    """
    width = max(240, min(int(width), 640))
    version = max(1, int(version))
    return PREVIEW_DIR / f"v{version}" / f"{int(channel_id)}_{int(message_id)}" / f"{int(bucket)}_{width}.jpg"


# V29.4.118: the MP4 keyframe helpers live in the media manager. They were
# referenced here without an import, so the fast cache-backed scrub preview
# always raised NameError and silently fell back to a full FFmpeg decode.
def _mm():
    from ..media_manager import telegram_media_manager as mm
    return mm


def _find_moov_from_window(window, base_offset, total_size):
    return _mm()._find_moov_from_window(window, base_offset, total_size)


def _parse_visual_config(moov):
    return _mm()._parse_visual_config(moov)


def _make_annexb_stream(index, blob, start_sample, end_sample):
    return _mm()._make_annexb_stream(index, blob, start_sample, end_sample)


def _decode_gop_image(index, annexb, target_rank, tw, th):
    return _mm()._decode_gop_image(index, annexb, target_rank, tw, th)


class _PlaybackPreviewRangeSource:
    """Async byte-range source backed by the shared PlaybackEngine cache."""
    def __init__(self, engine, vkey, message, epoch=0, priority=2, kind="preview"):
        self.engine = engine
        self.vkey = (int(vkey[0]), int(vkey[1]))
        self.message = message
        self.size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        self.epoch = int(epoch or 0)
        self.priority = int(priority)
        self.kind = str(kind or "preview")

    async def get_range(self, start: int, length: int) -> bytes:
        start=max(0,int(start)); length=max(0,int(length)); end=min(self.size,start+length)
        if end<=start: return b""
        result=await self.engine.media_resource(self.vkey,self.message).read(start,end-start,priority=self.priority,epoch=self.epoch,kind=self.kind)
        if len(result)!=end-start: raise RuntimeError("shared media resource returned an incomplete preview range")
        return result



async def _walk_moov_playback(source: _PlaybackPreviewRangeSource, max_boxes: int = 24):
    """Find moov by walking top-level MP4 box headers (V29.4.118).

    Each header is a 16-byte read, so this costs one cached segment for the
    file start plus at most one or two more ranges, instead of probing 4-8 MiB
    windows at both ends of the file.
    """
    import struct
    total = int(source.size)
    off = 0
    for _ in range(max(1, int(max_boxes))):
        if off + 8 > total:
            return None
        hdr = await source.get_range(off, min(16, total - off))
        if len(hdr) < 8:
            return None
        size = struct.unpack(">I", hdr[:4])[0]
        typ = bytes(hdr[4:8])
        if size == 1:
            if len(hdr) < 16:
                return None
            size = struct.unpack(">Q", hdr[8:16])[0]
        elif size == 0:
            size = total - off
        if size < 8 or off + size > total:
            return None
        if typ == b"moov":
            if size > 96 * 1024 * 1024:
                return None
            return await source.get_range(off, int(size)), off
        off += int(size)
    return None


async def _load_moov_playback(source: _PlaybackPreviewRangeSource):
    """Locate MP4 moov through the shared playback range/cache layer."""
    total = int(source.size)
    if total <= 0:
        raise RuntimeError("Invalid Telegram video size")
    try:
        walked = await _walk_moov_playback(source)
        if walked:
            return walked
    except asyncio.CancelledError:
        raise
    except Exception:
        _swallowed_note("media_tools._load_moov_playback")
        pass
    for mb in (4, 8):
        window_size = min(total, mb * 1024 * 1024)
        base = max(0, total - window_size)
        try:
            tail = await source.get_range(base, window_size)
            hit = _find_moov_from_window(tail, base, total)
            if hit:
                start_off, size = hit
                if base <= start_off and start_off + size <= base + len(tail):
                    return tail[start_off - base:start_off - base + size], start_off
                if 0 < size <= 2 * 1024 * 1024:
                    return await source.get_range(start_off, size), start_off
        except Exception:
            _swallowed_note("media_tools._load_moov_playback")
            pass
        try:
            front = await source.get_range(0, window_size)
            hit = _find_moov_from_window(front, 0, total)
            if hit:
                start_off, size = hit
                if start_off + size <= len(front):
                    return front[start_off:start_off + size], start_off
                if 0 < size <= 2 * 1024 * 1024:
                    return await source.get_range(start_off, size), start_off
        except Exception:
            _swallowed_note("media_tools._load_moov_playback")
            pass
    raise RuntimeError("MP4 moov atom was not found")


async def preview_frame_playback_cache(engine, vkey, message, t: float, width: int = 640,
                                       timeout: float = 2.0, epoch: int = 0,
                                       priority: int = 2, kind: str = "preview") -> bytes:
    """Fast live seek preview using the shared playback cache.

    This intentionally returns the nearest independently decodable sync frame,
    not an arbitrary exact presentation frame. That is the same useful tradeoff
    needed while scrubbing: fetch one compact keyframe range instead of making
    FFmpeg reopen and seek a remote localhost stream for every hover position.
    """
    width = max(240, min(int(width), 640))
    height = max(135, int(round(width * 9 / 16)))
    key = (int(vkey[0]), int(vkey[1]))
    source = _PlaybackPreviewRangeSource(engine, key, message, epoch=epoch, priority=priority, kind=kind)

    async def work():
        """Decode a preview frame using the cached MP4 index."""
        with _DIRECT_PREVIEW_INDEX_LOCK:
            cached = _DIRECT_PREVIEW_INDEX_CACHE.get(key)
            if cached and time.monotonic() - cached[0] <= _DIRECT_PREVIEW_INDEX_TTL:
                index = cached[1]
            else:
                index = None
        if index is None:
            moov, _ = await _load_moov_playback(source)
            index = _parse_visual_config(moov)
            if not index:
                raise RuntimeError("MP4 visual sample index unavailable")
            with _DIRECT_PREVIEW_INDEX_LOCK:
                _DIRECT_PREVIEW_INDEX_CACHE[key] = (time.monotonic(), index)
                while len(_DIRECT_PREVIEW_INDEX_CACHE) > _DIRECT_PREVIEW_INDEX_MAX:
                    oldest = min(_DIRECT_PREVIEW_INDEX_CACHE.items(), key=lambda kv: kv[1][0])[0]
                    _DIRECT_PREVIEW_INDEX_CACHE.pop(oldest, None)

        times = [int(x) for x in index["pts"]]
        sync = list(index.get("sync") or [])
        if not sync:
            raise RuntimeError("MP4 has no sync-sample table")
        target_ticks = int(max(0.0, float(t)) * index["timescale"])
        # Choose the closest sync frame, with a deterministic lower-time tie.
        sync_idx = min(sync, key=lambda i: (abs(times[i] - target_ticks), times[i]))
        start_byte = int(index["offsets"][sync_idx])
        end_byte = start_byte + int(index["sizes"][sync_idx])
        blob = await source.get_range(start_byte, end_byte - start_byte)
        annexb = _make_annexb_stream(index, blob, sync_idx, sync_idx)
        # V29.4.118: keep the video's own aspect ratio (the decoder resizes
        # to exactly tw x th, so a fixed 16:9 box squashed vertical videos).
        tw, th = width, height
        vw, vh = int(index.get("width") or 0), int(index.get("height") or 0)
        if vw > 0 and vh > 0:
            if vh > vw:
                th = width
                tw = max(2, int(round(width * vw / vh / 2.0)) * 2)
            else:
                tw = width
                th = max(2, int(round(width * vh / vw / 2.0)) * 2)
        def decode_jpeg():
            img = _decode_gop_image(index, annexb, 0, tw, th)
            if isinstance(img, (bytes, bytearray)):
                return bytes(img)
            out = io.BytesIO()
            img.convert("RGB").save(out, format="JPEG", quality=74)
            return out.getvalue()
        return await asyncio.to_thread(decode_jpeg)

    return await asyncio.wait_for(work(), timeout=max(0.5, float(timeout)))


async def preview_frame_legacy(url: str, t: float, width: int = 640, timeout: float = 15.0) -> bytes:
    """Generate one seek-preview JPEG using the v18.44.05 FFmpeg path.

    This deliberately does not pass a machine-wide hardware profile. A preview
    is a tiny, disposable decode and must not inherit a broken QSV/D3D11/CUDA
    capability discovered during startup. The old Archive X path used one
    straightforward FFmpeg invocation against the range-capable /video URL;
    that behavior remains the primary preview path for reliability.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg was not found")
    t = max(0.0, float(t))
    width = max(240, min(int(width), 640))
    height = max(135, int(round(width * 9 / 16)))
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#292929")
    # V28.8.20 speed path: `-noaccurate_seek` returns the keyframe at/before t
    # instead of decoding forward to the exact timestamp. That needs only the
    # keyframe's bytes (usually one Telegram segment) rather than the whole GOP,
    # which is the difference between ~1 s and ~5 s on a slow Telegram link.
    # Smaller probe limits stop FFmpeg reading megabytes before the seek.
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-probesize", "2M", "-analyzeduration", "1000000",
           "-noaccurate_seek",
           "-ss", f"{t:.3f}", "-i", url,
           "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn",
           "-vf", vf, "-q:v", "8",
           "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    _rc, out, err = await _run(cmd, timeout)
    if not out:
        raise RuntimeError((err.decode("utf-8", "ignore") or f"no frame at {t:.1f}s").strip()[-500:])
    return out


async def preview_frame(url: str, t: float, width: int = 480, timeout: float = 15.0, hardware_profile: dict | None = None) -> bytes:
    """Return a clear seek-bar frame near `t` without requiring an exact keyframe.

    The previous implementation used FFmpeg's `-skip_frame nokey`. On some
    Telegram-streamed containers that can return no frame at all for a seek
    position, leaving the seek preview blank. We now use a fast input-side
    seek and decode the nearest available video frame, then fit it into a
    consistent 16:9 preview box without cropping the source.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg was not found")
    t = max(0.0, float(t))
    width = max(240, min(int(width), 640))
    height = max(135, int(round(width * 9 / 16)))
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#292929")
    # `image2` is a file-sequence muxer and is unreliable when its output is a
    # bare pipe on some Windows FFmpeg builds.  Use image2pipe explicitly for
    # the one-frame JPEG response.  This is the narrow fix for the observed
    # `enc:mjpeg ... error -22` preview failures.
    # Two-stage random access: fast input-side seek near the target, then
    # accurate output-side seek over only a short decode window.
    anchor_window = 3.0
    anchor = max(0.0, t - anchor_window)
    refine = t - anchor
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2",
           *hwaccel_input_args(hardware_profile),
           "-analyzeduration", "512k", "-probesize", "512k",
           "-ss", f"{anchor:.3f}", "-i", url, "-ss", f"{refine:.3f}",
           "-map", "0:v:0?", "-sn", "-dn",
           "-frames:v", "1", "-an", "-vf", vf, "-q:v", "8",
           "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
    rc, out, err = await _run(cmd, timeout)
    if out:
        return out

    # Some FFmpeg builds expose PNG more reliably than MJPEG.  Keep the same
    # HTTP contract by decoding/re-encoding the fallback to JPEG in-process.
    png_cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2",
               *hwaccel_input_args(hardware_profile),
               "-analyzeduration", "512k", "-probesize", "512k",
               "-ss", f"{anchor:.3f}", "-i", url, "-ss", f"{refine:.3f}",
               "-map", "0:v:0?", "-sn", "-dn",
               "-frames:v", "1", "-an", "-vf", vf, "-f", "image2pipe", "-c:v", "png", "pipe:1"]
    _rc2, png, err2 = await _run(png_cmd, timeout)
    if png:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(png)) as im:
                out_jpg = io.BytesIO()
                im.convert("RGB").save(out_jpg, format="JPEG", quality=75, optimize=False, progressive=False)
                return out_jpg.getvalue()
        except Exception:
            _swallowed_note("media_tools.preview_frame")
            pass

    # Hardware decoding may fail for one HEVC stream even though the machine-
    # wide hardware probe succeeds. Preview generation must remain functional,
    # so retry once on the CPU before reporting the preview as unavailable.
    if hardware_profile:
        sw_cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2",
                  "-analyzeduration", "1M", "-probesize", "1M",
                  "-ss", f"{anchor:.3f}", "-i", url, "-ss", f"{refine:.3f}",
                  "-map", "0:v:0?", "-sn", "-dn", "-frames:v", "1", "-an",
                  "-vf", vf, "-q:v", "8", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
        try:
            _rc3, sw_out, sw_err = await _run(sw_cmd, timeout)
        except Exception:
            sw_out, sw_err = b"", b""
        if sw_out:
            return sw_out
        if sw_err:
            err2 = sw_err
    raise RuntimeError((err.decode("utf-8", "ignore") or err2.decode("utf-8", "ignore") or f"no frame at {t:.1f}s").strip()[-600:])




# ==============================================================================
# SECTION: Preview cleanup
# Prunes preview files.
# ==============================================================================

def _prune_files_by_size(root: Path, patterns: tuple[str, ...], max_bytes: int) -> dict:
    """Prune cache files by oldest access/mtime while preserving the newest files.

    Cache layout can be nested (v3/<video>/<bucket>.jpg), so this deliberately
    uses rglob instead of only looking one directory level deep.
    """
    result = {"removed": 0, "bytes": 0, "remaining_bytes": 0}
    if not root.exists():
        return result
    files = []
    total = 0
    for pattern in patterns:
        for f in root.rglob(pattern):
            if not f.is_file():
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            size = int(st.st_size)
            total += size
            files.append((max(float(st.st_mtime), float(getattr(st, "st_atime", 0.0))), size, f))
    result["remaining_bytes"] = total
    if total <= max_bytes:
        return result
    for _mtime, size, f in sorted(files, key=lambda x: x[0]):
        if total <= max_bytes:
            break
        try:
            f.unlink(missing_ok=True)
            total -= size
            result["removed"] += 1
            result["bytes"] += size
        except OSError:
            pass
    result["remaining_bytes"] = total
    # Remove empty cache directories left behind by the file pruning.
    for d in sorted((x for x in root.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    return result


def cleanup_previews(max_bytes: int = 300 * 1024 * 1024) -> dict:
    """Keep seek-preview caches bounded and remove stale generation artifacts.

    Preview files are disposable derived data. They may be recreated at any
    time, so pruning them never touches original media, metadata, database, or
    Telegram job state.
    """
    result = {"preview_removed": 0, "preview_bytes": 0, "sheet_removed": 0,
              "sheet_bytes": 0, "temp_removed": 0, "temp_bytes": 0}
    try:
        r = _prune_files_by_size(PREVIEW_DIR, ("*.jpg", "*.webp", "*.png"), max_bytes)
        result["preview_removed"] = r["removed"]
        result["preview_bytes"] = r["bytes"]
    except Exception:
        _swallowed_note("media_tools.cleanup_previews")
        pass

    # 36-frame sheets are also disposable. Keep a separate, smaller cache so a
    # large library cannot turn timeline previews into permanent storage.
    sheet_root = config.PLAYER_CACHE_DIR / "preview_sheets"
    try:
        r = _prune_files_by_size(sheet_root, ("*.jpg", "*.webp", "*.png"), max(128, max_bytes // 2))
        result["sheet_removed"] = r["removed"]
        result["sheet_bytes"] = r["bytes"]
    except Exception:
        _swallowed_note("media_tools.cleanup_previews")
        pass

    # Crash leftovers from atomic writes. They are never valid published cache
    # entries and are safe to remove once they are no longer being written.
    cutoff = time.time() - 3600
    for root in (PREVIEW_DIR, sheet_root, config.PLAYER_CACHE_DIR / "playlists"):
        if not root.exists():
            continue
        try:
            for f in root.rglob("*"):
                if not f.is_file() or not f.name.endswith((".tmp", ".tmp.jpg", ".part", ".partial")):
                    continue
                try:
                    if f.stat().st_mtime < cutoff:
                        size = f.stat().st_size
                        f.unlink(missing_ok=True)
                        result["temp_removed"] += 1
                        result["temp_bytes"] += size
                except OSError:
                    pass
        except OSError:
            pass
    return result
