//! Port of the job lifecycle writes of `telegram_archive/core/database.py`: `create_job`, `transition_job`, `update_job`,
//! `append_job_journal`, `add_job_dependencies` and `job_dependencies`.
//!
//! These functions take the connection of a transaction the caller has opened (`Writer::write` does that), so one call is one atomic step, as
//! in Python where each method locks, begins, and commits. Errors that Python raises as `ValueError` are `JobError::Invalid` with the same text.
//! The behaviour is checked against Python by `tests/job_ops.rs`, which replays operation sequences recorded from the Python original
//! (`tools/parity/gen_job_op_vectors.py`).

use std::collections::{HashMap, HashSet};

use rusqlite::{params, params_from_iter, Connection};
use serde_json::{Map, Value};

#[derive(Debug)]
pub enum JobError {
    /// What Python raises as `ValueError`.
    Invalid(String),
    /// What Python raises as `RuntimeError` (a job that already has an execution).
    Conflict(String),
    Sql(rusqlite::Error),
}

impl std::fmt::Display for JobError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JobError::Invalid(m) | JobError::Conflict(m) => f.write_str(m),
            JobError::Sql(e) => write!(f, "{e}"),
        }
    }
}
impl std::error::Error for JobError {}
impl From<rusqlite::Error> for JobError {
    fn from(e: rusqlite::Error) -> Self {
        JobError::Sql(e)
    }
}

pub(crate) fn invalid<T>(msg: String) -> Result<T, JobError> {
    Err(JobError::Invalid(msg))
}

pub const JOB_STATUSES: &[&str] =
    &["QUEUED", "RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERING"];

/// The statuses a job may move to from `from` (`Database.JOB_STATUS_TRANSITIONS`).
pub fn allowed_transitions(from: &str) -> &'static [&'static str] {
    match from {
        "QUEUED" => &["QUEUED", "RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED", "CANCELLED", "INTERRUPTED", "RECOVERING"],
        "RUNNING" => &["RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERING", "QUEUED"],
        "PAUSING" => &["PAUSING", "PAUSED", "RUNNING", "QUEUED", "CANCEL_REQUESTED", "CANCELLED", "INTERRUPTED", "RECOVERING"],
        "PAUSED" => &["PAUSED", "RUNNING", "QUEUED", "CANCEL_REQUESTED", "CANCELLED", "INTERRUPTED", "RECOVERING"],
        "CANCEL_REQUESTED" => &["CANCEL_REQUESTED", "CANCELLED", "FAILED", "INTERRUPTED", "RECOVERING"],
        "INTERRUPTED" => &["INTERRUPTED", "RECOVERING", "CANCELLED"],
        "RECOVERING" => &["RECOVERING", "QUEUED", "RUNNING", "FAILED", "CANCELLED", "INTERRUPTED"],
        "FAILED" => &["FAILED", "QUEUED"],
        "CANCELLED" => &["CANCELLED", "QUEUED"],
        "COMPLETED" => &["COMPLETED"],
        "COMPLETED_WITH_ERRORS" => &["COMPLETED_WITH_ERRORS"],
        _ => &[],
    }
}

/// `datetime.fromtimestamp(secs, utc).isoformat(timespec="seconds")`, e.g. `2026-11-20T11:19:23+00:00`.
pub fn iso_from_unix(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // civil-from-days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}+00:00", rem / 3600, (rem % 3600) / 60, rem % 60)
}

thread_local! {
    /// A fixed clock for the replay tests (seconds since the epoch); never set in the application.
    static CLOCK: std::cell::Cell<Option<i64>> = const { std::cell::Cell::new(None) };
}

/// Make `now_iso()` on this thread return a fixed moment (tests only); `None` goes back to the real clock.
pub fn set_clock_for_tests(secs: Option<i64>) {
    CLOCK.with(|c| c.set(secs));
}

/// `config.now_iso()`.
pub fn now_iso() -> String {
    if let Some(secs) = CLOCK.with(|c| c.get()) {
        return iso_from_unix(secs);
    }
    let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    iso_from_unix(secs)
}

