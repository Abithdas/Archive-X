//! The pure decisions of `jobs/manager.py`: which execution lanes and resource category a job has, whether two jobs conflict, how many
//! slots a lane has, how coalesced metadata jobs merge, and how a finished Metadata & Assets pipeline is judged.
//!
//! These are the answers the orchestrator asks of its manager ([`crate::orchestrator::ManagerView`]); the worker loops that call them
//! need the engines and come later. Python raises on a few odd payloads (a detail that is JSON but not an object, `operations` that
//! is a number); those raise here too, as [`PyError`] with Python's message, because the callers do not catch them.

use ax_core::text::casefold;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

/// The text of an exception Python would raise.
#[derive(Debug, PartialEq, Eq)]
pub struct PyError(pub String);

impl std::fmt::Display for PyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

type R<T> = Result<T, PyError>;

pub const TRANSFER_FAMILY: [&str; 3] = ["TELEGRAM_TRANSFER", "UPLOAD_LOCAL", "MEDIA_MANAGER"];
pub const METADATA_ASSET_FAMILY: [&str; 6] = ["METADATA_ASSETS", "METADATA_CHANNEL_PIPELINE", "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC", "THUMBNAIL_INDEXER"];

/// `config.EXECUTION_LANE_CAPACITIES`.
pub const EXECUTION_LANE_CAPACITIES: [(&str, i64); 13] = [
    ("METADATA", 3),
    ("SUGGESTIONS", 2),
    ("ENTITY_DATA", 3),
    ("THUMBNAILS", 3),
    ("METADATA_ASSETS", 3),
    ("METADATA_CHANNEL_PIPELINE", 3),
    ("TRANSFER_LOCAL", 2),
    ("TRANSFER_TELEGRAM", 3),
    ("MEDIA_MANAGER", 3),
    ("PREVIEW", 1),
    ("CONTENT_AUTOMATION", 1),
    ("CONTENT_AUTOMATION_DELIVERY", 3),
    ("BACKUP_CREATE", 1),
];

fn py_type(v: &Value) -> &'static str {
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

/// Python's `repr` of a string.
fn py_repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python's `repr` of a JSON-shaped value.
fn py_repr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => {
            if let Some(f) = n.as_f64().filter(|_| n.is_f64()) {
                format!("{f:?}")
            } else {
                n.to_string()
            }
        }
        Value::String(s) => py_repr_str(s),
        Value::Array(a) => format!("[{}]", a.iter().map(py_repr).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => format!("{{{}}}", o.iter().map(|(k, x)| format!("{}: {}", py_repr_str(k), py_repr(x))).collect::<Vec<_>>().join(", ")),
    }
}

/// Python's `str(value)`.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => py_repr(other),
    }
}

/// `json.loads(text or "{}")` where a failure means `{}` (the callers' `except Exception`). The result may be any JSON value.
fn loads_or_empty(raw: Option<&Value>) -> Value {
    let text = match raw {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(Value::String(_)) | Some(Value::Null) | None => "{}".into(),
        Some(v) if !truthy(v) => "{}".into(),
        // json.loads of a number or a list raises TypeError, which the callers also catch
        _ => return json!({}),
    };
    serde_json::from_str(&text).unwrap_or_else(|_| json!({}))
}

/// `detail.get(key)` on a value that Python calls `.get` on without a check.
fn get<'a>(detail: &'a Value, key: &str) -> R<Option<&'a Value>> {
    match detail {
        Value::Object(o) => Ok(o.get(key)),
        other => Err(PyError(format!("'{}' object has no attribute 'get'", py_type(other)))),
    }
}

fn job_str(job: &Map<String, Value>, key: &str) -> String {
    match job.get(key) {
        Some(v) if truthy(v) => py_str(v),
        _ => String::new(),
    }
}

// ---- metadata_assets.unified_detail: the operations -----------------------------------------------------------------------------

fn op_alias(name: &str) -> Option<&'static str> {
    match name {
        "metadata_sync" | "metadata" => Some("metadata"),
        "suggestion_scan" | "suggestions" => Some("suggestions"),
        "entity_data" | "entity_images" => Some("entity_data"),
        "thumbnail_indexer" | "thumbnails" => Some("thumbnails"),
        _ => None,
    }
}

fn legacy_operation(job_type: &str) -> Option<&'static str> {
    match job_type {
        "METADATA_SYNC" => Some("metadata"),
        "SUGGESTION_SCAN" => Some("suggestions"),
        "ENTITY_IMAGE_SYNC" => Some("entity_data"),
        "THUMBNAIL_INDEXER" => Some("thumbnails"),
        _ => None,
    }
}

