"""Size-aware local-upload optimizer.

The optimizer is deliberately used only when a local upload exceeds the
account-specific safe Telegram limit. It preserves the source file, keeps the
source resolution whenever practical, chooses a verified encoder, estimates a
bitrate from the target size, and performs a small bounded feedback search when
an encode misses the target.
"""
from __future__ import annotations

from .swallowed import note as _swallowed_note
import asyncio, math, os, subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..playback.media_tools import best_h264_encoder

ProgressFn = Callable[[str], Awaitable[None]]


def target_video_bitrate(duration: float, target_bytes: int, audio_kbps: int = 128, safety: float = 0.94) -> int:
    """Return a conservative video bitrate in bits/sec for a target file size."""
    duration = max(float(duration or 0), 1.0)
    usable = max(int(target_bytes * float(safety)), 1_000_000)
    total_bps = usable * 8 / duration
    video_bps = total_bps - audio_kbps * 1000
    return max(250_000, int(video_bps))


def _quality_args(encoder: str, bitrate: int, fps: float) -> list[str]:
    kbps=max(250,int(bitrate/1000)); max_kbps=max(kbps+1,int(kbps*1.10)); buf=max(kbps*2,max_kbps*2)
    gop=max(30,int(round(max(fps or 30,10)*2)))
    if encoder == 'h264_nvenc':
        return ['-c:v','h264_nvenc','-preset','p5','-tune','hq','-rc','vbr','-b:v',f'{kbps}k','-maxrate',f'{max_kbps}k','-bufsize',f'{buf}k','-rc-lookahead','16','-spatial_aq','1','-temporal_aq','1','-aq-strength','8','-pix_fmt','yuv420p','-g',str(gop)]
    if encoder == 'h264_qsv':
        # No -global_quality: with it QSV switches to ICQ (constant quality)
        # and ignores the bitrate, so the output size was never controlled
        # and every pass missed the Telegram limit.
        return ['-c:v','h264_qsv','-preset','medium','-b:v',f'{kbps}k','-maxrate',f'{max_kbps}k','-bufsize',f'{buf}k','-pix_fmt','nv12','-g',str(gop)]
    if encoder == 'h264_amf':
        return ['-c:v','h264_amf','-quality','quality','-rc','vbr_peak','-b:v',f'{kbps}k','-maxrate',f'{max_kbps}k','-bufsize',f'{buf}k','-pix_fmt','yuv420p','-g',str(gop)]
    return ['-c:v','libx264','-preset','veryfast','-b:v',f'{kbps}k','-maxrate',f'{max_kbps}k','-bufsize',f'{buf}k','-pix_fmt','yuv420p','-g',str(gop)]


def _encode_cmd(ffmpeg: str, src: Path, out: Path, meta: dict[str,Any], encoder: str, bitrate: int) -> list[str]:
    w=int(meta.get('width') or 0); h=int(meta.get('height') or 0); fps=float(meta.get('fps') or 30)
    # Preserve source resolution. Telegram/browser compatibility is handled by H.264 encoding,
    # while the size optimizer only changes resolution as an explicit future policy if needed.
    vf=[]
    if w % 2 or h % 2: vf=['-vf',f'scale={w - w%2}:{h - h%2}']
    return [ffmpeg,'-hide_banner','-loglevel','error','-nostats','-progress','pipe:1','-y','-i',str(src),'-map','0:v:0','-map','0:a:0?','-sn','-dn','-map_metadata','-1',*vf,*_quality_args(encoder,bitrate,fps),'-c:a','aac','-b:a','128k','-ac','2','-ar','48000','-movflags','+faststart',str(out)]


