//! `jobs/orchestrator.py` (`JobOrchestrator`): is a queued job eligible to start, and what happens to it when it fails.
//!
//! Python's orchestrator is written against its manager (which jobs are running, paused or cancelled, and how a job maps to execution
//! lanes and a resource category), the resource manager and the governor. Here the manager is the [`ManagerView`] trait and the other two are
//! passed in; every decision writes through `ax-db` exactly as the Python orchestrator writes through `Database`.

use std::collections::{BTreeMap, HashMap};

use ax_core::governor_policy::Governor;
use ax_core::job_policy::{classify_failure, job_priority, retry_delay, Failure};
use ax_core::resource_policy::ResourcePolicy;
use ax_db::job_reads;
use ax_db::jobs::{self, JobError};
use ax_db::leases::{self, Admission, Now, RetryPolicy};
use ax_db::rusqlite::Connection;
use serde_json::{json, Map, Value};

pub const ADMISSION_READY: &str = "READY";
pub const ADMISSION_WAITING: &str = "WAITING";
pub const ADMISSION_BLOCKED: &str = "BLOCKED";
pub const ADMISSION_DELAYED: &str = "DELAYED";

pub const REASON_DEPENDENCY: &str = "DEPENDENCY";
pub const REASON_RETRY_DELAY: &str = "RETRY_DELAY";
pub const REASON_OPERATOR_PAUSED: &str = "OPERATOR_PAUSED";
pub const REASON_DUPLICATE_EXECUTION: &str = "EXECUTION_ALREADY_ACTIVE";
pub const REASON_DEADLINE: &str = "DEADLINE_EXPIRED";

/// What the orchestrator asks of Python's `JobManager`.
pub trait ManagerView {
    /// `job_id in running and not running[job_id].done()`.
    fn is_running(&self, job_id: &str) -> bool;
    fn is_cancelled(&self, job_id: &str) -> bool;
    fn is_job_paused(&self, job_id: &str) -> bool;
    /// `manager.paused`: everything is paused.
    fn paused(&self) -> bool;
    fn lane_drain(&self) -> bool;
    fn lane_keys(&self, job: &Map<String, Value>) -> Vec<String>;
    fn lane_capacities(&self, lanes: &[String]) -> BTreeMap<String, i64>;
    fn resource_category(&self, job: &Map<String, Value>) -> String;
    fn worker_id(&self) -> String;
}

#[derive(Debug)]
pub enum OrchError {
    Db(JobError),
    Sql(ax_db::rusqlite::Error),
    /// An error Python raises that is not a database error (the text of the exception).
    Python(String),
}

impl std::fmt::Display for OrchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OrchError::Db(e) => write!(f, "{e}"),
            OrchError::Sql(e) => write!(f, "{e}"),
            OrchError::Python(s) => write!(f, "{s}"),
        }
    }
}

impl From<JobError> for OrchError {
    fn from(e: JobError) -> Self {
        OrchError::Db(e)
    }
}
impl From<ax_db::rusqlite::Error> for OrchError {
    fn from(e: ax_db::rusqlite::Error) -> Self {
        OrchError::Sql(e)
    }
}

