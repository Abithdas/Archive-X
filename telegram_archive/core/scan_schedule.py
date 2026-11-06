"""Cancellable local-time gate for optional Metadata & Assets daily windows."""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta

async def wait_for_metadata_scan_window(ctx) -> None:
    if not bool(getattr(ctx, "scan_window_enforced", False)):
        return
    while True:
        enabled = str(await asyncio.to_thread(ctx.db.setting, "metadata_scan_window_enabled", "0") or "0") == "1"
        if not enabled:
            return
        start = str(await asyncio.to_thread(ctx.db.setting, "metadata_scan_window_start", "01:00") or "01:00")
        end = str(await asyncio.to_thread(ctx.db.setting, "metadata_scan_window_end", "07:00") or "07:00")
        now = datetime.now()
        try:
            sh, sm = (int(x) for x in start.split(":")); eh, em = (int(x) for x in end.split(":"))
            a = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            b = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        except Exception:
            return
        if a == b or (a <= now < b if a < b else (now >= a or now < b)):
            return
        if a < now:
            a += timedelta(days=1)
        seconds = max(1.0, (a-now).total_seconds())
        ctx.report(operation=f"Metadata & Assets paused · daily window {start}–{end}", current_item="", force=True)
        await ctx.checkpoint()
        await asyncio.sleep(min(10.0, seconds))
