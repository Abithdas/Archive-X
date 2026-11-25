//! The `settings` table as Python's `Database.setting` / `set_setting` use it for ordinary (non-secret) keys: values are stored as JSON text.

use ax_db::rusqlite::{params, Connection, OptionalExtension};
use serde_json::Value;

/// The stored value, or `None` when there is none (or it is not valid JSON).
pub fn get(conn: &Connection, key: &str) -> Result<Option<Value>, ax_db::rusqlite::Error> {
    let raw: Option<String> = conn.query_row("SELECT value FROM settings WHERE key=?", [key], |r| r.get(0)).optional()?;
    Ok(raw.and_then(|t| serde_json::from_str(&t).ok()))
}

pub fn set(conn: &Connection, key: &str, value: &Value) -> Result<(), ax_db::rusqlite::Error> {
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        params![key, crate::pytext::dumps(value, true)],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn values_are_stored_as_python_writes_them() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)").unwrap();
        set(&conn, "k", &Value::String("é".into())).unwrap();
        let raw: String = conn.query_row("SELECT value FROM settings WHERE key='k'", [], |r| r.get(0)).unwrap();
        assert_eq!(raw, "\"\\u00e9\"");
        assert_eq!(get(&conn, "k").unwrap(), Some(Value::String("é".into())));
        set(&conn, "k", &serde_json::json!(5)).unwrap();
        assert_eq!(get(&conn, "k").unwrap(), Some(serde_json::json!(5)));
        assert_eq!(get(&conn, "missing").unwrap(), None);
    }
}
