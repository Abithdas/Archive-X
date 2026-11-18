"""Audio tracks, subtitle tracks and live English captions for the web player."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ..core.safe_paths import inside
from ..engines import local_scanner, local_videos
from ..playback import captions, tracks

_LOG = logging.getLogger(__name__)


def register_tracks_routes(router: APIRouter, *, config, require_auth, _local_url, require_playback_allowed, service: "captions.CaptionService | None" = None, db=None) -> dict[str, object]:
    root = Path(config.PLAYER_CACHE_DIR) / "tracks"
    saved_model = None
    if service is None and db is not None:
        try:
            saved_model = str(db.setting("captions_model") or "") or None
        except Exception:
            saved_model = None
    svc = service or captions.CaptionService(Path(config.PLAYER_CACHE_DIR), model=saved_model)
    track_cache: dict[tuple, dict] = {}
    audio_jobs: dict[tuple, asyncio.Task] = {}

    async def src(cid: int, mid: int, ttl: int = 6 * 3600) -> str:
        """What ffmpeg reads for this video: the file itself for a video from a folder on this PC, else the loopback feed that streams it from Telegram."""
        if db is not None and local_scanner.is_local_channel(cid):
            path = await asyncio.to_thread(local_videos.file_of, db, cid, mid)
            if not path:
                raise HTTPException(404, "That file is not in its folder any more")
            return path
        return _local_url(cid, mid, ttl=ttl)

    async def info(cid: int, mid: int) -> dict:
        key = (cid, mid)
        if key not in track_cache:
            try:
                track_cache[key] = await tracks.probe_tracks(await src(cid, mid))
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(503, "The video file is not available to read its tracks") from exc
            except Exception as exc:
                _LOG.warning("track probe failed: %s", exc)
                raise HTTPException(502, "Could not read the tracks of this video") from exc
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
        cache = inside(root, f"{int(channel_id)}_{int(message_id)}", f"sub_{int(index)}.vtt")
        if not cache.exists():
            try:
                data = await tracks.subtitle_vtt(await src(channel_id, message_id), index)
            except Exception as exc:
                _LOG.warning("subtitle extraction failed: %s", exc)
                raise HTTPException(502, "Could not read this subtitle track") from exc
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        return Response(cache.read_bytes(), media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "private, max-age=3600"})

    def audio_path(cid: int, mid: int, index: int) -> Path:
        return inside(root, f"{int(cid)}_{int(mid)}", f"audio_{int(index)}.m4a")

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
                _LOG.warning("audio track extraction failed: %s", exc)
                return {"ready": False, "error": "Could not extract this audio track"}
        if key not in audio_jobs:
            audio_jobs[key] = asyncio.create_task(tracks.extract_audio_track(await src(channel_id, message_id, 24 * 3600), index, p))
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
    async def captions_status(request: Request, prepare: int = 0):
        """?prepare=1 starts downloading/loading the speech model in the background; the player polls until model is "ready"."""
        require_auth(request)
        if prepare and svc.status()["available"]:
            svc.ensure_ready()
        return svc.status()

    @router.get("/api/captions/model")
    async def captions_model(request: Request):
        """The speech model in use, the ones to choose from, and how fast the last windows were made (1.0 is real time)."""
        require_auth(request)
        return {"ok": True, "model": svc.model, "choices": [{"id": k, **v} for k, v in captions.MODELS.items()], "speed": round(svc.speed, 1), "state": svc.model_state, "device": svc.status()["device"]}

    @router.post("/api/captions/model")
    async def captions_model_set(request: Request):
        require_auth(request)
        body = await request.json()
        name = str((body or {}).get("model") or "")
        if name not in captions.MODELS:
            raise HTTPException(400, "Choose one of: " + ", ".join(captions.MODELS))
        svc.set_model(name)
        if db is not None:
            await asyncio.to_thread(db.set_setting, "captions_model", name)
        return {"ok": True, "model": svc.model}

    @router.get("/api/play/captions/{channel_id}/{message_id}")
    async def play_captions(channel_id: int, message_id: int, request: Request, start: float = 0.0, track: int | None = None, task: str = "translate", at: float | None = None):
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        if not svc.status()["available"]:
            raise HTTPException(501, "Live captions need the optional faster-whisper package (pip install faster-whisper)")
        url = await src(channel_id, message_id, 3600)

        async def wav(s: float, d: float) -> bytes:
            return await tracks.audio_window_wav(url, s, d, track)
        try:
            return await svc.window(f"{channel_id}_{message_id}", start, track, wav, "transcribe" if task == "transcribe" else "translate", is_gone=request.is_disconnected, at=at)
        except captions.CaptionsPreparing:
            return JSONResponse({"preparing": True, "cues": []}, status_code=202)
        except captions.CaptionsStale:
            return JSONResponse({"skipped": True, "cues": []}, status_code=202)
        except Exception as exc:
            raise HTTPException(502, f"Caption engine failed: {exc}") from exc

    return {"_caption_service": svc}
