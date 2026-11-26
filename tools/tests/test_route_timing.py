"""Each route is timed by its pattern up to its first response byte."""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from telegram_archive.core import route_timing


def _app():
    app = FastAPI()

    @app.get("/thing/{thing_id}")
    def thing(thing_id: int):
        time.sleep(0.05)
        return {"id": thing_id}

    @app.get("/stream")
    def stream():
        def gen():
            yield b"a"
            time.sleep(0.3)
            yield b"b"
        return StreamingResponse(gen())

    app.add_middleware(route_timing.RouteTimingMiddleware)
    return TestClient(app)


def test_routes_are_counted_by_pattern_not_by_address():
    route_timing.reset()
    c = _app()
    for i in range(3):
        c.get(f"/thing/{i}")
    rows = {r["route"]: r for r in route_timing.summary()}
    assert list(rows) == ["GET /thing/{thing_id}"]
    assert rows["GET /thing/{thing_id}"]["calls"] == 3 and rows["GET /thing/{thing_id}"]["average_ms"] >= 40


def test_a_streamed_answer_is_timed_to_its_first_byte_not_its_end():
    route_timing.reset()
    _app().get("/stream")
    row = route_timing.summary()[0]
    assert row["route"] == "GET /stream" and row["longest_ms"] < 250
