//! Port of `telegram_archive/core/retry_policy.py`: classify an error into a retry decision.
//!
//! The Python version takes the exception object. Here the caller passes what the classifier actually reads: the error's type name,
//! its message, and its `seconds` attribute if it has one. `jitter` (0..1) replaces `random.random()` so results are testable.

use regex::Regex;
use std::sync::OnceLock;

#[derive(Debug, Clone, PartialEq)]
pub struct RetryDecision {
    pub category: &'static str,
    pub retry: bool,
    pub delay: f64,
    pub refresh_message: bool,
    pub reason: String,
}

#[derive(Debug, Clone)]
pub struct RetryPolicy {
    pub max_attempts: u32,
    pub base_delay: f64,
    pub max_delay: f64,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self::new(4, 0.35, 8.0)
    }
}

/// An error as the classifier sees it.
#[derive(Debug, Clone, Default)]
pub struct ErrorInfo<'a> {
    pub type_name: &'a str,
    pub message: &'a str,
    /// Telethon's `FloodWaitError.seconds` and friends.
    pub seconds: Option<i64>,
}

impl ErrorInfo<'_> {
    fn text(&self) -> String {
        format!("{} {}", self.type_name, self.message).to_uppercase()
    }
}

fn flood_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"FLOOD(?:_PREMIUM)?_WAIT[_ ](\d+)").unwrap())
}

impl RetryPolicy {
    pub fn new(max_attempts: i64, base_delay: f64, max_delay: f64) -> Self {
        let base = base_delay.max(0.05);
        Self { max_attempts: max_attempts.max(1) as u32, base_delay: base, max_delay: max_delay.max(base) }
    }

    /// Seconds Telegram asked us to wait: the `seconds` attribute if present (floored at 0), else parsed from the text, else 0.
    pub fn flood_seconds(err: &ErrorInfo) -> i64 {
        if let Some(s) = err.seconds {
            return s.max(0);
        }
        flood_re().captures(&err.text()).map(|c| c[1].parse::<i64>().unwrap_or(i64::MAX)).unwrap_or(0)
    }

