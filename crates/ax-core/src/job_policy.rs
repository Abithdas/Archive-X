//! Port of the pure policy of `telegram_archive/jobs/priorities.py` and `jobs/orchestrator.py`: job priorities, failure classes and retry
//! delays. The rest of the orchestrator (admission, leases, dependencies) reads and writes the jobs tables and the resource manager, so it moves
//! with the database port.

use serde_json::Value;

pub const DEFAULT_PRIORITY: i64 = 50;

/// Priority of each job type; lower runs first.
pub const PRIORITY_BY_TYPE: &[(&str, i64)] = &[
    ("CONTENT_AUTOMATION", 20),
    ("UPLOAD_LOCAL", 25),
    ("TELEGRAM_TRANSFER", 25),
    ("MEDIA_MANAGER", 25),
    ("METADATA_SYNC", 40),
    ("SUGGESTION_SCAN", 45),
    ("ENTITY_IMAGE_SYNC", 55),
    ("THUMBNAIL_INDEXER", 60),
    ("METADATA_ASSETS", 50),
    ("METADATA_CHANNEL_PIPELINE", 48),
    ("ASSET_PREFETCH", 70),
    ("PREVIEW_PREFETCH", 78),
];

/// Python's `int(value)` for a JSON value; `None` where Python raises (`ValueError`, `TypeError`, `OverflowError`).
/// Integers beyond `i64` saturate: the callers clamp to 0..=100 anyway.
fn py_int(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else if n.is_u64() {
                Some(i64::MAX)
            } else {
                let f = n.as_f64()?;
                if f.is_finite() {
                    Some(f.trunc().clamp(i64::MIN as f64, i64::MAX as f64) as i64)
                } else {
                    None
                }
            }
        }
        Value::String(s) => {
            // int(str): surrounding whitespace, one sign, digits with single underscores between them
            let t = s.trim();
            let (neg, digits) = match t.strip_prefix('-') {
                Some(r) => (true, r),
                None => (false, t.strip_prefix('+').unwrap_or(t)),
            };
            let valid =
                !digits.is_empty() && !digits.starts_with('_') && !digits.ends_with('_') && !digits.contains("__") && digits.chars().all(|c| c.is_ascii_digit() || c == '_');
            if !valid {
                return None;
            }
            let clean: String = digits.chars().filter(|c| *c != '_').collect();
            let magnitude = clean.parse::<i64>().unwrap_or(i64::MAX);
            Some(if neg { -magnitude } else { magnitude })
        }
        _ => None,
    }
}

/// `job_priority`: a bounded integer for a saved job row. A priority stored on the row wins; else `detail.priority`; else the job type's.
pub fn job_priority(priority: Option<&Value>, job_type: &str, detail: Option<&Value>) -> i64 {
    if let Some(p) = priority {
        let blank = p.is_null() || p.as_str() == Some("");
        if !blank {
            if let Some(n) = py_int(p) {
                return n.clamp(0, 100);
            }
        }
    }
    let base = PRIORITY_BY_TYPE.iter().find(|(t, _)| *t == job_type).map(|(_, p)| *p).unwrap_or(DEFAULT_PRIORITY);
    // detail is JSON text or an object; anything else, or text that is not JSON, is no detail
    let parsed: Option<Value> = match detail {
        Some(Value::String(s)) => serde_json::from_str(s).ok(),
        Some(v @ Value::Object(_)) => Some(v.clone()),
        _ => None,
    };
    let override_ = parsed.as_ref().and_then(|d| d.get("priority")).and_then(py_int).unwrap_or(base);
    override_.clamp(0, 100)
}

/// Failures worth retrying, and ones that are not.
pub const TRANSIENT_FAILURES: &[&str] = &["TIMEOUT", "NETWORK", "THROTTLED", "RESOURCE"];

/// What the failure classifier looks at: the error's type name and message, and which of Python's built-in families it belongs to.
#[derive(Debug, Clone, Default)]
pub struct Failure<'a> {
    pub type_name: &'a str,
    pub message: &'a str,
    /// `TimeoutError` or `asyncio.TimeoutError`.
    pub is_timeout: bool,
    /// `ConnectionError` or `OSError` (including its subclasses).
    pub is_os_error: bool,
    /// `ValueError`, `KeyError` or `TypeError`.
    pub is_input_error: bool,
}

/// `JobOrchestrator.classify_failure`.
pub fn classify_failure(f: &Failure) -> &'static str {
    let name = f.type_name.to_lowercase();
    let text = f.message.to_lowercase();
    if name.contains("floodwait") || text.contains("flood wait") {
        return "THROTTLED";
    }
    if f.is_timeout || name.contains("timeout") || text.contains("timed out") {
        return "TIMEOUT";
    }
    if f.is_os_error || ["connection reset", "network", "temporarily unavailable"].iter().any(|x| text.contains(x)) {
        return "NETWORK";
    }
    if text.contains("resource") || text.contains("capacity") {
        return "RESOURCE";
    }
    if f.is_input_error {
        return "INVALID_INPUT";
    }
    "UNKNOWN"
}

