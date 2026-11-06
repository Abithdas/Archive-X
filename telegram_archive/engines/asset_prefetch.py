"""Background library asset prefetch.

Keeps video thumbnails and performer/studio artwork in SQLite before a page is
opened.  The browser therefore becomes a local-cache consumer instead of a
Telegram/network downloader.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter

from ..jobs.context import JobCancelled
from .thumbnail_cache import prefetch_entity_image

THUMB_CONCURRENCY = 2
ART_CONCURRENCY = 4
BATCH = 8
# Keep automatic maintenance incremental. A new/large channel must not turn
# background prefetch into an hours-long foreground competitor for Telegram work.
MAX_ITEMS_PER_RUN = 32


# Artwork URLs that failed recently. Without this memory a permanently broken
# provider URL kept needs_prefetch() true forever, so the idle watcher started
# a new job every minute that re-downloaded the same failing URLs.
FAILURE_COOLDOWN_SECONDS = 12 * 3600
_recent_failures: dict[tuple[str, int, str], float] = {}


def _cooling_down(kind: str, entity_id: int, url: str) -> bool:
    failed_at = _recent_failures.get((kind, int(entity_id), str(url)))
    return failed_at is not None and time.monotonic() - failed_at < FAILURE_COOLDOWN_SECONDS


def _record_failure(kind: str, entity_id: int, url: str) -> None:
    _recent_failures[(kind, int(entity_id), str(url))] = time.monotonic()


def _pending_assets(db, limit: int = MAX_ITEMS_PER_RUN):
    remaining = max(1, int(limit or MAX_ITEMS_PER_RUN))
    out: dict[str, list] = {"performer": [], "studio": []}
    queries = (
        ("performer", "SELECT p.id,p.name,p.image FROM performers p LEFT JOIN performer_image_cache c ON c.performer_id=p.id "
                      "WHERE COALESCE(p.image_locked,0)=0 AND TRIM(COALESCE(p.image,''))<>'' "
                      "AND (c.data IS NULL OR c.url<>p.image) ORDER BY p.id"),
        ("studio", "SELECT s.id,s.name,s.image FROM studios s LEFT JOIN studio_image_cache c ON c.studio_id=s.id "
                   "WHERE COALESCE(s.image_locked,0)=0 AND TRIM(COALESCE(s.image,''))<>'' "
                   "AND (c.data IS NULL OR c.url<>s.image) ORDER BY s.id"),
    )
    with db.lock:
        for kind, sql in queries:
            for row in db.conn.execute(sql):
                if remaining <= 0:
                    break
                if _cooling_down(kind, int(row["id"]), str(row["image"] or "")):
                    continue
                out[kind].append(row)
                remaining -= 1
    return out["performer"], out["studio"]


def needs_prefetch(db) -> bool:
    performers, studios = _pending_assets(db, 1)
    return bool(performers or studios)


async def run(ctx):
    performers,studios=await asyncio.to_thread(_pending_assets,ctx.db,MAX_ITEMS_PER_RUN)
    artwork=[("performer",r) for r in performers]+[("studio",r) for r in studios]
    total=len(artwork)
    if not total:
        ctx.report(completed=0,total=0,progress=100,operation="Provider artwork already cached",force=True)
        return
    stats=Counter(); art_sem=asyncio.Semaphore(ART_CONCURRENCY)
    ctx.report(completed=0,total=total,operation="Prefetching performer/studio artwork",force=True)
    for start in range(0,len(artwork),BATCH):
        batch=artwork[start:start+BATCH]
        async def one_art(item):
            await ctx.checkpoint(); kind,row=item
            async with art_sem:
                try:
                    if str(row["image"] or "").strip():
                        await prefetch_entity_image(ctx.db,kind,int(row["id"]),str(row["image"])); stats["art_ok"]+=1
                    else: stats["art_missing"]+=1
                except (JobCancelled,asyncio.CancelledError): raise
                except Exception as exc:
                    stats["art_failed"]+=1
                    _record_failure(kind,int(row["id"]),str(row["image"] or ""))
                    if stats["art_failed"]<=10: ctx.db.log(f"Asset prefetch {kind} artwork {row['name']} failed: {type(exc).__name__}: {exc}","WARN")
                finally:
                    stats["done"]+=1; ctx.report(completed=stats["done"],total=total,current_item=str(row["name"] or f"{kind} {row['id']}"),operation="Prefetching performer/studio artwork")
        await asyncio.gather(*(one_art(x) for x in batch))
    ctx.report(completed=total,total=total,operation="Finished",force=True)
    ctx.db.log(f"Library Asset Prefetch: video snapshots are handled by Playback Snapshot Cache; {stats['art_ok']} provider artworks cached, {stats['art_failed']} artwork failures, {stats['art_missing']} without usable artwork", "SUCCESS" if stats['art_failed']==0 else "WARN")

