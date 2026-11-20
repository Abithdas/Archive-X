//! The job lifecycle writes of `ax_db::jobs` must do what Python's `Database` does. The cases are recorded from the Python original by
//! `tools/parity/gen_job_op_vectors.py`; each step's error text and the rows of `jobs`, `job_journal` and `job_dependencies` must match.

use std::collections::HashMap;

use ax_db::jobs::{self, JournalEntry, NewJob};
use ax_db::rusqlite::{types::ValueRef, Connection};
use serde_json::{Map, Value};

fn schema() -> Value {
    serde_json::from_str(include_str!("../schema/schema.json")).unwrap()
}

/// The table as the Python side creates it, from the committed snapshot, with the keys the snapshot lists.
fn create_table(c: &Connection, snapshot: &Value, table: &str, extra: &str) {
    let cols = snapshot[table].as_array().unwrap();
    let pks: Vec<&str> = cols.iter().filter(|c| c["pk"] == true).map(|c| c["name"].as_str().unwrap()).collect();
    let mut defs: Vec<String> = Vec::new();
    for col in cols {
        let name = col["name"].as_str().unwrap();
        let ty = col["type"].as_str().unwrap();
        let single_integer_key = pks.len() == 1 && pks[0] == name && ty == "INTEGER";
        let mut def = format!("\"{name}\" {ty}");
        if single_integer_key {
            def.push_str(" PRIMARY KEY AUTOINCREMENT");
        } else if col["notnull"] == true {
            def.push_str(" NOT NULL");
        }
        defs.push(def);
    }
    if pks.len() > 1 || (pks.len() == 1 && !defs.iter().any(|d| d.contains("PRIMARY KEY"))) {
        defs.push(format!("PRIMARY KEY({})", pks.iter().map(|p| format!("\"{p}\"")).collect::<Vec<_>>().join(", ")));
    }
    if !extra.is_empty() {
        defs.push(extra.to_string());
    }
    c.execute_batch(&format!("CREATE TABLE \"{table}\" ({});", defs.join(", "))).unwrap();
}

fn is_iso(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 25
        && b[4] == b'-'
        && b[7] == b'-'
        && b[10] == b'T'
        && b[13] == b':'
        && b[16] == b':'
        && s.ends_with("+00:00")
        && b.iter().enumerate().all(|(i, c)| matches!(i, 4 | 7 | 10 | 13 | 16 | 19..=24) || c.is_ascii_digit())
}

/// A cell as Python hands it to `json.dumps`, with the recorded normalisation: job ids by name, times as `<T>` (except two columns).
fn cell(v: ValueRef, key: &str, names: &HashMap<String, String>) -> Value {
    match v {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(i) => Value::from(i),
        ValueRef::Real(f) => Value::from(f),
        ValueRef::Text(t) => {
            let s = String::from_utf8_lossy(t).to_string();
            if let Some(n) = names.get(&s) {
                Value::String(format!("<job:{n}>"))
            } else if is_iso(&s) && !matches!(key, "deadline_at" | "next_run_at") {
                Value::String("<T>".into())
            } else {
                Value::String(s)
            }
        }
        ValueRef::Blob(_) => Value::Null,
    }
}

fn table_rows(c: &Connection, table: &str, names: &HashMap<String, String>) -> Vec<Value> {
    let mut st = c.prepare(&format!("SELECT * FROM {table}")).unwrap();
    let cols: Vec<String> = st.column_names().map(|s| s.to_string()).collect();
    let mut rows: Vec<Map<String, Value>> = st
        .query_map([], |r| {
            let mut m = Map::new();
            for (i, name) in cols.iter().enumerate() {
                m.insert(name.clone(), cell(r.get_ref(i)?, name, names));
            }
            Ok(m)
        })
        .unwrap()
        .map(|r| r.unwrap())
        .collect();
    if table == "job_journal" {
        for r in rows.iter_mut() {
            r.shift_remove("id");
            r.insert("event_id".into(), Value::String("<E>".into()));
            if let Some(Value::String(p)) = r.get("payload").cloned() {
                let mut out = p;
                for (id, n) in names {
                    out = out.replace(id, &format!("<job:{n}>"));
                }
                r.insert("payload".into(), Value::String(out));
            }
        }
    }
    let key = |m: &Map<String, Value>, k: &str| m.get(k).map(|v| v.to_string()).unwrap_or_default();
    match table {
        "jobs" => rows.sort_by_key(|m| key(m, "id")),
        "job_journal" => rows.sort_by_key(|m| (key(m, "job_id"), m["sequence"].as_i64().unwrap())),
        _ => rows.sort_by_key(|m| (key(m, "job_id"), key(m, "depends_on_job_id"))),
    }
    rows.into_iter().map(Value::Object).collect()
}

fn state(c: &Connection, names: &HashMap<String, String>) -> Value {
    let mut m = Map::new();
    for t in ["jobs", "job_journal", "job_dependencies"] {
        m.insert(t.into(), Value::Array(table_rows(c, t, names)));
    }
    Value::Object(m)
}

fn text(v: &Value) -> Option<String> {
    if v.is_null() {
        None
    } else {
        Some(v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string()))
    }
}

