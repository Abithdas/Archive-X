"""Persistence operations for Archive X's local AI investigation history."""
from __future__ import annotations

import json

from .. import config


class AIPersistence:
    """Own AI investigation queries while using the shared database connection."""

    def __init__(self, database):
        self.database = database

    def ensure_schema(self) -> None:
        """Create only the tables and indexes owned by the AI persistence domain."""
        with self.database.lock:
            self.database.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ai_chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    response_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_created
                    ON ai_chat_messages(created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_conversation
                    ON ai_chat_messages(conversation_id, id);

                CREATE TABLE IF NOT EXISTS ai_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL DEFAULT 'OBSERVATION',
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_observations_created
                    ON ai_observations(created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS ai_investigations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    stage TEXT NOT NULL DEFAULT 'OBSERVE',
                    local_session_id TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_investigations_updated
                    ON ai_investigations(updated_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS ai_evidence_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evidence_id TEXT NOT NULL UNIQUE,
                    investigation_id INTEGER,
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL DEFAULT '',
                    source_page TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'STAGED',
                    sha256 TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_evidence_history_investigation
                    ON ai_evidence_history(investigation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_evidence_history_status
                    ON ai_evidence_history(status, created_at DESC);
                """
            )
            self.database.conn.commit()

    def migrate_legacy_schema(self) -> None:
        """Move legacy remote conversation identifiers into local session ids."""
        with self.database.lock:
            columns = {
                row[1]
                for row in self.database.conn.execute("PRAGMA table_info(ai_investigations)").fetchall()
            }
            if "local_session_id" not in columns:
                self.database.conn.execute(
                    "ALTER TABLE ai_investigations ADD COLUMN local_session_id TEXT NOT NULL DEFAULT ''"
                )
                if "openai_conversation_id" in columns:
                    self.database.conn.execute(
                        "UPDATE ai_investigations "
                        "SET local_session_id=COALESCE(NULLIF(openai_conversation_id,''),'')"
                    )
            self.database.conn.execute(
                "DELETE FROM settings WHERE key IN ('ai_openai_conversation_id','openai_model')"
            )
            self.database.conn.commit()

    def create_investigation(self, title: str, stage: str = "OBSERVE", status: str = "ACTIVE") -> int:
        now = config.now_iso()
        clean_title = str(title or "New investigation").strip()[:160] or "New investigation"
        clean_stage = str(stage or "OBSERVE").upper()[:32]
        clean_status = str(status or "ACTIVE").upper()[:32]
        with self.database.lock:
            cur = self.database.conn.execute(
                "INSERT INTO ai_investigations(title,status,stage,created_at,updated_at) VALUES(?,?,?,?,?)",
                (clean_title, clean_status, clean_stage, now, now),
            )
            self.database.conn.commit()
            return int(cur.lastrowid)

    def list_investigations(self, limit: int = 50) -> list[dict]:
        n = max(1, min(int(limit or 50), 100))
        with self.database.lock:
            rows = self.database.conn.execute(
                "SELECT id,title,status,stage,local_session_id,summary,created_at,updated_at "
                "FROM ai_investigations ORDER BY updated_at DESC, id DESC LIMIT ?", (n,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_investigation(self, investigation_id: int | None) -> dict | None:
        if not investigation_id:
            return None
        with self.database.lock:
            row = self.database.conn.execute(
                "SELECT id,title,status,stage,local_session_id,summary,created_at,updated_at "
                "FROM ai_investigations WHERE id=?",
                (int(investigation_id),),
            ).fetchone()
        return dict(row) if row else None

    def active_investigation(self) -> dict:
        """Return the active investigation, creating or repairing the selection if needed."""
        raw = self.database.setting("ai_active_investigation_id", None)
        investigation = self.get_investigation(int(raw)) if raw else None
        if investigation:
            return investigation
        with self.database.lock:
            row = self.database.conn.execute(
                "SELECT id,title,status,stage,local_session_id,summary,created_at,updated_at "
                "FROM ai_investigations ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                investigation_id = int(row["id"])
            else:
                now = config.now_iso()
                cur = self.database.conn.execute(
                    "INSERT INTO ai_investigations(title,status,stage,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("General", "ACTIVE", "OBSERVE", now, now),
                )
                self.database.conn.commit()
                investigation_id = int(cur.lastrowid)
                with self.database.lock:
                    self.database.conn.execute(
                        "UPDATE ai_chat_messages SET investigation_id=? WHERE investigation_id IS NULL",
                        (investigation_id,),
                    )
                    self.database.conn.execute(
                        "UPDATE ai_observations SET investigation_id=? WHERE investigation_id IS NULL",
                        (investigation_id,),
                    )
                    self.database.conn.commit()
        self.database.set_setting("ai_active_investigation_id", investigation_id)
        return self.get_investigation(investigation_id) or {
            "id": investigation_id,
            "title": "General",
            "status": "ACTIVE",
            "stage": "OBSERVE",
            "local_session_id": "",
            "summary": "",
            "created_at": config.now_iso(),
            "updated_at": config.now_iso(),
        }

    def set_active_investigation(self, investigation_id: int) -> dict:
        investigation = self.get_investigation(investigation_id)
        if not investigation:
            raise ValueError("Investigation not found")
        self.database.set_setting("ai_active_investigation_id", int(investigation_id))
        return investigation

    def update_investigation(
        self,
        investigation_id: int,
        *,
        title: str | None = None,
        status: str | None = None,
        stage: str | None = None,
        summary: str | None = None,
        local_session_id: str | None = None,
    ) -> dict:
        investigation = self.get_investigation(investigation_id)
        if not investigation:
            raise ValueError("Investigation not found")
        values = {
            "title": str(title if title is not None else investigation["title"])[:160],
            "status": str(status if status is not None else investigation["status"]).upper()[:32],
            "stage": str(stage if stage is not None else investigation["stage"]).upper()[:32],
            "summary": str(summary if summary is not None else investigation.get("summary", ""))[:5000],
            "local_session_id": str(
                local_session_id if local_session_id is not None else investigation.get("local_session_id", "")
            )[:200],
        }
        with self.database.lock:
            self.database.conn.execute(
                "UPDATE ai_investigations SET title=?,status=?,stage=?,summary=?,local_session_id=?,updated_at=? WHERE id=?",
                (
                    values["title"],
                    values["status"],
                    values["stage"],
                    values["summary"],
                    values["local_session_id"],
                    config.now_iso(),
                    int(investigation_id),
                ),
            )
            self.database.conn.commit()
        return self.get_investigation(investigation_id) or investigation

    def add_chat_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        response_id: str = "",
        investigation_id: int | None = None,
    ) -> int:
        investigation_id = investigation_id or int(self.active_investigation()["id"])
        with self.database.lock:
            cur = self.database.conn.execute(
                "INSERT INTO ai_chat_messages(investigation_id,conversation_id,role,content,response_id,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    investigation_id,
                    str(conversation_id or ""),
                    str(role or "user")[:32],
                    str(content or ""),
                    str(response_id or ""),
                    config.now_iso(),
                ),
            )
            self.database.conn.commit()
            return int(cur.lastrowid)

    def list_chat_messages(self, limit: int = 120, investigation_id: int | None = None) -> list[dict]:
        n = max(1, min(int(limit or 120), 500))
        investigation_id = investigation_id or int(self.active_investigation()["id"])
        with self.database.lock:
            rows = self.database.conn.execute(
                "SELECT id,investigation_id,conversation_id,role,content,response_id,created_at "
                "FROM ai_chat_messages WHERE investigation_id=? ORDER BY id DESC LIMIT ?",
                (investigation_id, n),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def clear_chat_messages(self, investigation_id: int | None = None) -> None:
        investigation_id = investigation_id or int(self.active_investigation()["id"])
        with self.database.lock:
            self.database.conn.execute(
                "DELETE FROM ai_chat_messages WHERE investigation_id=?", (investigation_id,)
            )
            self.database.conn.commit()

    def add_observation(self, kind: str, content: str, investigation_id: int | None = None) -> int:
        investigation_id = investigation_id or int(self.active_investigation()["id"])
        with self.database.lock:
            cur = self.database.conn.execute(
                "INSERT INTO ai_observations(investigation_id,kind,content,created_at) VALUES(?,?,?,?)",
                (
                    investigation_id,
                    str(kind or "OBSERVATION").upper()[:32],
                    str(content or "")[:20000],
                    config.now_iso(),
                ),
            )
            self.database.conn.commit()
            return int(cur.lastrowid)

    def list_observations(self, limit: int = 60, investigation_id: int | None = None) -> list[dict]:
        n = max(1, min(int(limit or 60), 200))
        investigation_id = investigation_id or int(self.active_investigation()["id"])
        with self.database.lock:
            rows = self.database.conn.execute(
                "SELECT id,investigation_id,kind,content,created_at FROM ai_observations "
                "WHERE investigation_id=? ORDER BY id DESC LIMIT ?",
                (investigation_id, n),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def record_evidence_history(
        self,
        *,
        evidence_id: str,
        investigation_id: int | None,
        kind: str,
        reason: str,
        scope: str,
        source_page: str,
        source: str,
        status: str,
        sha256: str,
        summary: dict | None = None,
    ) -> None:
        now = config.now_iso()
        with self.database.lock:
            self.database.conn.execute(
                "INSERT INTO ai_evidence_history(evidence_id,investigation_id,kind,reason,scope,source_page,source,status,sha256,summary_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(evidence_id) DO UPDATE SET status=excluded.status,sha256=excluded.sha256,"
                "summary_json=excluded.summary_json,updated_at=excluded.updated_at",
                (
                    str(evidence_id),
                    investigation_id,
                    str(kind or ""),
                    str(reason or "")[:2000],
                    str(scope or "")[:500],
                    str(source_page or "")[:500],
                    str(source or "")[:120],
                    str(status or "STAGED")[:32],
                    str(sha256 or "")[:128],
                    json.dumps(summary or {}, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )
            self.database.conn.commit()

    def list_evidence_history(self, investigation_id: int | None = None, limit: int = 50) -> list[dict]:
        n = max(1, min(int(limit or 50), 100))
        investigation_id = investigation_id or int(self.active_investigation()["id"])
        with self.database.lock:
            rows = self.database.conn.execute(
                "SELECT evidence_id,investigation_id,kind,reason,scope,source_page,source,status,sha256,"
                "summary_json,created_at,updated_at FROM ai_evidence_history "
                "WHERE investigation_id=? ORDER BY id DESC LIMIT ?",
                (investigation_id, n),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["summary"] = json.loads(item.pop("summary_json") or "{}")
            except Exception:
                item["summary"] = {}
            result.append(item)
        return result
