//! The assistant's investigations, chat history, observations and evidence history (`ai/ai_persistence.py`, `AIPersistence`).
//!
//! The functions take the connection and a clock, like the other database ports; each call is one step. Not ported: `ensure_schema` and
//! `migrate_legacy_schema` (the fresh schema has these tables; upgrading an old database waits for the migrations, Phase 6).

use crate::pytext::{dumps, head};
use crate::settings;
use ax_config::pyfmt::{py_int_str, py_truthy};
use ax_config::ConfigError;
use ax_core::scene_parser::py_strip;
use ax_db::rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Map, Value};

pub type Result<T> = std::result::Result<T, ConfigError>;

fn sql(e: ax_db::rusqlite::Error) -> ConfigError {
    ConfigError::new("OperationalError", e.to_string())
}

/// `dict(row)`: the columns in the order of the query.
fn row_to_map(row: &Row) -> ax_db::rusqlite::Result<Map<String, Value>> {
    use ax_db::rusqlite::types::ValueRef;
    let mut m = Map::new();
    for (i, name) in row.as_ref().column_names().enumerate() {
        let v = match row.get_ref(i)? {
            ValueRef::Null => Value::Null,
            ValueRef::Integer(n) => json!(n),
            ValueRef::Real(f) => json!(f),
            ValueRef::Text(t) => json!(String::from_utf8_lossy(t)),
            ValueRef::Blob(_) => Value::Null,
        };
        m.insert(name.to_string(), v);
    }
    Ok(m)
}

fn rows(conn: &Connection, query: &str, args: impl ax_db::rusqlite::Params) -> Result<Vec<Map<String, Value>>> {
    let mut stmt = conn.prepare(query).map_err(sql)?;
    let found = stmt.query_map(args, row_to_map).map_err(sql)?.collect::<std::result::Result<Vec<_>, _>>().map_err(sql)?;
    Ok(found)
}

const INVESTIGATION: &str = "SELECT id,title,status,stage,local_session_id,summary,created_at,updated_at FROM ai_investigations";

/// `str(value or default)`.
fn or_default(v: Option<&str>, default: &str) -> String {
    match v {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => default.to_string(),
    }
}

fn upper(s: &str, n: usize) -> String {
    head(&s.to_uppercase(), n)
}

pub fn create_investigation(conn: &Connection, now: &dyn Fn() -> String, title: Option<&str>, stage: Option<&str>, status: Option<&str>) -> Result<i64> {
    let stamp = now();
    let stripped = head(py_strip(&or_default(title, "New investigation")), 160);
    let clean_title = if stripped.is_empty() { "New investigation".to_string() } else { stripped };
    conn.execute(
        "INSERT INTO ai_investigations(title,status,stage,created_at,updated_at) VALUES(?,?,?,?,?)",
        params![clean_title, upper(&or_default(status, "ACTIVE"), 32), upper(&or_default(stage, "OBSERVE"), 32), stamp, stamp],
    )
    .map_err(sql)?;
    Ok(conn.last_insert_rowid())
}

pub fn list_investigations(conn: &Connection, limit: i64) -> Result<Vec<Value>> {
    let n = (if limit == 0 { 50 } else { limit }).clamp(1, 100);
    Ok(rows(conn, &format!("{INVESTIGATION} ORDER BY updated_at DESC, id DESC LIMIT ?"), [n])?.into_iter().map(Value::Object).collect())
}

/// Nothing for an id of zero or none.
pub fn get_investigation(conn: &Connection, investigation_id: Option<i64>) -> Result<Option<Map<String, Value>>> {
    let Some(id) = investigation_id.filter(|i| *i != 0) else { return Ok(None) };
    Ok(rows(conn, &format!("{INVESTIGATION} WHERE id=?"), [id])?.into_iter().next())
}

/// `int(value)` of a stored setting.
fn setting_int(v: &Value) -> Result<i64> {
    match v {
        Value::Bool(b) => Ok(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).ok_or_else(|| ConfigError::value("int too large")),
        Value::String(s) => py_int_str(s),
        other => Err(ConfigError::new(
            "TypeError",
            format!("int() argument must be a string, a bytes-like object or a real number, not '{}'", ax_config::pyfmt::py_type_name(other)),
        )),
    }
}

