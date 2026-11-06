"""Short-lived, capability-scoped live test sessions for Archive X.

The bridge is intentionally not a general-purpose proxy.  It exposes only
curated test actions, keeps tokens hashed in memory, requires normal Archive X
login to issue a session, and defaults to read-only capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import threading
from typing import Any


CAPABILITIES = (
    "READ_STATUS",
    "READ_LOGS",
    "API_TEST",
    "PLAYBACK_TEST",
    "UI_TEST",
    "TELEGRAM_READ",
    "TELEGRAM_WRITE",
    "DESTRUCTIVE",
)
READ_ONLY = frozenset(CAPABILITIES[:6])
WRITE_CAPS = frozenset({"TELEGRAM_WRITE", "DESTRUCTIVE"})


@dataclass
class TestSession:
    """One operator-authorized live test bridge session."""
    session_id: str
    token_hash: str
    capabilities: frozenset[str]
    expires_at: datetime
    created_at: datetime
    created_by: str
    revoked: bool = False
    origin_session_token: str = field(default="", repr=False)
    last_action: str = ""
    action_count: int = 0

    def expired(self) -> bool:
        return self.revoked or datetime.now(timezone.utc) >= self.expires_at

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "capabilities": sorted(self.capabilities),
            "revoked": self.revoked,
            "active": not self.expired(),
            "last_action": self.last_action,
            "action_count": self.action_count,
        }


class LiveTestBridge:
    """In-memory capability registry for operator-authorized live testing."""

    def __init__(self, *, max_ttl_seconds: int = 30 * 60, default_ttl_seconds: int = 15 * 60):
        self.max_ttl_seconds = max(60, int(max_ttl_seconds))
        self.default_ttl_seconds = min(max(60, int(default_ttl_seconds)), self.max_ttl_seconds)
        self._lock = threading.RLock()
        self._sessions: dict[str, TestSession] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    @staticmethod
    def _clean_caps(capabilities: Any) -> frozenset[str]:
        if capabilities is None:
            return frozenset(READ_ONLY)
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        vals = {str(x).strip().upper() for x in capabilities if str(x).strip()}
        unknown = vals - set(CAPABILITIES)
        if unknown:
            raise ValueError(f"Unknown capabilities: {', '.join(sorted(unknown))}")
        # DESTRUCTIVE is always opt-in together with write capability.  Do not
        # silently elevate a read-only session.
        return frozenset(vals)

    def issue(self, *, created_by: str, capabilities=None, ttl_seconds=None, origin_session_token: str = "") -> tuple[TestSession, str]:
        caps = self._clean_caps(capabilities)
        ttl = self.default_ttl_seconds if ttl_seconds is None else max(60, min(int(ttl_seconds), self.max_ttl_seconds))
        now = datetime.now(timezone.utc)
        sid = secrets.token_hex(8)
        token = secrets.token_urlsafe(32)
        session = TestSession(
            session_id=sid,
            token_hash=self._hash(token),
            capabilities=caps,
            expires_at=now + timedelta(seconds=ttl),
            created_at=now,
            created_by=str(created_by or "operator"),
            origin_session_token=origin_session_token,
        )
        with self._lock:
            self._purge_locked()
            self._sessions[sid] = session
        return session, token

    def authenticate(self, token: str) -> TestSession | None:
        supplied = str(token or "").strip()
        if not supplied:
            return None
        token_hash = self._hash(supplied)
        with self._lock:
            self._purge_locked()
            for session in self._sessions.values():
                if secrets.compare_digest(session.token_hash, token_hash) and not session.expired():
                    return session
        return None

    def revoke(self, session_id: str) -> bool:
        with self._lock:
            sess = self._sessions.get(str(session_id))
            if not sess:
                return False
            sess.revoked = True
            return True

    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            self._purge_locked()
            return [x.to_public() for x in sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)]

    def require(self, token: str, capability: str) -> TestSession:
        session = self.authenticate(token)
        if session is None:
            raise PermissionError("Invalid or expired live test session")
        cap = str(capability).upper()
        if cap not in session.capabilities:
            raise PermissionError(f"Live test capability required: {cap}")
        return session

    def record(self, session: TestSession, action: str) -> None:
        with self._lock:
            session.last_action = str(action)[:120]
            session.action_count += 1

    def _purge_locked(self) -> None:
        expired = [sid for sid, sess in self._sessions.items() if sess.expired()]
        for sid in expired:
            self._sessions.pop(sid, None)