pub(crate) fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    getrandom::getrandom(&mut buf).expect("operating system random numbers");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// `uuid.uuid4().hex`.
pub(crate) fn uuid4_hex() -> String {
    let mut b = [0u8; 16];
    getrandom::getrandom(&mut b).expect("operating system random numbers");
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// `json.dumps(value, ensure_ascii=False, separators=(",", ":"))`.
pub fn compact_json(value: &Value) -> String {
    serde_json::to_string(value).expect("a JSON value serialises")
}

/// Python's truthiness of a JSON value.
pub(crate) fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `type(value).__name__` of a decoded JSON value.
fn py_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// A job column value as Python's `sqlite3` binds it.
pub(crate) fn bind(v: &Value) -> Result<rusqlite::types::Value, JobError> {
    use rusqlite::types::Value as S;
    Ok(match v {
        Value::Null => S::Null,
        Value::Bool(b) => S::Integer(i64::from(*b)),
        Value::Number(n) => match n.as_i64() {
            Some(i) => S::Integer(i),
            None => S::Real(n.as_f64().unwrap_or(0.0)),
        },
        Value::String(s) => S::Text(s.clone()),
        Value::Array(_) | Value::Object(_) => return invalid("Error binding parameter: type 'list' or 'dict' is not supported".into()),
    })
}

/// A column name that came from a caller: letters, digits and underscores only (Python formats the names into the SQL text).
pub(crate) fn column(name: &str) -> Result<&str, JobError> {
    if !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        Ok(name)
    } else {
        invalid(format!("Invalid job column name: {name:?}"))
    }
}

/// What `append_job_journal` takes besides the job id and event type.
#[derive(Debug, Default, Clone)]
pub struct JournalEntry {
    pub status: Option<String>,
    pub from_status: Option<String>,
    pub checkpoint: Option<Value>,
    pub operation: Option<String>,
    pub detail: Option<String>,
    pub payload: Option<Value>,
    pub error_type: Option<String>,
    pub retry_count: Option<i64>,
    pub launch_id: Option<String>,
    pub created_at: Option<String>,
}

/// `append_job_journal`: one semantic event in the durable journal, numbered per job.
pub fn append_job_journal(c: &Connection, job_id: &str, event_type: &str, e: &JournalEntry) -> Result<(), JobError> {
    let ts = e.created_at.clone().unwrap_or_else(now_iso);
    let n: i64 = c.query_row("SELECT COALESCE(MAX(sequence),0)+1 AS n FROM job_journal WHERE job_id=?", [job_id], |r| r.get(0))?;
    let encode = |v: &Option<Value>| v.as_ref().map(compact_json);
    c.execute(
        "INSERT INTO job_journal (event_id,job_id,sequence,event_type,from_status,status,checkpoint,operation,detail,payload,error_type,retry_count,launch_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![uuid4_hex(), job_id, n, event_type, e.from_status, e.status, encode(&e.checkpoint), e.operation, e.detail, encode(&e.payload), e.error_type, e.retry_count, e.launch_id, ts],
    )?;
    Ok(())
}

/// The arguments of `create_job` after the job type.
#[derive(Debug, Clone)]
pub struct NewJob {
    pub source: String,
    pub destination: String,
    pub destructive: bool,
    pub detail: String,
    pub dry_run: bool,
    pub priority: Option<i64>,
    pub max_retries: i64,
    pub deadline_at: Option<String>,
    pub resource_requirements: Option<Value>,
}

impl Default for NewJob {
    fn default() -> Self {
        NewJob {
            source: String::new(),
            destination: String::new(),
            destructive: false,
            detail: String::new(),
            dry_run: false,
            priority: None,
            max_retries: 3,
            deadline_at: None,
            resource_requirements: None,
        }
    }
}

/// `create_job` without the dependency step: the job row and its `JOB_CREATED` journal entry. Returns the new id. (Python adds the
/// dependencies in a second step after committing the job; call `add_job_dependencies` for that, in its own transaction.)
pub fn create_job(c: &Connection, job_type: &str, job: &NewJob) -> Result<String, JobError> {
    let jid = random_hex(6); // uuid4().hex[:12]: the first 12 characters are all random bits
    let ts = now_iso();
    let priority = job.priority.map(|p| p.clamp(0, 100)).unwrap_or(50);
    let requirements = match &job.resource_requirements {
        Some(v) if truthy(v) => compact_json(v),
        _ => "{}".to_string(),
    };
    c.execute(
        "INSERT INTO jobs (id,job_type,source_channel,destination_channel,status,progress,current_item,detail,dry_run,error,destructive,created_at,updated_at,admission_state,admission_reason,next_run_at,priority,attempt,admission_updated_at,execution_id,worker_id,lease_id,lease_expires_at,last_heartbeat_at,worker_state,retry_count,max_retries,failure_class,deadline_at,resource_requirements) \
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![
            jid, job_type, job.source, job.destination, "QUEUED", 0, "", job.detail, i64::from(job.dry_run), "", i64::from(job.destructive), ts, ts, "READY", "",
            rusqlite::types::Null, priority, 0, ts, rusqlite::types::Null, rusqlite::types::Null, rusqlite::types::Null, rusqlite::types::Null, rusqlite::types::Null, "IDLE", 0,
            job.max_retries.max(0), "", job.deadline_at, requirements
        ],
    )?;
    let mut payload = Map::new();
    payload.insert("job_type".into(), Value::from(job_type));
    payload.insert("source".into(), Value::from(job.source.clone()));
    payload.insert("destination".into(), Value::from(job.destination.clone()));
    payload.insert("dry_run".into(), Value::Bool(job.dry_run));
    append_job_journal(
        c,
        &jid,
        "JOB_CREATED",
        &JournalEntry { status: Some("QUEUED".into()), detail: Some("Job created and queued".into()), payload: Some(Value::Object(payload)), ..Default::default() },
    )?;
    Ok(jid)
}

