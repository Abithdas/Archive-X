"""Bounded, read-only Archive X tools exposed to the local AI model."""
from __future__ import annotations

import json
import re
from typing import Any

from .ai_safety import redact


class AIToolRegistry:
    """Read-only library and job tools exposed to the assistant."""
    def __init__(self, db, jobs=None, governor=None):
        self.db = db
        self.jobs = jobs
        self.governor = governor

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": "search_library", "description": "Search published Archive X videos, performers, studios, and tags. Read-only.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"], "additionalProperties": False}}},
            {"type": "function", "function": {"name": "get_library_stats", "description": "Return bounded local library and job counts. Read-only.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
            {"type": "function", "function": {"name": "get_jobs", "description": "Return active or recent jobs and statuses. Read-only.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "additionalProperties": False}}},
            {"type": "function", "function": {"name": "get_job", "description": "Inspect one Archive X job by id. Read-only.", "parameters": {"type": "object", "properties": {"job_id": {"type": "string", "maxLength": 80}}, "required": ["job_id"], "additionalProperties": False}}},
            {"type": "function", "function": {"name": "get_diagnostics_summary", "description": "Return a bounded current diagnostics summary, not raw logs. Read-only.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
            {"type": "function", "function": {"name": "find_unresolved_metadata", "description": "Find published or catalogued videos whose metadata identification state needs review. Read-only.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "additionalProperties": False}}},
            {"type": "function", "function": {"name": "find_duplicates", "description": "Find duplicate video fingerprints by OSHASH. Read-only.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "additionalProperties": False}}},
        ]

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        methods = {
            "search_library": self.search_library,
            "get_library_stats": self.get_library_stats,
            "get_jobs": self.get_jobs,
            "get_job": self.get_job,
            "get_diagnostics_summary": self.get_diagnostics_summary,
            "find_unresolved_metadata": self.find_unresolved_metadata,
            "find_duplicates": self.find_duplicates,
        }
        fn = methods.get(str(name))
        if fn is None:
            raise ValueError("Unknown AI tool")
        return redact(fn(**args))     # whatever a tool finds, the model never sees a key, token, password or cookie

    def search_library(self, query: str, limit: int = 10) -> dict[str, Any]:
        q = str(query or "").strip()
        if not q:
            return {"query": "", "videos": [], "performers": [], "studios": [], "tags": []}
        n = max(1, min(20, int(limit or 10)))
        like = f"%{q}%"
        with self.db.reading() as conn:
            videos = conn.execute("""
                SELECT m.id,m.source_channel,m.source_message_id,m.title,m.filename,m.duration,m.size,m.identification_state
                FROM archive_x_published_media m
                WHERE COALESCE(m.title,'') LIKE ? OR COALESCE(m.filename,'') LIKE ? OR COALESCE(m.caption,'') LIKE ?
                ORDER BY m.upload_date DESC LIMIT ?
            """, (like, like, like, n)).fetchall()
            performers = conn.execute("SELECT id,name FROM performers WHERE name LIKE ? ORDER BY name LIMIT ?", (like, n)).fetchall()
            studios = conn.execute("SELECT id,name FROM studios WHERE name LIKE ? ORDER BY name LIMIT ?", (like, n)).fetchall()
            tags = conn.execute("SELECT id,name FROM tags WHERE name LIKE ? ORDER BY name LIMIT ?", (like, n)).fetchall()
        return {"query": q, "videos": [dict(x) for x in videos], "performers": [dict(x) for x in performers], "studios": [dict(x) for x in studios], "tags": [dict(x) for x in tags]}

    def get_library_stats(self) -> dict[str, Any]:
        return {"stats": self.db.dashboard_stats()}

    def get_jobs(self, limit: int = 12) -> dict[str, Any]:
        n = max(1, min(20, int(limit or 12)))
        active, recent = self.db.job_dashboard_snapshot()
        rows = active[:n]
        if len(rows) < n:
            rows.extend(list(recent.values())[: n - len(rows)])
        return {"jobs": [{k: v for k, v in row.items() if k in {"id", "job_type", "status", "current_operation", "created_at", "updated_at", "error", "completed", "total"}} for row in rows]}

    def get_job(self, job_id: str) -> dict[str, Any]:
        jid = str(job_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,100}", jid):
            raise ValueError("Invalid job id")
        row = self.db.get_job(jid)
        if not row:
            return {"found": False, "job_id": jid}
        keep = {"id", "job_type", "status", "error", "current_operation", "created_at", "updated_at", "completed", "total", "dependencies", "executions", "events"}
        return {"found": True, "job": {k: row.get(k) for k in keep if k in row}}

    def get_diagnostics_summary(self) -> dict[str, Any]:
        from .diagnostics import observability_snapshot, recent_structured_events
        snap = observability_snapshot(recent_limit=20, errors_limit=10)
        events = recent_structured_events(30)
        return {"observability": snap, "recent_events": [{k: e.get(k) for k in ("ts", "level", "event", "message", "job_id", "operation") if e.get(k) is not None} for e in events]}

    def find_unresolved_metadata(self, limit: int = 10) -> dict[str, Any]:
        n = max(1, min(20, int(limit or 10)))
        with self.db.reading() as conn:
            rows = conn.execute("""
                SELECT id,source_channel,source_message_id,title,filename,identification_state,metadata_source
                FROM media
                WHERE COALESCE(identification_state,'UNIDENTIFIED') IN ('UNIDENTIFIED','PARTIAL','REPAIR')
                ORDER BY updated_at DESC,id DESC LIMIT ?
            """, (n,)).fetchall()
        return {"count": len(rows), "videos": [dict(x) for x in rows]}

    def find_duplicates(self, limit: int = 10) -> dict[str, Any]:
        n = max(1, min(20, int(limit or 10)))
        with self.db.reading() as conn:
            groups = conn.execute("""
                SELECT oshash,COUNT(*) AS count
                FROM media
                WHERE oshash IS NOT NULL AND TRIM(oshash)<>''
                GROUP BY oshash HAVING COUNT(*)>1
                ORDER BY count DESC,oshash LIMIT ?
            """, (n,)).fetchall()
            out=[]
            for group in groups:
                rows=conn.execute("SELECT id,source_channel,source_message_id,title,filename,size,duration FROM media WHERE oshash=? ORDER BY id", (group["oshash"],)).fetchall()
                out.append({"oshash":group["oshash"],"count":group["count"],"videos":[dict(x) for x in rows]})
        return {"groups": out}
