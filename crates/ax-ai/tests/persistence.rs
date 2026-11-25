//! Replays `persistence_cases.json`, recorded from Python's `ai/ai_persistence.py` by `tools/parity/gen_ai_persistence_vectors.py`.

use ax_ai::persistence::{self as p, Evidence, InvestigationUpdate};
use ax_config::pyfmt::py_int_str;
use ax_db::rusqlite::Connection;
use serde_json::{json, Value};
use std::cell::Cell;
use std::fs;
use std::path::Path;

fn text(a: &Value, k: &str) -> Option<String> {
    a.get(k).and_then(Value::as_str).map(String::from)
}

/// An id as Python's `int()` reads it: a number, or text of digits.
fn id(a: &Value, k: &str) -> Option<i64> {
    match a.get(k)? {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => py_int_str(s).ok(),
        _ => None,
    }
}

fn limit(a: &Value, default: i64) -> i64 {
    a.get("limit").and_then(Value::as_i64).unwrap_or(default)
}

fn dump(conn: &Connection) -> Value {
    let mut tables = serde_json::Map::new();
    for (t, cols) in [
        ("ai_investigations", "id,title,status,stage,local_session_id,summary,created_at,updated_at"),
        ("ai_chat_messages", "id,investigation_id,conversation_id,role,content,response_id,created_at"),
        ("ai_observations", "id,investigation_id,kind,content,created_at"),
        (
            "ai_evidence_history",
            "id,evidence_id,investigation_id,kind,reason,scope,source_page,source,status,sha256,summary_json,created_at,updated_at",
        ),
        ("settings", "key,value"),
    ] {
        let mut stmt = conn.prepare(&format!("SELECT {cols} FROM {t} ORDER BY 1")).unwrap();
        let n = stmt.column_count();
        let rows: Vec<Value> = stmt
            .query_map([], |r| {
                Ok(Value::Array(
                    (0..n)
                        .map(|i| match r.get_ref(i).unwrap() {
                            ax_db::rusqlite::types::ValueRef::Integer(v) => json!(v),
                            ax_db::rusqlite::types::ValueRef::Text(b) => json!(String::from_utf8_lossy(b)),
                            _ => Value::Null,
                        })
                        .collect(),
                ))
            })
            .unwrap()
            .map(Result::unwrap)
            .collect();
        tables.insert(t.to_string(), Value::Array(rows));
    }
    Value::Object(tables)
}

fn answer<T>(r: p::Result<T>, ok: impl FnOnce(T) -> Value) -> Value {
    match r {
        Ok(v) => json!({"ok": ok(v)}),
        Err(e) => json!({"error": [e.kind, e.message]}),
    }
}

