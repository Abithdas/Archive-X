"""What the assistant knows about Archive X, and its notebook of findings."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core import ai_knowledge as kb  # noqa: E402
from telegram_archive.core.ai_tools import AIToolRegistry  # noqa: E402
from telegram_archive.core.database import Database  # noqa: E402

H = {"Origin": "https://testserver"}

CHANGELOG = """# Changelog

## 9.9.9 (2030-01-01)
- Snapshot identification: the watermark of a studio is compared with its other videos.
  - Needs at least three videos that agree.
- Faster channel scan: StashDB is asked about forty fingerprints at once.

## 9.9.8
- Gallery pictures from videos. api_key=SECRETPIECE123 was once in a log line.
"""
CODE = '''"""Module about widgets."""


def make_widget(size, colour):
    """Build a widget of the given size. Slow when the colour is mixed."""


class Widget:
    """A thing that can be built."""

    def spin(self, times):
        """Spin the widget a number of times."""

    def __hidden__(self):
        """Not indexed."""
'''


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "updates.md").write_text("# Updates\n\n## How updates are signed\nThe installer is signed with a key and latest.json lists the version.\n", encoding="utf-8")
    (root / "docs" / "archive").mkdir()
    (root / "docs" / "archive" / "old.md").write_text("# Old\nancientword lives here\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    pkg = root / "telegram_archive"
    (pkg / "engines").mkdir(parents=True)
    (pkg / "engines" / "widgets.py").write_text(CODE, encoding="utf-8")
    (pkg / "engines" / "broken.py").write_text("def (:\n", encoding="utf-8")
    monkeypatch.setattr(kb, "ROOT", root)
    monkeypatch.setattr(kb, "PACKAGE", pkg)
    return root


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


# ---------------------------------------------------------------- knowledge -----------------------------------------------------------------------
def test_the_index_is_built_from_the_changelog_docs_and_docstrings(tree, db):
    k = kb.Knowledge(db)
    assert k.ensure() is True
    c = k.counts()
    assert c["changelog"] == 3 and c["docs"] >= 1 and c["code"] >= 4
    assert not any("ancientword" in r["text"] for r in k.search("ancientword"))     # the archive folder is not read


def test_a_question_finds_the_piece_that_answers_it(tree, db):
    k = kb.Knowledge(db)
    k.ensure()
    top = k.search("how does the studio watermark identification work")[0]
    assert top["source"] == "changelog" and "watermark" in top["text"].lower()
    assert any("signed" in r["text"] for r in k.search("how are updates signed"))
    code = k.search("spin the widget")
    assert any(r["source"] == "code" and r["title"].startswith("Widget.spin") for r in code)
    assert not any("Not indexed" in r["text"] for r in k.search("indexed"))          # dunder methods are skipped
    assert k.search("") == [] and k.search("the of and") == []


def test_results_are_bounded_and_small(tree, db):
    k = kb.Knowledge(db)
    k.ensure()
    out = k.search("widget", limit=50)
    assert len(out) <= 10 and all(len(r["text"]) <= 320 for r in out)


def test_it_is_only_rebuilt_when_the_files_change(tree, db):
    k = kb.Knowledge(db)
    assert k.ensure() is True and k.ensure() is False
    (tree / "CHANGELOG.md").write_text(CHANGELOG + "- A brand new line about zebras.\n", encoding="utf-8")
    assert k.ensure() is True
    assert any("zebras" in r["text"] for r in k.search("zebras"))


def test_secrets_in_the_documents_never_enter_the_index(tree, db):
    k = kb.Knowledge(db)
    k.ensure()
    assert "SECRETPIECE123" not in str(k.search("gallery pictures log line"))
    with db.lock:
        assert db.conn.execute("SELECT COUNT(*) FROM ai_knowledge WHERE body LIKE '%SECRETPIECE123%'").fetchone()[0] == 0


def test_a_missing_source_tree_gives_an_empty_but_working_index(tmp_path, monkeypatch, db):
    monkeypatch.setattr(kb, "ROOT", tmp_path / "nothing")
    monkeypatch.setattr(kb, "PACKAGE", tmp_path / "nothing2")
    k = kb.Knowledge(db)
    k.ensure()
    assert k.search("anything") == [] and k.counts() == {}


# ---------------------------------------------------------------- findings ------------------------------------------------------------------------
def test_a_finding_is_recorded_once_and_a_repeat_only_refreshes_it(db):
    f = kb.Findings(db)
    a = f.record("issue", "Thumbnail indexer is slow on large channels", "Takes 40 s per 1000 videos.")
    assert a["recorded"] and a["status"] == "OPEN" and a["repeat"] is False
    b = f.record("issue", "thumbnail indexer is SLOW on large channels!", "Now 55 s.")
    assert b["id"] == a["id"] and b["repeat"] is True
    assert len(f.list()) == 1 and f.list()[0]["detail"] == "Now 55 s."


def test_a_rejected_finding_is_never_recorded_again(db):
    f = kb.Findings(db)
    a = f.record("idea", "Use a larger context window for everything")
    f.decide(a["id"], "REJECTED")
    again = f.record("idea", "Use a larger context window for everything")
    assert again["recorded"] is False and "rejected" in again["reason"]
    assert [x["status"] for x in f.list()] == ["REJECTED"]


def test_accepted_and_fixed_ones_stay_recorded_and_are_counted(db):
    f = kb.Findings(db)
    ids = [f.record("issue", f"Problem number {i} in the scan")["id"] for i in range(3)]
    f.decide(ids[0], "ACCEPTED")
    f.decide(ids[1], "FIXED")
    assert f.counts() == {"OPEN": 1, "ACCEPTED": 1, "REJECTED": 0, "FIXED": 1}
    assert [x["title"] for x in f.list("FIXED")] == ["Problem number 1 in the scan"]
    assert f.record("issue", "Problem number 1 in the scan")["status"] == "FIXED"
    with pytest.raises(ValueError):
        f.decide(ids[0], "DELETED")
    assert f.decide(99999, "OPEN") is False


def test_a_finding_needs_a_clear_title_and_never_keeps_a_secret(db):
    f = kb.Findings(db)
    assert f.record("issue", "x")["recorded"] is False
    r = f.record("bogus kind", "The login log shows token=abcd1234efgh5678", "password: hunter2hunter2 was printed")
    assert r["recorded"]
    saved = f.list()[0]
    assert saved["kind"] == "note" and "abcd1234efgh5678" not in saved["title"] and "hunter2hunter2" not in saved["detail"]


def test_the_notebook_has_a_limit_on_open_findings(db, monkeypatch):
    monkeypatch.setattr(kb, "MAX_OPEN_FINDINGS", 3)
    f = kb.Findings(db)
    for i in range(3):
        assert f.record("note", f"Open finding number {i} here")["recorded"]
    full = f.record("note", "One more open finding here")
    assert full["recorded"] is False and "full" in full["reason"]


# ---------------------------------------------------------------- tools ---------------------------------------------------------------------------
def test_the_four_new_tools_are_offered_and_work(tree, db):
    reg = AIToolRegistry(db)
    names = {d["function"]["name"] for d in reg.definitions()}
    assert {"search_knowledge", "search_logs", "list_findings", "record_finding"} <= names
    assert reg.execute("search_knowledge", {"query": "forty fingerprints"})["results"][0]["source"] == "changelog"
    assert reg.execute("record_finding", {"kind": "issue", "title": "Scan is slow at night"})["recorded"] is True
    listed = reg.execute("list_findings", {})
    assert listed["findings"][0]["title"] == "Scan is slow at night" and listed["counts"]["OPEN"] == 1


def test_search_logs_shows_warnings_by_default_and_filters_by_words(db):
    db.log("All good here", "INFO")
    db.log("Telegram send failed for video 7", "ERROR")
    db.log("Slow query warning on the library page", "WARN")
    reg = AIToolRegistry(db)
    events = reg.execute("search_logs", {})["events"]
    assert {e["level"] for e in events} <= {"WARN", "WARNING", "ERROR"} and len(events) == 2
    only = reg.execute("search_logs", {"query": "telegram send"})["events"]
    assert [e["message"] for e in only] == ["Telegram send failed for video 7"]
    assert reg.execute("search_logs", {"query": "nothing matches this"})["events"] == []


def test_a_log_line_with_a_secret_is_hidden_from_the_model(db):
    db.log("Request failed with api_key=LEAKEDKEY99887766 for stashdb", "ERROR")
    out = AIToolRegistry(db).execute("search_logs", {})
    assert "LEAKEDKEY99887766" not in str(out)


def test_the_tool_list_stays_small_enough_for_a_short_context_window(db):
    import json
    size = len(json.dumps(AIToolRegistry(db).definitions()))
    assert size < 7200, size       # about 1,800 tokens: the assistant's window can be as short as 4,096 tokens on a 4 GB card


# ---------------------------------------------------------------- routes --------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path):
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    d = Database(tmp_path / "r.db")
    ensure_web_credentials(d)
    c = TestClient(create_app(db=d), base_url="https://testserver", client=("127.0.0.1", 50000))
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, d


def test_the_routes_list_decide_and_rebuild(client, tree):
    c, d = client
    f = kb.Findings(d)
    a = f.record("issue", "Gallery download retries too often")["id"]
    out = c.get("/api/ai/findings", headers=H).json()
    assert out["counts"]["OPEN"] == 1 and out["findings"][0]["id"] == a and out["knowledge"]["changelog"] == 3
    assert c.post(f"/api/ai/findings/{a}", json={"status": "accepted"}, headers=H).status_code == 200
    assert c.get("/api/ai/findings?status=accepted", headers=H).json()["findings"][0]["status"] == "ACCEPTED"
    assert c.post(f"/api/ai/findings/{a}", json={"status": "nonsense"}, headers=H).status_code == 400
    assert c.post("/api/ai/findings/99999", json={"status": "FIXED"}, headers=H).status_code == 404
    assert c.post("/api/ai/knowledge/rebuild", headers=H).json()["knowledge"]["changelog"] == 3


def test_the_routes_need_a_login(tmp_path):
    from telegram_archive.app import create_app
    c = TestClient(create_app(db=Database(tmp_path / "n.db")), base_url="https://testserver")
    assert c.get("/api/ai/findings", headers=H).status_code in (401, 403)