/// `unified_detail(job_type, detail)["operations"]`.
pub fn unified_operations(job_type: &str, raw_detail: Option<&Value>) -> R<Vec<String>> {
    // `_loads`: a dict is taken as it is; text is parsed; anything that is not a dict gives {}
    let detail: Map<String, Value> = match raw_detail {
        Some(Value::Object(o)) => o.clone(),
        Some(Value::String(s)) => match serde_json::from_str::<Value>(if s.is_empty() { "{}" } else { s }) {
            Ok(Value::Object(o)) => o,
            _ => Map::new(),
        },
        _ => Map::new(),
    };
    if let Some(op) = legacy_operation(job_type) {
        if !detail.contains_key("operations") {
            return Ok(vec![op.to_string()]);
        }
    }
    let mut operations: Vec<String> = Vec::new();
    // `for value in detail.get("operations") or []`
    let items: Vec<Value> = match detail.get("operations") {
        Some(v) if truthy(v) => match v {
            Value::Array(a) => a.clone(),
            Value::String(s) => s.chars().map(|c| Value::String(c.to_string())).collect(),
            Value::Object(o) => o.keys().map(|k| Value::String(k.clone())).collect(),
            other => return Err(PyError(format!("'{}' object is not iterable", py_type(other)))),
        },
        _ => vec![],
    };
    for value in items {
        let name = casefold(py_str(&if truthy(&value) { value } else { Value::String(String::new()) }).trim());
        if let Some(op) = op_alias(&name) {
            if !operations.iter().any(|o| o == op) {
                operations.push(op.to_string());
            }
        }
    }
    Ok(operations)
}

// ---- resource category and lanes ------------------------------------------------------------------------------------------------

/// `job_resource_category`.
pub fn job_resource_category(job: &Map<String, Value>) -> R<&'static str> {
    let job_type = job_str(job, "job_type");
    let t = job_type.as_str();
    if t == "PREVIEW_PREFETCH" || t == "THUMBNAIL_INDEXER" {
        return Ok("preview");
    }
    if ["METADATA_ASSETS", "METADATA_CHANNEL_PIPELINE", "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC"].contains(&t) {
        let thumbnails = if t == "METADATA_ASSETS" { unified_operations(t, job.get("detail"))?.iter().any(|o| o == "thumbnails") } else { false };
        return Ok(if thumbnails { "preview" } else { "metadata" });
    }
    Ok("generic")
}

/// `_execution_keys`.
pub fn execution_keys(job: &Map<String, Value>) -> R<Vec<String>> {
    let job_type = job_str(job, "job_type");
    let t = job_type.as_str();
    if TRANSFER_FAMILY.contains(&t) {
        if t == "UPLOAD_LOCAL" {
            return Ok(vec!["TRANSFER_LOCAL".into()]);
        }
        if t == "MEDIA_MANAGER" {
            return Ok(vec!["MEDIA_MANAGER".into()]);
        }
        let detail = loads_or_empty(job.get("detail"));
        let workflow = get(&detail, "workflow")?.filter(|v| truthy(v)).map(py_str).unwrap_or_default().trim().to_uppercase();
        if workflow == "CONTENT_AUTOMATION" {
            return Ok(vec!["CONTENT_AUTOMATION_DELIVERY".into()]);
        }
        let source = ["source", "source_type"]
            .iter()
            .find_map(|k| get(&detail, k).ok().flatten().filter(|v| truthy(v)))
            .map(py_str)
            .unwrap_or_else(|| "telegram".into());
        let source = casefold(source.trim());
        let local = ["local", "local_files", "upload_local"].contains(&source.as_str());
        return Ok(vec![if local { "TRANSFER_LOCAL" } else { "TRANSFER_TELEGRAM" }.into()]);
    }
    if t == "METADATA_CHANNEL_PIPELINE" {
        let detail = loads_or_empty(job.get("detail"));
        let stages: Vec<Value> = match get(&detail, "stages")? {
            Some(Value::Array(a)) => a.clone(),
            _ => ["inventory", "metadata", "performers", "channel_data", "snapshots"].iter().map(|s| json!(s)).collect(),
        };
        let has = |name: &str| stages.iter().any(|s| s.as_str() == Some(name));
        let mut lanes: BTreeSet<&str> = BTreeSet::new();
        if has("metadata") {
            lanes.insert("METADATA");
        }
        if has("performers") || has("channel_data") {
            lanes.insert("ENTITY_DATA");
        }
        if has("snapshots") {
            lanes.insert("THUMBNAILS");
        }
        if lanes.is_empty() {
            lanes.insert("METADATA_ASSETS");
        }
        return Ok(lanes.into_iter().map(String::from).collect());
    }
    if METADATA_ASSET_FAMILY.contains(&t) {
        let alias = match t {
            "METADATA_SYNC" => Some("METADATA"),
            "SUGGESTION_SCAN" => Some("SUGGESTIONS"),
            "ENTITY_IMAGE_SYNC" => Some("ENTITY_DATA"),
            "THUMBNAIL_INDEXER" => Some("THUMBNAILS"),
            _ => None,
        };
        if let Some(a) = alias {
            return Ok(vec![a.into()]);
        }
        let ops = unified_operations(t, job.get("detail"))?;
        let mut out: BTreeSet<&str> = BTreeSet::new();
        for op in &ops {
            match op.as_str() {
                "metadata" => out.insert("METADATA"),
                "suggestions" => out.insert("SUGGESTIONS"),
                "entity_data" => out.insert("ENTITY_DATA"),
                "thumbnails" => out.insert("THUMBNAILS"),
                _ => false,
            };
        }
        if out.is_empty() {
            out.insert("METADATA_ASSETS");
        }
        return Ok(out.into_iter().map(String::from).collect());
    }
    Ok(vec![job_type])
}

