"""Which content mode (SFW or NSFW) a background job belongs to, so each mode shows only its own tasks.

A job belongs to the mode of the channels (or local folders) it works on: its source and destination columns, and the channel ids in its detail.
A job that names an NSFW channel (or a channel the library does not know, which counts as NSFW like everywhere else) is an NSFW job; one that
names only SFW channels is an SFW job; one that names no channel at all (a backup, a cleanup) belongs to both and is always shown.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

_ID = re.compile(r"-?\d{5,}")
_DETAIL_KEYS = ("channel_ids", "channels", "source_channels", "source_channel", "source_channel_ids", "destination_channel", "destination_channel_id",
                "channel_id", "channel", "folder_id", "folder_ids", "target_channel")


def _ids_in(value: Any, out: set[str], depth: int = 0) -> None:
    if value is None or depth > 3:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) >= 10000:
            out.add(str(value))
    elif isinstance(value, str):
        out.update(_ID.findall(value))
    elif isinstance(value, dict):
        for key in ("id", "telegram_id", "channel_id"):
            if key in value:
                _ids_in(value[key], out, depth + 1)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _ids_in(item, out, depth + 1)


def channel_ids_of(job: dict) -> set[str]:
    """The channel and folder ids a job works on."""
    ids: set[str] = set()
    _ids_in(job.get("source_channel"), ids)
    _ids_in(job.get("destination_channel"), ids)
    try:
        detail = json.loads(job.get("detail") or "{}")
    except (TypeError, ValueError):
        detail = {}
    if isinstance(detail, dict):
        for key in _DETAIL_KEYS:
            _ids_in(detail.get(key), ids)
    return ids


def modes_of(db, jobs: Iterable[dict]) -> dict[str, str]:
    """job id -> "sfw", "nsfw" or "" (names no channel: shown in both modes)."""
    jobs = list(jobs)
    wanted = {str(j.get("id")): channel_ids_of(j) for j in jobs}
    every = set().union(*wanted.values()) if wanted else set()
    ratings: dict[str, str] = {}
    if every:
        marks = ",".join("?" for _ in every)
        with db.reading() as conn:     # main.: the real table, not the mode's filtered view of it
            for row in conn.execute(f"SELECT CAST(telegram_id AS TEXT) AS id, COALESCE(content_rating,'nsfw') AS r FROM main.channels WHERE CAST(telegram_id AS TEXT) IN ({marks})", sorted(every)):
                ratings[str(row["id"])] = str(row["r"]).lower()
    out: dict[str, str] = {}
    for job_id, ids in wanted.items():
        if not ids:
            out[job_id] = ""
            continue
        marks_of = {ratings.get(i, "nsfw") for i in ids}      # a channel the library does not know counts as NSFW
        out[job_id] = "nsfw" if "nsfw" in marks_of else "sfw"
    return out


def split_by_mode(db, jobs: list[dict], mode: str) -> tuple[list[dict], list[dict]]:
    """(the jobs of this mode, and the jobs that are from the other mode). Jobs that belong to both are in the first."""
    modes = modes_of(db, jobs)
    mine, other = [], []
    for j in jobs:
        m = modes.get(str(j.get("id")), "")
        (other if m and m != mode else mine).append(j)
    return mine, other


def other_summary(other: list[dict]) -> dict:
    """What the other mode has going, without saying what: how many tasks, and how many of them are running now."""
    running = sum(1 for j in other if str(j.get("status") or "").upper() in {"RUNNING", "PAUSING", "RECOVERING"})
    return {"count": len(other), "running": running}