/// `transition_job`: a validated lifecycle change, with its `STATE_CHANGED` journal entry. `fields` are other job columns to set at the same time.
pub fn transition_job(c: &Connection, job_id: &str, status: &str, fields: &Map<String, Value>) -> Result<(), JobError> {
    let status = status.to_uppercase();
    let row: Option<(Option<String>, String)> = match c.query_row("SELECT status, job_type FROM jobs WHERE id=?", [job_id], |r| Ok((r.get(0)?, r.get(1)?))) {
        Ok(r) => Some(r),
        Err(rusqlite::Error::QueryReturnedNoRows) => None,
        Err(e) => return Err(e.into()),
    };
    let Some((current, job_type)) = row else { return invalid(format!("Job not found: {job_id}")) };
    let current = current.unwrap_or_default().to_uppercase();
    if !JOB_STATUSES.contains(&status.as_str()) {
        return invalid(format!("Unknown job lifecycle status: {status}"));
    }
    if !allowed_transitions(&current).contains(&status.as_str()) {
        return invalid(format!("Invalid job transition {current} -> {status}"));
    }
    let mut fields = fields.clone();
    fields.insert("status".into(), Value::String(status.clone()));
    fields.insert("updated_at".into(), Value::String(now_iso()));
    let mut sets = Vec::new();
    let mut values: Vec<rusqlite::types::Value> = Vec::new();
    for (k, v) in &fields {
        sets.push(format!("{}=?", column(k)?));
        values.push(bind(v)?);
    }
    values.push(rusqlite::types::Value::Text(job_id.to_string()));
    c.execute(&format!("UPDATE jobs SET {} WHERE id=?", sets.join(", ")), params_from_iter(values.iter()))?;
    let mut changed: Vec<&String> = fields.keys().filter(|k| !matches!(k.as_str(), "status" | "updated_at")).collect();
    changed.sort();
    let mut payload = Map::new();
    payload.insert("job_type".into(), Value::from(job_type));
    payload.insert("changed_fields".into(), Value::Array(changed.into_iter().map(|k| Value::from(k.clone())).collect()));
    let text = |k: &str| fields.get(k).and_then(|v| if v.is_null() { None } else { Some(v.as_str().map(str::to_string).unwrap_or_else(|| compact_json(v))) });
    let error_type = fields.get("error").filter(|v| truthy(v)).map(|v| py_type_name(v).to_string());
    append_job_journal(
        c,
        job_id,
        "STATE_CHANGED",
        &JournalEntry {
            from_status: Some(current),
            status: Some(status),
            operation: text("current_operation"),
            detail: text("detail"),
            payload: Some(Value::Object(payload)),
            error_type,
            ..Default::default()
        },
    )?;
    Ok(())
}

/// `update_job`: change job columns. A `status` among them is a lifecycle transition (validated, journalled); otherwise it is a plain update.
pub fn update_job(c: &Connection, job_id: &str, fields: &Map<String, Value>) -> Result<(), JobError> {
    let mut fields = fields.clone();
    if let Some(status) = fields.shift_remove("status") {
        let status = match &status {
            v if truthy(v) => v.as_str().map(str::to_string).unwrap_or_else(|| compact_json(v)),
            _ => String::new(),
        };
        return transition_job(c, job_id, &status, &fields);
    }
    fields.insert("updated_at".into(), Value::String(now_iso()));
    let mut sets = Vec::new();
    let mut values: Vec<rusqlite::types::Value> = Vec::new();
    for (k, v) in &fields {
        sets.push(format!("{}=?", column(k)?));
        values.push(bind(v)?);
    }
    values.push(rusqlite::types::Value::Text(job_id.to_string()));
    c.execute(&format!("UPDATE jobs SET {} WHERE id=?", sets.join(", ")), params_from_iter(values.iter()))?;
    Ok(())
}

