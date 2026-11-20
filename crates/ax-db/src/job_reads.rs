//! Port of the job queries of `telegram_archive/core/database.py`: `jobs`, `active_jobs`, `job_dashboard_snapshot`, `get_job`,
//! `latest_job_activity_id`, `recent_job_activity`, `get_job_transfer_telemetry`, `get_job_checkpoint` and `save_job_checkpoint`.
//!
//! Python flushes its buffer of log events before reading the activity table; here only what is stored is read, which is the same whenever
//! Rust is the only writer, and a reader that runs beside the Python process sees events that Python has not flushed yet only after it does.
//! Checked against recorded Python behaviour by `tests/job_read_ops.rs`.

use rusqlite::{params, params_from_iter, Connection};
use serde_json::{Map, Value};

use crate::jobs::{allowed_transitions, append_job_journal, invalid, now_iso, JobError, JournalEntry, JOB_STATUSES};

fn rows_as_maps(c: &Connection, sql: &str, p: impl rusqlite::Params) -> rusqlite::Result<Vec<Map<String, Value>>> {
    let mut st = c.prepare(sql)?;
    let names: Vec<String> = st.column_names().map(|s| s.to_string()).collect();
    let rows = st.query_map(p, |r| {
        let mut m = Map::new();
        for (i, n) in names.iter().enumerate() {
            m.insert(
                n.clone(),
                match r.get_ref(i)? {
                    rusqlite::types::ValueRef::Null => Value::Null,
                    rusqlite::types::ValueRef::Integer(v) => Value::from(v),
                    rusqlite::types::ValueRef::Real(v) => Value::from(v),
                    rusqlite::types::ValueRef::Text(t) => Value::String(String::from_utf8_lossy(t).to_string()),
                    rusqlite::types::ValueRef::Blob(_) => Value::Null,
                },
            );
        }
        Ok(m)
    })?;
    rows.collect()
}

/// `jobs(limit)`: the newest jobs.
pub fn jobs(c: &Connection, limit: i64) -> rusqlite::Result<Vec<Map<String, Value>>> {
    rows_as_maps(c, "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", params![limit])
}

pub const ACTIVE_STATUSES: &str = "'QUEUED','RUNNING','PAUSING','PAUSED','CANCEL_REQUESTED','INTERRUPTED','RECOVERING'";

/// `active_jobs`: jobs still to do or in progress, oldest first (never history).
pub fn active_jobs(c: &Connection, job_type: Option<&str>) -> rusqlite::Result<Vec<Map<String, Value>>> {
    match job_type.filter(|t| !t.is_empty()) {
        Some(t) => rows_as_maps(c, &format!("SELECT * FROM jobs WHERE status IN ({ACTIVE_STATUSES}) AND job_type=? ORDER BY created_at"), params![t]),
        None => rows_as_maps(c, &format!("SELECT * FROM jobs WHERE status IN ({ACTIVE_STATUSES}) ORDER BY created_at"), []),
    }
}

/// `job_dashboard_snapshot`: the active jobs, and the latest finished job of each type, as JSON `[active, last]`.
pub fn job_dashboard_snapshot(c: &Connection) -> rusqlite::Result<Value> {
    let active = rows_as_maps(c, &format!("SELECT * FROM jobs WHERE status IN ({ACTIVE_STATUSES}) ORDER BY created_at"), [])?;
    let terminal = rows_as_maps(
        c,
        "SELECT j.job_type, j.status, j.error, j.updated_at, j.completed, j.total FROM jobs j \
         JOIN (SELECT job_type, MAX(updated_at) mu FROM jobs WHERE status IN ('COMPLETED','COMPLETED_WITH_ERRORS','FAILED','CANCELLED') GROUP BY job_type) m \
         ON m.job_type=j.job_type AND m.mu=j.updated_at WHERE j.status IN ('COMPLETED','COMPLETED_WITH_ERRORS','FAILED','CANCELLED')",
        [],
    )?;
    let mut last = Map::new();
    for r in terminal {
        let key = r.get("job_type").and_then(Value::as_str).unwrap_or("").to_string();
        last.insert(key, Value::Object(r)); // a later row of the same type replaces an earlier one, as in a dict comprehension
    }
    Ok(Value::Array(vec![Value::Array(active.into_iter().map(Value::Object).collect()), Value::Object(last)]))
}

