"""Canonical Telegram peer IDs shared by scanners, playback caches and delivery paths."""
from __future__ import annotations


from .swallowed import note as _swallowed_note
def canonical_peer_id(peer_or_id) -> str:
    """Return Telethon's marked peer ID for an entity, or preserve a supplied ID.

    Telegram entity ``.id`` values are positive raw IDs for channels and groups,
    while Archive X's scanned-media catalogue uses Telethon marked peer IDs
    (for example ``-100...`` for channels). Never reinterpret a bare number
    because its peer type is unknowable; pass the resolved entity whenever one
    is available.
    """
    if peer_or_id is None:
        return ""
    if isinstance(peer_or_id, (str, int)):
        return str(peer_or_id).strip()
    try:
        from telethon.utils import get_peer_id
        return str(int(get_peer_id(peer_or_id)))
    except Exception:
        _swallowed_note("telegram_identity.canonical_peer_id")
        pass
    # Defensive fallback for lightweight adapters / test doubles when Telethon
    # cannot infer the entity class. Production Telethon entities use get_peer_id.
    value = getattr(peer_or_id, "id", None)
    if value is None:
        value = getattr(peer_or_id, "channel_id", None)
    if value is None:
        value = getattr(peer_or_id, "chat_id", None)
    if value is None:
        value = getattr(peer_or_id, "user_id", None)
    if value is None:
        return str(peer_or_id).strip()
    name = type(peer_or_id).__name__.lower()
    number = int(value)
    if "channel" in name:
        return f"-100{abs(number)}"
    if "chat" in name and number > 0:
        return str(-number)
    return str(number)
