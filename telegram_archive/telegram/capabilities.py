"""Account capability snapshot for one authenticated Telegram session.

The snapshot is intentionally small and non-sensitive.  It is refreshed when
Telegram connects/logs in and is shared by upload safety and playback so the
application does not repeatedly call get_me() just to rediscover the account
 tier.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

# Telegram's documented file-size limits used by the existing upload safety
# layer.  These are account capability facts; the actual safety cap is kept in
# core.upload_limits so that policy remains in one place.
FREE_MAX_UPLOAD_BYTES = 2_000_000_000
PREMIUM_MAX_UPLOAD_BYTES = 4_000_000_000

# Application tuning profiles. These are deliberately application-level values,
# not claims about Telegram's server-side connection limits.
# Free-account playback is kept close to the previously verified smooth
# profile. These are application tuning values, not Telegram server limits.
FREE_PLAYBACK_MIN_FETCH = 2
FREE_PLAYBACK_INITIAL_FETCH = 4
FREE_PLAYBACK_MAX_FETCH = 8  # V29.4.121: Telegram Web K free budget is ~9 x 512 KiB
FREE_PLAYBACK_MIN_TRANSFER_SESSIONS = 4
FREE_PLAYBACK_INITIAL_TRANSFER_SESSIONS = 4
FREE_PLAYBACK_MAX_TRANSFER_SESSIONS = 6

PREMIUM_PLAYBACK_MIN_FETCH = 4
PREMIUM_PLAYBACK_INITIAL_FETCH = 8
PREMIUM_PLAYBACK_MAX_FETCH = 16
PREMIUM_PLAYBACK_MIN_TRANSFER_SESSIONS = 4
PREMIUM_PLAYBACK_INITIAL_TRANSFER_SESSIONS = 8
PREMIUM_PLAYBACK_MAX_TRANSFER_SESSIONS = 16


def account_label(premium: bool) -> str:
    return "Premium" if premium else "Free"


class AccountLabelMixin:
    """Adds a readable account label to limit objects."""
    @property
    def account_label(self) -> str:
        return account_label(bool(getattr(self, "premium", False)))


@dataclass(frozen=True)
class TelegramAccountProfile(AccountLabelMixin):
    """Ephemeral capability snapshot for the currently connected account."""

    premium: bool
    checked_at: str
    source: str = "telegram.get_me"


    @property
    def max_upload_bytes(self) -> int:
        return PREMIUM_MAX_UPLOAD_BYTES if self.premium else FREE_MAX_UPLOAD_BYTES

    @property
    def playback_min_fetch(self) -> int:
        return PREMIUM_PLAYBACK_MIN_FETCH if self.premium else FREE_PLAYBACK_MIN_FETCH

    @property
    def playback_initial_fetch(self) -> int:
        return PREMIUM_PLAYBACK_INITIAL_FETCH if self.premium else FREE_PLAYBACK_INITIAL_FETCH

    @property
    def playback_max_fetch(self) -> int:
        return PREMIUM_PLAYBACK_MAX_FETCH if self.premium else FREE_PLAYBACK_MAX_FETCH

    @property
    def playback_min_transfer_sessions(self) -> int:
        return PREMIUM_PLAYBACK_MIN_TRANSFER_SESSIONS if self.premium else FREE_PLAYBACK_MIN_TRANSFER_SESSIONS

    @property
    def playback_initial_transfer_sessions(self) -> int:
        return PREMIUM_PLAYBACK_INITIAL_TRANSFER_SESSIONS if self.premium else FREE_PLAYBACK_INITIAL_TRANSFER_SESSIONS

    @property
    def playback_max_transfer_sessions(self) -> int:
        return PREMIUM_PLAYBACK_MAX_TRANSFER_SESSIONS if self.premium else FREE_PLAYBACK_MAX_TRANSFER_SESSIONS

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update({
            "account": self.account_label,
            "max_upload_bytes": self.max_upload_bytes,
            "playback_min_fetch": self.playback_min_fetch,
            "playback_initial_fetch": self.playback_initial_fetch,
            "playback_max_fetch": self.playback_max_fetch,
            "playback_min_transfer_sessions": self.playback_min_transfer_sessions,
            "playback_initial_transfer_sessions": self.playback_initial_transfer_sessions,
            "playback_max_transfer_sessions": self.playback_max_transfer_sessions,
            "profile": "premium" if self.premium else "free",
        })
        return data

    @classmethod
    def from_user(cls, user: Any, *, source: str = "telegram.get_me") -> "TelegramAccountProfile":
        premium = bool(getattr(user, "premium", False))
        return cls(
            premium=premium,
            checked_at=datetime.now(timezone.utc).isoformat(),
            source=str(source or "telegram.get_me"),
        )