/// `datetime.fromisoformat(text.replace("Z", "+00:00"))` as a UTC moment (microseconds since the epoch), `None` where Python raises.
/// Covers `YYYY-MM-DD`, `YYYYMMDD`, a `T` or space and `HH[:MM[:SS[.f]]]` (or basic `HHMMSS`), and an optional `+HH[:MM[:SS]]` offset.
pub fn parse_iso(text: &str) -> Option<i128> {
    let t = text.replace('Z', "+00:00");
    let b: Vec<char> = t.chars().collect();
    let digits = |s: &[char]| -> Option<i64> {
        if s.is_empty() || !s.iter().all(|c| c.is_ascii_digit()) {
            return None;
        }
        s.iter().collect::<String>().parse().ok()
    };
    // date
    let (y, mo, d, mut i) = if b.len() >= 10 && b[4] == '-' && b[7] == '-' {
        (digits(&b[0..4])?, digits(&b[5..7])?, digits(&b[8..10])?, 10)
    } else if b.len() >= 8 {
        (digits(&b[0..4])?, digits(&b[4..6])?, digits(&b[6..8])?, 8)
    } else {
        return None;
    };
    if !(1..=9999).contains(&y) || !(1..=12).contains(&mo) {
        return None;
    }
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let dim = [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][(mo - 1) as usize];
    if !(1..=dim).contains(&d) {
        return None;
    }
    let (mut hh, mut mm, mut ss, mut micros) = (0i64, 0i64, 0i64, 0i64);
    let mut offset_secs = 0i64;
    if i < b.len() {
        if b[i] != 'T' && b[i] != ' ' {
            return None;
        }
        i += 1;
        let rest = &b[i..];
        // the time ends at an offset sign or the end
        let end = rest.iter().position(|c| *c == '+' || *c == '-').unwrap_or(rest.len());
        let time = &rest[..end];
        let basic = !time.contains(&':');
        let (h, m, s, frac): (&[char], &[char], &[char], &[char]) = if basic {
            let main_end = time.iter().position(|c| *c == '.' || *c == ',').unwrap_or(time.len());
            let main = &time[..main_end];
            let frac = if main_end < time.len() { &time[main_end + 1..] } else { &[] };
            match main.len() {
                2 => (&main[0..2], &[], &[], frac),
                4 => (&main[0..2], &main[2..4], &[], frac),
                6 => (&main[0..2], &main[2..4], &main[4..6], frac),
                _ => return None,
            }
        } else {
            let parts: Vec<&[char]> = time.split(|c| *c == ':').collect();
            match parts.len() {
                2 => (parts[0], parts[1], &[], &[]),
                3 => {
                    let sec = parts[2];
                    let dot = sec.iter().position(|c| *c == '.' || *c == ',');
                    match dot {
                        Some(p) => (parts[0], parts[1], &sec[..p], &sec[p + 1..]),
                        None => (parts[0], parts[1], sec, &[]),
                    }
                }
                _ => return None,
            }
        };
        if h.len() != 2 || (!m.is_empty() && m.len() != 2) || (!s.is_empty() && s.len() != 2) {
            return None;
        }
        hh = digits(h)?;
        mm = if m.is_empty() { 0 } else { digits(m)? };
        ss = if s.is_empty() { 0 } else { digits(s)? };
        if !frac.is_empty() {
            if !frac.iter().all(|c| c.is_ascii_digit()) {
                return None;
            }
            let mut f: String = frac.iter().take(6).collect();
            while f.len() < 6 {
                f.push('0');
            }
            micros = f.parse().ok()?;
        }
        if hh > 23 || mm > 59 || ss > 59 {
            return None;
        }
        if end < rest.len() {
            let off = &rest[end..];
            let sign = if off[0] == '-' { -1 } else { 1 };
            let body: Vec<char> = off[1..].to_vec();
            let (oh, om, os): (i64, i64, i64) = if body.contains(&':') {
                let p: Vec<&[char]> = body.split(|c| *c == ':').collect();
                match p.len() {
                    2 => (digits(p[0])?, digits(p[1])?, 0),
                    3 => (digits(p[0])?, digits(p[1])?, digits(p[2])?),
                    _ => return None,
                }
            } else {
                match body.len() {
                    2 => (digits(&body[0..2])?, 0, 0),
                    4 => (digits(&body[0..2])?, digits(&body[2..4])?, 0),
                    6 => (digits(&body[0..2])?, digits(&body[2..4])?, digits(&body[4..6])?),
                    _ => return None,
                }
            };
            if oh > 23 || om > 59 || os > 59 {
                return None;
            }
            offset_secs = sign * (oh * 3600 + om * 60 + os);
        }
    }
    // days from civil
    let (yy, mmn) = if mo <= 2 { (y - 1, mo + 12) } else { (y, mo) };
    let era = yy.div_euclid(400);
    let yoe = yy - era * 400;
    let doy = (153 * (mmn - 3) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    let secs = days * 86400 + hh * 3600 + mm * 60 + ss - offset_secs;
    Some(secs as i128 * 1_000_000 + micros as i128)
}

fn now_micros(now: Now) -> i128 {
    now.secs as i128 * 1_000_000 + now.micros as i128
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python's `int(x or 0)` for a snapshot value.
fn int_or0(v: Option<&Value>) -> i64 {
    match v {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)).unwrap_or(0),
        Some(Value::Bool(b)) => i64::from(*b),
        _ => 0,
    }
}