#[allow(clippy::cmp_owned)] // compared as text on purpose: key order is part of what Python produced
fn run_case(case: &Value) -> Result<(), String> {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(ax_db::schema::FRESH_SCHEMA_SQL).unwrap();
    let tick = Cell::new(0u32);
    let now = || {
        tick.set(tick.get() + 1);
        let t = tick.get();
        format!("2031-02-01T00:{:02}:{:02}+00:00", t / 60, t % 60)
    };
    let mut steps = Vec::new();
    for op in case["ops"].as_array().unwrap() {
        let a = op.get("args").unwrap_or(&Value::Null);
        let s = |k: &str| text(a, k);
        let r = |k: &str| s(k);
        let step = match op["op"].as_str().unwrap() {
            "raw" => {
                let params: Vec<String> = a["params"].as_array().map(|v| v.iter().map(|x| x.as_str().unwrap().to_string()).collect()).unwrap_or_default();
                conn.execute(a["sql"].as_str().unwrap(), ax_db::rusqlite::params_from_iter(params.iter())).unwrap();
                json!({"ok": null})
            }
            "setting" => {
                ax_ai_set(&conn, a["key"].as_str().unwrap(), &a["value"]);
                json!({"ok": null})
            }
            "create_investigation" => answer(p::create_investigation(&conn, &now, r("title").as_deref(), r("stage").as_deref(), r("status").as_deref()), |v| json!(v)),
            "list_investigations" => answer(p::list_investigations(&conn, limit(a, 50)), Value::Array),
            "get_investigation" => answer(p::get_investigation(&conn, id(a, "investigation_id")), |v| v.map_or(Value::Null, Value::Object)),
            "active_investigation" => answer(p::active_investigation(&conn, &now), Value::Object),
            "set_active_investigation" => answer(p::set_active_investigation(&conn, id(a, "investigation_id").unwrap()), Value::Object),
            "update_investigation" => {
                let (title, status, stage, summary, session) = (s("title"), s("status"), s("stage"), s("summary"), s("local_session_id"));
                let change = InvestigationUpdate {
                    title: title.as_deref(),
                    status: status.as_deref(),
                    stage: stage.as_deref(),
                    summary: summary.as_deref(),
                    local_session_id: session.as_deref(),
                };
                answer(p::update_investigation(&conn, &now, id(a, "investigation_id").unwrap(), &change), Value::Object)
            }
            "add_chat_message" => answer(
                p::add_chat_message(
                    &conn,
                    &now,
                    s("conversation_id").as_deref(),
                    s("role").as_deref(),
                    s("content").as_deref(),
                    s("response_id").as_deref(),
                    id(a, "investigation_id"),
                ),
                |v| json!(v),
            ),
            "list_chat_messages" => answer(p::list_chat_messages(&conn, &now, limit(a, 120), id(a, "investigation_id")), Value::Array),
            "clear_chat_messages" => answer(p::clear_chat_messages(&conn, &now, id(a, "investigation_id")), |_| Value::Null),
            "add_observation" => answer(p::add_observation(&conn, &now, s("kind").as_deref(), s("content").as_deref(), id(a, "investigation_id")), |v| json!(v)),
            "list_observations" => answer(p::list_observations(&conn, &now, limit(a, 60), id(a, "investigation_id")), Value::Array),
            "record_evidence_history" => {
                let (kind, reason, scope, page, source, status, sha) = (s("kind"), s("reason"), s("scope"), s("source_page"), s("source"), s("status"), s("sha256"));
                let e = Evidence {
                    evidence_id: a["evidence_id"].as_str().unwrap(),
                    investigation_id: id(a, "investigation_id"),
                    kind: kind.as_deref(),
                    reason: reason.as_deref(),
                    scope: scope.as_deref(),
                    source_page: page.as_deref(),
                    source: source.as_deref(),
                    status: status.as_deref(),
                    sha256: sha.as_deref(),
                    summary: a.get("summary"),
                };
                answer(p::record_evidence_history(&conn, &now, &e), |_| Value::Null)
            }
            "list_evidence_history" => answer(p::list_evidence_history(&conn, &now, id(a, "investigation_id"), limit(a, 50)), Value::Array),
            other => panic!("{other}"),
        };
        steps.push(step);
    }
    let expect = &case["expect"];
    let got = json!({"steps": steps, "tables": dump(&conn)});
    if got["steps"].to_string() != expect["steps"].to_string() {
        let (g, w) = (got["steps"].as_array().unwrap(), expect["steps"].as_array().unwrap());
        let at = (0..g.len().max(w.len())).find(|&i| g.get(i).map(Value::to_string) != w.get(i).map(Value::to_string)).unwrap_or(0);
        return Err(format!("step {at} ({}): want {}\n   got  {}", case["ops"][at]["op"], w.get(at).unwrap_or(&Value::Null), g.get(at).unwrap_or(&Value::Null)));
    }
    for t in ["ai_investigations", "ai_chat_messages", "ai_observations", "ai_evidence_history", "settings"] {
        if got["tables"][t] != expect["tables"][t] {
            let (g, w) = (got["tables"][t].as_array().unwrap(), expect["tables"][t].as_array().unwrap());
            let at = (0..g.len().max(w.len())).find(|&i| g.get(i) != w.get(i)).unwrap_or(0);
            return Err(format!("table {t} row {at}: want {}\n   got  {}", w.get(at).unwrap_or(&Value::Null), g.get(at).unwrap_or(&Value::Null)));
        }
    }
    Ok(())
}

/// Python's `set_setting`: the value as JSON text, non-ASCII escaped.
fn ax_ai_set(conn: &Connection, key: &str, value: &Value) {
    let mut text = serde_json::to_string(value).unwrap();
    if !text.is_ascii() {
        text = text
            .chars()
            .map(|c| if c.is_ascii() { c.to_string() } else { c.encode_utf16(&mut [0; 2]).iter().map(|u| format!("\\u{u:04x}")).collect() })
            .collect();
    }
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, &text]).unwrap();
}

#[test]
fn matches_python() {
    let cases: Vec<Value> = serde_json::from_str(&fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/persistence_cases.json")).unwrap()).unwrap();
    let failures: Vec<String> = cases.iter().filter_map(|c| run_case(c).err().map(|e| format!("{}: {e}", c["name"]))).collect();
    assert!(failures.is_empty(), "{} of {} cases differ from Python:\n{}", failures.len(), cases.len(), failures.join("\n"));
}