/// `JobManager.execution_lane_keys`: sorted, no repeats.
pub fn execution_lane_keys(job: &Map<String, Value>) -> R<Vec<String>> {
    Ok(execution_keys(job)?.into_iter().collect::<BTreeSet<_>>().into_iter().collect())
}

/// `execution_lanes_conflict`: the two jobs share a lane.
pub fn execution_lanes_conflict(a: &Map<String, Value>, b: &Map<String, Value>) -> R<bool> {
    let first: BTreeSet<String> = execution_keys(a)?.into_iter().collect();
    Ok(execution_keys(b)?.iter().any(|k| first.contains(k)))
}

/// `JobManager.execution_lane_capacity`: the configured slots, none while draining, fewer under resource pressure.
/// `pressure` is `resources.snapshot()`'s `pressure_state` (or `pressure`), which is `None` when the snapshot has neither.
pub fn execution_lane_capacity(lane: &str, drain: bool, pressure: Option<&str>) -> i64 {
    let base = EXECUTION_LANE_CAPACITIES.iter().find(|(l, _)| *l == lane).map(|(_, c)| *c).unwrap_or(1);
    if drain {
        return 0;
    }
    match pressure.map(str::to_uppercase).as_deref() {
        Some("CRITICAL") | Some("CONSTRAINED") => 1.max(base.div_euclid(2)),
        Some("PRESSURED") if base > 1 => 1.max(base - 1),
        _ => base,
    }
}

// ---- coalescing and completion --------------------------------------------------------------------------------------------------

/// `json.dumps(value)` with Python's defaults: `", "` and `": "` separators, non-ASCII escaped.
pub fn py_json_dumps(v: &Value) -> String {
    fn string(s: &str, out: &mut String) {
        out.push('"');
        for ch in s.chars() {
            match ch {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{8}' => out.push_str("\\b"),
                '\u{c}' => out.push_str("\\f"),
                c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                    let mut buf = [0u16; 2];
                    for unit in c.encode_utf16(&mut buf) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
                c => out.push(c),
            }
        }
        out.push('"');
    }
    fn go(v: &Value, out: &mut String) {
        match v {
            Value::Null => out.push_str("null"),
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Value::Number(n) => out.push_str(&n.to_string()),
            Value::String(s) => string(s, out),
            Value::Array(a) => {
                out.push('[');
                for (i, x) in a.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    go(x, out);
                }
                out.push(']');
            }
            Value::Object(o) => {
                out.push('{');
                for (i, (k, x)) in o.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    string(k, out);
                    out.push_str(": ");
                    go(x, out);
                }
                out.push('}');
            }
        }
    }
    let mut out = String::new();
    go(v, &mut out);
    out
}

/// Python's `int(value)` for the values a stored limit can have.
fn py_int(v: &Value) -> R<i64> {
    match v {
        Value::Bool(b) => Ok(i64::from(*b)),
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64))
            .ok_or_else(|| PyError("cannot convert float NaN or infinity to integer".into())),
        Value::String(s) => s.trim().replace('_', "").parse::<i64>().map_err(|_| PyError(format!("invalid literal for int() with base 10: {}", py_repr_str(s)))),
        other => Err(PyError(format!("int() argument must be a string, a bytes-like object or a real number, not '{}'", py_type(other)))),
    }
}

