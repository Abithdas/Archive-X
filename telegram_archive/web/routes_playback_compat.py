"""Explicit registration for compatibility streaming, HLS, and external players."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Browser compatibility stream
#       - HLS
#       - External devices and players
#       - Scan schedule
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import copy
import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..playback import media_tools


def register_playback_compat_routes(
    router: APIRouter,
    *,
    config,
    db,
    ps,
    resources,
    require_auth,
    tokens,
    _external_player_monitors,
    _compat_info,
    _engine_message,
    _get_engine,
    _local_url,
    _media_hw_profile,
    _playback_claim_from,
    _player_port,
    _select_compat_profile,
    require_playback_allowed,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Browser compatibility stream
    # FFmpeg compat stream.
    # ------------------------------------------------------------------
    """Register compatibility stream, HLS and external player routes."""
    async def _start_compat_process(channel_id: int, message_id: int, info: dict, t0: float, selected: dict, *, force_transcode_video: bool = False, force_transcode_audio: bool = False):
        """Start live compatibility output and fall through to a safe backend if
        the selected decoder/encoder cannot emit a first browser chunk.

        The persistent compatibility cache remains an optional background
        optimization; interactive playback must never wait for the entire file.
        """
        ffmpeg = media_tools.find_ffmpeg()
        plan = media_tools.plan_compat(info) if info.get("vcodec") else {"copy_video": False, "copy_audio": False, "has_audio": False}
        if force_transcode_video:
            plan["copy_video"] = False
        if force_transcode_audio:
            plan["copy_audio"] = False
        candidates = []
        selected_encoder = str(selected.get("encoder") or "libx264")
        if force_transcode_video and selected_encoder == "copy":
            selected_encoder = "libx264"
        first = (str(selected.get("decoder") or "") or None,
                 "copy" if plan.get("copy_video") else selected_encoder)
        candidates.append(first)
        if os.name == "nt" and first != ("d3d11va", "libx264") and media_tools.find_ffmpeg():
            candidates.append(("d3d11va", "libx264"))
        if candidates[-1] != (None, "libx264"):
            candidates.append((None, "libx264"))

        tried=set()
        for decoder, encoder in candidates:
            pair=(decoder,encoder)
            if pair in tried:
                continue
            tried.add(pair)
            profile=copy.deepcopy(selected.get("profile") or {}) if pair==first else {"hardware_acceleration":{"preferred_decoder":decoder,"preferred_transcode_decoder":decoder}} if decoder else {}
            cmd=media_tools.compat_command(
                ffmpeg, _local_url(channel_id, message_id, ttl=6*3600),
                info if info.get("vcodec") else {}, t0, encoder, profile,
                force_transcode_video=force_transcode_video,
                force_transcode_audio=force_transcode_audio,
            )
            try:
                proc=await asyncio.create_subprocess_exec(
                    *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, creationflags=media_tools._NO_WINDOW
                )
                try:
                    first_chunk=await asyncio.wait_for(proc.stdout.read(256*1024), timeout=10.0)
                except asyncio.TimeoutError:
                    first_chunk=b""
                if first_chunk:
                    db.log(f"Compatibility backend started {channel_id}/{message_id}: decoder={decoder or 'software'} encoder={encoder}", "INFO")
                    return proc, first_chunk, decoder, encoder, plan, profile
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    pass
                try:
                    err=(await proc.stderr.read()).decode("utf-8","ignore")[-700:]
                except Exception:
                    err=""
                if proc.returncode is None:
                    try: proc.kill()
                    except Exception: pass
                try: await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception: pass
                db.log(f"Compatibility backend failed {channel_id}/{message_id}: decoder={decoder or 'software'} encoder={encoder} {err}".strip(), "WARN")
            except Exception as exc:
                db.log(f"Compatibility backend start failed {channel_id}/{message_id}: decoder={decoder or 'software'} encoder={encoder}: {type(exc).__name__}: {exc}", "WARN")
        raise HTTPException(503, "No browser-compatible conversion backend could start for this video")

    def _mse_compat_codec_type(info: dict, plan: dict) -> str:
        """Return an MSE-compatible MP4 MIME declaration for the chosen output."""
        if not plan.get("copy_video"):
            video_codec = "avc1.4D4029"  # transcoded output is pinned to H.264 Main@4.1
        else:
            profile = str(info.get("vprofile") or "").lower()
            prefix = "42E0" if "baseline" in profile else "4D40" if "main" in profile else "6400"
            try: level = int(info.get("vlevel") or 0)
            except (TypeError, ValueError, OverflowError): level = 0
            if not 10 <= level <= 62:
                height = int(info.get("height") or 0)
                level = 51 if height >= 2160 else 40 if height >= 1080 else 31
            video_codec = f"avc1.{prefix}{level:02x}"
        codecs = [video_codec]
        if plan.get("has_audio"):
            acodec = str(info.get("acodec") or "").lower()
            aprofile = str(info.get("aprofile") or "").lower()
            if plan.get("copy_audio") and acodec == "mp3": codecs.append("mp4a.6B")
            elif plan.get("copy_audio") and "he-aac v2" in aprofile: codecs.append("mp4a.40.29")
            elif plan.get("copy_audio") and "he-aac" in aprofile: codecs.append("mp4a.40.5")
            else: codecs.append("mp4a.40.2")
        return 'video/mp4; codecs="' + ", ".join(codecs) + '"'

    @router.get("/stream-compat/{channel_id}/{message_id}")
    async def stream_browser_compat(channel_id: int, message_id: int, request: Request):
        """For videos the browser cannot play itself (HEVC, AC3/DTS audio,
        AVI/WMV...): the PC converts on the fly - like VLC/MPC-HC do inside -
        and sends a browser-friendly MP4. Only what is needed is converted
        (e.g. MKV H.264 + AC3 only gets its audio converted). ?t=seconds
        starts at that position (the player uses it for seeking)."""
        require_auth(request)
        ffmpeg = media_tools.find_ffmpeg()
        if not ffmpeg:
            raise HTTPException(503, "ffmpeg is required for compatibility playback (run Setup-And-Run.ps1)")
        engine, message = await _engine_message(
            channel_id, message_id,
            playback_claim=_playback_claim_from(request.query_params),
        )
        try: target_time = max(0.0, float(request.query_params.get("t", "0") or 0))
        except (TypeError, ValueError, OverflowError): target_time = 0.0
        info = await _compat_info(channel_id, message_id)
        selected = await _select_compat_profile((channel_id, message_id), info)
        plan = media_tools.plan_compat(info) if info.get("vcodec") else {"copy_video": False, "copy_audio": False, "has_audio": False}
        mse_mode = request.query_params.get("mse") == "1" or request.query_params.get("mse_force_transcode") == "1"
        force_mse_compatibility = request.query_params.get("mse_force_transcode") == "1"
        force_transcode_video = force_mse_compatibility
        force_transcode_audio = mse_mode and (
            str(info.get("acodec") or "").lower() == "mp3"
            or (force_mse_compatibility and bool(info.get("acodec")))
        )
        if force_transcode_video:
            plan["copy_video"] = False
        # MP3-in-MP4 is inconsistent for MSE across browsers; keep the video's
        # packets where possible but normalize audio to AAC for the fMP4 path.
        keyframes = []
        source_size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        if mse_mode and target_time > 0.02 and plan.get("copy_video"):
            try:
                from ..core.playback_index_cache import read_manifest
                index_manifest = await asyncio.to_thread(read_manifest, int(channel_id), int(message_id), source_size or None)
                keyframes = (index_manifest or {}).get("keyframes") or []
            except Exception:
                keyframes = []
        seek_plan = media_tools.resolve_mse_seek(
            target_time, copy_video=bool(plan.get("copy_video")), keyframes=keyframes,
            source_size=source_size, max_keyframe_gap=30.0,
        ) if mse_mode else {
            "stream_start": target_time, "timeline_origin": target_time,
            "force_transcode_video": False, "mode": "legacy-compat-target"
        }
        stream_start = float(seek_plan["stream_start"])
        timeline_origin = float(seek_plan["timeline_origin"])
        force_transcode_video = bool(force_transcode_video or seek_plan["force_transcode_video"])
        seek_anchor_kind = "forced-h264-aac-transcode" if force_mse_compatibility else str(seek_plan["mode"])
        acquired = False
        if resources is not None and (not plan.get("copy_video") or force_transcode_video or not plan.get("copy_audio") or force_transcode_audio):
            await resources.acquire_background("conversion")
            acquired = True
        try:
            proc, first_chunk, decoder, encoder, plan, media_profile = await _start_compat_process(
                channel_id, message_id, info, stream_start, selected,
                force_transcode_video=force_transcode_video,
                force_transcode_audio=force_transcode_audio,
            )
        except Exception:
            if acquired and resources is not None:
                await resources.release_background("conversion")
                acquired = False
            raise
        playback_label = "MSE/fMP4 playback" if mse_mode else "compatibility playback"
        db.log(f"{playback_label} {channel_id}/{message_id} target={target_time:.3f}s stream_start={stream_start:.3f}s origin={timeline_origin:.3f}s seek={seek_anchor_kind}: video "
               f"{'copied' if plan['copy_video'] else 'converted with ' + encoder} ({info.get('vcodec') or '?'}), "
               f"decode={decoder or 'software'}, audio "
               f"{'copied' if plan['copy_audio'] else 'converted to AAC'} ({info.get('acodec') or 'none'})")

        from fastapi.responses import StreamingResponse

        async def gen():
            nonlocal acquired
            try:
                if first_chunk:
                    yield first_chunk
                while True:
                    chunk = await proc.stdout.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except Exception:
                    pass
                if acquired and resources is not None:
                    await resources.release_background("conversion")
                    acquired = False
                if proc.returncode not in (0, None, -9, 1, 255):
                    try:
                        err = (await proc.stderr.read()).decode("utf-8", "ignore")[-500:]
                        if err.strip():
                            db.log(f"Compatibility stream {channel_id}/{message_id}: {err}", "WARN")
                    except Exception:
                        pass

        mime_type = _mse_compat_codec_type(info, plan)
        return StreamingResponse(gen(), media_type="video/mp4", headers={
            "Accept-Ranges": "none", "Cache-Control": "no-store",
            "X-Accel-Buffering": "no", "Content-Disposition": "inline",
            "X-Compat-Start": f"{timeline_origin:.3f}",
            "X-Compat-Seek-Start": f"{stream_start:.3f}",
            "X-Compat-Target": f"{target_time:.3f}", "X-Compat-Seek": seek_anchor_kind,
            "X-Compat-Mode": "copy" if plan["copy_video"] else encoder,
            "X-Archive-X-MSE-Type": mime_type,
            "X-Archive-X-MSE-Strategy": "copy" if plan.get("copy_video") and plan.get("copy_audio") else "remux" if plan.get("copy_video") else "transcode"})

    # HLS (iPhone/iPad): the converted video is cut into independent 6-second
    # pieces, made on demand; the next pieces are prepared ahead of time.
    hls_state = {"key": None, "segments": OrderedDict(), "sem": asyncio.Semaphore(2)}


    # ------------------------------------------------------------------
    # SUBSECTION: HLS
    # HLS auth, generation and segments.
    # ------------------------------------------------------------------
    def _hls_auth(request: Request, channel_id: int, message_id: int):
        tok = request.query_params.get("t", "")
        if tok and tokens.check(int(channel_id), int(message_id), tok):
            return
        require_auth(request)

    async def _hls_make(key, index, info):
        ffmpeg = media_tools.find_ffmpeg()
        encoder = await media_tools.best_h264_encoder()
        media_profile = await _media_hw_profile(key[0], key[1], info)
        cmd = media_tools.hls_segment_command(
            ffmpeg, _local_url(*key, ttl=6 * 3600), info, index, encoder,
            hardware_profile=media_profile,
        )
        acquired = False
        if resources is not None:
            await resources.acquire_background("conversion")
            acquired = True
        try:
            async with hls_state["sem"]:
                rc, out, err = await media_tools._run(cmd, 120)
        finally:
            if acquired:
                await resources.release_background("conversion")
        if not out:
            raise RuntimeError(err.decode("utf-8", "ignore")[-300:] or f"ffmpeg exit {rc}")
        return out

    def _hls_future(key, index, info):
        if hls_state["key"] != key:
            for fut in hls_state["segments"].values():
                if not fut.done():
                    fut.cancel()
            hls_state["segments"].clear()
            hls_state["key"] = key
        segs = hls_state["segments"]
        fut = segs.get(index)
        if fut is None or (fut.done() and (fut.cancelled() or fut.exception() is not None)):
            fut = asyncio.ensure_future(_hls_make(key, index, info))
            fut.add_done_callback(lambda f: f.cancelled() or f.exception())
            segs[index] = fut
        segs.move_to_end(index)
        while len(segs) > 14:
            old_i, old = segs.popitem(last=False)
            if not old.done():
                old.cancel()
        return fut

    @router.get("/hls/{channel_id}/{message_id}/index.m3u8")
    async def hls_index(channel_id: int, message_id: int, request: Request):
        """GET /hls/{channel_id}/{message_id}/index.m3u8: Hls index."""
        _hls_auth(request, channel_id, message_id)
        playback_claim = _playback_claim_from(request.query_params)
        if playback_claim:
            engine = await _get_engine()
            try:
                claimed = engine.claim_playback((int(channel_id), int(message_id)), **playback_claim)
            except Exception as e:
                raise HTTPException(503, f"Playback handoff could not be registered: {type(e).__name__}")
            if not claimed:
                raise HTTPException(409, "This playback request was superseded by a newer open")
        from fastapi.responses import Response
        if not media_tools.find_ffmpeg():
            raise HTTPException(503, "ffmpeg is required for compatibility playback")
        info = await _compat_info(channel_id, message_id)
        duration = float(info.get("duration") or 0)
        if duration <= 0:
            raise HTTPException(503, "Video length unknown")
        tok = tokens.make(int(channel_id), int(message_id), ttl=12 * 3600)
        body = media_tools.hls_playlist(duration, f"/hls/{int(channel_id)}/{int(message_id)}", f"?t={tok}")
        return Response(body, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})

    @router.get("/hls/{channel_id}/{message_id}/{index}.ts")
    async def hls_segment(channel_id: int, message_id: int, index: int, request: Request):
        """GET /hls/{channel_id}/{message_id}/{index}.ts: Hls segment."""
        _hls_auth(request, channel_id, message_id)
        from fastapi.responses import Response
        key = (int(channel_id), int(message_id))
        info = await _compat_info(*key)
        duration = float(info.get("duration") or 0)
        count = max(1, int((duration + media_tools.HLS_SEGMENT_SECONDS - 0.001) // media_tools.HLS_SEGMENT_SECONDS))
        if index < 0 or index >= count:
            raise HTTPException(404, "no such segment")
        fut = _hls_future(key, index, info)
        for ahead in (1, 2):
            if index + ahead < count:
                _hls_future(key, index + ahead, info)
        try:
            data = await asyncio.shield(fut)
        except Exception as e:
            raise HTTPException(502, f"conversion failed: {e}")
        return Response(data, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


    # ------------------------------------------------------------------
    # SUBSECTION: External devices and players
    # Device links and external player sessions.
    # ------------------------------------------------------------------
    # Players on another device (laptop/phone VLC) use the signed plain-HTTP feed.
    def _device_url(request: Request, channel_id: int, message_id: int) -> str:
        host = request.url.hostname or config.local_ip()
        if host in ("localhost", "::1"):
            host = "127.0.0.1"
        return (f"http://{host}:{_player_port()}/video/{int(message_id)}"
                f"?c={int(channel_id)}&t={tokens.make(int(channel_id), int(message_id), ttl=7 * 24 * 3600)}")

    @router.get("/api/play/device-link/{channel_id}/{message_id}")
    async def play_device_link(channel_id: int, message_id: int, request: Request):
        """Return a VLC/iOS/Android-safe signed LAN playback URL."""
        require_auth(request)
        remote = (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost")
        # Use the dedicated signed HTTP player feed for LAN devices. It is bound
        # on the LAN address by the application, avoiding browser-session cookies
        # that VLC on iOS cannot supply.
        return {"url": _device_url(request, channel_id, message_id),
                "lan": config.LOCAL_PLAYER_HOST != "127.0.0.1",
                "same_pc": not remote,
                "port": _player_port(),
                "transport": "signed-http"}

    @router.post("/api/play/device-playlist-link")
    async def play_device_playlist_link(request: Request):
        """Create a short-lived HTTP M3U URL for VLC on a remote device."""
        require_auth(request)
        from fastapi.responses import JSONResponse
        data = await request.json()
        items = []
        seen = set()
        for item in (data.get("items") or [])[:5000]:
            try:
                c, m = int(item["channel_id"]), int(item["message_id"])
                key = (c, m)
                if key in seen:
                    continue
                seen.add(key)
                items.append((c, m, str(item.get("name") or "").replace("\r", " ").replace("\n", " ")))
            except Exception:
                continue
        if not items:
            raise HTTPException(400, "no videos")
        for c, m, _name in items:
            require_playback_allowed(c, m)
        try:
            engine = await _get_engine()
            engine.set_play_order([(c, m) for c, m, _ in items])
        except Exception:
            pass
        playlist_dir = config.PLAYER_CACHE_DIR / "device_playlists"
        playlist_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for old in playlist_dir.glob("*.m3u"):
            try:
                if now - old.stat().st_mtime > 12 * 3600:
                    old.unlink()
            except Exception:
                pass
        playlist_id = secrets.token_hex(16)
        path = playlist_dir / f"{playlist_id}.m3u"
        lines = ["#EXTM3U"]
        for c, m, name in items:
            lines += [f"#EXTINF:-1,{name or f'Telegram video {m}'}", _device_url(request, c, m)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        host = request.url.hostname or config.local_ip()
        if host in ("localhost", "::1"):
            host = "127.0.0.1"
        url = f"http://{host}:{_player_port()}/playlist/{playlist_id}.m3u"
        return JSONResponse({"url": url, "count": len(items), "expires_in": 12 * 3600})

    @router.post("/api/play/device-playlist")
    async def play_device_playlist(request: Request):
        """M3U playlist with links for a player on the device that asks."""
        require_auth(request)
        from fastapi.responses import Response
        data = await request.json()
        items = []
        for item in (data.get("items") or [])[:5000]:
            try:
                items.append((int(item["channel_id"]), int(item["message_id"]),
                              str(item.get("name") or "").replace("\r", " ").replace("\n", " ")))
            except Exception:
                continue
        if not items:
            raise HTTPException(400, "no videos")
        for c, m, _name in items:
            require_playback_allowed(c, m)
        try:
            engine = await _get_engine()
            engine.set_play_order([(c, m) for c, m, _ in items])
        except Exception:
            pass
        lines = ["#EXTM3U"]
        for c, m, name in items:
            lines += [f"#EXTINF:-1,{name or f'Telegram video {m}'}", _device_url(request, c, m)]
        return Response("\n".join(lines) + "\n", media_type="audio/x-mpegurl",
                        headers={"Content-Disposition": 'attachment; filename="telegram-archive.m3u"',
                                 "Cache-Control": "no-store"})

    @router.post("/api/play/external")
    async def play_external(request: Request):
        """Open one video or a shuffled playlist in VLC / MPC-HC.

        Same model as telegram_player.py: a local M3U of 127.0.0.1 range URLs,
        the first 512 KiB range fetched before the player opens, then the current
        video fills the disk cache while the next videos are pre-warmed."""
        require_auth(request)
        data = await request.json()
        player = str(data.get("player") or "vlc").strip().lower()
        if player not in ("vlc", "mpc", "mpc-hc"):
            raise HTTPException(400, "player must be vlc or mpc-hc")
        playlist = data.get("playlist")
        items = []
        seen = set()
        source = playlist if isinstance(playlist, list) and playlist else [data]
        for item in source:
            try:
                c, m = int(item["channel_id"]), int(item["message_id"])
            except Exception:
                continue
            if (c, m) in seen:
                continue
            seen.add((c, m))
            items.append({"channel_id": c, "message_id": m, "name": str(item.get("name") or "")})
        if not items:
            raise HTTPException(400, "channel_id and message_id are required")
        for item in items:
            require_playback_allowed(item["channel_id"], item["message_id"])
        from ..playback import vlc_launcher
        if player in ("mpc", "mpc-hc") and not vlc_launcher.find_mpc_hc():
            raise HTTPException(400, "MPC-HC was not found on this PC")
        if player == "vlc" and not vlc_launcher.find_vlc():
            raise HTTPException(400, "VLC was not found on this PC")

        engine, first_message = await _engine_message(items[0]["channel_id"], items[0]["message_id"])
        first_key = (items[0]["channel_id"], items[0]["message_id"])
        engine.set_play_order([(x["channel_id"], x["message_id"]) for x in items])
        # External players should become visible quickly. One foreground segment
        # is enough to prove the signed HTTP path and let VLC/MPC-HC start; the
        # remaining head/tail ranges are admitted as background work. The previous
        # four-segment blocking warm-up made startup unnecessarily dependent on
        # Telegram latency, especially for large HEVC files.
        try:
            await engine.warm_first_segment(first_key, first_message, timeout=15.0, segments=1)
            engine.prepare_video(first_key, first_message, head_segments=2)
        except Exception as e:
            db.log(f"External playback: first segment warm-up failed ({type(e).__name__}: {e})", "ERROR")
            raise HTTPException(503, f"Playback warm-up failed: {e}")
        engine.begin_video(first_key, first_message, 0)

        cache_dir = Path(config.DATA_DIR) / "player_cache" / "playlists"
        cache_dir.mkdir(parents=True, exist_ok=True)
        for old in cache_dir.glob("*.m3u"):
            try:
                if old.stat().st_mtime < time.time() - 86400:
                    old.unlink()
            except Exception:
                pass
        kind = "random" if len(items) > 1 else "single"
        launch_source = None
        if len(items) == 1:
            # A single video does not need an M3U indirection. Direct HTTP keeps
            # MPC-HC/VLC on the actual media URL and avoids an extra playlist parse.
            launch_source = _local_url(items[0]["channel_id"], items[0]["message_id"])
        else:
            playlist_path = cache_dir / f"{kind}_{secrets.token_hex(6)}.m3u"
            lines = ["#EXTM3U"]
            for i, item in enumerate(items):
                name = item["name"] or (first_message.file.name if i == 0 else "") or f"Telegram video {item['message_id']}"
                name = name.replace("\r", " ").replace("\n", " ")
                lines.extend([f"#EXTINF:-1,{name}", _local_url(item["channel_id"], item["message_id"])])
            playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            launch_source = str(playlist_path)

        # Player existence was checked before building the temporary playlist.
        try:
            result = await asyncio.to_thread(vlc_launcher.launch, launch_source, player, str(items[0].get("name") or "Archive X"))
        except FileNotFoundError as e:
            raise HTTPException(400, str(e))
        # Bind the native player process to the complete video set it owns.
        # A later status poll can then stop remaining range/read-ahead work even
        # when the user closes the player window directly (not through Archive X).
        vlc_launcher.register_session(
            result["pid"],
            [(x["channel_id"], x["message_id"]) for x in items],
        )
        # Server-side monitoring remains active even if the browser tab closes.
        # The task lives on the app loop so playback-engine teardown is not called
        # from a foreign monitoring thread.
        pid = int(result["pid"])
        old_monitor = _external_player_monitors.get(pid)
        if old_monitor is not None and not old_monitor.done():
            old_monitor.cancel()
        _external_player_monitors[pid] = asyncio.create_task(
            _monitor_external_player(pid), name=f"external-player-watch-{pid}"
        )
        db.log(f"Started {result['player']} with {len(items)} video(s); native log={result.get('log_path','')}")
        try:
            from ..core.diagnostics import record_event
            record_event(event="playback.external.launch", level="INFO",
                         message=f"Started external player {result['player']}",
                         details={"player": result["player"], "pid": int(result["pid"]),
                                  "item_count": len(items), "log_path": result.get("log_path", ""),
                                  "direct_single": len(items) == 1})
        except Exception:
            pass
        return {"ok": True, **result, "random": len(items) > 1, "count": len(items),
                "playlist": str(launch_source) if len(items) > 1 else None}

    def _finish_external_playback_session(pid: int, reason: str) -> dict:
        """Release every range/cache job owned by one closed external player.

        This is intentionally idempotent: a stop request and an overlapping
        status poll may race, but only the first consumes the registered session.
        """
        from ..playback import vlc_launcher
        session = vlc_launcher.take_session(pid)
        if not session:
            return {"released_videos": 0, "session_found": False}
        keys = list(session.get("videos") or [])
        eng = ps.get("engine")
        released = 0
        if eng is not None and hasattr(eng, "release_video"):
            # Only clear play order when it is still precisely the order assigned
            # by this external-player session; newer browser playback may have
            # installed a different queue while the native app was running.
            try:
                current_order = [tuple(x) for x in (getattr(eng, "play_order", []) or [])]
                normalized_keys = [tuple(x) for x in keys]
                if current_order == normalized_keys:
                    eng.set_play_order([])
            except Exception:
                pass
            for key in keys:
                try:
                    eng.release_video(tuple(key), reason=("external_" + str(reason))[:40])
                    released += 1
                except Exception as exc:
                    try:
                        db.log(f"External player cleanup failed for {key}: {type(exc).__name__}: {exc}", "WARN",
                               event="playback.external.cleanup_failed", details={"pid": int(pid), "video": list(key)})
                    except Exception:
                        pass
        try:
            db.log(f"External player session closed ({reason}); released {released}/{len(keys)} video resource(s)",
                   "INFO", event="playback.external.closed", details={"pid": int(pid), "reason": reason,
                   "video_count": len(keys), "released_videos": released})
        except Exception:
            pass
        return {"released_videos": released, "session_found": True}

    async def _monitor_external_player(pid: int) -> None:
        """Observe an Archive X-launched native player until it exits.

        This monitor is independent of the browser's status poll, so closing or
        navigating away from the Archive X tab cannot orphan the playback queue.
        """
        from ..playback import vlc_launcher
        try:
            while True:
                await asyncio.sleep(1.0)
                result = vlc_launcher.status(int(pid))
                if not result.get("running"):
                    _finish_external_playback_session(int(pid), "process_closed")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                db.log(f"External player monitor failed for PID {pid}: {type(exc).__name__}: {exc}", "WARN",
                       event="playback.external.monitor_failed", details={"pid": int(pid)})
            except Exception:
                pass
        finally:
            current = _external_player_monitors.get(int(pid))
            if current is asyncio.current_task():
                _external_player_monitors.pop(int(pid), None)

    @router.get("/api/play/external/status/{pid}")
    async def play_external_status(pid: int, request: Request):
        """Return player status and release playback work when its process exits."""
        require_auth(request)
        from ..playback import vlc_launcher
        result = vlc_launcher.status(int(pid))
        cleanup = _finish_external_playback_session(int(pid), "process_closed") if not result.get("running") else {}
        return {"ok": True, "pid": int(pid), **result, **cleanup}

    @router.post("/api/play/external/stop/{pid}")
    async def play_external_stop(pid: int, request: Request):
        """Stop a player and release its entire associated playback job/session."""
        require_auth(request)
        from ..playback import vlc_launcher
        stopped = vlc_launcher.stop(int(pid))
        cleanup = _finish_external_playback_session(int(pid), "operator_stop")
        return {"ok": True, "stopped": bool(stopped), "pid": int(pid), **cleanup}


    # ------------------------------------------------------------------
    # SUBSECTION: Scan schedule
    # Scan schedule read route.
    # ------------------------------------------------------------------
    @router.get("/api/settings/scan-schedule")
    async def get_scan_schedule(request: Request):
        require_auth(request)
        return {"enabled": str(db.setting("metadata_scan_window_enabled", "0") or "0") == "1",
                "start": str(db.setting("metadata_scan_window_start", "01:00") or "01:00"),
                "end": str(db.setting("metadata_scan_window_end", "07:00") or "07:00")}