/// `get_job`: the job row with its latest 60 activity messages, its dependencies, its last 10 executions, and `resource_requirements` decoded.
pub fn get_job(c: &Connection, job_id: &str) -> rusqlite::Result<Option<Map<String, Value>>> {
    let Some(mut d) = rows_as_maps(c, "SELECT * FROM jobs WHERE id=?", params![job_id])?.into_iter().next() else { return Ok(None) };
    let mut events: Vec<Value> = rows_as_maps(c, "SELECT message FROM job_activity_events WHERE job_id=? ORDER BY id DESC LIMIT 60", params![job_id])?
        .into_iter()
        .map(|r| r.get("message").cloned().unwrap_or(Value::Null))
        .collect();
    events.reverse();
    d.insert("events".into(), Value::Array(events));
    let deps = rows_as_maps(
        c,
        "SELECT d.job_id,d.depends_on_job_id,d.dependency_type,d.created_at,j.status AS dependency_status FROM job_dependencies d LEFT JOIN jobs j ON j.id=d.depends_on_job_id WHERE d.job_id=? ORDER BY d.created_at",
        params![job_id],
    )?;
    let executions = rows_as_maps(c, "SELECT * FROM job_executions WHERE job_id=? ORDER BY attempt DESC LIMIT 10", params![job_id])?;
    d.insert("dependencies".into(), Value::Array(deps.into_iter().map(Value::Object).collect()));
    d.insert("executions".into(), Value::Array(executions.into_iter().map(Value::Object).collect()));
    // json.loads(d.get("resource_requirements") or "{}"); text that is not JSON stays as it is
    let raw = d.get("resource_requirements").cloned().unwrap_or(Value::Null);
    let text = match &raw {
        Value::String(s) if !s.is_empty() => s.clone(),
        _ => "{}".to_string(),
    };
    if let Ok(v) = serde_json::from_str::<Value>(&text) {
        d.insert("resource_requirements".into(), v);
    }
    Ok(Some(d))
}

/// The ids of a call: a list without blanks and without `-` and `None`, no repeats, at most 800.
fn clean_ids(ids: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for x in ids {
        if x.trim().is_empty() || matches!(x.as_str(), "" | "-" | "None") || out.contains(x) {
            continue;
        }
        out.push(x.clone());
    }
    out.truncate(800);
    out
}

/// `latest_job_activity_id`.
pub fn latest_job_activity_id(c: &Connection, job_id: &str) -> rusqlite::Result<i64> {
    let jid = job_id.trim();
    if jid.is_empty() {
        return Ok(0);
    }
    c.query_row("SELECT COALESCE(MAX(id),0) AS latest_id FROM job_activity_events WHERE job_id=?", [jid], |r| r.get::<_, Option<i64>>(0))
        .map(|v| v.unwrap_or(0))
}

