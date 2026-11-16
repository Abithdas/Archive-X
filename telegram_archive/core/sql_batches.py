"""SQLite refuses a statement with more than 32,766 `?` placeholders ("too many SQL variables"), and older builds more than 999. A channel
with tens of thousands of videos therefore cannot pass all its ids to one `IN (...)`. Walk the ids in batches instead."""
from __future__ import annotations

from typing import Iterable, Iterator, TypeVar

T = TypeVar("T")
BATCH = 500


def batches(items: Iterable[T], size: int = BATCH) -> Iterator[list[T]]:
    pool = list(items)
    for start in range(0, len(pool), max(1, int(size))):
        yield pool[start:start + max(1, int(size))]


def marks(n: int) -> str:
    """"?,?,?" for n placeholders."""
    return ",".join("?" for _ in range(n))