/// Run one recorded operation; `Err` is the text Python raised (job ids by name).
fn apply(c: &Connection, op: &Value, ids: &mut HashMap<String, String>, names: &mut HashMap<String, String>) -> Result<(), String> {
    let id_of = |ids: &HashMap<String, String>, n: &str| ids.get(n).cloned().unwrap_or_else(|| n.to_string());
    let describe = |e: jobs::JobError, names: &HashMap<String, String>| {
        let mut t = e.to_string();
        for (id, n) in names {
            t = t.replace(id, &format!("<job:{n}>"));
        }
        t
    };
    match op["op"].as_str().unwrap() {
        "create" => {
            let a = &op["args"];
            let job = NewJob {
                source: a["source"].as_str().unwrap_or("").to_string(),
                destination: a["destination"].as_str().unwrap_or("").to_string(),
                destructive: a["destructive"].as_bool().unwrap_or(false),
                detail: a["detail"].as_str().unwrap_or("").to_string(),
                dry_run: a["dry_run"].as_bool().unwrap_or(false),
                priority: a["priority"].as_i64(),
                max_retries: a.get("max_retries").and_then(Value::as_i64).unwrap_or(3),
                deadline_at: a["deadline_at"].as_str().map(str::to_string),
                resource_requirements: a.get("resource_requirements").cloned(),
            };
            let jid = jobs::create_job(c, op["type"].as_str().unwrap(), &job).map_err(|e| describe(e, names))?;
            let name = op["name"].as_str().unwrap().to_string();
            ids.insert(name.clone(), jid.clone());
            names.insert(jid.clone(), name);
            // Python adds the dependencies after committing the job; a refused dependency leaves the job behind
            if let Some(deps) = a.get("depends_on").and_then(Value::as_array) {
                let deps: Vec<String> = deps.iter().map(|d| id_of(ids, d.as_str().unwrap())).collect();
                jobs::add_job_dependencies(c, &jid, &deps, "SUCCESS").map_err(|e| describe(e, names))?;
            }
            Ok(())
        }
        "transition" => {
            let fields = op["fields"].as_object().cloned().unwrap_or_default();
            jobs::transition_job(c, &id_of(ids, op["job"].as_str().unwrap()), op["status"].as_str().unwrap(), &fields).map_err(|e| describe(e, names))
        }
        "update" => {
            let fields = op["fields"].as_object().cloned().unwrap_or_default();
            jobs::update_job(c, &id_of(ids, op["job"].as_str().unwrap()), &fields).map_err(|e| describe(e, names))
        }
        "journal" => {
            let k = &op["kwargs"];
            let entry = JournalEntry {
                status: text(&k["status"]),
                from_status: text(&k["from_status"]),
                checkpoint: k.get("checkpoint").filter(|v| !v.is_null()).cloned(),
                operation: text(&k["operation"]),
                detail: text(&k["detail"]),
                payload: k.get("payload").filter(|v| !v.is_null()).cloned(),
                error_type: text(&k["error_type"]),
                retry_count: k["retry_count"].as_i64(),
                launch_id: text(&k["launch_id"]),
                created_at: text(&k["created_at"]),
            };
            jobs::append_job_journal(c, &id_of(ids, op["job"].as_str().unwrap()), op["event"].as_str().unwrap(), &entry).map_err(|e| describe(e, names))
        }
        "depends" => {
            let on: Vec<String> = op["on"].as_array().unwrap().iter().map(|d| id_of(ids, d.as_str().unwrap())).collect();
            jobs::add_job_dependencies(c, &id_of(ids, op["job"].as_str().unwrap()), &on, op["type"].as_str().unwrap()).map_err(|e| describe(e, names))
        }
        other => panic!("unknown operation {other}"),
    }
}

#[test]
fn every_recorded_scenario_replays_to_the_same_rows_and_errors() {
    let cases: Value = serde_json::from_str(include_str!("job_ops_cases.json")).unwrap();
    let snapshot = schema();
    for (scenario, case) in cases.as_object().unwrap() {
        let c = Connection::open_in_memory().unwrap();
        create_table(&c, &snapshot, "jobs", "");
        create_table(&c, &snapshot, "job_journal", "UNIQUE(job_id, sequence)");
        create_table(&c, &snapshot, "job_dependencies", "");
        let (mut ids, mut names) = (HashMap::new(), HashMap::new());
        for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            // each operation is one transaction, as in Python
            c.execute_batch("BEGIN IMMEDIATE").unwrap();
            let result = apply(&c, &step["op"], &mut ids, &mut names);
            // a create whose dependencies were refused keeps the job (Python committed it first)
            let keep = result.is_ok() || step["op"]["op"] == "create";
            c.execute_batch(if keep { "COMMIT" } else { "ROLLBACK" }).unwrap();
            let expected_error = step["error"].as_str().map(regex_job_ids);
            let got_error = result.err().map(|e| regex_job_ids(&e));
            assert_eq!(got_error, expected_error, "{scenario}, step {i}: {}", step["op"]);
            let got = state(&c, &names);
            assert_eq!(got["jobs"], step["state"]["jobs"], "{scenario}, step {i} ({}): jobs", step["op"]);
            assert_eq!(got["job_journal"], step["state"]["job_journal"], "{scenario}, step {i} ({}): journal", step["op"]);
            assert_eq!(got["job_dependencies"], step["state"]["job_dependencies"], "{scenario}, step {i} ({}): dependencies", step["op"]);
        }
    }
}

/// Python's recorded text names unknown jobs `<job:?>` when the id is 12 hex characters; do the same to ours.
fn regex_job_ids(s: &str) -> String {
    let b: Vec<char> = s.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < b.len() {
        let is_hex = |c: char| c.is_ascii_hexdigit() && !c.is_ascii_uppercase();
        let boundary_before = i == 0 || !(b[i - 1].is_alphanumeric() || b[i - 1] == '_');
        if boundary_before && i + 12 <= b.len() && b[i..i + 12].iter().all(|c| is_hex(*c)) && (i + 12 == b.len() || !(b[i + 12].is_alphanumeric() || b[i + 12] == '_')) {
            out.push_str("<job:?>");
            i += 12;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    out
}