fn py_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_f64() {
                "float"
            } else {
                "int"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

fn job_str<'a>(job: &'a Map<String, Value>, key: &str) -> &'a str {
    job.get(key).and_then(Value::as_str).unwrap_or("")
}

fn priority_of(job: &Map<String, Value>) -> i64 {
    job_priority(job.get("priority"), job_str(job, "job_type"), job.get("detail"))
}

/// A failure, as the orchestrator classifies it.
pub struct FailureInfo<'a> {
    pub type_name: &'a str,
    /// `str(exc)`.
    pub message: &'a str,
    pub is_timeout: bool,
    pub is_os_error: bool,
    pub is_input_error: bool,
}

pub struct Orchestrator {
    pub lease_seconds: i64,
    pub heartbeat_interval: f64,
    pub retry_max_delay: f64,
    now: Box<dyn Fn() -> Now>,
    mono: Box<dyn Fn() -> f64>,
    last_heartbeat: HashMap<String, f64>,
}

impl Orchestrator {
    pub fn new(now: Box<dyn Fn() -> Now>, mono: Box<dyn Fn() -> f64>) -> Self {
        Orchestrator { lease_seconds: 45, heartbeat_interval: 2.0, retry_max_delay: 300.0, now, mono, last_heartbeat: HashMap::new() }
    }

    fn admit(&self, c: &Connection, job_id: &str, state: &str, a: &Admission) -> Result<(), OrchError> {
        leases::set_job_admission(c, (self.now)(), job_id, state, a)?;
        Ok(())
    }

    fn future(&self, iso: Option<&str>) -> bool {
        match iso.filter(|s| !s.is_empty()).and_then(parse_iso) {
            Some(v) => v > now_micros((self.now)()),
            None => false,
        }
    }

    fn expired(&self, iso: Option<&str>) -> bool {
        match iso.filter(|s| !s.is_empty()).and_then(parse_iso) {
            Some(v) => v <= now_micros((self.now)()),
            None => false,
        }
    }