/// The active investigation, creating or repairing the selection if needed.
pub fn active_investigation(conn: &Connection, now: &dyn Fn() -> String) -> Result<Map<String, Value>> {
    let raw = settings::get(conn, "ai_active_investigation_id").map_err(sql)?;
    let chosen = match raw.as_ref().filter(|v| py_truthy(v)) {
        Some(v) => get_investigation(conn, Some(setting_int(v)?))?,
        None => None,
    };
    if let Some(found) = chosen {
        return Ok(found);
    }
    let first: Option<i64> = conn.query_row("SELECT id FROM ai_investigations ORDER BY id LIMIT 1", [], |r| r.get(0)).optional().map_err(sql)?;
    let investigation_id = match first {
        Some(id) => id,
        None => {
            let stamp = now();
            conn.execute(
                "INSERT INTO ai_investigations(title,status,stage,created_at,updated_at) VALUES(?,?,?,?,?)",
                params!["General", "ACTIVE", "OBSERVE", stamp, stamp],
            )
            .map_err(sql)?;
            let id = conn.last_insert_rowid();
            conn.execute("UPDATE ai_chat_messages SET investigation_id=? WHERE investigation_id IS NULL", [id]).map_err(sql)?;
            conn.execute("UPDATE ai_observations SET investigation_id=? WHERE investigation_id IS NULL", [id]).map_err(sql)?;
            id
        }
    };
    settings::set(conn, "ai_active_investigation_id", &json!(investigation_id)).map_err(sql)?;
    match get_investigation(conn, Some(investigation_id))? {
        Some(found) => Ok(found),
        None => {
            let (a, b) = (now(), now());
            Ok(json!({"id": investigation_id, "title": "General", "status": "ACTIVE", "stage": "OBSERVE", "local_session_id": "", "summary": "", "created_at": a, "updated_at": b})
                .as_object()
                .cloned()
                .expect("object"))
        }
    }
}

pub fn set_active_investigation(conn: &Connection, investigation_id: i64) -> Result<Map<String, Value>> {
    let Some(found) = get_investigation(conn, Some(investigation_id))? else { return Err(ConfigError::value("Investigation not found")) };
    settings::set(conn, "ai_active_investigation_id", &json!(investigation_id)).map_err(sql)?;
    Ok(found)
}

/// What to change; nothing for a field means keep it.
#[derive(Default)]
pub struct InvestigationUpdate<'a> {
    pub title: Option<&'a str>,
    pub status: Option<&'a str>,
    pub stage: Option<&'a str>,
    pub summary: Option<&'a str>,
    pub local_session_id: Option<&'a str>,
}

pub fn update_investigation(conn: &Connection, now: &dyn Fn() -> String, investigation_id: i64, change: &InvestigationUpdate) -> Result<Map<String, Value>> {
    let Some(current) = get_investigation(conn, Some(investigation_id))? else { return Err(ConfigError::value("Investigation not found")) };
    let text = |key: &str| current.get(key).and_then(Value::as_str).unwrap_or("").to_string();
    let title = head(change.title.unwrap_or(&text("title")), 160);
    let status = upper(change.status.unwrap_or(&text("status")), 32);
    let stage = upper(change.stage.unwrap_or(&text("stage")), 32);
    let summary = head(change.summary.unwrap_or(&text("summary")), 5000);
    let session = head(change.local_session_id.unwrap_or(&text("local_session_id")), 200);
    conn.execute(
        "UPDATE ai_investigations SET title=?,status=?,stage=?,summary=?,local_session_id=?,updated_at=? WHERE id=?",
        params![title, status, stage, summary, session, now(), investigation_id],
    )
    .map_err(sql)?;
    Ok(get_investigation(conn, Some(investigation_id))?.unwrap_or(current))
}

/// `investigation_id or int(active_investigation()["id"])`.
fn investigation_or_active(conn: &Connection, now: &dyn Fn() -> String, investigation_id: Option<i64>) -> Result<i64> {
    match investigation_id.filter(|i| *i != 0) {
        Some(i) => Ok(i),
        None => Ok(active_investigation(conn, now)?.get("id").and_then(Value::as_i64).unwrap_or(0)),
    }
}

pub fn add_chat_message(
    conn: &Connection,
    now: &dyn Fn() -> String,
    conversation_id: Option<&str>,
    role: Option<&str>,
    content: Option<&str>,
    response_id: Option<&str>,
    investigation_id: Option<i64>,
) -> Result<i64> {
    let investigation = investigation_or_active(conn, now, investigation_id)?;
    conn.execute(
        "INSERT INTO ai_chat_messages(investigation_id,conversation_id,role,content,response_id,created_at) VALUES(?,?,?,?,?,?)",
        params![investigation, or_default(conversation_id, ""), head(&or_default(role, "user"), 32), or_default(content, ""), or_default(response_id, ""), now()],
    )
    .map_err(sql)?;
    Ok(conn.last_insert_rowid())
}

