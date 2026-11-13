"""Explicit registration for browser playback, media probes, and local caches."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Channel index and system probes
#       - Stream and playback session
#       - Media probing
#       - Preload and range cache
#       - Download and buffer status
#       - Direct and compatibility assets
#       - Seek previews
#       - Compatibility plan and cached streams
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import collections
import copy
import json
import math
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..core.hardware_adaptive import apply_network_profile, run_internet_test


def register_playback_routes(
    router: APIRouter,
    *,
    config,
    db,
    ps,
    resources,
    require_auth,
    _engine_message,
    _get_engine,
    _index_state,
    _index_status,
    _local_url,
    _playback_claim_from,
    _validated_snapshot_counts_sync,
    engine_lock,
    index_tasks,
    require_playback_allowed,
) -> dict[str, object]:

    # ------------------------------------------------------------------
    # SUBSECTION: Channel index and system probes
    # Index status, videos, hardware and internet.
    # ------------------------------------------------------------------
    """Register browser playback, probe and local cache routes."""
    @router.get("/api/channels/{channel_id}/index-status")
    async def channel_index_status(channel_id: int, request: Request):
        """GET /api/channels/{channel_id}/index-status: Channel index status."""
        require_auth(request)
        state, freshness = await asyncio.to_thread(_index_state, channel_id)
        result = _index_status(index_tasks.get(channel_id))
        # Inventory and required playback assets are separate phases. Do not
        # label the whole channel 100% until both index passes and the frame
        # readiness checks are complete. Asset validation itself is never done
        # synchronously in this status handler.
        indexed_total = int((state or {}).get("record_count") or 0)
        # Decode actual files on first inspection, then reuse results only while
        # their per-file size/mtime signature is unchanged. This prevents stale
        # READY rows from reporting 100% after cache loss or corruption.
        asset_total, asset_ready = await asyncio.to_thread(_validated_snapshot_counts_sync, int(channel_id))
        index_complete = bool((state or {}).get("complete")) and not bool((state or {}).get("dirty"))
        overall_complete = index_complete and asset_total == indexed_total and asset_ready == asset_total
        result.update({"freshness": freshness, "state": state,
                       "inventory_complete": index_complete,
                       "indexed_total": indexed_total,
                       "assets_total": asset_total,
                       "assets_ready": asset_ready,
                       "overall_complete": overall_complete,
                       "progress_percent": 100 if overall_complete else min(99, int(100 * asset_ready / max(asset_total, indexed_total, 1)))})
        return result

    @router.get("/api/channels/{channel_id}/videos")
    def list_channel_videos(channel_id: int, request: Request):
        """Return only fully ready videos for the selected Library channel.

        Pending inventory remains accessible through processing tools, not the
        published Library. This handler is local/read-only and does not start work.
        """
        require_auth(request)
        with db.reading() as _rdb:
            channel = _rdb.execute("SELECT * FROM channels WHERE telegram_id=?", (str(channel_id),)).fetchone()
            if not channel:
                # V29.4.129: a Telegram source without a channels row (the
                # account left it) is still a Library group when it has
                # published videos.
                if not _rdb.execute("SELECT 1 FROM archive_x_published_media WHERE source_channel=? LIMIT 1", (str(channel_id),)).fetchone():
                    raise HTTPException(404, "Channel not found")
                channel = {"title": "Unknown channel"}
            rows = _rdb.execute(
                "SELECT m.*, 1 AS library_ready, 'Ready' AS library_state, "
                "'COMPLETE' AS library_catalogue_state, 'COMPLETE' AS library_metadata_state, "
                "'COMPLETE' AS library_entity_state, 'READY' AS library_snapshot_state, "
                "GROUP_CONCAT(DISTINCT p.name) performers, "
                "GROUP_CONCAT(DISTINCT lp.name) local_performers, "
                "GROUP_CONCAT(DISTINCT s.name) studios, "
                "GROUP_CONCAT(DISTINCT t.name) tags "
                 "FROM media m "
                "LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id "
                "LEFT JOIN local_video_performers lvp ON lvp.video_id=m.id LEFT JOIN performers lp ON lp.id=lvp.performer_id "
                "LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id "
                "LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id "
                "WHERE m.source_channel=? GROUP BY m.id ORDER BY m.upload_date DESC",
                (str(channel_id),)).fetchall()
        result=[]
        for r in rows:
            d=dict(r)
            d["performers"]=list(dict.fromkeys([x for x in (d.get("performers") or "").split(",") if x]+[x for x in (d.get("local_performers") or "").split(",") if x]))
            d["local_performers"]=[x for x in (d.get("local_performers") or "").split(",") if x]
            d["studios"]=[x for x in (d.get("studios") or "").split(",") if x]
            d["studio"]=d["studios"][0] if d["studios"] else ""
            d["tags"]=[x for x in (d.get("tags") or "").split(",") if x]
            d["channel_id"]=str(channel_id); d["_cid"]=str(channel_id)
            d["id"]=d.get("source_message_id")
            d["name"]=d.get("title") or d.get("filename") or f"video {d['id']}"
            d["telegram_filename"]=d.get("filename") or ""
            d["thumbnail_id"]=d.get("thumbnail_message_id") or d.get("source_message_id")
            result.append(d)
        ready_count = sum(1 for item in result if bool(item.get("library_ready")))
        return {"channel_id": int(channel_id), "title": channel["title"], "count": len(result), "ready_count": ready_count, "videos": result, "catalogued_only": False, "published_only": True}

    @router.get("/api/system/hardware")
    async def get_system_hardware(request: Request):
        require_auth(request)
        return {"ok": True, **(getattr(request.app.state, "archive_x_hardware", None) or {})}

    @router.post("/api/system/internet-test")
    async def test_system_internet(request: Request):
        require_auth(request)
        result = await run_internet_test()
        current = getattr(request.app.state, "archive_x_hardware", None) or {}
        updated = apply_network_profile(current, result)
        request.app.state.archive_x_hardware = updated
        if resources is not None:
            resources.hardware_profile = updated
            # A measured slow route should not make background work more aggressive.
            mode = updated.get("network_mode")
            base = int(updated.get("background_capacity") or resources.background_capacity or 1)
            if mode == "conservative": resources.background_capacity = max(1, min(base, 2))
            elif mode == "balanced": resources.background_capacity = max(1, min(base, 3))
            else: resources.background_capacity = max(1, min(base, 6))
        return {"ok": True, **result, "network_mode": updated.get("network_mode", "adaptive"),
                "background_capacity": getattr(resources, "background_capacity", None),
                "tested_profile": {"network_mode": updated.get("network_mode"), "background_capacity": getattr(resources, "background_capacity", None)}}


    # ------------------------------------------------------------------
    # SUBSECTION: Stream and playback session
    # Stream route, telemetry, stop and release.
    # ------------------------------------------------------------------
    @router.get("/stream/{channel_id}/{message_id}")
    async def stream(channel_id: int, message_id: int, request: Request):
        """Browser playback through the persistent, segment-aligned cache."""
        require_auth(request)
        from fastapi.responses import Response, StreamingResponse
        from ..playback.stream_server import parse_range, content_disposition, _stream_log
        import time as _time
        _PLAYBACK_RANGE_MAX = 2 * 1024 * 1024   # 2 MiB cap for open-ended ranges
        _PLAYBACK_RANGE_DEADLINE = 12.0          # seconds per range fetch
        _t0 = _time.monotonic()
        def _lap(stage):
            dt = _time.monotonic() - _t0
            if dt > 1.0:
                _stream_log(f"STREAM slow stage={stage} t={dt:.1f}s channel={channel_id} message={message_id} "
                            f"prebuffer={request.headers.get('x-archive-x-prebuffer') == '1'} range={request.headers.get('range', '')}")
        engine, message = await _engine_message(
            channel_id, message_id,
            playback_claim=_playback_claim_from(request.query_params),
        )
        _lap("engine_message")
        try:
            # Scan-time location manifests are durable but their file_reference
            # may be stale and their proxy media wrapper is not a live Message.
            # Refresh the real message once on Play; subsequent ranges reuse it.
            message = await engine.get_playback_message(channel_id, message_id)
            _lap("playback_message")
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:
            raise HTTPException(503, f"Could not resolve Telegram playback message: {type(exc).__name__}: {exc}")

        is_prebuffer = request.headers.get("x-archive-x-prebuffer") == "1"

        async def read_cached_range(part_message, start_byte, end_byte):
            if is_prebuffer:
                return await asyncio.wait_for(
                    engine.read_browser_prebuffer_range(
                        channel_id, part_message, int(start_byte), int(end_byte),
                    ),
                    timeout=_PLAYBACK_RANGE_DEADLINE,
                )
            return await asyncio.wait_for(
                engine.read_browser_range(
                    channel_id, part_message, int(start_byte), int(end_byte),
                ),
                timeout=_PLAYBACK_RANGE_DEADLINE,
            )

        from ..core.split_files import load_split_group, map_split_range, SplitFileError, _PLAYABLE_PART_RE
        try:
            split_group = await asyncio.to_thread(load_split_group, db, str(channel_id), int(message_id))
        except SplitFileError as exc:
            raise HTTPException(503, f"Split-file group is incomplete or inconsistent: {exc}") from exc
        _lap("split_group")
        if split_group is not None:
            # Playable-part groups: each part is independently streamable.
            # When the player requests an individual part's message_id, skip
            # the byte-assembly path and let it stream as a normal video.
            _sg_parts = split_group.get("parts") or []
            _sg_is_playable = any(
                _PLAYABLE_PART_RE.fullmatch(str(p.get("telegram_filename") or ""))
                for p in _sg_parts
            )
            if _sg_is_playable:
                # For playable parts, fall through to normal single-video streaming.
                # The player handles sequencing across parts via play_info metadata.
                split_group = None
        if split_group is not None:
            group_status = str(split_group.get("status") or "").upper()
            if group_status != "VERIFIED":
                raise HTTPException(503, "Split-file sequence is not complete and verified yet; refusing to serve a partial file")
            size = int(split_group.get("total_size") or 0)
            if size <= 0 or not split_group.get("parts"):
                raise HTTPException(503, "Split-file group has no valid volume sequence")
            range_header = request.headers.get("range", "")
            if not range_header:
                # A raw GET has no Range header and therefore must return a full
                # 200 representation. Preflight every remote volume before HTTP
                # headers are sent: otherwise a missing later part can turn a
                # seemingly successful response into a silently truncated body.
                cached_messages = {}
                try:
                    for part in split_group["parts"]:
                        part_id = int(part["message_id"])
                        part_message = message if int(getattr(message, "id", 0) or 0) == part_id else await engine.get_playback_message(channel_id, part_id)
                        expected_size = int(part["size_bytes"])
                        actual_size = int(getattr(getattr(part_message, "file", None), "size", 0) or 0)
                        if part_message is None or actual_size != expected_size:
                            raise SplitFileError(
                                f"Telegram volume {part.get('telegram_filename') or part_id} has size {actual_size}; expected {expected_size}"
                            )
                        cached_messages[part_id] = part_message
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise HTTPException(503, f"Split-file sequence is not fully available: {type(exc).__name__}: {exc}") from exc
                # Stream bounded chunks across Telegram volumes instead of
                # joining a multi-GB file in memory.
                whole_headers = {
                    "Accept-Ranges": "bytes", "Content-Length": str(size),
                    "Content-Disposition": content_disposition(str(split_group.get("logical_filename") or "download.bin")),
                    "Cache-Control": "private, max-age=30, must-revalidate", "Vary": "Cookie, Range",
                    "ETag": f'"{str(split_group.get("group_id") or "split")[:24]}-{size:x}"',
                    "X-Archive-X-Playback-Delivery": "split-stream",
                }

                async def stream_complete_split_file():
                    try:
                        chunk_size = 4 * 1024 * 1024
                        chunk_start = 0
                        while chunk_start < size:
                            chunk_end = min(size - 1, chunk_start + chunk_size - 1)
                            ranges = map_split_range(split_group["parts"], chunk_start, chunk_end, total_size=size)
                            for part_range in ranges:
                                part_message = cached_messages.get(int(part_range.message_id))
                                if part_message is None:
                                    part_message = await engine.get_playback_message(channel_id, int(part_range.message_id))
                                    cached_messages[int(part_range.message_id)] = part_message
                                expected_size = int(split_group["parts"][part_range.part_index - 1]["size_bytes"])
                                actual_size = int(getattr(getattr(part_message, "file", None), "size", 0) or 0)
                                if actual_size != expected_size:
                                    raise RuntimeError(
                                        f"Telegram volume {part_range.telegram_filename or part_range.message_id} "
                                        f"has size {actual_size}; expected {expected_size}"
                                    )
                                data = await read_cached_range(
                                    part_message, part_range.local_start, part_range.local_end,
                                )
                                expected_piece = part_range.local_end - part_range.local_start + 1
                                if len(data) != expected_piece:
                                    raise RuntimeError(
                                        f"Short range from volume {part_range.telegram_filename or part_range.message_id}: "
                                        f"received {len(data)} of {expected_piece} bytes"
                                    )
                                yield data
                            chunk_start = chunk_end + 1
                    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
                        pass
                    except asyncio.CancelledError:
                        pass

                return StreamingResponse(stream_complete_split_file(), status_code=200,
                                         headers=whole_headers,
                                         media_type=str(split_group.get("mime_type") or "application/octet-stream"))
            try:
                requested_start, requested_end, requested_status = parse_range(size, range_header)
                start, end, status = parse_range(size, range_header, max_bytes=_PLAYBACK_RANGE_MAX)
            except ValueError:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            pieces = []
            try:
                mapped_ranges = map_split_range(split_group["parts"], start, end, total_size=size)
                for part_range in mapped_ranges:
                    part_id = int(part_range.message_id)
                    part_size = int(split_group["parts"][part_range.part_index - 1]["size_bytes"])
                    part_message = message if int(getattr(message, "id", 0) or 0) == part_id else await engine.get_playback_message(channel_id, part_id)
                    actual_size = int(getattr(getattr(part_message, "file", None), "size", 0) or 0)
                    if actual_size != part_size:
                        raise SplitFileError(
                            f"Telegram volume {part_range.telegram_filename or part_id} has size {actual_size}; expected {part_size}"
                        )
                    data = await read_cached_range(
                        part_message, part_range.local_start, part_range.local_end,
                    )
                    expected_piece = part_range.local_end - part_range.local_start + 1
                    if len(data) != expected_piece:
                        raise SplitFileError(
                            f"Short range from volume {part_range.telegram_filename or part_id}: "
                            f"received {len(data)} of {expected_piece} bytes"
                        )
                    pieces.append(data)
                body = b"".join(pieces)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(503, f"Split-file range could not be served safely: {type(exc).__name__}: {exc}") from exc
            expected_body = end - start + 1
            if len(body) != expected_body:
                raise HTTPException(503, f"Split-file range is incomplete: received {len(body)} of {expected_body} bytes")
            bounded = requested_status == 206 and (start != requested_start or end != requested_end)
            headers = {
                "Accept-Ranges": "bytes", "Content-Length": str(expected_body),
                "Content-Disposition": content_disposition(str(split_group.get("logical_filename") or "download.bin")),
                "Cache-Control": "private, max-age=30, must-revalidate", "Vary": "Cookie, Range",
                "ETag": f'"{str(split_group.get("group_id") or "split")[:24]}-{size:x}"',
                "X-Archive-X-Playback-Delivery": "materialized-range",
            }
            if status == 206:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            if bounded:
                headers["X-Archive-X-Range-Capped"] = "1"
            return Response(content=body, status_code=status, headers=headers,
                            media_type=str(split_group.get("mime_type") or "application/octet-stream"))
        size = int(message.file.size)
        mime = getattr(message.file, "mime_type", None) or "video/mp4"
        if mime == "application/octet-stream":
            name = str(getattr(message.file, "name", "") or "").lower()
            if name.endswith((".mp4", ".m4v", ".mov")):
                mime = "video/mp4"
            elif name.endswith((".webm",)):
                mime = "video/webm"
        # Keep the source MIME type accurate. Matroska/H.264 is not WebM;
        # pretending it is video/webm can make browsers choose an invalid
        # demuxer and causes intermittent playback/decode failures.
        range_header = request.headers.get("range", "")
        try:
            requested_start, requested_end, requested_status = parse_range(size, range_header)
            start, end, status = parse_range(
                size, range_header, max_bytes=_PLAYBACK_RANGE_MAX,
            )
        except ValueError:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        range_was_bounded = (
            requested_status == 206
            and (start != requested_start or end != requested_end)
        )
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Disposition": content_disposition(getattr(message.file, "name", None) or f"video_{message_id}.mp4"),
            "Cache-Control": "private, max-age=30, must-revalidate",
            "Vary": "Cookie, Range",
            "ETag": (
                f'"{int(getattr(getattr(message, "document", None), "id", 0) or 0):x}-{size:x}"'
            ),
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if range_was_bounded:
            headers["X-Archive-X-Range-Capped"] = "1"
        async def stream_browser_range():
            # Persisted segments stream as they are ready; a client that disconnects (navigated away, closed tab)
            # is swallowed so the playback engine's resources are released cleanly.
            try:
                async for chunk in engine.iter_range(
                    channel_id, message, start, end, mode="play", browser=True,
                ):
                    if chunk:
                        yield chunk
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
                pass
            except asyncio.CancelledError:
                pass

        # A plain GET streams cache-sized segments so large videos are never
        # assembled in memory. Each successful segment is persisted by the
        # playback engine and can be reused after the player or app is reopened.
        if not range_header:
            headers["X-Archive-X-Playback-Delivery"] = "persistent-segment-stream"
            return StreamingResponse(
                stream_browser_range(), status_code=status, headers=headers, media_type=mime,
            )

        # Startup prebuffer requests need a complete response so fetch() can
        # validate the bounded range. Normal browser ranges stream each segment
        # as soon as it is ready; the playback engine persists that same segment
        # before yielding it, without holding the browser until the full Range
        # body has been collected.
        _lap("before_range")
        try:
            if is_prebuffer:
                data = await read_cached_range(message, start, end)
                _lap("prebuffer_range_done")
                if len(data) != (end - start + 1):
                    raise HTTPException(503, f"Playback prebuffer incomplete: received {len(data)} of {end - start + 1} bytes")
                headers["X-Archive-X-Playback-Delivery"] = "persistent-segment-prebuffer"
                return Response(content=data, status_code=status, headers=headers, media_type=mime)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                _lap("prebuffer_timeout")
                raise HTTPException(503, f"Telegram playback prebuffer timed out: {exc}") from exc
            raise HTTPException(503, f"Telegram playback prebuffer failed: {type(exc).__name__}: {exc}") from exc

        headers["X-Archive-X-Playback-Delivery"] = "persistent-segment-stream"
        return StreamingResponse(
            stream_browser_range(), status_code=status, headers=headers, media_type=mime,
        )

    @router.post("/api/play/upcoming")
    async def play_upcoming(request: Request):
        """The browser tells the engine what plays next so it can be warmed."""
        require_auth(request)
        data = await request.json()
        keys = []
        for item in (data.get("items") or [])[:8]:
            try:
                key = (int(item["channel_id"]), int(item["message_id"]))
                if key not in keys:
                    keys.append(key)
            except Exception:
                continue
        selected_current = None
        try:
            current = data.get("current") or {}
            candidate = (int(current["channel_id"]), int(current["message_id"]))
            if candidate in keys:
                selected_current = candidate
        except Exception:
            pass
        if not keys:
            return {"ok": True}
        for c, m in keys:
            require_playback_allowed(c, m)
        engine = await _get_engine()
        engine.set_play_order(keys)
        if selected_current and len(keys) > 1:
            # The browser can have warmed the current video's source before the
            # user presses Play, so there may be no active range request now.
            # The explicit Play intent is enough to begin the low-priority
            # ten-second next-video prefix; foreground ranges still outrank it.
            engine.start_next_prefix(selected_current)
        elif engine.current_key in keys and len(keys) > 1:
            engine.start_next_prefix(engine.current_key)
        return {"ok": True}

    play_telemetry = {"count": 0, "startup_ms": [], "first_frame_ms": [], "rebuffer": 0, "seeks": 0}

    # Take player telemetry from the browser and update the engine.
    @router.post("/api/play/telemetry")
    async def play_telemetry_route(request: Request):
        require_auth(request)
        try:
            data = await request.json()
            startup = data.get("startup_ms")
            reason = str(data.get("reason") or "")
            session_id = str(data.get("session_id") or "").strip()[:80]
            if isinstance(startup, (int, float)) and startup >= 0 and reason != "first-frame":
                play_telemetry["startup_ms"].append(float(startup))
                play_telemetry["startup_ms"] = play_telemetry["startup_ms"][-200:]
            first_frame = data.get("first_frame_ms")
            if reason == "first-frame" and isinstance(first_frame, (int, float)) and first_frame >= 0:
                play_telemetry["first_frame_ms"].append(float(first_frame))
                play_telemetry["first_frame_ms"] = play_telemetry["first_frame_ms"][-200:]
                if session_id:
                    from ..core.diagnostics import ingest_browser_diagnostics, record_event
                    telemetry_event = {
                        "type": "playback.first_frame", "level": "INFO",
                        "message": "Time from Play click to first rendered video frame",
                        "page": "/", "data": {
                            "channel_id": data.get("channel_id"), "message_id": data.get("message_id"),
                            "click_to_first_frame_ms": float(first_frame), "mode": data.get("mode"),
                            "state": data.get("state"), "session_ms": data.get("session_ms"),
                        },
                    }
                    ingest_browser_diagnostics(session_id=session_id,
                        snapshot={"session_id": session_id, "captured_at": config.now_iso(), "playback_first_frame_ms": float(first_frame)},
                        events=[telemetry_event], resources=[])
                    record_event(event="playback.first_frame", level="INFO",
                        message=f"Video first frame rendered after {float(first_frame):.0f} ms",
                        details={"component":"playback", **telemetry_event["data"]})
            if reason != "first-frame":
                play_telemetry["rebuffer"] += max(0, int(data.get("rebuffer_count") or 0))
                play_telemetry["seeks"] += max(0, int(data.get("seek_count") or 0))
            # Browser buffer is a separate signal from server-side cache
            # continuity. When fresh, it drives the playback QoS emergency
            # state so a browser starvation condition can immediately reserve
            # the full foreground worker/range lane.
            buffered_seconds = data.get("buffered_seconds")
            if isinstance(buffered_seconds, (int, float)) and buffered_seconds >= 0:
                try:
                    eng = await _get_engine()
                    if eng is not None:
                        eng.qos.observe_browser_buffer(
                            float(buffered_seconds),
                            target_buffer_seconds=data.get("target_buffer_seconds"),
                        )
                        if eng.governor is not None:
                            eng.governor.set_playback_emergency(
                                eng.qos.context.state in {"STARVING", "STALLED"}
                            )
                        eng.scheduler.wake()
                except Exception:
                    pass
            if reason != "first-frame":
                play_telemetry["count"] += 1
            return {"ok": True}
        except Exception:
            return {"ok": False}

    async def _finish_playback_stop(eng):
        """Perform the slower teardown after the Stop API has already returned."""
        try:
            if resources is not None:
                try:
                    await asyncio.wait_for(resources.interactive_playback_exit(), timeout=2.0)
                except Exception as exc:
                    db.log(f"Playback resource release failed: {type(exc).__name__}: {exc}", "WARN")
            try:
                await asyncio.wait_for(eng.stop(), timeout=10.0)
            except asyncio.TimeoutError:
                db.log("Playback teardown exceeded 10 seconds; engine was detached so the Stop control remains responsive", "WARN")
            except Exception as exc:
                db.log(f"Playback teardown failed: {type(exc).__name__}: {exc}", "WARN")
            else:
                db.log("Interactive playback teardown completed", "INFO")
        finally:
            if ps.get("_playback_stop_task") is asyncio.current_task():
                ps["_playback_stop_task"] = None

    @router.post("/api/play/stop")
    async def play_stop(request: Request):
        """Detach playback immediately, then release Telegram/cache resources asynchronously."""
        require_auth(request)
        async with engine_lock:
            current_stop = ps.get("_playback_stop_task")
            if current_stop is not None and not current_stop.done():
                return {"ok": True, "stopped": True, "stopping": True, "reason": "already_stopping"}
            eng = ps.get("engine")
            if eng is None:
                return {"ok": True, "stopped": False, "stopping": False, "reason": "not_running"}
            # Mark the engine idle and cancel in-flight range work synchronously.
            # The browser can return to the Library immediately; cleanup continues
            # off the request path and new Play requests wait for this task.
            request_stop = getattr(eng, "request_stop", None)
            if callable(request_stop):
                request_stop("operator")
            else:
                eng.running = False
            if ps.get("engine") is eng:
                ps["engine"] = None
            task = asyncio.create_task(_finish_playback_stop(eng), name="archive-x-playback-stop")
            ps["_playback_stop_task"] = task
        db.log("Interactive playback stop requested from Active Jobs; engine detached and cleanup scheduled", "INFO")
        return JSONResponse({"ok": True, "stopped": True, "stopping": True,
                             "message": "Playback stopped. Background resource cleanup is finishing."},
                            status_code=202, headers={"Cache-Control": "no-store"})

    @router.post("/api/play/release")
    async def play_release(request: Request):
        """V29.4.129: the viewer left a video (Back, or moved to another one).

        Stops every download of that video and deletes its temporary cache so
        only the video being watched has an active playback job.
        """
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            vkey = (int(body.get("channel_id")), int(body.get("message_id")))
        except (TypeError, ValueError):
            return {"ok": True, "released": False, "reason": "not_a_telegram_video"}
        eng = ps.get("engine")
        if eng is None or not hasattr(eng, "release_video"):
            return {"ok": True, "released": False, "reason": "not_running"}
        # Resource accounting (interactive playback flag) is left to the
        # activity/expiry flow: a late release must not end the next video's
        # start-up.
        claim = _playback_claim_from(body)
        release_kwargs = claim or {}
        result = eng.release_video(
            vkey, reason=str(body.get("reason") or "viewer_left")[:40],
            **release_kwargs,
        )
        return {"ok": True, "released": True, **result}

    @router.get("/api/play/status")
    def play_status(request: Request):
        """GET /api/play/status: Play status."""
        require_auth(request)
        eng = ps.get("engine")
        out = eng.status() if eng else {"running": False}
        vals = sorted(play_telemetry["startup_ms"])
        frame_vals = sorted(play_telemetry["first_frame_ms"])
        def _percentile(sorted_values, p):
            if not sorted_values:
                return None
            rank = max(0, min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * float(p)))))
            return round(float(sorted_values[rank]), 1)
        out["telemetry"] = {
            "sessions": play_telemetry["count"],
            "startup_ms_avg": round(sum(vals) / len(vals), 1) if vals else None,
            "startup_ms_p50": _percentile(vals, 0.50),
            "startup_ms_p95": _percentile(vals, 0.95),
            "startup_ms_max": round(vals[-1], 1) if vals else None,
            "first_frame_ms_avg": round(sum(frame_vals) / len(frame_vals), 1) if frame_vals else None,
            "first_frame_ms_p50": _percentile(frame_vals, 0.50),
            "first_frame_ms_p95": _percentile(frame_vals, 0.95),
            "first_frame_ms_max": round(frame_vals[-1], 1) if frame_vals else None,
            "rebuffer_events": play_telemetry["rebuffer"],
            "seeks": play_telemetry["seeks"],
        }
        out["preview_scheduler"] = preview_scheduler.status() if "preview_scheduler" in locals() else {}
        out["preview_stats"] = dict(preview_stats) if "preview_stats" in locals() else {}
        return out

    # ---------------- media info, compatibility, previews (v18.8) ----------------
    from ..playback import media_tools
    from ..playback.performance import PreviewScheduler

    probe_mem: dict = {}
    probe_tasks: dict = {}
    with db.lock:
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS media_probe (source_channel TEXT NOT NULL, message_id INTEGER NOT NULL,"
            " info TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (source_channel, message_id))")
        db.conn.commit()


    # ------------------------------------------------------------------
    # SUBSECTION: Media probing
    # Probe helpers and info.
    # ------------------------------------------------------------------
    def _probe_url(cid: int, mid: int, light: bool = True, mode: str | None = None) -> str:
        suffix = ""
        if light:
            suffix = "&m=" + (str(mode) if mode in {"probe", "preview", "prewarm"} else "probe")
        return _local_url(cid, mid, ttl=6 * 3600) + suffix

    def _probe_row(key):
        with db.lock:
            row = db.conn.execute("SELECT info FROM media_probe WHERE source_channel=? AND message_id=?",
                                  (str(key[0]), int(key[1]))).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["info"])
        except Exception:
            return None

    def _probe_save(key, info):
        with db.lock:
            db.conn.execute("INSERT OR REPLACE INTO media_probe(source_channel,message_id,info,updated_at) VALUES(?,?,?,?)",
                            (str(key[0]), int(key[1]), json.dumps(info), config.now_iso()))
            # Keep the catalogue's technical columns up to date as well.
            db.conn.execute(
                "UPDATE media SET codec=COALESCE(NULLIF(?,''),codec), audio_codec=COALESCE(NULLIF(?,''),audio_codec),"
                " width=CASE WHEN ?>0 THEN ? ELSE width END, height=CASE WHEN ?>0 THEN ? ELSE height END,"
                " duration=CASE WHEN ?>0 THEN ? ELSE duration END, fps=CASE WHEN ?>0 THEN ? ELSE fps END"
                " WHERE source_channel=? AND source_message_id=?",
                (info.get("vcodec") or "", info.get("acodec") or "",
                 int(info.get("width") or 0), int(info.get("width") or 0),
                 int(info.get("height") or 0), int(info.get("height") or 0),
                 float(info.get("duration") or 0), float(info.get("duration") or 0),
                 float(info.get("fps") or 0), float(info.get("fps") or 0),
                 str(key[0]), int(key[1])))
            db.conn.commit()

    async def _do_probe(key):
        # This routine is only admitted by the explicit Play/info path. Scans
        # populate media_probe from the cached MP4 index; never prefetch ranges
        # merely to inspect a video's details.
        engine, message = await _engine_message(*key)
        f = message.file
        base = {"size": int(f.size), "mime": getattr(f, "mime_type", "") or "",
                "name": getattr(f, "name", "") or ""}
        try:
            info = await media_tools.probe(_probe_url(*key), timeout=60)
        except FileNotFoundError:
            info = {"duration": float(getattr(f, "duration", 0) or 0), "probe_error": "ffprobe not found",
                    **base, "direct_likely": True}
            probe_mem[key] = info
            return info
        info.update(base)
        if not info.get("duration"):
            info["duration"] = float(getattr(f, "duration", 0) or 0)
        info.update(media_tools.browser_verdict(info, base["mime"]))
        probe_mem[key] = info
        await asyncio.to_thread(_probe_save, key, info)
        return info

    async def _get_info(cid, mid, wait: bool = True, timeout: float = 45.0):
        key = (int(cid), int(mid))
        if key in probe_mem:
            return probe_mem[key]
        row = await asyncio.to_thread(_probe_row, key)
        if row:
            probe_mem[key] = row
            return row
        task = probe_tasks.get(key)
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            task = asyncio.create_task(_do_probe(key), name=f"probe-{key[1]}")
            probe_tasks[key] = task

            def _done(t, k=key):
                if probe_tasks.get(k) is t:
                    probe_tasks.pop(k, None)
                if not t.cancelled() and t.exception() is not None:
                    db.log(f"Could not read video details {k[0]}/{k[1]}: {t.exception()}", "WARN")
            task.add_done_callback(_done)
        if not wait:
            return None
        return await asyncio.wait_for(asyncio.shield(task), timeout)


    # ------------------------------------------------------------------
    # SUBSECTION: Preload and range cache
    # Preload, cache badges, keep and clear.
    # ------------------------------------------------------------------
    @router.post("/api/play/preload")
    async def play_preload(request: Request):
        """Prepare an MP4 byte window for an explicit card click or resume prompt."""
        require_auth(request)
        try:
            data = await request.json()
            cid, mid = int(data["channel_id"]), int(data["message_id"])
            start_seconds = float(data.get("start_seconds", 0) or 0)
            seconds = float(data.get("seconds", 30) or 30)
        except Exception:
            raise HTTPException(400, "channel_id, message_id, and valid preload window are required")
        if not math.isfinite(start_seconds) or not math.isfinite(seconds):
            raise HTTPException(400, "preload window must use finite seconds")
        start_seconds = max(0.0, start_seconds)
        seconds = max(1.0, min(120.0, seconds))
        require_playback_allowed(cid, mid)
        engine = await _get_engine()
        playback_claim = _playback_claim_from(data)
        from ..core.split_files import load_split_group, SplitFileError
        try:
            split_group = await asyncio.to_thread(load_split_group, db, str(cid), int(mid))
        except SplitFileError as exc:
            raise HTTPException(503, f"Split-file group is incomplete or inconsistent: {exc}") from exc
        if split_group is not None:
            if str(split_group.get("status") or "").upper() != "VERIFIED":
                raise HTTPException(503, "Split-file sequence is not complete and verified yet")
            return {"ok": True, "status": "split-group-preload-deferred", "channel_id": cid, "message_id": mid}
        if playback_claim:
            try:
                claimed = engine.claim_playback((cid, mid), **playback_claim)
            except Exception as e:
                raise HTTPException(503, f"Playback handoff could not be registered: {type(e).__name__}")
            if not claimed:
                return {"ok": True, "status": "superseded", "channel_id": cid, "message_id": mid}
        speed_limit_bps = data.get("speed_limit_bps", 0)
        try:
            speed_limit_bps = int(float(speed_limit_bps or 0))
        except (TypeError, ValueError, OverflowError):
            speed_limit_bps = 0
        speed_limit_bps = max(0, min(20 * 1024 * 1024, speed_limit_bps))
        return engine.start_video_preload(
            (cid, mid), seconds=seconds, start_seconds=start_seconds,
            speed_limit_bps=speed_limit_bps,
        )

    @router.get("/api/play/range-cache/{channel_id}/{message_id}")
    async def play_range_cache_status(channel_id: int, message_id: int, request: Request):
        """Report persisted sparse byte coverage for the selected video."""
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        from ..playback.stream_server import range_cache_status_for_video
        return await asyncio.to_thread(range_cache_status_for_video, channel_id, message_id)

    @router.post("/api/play/mse-event")
    async def play_mse_event(request: Request):
        """Record a browser-side MSE/fMP4 startup problem in the diagnostics."""
        require_auth(request)
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        details = {"component": "playback", "channel_id": data.get("channel_id"), "message_id": data.get("message_id"),
                   "stage": str(data.get("stage") or "")[:40], "error": str(data.get("error") or "")[:300],
                   "mse": data.get("mse") if isinstance(data.get("mse"), dict) else {}}
        try:
            from ..core.diagnostics import record_event
            record_event(event="playback.mse_client", level="WARN",
                         message=f"MSE playback {details['stage'] or 'event'} in the browser", details=details)
        except Exception:
            pass
        db.log(f"MSE browser event {details['stage']} {details['channel_id']}/{details['message_id']}: {details['error']} {json.dumps(details['mse'])[:400]}", "WARN")
        return {"ok": True}

    @router.get("/api/play/range-cache-badges")
    async def play_range_cache_badges(request: Request):
        """Cached percentage of every partly cached video, for the library cards."""
        require_auth(request)
        from ..playback.stream_server import range_cache_badges
        return {"ok": True, "items": await asyncio.to_thread(range_cache_badges)}

    @router.post("/api/play/range-cache/{channel_id}/{message_id}/keep")
    async def play_range_cache_keep(channel_id: int, message_id: int, request: Request):
        """Keep (or un-keep) one video's cached byte ranges across restarts."""
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        try:
            body = await request.json()
        except Exception:
            body = {}
        kept = bool(body.get("kept", True)) if isinstance(body, dict) else True
        key = (int(channel_id), int(message_id))
        eng = ps.get("engine")
        if eng is not None:
            holders = [(k, c) for k, c in list(getattr(eng, "disk_caches", {}).items())]
            holders += [(getattr(c, "key", ()), c) for c in list(getattr(eng, "_pending_cleanup", ()))]
            for cache_key, cache in holders:
                if tuple(cache_key[:2]) == key:
                    cache.kept = kept
                    cache.last_access_epoch = time.time()
                    try:
                        await asyncio.to_thread(cache._persist_manifest_sync)
                    except Exception:
                        pass
        from ..playback.stream_server import set_range_cache_kept
        return await asyncio.to_thread(set_range_cache_kept, channel_id, message_id, kept)

    @router.post("/api/play/range-cache/{channel_id}/{message_id}/clear")
    async def play_range_cache_clear(channel_id: int, message_id: int, request: Request):
        """Discard one video's retained byte ranges after its player has closed."""
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        key = (int(channel_id), int(message_id))
        eng = ps.get("engine")
        if eng is not None:
            claims = getattr(eng, "playback_claims", {}).get(key, {})
            if any(bool(claim.get("active")) for claim in claims.values()):
                raise HTTPException(409, "This video is still active in another playback or download session. Close it first.")
            eng.release_video(key, reason="discard_buffer")
            for _ in range(60):
                pending_busy = any(getattr(cache, "key", None) == key and cache.busy()
                                   for cache in getattr(eng, "_pending_cleanup", set()))
                if not pending_busy:
                    break
                await asyncio.sleep(0.05)
            pending_busy = any(getattr(cache, "key", None) == key and cache.busy()
                               for cache in getattr(eng, "_pending_cleanup", set()))
            if pending_busy:
                raise HTTPException(409, "The last cache write is still finishing. Try again in a moment.")
            for skey in list(getattr(eng, "segments", {})):
                if tuple(skey[:2]) == key:
                    eng.segments.pop(skey, None)
        from ..playback.stream_server import clear_persistent_range_cache_for_video
        result = await asyncio.to_thread(clear_persistent_range_cache_for_video, *key)
        return {"ok": True, **result}


    # ------------------------------------------------------------------
    # SUBSECTION: Download and buffer status
    # Original download, buffer and play info.
    # ------------------------------------------------------------------
    @router.get("/api/play/download/{channel_id}/{message_id}")
    async def play_download_original(channel_id: int, message_id: int, request: Request):
        """Stream a complete original Telegram document, reusing any cached ranges."""
        require_auth(request)
        require_playback_allowed(channel_id, message_id)
        engine, message = await _engine_message(channel_id, message_id)
        try:
            size = int(getattr(message.file, "size", 0) or 0)
        except Exception:
            size = 0
        if size <= 0:
            raise HTTPException(404, "Telegram file size is unavailable")
        try:
            rate = max(0, min(20 * 1024 * 1024, int(float(request.query_params.get("speed_limit_bps", "0") or 0))))
        except (TypeError, ValueError, OverflowError):
            rate = 0
        # Hold an independent engine claim so closing the player cannot cancel a
        # user-requested full-file download. The stream remains a low-priority
        # consumer and still fills/reuses Archive X's persistent range cache.
        import uuid
        download_client = "download-" + uuid.uuid4().hex
        download_token = uuid.uuid4().hex
        claimed = engine.claim_playback((int(channel_id), int(message_id)),
            client_id=download_client, generation=1, token=download_token)
        if not claimed:
            raise HTTPException(409, "Could not reserve a download session for this video")
        from fastapi.responses import StreamingResponse
        from ..playback.stream_server import content_disposition
        file_name = str(getattr(message.file, "name", "") or f"video-{message_id}.mkv")
        start_clock = time.monotonic()
        async def body():
            sent = 0
            try:
                async for chunk in engine.iter_range(int(channel_id), message, 0, size - 1, mode="prewarm"):
                    if not chunk:
                        continue
                    yield chunk
                    sent += len(chunk)
                    if rate:
                        delay = sent / float(rate) - (time.monotonic() - start_clock)
                        if delay > 0:
                            await asyncio.sleep(delay)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
                pass
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    engine.release_video((int(channel_id), int(message_id)), reason="download_finished",
                        client_id=download_client, generation=1, token=download_token)
                except Exception:
                    pass
        return StreamingResponse(body(), media_type="application/octet-stream", headers={
            "Content-Length": str(size), "Accept-Ranges": "none",
            "Content-Disposition": content_disposition(file_name).replace("inline;", "attachment;", 1),
            "Cache-Control": "no-store", "X-Archive-X-Download-Source": "telegram-range-cache",
        })

    @router.get("/api/play/buffer/{channel_id}/{message_id}")
    async def play_buffer_status(channel_id: int, message_id: int, request: Request):
        """Return the contiguous local playback buffer without downloading."""
        require_auth(request)
        engine, message = await _engine_message(channel_id, message_id)
        try:
            start_seg = max(0, int(request.query_params.get("seg", "0") or 0))
        except ValueError:
            start_seg = 0
        try:
            target = max(1, min(8, int(request.query_params.get("need", "4") or 4)))
        except ValueError:
            target = 4
        return engine.buffer_status((channel_id, message_id), message, start_seg, target)

    @router.get("/api/play/info/{channel_id}/{message_id}")
    async def play_info(channel_id: int, message_id: int, request: Request):
        """GET /api/play/info/{channel_id}/{message_id}: Play info."""
        require_auth(request)
        from ..core.split_files import load_split_group, SplitFileError
        try:
            split_group = await asyncio.to_thread(load_split_group, db, str(channel_id), int(message_id))
        except SplitFileError as exc:
            raise HTTPException(503, f"Split-file group is incomplete or inconsistent: {exc}") from exc
        if split_group is not None:
            if str(split_group.get("status") or "").upper() != "VERIFIED":
                raise HTTPException(503, "Split-file sequence is not complete and verified yet")
            # Detect playable-part groups (each part is an independently
            # streamable video) vs legacy byte-split groups.
            from ..core.split_files import _PLAYABLE_PART_RE
            parts_list = split_group.get("parts") or []
            is_playable_parts = any(
                _PLAYABLE_PART_RE.fullmatch(str(p.get("telegram_filename") or ""))
                for p in parts_list
            )
            info = {
                "name": str(split_group.get("logical_filename") or ""),
                "mime": str(split_group.get("mime_type") or "application/octet-stream"),
                "size": int(split_group.get("total_size") or 0),
                "duration": float(split_group.get("duration") or 0),
                "width": int(split_group.get("width") or 0),
                "height": int(split_group.get("height") or 0),
                "fps": float(split_group.get("fps") or 0),
                "bitrate": int(split_group.get("bitrate") or 0),
                "codec": str(split_group.get("codec") or ""),
                "audio_codec": str(split_group.get("audio_codec") or ""),
                "streamable": True,
                "split_file": True,
            }
            if is_playable_parts:
                info["streamability_label"] = "Multi-part playable stream"
                info["playable_parts"] = [
                    {
                        "part_index": int(p.get("part_index") or 0),
                        "message_id": int(p.get("message_id") or 0),
                        "size_bytes": int(p.get("size_bytes") or 0),
                        "telegram_filename": str(p.get("telegram_filename") or ""),
                    }
                    for p in sorted(parts_list, key=lambda x: int(x.get("part_index") or 0))
                ]
            else:
                info["streamability_label"] = "Split-file byte-range stream"
            return info
        try:
            wait = float(request.query_params.get("wait", "20") or 20)
        except ValueError:
            wait = 20.0
        try:
            info = await _get_info(channel_id, message_id, wait=True, timeout=max(1.0, min(wait, 60.0)))
        except asyncio.TimeoutError:
            return JSONResponse({"pending": True}, status_code=202)
        except HTTPException:
            raise
        except Exception as e:
            return {"error": str(e), "direct_likely": True}
        if not info:
            return {"pending": True}
        name = str(info.get("name") or "").lower()
        mime = str(info.get("mime") or "").lower()
        is_mp4 = name.endswith((".mp4", ".m4v", ".mov")) or mime in {"video/mp4", "video/quicktime"}
        if is_mp4:
            try:
                from ..core.playback_index_cache import read_manifest
                size = max(0, int(info.get("size") or 0))
                manifest = await asyncio.to_thread(read_manifest, int(channel_id), int(message_id), size or None)
                if manifest:
                    moov_offset = max(0, int(manifest.get("moov_offset") or 0))
                    moov_size = max(0, int(manifest.get("moov_size") or 0))
                    info["moov_offset"] = moov_offset
                    info["moov_size"] = moov_size
                    moov_valid = bool(size and moov_size >= 8 and moov_offset + moov_size <= size)
                    if moov_valid:
                        near_start = moov_offset <= min(512 * 1024, max(64 * 1024, size * 0.02))
                        info["streamable"] = bool(near_start and moov_offset + moov_size < size * 0.80)
                        info["streamability_label"] = "Streamable" if info["streamable"] else "Not streamable (moov at end)"
                    else:
                        info["streamable"] = None
                        info["streamability_label"] = "Streamability unknown"
                else:
                    info["streamable"] = None
                    info["streamability_label"] = "Streamability unknown"
            except Exception:
                info["streamable"] = None
                info["streamability_label"] = "Streamability unknown"
        else:
            info["streamable"] = None
            info["streamability_label"] = "Compatibility stream" if name.endswith((".mkv", ".avi", ".wmv")) else ""
        return info

    preview_inflight = {}
    preview_generation = {}
    preview_cleanup = {"last": 0.0}
    preview_scheduler = PreviewScheduler(workers=int(getattr(config,"PLAYBACK_PREVIEW_WORKERS",2)), max_pending=int(getattr(config,"PLAYBACK_PREVIEW_MAX_PENDING",12)))
    preview_stats = {"requests": 0, "cache_hits": 0, "generated": 0, "failures": 0, "dedup_hits": 0, "last_error": "", "last_ms": 0.0, "superseded": 0, "gen_ms_p50": 0.0, "gen_ms_p90": 0.0}
    preview_gen_ms = collections.deque(maxlen=50)
    preview_player_latest = {}
    ps["preview_stats"] = preview_stats

    # Persistent browser-playback assets for formats the browser cannot decode
    # directly. These are intentionally separate from Telegram's sparse range
    # cache: once a compatibility asset is complete, the browser can seek it
    # with ordinary local HTTP ranges and never starts a new FFmpeg process per seek.
    compat_cache_tasks: dict[tuple[int, int], asyncio.Task] = {}
    compat_cache_state: dict[tuple[int, int], dict] = {}
    compat_profile_tasks: dict[tuple[int, int], asyncio.Task] = {}
    compat_profile_state: dict[tuple[int, int], dict] = {}

    # Persistent source cache for browser-compatible Telegram media.
    # Unlike the RAM/sparse playback cache, this survives application restarts.
    direct_cache_tasks: dict[tuple[int, int], asyncio.Task] = {}
    direct_cache_state: dict[tuple[int, int], dict] = {}


    # ------------------------------------------------------------------
    # SUBSECTION: Direct and compatibility assets
    # Asset paths, builds and caching.
    # ------------------------------------------------------------------
    def _direct_asset_dir(channel_id: int, message_id: int) -> Path:
        return config.PLAYBACK_ASSET_DIR / str(int(channel_id)) / str(int(message_id)) / "direct"

    def _direct_asset_path(channel_id: int, message_id: int) -> Path:
        return _direct_asset_dir(channel_id, message_id) / "video.bin"

    def _direct_manifest_path(channel_id: int, message_id: int) -> Path:
        return _direct_asset_dir(channel_id, message_id) / "manifest.json"

    def _direct_asset_state(channel_id: int, message_id: int) -> dict:
        key = (int(channel_id), int(message_id))
        final_path = _direct_asset_path(*key)
        state = direct_cache_state.get(key)
        if state:
            return dict(state)
        if final_path.exists() and final_path.stat().st_size > 1024:
            return {
                "status": "ready",
                "kind": "direct",
                "size": final_path.stat().st_size,
            }
        manifest_path = _direct_manifest_path(*key)
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                size = int(data.get("size") or 0)
                completed = list(data.get("completed") or [])
                if size > 0 and completed:
                    return {
                        "status": "resumable",
                        "kind": "direct",
                        "size": size,
                        "completed_segments": len(completed),
                    }
            except Exception:
                pass
        return {"status": "idle", "kind": "direct"}

    def _compat_asset_path(channel_id: int, message_id: int) -> Path:
        return config.PLAYBACK_ASSET_DIR / str(int(channel_id)) / str(int(message_id)) / "video.mp4"

    def _compat_asset_state(channel_id: int, message_id: int) -> dict:
        key = (int(channel_id), int(message_id))
        path = _compat_asset_path(*key)
        state = compat_cache_state.get(key)
        if path.exists() and path.stat().st_size > 1024:
            return {"status": "ready", "size": path.stat().st_size}
        if state:
            return dict(state)
        return {"status": "idle"}

    async def _build_direct_asset(key: tuple[int, int]):
        """Persist a browser-compatible source video from the shared segment cache.

        The file is written segment-by-segment to a preallocated local file.
        A small JSON manifest records completed segments, so an interrupted
        application can resume without restarting the download.
        """
        cid, mid = key
        try:
            engine, message = await _engine_message(cid, mid)
            size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
            if size <= 0:
                raise RuntimeError("source video size unavailable")
            name = str(getattr(getattr(message, "file", None), "name", "") or f"{mid}.mp4")
            mime = str(getattr(getattr(message, "file", None), "mime_type", "") or "video/mp4")

            base_dir = _direct_asset_dir(cid, mid)
            base_dir.mkdir(parents=True, exist_ok=True)
            final_path = _direct_asset_path(cid, mid)
            partial = base_dir / "video.bin.partial"
            manifest_path = _direct_manifest_path(cid, mid)

            if final_path.exists() and final_path.stat().st_size == size:
                direct_cache_state[key] = {
                    "status": "ready", "kind": "direct", "size": size,
                    "mime": mime, "name": name,
                }
                return

            completed = set()
            if manifest_path.exists():
                try:
                    m = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if int(m.get("size") or 0) == size:
                        completed = {int(x) for x in (m.get("completed") or [])}
                except Exception:
                    completed = set()

            # Remove completed entries outside the current segment count.
            total = (size + config.PLAYBACK_RANGE_CHUNK_BYTES - 1) // config.PLAYBACK_RANGE_CHUNK_BYTES
            completed = {x for x in completed if 0 <= x < total}

            if not partial.exists():
                with partial.open("w+b") as fh:
                    fh.truncate(size)
            else:
                with partial.open("r+b") as fh:
                    fh.truncate(size)

            direct_cache_state[key] = {
                "status": "running", "kind": "direct", "size": size,
                "mime": mime, "name": name,
                "completed_segments": len(completed), "total_segments": total,
            }

            # Download a tiny rolling group. The same scheduler is shared with
            # foreground playback, so a segment already requested by the player
            # is joined rather than downloaded again.
            while len(completed) < total:
                missing = [seg for seg in range(total) if seg not in completed][:4]
                if not missing:
                    break
                results = await asyncio.gather(
                    *[
                        engine.get_segment(
                            (cid, mid), message, seg,
                            priority=1, epoch=getattr(engine, "epoch", 0),
                            kind="playback-cache",
                        )
                        for seg in missing
                    ],
                    return_exceptions=True,
                )
                progress = False
                with partial.open("r+b") as fh:
                    for seg, data in zip(missing, results):
                        if isinstance(data, BaseException) or not data:
                            continue
                        expected = min(config.PLAYBACK_RANGE_CHUNK_BYTES, size - seg * config.PLAYBACK_RANGE_CHUNK_BYTES)
                        blob = bytes(data)[:expected]
                        if len(blob) != expected:
                            continue
                        fh.seek(seg * config.PLAYBACK_RANGE_CHUNK_BYTES)
                        fh.write(blob)
                        completed.add(seg)
                        progress = True

                # Persist every cycle. Losing one cycle only costs the latest few
                # segments, not the entire file.
                manifest = {
                    "version": 1, "size": size, "mime": mime, "name": name,
                    "segment_size": config.PLAYBACK_RANGE_CHUNK_BYTES,
                    "completed": sorted(completed),
                }
                tmp_manifest = manifest_path.with_suffix(".json.tmp")
                tmp_manifest.write_text(json.dumps(manifest), encoding="utf-8")
                os.replace(str(tmp_manifest), str(manifest_path))

                direct_cache_state[key].update({
                    "completed_segments": len(completed),
                    "total_segments": total,
                    "progress": round(len(completed) / max(1, total), 4),
                })

                if not progress:
                    await asyncio.sleep(1.0)

            if len(completed) >= total:
                # Final integrity barrier: exact expected file size, then publish.
                if partial.stat().st_size != size:
                    raise RuntimeError("direct playback cache size mismatch")
                os.replace(str(partial), str(final_path))
                direct_cache_state[key] = {
                    "status": "ready", "kind": "direct", "size": size,
                    "mime": mime, "name": name,
                    "progress": 1.0,
                }
                db.log(f"Direct web playback cache ready {cid}/{mid}: {size} bytes")
            else:
                direct_cache_state[key]["status"] = "resumable"

        except asyncio.CancelledError:
            # Keep the partial file + manifest. It will resume next time.
            raise
        except Exception as exc:
            direct_cache_state[key] = {
                "status": "error",
                "kind": "direct",
                "error": f"{type(exc).__name__}: {exc}"[:800],
            }
            db.log(f"Direct web playback cache failed {cid}/{mid}: {type(exc).__name__}: {exc}", "WARN")
        finally:
            direct_cache_tasks.pop(key, None)

    async def _probe_transcode_candidate(key: tuple[int, int], info: dict, decoder: str | None, encoder: str) -> bool:
        """Verify a real media item can be decoded/encoded with one backend pair.

        Startup GPU detection is not enough: the uploaded log showed Intel QSV
        advertising successfully while the actual HEVC stream rejected the MFX
        session. This bounded per-media probe prevents that stale capability from
        breaking browser compatibility playback.
        """
        ffmpeg = media_tools.find_ffmpeg()
        if not ffmpeg:
            return False
        profile = {"hardware_acceleration": {
            "preferred_decoder": decoder,
            "preferred_transcode_decoder": decoder,
        }}
        hw = media_tools.hwaccel_input_args(profile, encoder) if encoder != "copy" else []
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-analyzeduration", "2M", "-probesize", "2M", *hw,
               "-i", _local_url(key[0], key[1], ttl=120), "-map", "0:v:0",
               "-frames:v", "2"]
        if encoder.startswith("h264_"):
            cmd += ["-c:v", encoder, "-f", "null", "-"]
        else:
            cmd += ["-f", "null", "-"]
        try:
            rc, _out, _err = await media_tools._run(cmd, timeout=7, want_stdout=False)
            return rc == 0
        except Exception:
            return False

    async def _select_compat_profile(key: tuple[int, int], info: dict) -> dict:
        """Select a verified per-media browser compatibility backend.

        Browser playback remains direct-first. This profile is only used after
        the browser cannot decode the original media or when the operator asks
        for compatibility playback. Hardware decode is preferred and hardware
        encode is used when the exact device/driver path actually works.
        """
        codec = str(info.get("vcodec") or "").lower()
        pix = str(info.get("pix_fmt") or "").lower()
        key2 = (int(key[0]), int(key[1]))
        cached = compat_profile_state.get(key2)
        if cached and cached.get("status") == "ready":
            return cached
        existing = compat_profile_tasks.get(key2)
        if existing is not None and not existing.done():
            return await asyncio.shield(existing)

        async def choose():
            """Choose a compat encoding profile by probing hardware candidates."""
            # Start from the detected hardware profile, but never trust it for
            # the actual media until the real HEVC/H.265 item passes the probe.
            base = copy.deepcopy(resources.hardware_profile or {}) if resources is not None else {}
            accel_info = base.setdefault("hardware_acceleration", {})
            gpus = list(base.get("gpus") or [])
            hwaccels = set(accel_info.get("ffmpeg_hwaccels") or [])
            encoder_candidates = []
            preferred_enc = accel_info.get("preferred_encoder")
            if preferred_enc:
                encoder_candidates.append(str(preferred_enc))
            for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
                if enc not in encoder_candidates:
                    encoder_candidates.append(enc)

            candidates: list[tuple[str | None, str]] = []
            # Prefer the same Windows decode family used by MPC-HC/LAV. The
            # current incident showed a machine-wide QSV capability that could
            # not create an MFX session for the real HEVC stream. D3D11VA +
            # libx264 is a safer hardware-decode fallback because the decoder
            # is independent of QSV's encoder/session stack.
            if os.name == "nt" and "d3d11va" in hwaccels and gpus:
                candidates.append(("d3d11va", "libx264"))
            # Same-GPU hardware encode paths next, but each is still verified
            # against the exact media item before it is selected.
            for enc in encoder_candidates:
                if enc == "h264_nvenc" and "cuda" in hwaccels and any(g.get("vendor") == "nvidia" for g in gpus):
                    candidates.append(("cuda", enc))
                elif enc == "h264_qsv" and "qsv" in hwaccels and any(g.get("vendor") == "intel" for g in gpus):
                    candidates.append(("qsv", enc))
                elif enc == "h264_amf" and os.name == "nt" and "d3d11va" in hwaccels and any(g.get("vendor") == "amd" for g in gpus):
                    candidates.append(("d3d11va", enc))
            # Last resort: pure software conversion.
            candidates.append((None, "libx264"))

            # If the video is already H.264 and only audio/container work is
            # required, don't force a video decode/encode stage.
            plan = media_tools.plan_compat(info)
            if plan.get("copy_video"):
                return {"status": "ready", "decoder": None, "encoder": "copy",
                        "hardware_decode": False, "hardware_encode": False,
                        "reason": "video-copy", "codec": codec, "pix_fmt": pix}

            for decoder, encoder in candidates:
                if encoder != "libx264" and decoder is None:
                    continue
                ok = await _probe_transcode_candidate(key2, info, decoder, encoder)
                if ok:
                    profile = copy.deepcopy(base)
                    h = profile.setdefault("hardware_acceleration", {})
                    h["preferred_decoder"] = decoder
                    h["preferred_transcode_decoder"] = decoder
                    h["preferred_encoder"] = encoder if encoder.startswith("h264_") else None
                    result = {"status": "ready", "decoder": decoder, "encoder": encoder,
                              "hardware_decode": bool(decoder),
                              "hardware_encode": encoder.startswith("h264_"),
                              "profile": profile, "reason": "verified-media-path",
                              "codec": codec, "pix_fmt": pix}
                    db.log(f"Browser compatibility backend {key2[0]}/{key2[1]}: "
                           f"decoder={decoder or 'software'} encoder={encoder}", "INFO")
                    return result
            return {"status": "ready", "decoder": None, "encoder": "libx264",
                    "hardware_decode": False, "hardware_encode": False,
                    "profile": {}, "reason": "software-fallback", "codec": codec, "pix_fmt": pix}

        task = asyncio.create_task(choose(), name=f"compat-profile-{key2[0]}-{key2[1]}")
        compat_profile_tasks[key2] = task
        try:
            result = await task
            compat_profile_state[key2] = result
            return result
        finally:
            if compat_profile_tasks.get(key2) is task:
                compat_profile_tasks.pop(key2, None)

    async def _build_compat_asset(key: tuple[int, int], info: dict):
        """Build a browser-compatible file for one video."""
        ffmpeg = media_tools.find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for local playback cache")
        cid, mid = key
        path = _compat_asset_path(cid, mid)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name("video.partial.mp4")
        try:
            if path.exists() and path.stat().st_size > 1024:
                return
            if partial.exists():
                partial.unlink()
            selected = await _select_compat_profile(key, info)
            encoder = str(selected.get("encoder") or "libx264")
            media_profile = selected.get("profile") or {}
            cmd = media_tools.compat_file_command(
                ffmpeg,
                _local_url(cid, mid, ttl=24 * 3600),
                info,
                str(partial),
                encoder,
                media_profile,
            )
            compat_cache_state[key] = {"status": "running", "encoder": encoder, "size": 0}
            acquired = False
            if resources is not None:
                await resources.acquire_background("conversion")
                acquired = True
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=media_tools._NO_WINDOW,
            )
            try:
                stdout, stderr = await proc.communicate()
            except asyncio.CancelledError:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await proc.wait()
                except Exception:
                    pass
                raise
            finally:
                if acquired:
                    await resources.release_background("conversion")
            if proc.returncode != 0 or not partial.exists() or partial.stat().st_size <= 1024:
                err = stderr.decode("utf-8", "ignore")[-800:] if stderr else ""
                raise RuntimeError(err or f"ffmpeg exited with {proc.returncode}")
            # Probe the finished file before publishing it. A complete rename is
            # the readiness barrier seen by the HTTP route.
            local_info = await media_tools.probe(str(partial), timeout=60)
            if not media_tools.browser_verdict(local_info, "video/mp4").get("direct_likely"):
                raise RuntimeError("local playback cache did not produce browser-compatible MP4")
            os.replace(str(partial), str(path))
            compat_cache_state[key] = {
                "status": "ready", "size": path.stat().st_size,
                "width": local_info.get("width", 0), "height": local_info.get("height", 0),
                "duration": local_info.get("duration", 0), "vcodec": local_info.get("vcodec", ""),
                "acodec": local_info.get("acodec", ""),
                "encoder": encoder, "decoder": selected.get("decoder"),
                "hardware_decode": bool(selected.get("hardware_decode")),
                "hardware_encode": bool(selected.get("hardware_encode")),
            }
            db.log(f"Web playback cache ready {cid}/{mid}: {path.stat().st_size} bytes ({encoder})")
        except Exception as exc:
            try:
                partial.unlink(missing_ok=True)
            except Exception:
                pass
            compat_cache_state[key] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:800]}
            db.log(f"Web playback cache failed {cid}/{mid}: {type(exc).__name__}: {exc}", "WARN")
        finally:
            compat_cache_tasks.pop(key, None)

    def _start_direct_cache(key: tuple[int, int]) -> dict:
        state = _direct_asset_state(*key)
        if state.get("status") == "ready":
            return state
        existing = direct_cache_tasks.get(key)
        if existing is not None and not existing.done():
            return _direct_asset_state(*key)
        direct_cache_state[key] = {**state, "status": "running", "kind": "direct"}
        task = asyncio.create_task(_build_direct_asset(key), name=f"direct-cache-{key[0]}-{key[1]}")
        direct_cache_tasks[key] = task
        return _direct_asset_state(*key)

    def _cached_asset_state(channel_id: int, message_id: int) -> dict:
        direct = _direct_asset_state(channel_id, message_id)
        if direct.get("status") == "ready":
            return direct
        compat = _compat_asset_state(channel_id, message_id)
        if compat.get("status") in {"ready", "running", "error"}:
            return {**compat, "kind": "compat"}
        if direct.get("status") in {"running", "resumable", "error"}:
            return direct
        return {"status": "idle"}

    def _cached_asset_path(channel_id: int, message_id: int) -> Path | None:
        direct = _direct_asset_state(channel_id, message_id)
        if direct.get("status") == "ready":
            return _direct_asset_path(channel_id, message_id)
        compat = _compat_asset_state(channel_id, message_id)
        if compat.get("status") == "ready":
            return _compat_asset_path(channel_id, message_id)
        return None

    def _start_compat_cache(key: tuple[int, int], info: dict) -> dict:
        state = _compat_asset_state(*key)
        if state.get("status") == "ready":
            return state
        existing = compat_cache_tasks.get(key)
        if existing is not None and not existing.done():
            return {"status": "running"}
        compat_cache_state[key] = {"status": "running", "size": 0}
        task = asyncio.create_task(_build_compat_asset(key, info), name=f"play-cache-{key[0]}-{key[1]}")
        compat_cache_tasks[key] = task
        return {"status": "running"}


    # ------------------------------------------------------------------
    # SUBSECTION: Seek previews
    # Preview manifest, grid and frames.
    # ------------------------------------------------------------------
    async def _generate_preview(path, engine, channel_id, message_id, t, width, latest_wins=False, exact=False):
        """Generate one preview through the single application preview scheduler.

        The scheduler is the only CPU/decode concurrency boundary for interactive
        preview work. Playback byte acquisition remains owned by PlaybackEngine's
        SegmentTransferScheduler, so the two layers do not create duplicate network
        downloads or duplicate worker pools. Latest-wins applies only to live player
        scrubbing; suggestion/batch preview frames use independent scheduler keys.
        """
        key = str(path)
        existing = preview_inflight.get(key)
        if existing is not None and not existing.done():
            preview_stats["dedup_hits"] += 1
            return await asyncio.shield(existing)

        media_key = (int(channel_id), int(message_id))
        if latest_wins:
            generation = int(preview_generation.get(media_key, 0)) + 1
            preview_generation[media_key] = generation
            scheduler_key = (media_key, "live")
        else:
            generation = 0
            scheduler_key = (media_key, "batch", int(round(float(t))), int(width))

        async def run():
            source = _probe_url(channel_id, message_id, light=True, mode="preview")
            if latest_wins and exact:
                # The committed seek must preview the same presentation time the
                # browser will seek to. The fast MP4-only sync-frame path is great
                # for hover, but can return a different keyframe than the visible
                # frame at `t`; use FFmpeg's short accurate-decode window here.
                return await media_tools.preview_frame(
                    source, float(t), width=width, timeout=12.0, hardware_profile=None
                )
            if latest_wins:
                try:
                    # V29.4.120: `_message` was undefined here, so the fast
                    # keyframe preview always failed over to a full FFmpeg
                    # decode of the remote stream (slow -> black bubble).
                    live_message = await engine.get_message(int(channel_id), int(message_id))
                    return await media_tools.preview_frame_playback_cache(
                        engine, media_key, live_message, float(t),
                        width=width, timeout=4.0,
                        epoch=getattr(engine, "epoch", 0),
                    )
                except Exception:
                    pass
            try:
                return await media_tools.preview_frame_legacy(
                    source, float(t), width=width, timeout=15.0
                )
            except Exception as legacy_error:
                try:
                    return await media_tools.preview_frame(
                        source, float(t), width=width, timeout=15.0, hardware_profile=None
                    )
                except Exception:
                    raise legacy_error

        async def scheduled_work():
            return await run()

        task = asyncio.create_task(
            preview_scheduler.submit(
                scheduler_key, generation, scheduled_work,
                priority=2,
            ),
            name=f"preview-request-{channel_id}-{message_id}-{int(float(t))}",
        )
        preview_inflight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if preview_inflight.get(key) is task:
                preview_inflight.pop(key, None)
            if latest_wins and preview_generation.get(media_key) == generation:
                # Keep the generation value monotonic for later scrubs; removing
                # it would allow a stale request to become current after wrapback.
                pass

    @router.get("/api/play/preview-manifest/{channel_id}/{message_id}")
    async def play_preview_manifest(channel_id: int, message_id: int, request: Request):
        """Return the saved snapshot-validation result without decoding files."""
        require_auth(request)
        from ..engines.preview_prefetch import (PREVIEW_VERSION, FRAME_COUNT, frame_times,
                                                preview_frames_path, representative_frame_path,
                                                ensure_local_preview_assets_sync)
        # Self-heal a missing receipt or sprite from local JPEGs only. The repair
        # runs off-loop and never reaches Telegram or the playback engine.
        await asyncio.to_thread(ensure_local_preview_assets_sync, db, int(channel_id), int(message_id))
        def _read_saved_manifest():
            """Read saved preview data for this video from the database and disk."""
            with db.reading() as rdb:
                row = rdb.execute(
                    "SELECT status,duration,width,height,frame_count,version,error,updated_at,path,frame_times,frames_path "
                    "FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=?",
                    (str(int(channel_id)), int(message_id)),
                ).fetchone()
                media = rdb.execute(
                    "SELECT duration FROM media WHERE CAST(source_channel AS TEXT)=? AND CAST(source_message_id AS INTEGER)=? LIMIT 1",
                    (str(int(channel_id)), int(message_id)),
                ).fetchone()
            payload = dict(row) if row else {}
            duration = float((payload.get("duration") if payload else 0) or (media["duration"] if media else 0) or 0)
            try:
                saved_times = json.loads(payload.get("frame_times") or "[]")
            except (TypeError, ValueError):
                saved_times = []
            times = saved_times if isinstance(saved_times, list) and len(saved_times) == FRAME_COUNT else frame_times(duration)
            frame_count = max(0, int(payload.get("frame_count") or 0))
            saved_ready = (str(payload.get("status") or "").upper() == "READY"
                           and int(payload.get("version") or 0) == PREVIEW_VERSION
                           and frame_count >= FRAME_COUNT)
            frames_dir = preview_frames_path(int(channel_id), int(message_id))
            representative = representative_frame_path(int(channel_id), int(message_id))
            sprite = frames_dir / "grid_sprite.jpg"
            # The one-time scan stores this small validation receipt beside the
            # frame set. Reading it is cheap; requests never decode/verify 36 JPEGs.
            try:
                validation = json.loads((frames_dir / "validation.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                validation = {}
            validation_ready = (int(validation.get("version") or 0) == PREVIEW_VERSION
                                and validation.get("ready") is True
                                and int(validation.get("frame_count") or 0) >= FRAME_COUNT
                                and len(validation.get("files") or []) == FRAME_COUNT)
            saved_ready = saved_ready and validation_ready
            # Existence checks are O(1); JPEG validation was done at creation.
            sprite_ready = saved_ready and sprite.is_file()
            frames_ready = saved_ready and representative.is_file()
            raw_status = str(payload.get("status") or "MISSING").upper()
            status = "READY" if frames_ready else ("PREPARING" if raw_status == "PREPARING" else
                     "FAILED" if raw_status == "FAILED" else ("INCOMPLETE" if frame_count else "UNAVAILABLE"))
            return {
                "ready": frames_ready, "state": status, "status": status,
                "version": PREVIEW_VERSION, "frame_count": frame_count,
                "distinct_frames": int(validation.get("distinct_frames") or 0) if frames_ready else 0,
                "corrupt_frames": list(validation.get("corrupt_frames") or []) if frames_ready else [],
                "frame_times": times, "frames_ready": frames_ready,
                "sprite_ready": sprite_ready, "frame_width": 320, "frame_height": 180,
                "representative_ready": representative.is_file(), "path": "",
                "source": "local-sprite" if sprite_ready else "individual-frames",
                "error": str(payload.get("error") or ""), "updated_at": payload.get("updated_at"),
            }
        return await asyncio.to_thread(_read_saved_manifest)

    @router.get("/api/play/preview-grid/{channel_id}/{message_id}")
    async def play_preview_grid(channel_id: int, message_id: int, request: Request):
        """Send the prebuilt 36-frame sprite in one local file response."""
        require_auth(request)
        from fastapi.responses import FileResponse
        from ..engines.preview_prefetch import PREVIEW_VERSION, preview_frames_path
        path = preview_frames_path(int(channel_id), int(message_id)) / "grid_sprite.jpg"
        try:
            stat = path.stat()
            if not path.is_file() or stat.st_size < 1024:
                raise OSError("snapshot grid sprite unavailable")
        except OSError:
            raise HTTPException(404, "cached snapshot grid not ready")
        etag = f'"pg-{PREVIEW_VERSION}-{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        headers = {"Cache-Control": "private, max-age=86400, immutable", "ETag": etag}
        if request.headers.get("if-none-match", "").strip() == etag:
            from fastapi.responses import Response
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="image/jpeg", headers=headers)

    @router.get("/api/play/preview-frame/{channel_id}/{message_id}/{frame_index}")
    async def play_preview_frame(channel_id: int, message_id: int, frame_index: int, request: Request):
        """Serve an already validated local JPEG without Pillow work per request."""
        require_auth(request)
        from fastapi.responses import FileResponse, Response
        from ..engines.preview_prefetch import PREVIEW_VERSION, FRAME_COUNT, preview_frame_path, MIN_FRAME_BYTES
        idx = int(frame_index)
        if idx < 0 or idx >= FRAME_COUNT:
            raise HTTPException(400, f"frame index must be 0..{FRAME_COUNT - 1}")
        path = preview_frame_path(int(channel_id), int(message_id), idx)
        try:
            stat = path.stat()
            if not path.is_file() or stat.st_size < MIN_FRAME_BYTES:
                raise OSError("frame not ready")
        except OSError:
            raise HTTPException(404, "cached snapshot frame not ready")
        etag = f'"pf-{PREVIEW_VERSION}-{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        headers = {"Cache-Control": "private, max-age=86400, immutable", "ETag": etag,
                   "X-Preview-Frame": str(idx), "X-Preview-Version": str(PREVIEW_VERSION)}
        if request.headers.get("if-none-match", "").strip() == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="image/jpeg", headers=headers)

    @router.get("/api/play/preview/{channel_id}/{message_id}")
    async def play_preview(channel_id: int, message_id: int, request: Request):
        """Clear seek-bar preview frame for the requested time bucket."""
        require_auth(request)
        from fastapi.responses import Response
        preview_stats["requests"] += 1
        started_at = time.monotonic()
        try:
            t = max(0.0, float(request.query_params.get("t", "0") or 0))
        except ValueError:
            t = 0.0
        exact = request.query_params.get("exact") == "1"
        try:
            width = max(240, min(640, int(request.query_params.get("w", "480") or 480)))
        except ValueError:
            width = 480
        # v2 previews use a fresh cache namespace so older tiny preview frames
        # cannot be reused after the seek-preview UI upgrade.
        try:
            preview_version = int(request.query_params.get("pv", "15") or 15)
        except ValueError:
            preview_version = 15
        # Scrubbing keeps its compact one-second cache buckets. Resume dialogs
        # can request a millisecond-keyed frame so it matches the saved start
        # position instead of reusing a nearby hover preview.
        precision = 1000 if exact else 1
        preview_bucket = int(round(t * precision))
        preview_t = preview_bucket / precision
        latest_wins = request.query_params.get("lw") == "1"
        path = media_tools.preview_path(
            channel_id, message_id, preview_bucket, width=width, version=preview_version
        )
        headers = {"Cache-Control": "private, max-age=604800"}
        if await asyncio.to_thread(path.exists):
            preview_stats["cache_hits"] += 1
            preview_stats["last_ms"] = round((time.monotonic() - started_at) * 1000.0, 1)
            return Response(await asyncio.to_thread(path.read_bytes), media_type="image/jpeg", headers=headers)
        # Exact player preview is separate from cached individual snapshot frames.
        # The player preview is expected to show the actual frame at the requested
        # timestamp. If an exact frame is already cached, return it immediately.
        if not media_tools.find_ffmpeg():
            raise HTTPException(404, "ffmpeg is not installed")
        engine, _message = await _engine_message(channel_id, message_id)
        # V29.4.120: live scrub previews no longer start the 24-frame
        # timeline pre-render; it competed with the live frame the user is
        # waiting for (Telegram/CPU) and is a snapshot set, not a live frame.
        if await asyncio.to_thread(path.exists):
            preview_stats["cache_hits"] += 1
            preview_stats["last_ms"] = round((time.monotonic() - started_at) * 1000.0, 1)
            return Response(await asyncio.to_thread(path.read_bytes), media_type="image/jpeg", headers=headers)
        preview_task = asyncio.create_task(
            _generate_preview(path, engine, channel_id, message_id, float(preview_t), width, latest_wins, exact=exact),
            name=f"preview-request-{channel_id}-{message_id}-{preview_bucket}",
        )
        gen_started = time.monotonic()
        try:
            jpg = await asyncio.shield(preview_task)
        except asyncio.CancelledError:
            if preview_task.cancelled():
                # Superseded by a newer scrub position from the same player.
                return Response(status_code=409, headers={"Cache-Control": "no-store"})
            raise
        except Exception as e:
            preview_stats["failures"] += 1
            preview_stats["last_error"] = f"{type(e).__name__}: {e}"[:500]
            preview_stats["last_ms"] = round((time.monotonic() - started_at) * 1000.0, 1)
            raise HTTPException(502, f"preview failed: {e}")
        finally:
            pass

        def _save():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(jpg)
            if time.time() - preview_cleanup["last"] > 600:
                preview_cleanup["last"] = time.time()
                media_tools.cleanup_previews()
                try:
                    from ..core.runtime_cleanup import cleanup_runtime_artifacts
                    cleanup_runtime_artifacts()
                except Exception:
                    pass
        try:
            if not path.exists():
                await asyncio.to_thread(_save)
            else:
                jpg = await asyncio.to_thread(path.read_bytes)
        except Exception:
            pass
        preview_stats["generated"] += 1
        preview_stats["last_ms"] = round((time.monotonic() - started_at) * 1000.0, 1)
        preview_gen_ms.append((time.monotonic() - gen_started) * 1000.0)
        ordered = sorted(preview_gen_ms)
        preview_stats["gen_ms_p50"] = round(ordered[len(ordered) // 2], 1)
        preview_stats["gen_ms_p90"] = round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1)
        headers = {**headers, "X-Preview-Ms": str(preview_stats["last_ms"])}
        return Response(jpg, media_type="image/jpeg", headers=headers)

    media_hw_decode_cache: dict[str, bool] = {}


    # ------------------------------------------------------------------
    # SUBSECTION: Compatibility plan and cached streams
    # Plans, cache prepare and cached streams.
    # ------------------------------------------------------------------
    async def _media_hw_profile(channel_id: int, message_id: int, info: dict) -> dict | None:
        """Return a copy of the hardware profile with HEVC decode verified for this media.

        The startup hardware probe only proves that a DirectX/QSV/CUDA device can
        initialize; it does not prove that the installed driver can decode this
        particular HEVC stream. MPC/LAV makes this distinction too: if hardware
        decode cannot handle the stream, it falls back to its optimized software
        decoder. Archive X now does the same for compatibility playback.
        """
        if resources is None:
            return None
        profile = copy.deepcopy(resources.hardware_profile or {})
        accel = ((profile.get("hardware_acceleration") or {}).get("preferred_decoder"))
        codec = str(info.get("vcodec") or "").lower()
        if not accel or codec not in {"hevc", "h265"}:
            return profile
        ffmpeg = media_tools.find_ffmpeg()
        if not ffmpeg:
            return profile
        # Cache the probe per exact media item. HEVC profiles/bit-depths can
        # differ from one Telegram message to another, so a global decoder
        # result could incorrectly reuse a PASS from a different stream.
        key = f"{int(channel_id)}:{int(message_id)}:{accel}:{codec}:{info.get('pix_fmt') or ''}:{info.get('width') or 0}x{info.get('height') or 0}"
        cached = media_hw_decode_cache.get(key)
        if cached is None:
            url = _local_url(channel_id, message_id, ttl=900)
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-analyzeduration", "2M", "-probesize", "2M",
                   "-ss", "0", "-hwaccel", accel, "-i", url,
                   "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"]
            try:
                rc, _, _ = await media_tools._run(cmd, timeout=15, want_stdout=False)
                cached = rc == 0
            except Exception:
                cached = False
            media_hw_decode_cache[key] = bool(cached)
            db.log(
                f"HEVC playback decode probe {accel}: {'PASS — hardware decode enabled' if cached else 'FALLBACK TO SOFTWARE'}",
                "INFO" if cached else "WARN",
            )
        if not cached:
            profile.setdefault("hardware_acceleration", {})["preferred_decoder"] = None
        return profile

    async def _compat_info(channel_id, message_id):
        try:
            return await _get_info(channel_id, message_id, wait=True, timeout=40)
        except Exception:
            return {}

    @router.get("/api/play/compatibility-plan/{channel_id}/{message_id}")
    async def play_compatibility_plan(channel_id: int, message_id: int, request: Request):
        """Return a verified backend plan for a browser-incompatible media item."""
        require_auth(request)
        key = (int(channel_id), int(message_id))
        info = await _compat_info(*key)
        if not info.get("vcodec"):
            return {"ok": False, "status": "unavailable", "detail": "media information unavailable"}
        plan = media_tools.plan_compat(info)
        if plan.get("copy_video") and plan.get("copy_audio"):
            return {"ok": True, "status": "not-needed", "strategy": "copy"}
        selected = await _select_compat_profile(key, info)
        return {"ok": True, **{k: selected.get(k) for k in ("status", "decoder", "encoder", "hardware_decode", "hardware_encode", "reason")}}

    @router.post("/api/play/cache/prepare")
    async def play_cache_prepare(request: Request):
        """Start persistent browser-playback cache for a non-direct video."""
        require_auth(request)
        data = await request.json()
        try:
            key = (int(data["channel_id"]), int(data["message_id"]))
        except Exception:
            raise HTTPException(400, "channel_id and message_id are required")
        info = await _compat_info(*key)
        if not info.get("vcodec"):
            return {"status": "idle", "error": "media info unavailable"}
        if info.get("direct_likely"):
            return _start_direct_cache(key)
        return _start_compat_cache(key, info)

    @router.get("/api/play/cache/status/{channel_id}/{message_id}")
    async def play_cache_status(channel_id: int, message_id: int, request: Request):
        require_auth(request)
        return _cached_asset_state(channel_id, message_id)

    @router.get("/stream-cached/{channel_id}/{message_id}")
    async def stream_cached(channel_id: int, message_id: int, request: Request):
        """Serve the persistent local browser-compatible MP4 with real byte ranges."""
        require_auth(request)
        path = _cached_asset_path(channel_id, message_id)
        if path is None or not path.exists() or path.stat().st_size <= 1024:
            raise HTTPException(409, "playback cache is not ready")
        cached_state = _cached_asset_state(channel_id, message_id)
        size = int(path.stat().st_size)
        from fastapi.responses import StreamingResponse, Response
        from ..playback.stream_server import parse_range, content_disposition
        try:
            start, end, status = parse_range(size, request.headers.get("range", ""))
        except ValueError:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        length = end - start + 1
        async def body():
            try:
                with path.open("rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = await asyncio.to_thread(fh.read, min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
                pass
            except asyncio.CancelledError:
                pass
        mime_type = "video/mp4" if cached_state.get("kind") == "compat" else str(cached_state.get("mime") or "video/mp4")
        display_name = str(cached_state.get("name") or path.name)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Disposition": content_disposition(display_name),
            "Cache-Control": "private, max-age=86400",
            "X-Playback-Cache": "1",
            "Vary": "Range",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(body(), status_code=status, headers=headers, media_type=mime_type)

    return {
        "_compat_info": _compat_info,
        "_media_hw_profile": _media_hw_profile,
        "_select_compat_profile": _select_compat_profile,
    }