/// `recent_job_activity`: a page of the timeline, oldest first, optionally only events before an id.
pub fn recent_job_activity(c: &Connection, job_ids: &[String], limit: i64, before_id: Option<i64>) -> rusqlite::Result<Vec<Value>> {
    let ids = clean_ids(job_ids);
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let limit = (if limit == 0 { 200 } else { limit }).clamp(1, 500);
    let marks = vec!["?"; ids.len()].join(",");
    let mut sql_where = format!("job_id IN ({marks})");
    let mut values: Vec<rusqlite::types::Value> = ids.iter().map(|i| rusqlite::types::Value::Text(i.clone())).collect();
    if let Some(b) = before_id {
        sql_where.push_str(" AND id < ?");
        values.push(rusqlite::types::Value::Integer(b.max(0)));
    }
    values.push(rusqlite::types::Value::Integer(limit));
    let rows = rows_as_maps(
        c,
        &format!("SELECT id,job_id,level,event,operation,message,details,created_at FROM job_activity_events WHERE {sql_where} ORDER BY id DESC LIMIT ?"),
        params_from_iter(values.iter()),
    )?;
    Ok(rows
        .into_iter()
        .rev()
        .map(|mut item| {
            let raw = item.get("details").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("{}").to_string();
            let details = serde_json::from_str::<Value>(&raw).unwrap_or_else(|_| {
                let mut m = Map::new();
                m.insert("raw".into(), Value::String(item.get("details").and_then(Value::as_str).unwrap_or("").chars().take(2000).collect()));
                Value::Object(m)
            });
            item.insert("details".into(), details);
            Value::Object(item)
        })
        .collect())
}

/// `get_job_transfer_telemetry`: the latest telemetry of each job given, with its `updated_at` merged in.
pub fn get_job_transfer_telemetry(c: &Connection, job_ids: &[String]) -> rusqlite::Result<Map<String, Value>> {
    let ids = clean_ids(job_ids);
    let mut out = Map::new();
    if ids.is_empty() {
        return Ok(out);
    }
    let marks = vec!["?"; ids.len()].join(",");
    for row in rows_as_maps(c, &format!("SELECT job_id,telemetry,updated_at FROM job_transfer_telemetry WHERE job_id IN ({marks})"), params_from_iter(ids.iter()))? {
        let raw = row.get("telemetry").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("{}");
        let Ok(Value::Object(mut t)) = serde_json::from_str::<Value>(raw) else { continue };
        t.insert("updated_at".into(), row.get("updated_at").cloned().unwrap_or(Value::Null));
        out.insert(row.get("job_id").and_then(Value::as_str).unwrap_or("").to_string(), Value::Object(t));
    }
    Ok(out)
}

/// `get_job_checkpoint`: the latest checkpoint of a job, with its payload decoded when it is JSON.
pub fn get_job_checkpoint(c: &Connection, job_id: &str) -> rusqlite::Result<Option<Map<String, Value>>> {
    let Some(mut r) = rows_as_maps(c, "SELECT * FROM job_checkpoints WHERE job_id=?", params![job_id])?.into_iter().next() else { return Ok(None) };
    if let Some(Value::String(raw)) = r.get("payload").cloned() {
        if !raw.is_empty() {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                r.insert("payload".into(), v);
            }
        }
    }
    Ok(Some(r))
}

/// The arguments of `save_job_checkpoint` after the job id.
#[derive(Debug, Default, Clone)]
pub struct Checkpoint {
    pub payload: Option<Value>,
    pub stage: Option<String>,
    pub item_id: Option<String>,
    pub item_index: Option<i64>,
    pub total_items: Option<i64>,
    pub current_operation: Option<String>,
    pub last_completed_operation: Option<String>,
    pub input_fingerprint: Option<String>,
    pub output_fingerprint: Option<String>,
    pub retry_count: Option<i64>,
    /// Job columns to change in the same transaction; a `status` among them is a validated, journalled transition.
    pub update_fields: Map<String, Value>,
}