pub fn list_chat_messages(conn: &Connection, now: &dyn Fn() -> String, limit: i64, investigation_id: Option<i64>) -> Result<Vec<Value>> {
    let n = (if limit == 0 { 120 } else { limit }).clamp(1, 500);
    let investigation = investigation_or_active(conn, now, investigation_id)?;
    let mut found = rows(
        conn,
        "SELECT id,investigation_id,conversation_id,role,content,response_id,created_at FROM ai_chat_messages WHERE investigation_id=? ORDER BY id DESC LIMIT ?",
        params![investigation, n],
    )?;
    found.reverse();
    Ok(found.into_iter().map(Value::Object).collect())
}

pub fn clear_chat_messages(conn: &Connection, now: &dyn Fn() -> String, investigation_id: Option<i64>) -> Result<()> {
    let investigation = investigation_or_active(conn, now, investigation_id)?;
    conn.execute("DELETE FROM ai_chat_messages WHERE investigation_id=?", [investigation]).map_err(sql)?;
    Ok(())
}

pub fn add_observation(conn: &Connection, now: &dyn Fn() -> String, kind: Option<&str>, content: Option<&str>, investigation_id: Option<i64>) -> Result<i64> {
    let investigation = investigation_or_active(conn, now, investigation_id)?;
    conn.execute(
        "INSERT INTO ai_observations(investigation_id,kind,content,created_at) VALUES(?,?,?,?)",
        params![investigation, upper(&or_default(kind, "OBSERVATION"), 32), head(&or_default(content, ""), 20000), now()],
    )
    .map_err(sql)?;
    Ok(conn.last_insert_rowid())
}

pub fn list_observations(conn: &Connection, now: &dyn Fn() -> String, limit: i64, investigation_id: Option<i64>) -> Result<Vec<Value>> {
    let n = (if limit == 0 { 60 } else { limit }).clamp(1, 200);
    let investigation = investigation_or_active(conn, now, investigation_id)?;
    let mut found = rows(
        conn,
        "SELECT id,investigation_id,kind,content,created_at FROM ai_observations WHERE investigation_id=? ORDER BY id DESC LIMIT ?",
        params![investigation, n],
    )?;
    found.reverse();
    Ok(found.into_iter().map(Value::Object).collect())
}

/// One piece of evidence the assistant was given; recording it again updates its status, hash and summary.
pub struct Evidence<'a> {
    pub evidence_id: &'a str,
    pub investigation_id: Option<i64>,
    pub kind: Option<&'a str>,
    pub reason: Option<&'a str>,
    pub scope: Option<&'a str>,
    pub source_page: Option<&'a str>,
    pub source: Option<&'a str>,
    pub status: Option<&'a str>,
    pub sha256: Option<&'a str>,
    pub summary: Option<&'a Value>,
}

pub fn record_evidence_history(conn: &Connection, now: &dyn Fn() -> String, e: &Evidence) -> Result<()> {
    let stamp = now();
    let summary = match e.summary {
        Some(v) if py_truthy(v) => dumps(v, false),
        _ => "{}".to_string(),
    };
    conn.execute(
        "INSERT INTO ai_evidence_history(evidence_id,investigation_id,kind,reason,scope,source_page,source,status,sha256,summary_json,created_at,updated_at) \
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET status=excluded.status,sha256=excluded.sha256,\
         summary_json=excluded.summary_json,updated_at=excluded.updated_at",
        params![
            e.evidence_id,
            e.investigation_id,
            or_default(e.kind, ""),
            head(&or_default(e.reason, ""), 2000),
            head(&or_default(e.scope, ""), 500),
            head(&or_default(e.source_page, ""), 500),
            head(&or_default(e.source, ""), 120),
            head(&or_default(e.status, "STAGED"), 32),
            head(&or_default(e.sha256, ""), 128),
            summary,
            stamp,
            stamp
        ],
    )
    .map_err(sql)?;
    Ok(())
}

pub fn list_evidence_history(conn: &Connection, now: &dyn Fn() -> String, investigation_id: Option<i64>, limit: i64) -> Result<Vec<Value>> {
    let n = (if limit == 0 { 50 } else { limit }).clamp(1, 100);
    let investigation = investigation_or_active(conn, now, investigation_id)?;
    let found = rows(
        conn,
        "SELECT evidence_id,investigation_id,kind,reason,scope,source_page,source,status,sha256,summary_json,created_at,updated_at FROM ai_evidence_history WHERE investigation_id=? ORDER BY id DESC LIMIT ?",
        params![investigation, n],
    )?;
    Ok(found
        .into_iter()
        .map(|mut item| {
            let raw = item.shift_remove("summary_json").and_then(|v| v.as_str().map(String::from)).unwrap_or_default();
            let text = if raw.is_empty() { "{}".to_string() } else { raw };
            item.insert("summary".into(), serde_json::from_str(&text).unwrap_or_else(|_| json!({})));
            Value::Object(item)
        })
        .collect())
}
