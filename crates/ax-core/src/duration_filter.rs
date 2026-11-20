//! Port of `telegram_archive/core/duration_filter.py`: "only videos shorter than N minutes".
//!
//! `message_duration_seconds` is not ported: it reads attributes of a Telethon message object, so it moves with the Telegram client.

pub const MAX_MINUTES_LIMIT: f64 = 24.0 * 60.0;

pub const NOT_A_NUMBER: &str = "Enter the maximum duration in minutes as a number, or leave it empty for no limit";
pub const NOT_POSITIVE: &str = "The maximum duration must be a positive number of minutes";

pub fn too_long() -> String {
    format!("The maximum duration cannot be more than {} minutes", MAX_MINUTES_LIMIT as i64)
}

/// Python's `float(text)` for the inputs this module can see: surrounding spaces were already stripped, `,` already became `.`.
/// Underscores are allowed between digits (`1_0`), as Python allows; `inf`, `infinity` and `nan` parse (and are rejected later).
fn py_float(text: &str) -> Option<f64> {
    let b = text.as_bytes();
    let mut cleaned = String::with_capacity(text.len());
    for (i, c) in text.char_indices() {
        if c == '_' {
            let between_digits = i > 0 && i + 1 < b.len() && b[i - 1].is_ascii_digit() && b[i + 1].is_ascii_digit();
            if !between_digits {
                return None;
            }
            continue;
        }
        cleaned.push(c);
    }
    cleaned.parse::<f64>().ok()
}

/// The limit in minutes, or `None` for no limit. Empty, missing or 0 mean no limit; anything else must be a positive number.
/// (`bool` inputs, which Python maps to no limit, are the caller's business: pass `None`.)
pub fn normalize_max_minutes(value: Option<&str>) -> Result<Option<f64>, String> {
    let Some(raw) = value else { return Ok(None) };
    let text = raw.trim().replace(',', ".");
    if text.is_empty() {
        return Ok(None);
    }
    let minutes = py_float(&text).ok_or_else(|| NOT_A_NUMBER.to_string())?;
    if minutes.is_nan() || minutes.is_infinite() || minutes < 0.0 {
        return Err(NOT_POSITIVE.to_string());
    }
    if minutes == 0.0 {
        return Ok(None);
    }
    if minutes > MAX_MINUTES_LIMIT {
        return Err(too_long());
    }
    Ok(Some(minutes))
}

/// Strictly shorter than the limit (10 means under 10:00). No limit keeps everything; an unknown duration is not kept.
pub fn within_limit(duration_seconds: Option<f64>, max_minutes: Option<f64>) -> bool {
    let Some(max) = max_minutes else { return true };
    let seconds = duration_seconds.unwrap_or(0.0);
    0.0 < seconds && seconds < max * 60.0
}

/// A real, positive number of seconds.
pub fn known(duration_seconds: Option<f64>) -> bool {
    duration_seconds.unwrap_or(0.0) > 0.0
}

pub fn describe(max_minutes: Option<f64>) -> String {
    match max_minutes {
        None => "any length".to_string(),
        Some(m) if m.fract() == 0.0 => format!("under {} min", m as i64),
        Some(m) => {
            // Python round(m, 2) prints the shortest repr of the rounded float; {} does the same for f64.
            let r = (m * 100.0).round() / 100.0;
            format!("under {r} min")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_missing_and_zero_mean_no_limit() {
        assert_eq!(normalize_max_minutes(None), Ok(None));
        assert_eq!(normalize_max_minutes(Some("")), Ok(None));
        assert_eq!(normalize_max_minutes(Some("  ")), Ok(None));
        assert_eq!(normalize_max_minutes(Some("0")), Ok(None));
        assert_eq!(normalize_max_minutes(Some("0.0")), Ok(None));
    }

    #[test]
    fn numbers_with_comma_or_dot() {
        assert_eq!(normalize_max_minutes(Some("10")), Ok(Some(10.0)));
        assert_eq!(normalize_max_minutes(Some(" 7,5 ")), Ok(Some(7.5)));
        assert_eq!(normalize_max_minutes(Some("1e1")), Ok(Some(10.0)));
        assert_eq!(normalize_max_minutes(Some("1_0")), Ok(Some(10.0)));
        assert_eq!(normalize_max_minutes(Some("1440")), Ok(Some(1440.0)));
    }

    #[test]
    fn rejects_what_python_rejects() {
        assert_eq!(normalize_max_minutes(Some("abc")), Err(NOT_A_NUMBER.into()));
        assert_eq!(normalize_max_minutes(Some("_1")), Err(NOT_A_NUMBER.into()));
        assert_eq!(normalize_max_minutes(Some("1__0")), Err(NOT_A_NUMBER.into()));
        assert_eq!(normalize_max_minutes(Some("-1")), Err(NOT_POSITIVE.into()));
        assert_eq!(normalize_max_minutes(Some("nan")), Err(NOT_POSITIVE.into()));
        assert_eq!(normalize_max_minutes(Some("inf")), Err(NOT_POSITIVE.into()));
        assert_eq!(normalize_max_minutes(Some("1441")), Err(too_long()));
        assert!(too_long().ends_with("1440 minutes"));
    }

    #[test]
    fn limit_is_strict_and_unknown_is_dropped() {
        assert!(within_limit(Some(599.0), Some(10.0)));
        assert!(!within_limit(Some(600.0), Some(10.0)));
        assert!(!within_limit(None, Some(10.0)));
        assert!(!within_limit(Some(0.0), Some(10.0)));
        assert!(!within_limit(Some(f64::NAN), Some(10.0)));
        assert!(within_limit(None, None));
        assert!(known(Some(0.1)));
        assert!(!known(Some(0.0)));
        assert!(!known(None));
    }

    #[test]
    fn describes() {
        assert_eq!(describe(None), "any length");
        assert_eq!(describe(Some(10.0)), "under 10 min");
        assert_eq!(describe(Some(7.5)), "under 7.5 min");
        assert_eq!(describe(Some(7.126)), "under 7.13 min");
    }
}