    pub fn classify(&self, err: &ErrorInfo, attempt: u32, jitter: f64) -> RetryDecision {
        let text = err.text();
        let more = attempt < self.max_attempts;
        let d = |category, retry, delay, refresh_message, reason: &str| RetryDecision { category, retry, delay, refresh_message, reason: reason.to_string() };

        if text.contains("FILE_REFERENCE") || text.contains("FILEREFERENCE") {
            return d("file_reference", more, 0.1, true, "refresh telegram message");
        }
        if text.contains("FLOOD") {
            let wait = Self::flood_seconds(err);
            return RetryDecision {
                category: "flood_wait",
                retry: more && wait <= 30,
                delay: wait.clamp(1, 30) as f64,
                refresh_message: false,
                reason: format!("telegram requested {wait}s"),
            };
        }
        if ["TIMEOUT", "TIMED OUT", "CONNECTIONRESET", "SERVERERROR", "TEMPORARY", "NETWORK"].iter().any(|x| text.contains(x)) {
            let exp = attempt.saturating_sub(1).min(60) as i32;
            let mut delay = self.max_delay.min(self.base_delay * 2f64.powi(exp));
            delay += jitter * 0.25f64.min(delay * 0.25);
            return d("transient", more, delay, false, "transient network/remote error");
        }
        if text.contains("FILE_MIGRATE") || text.contains("FILEMIGRATE") {
            return d("migrate", more, 0.1, false, "retry through another connection");
        }
        d("permanent", false, 0.0, false, "non-retryable error")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn e<'a>(t: &'a str, m: &'a str) -> ErrorInfo<'a> {
        ErrorInfo { type_name: t, message: m, seconds: None }
    }

    #[test]
    fn constructor_clamps_like_python() {
        let p = RetryPolicy::new(0, 0.0, 0.0);
        assert_eq!((p.max_attempts, p.base_delay, p.max_delay), (1, 0.05, 0.05));
        let p = RetryPolicy::new(3, 1.0, 0.5);
        assert_eq!(p.max_delay, 1.0);
    }

    #[test]
    fn flood_seconds_prefers_attribute_then_text() {
        assert_eq!(RetryPolicy::flood_seconds(&ErrorInfo { seconds: Some(12), ..e("FloodWaitError", "x") }), 12);
        assert_eq!(RetryPolicy::flood_seconds(&ErrorInfo { seconds: Some(-4), ..e("FloodWaitError", "x") }), 0);
        assert_eq!(RetryPolicy::flood_seconds(&e("RPCError", "A wait of 5 seconds FLOOD_WAIT_5")), 5);
        assert_eq!(RetryPolicy::flood_seconds(&e("RPCError", "flood_premium_wait 9")), 9);
        assert_eq!(RetryPolicy::flood_seconds(&e("RPCError", "FLOOD but no number")), 0);
        assert_eq!(RetryPolicy::flood_seconds(&e("RPCError", "FLOOD_WAIT_99999999999999999999999")), i64::MAX);
    }

    #[test]
    fn flood_wait_over_30s_is_not_retried_and_delay_is_capped() {
        let p = RetryPolicy::default();
        let d = p.classify(&ErrorInfo { seconds: Some(45), ..e("FloodWaitError", "") }, 1, 0.0);
        assert_eq!((d.category, d.retry, d.delay), ("flood_wait", false, 30.0));
        assert_eq!(d.reason, "telegram requested 45s");
        let d = p.classify(&ErrorInfo { seconds: Some(0), ..e("FloodWaitError", "") }, 1, 0.0);
        assert_eq!((d.retry, d.delay), (true, 1.0));
        let d = p.classify(&ErrorInfo { seconds: Some(5), ..e("FloodWaitError", "") }, 4, 0.0);
        assert!(!d.retry);
    }

    #[test]
    fn file_reference_refreshes_the_message() {
        let d = RetryPolicy::default().classify(&e("FileReferenceExpiredError", ""), 1, 0.0);
        assert_eq!((d.category, d.retry, d.delay, d.refresh_message), ("file_reference", true, 0.1, true));
    }

    #[test]
    fn transient_backs_off_exponentially_with_bounded_jitter() {
        let p = RetryPolicy::default();
        let d = p.classify(&e("TimeoutError", "timed out"), 1, 0.0);
        assert_eq!((d.category, d.retry, d.delay), ("transient", true, 0.35));
        assert_eq!(p.classify(&e("ServerError", ""), 3, 0.0).delay, 1.4);
        assert_eq!(p.classify(&e("ServerError", ""), 9, 0.0).delay, 8.0);
        // jitter adds at most min(0.25, delay * 0.25)
        let hi = p.classify(&e("ServerError", ""), 9, 1.0).delay;
        assert_eq!(hi, 8.0 + 0.25);
        let lo = p.classify(&e("ServerError", ""), 1, 1.0).delay;
        assert!((lo - (0.35 + 0.35 * 0.25)).abs() < 1e-12);
        assert!(!p.classify(&e("ServerError", ""), 4, 0.0).retry);
    }

    #[test]
    fn migrate_and_permanent() {
        let p = RetryPolicy::default();
        let d = p.classify(&e("FileMigrateError", ""), 1, 0.0);
        assert_eq!((d.category, d.retry, d.delay), ("migrate", true, 0.1));
        let d = p.classify(&e("ValueError", "bad"), 1, 0.0);
        assert_eq!((d.category, d.retry, d.delay, d.reason.as_str()), ("permanent", false, 0.0, "non-retryable error"));
    }

    #[test]
    fn first_matching_rule_wins() {
        // FILE_REFERENCE beats FLOOD, FLOOD beats TIMEOUT
        let p = RetryPolicy::default();
        assert_eq!(p.classify(&e("X", "FILE_REFERENCE FLOOD"), 1, 0.0).category, "file_reference");
        assert_eq!(p.classify(&e("X", "FLOOD TIMEOUT"), 1, 0.0).category, "flood_wait");
    }
}