    pub fn effective_priority(&self, c: &Connection, mgr: &dyn ManagerView, job: &Map<String, Value>) -> i64 {
        let base = priority_of(job);
        let created = match job.get("created_at") {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Null) | None => "None".to_string(),
            Some(other) => other.to_string(),
        };
        let Some(value) = parse_iso(&created) else { return base };
        let age_minutes = ((now_micros((self.now)()) - value) as f64 / 1_000_000.0 / 60.0).max(0.0);
        let aged = base as f64 - (age_minutes / 5.0).min(20.0);
        let mut effective = 0.max(aged.trunc() as i64);
        let lanes = mgr.lane_keys(job);
        if let Ok(snap) = leases::execution_lane_snapshot(c, (self.now)(), &mgr.lane_capacities(&lanes)) {
            let congested = lanes.iter().filter(|l| int_or0(snap.get("lanes").and_then(|x| x.get(l.as_str())).and_then(|x| x.get("available"))) <= 0).count() as i64;
            effective = 0.max(effective - 5.min(congested));
        }
        base.min(effective)
    }

    /// `(ok, state, reason)` of `_resource_decision`.
    fn resource_decision(
        &self,
        mgr: &dyn ManagerView,
        job: &Map<String, Value>,
        resources: Option<&mut ResourcePolicy>,
        governor: Option<&mut Governor>,
    ) -> Result<(bool, &'static str, &'static str), OrchError> {
        // `_requirements`: a dict, or JSON text (anything that is not JSON gives no requirements)
        let req: Value = match job.get("resource_requirements") {
            Some(v) if truthy(v) => match v {
                Value::Object(_) => v.clone(),
                Value::String(s) => serde_json::from_str(s).unwrap_or_else(|_| json!({})),
                // `json.loads(<a list or a number>)` raises TypeError, which Python's `except Exception` turns into no requirements
                _ => json!({}),
            },
            _ => json!({}),
        };
        if let Some(resources) = resources {
            let category = mgr.resource_category(job);
            if resources.background_paused {
                return Ok((false, ADMISSION_BLOCKED, "OPERATOR_PAUSED"));
            }
            let snap = resources.snapshot();
            if resources.playback_active() && resources.playback_blocks_category(&category) {
                // `req.get("playback_compatible", False)`: only a dict has `get`
                let compatible = match &req {
                    Value::Object(o) => o.get("playback_compatible").is_some_and(truthy),
                    other => return Err(OrchError::Python(format!("'{}' object has no attribute 'get'", py_type_name(other)))),
                };
                if !compatible {
                    return Ok((false, ADMISSION_WAITING, "RESOURCE_CAPACITY"));
                }
            }
            let capacity = snap.get("background_capacity");
            let capacity_or1 = if capacity.is_some_and(|c| c.is_null()) { 1 } else { int_or0(capacity) };
            let active = int_or0(snap["category_active"].get(&category));
            let limit = match snap["effective_limits"].get(&category) {
                Some(v) => int_or0(Some(v)),
                None => capacity_or1,
            };
            if active >= 1.max(limit) {
                return Ok((false, ADMISSION_WAITING, "RESOURCE_CATEGORY_CAPACITY"));
            }
            let or1 = |v: i64| if v == 0 { 1 } else { v };
            if int_or0(snap.get("background_active")) >= or1(capacity_or1) {
                return Ok((false, ADMISSION_WAITING, "SHARED_BACKGROUND_CAPACITY"));
            }
            let metadata_class = category == "metadata";
            let class_active = int_or0(snap.get(if metadata_class { "metadata_active" } else { "non_metadata_active" }));
            let class_limit_key = if metadata_class { "metadata_capacity" } else { "non_metadata_capacity" };
            let class_limit = or1(match snap.get(class_limit_key) {
                Some(v) => int_or0(Some(v)),
                None => capacity_or1,
            });
            if class_active >= 1.max(class_limit) {
                return Ok((false, ADMISSION_WAITING, if metadata_class { "METADATA_WORKER_CAPACITY" } else { "OTHER_WORKER_CAPACITY" }));
            }
        }
        if let Some(governor) = governor {
            if ["TELEGRAM_TRANSFER", "UPLOAD_LOCAL", "MEDIA_MANAGER"].contains(&job_str(job, "job_type")) {
                let gs = governor.snapshot();
                if int_or0(gs.get("flood_wait_remaining")) > 0 || int_or0(gs.get("cooldown_remaining")) > 0 {
                    return Ok((false, ADMISSION_DELAYED, "TELEGRAM_COOLDOWN"));
                }
                let capacity = int_or0(gs.get("transfer_capacity"));
                if int_or0(gs.get("transfer_active")) >= if capacity == 0 { 3 } else { capacity } {
                    return Ok((false, ADMISSION_WAITING, "TELEGRAM_TRANSFER_CAPACITY"));
                }
            }
        }
        Ok((true, ADMISSION_READY, ""))
    }

    /// `prepare`: may this queued job start? `(allowed, reason)`.
    pub fn prepare(
        &self,
        c: &Connection,
        mgr: &dyn ManagerView,
        resources: Option<&mut ResourcePolicy>,
        governor: Option<&mut Governor>,
        job_id: &str,
    ) -> Result<(bool, String), OrchError> {
        let Some(job) = job_reads::get_job(c, job_id)? else { return Ok((false, "not_queued".into())) };
        if job.get("status").and_then(Value::as_str) != Some("QUEUED") {
            return Ok((false, "not_queued".into()));
        }
        let priority = priority_of(&job);
        let adm = |operation: &str, reason: &str| Admission { reason: reason.into(), priority: Some(priority), operation: Some(operation.into()), ..Default::default() };
        if mgr.is_running(job_id) {
            self.admit(c, job_id, ADMISSION_BLOCKED, &adm("Admission blocked", REASON_DUPLICATE_EXECUTION))?;
            return Ok((false, "duplicate_execution".into()));
        }
        if self.future(job.get("next_run_at").and_then(Value::as_str)) {
            self.admit(c, job_id, ADMISSION_DELAYED, &adm("Admission delayed", REASON_RETRY_DELAY))?;
            return Ok((false, "delayed".into()));
        }
        if self.expired(job.get("deadline_at").and_then(Value::as_str)) {
            self.admit(c, job_id, ADMISSION_BLOCKED, &adm("Admission blocked", REASON_DEADLINE))?;
            return Ok((false, "deadline".into()));
        }
        if mgr.is_cancelled(job_id) {
            return Ok((false, "cancelled".into()));
        }
        if mgr.is_job_paused(job_id) || mgr.paused() {
            self.admit(c, job_id, ADMISSION_BLOCKED, &adm("Admission blocked", REASON_OPERATOR_PAUSED))?;
            return Ok((false, "paused".into()));
        }
        let deps = jobs::job_dependencies(c, job_id)?;
        let upper = |d: &jobs::Dependency| d.dependency_status.clone().unwrap_or_default().to_uppercase();
        let as_json = |d: &jobs::Dependency| json!({"job_id": d.job_id, "depends_on_job_id": d.depends_on_job_id, "dependency_type": d.dependency_type, "created_at": d.created_at, "dependency_status": d.dependency_status});
        let failed: Vec<Value> = deps
            .iter()
            .filter(|d| {
                ["FAILED", "CANCELLED", "COMPLETED_WITH_ERRORS"].contains(&upper(d).as_str())
                    && (if d.dependency_type.is_empty() { "SUCCESS".to_string() } else { d.dependency_type.to_uppercase() }) == "SUCCESS"
            })
            .map(as_json)
            .collect();
        let pending: Vec<Value> = deps.iter().filter(|d| !["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"].contains(&upper(d).as_str())).map(as_json).collect();
        if !failed.is_empty() {
            let a = Admission { payload: Some(json!({"dependencies": failed})), ..adm("Admission blocked", "DEPENDENCY_FAILED") };
            self.admit(c, job_id, ADMISSION_BLOCKED, &a)?;
            return Ok((false, "dependency_failed".into()));
        }
        if !pending.is_empty() {
            let a = Admission { payload: Some(json!({"dependencies": pending})), ..adm("Waiting for dependencies", REASON_DEPENDENCY) };
            self.admit(c, job_id, ADMISSION_WAITING, &a)?;
            return Ok((false, "dependency".into()));
        }
        let lanes = mgr.lane_keys(&job);
        if mgr.lane_drain() {
            let p = self.effective_priority(c, mgr, &job);
            let a = Admission { priority: Some(p), ..adm("Waiting for execution lane drain", "EXECUTION_LANE_DRAINING") };
            self.admit(c, job_id, ADMISSION_WAITING, &a)?;
            return Ok((false, "lane_draining".into()));
        }
        if !lanes.is_empty() {
            let snap = leases::execution_lane_snapshot(c, (self.now)(), &mgr.lane_capacities(&lanes))?;
            if lanes.iter().any(|l| int_or0(snap.get("lanes").and_then(|x| x.get(l.as_str())).and_then(|x| x.get("available"))) <= 0) {
                let p = self.effective_priority(c, mgr, &job);
                let a = Admission { priority: Some(p), ..adm("Waiting for execution lane capacity", "EXECUTION_LANE_CAPACITY") };
                self.admit(c, job_id, ADMISSION_WAITING, &a)?;
                return Ok((false, "lane_capacity".into()));
            }
        }
        let (ok, state, reason) = self.resource_decision(mgr, &job, resources, governor)?;
        if !ok {
            let next_run = (state == ADMISSION_DELAYED).then(|| {
                let n = (self.now)();
                // Python: timedelta(seconds=2.0) added to the current time, isoformat()
                Now { secs: n.secs + 2, micros: n.micros }.isoformat()
            });
            let operation = match reason {
                "TELEGRAM_TRANSFER_CAPACITY" => "Waiting for Telegram transfer capacity",
                "TELEGRAM_COOLDOWN" => "Waiting for Telegram rate-limit cooldown",
                "SHARED_BACKGROUND_CAPACITY" => "Waiting for shared CPU/disk capacity",
                "METADATA_WORKER_CAPACITY" => "Waiting for reserved metadata worker capacity",
                "OTHER_WORKER_CAPACITY" => "Waiting for independent worker capacity",
                "RESOURCE_CATEGORY_CAPACITY" => "Waiting for worker category capacity",
                "RESOURCE_CAPACITY" => "Waiting for playback/resource capacity",
                _ => "Waiting for resources",
            };
            let a = Admission { reason: reason.into(), next_run_at: next_run, priority: Some(priority), operation: Some(operation.into()), ..Default::default() };
            self.admit(c, job_id, state, &a)?;
            return Ok((false, "resource".into()));
        }
        self.admit(c, job_id, ADMISSION_READY, &adm("Admission ready", ""))?;
        Ok((true, "ready".into()))
    }

    pub fn waiting(&self, c: &Connection, job_id: &str, reason: &str, operation: &str, retry_after: Option<f64>) -> Result<(), OrchError> {
        let next_run = retry_after.map(|s| {
            let secs = s.max(0.1);
            let n = (self.now)();
            let total_micros = n.micros as i128 + (secs * 1_000_000.0).round_ties_even() as i128;
            Now { secs: n.secs + total_micros.div_euclid(1_000_000) as i64, micros: total_micros.rem_euclid(1_000_000) as u32 }.isoformat()
        });
        let state = if retry_after.is_none() { ADMISSION_WAITING } else { ADMISSION_DELAYED };
        self.admit(c, job_id, state, &Admission { reason: reason.into(), next_run_at: next_run, operation: Some(operation.into()), ..Default::default() })
    }

    pub fn ready(&self, c: &Connection, job_id: &str) -> Result<(), OrchError> {
        self.admit(c, job_id, ADMISSION_READY, &Admission { operation: Some("Admission ready".into()), ..Default::default() })
    }

    pub fn start_execution(&self, c: &Connection, mgr: &dyn ManagerView, job_id: &str) -> Result<leases::Execution, OrchError> {
        Ok(leases::acquire_job_execution(c, (self.now)(), job_id, &mgr.worker_id(), self.lease_seconds)?)
    }

    pub fn heartbeat_execution(&mut self, c: &Connection, job_id: &str, execution_id: &str, worker_state: &str) -> Result<bool, OrchError> {
        let now = (self.mono)();
        let interval = self.heartbeat_interval.max(0.25);
        let last = self.last_heartbeat.get(execution_id).copied().unwrap_or(0.0);
        if last != 0.0 && now - last < interval {
            return Ok(true);
        }
        let result = leases::heartbeat_job_execution(c, (self.now)(), job_id, execution_id, worker_state, self.lease_seconds)?;
        self.last_heartbeat.insert(execution_id.to_string(), now);
        Ok(result)
    }

    pub fn finish_execution(&mut self, c: &Connection, job_id: &str, execution_id: &str, status: &str, failure_class: &str) -> Result<(), OrchError> {
        leases::finish_job_execution(c, (self.now)(), job_id, execution_id, status, failure_class)?;
        self.last_heartbeat.remove(execution_id);
        Ok(())
    }

    pub fn classify(f: &FailureInfo) -> &'static str {
        classify_failure(&Failure { type_name: f.type_name, message: f.message, is_timeout: f.is_timeout, is_os_error: f.is_os_error, is_input_error: f.is_input_error })
    }

    /// `schedule_failure`: `(retry scheduled, failure class, delay)`. `jitter_percent` is what Python gets from `hash(job_id) % 20`.
    pub fn schedule_failure(&self, c: &Connection, job_id: &str, failure: &FailureInfo, jitter_percent: u32) -> Result<(bool, &'static str, Option<f64>), OrchError> {
        let job = job_reads::get_job(c, job_id)?.unwrap_or_default();
        let class = Self::classify(failure);
        let int = |k: &str| int_or0(job.get(k));
        let (retries, maximum) = (int("retry_count"), int("max_retries"));
        if let Some(delay) = retry_delay(class, retries, maximum, self.retry_max_delay, jitter_percent) {
            let retries = retries + 1;
            let n = (self.now)();
            let total_micros = n.micros as i128 + (delay * 1_000_000.0).round_ties_even() as i128;
            let next_run = Now { secs: n.secs + total_micros.div_euclid(1_000_000) as i64, micros: total_micros.rem_euclid(1_000_000) as u32 }.isoformat();
            leases::set_job_retry_policy(
                c,
                job_id,
                &RetryPolicy { retry_count: Some(retries), max_retries: Some(maximum), failure_class: Some(class.into()), next_run_at: Some(Some(next_run.clone())) },
            )?;
            let attempt = job.get("attempt").cloned().filter(|v| !v.is_null()).unwrap_or(json!(0));
            let a = Admission {
                reason: format!("RETRY_{class}"),
                next_run_at: Some(next_run),
                operation: Some(format!("Retry scheduled in {delay:.1}s")),
                payload: Some(json!({"attempt": attempt, "retry_count": retries, "failure_class": class})),
                ..Default::default()
            };
            self.admit(c, job_id, ADMISSION_DELAYED, &a)?;
            let mut fields = Map::new();
            fields.insert("error".into(), Value::from(failure.message.chars().take(1000).collect::<String>()));
            fields.insert("current_operation".into(), Value::from(format!("Retry scheduled ({class})")));
            jobs::transition_job(c, job_id, "QUEUED", &fields)?;
            return Ok((true, class, Some(delay)));
        }
        leases::set_job_retry_policy(
            c,
            job_id,
            &RetryPolicy { retry_count: Some(retries), max_retries: Some(maximum), failure_class: Some(class.into()), next_run_at: Some(None) },
        )?;
        Ok((false, class, None))
    }

    /// `recover_expired_leases`: the jobs whose worker lease ran out and that were running are interrupted.
    pub fn recover_expired_leases(&self, c: &Connection) -> Result<Vec<String>, OrchError> {
        let now = (self.now)();
        let expired = leases::expired_job_executions(c, now)?;
        leases::expire_resource_leases(c, now)?;
        leases::expire_execution_lane_slots(c, now)?;
        let mut recovered = Vec::new();
        for job in expired {
            let jid = job.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            if job.get("status").and_then(Value::as_str) == Some("RUNNING") {
                let attempt = (|| -> Result<(), JobError> {
                    let mut fields = Map::new();
                    fields.insert("current_operation".into(), Value::from("Worker lease expired; recovery required"));
                    jobs::transition_job(c, &jid, "INTERRUPTED", &fields)?;
                    jobs::append_job_journal(
                        c,
                        &jid,
                        "EXECUTION_LEASE_EXPIRED",
                        &jobs::JournalEntry {
                            status: Some("INTERRUPTED".into()),
                            operation: Some("Execution lease expired".into()),
                            payload: Some(json!({"execution_id": job.get("execution_id"), "worker_id": job.get("worker_id")})),
                            ..Default::default()
                        },
                    )?;
                    Ok(())
                })();
                if attempt.is_ok() {
                    recovered.push(jid);
                }
            }
        }
        Ok(recovered)
    }

    pub fn snapshot(&self, c: &Connection) -> Result<Value, OrchError> {
        let now = (self.now)();
        let mut snap = leases::job_orchestration_snapshot(c)?;
        let expired = leases::expired_job_executions(c, now)?.len();
        if let Value::Object(o) = &mut snap {
            o.insert("expired_leases".into(), json!(expired));
            o.insert("resource_leases".into(), leases::resource_lease_snapshot(c, now)?);
        }
        Ok(snap)
    }
}