async def _run_encode(cmd: list[str], duration: float, label: str, report: ProgressFn|None) -> tuple[int, str]:
    """Run one FFmpeg encode, reporting live progress every few seconds.

    A long encode used to report nothing between passes, so the job watchdog
    declared the upload stalled after 10 minutes and restarted it - the
    encode of a large file therefore never finished. Cancelling the job now
    also stops FFmpeg instead of leaving it running in the background.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **({"creationflags": 0x08000000} if os.name == "nt" else {}),
    )
    stderr_task = asyncio.create_task(proc.stderr.read())
    last_report = 0.0
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            key, _, value = line.decode("utf-8", "ignore").strip().partition("=")
            if key not in ("out_time_us", "out_time_ms") or not value.strip().lstrip("-").isdigit():
                continue
            done = max(0.0, int(value) / 1_000_000)
            now = loop.time()
            if report and now - last_report >= 3.0:
                last_report = now
                pct = min(99.9, 100.0 * done / max(duration, 1.0))
                elapsed = now - started
                eta = (elapsed * (100.0 - pct) / pct) if pct > 0.5 else 0
                eta_text = f" · ETA {int(eta // 60)}m {int(eta % 60):02d}s" if eta else ""
                await report(f"{label} · {pct:.0f}%{eta_text}")
        rc = await proc.wait()
    except BaseException:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except Exception:
                _swallowed_note("size_optimizer._run_encode")
                pass
        stderr_task.cancel()
        raise
    stderr = (await stderr_task).decode("utf-8", "ignore")
    return rc, stderr


def _effective_duration(meta: dict[str, Any], src: Path) -> float:
    duration = float(meta.get('duration') or 0)
    if duration > 1:
        return duration
    # Some MKV/AVI/WMV files report no duration; derive it from the container
    # bitrate so the target bitrate is not computed for a 1-second clip.
    bitrate = float(meta.get('bitrate') or meta.get('bit_rate') or 0)
    if bitrate > 0:
        return max(1.0, src.stat().st_size * 8 / bitrate)
    return 1.0


async def optimize_for_limit(ffmpeg: str, src: Path, out_dir: Path, safe_limit_bytes: int,
                             probe_fn: Callable[[str|None,Path],dict[str,Any]],
                             ffprobe: str|None = None,
                             report: ProgressFn|None=None) -> tuple[Path, dict[str,Any]]:
    """Re-encode a video to fit under a byte limit and return the result."""
    meta=await asyncio.to_thread(probe_fn, ffprobe, src)
    duration=_effective_duration(meta, src)
    if src.stat().st_size <= safe_limit_bytes:
        return src, {'optimized':False,'reason':'already-within-limit'}
    encoder=await best_h264_encoder()
    # Reserve room for muxing/Telegram-side size accounting. The search is
    # bounded: target, then up to three corrections.
    bitrate=target_video_bitrate(duration,safe_limit_bytes)
    out_dir.mkdir(parents=True,exist_ok=True)
    last=None
    history=[]
    errors=[]
    max_passes=4
    attempt=0
    while attempt < max_passes:
        out=out_dir/f'{src.stem}.telegram_opt_{attempt+1}.mp4'
        if out.exists(): out.unlink()
        label=f'Compressing {src.name}: pass {attempt+1}/{max_passes} using {encoder} at ~{bitrate/1e6:.2f} Mbps'
        if report: await report(label)
        rc, stderr = await _run_encode(_encode_cmd(ffmpeg,src,out,meta,encoder,bitrate), duration, label, report)
        if rc!=0 or not out.exists() or out.stat().st_size<=0:
            errors.append(f'{encoder}: {stderr.strip()[-400:] or f"exit code {rc}"}')
            if encoder != 'libx264':
                # A GPU encoder that passed the tiny startup test can still
                # reject this source (8K, odd sizes, driver session limits).
                # Fall back to the software encoder instead of failing.
                encoder='libx264'
                continue
            raise RuntimeError('FFmpeg could not compress the video: ' + ' | '.join(errors))
        size=out.stat().st_size; history.append({'attempt':attempt+1,'encoder':encoder,'bitrate':bitrate,'size_bytes':size})
        last=out
        attempt+=1
        if size <= safe_limit_bytes:
            # Remove superseded candidates immediately; keep only the selected output.
            for old in out_dir.glob(f'{src.stem}.telegram_opt_*.mp4'):
                if old != out:
                    try: old.unlink()
                    except OSError: pass
            return out, {'optimized':True,'encoder':encoder,'attempts':attempt,'history':history,'size_bytes':size,'source_size_bytes':src.stat().st_size,'target_bytes':safe_limit_bytes,'resolution':[meta.get('width'),meta.get('height')]}
        ratio=(safe_limit_bytes/max(size,1))*0.92
        bitrate=max(250_000,int(bitrate*ratio))
    raise RuntimeError(f'Could not compress {src.name} below {safe_limit_bytes} bytes after {max_passes} passes; last size={last.stat().st_size if last and last.exists() else 0}. Choose Skip for this file to keep the original unchanged.')
