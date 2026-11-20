//! Port of the execution, admission, execution-lane and resource-lease writes of `telegram_archive/core/database.py`:
//! `acquire_job_execution`, `interrupt_stale_job_execution`, `heartbeat_job_execution`, `finish_job_execution`, `expired_job_executions`,
//! `set_job_retry_policy`, `set_job_admission`, `job_orchestration_snapshot`, the `*_execution_lane_slots` family and the `*_resource_lease*` family.
//!
//! Like `jobs`, these take the connection of a transaction the caller opened. They also take the time as a `Now`, because Python's
//! timestamps here are written in two forms that sort differently against each other: `datetime.isoformat()` (microseconds when there are
//! any) for lease times and `config.now_iso()` (whole seconds) for the rest, and lease expiry is a string comparison between them.
//! Checked against recorded Python behaviour by `tests/lease_ops.rs`.

use std::collections::{BTreeMap, BTreeSet};

use rusqlite::{params, params_from_iter, Connection};
use serde_json::{Map, Value};

use crate::jobs::{append_job_journal, invalid, iso_from_unix, update_job, uuid4_hex, JobError, JournalEntry};

/// A moment, as Python's `datetime.now(timezone.utc)` has it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Now {
    pub secs: i64,
    pub micros: u32,
}

impl Now {
    pub fn current() -> Now {
        let d = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        Now { secs: d.as_secs() as i64, micros: d.subsec_micros() }
    }

    pub fn plus_seconds(self, s: i64) -> Now {
        Now { secs: self.secs + s, micros: self.micros }
    }

    /// `datetime.isoformat()`: the fraction is written only when it is not zero.
    pub fn isoformat(self) -> String {
        let base = iso_from_unix(self.secs);
        if self.micros == 0 {
            base
        } else {
            // "2026-11-20T11:19:23+00:00" -> "2026-11-20T11:19:23.123456+00:00"
            format!("{}.{:06}{}", &base[..19], self.micros, &base[19..])
        }
    }

    /// `config.now_iso()`: `isoformat(timespec="seconds")`.
    pub fn iso_seconds(self) -> String {
        iso_from_unix(self.secs)
    }
}

/// `timedelta(seconds=max(5, int(lease_seconds)))`.
fn lease(now: Now, lease_seconds: i64) -> (String, String) {
    (now.isoformat(), now.plus_seconds(lease_seconds.max(5)).isoformat())
}

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

/// A journal entry stamped with Python's `config.now_iso()` of the same moment.
fn journal(c: &Connection, now: Now, job_id: &str, event: &str, mut entry: JournalEntry) -> Result<(), JobError> {
    entry.created_at = Some(now.iso_seconds());
    append_job_journal(c, job_id, event, &entry)
}

// --- job executions ---------------------------------------------------------------------------------------------------------------

/// What `acquire_job_execution` returns.
#[derive(Debug, Clone, PartialEq)]
pub struct Execution {
    pub execution_id: String,
    pub lease_id: String,
    pub attempt: i64,
    pub lease_expires_at: String,
}

