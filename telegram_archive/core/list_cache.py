"""Remember the answer of a big list (performers, studios) until the data it is made from changes.

On a library of 175,000 videos these lists take a quarter to half a second, because every row is counted through the content-mode filter. The
same list is asked for again and again (every visit to the page, every refresh), and it only changes when something does. The data-change counters
(core/change_tracking.py: videos, entities, channels, settings) say when that happens, and they are read BEFORE the list is made, so a change
during the work makes the stored answer stale at once and the next request makes it again. A few things a list shows are not behind a counter
(cached-picture flags, movie and series counts), so an answer also expires after `TTL_SECONDS`.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict

TTL_SECONDS = 30.0
MAX_ENTRIES = 48
_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_LOCK = threading.Lock()


def _versions(db) -> tuple:
    try:
        with db.reading() as rdb:
            return tuple((r[0], r[1]) for r in rdb.execute("SELECT scope, version FROM data_versions ORDER BY scope").fetchall())
    except Exception:
        return ()


def cached(db, name: str, params, compute, ttl: float = TTL_SECONDS):
    """`compute()`'s answer for (name, params, content mode), reused while the counters are unchanged and it is younger than `ttl` seconds."""
    from . import content_mode as cm
    versions = _versions(db)
    if not versions:                       # counters unreadable: never serve a stored answer
        return compute()
    key = (name, tuple(sorted((str(k), str(v)) for k, v in dict(params).items())), cm.get_mode(db))
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == versions and now < hit[1]:
            _CACHE.move_to_end(key)
            return copy.deepcopy(hit[2])
    value = compute()
    with _LOCK:
        _CACHE[key] = (versions, now + ttl, copy.deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return value


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
