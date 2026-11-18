"""What the assistant may see (no secrets) and may do (only with permission)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.ai_safety import (  # noqa: E402
    ACTION_TIERS, HIDDEN, NotAllowed, PermissionBroker, Tier, is_secret_name, redact, redact_text,
)


# Fake credentials in the shapes the redactor must catch. They are put together at run time so secret scanners do not mistake the test data for leaks.
SK = "sk" + "_live_" + "ABCDEF123456"
BOT = "123456789" + ":" + "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw9"
JWT = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"])


# ---------------------------------------------------------------- secrets -------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["api_key", "stashdb_api_key", "tpdb_api_token", "web_password", "session_cookie", "Authorization", "telegram_api_hash",
                                  "private_key", "otp", "user_pin", "string_session", "client_secret", "bearer"])
def test_secret_names_are_recognised(name):
    assert is_secret_name(name)


@pytest.mark.parametrize("name", ["footprint", "passed", "bypass_cache", "title", "token_count", "max_tokens", "sessions_total", "tokens_per_second", "spinner", "oshash", "job_type"])
def test_plain_names_are_not_mistaken_for_secrets(name):
    assert not is_secret_name(name)


def test_secret_fields_are_hidden_whole_and_the_rest_is_kept():
    out = redact({"title": "Scene", "stashdb_api_key": "abc123", "nested": {"web_password": "hunter2", "ok": 1}, "list": [{"token": "zzz"}, "fine"]})
    assert out == {"title": "Scene", "stashdb_api_key": HIDDEN, "nested": {"web_password": HIDDEN, "ok": 1}, "list": [{"token": HIDDEN}, "fine"]}


def test_an_empty_secret_is_not_made_to_look_set():
    assert redact({"api_key": "", "token": None})["api_key"] == ""


@pytest.mark.parametrize("text,gone", [
    ("Authorization: Bearer abcdefghijklmnop12345", "abcdefghijklmnop12345"),
    ("api_key=" + SK, SK),
    ('{"password": "hunter2hunter2"}', "hunter2hunter2"),
    ("connect https://user:p4ssw0rd@example.com/x", "p4ssw0rd"),
    ("bot " + BOT, BOT.split(":")[1]),
    ("token " + JWT, JWT.split(".")[0]),
    ("sha " + "a" * 64, "a" * 64),
    ("key Zm9vYmFyQmF6MTIzNDU2Nzg5MFpYV1ZVVFNSUVBPTk1MS0pJSEc", "Zm9vYmFyQmF6MTIzNDU2Nzg5MFpYV1ZVVFNSUVBPTk1MS0pJSEc"),
    ("-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END " + "PRIVATE KEY-----", "MIIEvQIBADANBg"),
])
def test_secrets_inside_text_are_removed(text, gone):
    out = redact_text(text)
    assert gone not in out and HIDDEN in out


@pytest.mark.parametrize("text", [
    "Studio_Name_Scene_Title_Of_Something_Very_Long_1080p.mp4",
    "some-very-long-video-title-from-2024-with-many-words-in-it-1080p",
    "Matched 12 scenes for Ava Stone in 4.2 s",
    "oshash 8f3a9c21d4e5b607",
])
def test_ordinary_library_text_is_left_alone(text):
    assert redact_text(text) == text


def test_tool_results_never_carry_secrets(tmp_path):
    from telegram_archive.core.ai_tools import AIToolRegistry
    from telegram_archive.core.database import Database
    db = Database(tmp_path / "t.db")
    reg = AIToolRegistry(db)
    monkey = {"stashdb_api_key": "SECRETVALUE1234", "title": "x"}
    reg.get_library_stats = lambda: {"stats": monkey, "note": "token=SECRETVALUE1234"}      # a tool that happens to find one
    out = reg.execute("get_library_stats", {})
    assert "SECRETVALUE1234" not in str(out)


def test_the_snapshot_and_approved_evidence_are_scrubbed_before_the_model(tmp_path):
    from telegram_archive.core.ai_assistant import ArchiveXAI
    from telegram_archive.core.database import Database
    ai = ArchiveXAI(Database(tmp_path / "t.db"))
    assert "SUPERSECRET99" not in ai._bounded_context("jobs ok\napi_key = SUPERSECRET99\nmore")


# ---------------------------------------------------------------- permissions ---------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def broker():
    log = []
    clock = Clock()
    b = PermissionBroker(log=lambda msg, level="INFO", **kw: log.append((kw.get("event"), msg)), clock=clock)
    b.log, b.clock, b.ran = log, clock, []
    for action in ("queue_job", "delete_media", "change_setting", "start_scan"):
        b.register(action, lambda args, a=action: b.ran.append((a, args)) or {"ok": True})
    return b


def test_forbidden_things_cannot_be_proposed_or_registered(broker):
    for action in ("read_secret", "run_shell", "run_sql", "fetch_url", "something_not_listed"):
        with pytest.raises(NotAllowed):
            broker.propose(action, "do it")
    with pytest.raises(NotAllowed):
        broker.register("run_shell", lambda a: None)
    assert ACTION_TIERS["read_secret"] == Tier.FORBIDDEN
    assert any(e == "ai.action.refused" for e, _ in broker.log)


def test_nothing_runs_until_a_person_approves(broker):
    p = broker.propose("queue_job", "Re-run the metadata match", args={"job": "metadata"})
    with pytest.raises(NotAllowed):
        broker.run(p.id)
    assert broker.ran == []
    broker.decide(p.id, True)
    done = broker.run(p.id)
    assert done.status == "DONE" and broker.ran == [("queue_job", {"job": "metadata"})]


def test_a_declined_request_never_runs(broker):
    p = broker.propose("queue_job", "x")
    broker.decide(p.id, False)
    with pytest.raises(NotAllowed):
        broker.run(p.id)
    assert broker.ran == []


def test_an_approval_works_once(broker):
    p = broker.propose("queue_job", "x")
    broker.decide(p.id, True)
    broker.run(p.id)
    with pytest.raises(NotAllowed):
        broker.run(p.id)
    with pytest.raises(NotAllowed):
        broker.decide(p.id, True)
    assert len(broker.ran) == 1


def test_a_request_expires(broker):
    p = broker.propose("queue_job", "x")
    broker.clock.t += 11 * 60
    assert broker.pending() == []
    with pytest.raises(NotAllowed):
        broker.decide(p.id, True)
    assert broker.get(p.id).status == "EXPIRED"


def test_an_approval_expires_too(broker):
    p = broker.propose("queue_job", "x")
    broker.decide(p.id, True)
    broker.clock.t += 11 * 60
    with pytest.raises(NotAllowed):
        broker.run(p.id)
    assert broker.ran == []


def test_destructive_actions_must_name_their_items_and_a_bulk_needs_typed_confirmation(broker):
    with pytest.raises(NotAllowed):
        broker.propose("delete_media", "delete some videos")                     # no items named
    one = broker.propose("delete_media", "Delete one video", items=["clip.mp4"])
    assert one.needs_typed == ""
    broker.decide(one.id, True)
    many = broker.propose("delete_media", "Delete three videos", items=["a.mp4", "b.mp4", "c.mp4"])
    assert many.needs_typed == "DELETE 3"
    with pytest.raises(NotAllowed):
        broker.decide(many.id, True)
    with pytest.raises(NotAllowed):
        broker.decide(many.id, True, typed="delete 2")
    assert broker.decide(many.id, True, typed=" delete 3 ").status == "APPROVED"


def test_secret_or_access_settings_cannot_be_changed_even_with_permission(broker):
    for name in ("stashdb_api_key", "web_password", "web_username", "ai_local_endpoint", "tls_cert"):
        with pytest.raises(NotAllowed):
            broker.propose("change_setting", "x", args={"name": name, "value": "1"})
    assert broker.propose("change_setting", "Scan with 3 at once", args={"name": "scan_concurrency", "value": "3"}).status == "PENDING"


def test_the_summary_is_scrubbed_and_the_page_never_gets_the_raw_arguments(broker):
    p = broker.propose("queue_job", "Run with api_key=ABCD1234EFGH", args={"path": "C:/private/x", "token": "t"})
    shown = p.public()
    assert "ABCD1234EFGH" not in shown["summary"] and "args" not in shown and "C:/private" not in str(shown)


def test_a_failing_action_is_recorded_as_failed_and_cannot_run_again(broker):
    b = PermissionBroker(log=lambda *a, **k: None)
    b.register("start_scan", lambda args: (_ for _ in ()).throw(RuntimeError("boom")))
    p = b.propose("start_scan", "x")
    b.decide(p.id, True)
    done = b.run(p.id)
    assert done.status == "FAILED" and "boom" in done.result["error"]
    with pytest.raises(NotAllowed):
        b.run(p.id)


def test_an_action_result_is_scrubbed_and_every_step_is_audited(broker):
    broker.register("queue_job", lambda args: {"token": "SHOULDNOTSHOW1", "ok": 1})
    p = broker.propose("queue_job", "x")
    broker.decide(p.id, True)
    out = broker.run(p.id)
    assert out.result == {"token": HIDDEN, "ok": 1}
    events = [e for e, _ in broker.log]
    assert events == ["ai.action.proposed", "ai.action.approved", "ai.action.done"]
