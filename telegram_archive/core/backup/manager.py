"""Backup and restore: settings, encrypted archives, retention and staged restores."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Settings and repository
#   - File iteration and encryption
#   - Inspecting and retention
#   - Create, list and restore
# ------------------------------------------------------------------
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ... import config
from ..encrypted_files import MAGIC as VAULT_MAGIC, VaultError, protected_purpose, unseal

BACKUP_FORMAT = "archive-x-backup"
BACKUP_FORMAT_VERSION = 1
MAGIC = b"AXBK1\n"
DEFAULT_REPOSITORY_NAME = "Archive X Backups"

# User-owned application state. Cache is intentionally excluded because it is
# regenerable and may contain very large player preview data.
PENDING_BACKUP_PASSWORDS: dict[str, str] = {}

BACKUP_ROOTS = (
    "database",
    "auth",
    "assets",
    "metadata",
    "ai",
    "automation",
    "state",
    "logs",
    "settings.json",
    "PERSISTENT_DATA_MANIFEST.json",
)
EXCLUDED_DIRS = {"cache", "backups", ".restore_staging"}
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
SENSITIVE_PREFIXES = ("auth/",)




# ==============================================================================
# SECTION: Settings and repository
# Backup location and settings.
# ==============================================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_repository() -> Path:
    # Keep backups outside the data root so a backup cannot recursively contain
    # previous backups. A custom repository is stored in DB settings.
    return config.USER_DATA_DIR.parent / DEFAULT_REPOSITORY_NAME


def repository(db=None) -> Path:
    value = db.setting("backup_repository", "") if db else ""
    path = Path(str(value or "").strip()).expanduser() if value else default_repository()
    return path.resolve()


def backup_settings(db) -> dict:
    repo = repository(db)
    return {
        "enabled": bool(db.setting("backup_enabled", False)),
        "repository": str(repo),
        "retention_daily": int(db.setting("backup_retention_daily", 7) or 7),
        "retention_weekly": int(db.setting("backup_retention_weekly", 4) or 4),
        "retention_monthly": int(db.setting("backup_retention_monthly", 3) or 3),
        "last_backup": db.setting("backup_last_success", ""),
        "last_error": db.setting("backup_last_error", ""),
        "schedule_time": db.setting("backup_schedule_time", "02:00"),
    }


def save_backup_settings(db, data: dict) -> dict:
    if "enabled" in data:
        db.set_setting("backup_enabled", bool(data["enabled"]))
    if "repository" in data:
        raw = str(data.get("repository") or "").strip()
        if raw:
            p = Path(raw).expanduser().resolve()
            if p == config.USER_DATA_DIR.resolve() or config.USER_DATA_DIR.resolve() in p.parents:
                raise ValueError("Backup repository cannot be inside the Archive X data folder")
            db.set_setting("backup_repository", str(p))
    for key in ("daily", "weekly", "monthly"):
        if key in data:
            value = max(0, min(365, int(data[key])))
            db.set_setting(f"backup_retention_{key}", value)
    return backup_settings(db)




# ==============================================================================
# SECTION: File iteration and encryption
# File walking, hashing, key derivation and encryption.
# ==============================================================================

def _iter_files(root: Path):
    for base in BACKUP_ROOTS:
        path = root / base
        if path.is_file():
            yield path, Path(base)
        elif path.is_dir():
            for p in path.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(root)
                if any(part in EXCLUDED_DIRS for part in rel.parts):
                    continue
                # SQLite sidecars belong to the live database, not to the consistent
                # snapshot written below. Restoring a stale WAL next to the snapshot
                # makes SQLite replay old frames over it.
                if p.name.endswith(SQLITE_SIDECAR_SUFFIXES):
                    continue
                yield p, rel


def _sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _derive_key(password: str, salt: bytes) -> bytes:
    return hash_secret_raw(password.encode("utf-8"), salt, 3, 64 * 1024, 2, 32, Type.ID)


def _encrypt_file(src: Path, dst: Path, password: str, salt: bytes) -> None:
    key = _derive_key(password, salt)
    nonce = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce))
    enc = cipher.encryptor()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fi, dst.open("wb") as fo:
        header = {"format": BACKUP_FORMAT, "version": BACKUP_FORMAT_VERSION, "encrypted": True,
                  "kdf": "argon2id", "salt": salt.hex(), "nonce": nonce.hex(), "created_at": _now()}
        raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
        fo.write(MAGIC + len(raw).to_bytes(4, "big") + raw)
        while True:
            block = fi.read(1024 * 1024)
            if not block:
                break
            fo.write(enc.update(block))
        fo.write(enc.finalize())
        fo.write(enc.tag)


def _decrypt_file(src: Path, dst: Path, password: str) -> dict:
    """Decrypt an Archive X backup file and return its header details."""
    with src.open("rb") as fi:
        if fi.read(len(MAGIC)) != MAGIC:
            raise ValueError("Invalid Archive X encrypted backup header")
        size = int.from_bytes(fi.read(4), "big")
        header = json.loads(fi.read(size).decode("utf-8"))
        salt = bytes.fromhex(header["salt"])
        nonce = bytes.fromhex(header["nonce"])
        key = _derive_key(password, salt)
        # GCM tag is at EOF. The encrypted payload is streamed into a temporary
        # file so large application backups do not require loading into RAM.
        with tempfile.NamedTemporaryFile(prefix="archive_x_decrypt_", suffix=".bin", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                block = fi.read(1024 * 1024)
                if not block:
                    break
                tmp.write(block)
        try:
            total = tmp_path.stat().st_size
            if total < 16:
                raise ValueError("Encrypted backup is incomplete")
            payload_size = total - 16
            # GCM decryptor accepts the authentication tag at finalize time,
            # allowing the ciphertext to be streamed from disk.
            with tmp_path.open("rb") as tf:
                decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).decryptor()
                # cryptography permits finalize_with_tag for streaming GCM.
                with dst.open("wb") as out:
                    remaining = payload_size
                    while remaining:
                        block = tf.read(min(1024 * 1024, remaining))
                        remaining -= len(block)
                        out.write(decryptor.update(block))
                    tag = tf.read(16)
                    out.write(decryptor.finalize_with_tag(tag))
        except Exception:
            try: dst.unlink()
            except OSError: pass
            raise ValueError("Backup password is incorrect or the backup is corrupted")
        finally:
            try: tmp_path.unlink()
            except OSError: pass
    return header




# ==============================================================================
# SECTION: Inspecting and retention
# Opening archives, inspecting backups and retention.
# ==============================================================================

def _open_archive(path: Path, password: str | None, temp_dir: Path):
    if path.read_bytes()[:len(MAGIC)] == MAGIC:
        if not password:
            raise ValueError("This backup is encrypted; a backup password is required")
        decrypted = temp_dir / "backup.zip"
        _decrypt_file(path, decrypted, password)
        return decrypted
    return path


def inspect_backup(path: str | Path, password: str | None = None) -> dict:
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise ValueError("Backup file does not exist")
    with tempfile.TemporaryDirectory(prefix="archive_x_backup_inspect_") as td:
        archive = _open_archive(src, password, Path(td))
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                if zf.testzip() is not None:
                    raise ValueError("Backup archive checksum test failed")
                raw = zf.read("BACKUP_MANIFEST.json")
                manifest = json.loads(raw.decode("utf-8"))
                if manifest.get("format") != BACKUP_FORMAT:
                    raise ValueError("Unsupported backup format")
                if int(manifest.get("format_version", 0)) != BACKUP_FORMAT_VERSION:
                    raise ValueError("Unsupported backup format version")
                names = set(zf.namelist())
                for name in names:
                    purpose = protected_purpose(name)
                    if purpose:
                        payload = zf.read(name)
                        if payload.startswith(VAULT_MAGIC):
                            # Verify the original vault key before a restore
                            # can replace currently working credentials.
                            unseal(payload, purpose)
                missing = [x for x in manifest.get("files", []) if x.get("path") not in names]
                if missing:
                    raise ValueError(f"Backup is missing {len(missing)} manifest file(s)")
                return {"ok": True, "manifest": manifest, "size": src.stat().st_size, "encrypted": src.read_bytes()[:len(MAGIC)] == MAGIC}
        except zipfile.BadZipFile as exc:
            raise ValueError("Backup archive is corrupted") from exc


def apply_retention(db) -> dict:
    repo = repository(db)
    rows = []
    for p in repo.glob("*.axbackup") if repo.exists() else []:
        try: rows.append((p.stat().st_mtime, p))
        except OSError: pass
    rows.sort(reverse=True)
    daily = max(0, int(db.setting("backup_retention_daily", 7) or 7))
    weekly = max(0, int(db.setting("backup_retention_weekly", 4) or 4))
    monthly = max(0, int(db.setting("backup_retention_monthly", 3) or 3))
    keep: set[Path] = set(p for _, p in rows[:daily])
    import datetime as _dt
    weeks: set[tuple[int,int]] = set(); months: set[tuple[int,int]] = set()
    for ts,p in rows:
        dt=_dt.datetime.fromtimestamp(ts)
        wk=dt.isocalendar()[:2]
        if wk not in weeks and len(weeks)<weekly:
            keep.add(p); weeks.add(wk)
        mk=(dt.year,dt.month)
        if mk not in months and len(months)<monthly:
            keep.add(p); months.add(mk)
    deleted=0
    for _,p in rows:
        if p not in keep:
            try: p.unlink(); deleted+=1
            except OSError: pass
    return {"kept":len(keep),"deleted":deleted}




# ==============================================================================
# SECTION: Create, list and restore
# Public backup and restore operations.
# ==============================================================================

def create_backup(db, *, backup_type: str = "full_application", password: str | None = None,
                  repository_path: str | None = None) -> dict:
    """Create a backup archive, optionally encrypted, then apply retention."""
    root = config.USER_DATA_DIR.resolve()
    repo = Path(repository_path).expanduser().resolve() if repository_path else repository(db)
    if repo == root or root in repo.parents:
        raise ValueError("Backup repository cannot be inside Archive X data")
    repo.mkdir(parents=True, exist_ok=True)
    if backup_type not in {"configuration", "full_application"}:
        raise ValueError("Unsupported backup type")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final = repo / f"Archive_X_Backup_{timestamp}.axbackup"
    tmp_zip = repo / f".{final.stem}.zip.tmp"
    manifest = {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "application": "Archive X",
        "application_version": config.APP_VERSION,
        "data_manifest_version": config.DATA_MANIFEST_VERSION,
        "created_at": _now(),
        "backup_type": backup_type,
        "media_included": False,
        "cache_included": False,
        "encrypted": bool(password),
        "protected_credentials": "AES-256-GCM; original vault environment secret required",
        "checksum_algorithm": "sha256",
        "files": [],
    }
    try:
        db_snapshot = None
        try:
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for src, rel in _iter_files(root):
                    if backup_type == "configuration" and rel.parts[0] not in {"settings.json", "PERSISTENT_DATA_MANIFEST.json", "database", "auth"}:
                        continue
                    arc = str(rel).replace("\\", "/")
                    if arc == "database/archive.db":
                        import sqlite3
                        fd, snap_name = tempfile.mkstemp(prefix="archive_x_db_snapshot_", suffix=".db")
                        os.close(fd)
                        db_snapshot = Path(snap_name)
                        src_conn = sqlite3.connect(str(src))
                        dst_conn = sqlite3.connect(str(db_snapshot))
                        try:
                            src_conn.backup(dst_conn)
                        finally:
                            dst_conn.close(); src_conn.close()
                        zf.write(db_snapshot, arc)
                        snap_size = db_snapshot.stat().st_size
                        snap_hash = _sha256(db_snapshot)
                        manifest["files"].append({"path": arc, "size": snap_size, "sha256": snap_hash})
                    else:
                        purpose = protected_purpose(arc)
                        if purpose:
                            payload = src.read_bytes()
                            if not payload.startswith(VAULT_MAGIC):
                                raise VaultError(
                                    "Backup refused an unencrypted credentials/session file. "
                                    "Restart Archive X to complete protected-storage migration."
                                )
                            unseal(payload, purpose)
                            zf.writestr(arc, payload)
                            manifest["files"].append({"path": arc, "size": len(payload),
                                                      "sha256": hashlib.sha256(payload).hexdigest()})
                        else:
                            zf.write(src, arc)
                            manifest["files"].append({"path": arc, "size": src.stat().st_size, "sha256": _sha256(src)})
                zf.writestr("BACKUP_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        finally:
            if db_snapshot:
                db_snapshot.unlink(missing_ok=True)
        if password:
            salt = os.urandom(16)
            _encrypt_file(tmp_zip, final, password, salt)
            tmp_zip.unlink(missing_ok=True)
        else:
            os.replace(tmp_zip, final)
        # Re-open and validate the produced archive before declaring success.
        info = inspect_backup(final, password)
        manifest["verified_at"] = _now()
        db.set_setting("backup_last_success", manifest["verified_at"])
        db.set_setting("backup_last_error", "")
        retention = apply_retention(db)
        return {"ok": True, "path": str(final), "size": final.stat().st_size, "manifest": manifest, "verified": True, "retention": retention}
    except Exception as exc:
        try: tmp_zip.unlink(missing_ok=True)
        except Exception: pass
        db.set_setting("backup_last_error", f"{type(exc).__name__}: {exc}")
        raise


def list_backups(db) -> list[dict]:
    repo = repository(db)
    if not repo.exists():
        return []
    out = []
    for p in sorted(repo.glob("*.axbackup"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with p.open("rb") as fh:
                encrypted = fh.read(len(MAGIC)) == MAGIC
            if encrypted:
                out.append({"name": p.name, "path": str(p), "size": p.stat().st_size,
                            "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                            "type": "encrypted", "encrypted": True, "verified": None, "files": 0})
                continue
            info = inspect_backup(p)
            m = info["manifest"]
            out.append({"name": p.name, "path": str(p), "size": p.stat().st_size,
                        "created_at": m.get("created_at", ""), "type": m.get("backup_type", ""),
                        "encrypted": False, "verified": True,
                        "files": len(m.get("files", []))})
        except Exception as exc:
            out.append({"name": p.name, "path": str(p), "size": p.stat().st_size,
                        "created_at": "", "type": "unknown", "encrypted": False,
                        "verified": False, "error": str(exc)})
    return out


def stage_restore(db, backup_path: str, password: str | None = None) -> dict:
    """Validate a backup and stage it to be applied on next startup."""
    src = Path(backup_path).expanduser().resolve()
    info = inspect_backup(src, password)
    if info["manifest"].get("media_included"):
        raise ValueError("Media-inclusive restores are not supported by this staged restore yet")
    parent = config.USER_DATA_DIR.parent
    stage = Path(tempfile.mkdtemp(prefix="archive_x_restore_", dir=str(parent)))
    try:
        with tempfile.TemporaryDirectory(prefix="archive_x_restore_open_") as td:
            archive = _open_archive(src, password, Path(td))
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(stage)
        for item in info["manifest"].get("files", []):
            target = stage / str(item["path"])
            if not target.is_file() or target.stat().st_size != int(item.get("size", target.stat().st_size)):
                raise ValueError(f"Restored file failed size validation: {item.get('path')}")
            if _sha256(target) != str(item.get("sha256") or ""):
                raise ValueError(f"Restored file failed checksum validation: {item.get('path')}")
        # Backups made before v29.4.385 also carried the live WAL/SHM files. Drop
        # them so the validated snapshot is the only database state restored.
        for sidecar in stage.rglob("*"):
            if sidecar.is_file() and sidecar.name.endswith(SQLITE_SIDECAR_SUFFIXES):
                sidecar.unlink()
        # Validate the staged database with sqlite's read-only open.
        db_file = stage / "database" / "archive.db"
        if not db_file.is_file():
            raise ValueError("Backup does not contain the Archive X database")
        import sqlite3
        conn = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        marker = {
            "version": 1,
            "backup_path": str(src),
            "staging_path": str(stage),
            "created_at": _now(),
            "application_version": info["manifest"].get("application_version", ""),
        }
        pending = parent / ".archive_x_pending_restore.json"
        pending.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        return {"ok": True, "restart_required": True, "staging_path": str(stage), "manifest": info["manifest"]}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
