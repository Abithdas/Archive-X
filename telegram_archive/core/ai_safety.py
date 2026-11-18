"""What the local assistant may see, and what it may do only with your say-so.

Two rules, kept in one place so every path through the assistant uses the same ones:

1. **No secrets.** Keys, tokens, passwords, cookies and similar are removed from anything the model reads (the Archive X snapshot and every
   tool result) before it leaves this module. The assistant also has no tool that can read a secret setting.
2. **Actions need permission.** Reading is automatic. Anything that changes something is a *proposal* that waits for you: reversible
   actions need one Allow, destructive ones always need an explicit confirmation of the exact items (typed for more than one), and a
   few things are never possible. A proposal expires, is used once, and every step is written to the log.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable

HIDDEN = "[hidden]"

# ---------------------------------------------------------------- secrets -------------------------------------------------------------------------
_SECRET_NAME = re.compile(
    r"(api[_\s-]?(key|hash)|(?<![A-Za-z])api[_\s-]?id|token|secret|(?<![A-Za-z])pass(word|wd|phrase)?(?![A-Za-z])|cookie|session|authori[sz]ation|"
    r"credential|private[_\s-]?key|signing[_\s-]?(key|secret)|(?<![A-Za-z])(salt|otp|pin|2fa)(?![A-Za-z])|string[_\s-]?session|bearer)", re.I)
# Names that look secret-ish but are a count, a size or a rate: facts about the library, not secrets ("token_count", "max_tokens", "sessions_total").
_NOT_SECRET = re.compile(r"(?:^|_)(?:count|total|num|number|size|limit|max|min|per_second|used|ms|seconds|rate)s?$|^(?:max|min|num|total)_", re.I)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + HIDDEN),
    (re.compile(r"(?i)\b(api[_-]?key|api[_-]?hash|token|secret|passw(?:or)?d|passwd|passphrase|authorization|cookie|session[_-]?(?:id|string)?|private[_-]?key)\b(\s*[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}\]]+"),
     r"\1\2" + HIDDEN),
    (re.compile(r"(?<=//)[^/\s:@]+:[^/\s@]+(?=@)"), HIDDEN),                       # user:password@host
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), HIDDEN),                        # a Telegram bot token
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), HIDDEN),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), HIDDEN),   # a JWT
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), HIDDEN),                                 # a long hex secret or digest
    # a long unbroken run of letters of both cases and digits (a key). Words joined by - or _ (file and scene names) are left alone.
    (re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{40,}\b"), HIDDEN),
)


def is_secret_name(name: object) -> bool:
    """Whether a field or setting name holds a secret by its name (api_key, password, session_cookie, ...)."""
    text = str(name or "")
    return bool(text) and not _NOT_SECRET.search(text) and bool(_SECRET_NAME.search(text))


def redact_text(text: object) -> str:
    """The text with anything that looks like a secret replaced by [hidden]."""
    out = str(text if text is not None else "")
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact(value: Any, _depth: int = 0) -> Any:
    """A copy of `value` (dicts, lists, strings) safe to show the model: secret-named fields are hidden whole, other text is scrubbed."""
    if _depth > 12:
        return HIDDEN
    if isinstance(value, dict):
        return {k: (HIDDEN if is_secret_name(k) and v not in (None, "", 0, False) else redact(v, _depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


# ---------------------------------------------------------------- permissions ---------------------------------------------------------------------
class Tier(IntEnum):
    READ = 0           # looks at things: automatic
    REVERSIBLE = 1     # changes something that can be undone: one Allow
    DESTRUCTIVE = 2    # deletes, moves, overwrites, sends: explicit confirmation of the exact items
    FORBIDDEN = 3      # never, whatever anyone clicks


# What each action is allowed to be. An action that is not listed is forbidden: new powers have to be added here on purpose.
ACTION_TIERS: dict[str, Tier] = {
    "start_scan": Tier.REVERSIBLE,
    "queue_job": Tier.REVERSIBLE,
    "rerun_metadata_match": Tier.REVERSIBLE,
    "change_setting": Tier.REVERSIBLE,
    "apply_suggestion": Tier.REVERSIBLE,
    "apply_code_patch": Tier.DESTRUCTIVE,
    "delete_media": Tier.DESTRUCTIVE,
    "delete_performer": Tier.DESTRUCTIVE,
    "move_media": Tier.DESTRUCTIVE,
    "send_to_telegram": Tier.DESTRUCTIVE,
    "bulk_edit": Tier.DESTRUCTIVE,
    "read_secret": Tier.FORBIDDEN,
    "run_shell": Tier.FORBIDDEN,
    "run_sql": Tier.FORBIDDEN,
    "fetch_url": Tier.FORBIDDEN,
}
# Settings the assistant may never change, even with permission: the ones that hold a secret or open the app up.
PROTECTED_SETTINGS = re.compile(r"(key|token|secret|pass|cookie|session|credential|web_username|weblogin|auth|tls|cert|endpoint)", re.I)

PENDING, APPROVED, DECLINED, EXPIRED, DONE, FAILED = "PENDING", "APPROVED", "DECLINED", "EXPIRED", "DONE", "FAILED"
TTL_SECONDS = 10 * 60
MAX_ITEMS_SHOWN = 50


class NotAllowed(Exception):
    """Raised for anything the broker refuses; the message is plain words for the person."""


@dataclass
class Proposal:
    id: str
    action: str
    tier: Tier
    summary: str
    items: list[str]
    args: dict[str, Any]
    created: float
    expires: float
    status: str = PENDING
    needs_typed: str = ""         # the exact words that must be typed to confirm (more than one destructive item)
    result: Any = None
    history: list[tuple[float, str]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        """What the page shows: never the raw arguments (they may hold paths or ids the person need not see)."""
        return {"id": self.id, "action": self.action, "tier": self.tier.name, "summary": self.summary, "items": self.items[:MAX_ITEMS_SHOWN],
                "more": max(0, len(self.items) - MAX_ITEMS_SHOWN), "status": self.status, "needs_typed": self.needs_typed,
                "expires_in": max(0, int(self.expires - time.time()))}


class PermissionBroker:
    """Holds the assistant's proposals until a person decides. Nothing runs without an APPROVED, unexpired, unused proposal."""

    def __init__(self, log: Callable[..., Any] | None = None, clock: Callable[[], float] = time.time):
        self._log = log or (lambda *a, **k: None)
        self._clock = clock
        self._items: dict[str, Proposal] = {}
        self._executors: dict[str, Callable[[dict[str, Any]], Any]] = {}

    # ---- setup
    def register(self, action: str, executor: Callable[[dict[str, Any]], Any]) -> None:
        if ACTION_TIERS.get(action, Tier.FORBIDDEN) == Tier.FORBIDDEN:
            raise NotAllowed(f"'{action}' can never be given to the assistant")
        self._executors[action] = executor

    def _audit(self, p: Proposal, step: str, extra: str = "") -> None:
        p.history.append((self._clock(), step))
        try:
            self._log(f"AI action {step}: {p.action} ({p.tier.name}) {p.summary[:160]}{extra}", "INFO" if step != "refused" else "WARN",
                      event=f"ai.action.{step}", details={"id": p.id, "action": p.action, "tier": p.tier.name, "items": len(p.items)})
        except Exception:
            pass     # the audit line must never be what stops a refusal

    def _expire(self) -> None:
        now = self._clock()
        for p in self._items.values():
            if p.status in (PENDING, APPROVED) and now >= p.expires:
                p.status = EXPIRED
                self._audit(p, "expired")

    # ---- the assistant proposes
    def propose(self, action: str, summary: str, *, items: list[str] | None = None, args: dict[str, Any] | None = None) -> Proposal:
        tier = ACTION_TIERS.get(str(action), Tier.FORBIDDEN)
        shown = [str(i)[:200] for i in (items or [])]
        probe = Proposal(secrets.token_hex(8), str(action), tier, redact_text(summary)[:300], shown, dict(args or {}), self._clock(), self._clock() + TTL_SECONDS)
        if tier == Tier.FORBIDDEN:
            self._audit(probe, "refused")
            raise NotAllowed("The assistant is not allowed to do that.")
        if action == "change_setting" and PROTECTED_SETTINGS.search(str((args or {}).get("name", ""))):
            self._audit(probe, "refused", " (protected setting)")
            raise NotAllowed("That setting holds a secret or controls access; the assistant cannot change it.")
        if tier == Tier.DESTRUCTIVE and not shown:
            raise NotAllowed("A destructive action has to say exactly which items it touches.")
        if tier == Tier.DESTRUCTIVE and len(shown) > 1:
            probe.needs_typed = f"{action.split('_')[0].upper()} {len(shown)}"
        self._expire()
        self._items[probe.id] = probe
        self._audit(probe, "proposed")
        return probe

    def pending(self) -> list[Proposal]:
        self._expire()
        return [p for p in self._items.values() if p.status == PENDING]

    def get(self, proposal_id: str) -> Proposal | None:
        self._expire()
        return self._items.get(str(proposal_id))

    # ---- the person decides
    def decide(self, proposal_id: str, approve: bool, *, typed: str = "") -> Proposal:
        p = self.get(proposal_id)
        if p is None:
            raise NotAllowed("That request is not there any more.")
        if p.status != PENDING:
            raise NotAllowed(f"That request is already {p.status.lower()}.")
        if not approve:
            p.status = DECLINED
            self._audit(p, "declined")
            return p
        if p.needs_typed and typed.strip().upper() != p.needs_typed:
            raise NotAllowed(f"To confirm, type exactly: {p.needs_typed}")
        p.status = APPROVED
        self._audit(p, "approved")
        return p

    # ---- running what was approved
    def run(self, proposal_id: str) -> Proposal:
        """Run an approved proposal once. The result (or the failure) is kept on it."""
        p = self.get(proposal_id)
        if p is None:
            raise NotAllowed("That request is not there any more.")
        if p.status != APPROVED:
            raise NotAllowed("That request has not been approved." if p.status == PENDING else f"That request is {p.status.lower()}.")
        executor = self._executors.get(p.action)
        if executor is None:
            raise NotAllowed("Nothing is set up to carry that out.")
        p.status = DONE            # marked first: it can never run twice, even if the executor raises or is slow
        try:
            p.result = redact(executor(dict(p.args)))
            self._audit(p, "done")
        except Exception as exc:
            p.status = FAILED
            p.result = {"error": redact_text(f"{type(exc).__name__}: {exc}")[:300]}
            self._audit(p, "failed", f" {p.result['error']}")
        return p