/// `acquire_job_execution`: give a job a worker, a lease and an execution record, and bind its reserved resources to it, in one step.
pub fn acquire_job_execution(c: &Connection, now: Now, job_id: &str, worker_id: &str, lease_seconds: i64) -> Result<Execution, JobError> {
    let (ts, exp) = lease(now, lease_seconds);
    let (execution_id, lease_id) = (uuid4_hex(), uuid4_hex());
    let row: Option<(Option<String>, Option<i64>, Option<String>)> =
        match c.query_row("SELECT status,attempt,execution_id FROM jobs WHERE id=?", [job_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))) {
            Ok(r) => Some(r),
            Err(rusqlite::Error::QueryReturnedNoRows) => None,
            Err(e) => return Err(e.into()),
        };
    let Some((status, attempt, existing)) = row else { return invalid(format!("Job not found: {job_id}")) };
    if existing.as_deref().is_some_and(|e| !e.is_empty()) {
        return Err(JobError::Conflict(format!("Job already has active execution: {job_id}")));
    }
    let attempt = attempt.unwrap_or(0) + 1;
    c.execute(
        "UPDATE jobs SET execution_id=?,worker_id=?,lease_id=?,lease_expires_at=?,last_heartbeat_at=?,worker_state='STARTING',attempt=?,updated_at=? WHERE id=?",
        params![execution_id, worker_id, lease_id, exp, ts, attempt, ts, job_id],
    )?;
    c.execute(
        "INSERT INTO job_executions(execution_id,job_id,attempt,worker_id,lease_id,status,started_at,heartbeat_at,lease_expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
        params![execution_id, job_id, attempt, worker_id, lease_id, "RUNNING", ts, ts, exp],
    )?;
    c.execute(
        "UPDATE resource_leases SET execution_id=?,lease_id=?,state='ACTIVE',expires_at=?,last_heartbeat=?,updated_at=? WHERE job_id=? AND state='RESERVED'",
        params![execution_id, lease_id, exp, ts, ts, job_id],
    )?;
    let mut payload = Map::new();
    payload.insert("execution_id".into(), Value::from(execution_id.clone()));
    payload.insert("worker_id".into(), Value::from(worker_id));
    payload.insert("lease_id".into(), Value::from(lease_id.clone()));
    payload.insert("attempt".into(), Value::from(attempt));
    journal(
        c,
        now,
        job_id,
        "EXECUTION_STARTED",
        JournalEntry { status, operation: Some("Worker execution started".into()), payload: Some(Value::Object(payload)), ..Default::default() },
    )?;
    Ok(Execution { execution_id, lease_id, attempt, lease_expires_at: exp })
}

/// `interrupt_stale_job_execution`: invalidate the previous launch's ownership of a job's execution, resources and lanes. Returns the execution id.
pub fn interrupt_stale_job_execution(c: &Connection, now: Now, job_id: &str) -> Result<Option<String>, JobError> {
    let ts = now.iso_seconds();
    let execution_id: String = match c.query_row("SELECT execution_id FROM jobs WHERE id=?", [job_id], |r| r.get::<_, Option<String>>(0)) {
        Ok(v) => v.unwrap_or_default(),
        Err(rusqlite::Error::QueryReturnedNoRows) => String::new(),
        Err(e) => return Err(e.into()),
    };
    if !execution_id.is_empty() {
        c.execute(
            "UPDATE job_executions SET status='INTERRUPTED',ended_at=?,failure_class=COALESCE(NULLIF(failure_class,''),'UNKNOWN') WHERE execution_id=? AND ended_at IS NULL",
            params![ts, execution_id],
        )?;
        c.execute(
            "UPDATE resource_leases SET state='EXPIRED',expires_at=NULL,updated_at=? WHERE execution_id=? AND state IN ('RESERVED','ACTIVE','RELEASING')",
            params![ts, execution_id],
        )?;
        c.execute(
            "UPDATE execution_lane_slots SET state='RELEASED',expires_at=NULL WHERE execution_id=? AND state IN ('ACTIVE','DRAINING')",
            params![execution_id],
        )?;
        c.execute(
            "UPDATE jobs SET execution_id=NULL,worker_id=NULL,lease_id=NULL,lease_expires_at=NULL,last_heartbeat_at=NULL,worker_state='INTERRUPTED',updated_at=? WHERE id=? AND execution_id=?",
            params![ts, job_id, execution_id],
        )?;
    }
    Ok(if execution_id.is_empty() { None } else { Some(execution_id) })
}

