"""Shared transient-error classification and bounded retry delays."""
from __future__ import annotations

from .swallowed import note as _swallowed_note
import random
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryDecision:
    """Classification of one error: whether and how long to wait before retrying."""
    category: str
    retry: bool
    delay: float = 0.0
    refresh_message: bool = False
    reason: str = ""


class RetryPolicy:
    """Classifies Telegram errors into retry decisions."""
    def __init__(self, *, max_attempts: int = 4, base_delay: float = 0.35, max_delay: float = 8.0):
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay = max(0.05, float(base_delay))
        self.max_delay = max(self.base_delay, float(max_delay))

    @staticmethod
    def flood_seconds(exc) -> int:
        val = getattr(exc, "seconds", None)
        if val is not None:
            try:
                return max(0, int(val))
            except Exception:
                _swallowed_note("retry_policy.RetryPolicy.flood_seconds")
                pass
        text = f"{type(exc).__name__} {exc}".upper()
        m = re.search(r"FLOOD(?:_PREMIUM)?_WAIT[_ ](\d+)", text)
        return int(m.group(1)) if m else 0

    def classify(self, exc, attempt: int) -> RetryDecision:
        text = f"{type(exc).__name__} {exc}".upper()
        if "FILE_REFERENCE" in text or "FILEREFERENCE" in text:
            return RetryDecision("file_reference", attempt < self.max_attempts, 0.1, True, "refresh telegram message")
        if "FLOOD" in text:
            wait = self.flood_seconds(exc)
            return RetryDecision("flood_wait", attempt < self.max_attempts and wait <= 30,
                                 float(min(max(1, wait), 30)), False, f"telegram requested {wait}s")
        if any(x in text for x in ("TIMEOUT", "TIMED OUT", "CONNECTIONRESET", "SERVERERROR", "TEMPORARY", "NETWORK")):
            delay = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
            delay += random.random() * min(0.25, delay * 0.25)
            return RetryDecision("transient", attempt < self.max_attempts, delay, False, "transient network/remote error")
        if "FILE_MIGRATE" in text or "FILEMIGRATE" in text:
            return RetryDecision("migrate", attempt < self.max_attempts, 0.1, False, "retry through another connection")
        return RetryDecision("permanent", False, 0.0, False, "non-retryable error")