/// Seconds before the next try of a failed job, or `None` when it is not retried: the failure is transient and `retry_count` is still below
/// `max_retries`. `retry_count` is the number of retries already made; the delay doubles per attempt (capped at 2^8) up to `max_delay`, and
/// `jitter_percent` (0 to 19) spreads jobs that failed together. (Python's jitter comes from `hash(job_id)`, which differs per process.)
pub fn retry_delay(failure: &str, retry_count: i64, max_retries: i64, max_delay: f64, jitter_percent: u32) -> Option<f64> {
    if !TRANSIENT_FAILURES.contains(&failure) || retry_count >= max_retries {
        return None;
    }
    let attempt = (retry_count + 1).clamp(0, 8) as i32;
    let delay = max_delay.min(2f64.powi(attempt));
    Some(delay * (1.0 + f64::from(jitter_percent.min(19)) / 100.0))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_stored_priority_wins_and_is_clamped() {
        assert_eq!(job_priority(Some(&json!(7)), "UPLOAD_LOCAL", None), 7);
        assert_eq!(job_priority(Some(&json!(500)), "UPLOAD_LOCAL", None), 100);
        assert_eq!(job_priority(Some(&json!(-3)), "UPLOAD_LOCAL", None), 0);
        assert_eq!(job_priority(Some(&json!(" 12 ")), "X", None), 12);
        assert_eq!(job_priority(Some(&json!("1_0")), "X", None), 10);
        assert_eq!(job_priority(Some(&json!(true)), "X", None), 1);
        assert_eq!(job_priority(Some(&json!(33.9)), "X", None), 33);
    }

    #[test]
    fn otherwise_detail_then_the_type() {
        assert_eq!(job_priority(None, "UPLOAD_LOCAL", None), 25);
        assert_eq!(job_priority(Some(&json!("")), "METADATA_SYNC", None), 40);
        assert_eq!(job_priority(Some(&json!("x")), "METADATA_SYNC", None), 40, "not a number: the type's");
        assert_eq!(job_priority(None, "UNKNOWN_TYPE", None), 50);
        assert_eq!(job_priority(None, "UPLOAD_LOCAL", Some(&json!(r#"{"priority": 9}"#))), 9);
        assert_eq!(job_priority(None, "UPLOAD_LOCAL", Some(&json!({"priority": "250"}))), 100);
        assert_eq!(job_priority(None, "UPLOAD_LOCAL", Some(&json!("not json"))), 25);
        assert_eq!(job_priority(None, "UPLOAD_LOCAL", Some(&json!({"priority": null}))), 25);
        assert_eq!(job_priority(None, "UPLOAD_LOCAL", Some(&json!([1, 2]))), 25);
    }

    #[test]
    fn failures_are_classified_by_name_then_text_then_family() {
        let f = |type_name, message| Failure { type_name, message, ..Default::default() };
        assert_eq!(classify_failure(&f("FloodWaitError", "")), "THROTTLED");
        assert_eq!(classify_failure(&f("RPCError", "A Flood Wait of 5s")), "THROTTLED");
        assert_eq!(classify_failure(&f("ReadTimeout", "")), "TIMEOUT");
        assert_eq!(classify_failure(&Failure { is_timeout: true, ..Default::default() }), "TIMEOUT");
        assert_eq!(classify_failure(&f("X", "the operation timed out")), "TIMEOUT");
        assert_eq!(classify_failure(&Failure { is_os_error: true, ..Default::default() }), "NETWORK");
        assert_eq!(classify_failure(&f("X", "Connection reset by peer")), "NETWORK");
        assert_eq!(classify_failure(&f("X", "no capacity left")), "RESOURCE");
        assert_eq!(classify_failure(&Failure { is_input_error: true, ..Default::default() }), "INVALID_INPUT");
        assert_eq!(classify_failure(&f("Bug", "boom")), "UNKNOWN");
        // order: the flood wait beats the os error, a timeout beats the network
        assert_eq!(classify_failure(&Failure { type_name: "FloodWaitError", is_os_error: true, ..Default::default() }), "THROTTLED");
        assert_eq!(classify_failure(&Failure { type_name: "Timeout", is_os_error: true, ..Default::default() }), "TIMEOUT");
    }

    #[test]
    fn only_transient_failures_with_retries_left_are_retried() {
        assert_eq!(retry_delay("TIMEOUT", 0, 3, 300.0, 0), Some(2.0));
        assert_eq!(retry_delay("TIMEOUT", 1, 3, 300.0, 0), Some(4.0));
        assert_eq!(retry_delay("TIMEOUT", 2, 3, 300.0, 10), Some(8.0 * 1.1));
        assert_eq!(retry_delay("TIMEOUT", 3, 3, 300.0, 0), None, "no retries left");
        assert_eq!(retry_delay("INVALID_INPUT", 0, 3, 300.0, 0), None);
        assert_eq!(retry_delay("UNKNOWN", 0, 3, 300.0, 0), None);
        assert_eq!(retry_delay("NETWORK", 7, 20, 300.0, 0), Some(256.0), "doubling stops at 2^8");
        assert_eq!(retry_delay("NETWORK", 12, 20, 100.0, 0), Some(100.0), "and is capped by the maximum");
        assert_eq!(retry_delay("NETWORK", 0, 3, 300.0, 99), Some(2.0 * 1.19), "jitter stays below 20 percent");
    }
}