/// `heartbeat_job_execution`: renew the lease. `false` when the job no longer has that execution.
pub fn heartbeat_job_execution(c: &Connection, now: Now, job_id: &str, execution_id: &str, worker_state: &str, lease_seconds: i64) -> Result<bool, JobError> {
    let (ts, exp) = lease(now, lease_seconds);
    let state = if worker_state.is_empty() { "RUNNING" } else { worker_state };
    let changed = c.execute(
        "UPDATE jobs SET last_heartbeat_at=?,lease_expires_at=?,worker_state=?,updated_at=? WHERE id=? AND execution_id=?",
        params![ts, exp, state, ts, job_id, execution_id],
    )?;
    c.execute("UPDATE job_executions SET heartbeat_at=?,lease_expires_at=?,status=? WHERE execution_id=?", params![ts, exp, "RUNNING", execution_id])?;
    c.execute(
        "UPDATE resource_leases SET expires_at=?,last_heartbeat=?,updated_at=? WHERE execution_id=? AND state='ACTIVE'",
        params![exp, ts, ts, execution_id],
    )?;
    Ok(changed > 0)
}

/// `finish_job_execution`: close the execution record and release the resources and lanes it held.
pub fn finish_job_execution(c: &Connection, now: Now, job_id: &str, execution_id: &str, status: &str, failure_class: &str) -> Result<(), JobError> {
    let ts = now.iso_seconds();
    c.execute("UPDATE job_executions SET status=?,ended_at=?,failure_class=? WHERE execution_id=?", params![status, ts, failure_class, execution_id])?;
    c.execute(
        "UPDATE resource_leases SET state='RELEASED',expires_at=NULL,updated_at=? WHERE execution_id=? AND state IN ('RESERVED','ACTIVE','RELEASING')",
        params![ts, execution_id],
    )?;
    c.execute(
        "UPDATE execution_lane_slots SET state='RELEASED',expires_at=NULL WHERE execution_id=? AND state IN ('ACTIVE','DRAINING')",
        params![execution_id],
    )?;
    c.execute(
        "UPDATE jobs SET execution_id=NULL,worker_id=NULL,lease_id=NULL,lease_expires_at=NULL,last_heartbeat_at=NULL,worker_state=?,failure_class=?,updated_at=? WHERE id=? AND execution_id=?",
        params!["EXITED", failure_class, ts, job_id, execution_id],
    )?;
    Ok(())
}

/// `expired_job_executions`: the jobs whose lease ran out (a string comparison with `config.now_iso()`).
pub fn expired_job_executions(c: &Connection, now: Now) -> rusqlite::Result<Vec<Map<String, Value>>> {
    rows_as_maps(c, "SELECT * FROM jobs WHERE execution_id IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_expires_at < ?", params![now.iso_seconds()])
}

/// The arguments of `set_job_retry_policy` that were given.
#[derive(Debug, Default, Clone)]
pub struct RetryPolicy {
    pub retry_count: Option<i64>,
    pub max_retries: Option<i64>,
    pub failure_class: Option<String>,
    /// `Some(None)` sets it to NULL; `None` leaves it (unless `retry_count` was given, which sets it either way).
    pub next_run_at: Option<Option<String>>,
}

/// `set_job_retry_policy`.
pub fn set_job_retry_policy(c: &Connection, job_id: &str, p: &RetryPolicy) -> Result<(), JobError> {
    let mut fields = Map::new();
    if let Some(n) = p.retry_count {
        fields.insert("retry_count".into(), Value::from(n.max(0)));
    }
    if let Some(n) = p.max_retries {
        fields.insert("max_retries".into(), Value::from(n.max(0)));
    }
    if let Some(f) = &p.failure_class {
        fields.insert("failure_class".into(), Value::from(f.chars().take(80).collect::<String>()));
    }
    if p.next_run_at.is_some() || p.retry_count.is_some() {
        let v = p.next_run_at.clone().flatten().map(Value::from).unwrap_or(Value::Null);
        fields.insert("next_run_at".into(), v);
    }
    if fields.is_empty() {
        return Ok(());
    }
    update_job(c, job_id, &fields)
}