/// `merge_metadata_detail(keep, others)`: union of channels, the largest limit, any force.
pub fn merge_metadata_detail(keep: &Map<String, Value>, others: &[Map<String, Value>]) -> R<String> {
    let parse = |j: &Map<String, Value>| -> R<(Value, Vec<String>)> {
        let d = loads_or_empty(j.get("detail"));
        let mut chans: Vec<String> = Vec::new();
        let listed = get(&d, "source_channels")?.cloned().unwrap_or(Value::Null);
        if truthy(&listed) {
            let items: Vec<Value> = match &listed {
                Value::Array(a) => a.clone(),
                Value::String(s) => s.chars().map(|c| Value::String(c.to_string())).collect(),
                Value::Object(o) => o.keys().map(|k| Value::String(k.clone())).collect(),
                other => return Err(PyError(format!("'{}' object is not iterable", py_type(other)))),
            };
            chans.extend(items.iter().map(py_str).filter(|s| !s.is_empty()));
        }
        if let Some(sc) = j.get("source_channel").filter(|v| truthy(v)) {
            let s = py_str(sc);
            if !chans.contains(&s) {
                chans.push(s);
            }
        }
        Ok((d, chans))
    };
    let (base, mut chans) = parse(keep)?;
    let flag = |d: &Value, k: &str| -> R<bool> { Ok(get(d, k)?.is_some_and(truthy)) };
    let limit_of = |d: &Value| -> R<i64> {
        let v = get(d, "limit")?.cloned().unwrap_or(json!(50));
        Ok(if truthy(&v) { py_int(&v)? } else { 50 })
    };
    let mut unbounded = flag(&base, "unbounded")?;
    let mut limit = limit_of(&base)?;
    let mut force = flag(&base, "force")?;
    for o in others {
        let (d, c) = parse(o)?;
        for x in c {
            if !chans.contains(&x) {
                chans.push(x);
            }
        }
        let ol = limit_of(&d)?;
        unbounded = unbounded || flag(&d, "unbounded")?;
        if !unbounded {
            limit = limit.max(ol);
        }
        force = force || flag(&d, "force")?;
    }
    Ok(py_json_dumps(&json!({"limit": if unbounded { 0 } else { limit }, "unbounded": unbounded, "force": force, "source_channels": chans})))
}

/// `_metadata_pipeline_completion_status`, from the job row: the terminal status and what failed.
pub fn pipeline_completion_status(job: Option<&Map<String, Value>>) -> R<(&'static str, Value)> {
    let empty = Map::new();
    let job = job.unwrap_or(&empty);
    if job_str(job, "job_type") != "METADATA_CHANNEL_PIPELINE" {
        return Ok(("COMPLETED", json!({})));
    }
    let detail = match job.get("detail") {
        Some(Value::String(s)) => serde_json::from_str::<Value>(s).unwrap_or_else(|_| json!({})),
        Some(v) if truthy(v) => match v {
            Value::Object(_) => v.clone(),
            _ => json!({}), // dict(<list or number>) raises, which the `except` turns into {}
        },
        _ => json!({}),
    };
    let channels: Vec<Value> = match get(&detail, "channel_queue")? {
        Some(Value::Array(a)) => a.clone(),
        _ => vec![],
    };
    let bad = |v: Option<&Value>| {
        let s = v.filter(|x| truthy(x)).map(py_str).unwrap_or_default().to_uppercase();
        ["FAILED", "INCOMPLETE", "COMPLETED_WITH_ERRORS"].contains(&s.as_str())
    };
    let (mut failed_channels, mut failed_phases) = (Vec::<Value>::new(), Vec::<Value>::new());
    for row in &channels {
        let Value::Object(row) = row else { continue };
        let cid = row.get("channel_id").filter(|v| truthy(v)).map(py_str).unwrap_or_default();
        if bad(row.get("state")) {
            failed_channels.push(json!(cid));
        }
        if let Some(Value::Object(phases)) = row.get("phases") {
            for (phase, state) in phases {
                if bad(Some(state)) {
                    failed_phases.push(json!(format!("{cid}:{phase}")));
                }
            }
        }
    }
    let status = if failed_channels.is_empty() && failed_phases.is_empty() { "COMPLETED" } else { "COMPLETED_WITH_ERRORS" };
    Ok((status, json!({"failed_channels": failed_channels, "failed_phases": failed_phases, "channel_count": channels.len()})))
}

/// `_job_is_manual`.
pub fn job_is_manual(job: &Map<String, Value>) -> R<bool> {
    let detail = loads_or_empty(job.get("detail"));
    Ok(get(&detail, "manual")?.is_some_and(truthy))
}
