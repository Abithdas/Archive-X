"""Telegram account-aware upload safety limits.

Telegram's current client limits are 2 GB for non-Premium accounts and 4 GB
for Premium accounts.  The application deliberately keeps a 100 MB safety
margin below the account limit so an upload near the boundary is skipped
before any large transfer starts.
"""
from __future__ import annotations

from .swallowed import note as _swallowed_note
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import time

from ..telegram.capabilities import (
    TelegramAccountProfile,
    FREE_MAX_UPLOAD_BYTES,
    PREMIUM_MAX_UPLOAD_BYTES,
    AccountLabelMixin,
)

FREE_MAX_BYTES = FREE_MAX_UPLOAD_BYTES
PREMIUM_MAX_BYTES = PREMIUM_MAX_UPLOAD_BYTES
SAFETY_MARGIN_BYTES = 100_000_000
OVERSIZED_FILE_POLICIES = frozenset({"skip", "optimize", "split"})

def normalize_oversized_file_policy(value: Any = None, *, legacy_split_upload: bool = False, default: str = "optimize") -> str:
    """Normalize the operator's action for files above Telegram's safe limit.

    Older saved configurations exposed only ``split_upload``. When no explicit
    policy is present, preserve that setting; otherwise default to the existing
    size-aware optimization behavior used by Upload Local Files.
    """
    raw = str(value or "").strip().casefold()
    if not raw:
        raw = "split" if legacy_split_upload else str(default or "optimize").strip().casefold()
    if raw not in OVERSIZED_FILE_POLICIES:
        raise ValueError(
            "Oversized file handling must be one of: skip, optimize, split"
        )
    return raw

@dataclass(frozen=True)
class UploadPolicy(AccountLabelMixin):
    """Telegram upload size limits for the current account."""
    premium: bool
    telegram_limit_bytes: int
    safe_limit_bytes: int


    @property
    def safe_limit_gb(self) -> float:
        return self.safe_limit_bytes / 1_000_000_000.0

    @property
    def telegram_limit_gb(self) -> float:
        return self.telegram_limit_bytes / 1_000_000_000.0

    def describe(self) -> str:
        return (
            f"{self.account_label} account · Telegram limit {self.telegram_limit_gb:.2f} GB · "
            f"app safe limit {self.safe_limit_gb:.2f} GB (100 MB safety margin)"
        )


class UploadLimitExceeded(RuntimeError):
    """Raised before a local file is uploaded when it exceeds the safe cap."""

    def __init__(self, path: str | Path, size_bytes: int, policy: UploadPolicy):
        self.path = Path(path)
        self.size_bytes = int(size_bytes)
        self.policy = policy
        super().__init__(
            f"Upload skipped: {self.path.name} is {human_size(self.size_bytes)}, "
            f"above the {human_size(policy.safe_limit_bytes)} application upload cap "
            f"for a {policy.account_label} account ({human_size(policy.telegram_limit_bytes)} Telegram limit minus 100 MB)."
        )


def human_size(size_bytes: int) -> str:
    n = float(max(0, int(size_bytes)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000.0 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000.0
    return f"{n:.1f} TB"


async def get_upload_policy(client: Any) -> UploadPolicy:
    """Return and cache the account-specific application upload cap.

    The premium flag is resolved from the connected Telegram account once per
    client instance. If account lookup fails, the conservative Free policy is
    used rather than risking an over-limit upload.
    """
    cached = getattr(client, "_telegram_archive_upload_policy", None)
    cached_at = float(getattr(client, "_telegram_archive_upload_policy_at", 0.0) or 0.0)
    # Keep the cache interval local to this function so the upload path cannot
    # fail because of a missing module-level cache constant in a stale install.
    cache_seconds = 300.0
    if isinstance(cached, UploadPolicy) and (time.monotonic() - cached_at) < cache_seconds:
        return cached

    profile = getattr(client, "_telegram_archive_account_profile", None)
    if isinstance(profile, TelegramAccountProfile):
        premium = bool(profile.premium)
    else:
        premium = False
        try:
            me = await client.get_me()
            premium = bool(getattr(me, "premium", False))
        except Exception:
            premium = False

    telegram_limit = profile.max_upload_bytes if isinstance(profile, TelegramAccountProfile) else (PREMIUM_MAX_BYTES if premium else FREE_MAX_BYTES)
    safe_limit = max(0, telegram_limit - SAFETY_MARGIN_BYTES)
    policy = UploadPolicy(
        premium=premium,
        telegram_limit_bytes=telegram_limit,
        safe_limit_bytes=safe_limit,
    )
    try:
        setattr(client, "_telegram_archive_upload_policy", policy)
        setattr(client, "_telegram_archive_upload_policy_at", time.monotonic())
    except Exception:
        _swallowed_note("upload_limits.get_upload_policy")
        pass
    return policy


async def ensure_upload_allowed(client: Any, path: str | Path) -> UploadPolicy:
    policy = await get_upload_policy(client)
    p = Path(path)
    try:
        size = int(p.stat().st_size)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Upload source does not exist: {p}") from exc
    if size > policy.safe_limit_bytes:
        raise UploadLimitExceeded(p, size, policy)
    return policy


def upload_policy_dict(policy: UploadPolicy) -> dict[str, Any]:
    return {
        "premium": policy.premium,
        "account": policy.account_label,
        "telegram_limit_bytes": policy.telegram_limit_bytes,
        "safe_limit_bytes": policy.safe_limit_bytes,
        "safety_margin_bytes": SAFETY_MARGIN_BYTES,
        "telegram_limit_gb": policy.telegram_limit_gb,
        "safe_limit_gb": policy.safe_limit_gb,
        "label": policy.describe(),
    }