/// The arguments of `set_job_admission` after the state.
#[derive(Debug, Default, Clone)]
pub struct Admission {
    pub reason: String,
    pub next_run_at: Option<String>,
    pub priority: Option<i64>,
    pub operation: Option<String>,
    pub detail: Option<String>,
    pub payload: Option<Value>,
}

/// `set_job_admission`: store the orchestrator's decision and journal it when something changed.
pub fn set_job_admission(c: &Connection, now: Now, job_id: &str, state: &str, a: &Admission) -> Result<(), JobError> {
    let state = if state.is_empty() { "READY".to_string() } else { state.to_uppercase() };
    if !["READY", "WAITING", "BLOCKED", "DELAYED"].contains(&state.as_str()) {
        return invalid(format!("Unknown job admission state: {state}"));
    }
    let ts = now.iso_seconds();
    type Row = (Option<String>, Option<String>, Option<String>, Option<String>, Option<String>, Option<i64>);
    let row: Option<Row> = match c.query_row("SELECT status,job_type,admission_state,admission_reason,next_run_at,priority FROM jobs WHERE id=?", [job_id], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?))
    }) {
        Ok(r) => Some(r),
        Err(rusqlite::Error::QueryReturnedNoRows) => None,
        Err(e) => return Err(e.into()),
    };
    let Some((status, job_type, old_state, old_reason, old_next, old_priority)) = row else { return invalid(format!("Job not found: {job_id}")) };
    let priority = a.priority.map(|p| p.clamp(0, 100));
    let mut changed = old_state.as_deref().filter(|s| !s.is_empty()).unwrap_or("READY").to_uppercase() != state || old_reason.clone().unwrap_or_default() != a.reason;
    changed = changed || old_next != a.next_run_at;
    if let Some(p) = priority {
        changed = changed || old_priority.filter(|v| *v != 0).unwrap_or(50) != p;
    }
    let mut fields = Map::new();
    fields.insert("admission_state".into(), Value::from(state.clone()));
    fields.insert("admission_reason".into(), Value::from(a.reason.clone()));
    fields.insert("next_run_at".into(), a.next_run_at.clone().map(Value::from).unwrap_or(Value::Null));
    fields.insert("admission_updated_at".into(), Value::from(ts.clone()));
    fields.insert("updated_at".into(), Value::from(ts));
    if let Some(op) = a.operation.as_deref().filter(|o| !o.is_empty()) {
        fields.insert("current_operation".into(), Value::from(op.chars().take(300).collect::<String>()));
    }
    if let Some(p) = priority {
        fields.insert("priority".into(), Value::from(p));
    }
    let mut sets = Vec::new();
    let mut values: Vec<rusqlite::types::Value> = Vec::new();
    for (k, v) in &fields {
        sets.push(format!("{}=?", crate::jobs::column(k)?));
        values.push(crate::jobs::bind(v)?);
    }
    values.push(rusqlite::types::Value::Text(job_id.to_string()));
    c.execute(&format!("UPDATE jobs SET {} WHERE id=?", sets.join(", ")), params_from_iter(values.iter()))?;
    if changed {
        let mut payload = Map::new();
        payload.insert("job_type".into(), job_type.map(Value::from).unwrap_or(Value::Null));
        payload.insert("admission_state".into(), Value::from(state.clone()));
        payload.insert("admission_reason".into(), Value::from(a.reason.clone()));
        payload.insert("next_run_at".into(), a.next_run_at.clone().map(Value::from).unwrap_or(Value::Null));
        if let Some(p) = priority {
            payload.insert("priority".into(), Value::from(p));
        }
        let operation = a.operation.clone().filter(|o| !o.is_empty()).or_else(|| old_state.clone()).or(Some(String::new()));
        let detail = a.detail.clone().filter(|d| !d.is_empty()).unwrap_or_else(|| a.reason.clone());
        // (the caller's own `payload` argument is accepted by Python and not used)
        let _ = &a.payload;
        journal(
            c,
            now,
            job_id,
            "ADMISSION_CHANGED",
            JournalEntry { status, operation, detail: Some(detail), payload: Some(Value::Object(payload)), ..Default::default() },
        )?;
    }
    Ok(())
}

