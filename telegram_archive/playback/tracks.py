"""Audio and subtitle tracks of a video: list them, pull a subtitle out as WebVTT, and cut audio for the caption engine or as an alternate-audio file.

Everything takes a `source` (a local path or a loopback URL that ffmpeg can read) and works with ffmpeg/ffprobe only.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from . import media_tools

# Subtitle formats that are text and can become WebVTT. Picture-based ones (PGS, VobSub, DVB) cannot.
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "ttml", "subviewer", "microdvd", "sami", "jacosub", "mpl2", "realtext", "stl", "vplayer", "pjs"}
LANGUAGE_NAMES = {
    "eng": "English", "en": "English", "spa": "Spanish", "es": "Spanish", "fra": "French", "fre": "French", "fr": "French", "deu": "German", "ger": "German", "de": "German",
    "ita": "Italian", "it": "Italian", "por": "Portuguese", "pt": "Portuguese", "rus": "Russian", "ru": "Russian", "jpn": "Japanese", "ja": "Japanese", "kor": "Korean", "ko": "Korean",
    "zho": "Chinese", "chi": "Chinese", "zh": "Chinese", "ara": "Arabic", "ar": "Arabic", "hin": "Hindi", "hi": "Hindi", "tur": "Turkish", "tr": "Turkish", "nld": "Dutch", "dut": "Dutch",
    "nl": "Dutch", "pol": "Polish", "pl": "Polish", "swe": "Swedish", "sv": "Swedish", "tha": "Thai", "th": "Thai", "vie": "Vietnamese", "vi": "Vietnamese", "ukr": "Ukrainian", "uk": "Ukrainian",
    "und": "", "": "",
}


def language_name(code: Any) -> str:
    c = str(code or "").strip().lower()
    return LANGUAGE_NAMES.get(c, c.upper() if c else "")


def is_text_subtitle(codec: Any) -> bool:
    return str(codec or "").lower() in TEXT_SUBTITLE_CODECS


def _label(kind: str, ordinal: int, lang: str, title: str, extra: str = "") -> str:
    name = language_name(lang)
    base = title.strip() or name or f"{kind} {ordinal + 1}"
    if title.strip() and name and name.lower() not in title.lower():
        base = f"{name} · {title.strip()}"
    return f"{base}{extra}"


def parse_tracks(raw: dict) -> dict:
    """The audio and subtitle streams of an ffprobe result, each with a stable `index` (the stream's own number in the file)."""
    audio: list[dict] = []
    subs: list[dict] = []
    for s in raw.get("streams") or []:
        kind = str(s.get("codec_type") or "")
        tags = {str(k).lower(): str(v) for k, v in (s.get("tags") or {}).items()}
        disp = s.get("disposition") or {}
        lang = tags.get("language", "")
        title = tags.get("title", "")
        if kind == "audio":
            ch = int(s.get("channels") or 0)
            layout = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch} ch" if ch else "")
            audio.append({
                "index": int(s.get("index") or 0), "ordinal": len(audio), "codec": str(s.get("codec_name") or ""), "language": lang if lang != "und" else "",
                "title": title, "channels": ch, "default": bool(disp.get("default")),
                "label": _label("Audio", len(audio), lang, title, f" [{layout}]" if layout and ch > 2 else ""),
            })
        elif kind == "subtitle":
            codec = str(s.get("codec_name") or "")
            text = is_text_subtitle(codec)
            subs.append({
                "index": int(s.get("index") or 0), "ordinal": len(subs), "codec": codec, "language": lang if lang != "und" else "", "title": title,
                "forced": bool(disp.get("forced")), "default": bool(disp.get("default")), "text": text,
                "label": _label("Subtitles", len(subs), lang, title, " (forced)" if disp.get("forced") else "") + ("" if text else " (picture, not supported)"),
            })
    dur = 0.0
    try:
        dur = float((raw.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        pass
    return {"audio": audio, "subtitles": subs, "duration": dur}


async def probe_tracks(source: str, timeout: float = 40.0) -> dict:
    ffprobe = media_tools.find_ffprobe()
    if not ffprobe:
        raise FileNotFoundError("ffprobe was not found")
    rc, out, err = await media_tools._run([ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", source], timeout)
    if rc != 0 and not out:
        raise RuntimeError((err.decode("utf-8", "ignore") or "ffprobe failed").strip()[-300:])
    return parse_tracks(json.loads(out.decode("utf-8", "ignore") or "{}"))


async def subtitle_vtt(source: str, stream_index: int, timeout: float = 120.0) -> bytes:
    """One text subtitle stream as WebVTT."""
    ffmpeg = media_tools.find_ffmpeg()
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg was not found")
    rc, out, err = await media_tools._run([ffmpeg, "-v", "error", "-nostdin", "-i", source, "-map", f"0:{int(stream_index)}", "-f", "webvtt", "pipe:1"], timeout)
    if rc != 0 or not out.strip():
        raise RuntimeError((err.decode("utf-8", "ignore") or "could not read this subtitle track").strip()[-300:])
    return out


async def audio_window_wav(source: str, start: float, duration: float, stream_index: int | None = None, timeout: float = 90.0) -> bytes:
    """`duration` seconds of audio from `start`, as 16 kHz mono 16-bit WAV (what speech recognition wants)."""
    ffmpeg = media_tools.find_ffmpeg()
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg was not found")
    sel = f"0:{int(stream_index)}" if stream_index is not None else "0:a:0"
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.5, duration):.3f}", "-i", source,
           "-map", sel, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"]
    rc, out, err = await media_tools._run(cmd, timeout)
    if rc != 0 or len(out) < 64:
        raise RuntimeError((err.decode("utf-8", "ignore") or "could not read the audio").strip()[-300:])
    return out


async def extract_audio_track(source: str, stream_index: int, out_path: Path, timeout: float = 3 * 3600) -> Path:
    """One audio stream as an AAC file the browser can seek (the alternate-audio track). Written to a temp name, then renamed, so a half-made file is never served."""
    ffmpeg = media_tools.find_ffmpeg()
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg was not found")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".part.m4a")
    rc, _out, err = await media_tools._run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", source, "-map", f"0:{int(stream_index)}", "-vn", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-f", "mp4", str(tmp)], timeout, want_stdout=False)
    if rc != 0 or not tmp.exists() or tmp.stat().st_size < 256:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise RuntimeError((err.decode("utf-8", "ignore") or "could not extract this audio track").strip()[-300:])
    os.replace(tmp, out_path)
    return out_path


_VTT_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def vtt_seconds(stamp: str) -> float:
    m = _VTT_TIME.fullmatch(stamp.strip())
    if not m:
        raise ValueError(stamp)
    h, mi, s, ms = (int(m.group(1) or 0), int(m.group(2)), int(m.group(3)), int(m.group(4).ljust(3, "0")))
    return h * 3600 + mi * 60 + s + ms / 1000
