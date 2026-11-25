//! Replays `notebook_cases.json`, recorded from Python's `ai/ai_knowledge.py` by `tools/parity/gen_ai_notebook_vectors.py`:
//! the assistant's notebook of findings and the index of the changelog and docs, on a real SQLite database.

use ax_ai::{findings, knowledge};
use ax_db::rusqlite::Connection;
use serde_json::{json, Value};
use std::cell::Cell;
use std::fs;
use std::path::Path;
use std::time::{Duration, UNIX_EPOCH};

fn set_time(path: &Path, ns: u64) {
    let mut open = fs::OpenOptions::new();
    open.read(true);
    #[cfg(windows)]
    {
        // Windows only lets a time be set through a handle opened for it, and a folder is opened only with backup semantics
        use std::os::windows::fs::OpenOptionsExt;
        open.access_mode(0x0100 /* FILE_WRITE_ATTRIBUTES */ | 0x8000_0000 /* GENERIC_READ */)
            .custom_flags(0x0200_0000 /* FILE_FLAG_BACKUP_SEMANTICS */);
    }
    let f = open.open(path).unwrap();
    f.set_modified(UNIX_EPOCH + Duration::from_nanos(ns)).unwrap();
}

fn lay_out(base: &Path, files: &Value) {
    let mut folders = Vec::new();
    for (rel, what) in files.as_object().unwrap() {
        let path = base.join(rel);
        if what.get("dir").and_then(Value::as_bool) == Some(true) {
            fs::create_dir_all(&path).unwrap();
            folders.push((path, what.get("mtime_ns").and_then(Value::as_u64)));
            continue;
        }
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        if let Some(h) = what.get("hex").and_then(Value::as_str) {
            fs::write(&path, (0..h.len()).step_by(2).map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap()).collect::<Vec<u8>>()).unwrap();
        } else if let Some(fill) = what.get("fill").and_then(Value::as_str) {
            let size = what["size"].as_u64().unwrap() as usize;
            fs::write(&path, &fill.repeat(size / fill.len() + 1).as_bytes()[..size]).unwrap();
        } else {
            fs::write(&path, what["text"].as_str().unwrap()).unwrap();
        }
        if let Some(ns) = what.get("mtime_ns").and_then(Value::as_u64) {
            set_time(&path, ns);
        }
    }
    // a folder named like a Markdown file is listed too, so its time is part of the fingerprint: set last, after its files were written
    for (path, ns) in folders {
        if let Some(ns) = ns {
            set_time(&path, ns);
        }
    }
}

fn dump(conn: &Connection) -> Value {
    let mut tables = serde_json::Map::new();
    for (t, cols) in
        [("ai_findings", "id,kind,title,title_key,detail,status,created_at,updated_at"), ("ai_knowledge", "id,source,ref,title,body,updated_at"), ("settings", "key,value")]
    {
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

// Values are compared as text on purpose: objects compare equal whatever their key order, and the order is part of what Python produced.
#[allow(clippy::cmp_owned)]
fn run_case(case: &Value) -> Result<(), String> {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(ax_db::schema::FRESH_SCHEMA_SQL).unwrap();
    let tick = Cell::new(0u32);
    let now = || {
        tick.set(tick.get() + 1);
        let t = tick.get();
        format!("2031-01-01T00:{:02}:{:02}+00:00", t / 60, t % 60)
    };
    let tmp = tempfile::tempdir().unwrap();
    let base = tmp.path();
    lay_out(base, case.get("files").unwrap_or(&json!({})));
    let mut steps: Vec<Value> = Vec::new();
    for op in case["ops"].as_array().unwrap() {
        let s = |k: &str| op[k].as_str().unwrap_or("");
        let step = match op["op"].as_str().unwrap() {
            "record" => json!({"ok": findings::record(&conn, &now, s("kind"), s("title"), s("detail")).unwrap()}),
            "list" => json!({"ok": findings::list(&conn, op["status"].as_str(), op["limit"].as_i64().unwrap()).unwrap()}),
            "decide" => match findings::decide(&conn, &now, op["id"].as_i64().unwrap(), s("status")) {
                Ok(b) => json!({"ok": b}),
                Err(e) => json!({"error": e}),
            },
            "counts" => json!({"ok": findings::counts(&conn).unwrap()}),
            "ensure" => json!({"ok": knowledge::ensure(&conn, &now, base).unwrap()}),
            "rebuild" => json!({"ok": knowledge::rebuild(&conn, &now, base, None, None).unwrap()}),
            "kcounts" => json!({"ok": knowledge::counts(&conn).unwrap()}),
            "search" => json!({"ok": knowledge::search(&conn, s("query"), op["limit"].as_i64()).unwrap()}),
            "files" => {
                lay_out(base, &op["files"]);
                json!({"ok": null})
            }
            "remove" => {
                fs::remove_file(base.join(s("path"))).unwrap();
                json!({"ok": null})
            }
            other => panic!("{other}"),
        };
        steps.push(step);
    }
    let expect = &case["expect"];
    let mut tables = dump(&conn);
    if case["files"].as_object().is_some_and(|f| f.values().any(|v| v["dir"] == true)) {
        // the signature counts a folder's own size, which each file system sets differently (4096 on ext4, 0 on NTFS): that one row is not compared
        for row in tables["settings"].as_array_mut().unwrap() {
            if row[0] == "ai_kb_signature" {
                row[1] = json!("<depends on the size of a folder>");
            }
        }
    }
    let got = json!({"steps": steps, "tables": tables});
    if got["steps"].to_string() != expect["steps"].to_string() {
        let (g, w) = (got["steps"].as_array().unwrap(), expect["steps"].as_array().unwrap());
        let at = (0..g.len().max(w.len())).find(|&i| g.get(i).map(Value::to_string) != w.get(i).map(Value::to_string)).unwrap_or(0);
        return Err(format!("step {at} ({}): want {}\n   got  {}", case["ops"][at]["op"], w.get(at).unwrap_or(&Value::Null), g.get(at).unwrap_or(&Value::Null)));
    }
    if got["tables"] != expect["tables"] {
        for t in ["ai_findings", "ai_knowledge", "settings"] {
            if got["tables"][t] != expect["tables"][t] {
                let (g, w) = (got["tables"][t].as_array().unwrap(), expect["tables"][t].as_array().unwrap());
                let at = (0..g.len().max(w.len())).find(|&i| g.get(i) != w.get(i)).unwrap_or(0);
                return Err(format!("table {t} row {at}: want {}\n   got  {}", w.get(at).unwrap_or(&Value::Null), g.get(at).unwrap_or(&Value::Null)));
            }
        }
    }
    Ok(())
}

#[test]
fn matches_python() {
    let cases: Vec<Value> = serde_json::from_str(&fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/notebook_cases.json")).unwrap()).unwrap();
    let failures: Vec<String> = cases.iter().filter_map(|c| run_case(c).err().map(|e| format!("{}: {e}", c["name"]))).collect();
    assert!(failures.is_empty(), "{} of {} cases differ from Python:\n{}", failures.len(), cases.len(), failures.join("\n"));
}