/// `job_orchestration_snapshot`: counts for the orchestration panel.
pub fn job_orchestration_snapshot(c: &Connection) -> rusqlite::Result<Value> {
    let mut states: BTreeMap<String, i64> = ["READY", "WAITING", "BLOCKED", "DELAYED"].iter().map(|s| (s.to_string(), 0)).collect();
    // Python's dicts keep the order keys were added in: the four states first, then any other
    let mut order: Vec<String> = states.keys().cloned().collect::<Vec<_>>();
    order.sort_by_key(|k| ["READY", "WAITING", "BLOCKED", "DELAYED"].iter().position(|s| s == k).unwrap_or(9));
    let mut reasons: Vec<(String, i64)> = Vec::new();
    for r in rows_as_maps(c, "SELECT admission_state,admission_reason,COUNT(*) AS n FROM jobs WHERE status='QUEUED' GROUP BY admission_state,admission_reason", [])? {
        let state = r.get("admission_state").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("READY").to_uppercase();
        let n = r.get("n").and_then(Value::as_i64).unwrap_or(0);
        if !states.contains_key(&state) {
            order.push(state.clone());
        }
        *states.entry(state).or_insert(0) += n;
        if let Some(reason) = r.get("admission_reason").and_then(Value::as_str).filter(|s| !s.is_empty()) {
            match reasons.iter_mut().find(|(k, _)| k == reason) {
                Some((_, v)) => *v += n,
                None => reasons.push((reason.to_string(), n)),
            }
        }
    }
    let mut workers: Vec<(String, i64)> = Vec::new();
    for r in rows_as_maps(c, "SELECT worker_state,COUNT(*) AS n FROM jobs WHERE status IN ('RUNNING','PAUSING','CANCEL_REQUESTED') GROUP BY worker_state", [])? {
        let key = r.get("worker_state").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("UNKNOWN").to_string();
        workers.push((key, r.get("n").and_then(Value::as_i64).unwrap_or(0)));
    }
    let count = |sql: &str| c.query_row(sql, [], |r| r.get::<_, i64>(0));
    let deps = count("SELECT COUNT(*) AS n FROM job_dependencies d JOIN jobs j ON j.id=d.job_id WHERE j.status='QUEUED'")?;
    let retry = count("SELECT COUNT(*) AS n FROM jobs WHERE status='QUEUED' AND retry_count>0")?;
    let active = count("SELECT COUNT(*) AS n FROM jobs WHERE execution_id IS NOT NULL")?;
    let obj = |pairs: Vec<(String, i64)>| Value::Object(pairs.into_iter().map(|(k, v)| (k, Value::from(v))).collect());
    let state_pairs: Vec<(String, i64)> = order.iter().map(|k| (k.clone(), states[k])).collect();
    let queued: i64 = states.values().sum();
    let mut out = Map::new();
    out.insert("states".into(), obj(state_pairs));
    out.insert("reasons".into(), obj(reasons));
    out.insert("queued".into(), Value::from(queued));
    out.insert("workers".into(), obj(workers));
    out.insert("active_leases".into(), Value::from(active));
    out.insert("dependency_edges".into(), Value::from(deps));
    out.insert("retrying".into(), Value::from(retry));
    Ok(Value::Object(out))
}

// --- execution lane slots ---------------------------------------------------------------------------------------------------------

/// Who asks for lane slots.
#[derive(Debug, Clone)]
pub struct LaneRequest<'a> {
    pub job_id: &'a str,
    pub execution_id: &'a str,
    pub lane_ids: &'a [String],
    /// Defaults to the execution id.
    pub lease_id: Option<&'a str>,
    pub lease_seconds: i64,
}

