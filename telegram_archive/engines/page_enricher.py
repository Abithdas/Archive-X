"""Background details for the videos you are looking at (v18.8).

When a Library page is shown, the browser sends the videos on that page
here. In the background, without blocking anything:

1. videos that were never scanned get a local catalogue row (filename,
   size, length, resolution, studio/performers/tags parsed from the name),
   exactly like the Scan module would create;
2. if a StashDB/ThePornDB key is saved, videos without API metadata are
   looked up (file fingerprint first, then filename) and the result is saved.

The browser polls /api/enrich/updates and refreshes those cards in place.
Each video is attempted once per app run, so nothing is repeated endlessly.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
from ..core import content_mode
import asyncio
import time
from collections import deque
from datetime import datetime, timezone


class PageEnricher:
    """Background queue that fills in details for videos on the visible page."""
    def __init__(self, db, telegram, priority_gate=None, resources=None, metadata_blocked=None):
        self.db = db
        self.telegram = telegram
        self.priority_gate = priority_gate
        self.resources = resources
        self.metadata_blocked = metadata_blocked
        self.queue: deque = deque()
        self.queued: set = set()
        self.attempted: dict = {}     # key -> monotonic time of the attempt
        self.updated: dict = {}       # key -> wall time of the last change
        self.task = None
        self.current = None
        self.snapshot_waiting: set[tuple[int, int]] = set()
        self.metadata_queue: deque = deque()
        self._advance_task = None

    # ---------------------------------------------------------------- API

    async def stop(self) -> None:
        """Cancel this enricher's background tasks (application shutdown)."""
        tasks = [t for t in (self.task, self._advance_task) if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = self._advance_task = None

    def enqueue(self, keys) -> int:
        added = 0
        now = time.monotonic()
        for key in keys:
            key = (int(key[0]), int(key[1]))
            if key in self.queued:
                continue
            last = self.attempted.get(key)
            if last is not None and now - last < 6 * 3600:
                continue
            self.queue.append(key)
            self.queued.add(key)
            added += 1
        if self.queue and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self._run(), name="page-enricher")
        return added

    def snapshot_pending(self, keys=None) -> int:
        wanted = {(int(c), int(m)) for c, m in keys} if keys is not None else None
        values = self.snapshot_waiting
        return sum(1 for k in values if wanted is None or k in wanted)

    def pending(self, keys=None) -> int:
        if keys is None:
            return len(self.queue) + len(self.metadata_queue) + len(self.snapshot_waiting) + (1 if self.current else 0)
        wanted = {(int(c), int(m)) for c, m in keys}
        n = sum(1 for k in self.queue if k in wanted)
        return n + (1 if self.current in wanted else 0)

    def mark_updated(self, key):
        self.updated[(int(key[0]), int(key[1]))] = time.time()
        if len(self.updated) > 5000:
            for k, _ in sorted(self.updated.items(), key=lambda kv: kv[1])[:1000]:
                self.updated.pop(k, None)

    # ---------------------------------------------------------------- worker

    def _api_ready(self) -> bool:
        s = self.db.setting
        return bool(s("stashdb_api_key") or s("tpdb_api_token") or s("tpdb_token"))

    def _rows(self, channel_id, ids):
        ph = ",".join("?" for _ in ids)
        with self.db.lock:
            return {int(r["source_message_id"]): r for r in self.db.conn.execute(
                f"SELECT id,source_message_id,filename,metadata_source,metadata_synced_at FROM media "
                f"WHERE source_channel=? AND source_channel {content_mode.NOT_SFW_SOURCE} AND source_message_id IN ({ph})", [str(channel_id), *ids]).fetchall()}

    async def _run(self):
        """Drain the catalogue and metadata queues while Telegram is connected."""
        while self.queue or self.metadata_queue:
            if not self.telegram.connected:
                await asyncio.sleep(3)
                continue
            # Catalogue is always the first automatic network stage. Metadata
            # is intentionally deferred until the snapshot gate releases it.
            if self.queue:
                first = self.queue[0]
                batch = [k for k in list(self.queue) if k[0] == first[0]][:40]
                for k in batch:
                    try:
                        self.queue.remove(k)
                    except ValueError:
                        pass
                try:
                    acquired = False
                    if self.resources is not None:
                        await self.resources.acquire_background("metadata")
                        acquired = True
                    try:
                        if self.priority_gate:
                            async with self.priority_gate.slot("catalogue"):
                                await self._catalogue(first[0], [k[1] for k in batch])
                        else:
                            await self._catalogue(first[0], [k[1] for k in batch])
                    finally:
                        if acquired and self.resources is not None:
                            await self.resources.release_background("metadata")
                except Exception as exc:
                    self.db.log(f"Background catalogue for channel {first[0]} failed: {type(exc).__name__}: {exc}", "WARN")
                for key in batch:
                    self.snapshot_waiting.add(key)
                self._ensure_snapshot_gate()
                # Do not perform provider/fingerprint work in the same pass.
                # The snapshot maintenance lane must get the next turn.
                await asyncio.sleep(0)
                continue
            key = self.metadata_queue.popleft()
            if self._api_ready():
                # Manual Metadata & Assets work owns the metadata execution lane.
                # Page enrichment must wait rather than issuing a second provider
                # lookup for the same visible videos while the operator job runs.
                if self.metadata_blocked is not None and self.metadata_blocked():
                    self.metadata_queue.appendleft(key)
                    await asyncio.sleep(1.0)
                    continue
                self.current = key
                acquired = False
                try:
                    if self.resources is not None:
                        await self.resources.acquire_background("metadata")
                        acquired = True
                    await self._metadata(key)
                except Exception as exc:
                    self.db.log(f"Background metadata {key[0]}/{key[1]} failed: {type(exc).__name__}: {exc}", "WARN")
                finally:
                    if acquired and self.resources is not None:
                        try:
                            await self.resources.release_background("metadata")
                        except Exception:
                            _swallowed_note("page_enricher.PageEnricher._run")
                            pass
                    self.current = None
            self.attempted[key] = time.monotonic()
            self.queued.discard(key)
            await asyncio.sleep(0.3)

    def _snapshot_state(self, key):
        cid, mid = key
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT status FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=?",
                (str(cid), int(mid)),
            ).fetchone()
            thumb = self.db.conn.execute(
                "SELECT source FROM thumbnail_cache WHERE source_channel=? AND video_message_id=?",
                (str(cid), int(mid)),
            ).fetchone()
        ready = bool(row and str(row["status"] or "") == "READY" and thumb and str(thumb["source"] or "") == "playback-snapshot")
        failed = bool(row and str(row["status"] or "") == "FAILED")
        return ready, failed

    def _ensure_snapshot_gate(self) -> None:
        """Start the snapshot gate only while snapshots are actually awaited."""
        if self.snapshot_waiting and (self._advance_task is None or self._advance_task.done()):
            self._advance_task = asyncio.create_task(self._advance_after_snapshots(), name="page-enricher-snapshot-gate")

    async def _advance_after_snapshots(self):
        # Polls once a second while snapshots are pending, then exits instead
        # of polling forever on an idle application.
        while self.snapshot_waiting:
            try:
                for key in list(self.snapshot_waiting):
                    ready, failed = await asyncio.to_thread(self._snapshot_state, key)
                    if not ready and not failed:
                        continue
                    self.snapshot_waiting.discard(key)
                    self.metadata_queue.append(key)
                if self.metadata_queue and (self.task is None or self.task.done()):
                    self.task = asyncio.create_task(self._run(), name="page-enricher")
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.db.log(f"Background snapshot gate failed: {type(exc).__name__}: {exc}", "WARN")
                await asyncio.sleep(2)

    async def _catalogue(self, channel_id: int, ids: list):
        rows = await asyncio.to_thread(self._rows, channel_id, ids)
        missing = [i for i in ids if i not in rows]
        if not missing:
            return
        from .catalogue import _save_to_catalogue
        from .media_utils import (is_video_message, get_video_dimensions, media_attributes,
                                  safe_video_name, message_size, effective_orientation,
                                  streamable_status, document_identity)
        client = self.telegram.client
        entity = await client.get_entity(int(channel_id))
        videos = {}
        # Telegram returns at most 100 messages for one ID-list request.
        for start in range(0, len(missing), 100):
            messages = await client.get_messages(entity, ids=missing[start:start + 100])
            if not isinstance(messages, (list, tuple)):
                messages = [messages] if messages else []
            for msg in messages:
                if not msg or not is_video_message(msg):
                    continue
                w, h = get_video_dimensions(msg)
                _, _, duration = media_attributes(msg)
                videos[int(msg.id)] = {
                    "message_id": int(msg.id), "filename": safe_video_name(msg), "size": message_size(msg),
                    "width": w or 0, "height": h or 0, "duration": float(duration or 0),
                    "orientation": effective_orientation(msg), "streamable": streamable_status(msg),
                    "native_video": bool(getattr(msg, "video", None)), "doc_identity": document_identity(msg),
                    "thumbnail_id": None, "upload_date": getattr(msg, "date", None), "_msg": msg,
                }
        if not videos:
            return
        title = getattr(entity, "title", "") or str(channel_id)
        await asyncio.to_thread(_save_to_catalogue, self.db, int(channel_id), title, videos, {})
        for mid in videos:
            self.mark_updated((channel_id, mid))

    async def _metadata(self, key):
        """Identify one queued video and mark it updated."""
        channel_id, message_id = key
        rows = await asyncio.to_thread(self._rows, channel_id, [message_id])
        row = rows.get(message_id)
        if not row:
            return
        from ..metadata.pipeline import identify_video, get_identification_state
        state = await asyncio.to_thread(get_identification_state, self.db, channel_id, message_id)
        if state in ("MATCHED", "VERIFIED"):
            return  # already identified -- the reference DB's whole point (section 12)
        synced = row["metadata_synced_at"] or ""
        if synced:
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(synced.replace("Z", "+00:00"))
                if age.days < 30:
                    return
            except Exception:
                _swallowed_note("page_enricher.PageEnricher._metadata")
                pass
        filename = row["filename"] or f"telegram_{message_id}.mp4"
        oshash = None
        # Reuse a raw OSHASH captured during Content Automation whenever
        # available; otherwise fall back to a Telegram range fingerprint.
        try:
            with self.db.lock:
                stored = self.db.conn.execute(
                    "SELECT oshash FROM media WHERE source_channel=? AND source_message_id=?",
                    (str(channel_id), int(message_id)),
                ).fetchone()
            oshash = str(stored["oshash"] or "").strip() if stored else None
        except Exception:
            oshash = None
        # Provider matching is fingerprint-first even when a local filename
        # reference exists. If the exact hash has no usable result, identify_video
        # continues with filename clues and then falls back to the local reference.
        if not oshash:
            from ..metadata.fingerprint import oshash_from_source
            from ..core.telegram_range_transport import DirectTelegramRangeSource
            message = await self.telegram.client.get_messages(int(channel_id), ids=int(message_id))
            if not message or not getattr(message, "file", None):
                return
            filename = getattr(message.file, "name", None) or filename
            try:
                loop = asyncio.get_running_loop()
                src = DirectTelegramRangeSource(self.telegram.client, message, loop)
                if self.priority_gate:
                    async with self.priority_gate.slot("fingerprint"):
                        oshash = await asyncio.to_thread(oshash_from_source, src)
                else:
                    oshash = await asyncio.to_thread(oshash_from_source, src)
            except Exception:
                _swallowed_note("page_enricher.PageEnricher._metadata")
                pass
        if self.priority_gate:
            async with self.priority_gate.slot("metadata"):
                result = await identify_video(self.db, channel_id=int(channel_id), message_id=int(message_id),
                                              filename=filename, oshash=oshash)
        else:
            result = await identify_video(self.db, channel_id=int(channel_id), message_id=int(message_id),
                                          filename=filename, oshash=oshash)
        if result.get("action") in ("applied", "suggested"):
            self.mark_updated(key)  # card should refresh / show the new suggestion badge
