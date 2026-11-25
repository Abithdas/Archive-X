"""Record what Python's `ai/ai_persistence.py` does with a real SQLite database, for `crates/ax-ai/tests/persistence.rs`.

    python tools/parity/gen_ai_persistence_vectors.py          # rewrite crates/ax-ai/tests/persistence_cases.json
    python tools/parity/gen_ai_persistence_vectors.py --check  # fail if the file is out of date

Investigations, the chat history, observations and the evidence history, on a database made from the fresh schema, with a clock that counts.
After each case the tables are dumped; the Rust port must answer the same and leave the same rows. The schema creation and the
legacy-column migration of `AIPersistence` are not ported (the fresh schema has the tables; migrations wait for Phase 6).
"""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
OUT = ROOT / "crates" / "ax-ai" / "tests" / "persistence_cases.json"

from gen_ai_notebook_vectors import FakeDb  # noqa: E402
from telegram_archive.ai import ai_persistence as P  # noqa: E402

TABLES = (("ai_investigations", "id,title,status,stage,local_session_id,summary,created_at,updated_at"), ("ai_chat_messages", "id,investigation_id,conversation_id,role,content,response_id,created_at"),
          ("ai_observations", "id,investigation_id,kind,content,created_at"),
          ("ai_evidence_history", "id,evidence_id,investigation_id,kind,reason,scope,source_page,source,status,sha256,summary_json,created_at,updated_at"), ("settings", "key,value"))


def run(case: dict) -> dict:
    db = FakeDb()
    tick = [0]

    def now():
        tick[0] += 1
        return f"2031-02-01T00:{tick[0] // 60:02d}:{tick[0] % 60:02d}+00:00"

    P.config = types.SimpleNamespace(now_iso=now)
    store = P.AIPersistence(db)
    steps = []
    for op in case["ops"]:
        k, a = op["op"], op.get("args", {})
        try:
            if k == "raw":
                db.conn.execute(a["sql"], a.get("params", []))
                db.conn.commit()
                res = None
            elif k == "setting":
                db.set_setting(a["key"], a["value"])
                res = None
            else:
                res = getattr(store, k)(**a)
            steps.append({"ok": res})
        except (ValueError, TypeError) as exc:
            steps.append({"error": [type(exc).__name__, str(exc)]})
    tables = {t: [list(r) for r in db.conn.execute(f"SELECT {cols} FROM {t} ORDER BY 1")] for t, cols in TABLES}
    return {**case, "expect": {"steps": steps, "tables": tables}}


def op(name, **args):
    return {"op": name, "args": args}