/// `acquire_execution_lane_slots`: take one slot in each lane for an execution, or none at all (`false`) when a lane is full.
/// Expired slots are deleted first, and stay deleted even when the answer is `false`.
pub fn acquire_execution_lane_slots(c: &Connection, now: Now, req: &LaneRequest, capacities: &BTreeMap<String, i64>) -> Result<bool, JobError> {
    let lanes: Vec<&String> = req.lane_ids.iter().filter(|l| !l.is_empty()).collect::<BTreeSet<_>>().into_iter().collect();
    let (ts, exp) = lease(now, req.lease_seconds);
    c.execute("DELETE FROM execution_lane_slots WHERE expires_at IS NOT NULL AND expires_at < ? AND state IN ('ACTIVE','DRAINING')", params![ts])?;
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    if !lanes.is_empty() {
        let marks = vec!["?"; lanes.len()].join(",");
        let mut st = c.prepare(&format!(
            "SELECT lane_id,COUNT(*) n FROM execution_lane_slots WHERE lane_id IN ({marks}) AND state IN ('ACTIVE','DRAINING') GROUP BY lane_id"
        ))?;
        let rows = st.query_map(params_from_iter(lanes.iter()), |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
        for row in rows {
            let (lane, n) = row?;
            counts.insert(lane, n);
        }
    }
    for lane in &lanes {
        let cap = capacities.get(*lane).copied().unwrap_or(1).max(1);
        if counts.get(*lane).copied().unwrap_or(0) >= cap {
            return Ok(false);
        }
    }
    let lease_id = req.lease_id.filter(|l| !l.is_empty()).unwrap_or(req.execution_id);
    for lane in &lanes {
        c.execute(
            "INSERT INTO execution_lane_slots(slot_id,lane_id,job_id,execution_id,lease_id,state,acquired_at,expires_at,last_heartbeat) VALUES(?,?,?,?,?,?,?,?,?)",
            params![uuid4_hex(), lane, req.job_id, req.execution_id, lease_id, "ACTIVE", ts, exp, ts],
        )?;
    }
    Ok(true)
}

/// `release_execution_lane_slots`: by execution, else by job, else all. Returns how many slots changed.
pub fn release_execution_lane_slots(c: &Connection, execution_id: Option<&str>, job_id: Option<&str>, state: &str) -> rusqlite::Result<usize> {
    let target = if state.is_empty() { "RELEASED" } else { state };
    if let Some(e) = execution_id.filter(|e| !e.is_empty()) {
        c.execute("UPDATE execution_lane_slots SET state=?,expires_at=NULL WHERE execution_id=? AND state IN ('ACTIVE','DRAINING')", params![target, e])
    } else if let Some(j) = job_id.filter(|j| !j.is_empty()) {
        c.execute("UPDATE execution_lane_slots SET state=?,expires_at=NULL WHERE job_id=? AND state IN ('ACTIVE','DRAINING')", params![target, j])
    } else {
        c.execute("UPDATE execution_lane_slots SET state=?,expires_at=NULL WHERE state IN ('ACTIVE','DRAINING')", params![target])
    }
}

/// `expire_execution_lane_slots`: mark the slots whose lease ran out. Returns how many.
pub fn expire_execution_lane_slots(c: &Connection, now: Now) -> rusqlite::Result<usize> {
    let ts = now.isoformat();
    let ids: Vec<String> = {
        let mut st = c.prepare("SELECT slot_id FROM execution_lane_slots WHERE state IN ('ACTIVE','DRAINING') AND expires_at IS NOT NULL AND expires_at < ?")?;
        let rows = st.query_map([ts], |r| r.get::<_, String>(0))?;
        rows.collect::<rusqlite::Result<_>>()?
    };
    for id in &ids {
        c.execute("UPDATE execution_lane_slots SET state='EXPIRED',expires_at=NULL WHERE slot_id=?", [id])?;
    }
    Ok(ids.len())
}

/// `execution_lane_snapshot`: capacity, use and room per lane. Expires old slots first.
pub fn execution_lane_snapshot(c: &Connection, now: Now, capacities: &BTreeMap<String, i64>) -> rusqlite::Result<Value> {
    expire_execution_lane_slots(c, now)?;
    let mut active: BTreeMap<String, i64> = BTreeMap::new();
    let mut expired = 0i64;
    for r in rows_as_maps(c, "SELECT lane_id,state,COUNT(*) n FROM execution_lane_slots GROUP BY lane_id,state", [])? {
        let n = r["n"].as_i64().unwrap_or(0);
        match r["state"].as_str() {
            Some("EXPIRED") => expired += n,
            Some("ACTIVE") | Some("DRAINING") => *active.entry(r["lane_id"].as_str().unwrap_or("").to_string()).or_insert(0) += n,
            _ => {}
        }
    }
    let mut lanes = Map::new();
    for (lane, cap) in capacities {
        let a = active.get(lane).copied().unwrap_or(0);
        let mut m = Map::new();
        m.insert("capacity".into(), Value::from(*cap));
        m.insert("active".into(), Value::from(a));
        m.insert("available".into(), Value::from((*cap - a).max(0)));
        lanes.insert(lane.clone(), Value::Object(m));
    }
    let mut out = Map::new();
    out.insert("lanes".into(), Value::Object(lanes));
    out.insert("expired_slots".into(), Value::from(expired));
    out.insert("total_active".into(), Value::from(active.values().sum::<i64>()));
    Ok(Value::Object(out))
}

// --- resource leases --------------------------------------------------------------------------------------------------------------

/// The arguments of `acquire_resource_lease` after the key and job.
#[derive(Debug, Clone)]
pub struct NewResourceLease {
    pub owner_launch_id: String,
    pub resource_class: String,
    pub resource_type: Option<String>,
    pub reserved_amount: f64,
    pub lease_seconds: i64,
}

/// `acquire_resource_lease`: reserve a resource for a job (replacing a previous lease on the same key). Returns what Python returns.
pub fn acquire_resource_lease(c: &Connection, now: Now, resource_key: &str, job_id: Option<&str>, l: &NewResourceLease) -> Result<Value, JobError> {
    let (ts, exp) = lease(now, l.lease_seconds);
    let lease_id = uuid4_hex();
    let rtype = l.resource_type.clone().filter(|t| !t.is_empty()).unwrap_or_else(|| resource_key.to_string());
    let class = if l.resource_class.is_empty() { "BACKGROUND" } else { l.resource_class.as_str() };
    c.execute(
        "INSERT INTO resource_leases(resource_key,job_id,owner_launch_id,acquired_at,updated_at,lease_id,execution_id,resource_class,resource_type,reserved_amount,actual_amount,created_at,expires_at,last_heartbeat,state) \
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) \
         ON CONFLICT(resource_key) DO UPDATE SET job_id=excluded.job_id,owner_launch_id=excluded.owner_launch_id,updated_at=excluded.updated_at,lease_id=excluded.lease_id,execution_id=NULL,resource_class=excluded.resource_class,resource_type=excluded.resource_type,reserved_amount=excluded.reserved_amount,actual_amount=0,created_at=excluded.created_at,expires_at=excluded.expires_at,last_heartbeat=excluded.last_heartbeat,state='RESERVED'",
        params![resource_key, job_id, l.owner_launch_id, ts, ts, lease_id, rusqlite::types::Null, class, rtype, l.reserved_amount, 0.0, ts, exp, ts, "RESERVED"],
    )?;
    let mut out = Map::new();
    out.insert("lease_id".into(), Value::from(lease_id));
    out.insert("resource_key".into(), Value::from(resource_key));
    out.insert("job_id".into(), job_id.map(Value::from).unwrap_or(Value::Null));
    out.insert("state".into(), Value::from("RESERVED"));
    out.insert("expires_at".into(), Value::from(exp));
    Ok(Value::Object(out))
}

/// `invalidate_stale_resource_leases`: leases held by another launch are over. Returns how many.
pub fn invalidate_stale_resource_leases(c: &Connection, now: Now, owner_launch_id: &str) -> rusqlite::Result<usize> {
    c.execute(
        "UPDATE resource_leases SET state='EXPIRED',expires_at=NULL,updated_at=? WHERE owner_launch_id != ? AND state IN ('RESERVED','ACTIVE','RELEASING')",
        params![now.iso_seconds(), owner_launch_id],
    )
}

/// `expire_resource_leases`: mark the leases whose time ran out, and return them as they were.
pub fn expire_resource_leases(c: &Connection, now: Now) -> rusqlite::Result<Vec<Map<String, Value>>> {
    let ts = now.iso_seconds();
    let expired = rows_as_maps(
        c,
        "SELECT resource_key,job_id,execution_id,expires_at FROM resource_leases WHERE state IN ('RESERVED','ACTIVE') AND expires_at IS NOT NULL AND expires_at < ?",
        params![ts],
    )?;
    for r in &expired {
        c.execute(
            "UPDATE resource_leases SET state='EXPIRED',expires_at=NULL,updated_at=? WHERE resource_key=?",
            params![ts, r["resource_key"].as_str().unwrap_or("")],
        )?;
    }
    Ok(expired)
}

/// `resource_lease_snapshot`: counts by state and the open leases. Expires old leases and slots first.
pub fn resource_lease_snapshot(c: &Connection, now: Now) -> rusqlite::Result<Value> {
    expire_resource_leases(c, now)?;
    expire_execution_lane_slots(c, now)?;
    let counts = rows_as_maps(c, "SELECT state,COUNT(*) AS n FROM resource_leases GROUP BY state", [])?;
    let active = rows_as_maps(c, "SELECT * FROM resource_leases WHERE state IN ('RESERVED','ACTIVE','RELEASING') ORDER BY updated_at DESC LIMIT 100", [])?;
    let mut states = Map::new();
    let mut open = 0i64;
    for r in &counts {
        let state = r["state"].as_str().unwrap_or("").to_string();
        let n = r["n"].as_i64().unwrap_or(0);
        if matches!(state.as_str(), "RESERVED" | "ACTIVE" | "RELEASING") {
            open += n;
        }
        states.insert(state, Value::from(n));
    }
    let expired = states.get("EXPIRED").and_then(Value::as_i64).unwrap_or(0);
    let mut out = Map::new();
    out.insert("states".into(), Value::Object(states));
    out.insert("active".into(), Value::from(open));
    out.insert("expired".into(), Value::from(expired));
    out.insert("leases".into(), Value::Array(active.into_iter().map(Value::Object).collect()));
    out.insert("leaked".into(), Value::from(expired));
    Ok(Value::Object(out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isoformat_writes_the_fraction_only_when_there_is_one() {
        let n = Now { secs: 1_795_148_003, micros: 0 };
        assert_eq!(n.isoformat(), "2026-11-20T04:13:23+00:00");
        assert_eq!(Now { micros: 5, ..n }.isoformat(), "2026-11-20T04:13:23.000005+00:00");
        assert_eq!(Now { micros: 123_456, ..n }.isoformat(), "2026-11-20T04:13:23.123456+00:00");
        assert_eq!(Now { micros: 123_456, ..n }.iso_seconds(), "2026-11-20T04:13:23+00:00");
        assert_eq!(n.plus_seconds(45).isoformat(), "2026-11-20T04:14:08+00:00");
    }

    #[test]
    fn the_two_time_forms_sort_the_way_python_compares_them() {
        // "...23+00:00" is smaller than "...23.5+00:00" ('+' sorts before '.'): the string comparison Python's expiry queries rely on
        assert!("2026-11-20T04:13:23+00:00" < "2026-11-20T04:13:23.000005+00:00");
    }
}
