"""The assistant's streamed chat turn against a fake llama-server: status lines, a tool call, the answer word by word, saved history, and plain-words errors."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive import config
from telegram_archive.core.ai_assistant import ArchiveXAI
from telegram_archive.core.database import Database


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def delta(**d):
    return {"model": "fake-model", "choices": [{"index": 0, "delta": d}]}


class Fake(BaseHTTPRequestHandler):
    rounds: list = []
    seen: list = []

    def log_message(self, *a): pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append(payload)
        body = Fake.rounds[min(len(Fake.seen) - 1, len(Fake.rounds) - 1)].encode()
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


@pytest.fixture
def ai(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Fake.seen = []
    db = Database(tmp_path / "t.db")
    db.set_setting("ai_local_endpoint", f"http://127.0.0.1:{server.server_address[1]}/v1")
    yield ArchiveXAI(db)
    server.shutdown()


def test_a_plain_answer_streams_word_by_word_and_is_saved(ai):
    Fake.rounds = [sse(delta(role="assistant"), delta(content="Hello"), delta(content=" there"), delta(content="."))]
    events = list(ai.chat_events("hi", "context"))
    assert [e["t"] for e in events] == ["status", "status", "delta", "delta", "delta", "done"]
    assert "".join(e["text"] for e in events if e["t"] == "delta") == "Hello there."
    done = events[-1]
    assert done["text"] == "Hello there." and done["model"] == "fake-model" and done["tool_calls"] == 0
    assert Fake.seen[0]["stream"] is True and Fake.seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    saved = [(m["role"], m["content"]) for m in ai.db.ai_list_chat_messages(10, int(ai.db.ai_active_investigation()["id"]))]
    assert ("user", "hi") in saved and ("assistant", "Hello there.") in saved


def test_a_tool_call_is_reported_as_it_starts_and_ends_and_its_result_reaches_the_model(ai):
    call = {"index": 0, "id": "c1", "function": {"name": "get_library_stats", "arguments": ""}}
    Fake.rounds = [
        sse(delta(tool_calls=[call]), delta(tool_calls=[{"index": 0, "function": {"arguments": "{}"}}])),
        sse(delta(content="You have "), delta(content="0 videos.")),
    ]
    events = list(ai.chat_events("how many videos?", ""))
    tools = [(e["label"], e["state"]) for e in events if e["t"] == "tool"]
    assert tools == [("Reading library numbers", "start"), ("Reading library numbers", "done")]
    assert events[-1]["text"] == "You have 0 videos." and events[-1]["tool_calls"] == 1
    second = Fake.seen[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "get_library_stats" and second[-1]["role"] == "tool"   # the result went back to the model
    assert not any(e["t"] == "delta" and e["text"] == "" for e in events)


def test_a_tool_that_fails_is_marked_failed_and_the_turn_still_finishes(ai, monkeypatch):
    monkeypatch.setattr(ai.tools, "execute", lambda name, args: (_ for _ in ()).throw(RuntimeError("boom")))
    Fake.rounds = [sse(delta(tool_calls=[{"index": 0, "id": "c", "function": {"name": "get_jobs", "arguments": "{}"}}])), sse(delta(content="I could not read the jobs."))]
    events = list(ai.chat_events("jobs?", ""))
    assert [e["state"] for e in events if e["t"] == "tool"] == ["start", "error"]
    assert events[-1]["t"] == "done"


def test_an_assistant_that_is_not_set_up_says_so_in_plain_words(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_setting("ai_local_endpoint", "http://127.0.0.1:9/v1")           # nothing listens there
    db.set_setting("ai_local_runtime_path", str(tmp_path / "missing.exe"))
    events = list(ArchiveXAI(db).chat_events("hi", ""))
    assert events[-1]["t"] == "error" and "not set up yet" in events[-1]["detail"]
    assert list(ArchiveXAI(db).chat_events("   ", ""))[0] == {"t": "error", "detail": "Message is empty"}


def test_the_snapshot_is_trimmed_to_fit_the_models_window(ai):
    assert len(ai._bounded_context("x" * 100000)) == (8192 - 2600) * 3          # the default window
    ai.db.set_setting("ai_local_context", 2048)
    assert len(ai._bounded_context("x" * 100000)) == 3000                       # never smaller than a useful minimum
