"""pipe._run_engine: how one stage engine is run: Telegram rate limits, other errors, a cancel."""
import asyncio
from types import SimpleNamespace

import pytest

import metadata_scan_harness as h


class Flood(Exception):
    def __init__(self, seconds):
        super().__init__(f"A wait of {seconds} seconds is required")
        self.seconds = seconds


def wire(monkeypatch, outcomes):
    """Replace the real engine call with a script of outcomes (an exception is raised, anything else returns)."""
    calls = []

    async def fake_run_operation(subctx, operation, detail):
        calls.append(operation)
        out = outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out

    monkeypatch.setattr(h.pipe.metadata_assets, "_run_operation", fake_run_operation)
    monkeypatch.setattr(h.pipe, "_subctx", lambda ctx, job_type, detail: SimpleNamespace(report=None, scan_window_enforced=False))
    monkeypatch.setattr(h.pipe, "_scan_window_wait", h._no_wait)
    waits = h.fast_clock(monkeypatch, h.pipe)
    return calls, waits


def ctx_for(db):
    return h.make_ctx(db, {"channel_ids": ["-1"], "stages": ["metadata"]})


def test_a_clean_run_calls_the_engine_once(monkeypatch):
    calls, _ = wire(monkeypatch, [None])
    h.run(h.pipe._run_engine(ctx_for(h.fresh_db()), "metadata", {}))
    assert calls == ["metadata"]


def test_a_rate_limit_is_waited_out_and_the_engine_tried_again(monkeypatch):
    calls, waits = wire(monkeypatch, [Flood(30), Flood(30), None])
    h.run(h.pipe._run_engine(ctx_for(h.fresh_db()), "metadata", {}))
    assert calls == ["metadata"] * 3 and waits


def test_a_rate_limit_that_never_ends_gives_up_after_five_waits_with_the_real_error(monkeypatch):
    calls, _ = wire(monkeypatch, [Flood(10)] * 20)
    with pytest.raises(Flood):
        h.run(h.pipe._run_engine(ctx_for(h.fresh_db()), "thumbnails", {}))
    assert len(calls) == 6


def test_a_rate_limit_written_only_in_the_message_is_recognised(monkeypatch):
    calls, _ = wire(monkeypatch, [RuntimeError("FLOOD_WAIT_12"), RuntimeError("FloodWaitError: wait 9"), None])
    h.run(h.pipe._run_engine(ctx_for(h.fresh_db()), "entity_data", {}))
    assert len(calls) == 3


def test_any_other_error_is_raised_straight_away(monkeypatch):
    calls, _ = wire(monkeypatch, [ValueError("bad provider answer"), None])
    with pytest.raises(ValueError):
        h.run(h.pipe._run_engine(ctx_for(h.fresh_db()), "metadata", {}))
    assert calls == ["metadata"]


def test_a_cancel_passes_through_without_a_retry(monkeypatch):
    calls, _ = wire(monkeypatch, [h.JobCancelled(), None])
    with pytest.raises(h.JobCancelled):
        h.run(h.pipe._run_engine(ctx_for(h.fresh_db()), "metadata", {}))
    assert calls == ["metadata"]


def test_a_cancel_during_a_rate_limit_wait_ends_the_wait(monkeypatch):
    calls, _ = wire(monkeypatch, [Flood(3000), None])
    ctx = h.make_ctx(h.fresh_db(), {}, cancel_after=0)
    with pytest.raises(h.JobCancelled):
        h.run(h.pipe._run_engine(ctx, "metadata", {}))
    assert calls == ["metadata"]


def test_an_unknown_operation_is_a_clear_error(monkeypatch):
    wire(monkeypatch, [None])
    with pytest.raises(KeyError):
        h.run(h.pipe._run_engine(ctx_for(h.fresh_db()), "nonsense", {}))