/// `save_job_checkpoint`.
pub fn save_job_checkpoint(c: &Connection, job_id: &str, cp: &Checkpoint) -> Result<(), JobError> {
    let ts = now_iso();
    let payload = cp.payload.as_ref().map(crate::jobs::compact_json);
    let mut fields = cp.update_fields.clone();
    fields.shift_remove("updated_at");
    let row: Option<(Option<String>, Option<String>)> = match c.query_row("SELECT status,job_type FROM jobs WHERE id=?", [job_id], |r| Ok((r.get(0)?, r.get(1)?))) {
        Ok(r) => Some(r),
        Err(rusqlite::Error::QueryReturnedNoRows) => None,
        Err(e) => return Err(e.into()),
    };
    let Some((status, job_type)) = row else { return invalid(format!("Job not found: {job_id}")) };
    let current = status.unwrap_or_default().to_uppercase();
    // fields.pop("status", None): absent and null are the same; any other falsy value becomes "" (an unknown status)
    let requested: Option<String> = match fields.shift_remove("status") {
        None | Some(Value::Null) => None,
        Some(v) if !crate::jobs::truthy(&v) => Some(String::new()),
        Some(v) => Some(v.as_str().map(str::to_uppercase).unwrap_or_else(|| crate::jobs::compact_json(&v).to_uppercase())),
    };
    if let Some(r) = &requested {
        if !JOB_STATUSES.contains(&r.as_str()) {
            return invalid(format!("Unknown job lifecycle status: {r}"));
        }
        if !allowed_transitions(&current).contains(&r.as_str()) {
            return invalid(format!("Invalid job transition {current} -> {r}"));
        }
        fields.insert("status".into(), Value::String(r.clone()));
    }
    if !fields.is_empty() {
        fields.insert("updated_at".into(), Value::String(ts.clone()));
        let mut sets = Vec::new();
        let mut values: Vec<rusqlite::types::Value> = Vec::new();
        for (k, v) in &fields {
            sets.push(format!("{}=?", crate::jobs::column(k)?));
            values.push(crate::jobs::bind(v)?);
        }
        values.push(rusqlite::types::Value::Text(job_id.to_string()));
        c.execute(&format!("UPDATE jobs SET {} WHERE id=?", sets.join(", ")), params_from_iter(values.iter()))?;
    }
    if let Some(r) = requested.filter(|r| *r != current) {
        let mut changed: Vec<&String> = fields.keys().filter(|k| !matches!(k.as_str(), "status" | "updated_at")).collect();
        changed.sort();
        let mut payload = Map::new();
        payload.insert("job_type".into(), job_type.map(Value::from).unwrap_or(Value::Null));
        payload.insert("changed_fields".into(), Value::Array(changed.into_iter().map(|k| Value::from(k.clone())).collect()));
        let text = |k: &str| {
            fields
                .get(k)
                .and_then(|v| if v.is_null() { None } else { Some(v.as_str().map(str::to_string).unwrap_or_else(|| crate::jobs::compact_json(v))) })
        };
        let error_type = fields.get("error").filter(|v| crate::jobs::truthy(v)).map(|v| crate::jobs::py_type_name(v).to_string());
        append_job_journal(
            c,
            job_id,
            "STATE_CHANGED",
            &JournalEntry {
                from_status: Some(current),
                status: Some(r),
                operation: text("current_operation"),
                detail: text("detail"),
                payload: Some(Value::Object(payload)),
                error_type,
                ..Default::default()
            },
        )?;
    }
    let cut = |v: &Option<String>, n: usize| v.as_ref().map(|s| s.chars().take(n).collect::<String>());
    c.execute(
        "INSERT INTO job_checkpoints (job_id,stage,item_id,item_index,total_items,current_operation,last_completed_operation,input_fingerprint,output_fingerprint,retry_count,payload,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) \
         ON CONFLICT(job_id) DO UPDATE SET stage=excluded.stage,item_id=excluded.item_id,item_index=excluded.item_index,total_items=excluded.total_items,current_operation=excluded.current_operation,last_completed_operation=excluded.last_completed_operation,input_fingerprint=excluded.input_fingerprint,output_fingerprint=excluded.output_fingerprint,retry_count=excluded.retry_count,payload=excluded.payload,updated_at=excluded.updated_at",
        params![job_id, cp.stage, cut(&cp.item_id, 300), cp.item_index, cp.total_items, cut(&cp.current_operation, 200), cut(&cp.last_completed_operation, 200), cp.input_fingerprint, cp.output_fingerprint, cp.retry_count.unwrap_or(0), payload, ts],
    )?;
    Ok(())
}
