"""Audio tracks, subtitle tracks and live English captions for the web player."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..playback import captions, tracks


def register_tracks_routes(router: APIRouter, *, config, require_auth, _local_url, require_playback_allowed, service: "captions.CaptionService | None" = None) -> dict[str, object]:
    root = Path(config.PLAYER_CACHE_DIR) / "tracks"
    svc = service or captions.CaptionService(Path(config.PLAYER_CACHE_DIR))
    track_cache: dict[tuple, dict] = {}
    audio_jobs: dict[tuple, asyncio.Task] = {}

    def src(cid: int, mid: int, ttl: int = 6 * 3600) -> str:
        return _local_url(cid, mid, ttl=ttl)

    async def info(cid: int, mid: int) -> dict:
        key = (cid, mid)
        if key not in track_cache:
            try:
                track_cache[key] = await tracks.probe_tracks(src(cid, mid))
            except FileNotFoundError as exc:
                raise HTTPException(503, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(502, f"Could not read tracks: {exc}") from exc
        return track_cache[key]

    @router.get("/api/play/tracks/{channel_id}/{message_id}")
    async def play_tracks(channel_id: int, message_id: int, request: Request):
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        t = await info(channel_id, message_id)
        return {**t, "captions": svc.status()}

    @router.get("/api/play/subtitle/{channel_id}/{message_id}/{index}.vtt")
    async def play_subtitle(channel_id: int, message_id: int, index: int, request: Request):
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        t = await info(channel_id, message_id)
        s = next((x for x in t["subtitles"] if x["index"] == index), None)
        if not s or not s["text"]:
            raise HTTPException(404, "No such text subtitle track")
        cache = root / f"{channel_id}_{message_id}" / f"sub_{index}.vtt"
        if not cache.exists():
            try:
                data = await tracks.subtitle_vtt(src(channel_id, message_id), index)
            except Exception as exc:
                raise HTTPException(502, str(exc)) from exc
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        return Response(cache.read_bytes(), media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "private, max-age=3600"})

    def audio_path(cid: int, mid: int, index: int) -> Path:
        return root / f"{cid}_{mid}" / f"audio_{index}.m4a"

    @router.get("/api/play/audio-track/{channel_id}/{message_id}/{index}/status")
    async def audio_status(channel_id: int, message_id: int, index: int, request: Request):
        """Starts building the alternate audio file on first call; the player polls until it is ready."""
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        t = await info(channel_id, message_id)
        if not any(a["index"] == index for a in t["audio"]):
            raise HTTPException(404, "No such audio track")
        p = audio_path(channel_id, message_id, index)
        if p.exists():
            return {"ready": True, "url": f"/api/play/audio-track/{channel_id}/{message_id}/{index}.m4a"}
        key = (channel_id, message_id, index)
        job = audio_jobs.get(key)
        if job and job.done():
            exc = job.exception() if not job.cancelled() else None
            audio_jobs.pop(key, None)
            if exc:
                return {"ready": False, "error": str(exc)}
        if key not in audio_jobs:
            audio_jobs[key] = asyncio.create_task(tracks.extract_audio_track(src(channel_id, message_id, 24 * 3600), index, p))
        return {"ready": False}

    @router.get("/api/play/audio-track/{channel_id}/{message_id}/{index}.m4a")
    async def audio_file(channel_id: int, message_id: int, index: int, request: Request):
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        p = audio_path(channel_id, message_id, index)
        if not p.exists():
            raise HTTPException(404, "Audio track is not ready")
        return FileResponse(p, media_type="audio/mp4")

    @router.get("/api/captions/status")
    async def captions_status(request: Request):
        require_auth(request)
        return svc.status()

    @router.get("/api/play/captions/{channel_id}/{message_id}")
    async def play_captions(channel_id: int, message_id: int, request: Request, start: float = 0.0, track: int | None = None):
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        if not svc.status()["available"]:
            raise HTTPException(501, "Live captions need the optional faster-whisper package (pip install faster-whisper)")
        url = src(channel_id, message_id, 3600)

        async def wav(s: float, d: float) -> bytes:
            return await tracks.audio_window_wav(url, s, d, track)
        try:
            return await svc.window(f"{channel_id}_{message_id}", start, track, wav)
        except Exception as exc:
            raise HTTPException(502, f"Caption engine failed: {exc}") from exc

    return {"_caption_service": svc}
