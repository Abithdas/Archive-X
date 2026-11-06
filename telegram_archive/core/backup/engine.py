"""Job-system entry point for backups; the backup logic itself lives in core.backup.manager."""
from __future__ import annotations
import json
from .manager import create_backup, PENDING_BACKUP_PASSWORDS

async def run(ctx):
    detail = ctx.job.get("detail") or "{}"
    try:
        payload = json.loads(detail) if isinstance(detail, str) else dict(detail)
    except Exception:
        payload = {}
    ctx.report(operation="Preparing verified Archive X backup", progress=1, force=True)
    result = await ctx.blocking(create_backup, ctx.db,
                                backup_type=str(payload.get("backup_type") or "full_application"),
                                password=PENDING_BACKUP_PASSWORDS.pop(ctx.job_id, None),
                                repository_path=payload.get("repository") or None)
    ctx.report(operation="Backup verified", progress=100, completed=1, total=1,
               current_item=result.get("path", ""), force=True)
