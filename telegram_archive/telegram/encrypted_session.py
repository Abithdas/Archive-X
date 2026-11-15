"""Telethon's complete SQLite session, decrypted only into an in-memory database."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import threading

from telethon.sessions import MemorySession, SQLiteSession

from ..core.encrypted_files import MAGIC, VaultError, encryption_key, unseal, write_encrypted

PURPOSE = "telegram-sqlite-session-v1"


def session_path(value) -> Path:
    path = Path(value)
    return path if str(path).endswith(".session") else Path(str(path) + ".session")


def read_snapshot(path: Path) -> bytes | None:
    encryption_key()
    if not path.exists():
        return None
    raw = path.read_bytes()
    if raw.startswith(MAGIC):
        return unseal(raw, PURPOSE)
    if not raw.startswith(b"SQLite format 3\x00"):
        raise VaultError("Unrecognized Telegram session file; no data was overwritten.")
    # Snapshot the legacy database, including committed WAL changes. No
    # plaintext temporary file is created, and the original survives on failure.
    memory = sqlite3.connect(":memory:")
    try:
        source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            source.backup(memory)
        finally:
            source.close()
        # Serialized WAL-mode databases require an on-disk WAL after loading.
        # Convert this in-memory snapshot to rollback-journal mode in its header.
        image = bytearray(memory.serialize())
        image[18:20] = b"\x01\x01"
        return bytes(image)
    finally:
        memory.close()


class EncryptedSQLiteSession(SQLiteSession):
    """Telethon SQLite session stored encrypted at rest."""
    def __init__(self, filename):
        self._encrypted_path = session_path(filename)
        self._vault_lock = threading.RLock()
        self._ready = False
        self._deleted = False
        super().__init__(None)
        self._ready = True
        self.save()
        # These legacy sidecars may contain authentication material. Only
        # remove them after a validated encrypted snapshot is committed.
        for suffix in ("-wal", "-shm", "-journal"):
            self._encrypted_path.with_name(self._encrypted_path.name + suffix).unlink(missing_ok=True)

    def _cursor(self):
        with self._vault_lock:
            if self._conn is None:
                snapshot = read_snapshot(self._encrypted_path)
                connection = sqlite3.connect(":memory:", check_same_thread=False)
                try:
                    if snapshot is not None:
                        connection.deserialize(snapshot)
                    connection.execute("PRAGMA temp_store=MEMORY")
                except Exception:
                    connection.close()
                    raise VaultError("Telegram session could not be loaded; no data was overwritten.") from None
                self._conn = connection
            return self._conn.cursor()

    def save(self):
        with self._vault_lock:
            if self._conn is not None and not self._deleted:
                self._conn.commit()
                if self._ready:
                    image = self._conn.serialize()
                    digest = hashlib.sha256(image).digest()
                    # Telethon calls save() from its keepalive loop every
                    # minute; skip the encrypt + fsync + replace when nothing
                    # changed.
                    if digest == getattr(self, "_saved_digest", None):
                        return
                    try:
                        write_encrypted(self._encrypted_path, image, PURPOSE)
                    except OSError as exc:
                        # save() runs inside Telethon's connect/keepalive
                        # tasks. Raising here kills the keepalive task or
                        # aborts a reconnect, which is what users see as a
                        # random disconnect. The in-memory session stays
                        # authoritative; the next save() retries the write.
                        self._save_error = f"{type(exc).__name__}: {exc}"
                        return
                    self._saved_digest = digest
                    self._save_error = None

    def close(self):
        with self._vault_lock:
            self.save()
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def delete(self):
        with self._vault_lock:
            self._encrypted_path.unlink(missing_ok=True)
            self._deleted = True
            return True

    def clone(self, to_instance=None):
        # Playback/transfer clones should remain ephemeral, never share a
        # persistent encrypted file or require a second disk session.
        return super().clone(to_instance if to_instance is not None else MemorySession())


