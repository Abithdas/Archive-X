"""The Collection's background worker: one thread per library that does a round every ten minutes while automatic downloads are on, or at once when asked.

It holds no state of its own (everything is in the database), so a restart picks up where it was: a download that was interrupted resumes from its partial file.
"""
from __future__ import annotations

import threading

from ..metadata import screen_ids as si
from ..metadata import streaming_server as ss
from ..metadata import stremio_runtime as rt
from ..metadata import stream_search
from . import collection as col

INTERVAL = 600.0


def meta_of_factory(db):
    """The catalogue entry of a title (Cinemeta: episodes with their air dates, a film's release date)."""
    def meta_of(kind: str, imdb: str) -> dict:
        from . import screen_identify as sid
        screen = sid.make_screen(db)
        return screen._cinemeta_meta("tv" if kind == "series" else "movie", imdb) or {}
    return meta_of


def finder_factory(db):
    def finder(kind: str, imdb: str, season, episode) -> list[dict]:
        return stream_search.torrents_for(db, kind, imdb, season, episode)
    return finder


def ensure_server_factory(db):
    """() -> (ok, why): the streaming server answering, started when it can be (installed, or set up by Archive X); otherwise the plain reason it cannot."""
    def ensure() -> tuple[bool, str]:
        base = ss.configured(db)
        if ss.reachable(base):
            return True, ""
        out = ss.start(base)
        if out.get("up"):
            return True, ""
        if not out.get("found") and not rt.ready():
            return False, "The streaming server is not set up. Open Settings, Addons and press Set it up."
        return False, out.get("reason") or "The streaming server is not running."
    return ensure


class Worker:
    def __init__(self, db, interval: float = INTERVAL):
        self.db, self.interval = db, interval
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread: threading.Thread | None = None
        self.running = False

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="collection", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)

    def kick(self) -> None:
        self.wake.set()

    def run_once(self) -> dict:
        col.ensure(self.db)
        self.running = True
        try:
            return col.tick(self.db, meta_of=meta_of_factory(self.db), finder=finder_factory(self.db), server_base=ss.configured(self.db),
                            ensure_server=ensure_server_factory(self.db), stop=self.stop_event)
        finally:
            self.running = False

    def _loop(self) -> None:
        self.wake.wait(45)                             # the app is busy opening: the first round waits (a download the person just chose does not)
        self.wake.clear()
        while not self.stop_event.is_set():
            try:
                if col.settings(self.db)["on"] or col.has_downloads(self.db):     # downloads chosen by hand run even with automatic downloads off
                    self.run_once()
            except Exception as exc:
                self.db.log(f"Collection worker error: {type(exc).__name__}: {exc}", "WARN")
            self.wake.wait(self.interval)
            self.wake.clear()


def worker_for(db) -> Worker:
    w = getattr(db, "_collection_worker", None)
    if w is None:
        w = Worker(db)
        db._collection_worker = w
    return w


def start_for(db) -> None:
    worker_for(db).start()


def stop_for(db) -> None:
    w = getattr(db, "_collection_worker", None)
    if w is not None:
        w.stop()