/// `add_job_dependencies`: jobs that must finish first. Unknown jobs and cycles are refused.
pub fn add_job_dependencies(c: &Connection, job_id: &str, dependencies: &[String], dependency_type: &str) -> Result<(), JobError> {
    let mut unique: Vec<String> = Vec::new();
    for d in dependencies {
        if !d.is_empty() && d != job_id && !unique.contains(d) {
            unique.push(d.clone());
        }
    }
    if unique.is_empty() {
        return Ok(());
    }
    let ts = now_iso();
    let marks = vec!["?"; unique.len()].join(",");
    let found: HashSet<String> = {
        let mut st = c.prepare(&format!("SELECT id FROM jobs WHERE id IN ({marks})"))?;
        let rows = st.query_map(params_from_iter(unique.iter()), |r| r.get::<_, String>(0))?;
        rows.collect::<rusqlite::Result<_>>()?
    };
    for dep in &unique {
        if !found.contains(dep) {
            return invalid(format!("Dependency job not found: {dep}"));
        }
    }
    let mut graph: HashMap<String, Vec<String>> = HashMap::new();
    {
        let mut st = c.prepare("SELECT job_id,depends_on_job_id FROM job_dependencies")?;
        let rows = st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
        for row in rows {
            let (a, b) = row?;
            graph.entry(a).or_default().push(b);
        }
    }
    let kind = {
        let t = if dependency_type.is_empty() { "SUCCESS" } else { dependency_type };
        t.to_uppercase()
    };
    for dep in &unique {
        let mut frontier = vec![dep.clone()];
        let mut seen: HashSet<String> = HashSet::new();
        while let Some(node) = frontier.pop() {
            if node == job_id {
                return invalid(format!("Job dependency cycle detected: {job_id} -> {dep}"));
            }
            if !seen.insert(node.clone()) {
                continue;
            }
            if let Some(next) = graph.get(&node) {
                frontier.extend(next.iter().cloned());
            }
        }
        c.execute(
            "INSERT OR IGNORE INTO job_dependencies(job_id,depends_on_job_id,dependency_type,created_at) VALUES(?,?,?,?)",
            params![job_id, dep, kind, ts],
        )?;
    }
    Ok(())
}

/// One row of `job_dependencies`: the edge and the state of the job depended on.
#[derive(Debug, Clone, PartialEq)]
pub struct Dependency {
    pub job_id: String,
    pub depends_on_job_id: String,
    pub dependency_type: String,
    pub created_at: String,
    pub dependency_status: Option<String>,
}

/// `job_dependencies`.
pub fn job_dependencies(c: &Connection, job_id: &str) -> Result<Vec<Dependency>, rusqlite::Error> {
    let mut st = c.prepare("SELECT d.job_id, d.depends_on_job_id, d.dependency_type, d.created_at, j.status AS dependency_status FROM job_dependencies d LEFT JOIN jobs j ON j.id=d.depends_on_job_id WHERE d.job_id=? ORDER BY d.created_at")?;
    let rows = st.query_map([job_id], |r| {
        Ok(Dependency { job_id: r.get(0)?, depends_on_job_id: r.get(1)?, dependency_type: r.get(2)?, created_at: r.get(3)?, dependency_status: r.get(4)? })
    })?;
    rows.collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_matches_pythons_isoformat() {
        assert_eq!(iso_from_unix(0), "1970-01-01T00:00:00+00:00");
        assert_eq!(iso_from_unix(1_795_148_003), "2026-11-20T04:13:23+00:00");
        assert_eq!(iso_from_unix(951_782_400), "2000-02-29T00:00:00+00:00");
        assert_eq!(iso_from_unix(-1), "1969-12-31T23:59:59+00:00");
        assert_eq!(iso_from_unix(4_102_444_800), "2100-01-01T00:00:00+00:00");
    }

    #[test]
    fn transitions_follow_the_table() {
        assert!(allowed_transitions("QUEUED").contains(&"RUNNING"));
        assert!(!allowed_transitions("COMPLETED").contains(&"QUEUED"));
        assert!(allowed_transitions("FAILED").contains(&"QUEUED"));
        assert!(allowed_transitions("NOPE").is_empty());
    }

    #[test]
    fn column_names_are_checked() {
        assert!(column("current_operation").is_ok());
        assert!(column("x; DROP TABLE jobs").is_err());
        assert!(column("").is_err());
    }
}
