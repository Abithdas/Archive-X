"""Web dashboard authentication: password hashing, sessions, rate limiting.

This is the piece that was missing entirely in v13 (routes called a
require_auth() that didn't exist anywhere). It's implemented for real here
and unit-tested as part of building v14 — see the project chat history.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any, Optional

from .. import config
from .database import Database


def hash_password(password: str, iterations: int = 260_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest_hex = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations)
        test_digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
        )
        return hmac.compare_digest(test_digest.hex(), digest_hex)
    except Exception:
        return False


class SessionStore:
    """Login sessions. Kept in memory and (when a database is given) also in
    SQLite, so restarting or updating the app does not log you out and an
    open browser tab keeps working. Only a hash of each token is stored."""

    WRITE_EVERY = 600   # seconds between expiry refreshes written to the DB

    def __init__(self, db=None):
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.db = db
        if db is not None:
            try:
                with db.lock:
                    db.conn.execute(
                        "CREATE TABLE IF NOT EXISTS web_sessions ("
                        " token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL)")
                    db.conn.execute("DELETE FROM web_sessions WHERE expires < ?", (time.time(),))
                    db.conn.commit()
            except Exception:
                self.db = None

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _db_write(self, token: str, username: str, expires: float):
        if self.db is None:
            return
        try:
            with self.db.lock:
                self.db.conn.execute(
                    "INSERT OR REPLACE INTO web_sessions(token_hash,username,expires) VALUES(?,?,?)",
                    (self._hash(token), username, expires))
                self.db.conn.commit()
        except Exception as exc:
            # The in-memory session still works; only persistence across a
            # restart is lost, so record why.
            self.db.log(f"Web session could not be persisted: {type(exc).__name__}: {exc}", "WARN")

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = time.time() + config.SESSION_TTL_SECONDS
        with self.lock:
            self.sessions[token] = {"username": username, "expires": expires, "saved": time.time()}
        self._db_write(token, username, expires)
        return token

    def get(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        now = time.time()
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None and self.db is not None:
                try:
                    with self.db.lock:
                        row = self.db.conn.execute(
                            "SELECT username,expires FROM web_sessions WHERE token_hash=?",
                            (self._hash(token),)).fetchone()
                except Exception:
                    row = None
                if row:
                    entry = {"username": row["username"], "expires": float(row["expires"]), "saved": now}
                    self.sessions[token] = entry
            if not entry:
                return None
            if entry["expires"] < now:
                self.sessions.pop(token, None)
                return None
            entry["expires"] = now + config.SESSION_TTL_SECONDS
            refresh = now - entry.get("saved", 0) > self.WRITE_EVERY
            if refresh:
                entry["saved"] = now
        if refresh:
            self._db_write(token, entry["username"], entry["expires"])
        return entry

    def destroy(self, token: Optional[str]):
        if not token:
            return
        with self.lock:
            self.sessions.pop(token, None)
        if self.db is not None:
            try:
                with self.db.lock:
                    self.db.conn.execute("DELETE FROM web_sessions WHERE token_hash=?", (self._hash(token),))
                    self.db.conn.commit()
            except Exception as exc:
                self.db.log(f"Web session logout could not be persisted: {type(exc).__name__}: {exc}", "WARN")

    def destroy_all(self):
        """Used when the web password changes: every device must log in again."""
        with self.lock:
            self.sessions.clear()
        if self.db is not None:
            # Must not fail silently: a surviving persisted session would keep
            # an old device logged in after a credential change and restart.
            with self.db.lock:
                self.db.conn.execute("DELETE FROM web_sessions")
                self.db.conn.commit()


class LoginRateLimiter:
    """Simple sliding-window limiter keyed by client address."""

    def __init__(self, max_attempts: int = config.LOGIN_MAX_ATTEMPTS, window: int = config.LOGIN_WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window = window
        self.lock = threading.RLock()
        self.attempts: dict[str, list[float]] = {}

    def check(self, key: str):
        # Imported lazily to avoid a hard FastAPI dependency in this module.
        from fastapi import HTTPException

        now = time.time()
        with self.lock:
            recent = [t for t in self.attempts.get(key, []) if now - t < self.window]
            self.attempts[key] = recent
            if len(recent) >= self.max_attempts:
                retry_after = int(self.window - (now - recent[0]))
                raise HTTPException(429, f"Too many login attempts. Try again in {max(retry_after, 1)}s.")

    def record(self, key: str):
        with self.lock:
            self.attempts.setdefault(key, []).append(time.time())

    def clear(self, key: str):
        with self.lock:
            self.attempts.pop(key, None)


DEFAULT_WEB_USERNAME = "admin"
LOGIN_RESET_FLAG = "web_login_default_v18_7"
SETUP_COMPLETE_FLAG = "web_login_setup_complete_v28_9"
SETUP_REQUIRED_FLAG = "web_login_setup_required_v28_9"

def is_setup_required(db) -> bool:
    """Return whether the administrator must complete credential setup."""
    return str(db.setting(SETUP_COMPLETE_FLAG) or "").strip() == ""

def is_default_login(db) -> bool:
    """Compatibility alias: an unset setup is treated as requiring credential setup."""
    return is_setup_required(db)


def set_web_login(db, username: str, password: str | None = None) -> None:
    username = (username or "").strip()
    if username:
        db.set_setting("web_username", username)
    if password:
        db.set_setting("web_password_hash", hash_password(password))


def ensure_web_credentials(db: Database) -> None:
    """Prepare secure first-run credential setup without embedding a password.

    Existing installations are deliberately migrated into a one-time setup
    state. This prevents the historical LAN default credential from remaining
    an accepted production password. The operator chooses the new credential
    through the public setup endpoint, after which all sessions are invalidated.
    """
    username = str(db.setting("web_username") or DEFAULT_WEB_USERNAME).strip() or DEFAULT_WEB_USERNAME
    if not db.setting("web_username"):
        db.set_setting("web_username", username)
    if not db.setting(SETUP_REQUIRED_FLAG):
        db.set_setting(SETUP_REQUIRED_FLAG, config.now_iso())
    if not db.setting(SETUP_COMPLETE_FLAG):
        db.log("Web dashboard requires first-run administrator credential setup", "WARN")

