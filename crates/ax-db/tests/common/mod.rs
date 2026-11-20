//! Shared by the replay tests: the tables as the Python side creates them, and rows as Python's `json.dumps` sees them.
#![allow(dead_code)]

use ax_db::rusqlite::{types::ValueRef, Connection};
use serde_json::{Map, Value};

pub fn schema() -> Value {
    serde_json::from_str(include_str!("../../schema/schema.json")).unwrap()
}

/// The table from the committed snapshot, with the keys it lists, `extra` constraints, and the defaults Python's DDL has for columns the code leaves out.
pub fn create_table(c: &Connection, snapshot: &Value, table: &str, extra: &str) {
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
            if (table, name) == ("job_executions", "failure_class") {
                def.push_str(" DEFAULT ''");
            }
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

/// `YYYY-MM-DDTHH:MM:SS` with an optional `.ffffff`, then `+00:00`.
pub fn is_iso(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() < 25 || !s.ends_with("+00:00") {
        return false;
    }
    let body = &b[..b.len() - 6];
    let ok_date = body.len() >= 19
        && body[4] == b'-'
        && body[7] == b'-'
        && body[10] == b'T'
        && body[13] == b':'
        && body[16] == b':'
        && body[..19].iter().enumerate().all(|(i, c)| matches!(i, 4 | 7 | 10 | 13 | 16) || c.is_ascii_digit());
    let rest = &body[19..];
    ok_date && (rest.is_empty() || (rest.len() == 7 && rest[0] == b'.' && rest[1..].iter().all(|c| c.is_ascii_digit())))
}

/// One row, every column, as JSON (integers stay integers, reals stay reals).
pub fn rows(c: &Connection, table: &str) -> Vec<Map<String, Value>> {
    let mut st = c.prepare(&format!("SELECT * FROM {table}")).unwrap();
    let cols: Vec<String> = st.column_names().map(|s| s.to_string()).collect();
    st.query_map([], |r| {
        let mut m = Map::new();
        for (i, name) in cols.iter().enumerate() {
            m.insert(
                name.clone(),
                match r.get_ref(i)? {
                    ValueRef::Null => Value::Null,
                    ValueRef::Integer(v) => Value::from(v),
                    ValueRef::Real(v) => Value::from(v),
                    ValueRef::Text(t) => Value::String(String::from_utf8_lossy(t).to_string()),
                    ValueRef::Blob(_) => Value::Null,
                },
            );
        }
        Ok(m)
    })
    .unwrap()
    .map(|r| r.unwrap())
    .collect()
}