def cases():
    out = []
    out.append({"name": "investigations", "ops": [op("create_investigation", title="First look"), op("create_investigation", title="   ", stage="  ", status=" "), op("create_investigation", title="", stage="", status=""), op("create_investigation", title=None),
                                                  op("create_investigation", title="  padded  ", stage="orbserve", status="active"), op("create_investigation", title="T" * 200, stage="s" * 40, status="ß" * 20),
                                                  op("create_investigation", title="  " + "x" * 158 + "  y  "), op("create_investigation", title="é ü", stage="stageß"),
                                                  op("list_investigations"), op("list_investigations", limit=2), op("list_investigations", limit=0), op("list_investigations", limit=500), op("list_investigations", limit=-3),
                                                  op("get_investigation", investigation_id=1), op("get_investigation", investigation_id=0), op("get_investigation", investigation_id=None),
                                                  op("get_investigation", investigation_id=99), op("get_investigation", investigation_id="2")]})
    out.append({"name": "choosing the active investigation", "ops": [op("active_investigation"), op("active_investigation"), op("create_investigation", title="Second"), op("set_active_investigation", investigation_id=2),
                                                                    op("active_investigation"), op("set_active_investigation", investigation_id=99), op("setting", key="ai_active_investigation_id", value=99), op("active_investigation"),
                                                                    op("setting", key="ai_active_investigation_id", value="2"), op("active_investigation"), op("setting", key="ai_active_investigation_id", value=0),
                                                                    op("active_investigation"), op("setting", key="ai_active_investigation_id", value="junk"), op("active_investigation")]})
    out.append({"name": "the first investigation takes the orphans", "ops": [op("raw", sql="INSERT INTO ai_chat_messages(conversation_id,role,content,created_at) VALUES('c','user','orphan one','t')"),
                                                                           op("raw", sql="INSERT INTO ai_chat_messages(conversation_id,role,content,created_at,investigation_id) VALUES('c','user','kept',?,7)", params=["t"]),
                                                                           op("raw", sql="INSERT INTO ai_observations(kind,content,created_at) VALUES('OBSERVATION','orphan obs','t')"),
                                                                           op("active_investigation"), op("list_chat_messages", limit=10), op("list_observations", limit=10),
                                                                           op("list_chat_messages", limit=10, investigation_id=7)]})
    out.append({"name": "updating an investigation", "ops": [op("create_investigation", title="Before"), op("update_investigation", investigation_id=1, title="After", summary="s" * 6000),
                                                            op("update_investigation", investigation_id=1, status="done", stage="fix", local_session_id="x" * 300), op("update_investigation", investigation_id=1),
                                                            op("update_investigation", investigation_id=1, title="", summary=""), op("update_investigation", investigation_id=1, title="T" * 200, status="ß" * 40),
                                                            op("update_investigation", investigation_id=9, title="nope"), op("get_investigation", investigation_id=1)]})
    chat = [op("add_chat_message", conversation_id="c1", role="user", content="hello"), op("add_chat_message", conversation_id="c1", role="assistant", content="hi there", response_id="r1"),
            op("add_chat_message", conversation_id=None, role="", content=None), op("add_chat_message", conversation_id="c2", role="r" * 50, content="c" * 5000, response_id=None)]
    out.append({"name": "the list is ordered by when each was last changed", "ops": [op("create_investigation", title="old"), op("create_investigation", title="middle"), op("create_investigation", title="new"),
                                                                                   op("update_investigation", investigation_id=1, summary="touched last"), op("list_investigations"), op("list_investigations", limit=2)]})
    fill = lambda table, cols, vals, n: op("raw", sql=f"WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<{n}) INSERT INTO {table}({cols}) SELECT {vals} FROM c")  # noqa: E731
    out.append({"name": "how many rows a list returns", "ops": [op("create_investigation", title="One"),
                                                               fill("ai_chat_messages", "investigation_id,conversation_id,role,content,created_at", "1,'c','user','m'||x,'t'", 600), op("list_chat_messages", limit=1000), op("list_chat_messages", limit=500), op("list_chat_messages", limit=501),
                                                               fill("ai_observations", "investigation_id,kind,content,created_at", "1,'K','o'||x,'t'", 300), op("list_observations", limit=999), op("list_observations", limit=200), op("list_observations", limit=201),
                                                               fill("ai_investigations", "title,created_at,updated_at", "'t'||x,'a','b'", 150), op("list_investigations", limit=1000), op("list_investigations", limit=100), op("list_investigations", limit=101),
                                                               fill("ai_evidence_history", "evidence_id,investigation_id,kind,created_at,updated_at", "'e'||x,1,'K','a','b'", 150), op("list_evidence_history", limit=1000), op("list_evidence_history", limit=100), op("list_evidence_history", limit=101)]})
    out.append({"name": "chat messages", "ops": chat + [op("create_investigation", title="Other"), op("add_chat_message", conversation_id="c3", role="user", content="in other", investigation_id=2),
                                                        op("list_chat_messages"), op("list_chat_messages", limit=2), op("list_chat_messages", limit=0), op("list_chat_messages", limit=1000), op("list_chat_messages", investigation_id=2),
                                                        op("list_chat_messages", investigation_id=5), op("clear_chat_messages"), op("list_chat_messages"), op("list_chat_messages", investigation_id=2),
                                                        op("clear_chat_messages", investigation_id=2), op("list_chat_messages", investigation_id=2)]})
    out.append({"name": "observations", "ops": [op("add_observation", kind="note", content="first"), op("add_observation", kind="", content=None), op("add_observation", kind=None, content="x" * 25000),
                                                op("add_observation", kind="k" * 50, content="é"), op("create_investigation", title="Other"), op("add_observation", kind="other", content="o", investigation_id=2),
                                                op("list_observations"), op("list_observations", limit=2), op("list_observations", limit=0), op("list_observations", limit=999), op("list_observations", investigation_id=2)]})
    ev = dict(evidence_id="ev1", investigation_id=1, kind="DIAG", reason="why", scope="all", source_page="/settings", source="user", status="STAGED", sha256="a" * 64)
    out.append({"name": "evidence history", "ops": [op("create_investigation", title="One"), op("record_evidence_history", **ev, summary={"files": 3, "note": "é日本", "nested": {"a": [1, 2.5, None, True], "b": "x"}}),
                                                    op("record_evidence_history", **{**ev, "status": "SENT", "sha256": "b" * 64}, summary={"changed": 1}),
                                                    op("record_evidence_history", **{**ev, "evidence_id": "ev2", "reason": "r" * 3000, "scope": "s" * 700, "source_page": "p" * 700, "source": "o" * 200, "status": "t" * 50, "sha256": "c" * 200}, summary=None),
                                                    op("record_evidence_history", **{**ev, "evidence_id": "ev3", "investigation_id": None, "kind": None, "reason": None, "scope": None, "source_page": None, "source": None, "status": None, "sha256": None}, summary={}),
                                                    op("record_evidence_history", **{**ev, "evidence_id": "ev4", "investigation_id": 2}, summary={"floats": [0.1, 1e16, 1e-5, 123456789.123, -0.0, 1.5e300, 100.0, 2.5e-7, 12345678901234567890], "text": "quote\" backslash\\ tab\t nl\n \u0001  "}),
                                                    op("list_evidence_history"), op("list_evidence_history", investigation_id=2), op("list_evidence_history", investigation_id=1, limit=1), op("list_evidence_history", limit=0),
                                                    op("list_evidence_history", limit=500), op("raw", sql="UPDATE ai_evidence_history SET summary_json='not json' WHERE evidence_id='ev1'"), op("list_evidence_history")]})
    return out


def render() -> str:
    return "[\n" + ",\n".join(json.dumps(run(c), ensure_ascii=False) for c in cases()) + "\n]\n"


def main():
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is out of date; run python tools/parity/gen_ai_persistence_vectors.py")
            sys.exit(1)
        print("ai persistence vectors are up to date")
        return
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(text) // 1024} KiB")


if __name__ == "__main__":
    main()
