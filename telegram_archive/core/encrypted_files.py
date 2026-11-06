"""Authenticated encryption at rest; keys never live alongside protected files."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
import tempfile
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"ARCHIVE-X-AESGCM-1\n"
LOCAL_KEY_MAGIC = b"ARCHIVE-X-LOCAL-KEY-1\n"
DPAPI_KEY_MAGIC = b"ARCHIVE-X-DPAPI-KEY-1\n"
LOCK = threading.RLock()


class VaultError(RuntimeError):
    """A protected file must not silently become an empty/default file."""


def _protect_local_key(key: bytes) -> bytes:
    """Protect the generated per-user key with Windows DPAPI where available."""
    if os.name != "nt":
        return LOCAL_KEY_MAGIC + key

    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(wintypes.BYTE))]

    source_buffer = ctypes.create_string_buffer(key)
    source = DataBlob(len(key), ctypes.cast(source_buffer, ctypes.POINTER(wintypes.BYTE)))
    result = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "Archive X protected-storage key",
        None, None, None, 0x1, ctypes.byref(result),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return DPAPI_KEY_MAGIC + ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(result.pbData)


def _unprotect_local_key(payload: bytes) -> bytes:
    """Decode a stored local key (DPAPI on Windows, plain marker elsewhere)."""
    if payload.startswith(LOCAL_KEY_MAGIC) and os.name != "nt":
        key = payload[len(LOCAL_KEY_MAGIC):]
    elif payload.startswith(DPAPI_KEY_MAGIC) and os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(wintypes.BYTE))]

        protected = payload[len(DPAPI_KEY_MAGIC):]
        source_buffer = ctypes.create_string_buffer(protected)
        source = DataBlob(len(protected), ctypes.cast(source_buffer, ctypes.POINTER(wintypes.BYTE)))
        result = DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result),
        ):
            raise VaultError(
                "Archive X cannot unlock its Windows-protected local key. Sign in to the "
                "same Windows account or restore the original encryption secret."
            )
        try:
            key = ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            kernel32.LocalFree(result.pbData)
    else:
        raise VaultError(
            "Archive X's local encryption key has an unsupported or invalid format. "
            "Restore the original encryption secret or an intact backup."
        )
    if len(key) != 32:
        raise VaultError("Archive X's local encryption key is invalid; protected data was not changed.")
    return key


def _encrypted_user_files_exist(config) -> bool:
    """Do not invent a replacement key when existing ciphertext needs the old one."""
    candidates = (
        config.SECRETS_PATH, config.METADATA_KEYS_PATH,
        config.MEDIA_MANAGER_CONFIG_PATH,
    )
    for path in candidates:
        for candidate in (Path(path), Path(f"{path}.latest")):
            try:
                if candidate.is_file() and candidate.read_bytes().startswith(MAGIC):
                    return True
            except OSError:
                continue
    return False


def _local_key() -> bytes:
    """Load the protected-storage key, creating it when no encrypted files exist yet."""
    # Keep this key outside the movable app folder and the configurable data
    # root. On Windows DPAPI binds it to this Windows user profile.
    from .. import config

    key_path = Path(config.DATA_LOCATION_FILE).parent / "protected-storage.key"

    def read_existing() -> bytes:
        try:
            return _unprotect_local_key(key_path.read_bytes())
        except OSError:
            raise VaultError(
                "Archive X cannot read its per-user encryption key. Check access to "
                "the Archive X folder under your user profile."
            ) from None

    if key_path.exists():
        return read_existing()
    if _encrypted_user_files_exist(config):
        raise VaultError(
            "Archive X found encrypted data but its local key is missing. Set the original "
            "ARCHIVE_X_ENCRYPTION_KEY or SESSION_SECRET, or restore the key from the same "
            "Windows user profile. No protected data was overwritten."
        )

    key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = os.urandom(32)
    payload = _protect_local_key(key)
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read_existing()
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(key_path, 0o600)
    except Exception:
        try:
            key_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return key


def encryption_key() -> bytes:
    secret = os.environ.get("ARCHIVE_X_ENCRYPTION_KEY") or os.environ.get("SESSION_SECRET")
    secret_bytes = secret.encode("utf-8") if secret else _local_key()
    if len(secret_bytes) < 32:
        raise VaultError(
            "Protected storage requires ARCHIVE_X_ENCRYPTION_KEY or SESSION_SECRET "
            "with at least 32 characters, or an initialized per-user local key. "
            "Preserve the original key for restores."
        )
    # Domain separation: this encryption key is not the cookie/token signing key.
    return HKDF(algorithm=hashes.SHA256(), length=32,
                salt=b"Archive X protected storage v1",
                info=b"archive-x/aes-256-gcm/files").derive(secret_bytes)


def seal(data: bytes, purpose: str) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(encryption_key()).encrypt(nonce, data, purpose.encode())


def unseal(data: bytes, purpose: str) -> bytes:
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 28:
        raise VaultError("Invalid protected-file format; no data was overwritten.")
    offset = len(MAGIC)
    try:
        return AESGCM(encryption_key()).decrypt(
            data[offset:offset + 12], data[offset + 12:], purpose.encode())
    except InvalidTag:
        raise VaultError(
            "Protected data could not be authenticated. Restore the original encryption "
            "secret or an intact backup; no data was overwritten."
        ) from None


def atomic_write(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows Defender, search indexing and backup tools briefly open
        # freshly written files without FILE_SHARE_DELETE, which makes
        # os.replace fail with "Access is denied"/"being used by another
        # process". Those locks clear within milliseconds; retry briefly.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (2 ** attempt))
        if os.name == "posix":
            os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_encrypted(path: Path, data: bytes, purpose: str) -> None:
    with LOCK:
        ciphertext = seal(data, purpose)
        # Validate before replacing existing data. Temporary files contain only ciphertext.
        if unseal(ciphertext, purpose) != data:
            raise VaultError("Encrypted storage verification failed.")
        atomic_write(Path(path), ciphertext)


def read_json(path: Path, purpose: str, default=None):
    with LOCK:
        encryption_key()  # Fail closed even on first-run writes.
        path = Path(path)
        if not path.exists():
            return {} if default is None else default
        raw = path.read_bytes()
        encrypted = raw.startswith(MAGIC)
        clear = unseal(raw, purpose) if encrypted else raw
        try:
            result = json.loads(clear)
            if not isinstance(result, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise VaultError("Protected JSON data is invalid; no data was overwritten.") from None
        if not encrypted:
            write_encrypted(path, clear, purpose)
        return result


def write_json(path: Path, value: dict, purpose: str) -> None:
    write_encrypted(path, json.dumps(value, ensure_ascii=False).encode("utf-8"), purpose)


def protected_purpose(relative_path: str) -> str | None:
    """Identify opaque protected files inside backups without exposing values."""
    path = Path(relative_path)
    if "auth" in path.parts and path.name in (
        "credentials.json", "credentials.json.tmp", "credentials.json.latest"
    ):
        return "archive-x-credentials-v1"
    if "auth" in path.parts and path.name.endswith(".session"):
        return "telegram-sqlite-session-v1"
    if "auth" in path.parts and path.name in (
        "provider_keys.json", "provider_keys.json.tmp", "provider_keys.json.latest"
    ):
        return "archive-x-provider-keys-v1"
    if path.name in ("telegram_uploader_config.json",
                     "telegram_uploader_config.json.latest",
                     "telegram_uploader_config.json.tmp"):
        return "archive-x-media-manager-v1"
    return None
