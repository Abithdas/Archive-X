//! Port of `telegram_archive/core/resolution_filter.py`: resolution tiers for the Transfer Manager.

pub const RESOLUTION_FILTERS: [&str; 7] = ["ANY", "480P", "720P", "1080P", "1440P", "2160P", "4320P"];

// (tier, lower bound inclusive, upper bound exclusive). Bands use the shorter video dimension.
const BANDS: [(&str, i64, Option<i64>); 6] =
    [("4320P", 4320, None), ("2160P", 2160, Some(4320)), ("1440P", 1440, Some(2160)), ("1080P", 1080, Some(1440)), ("720P", 720, Some(1080)), ("480P", 1, Some(720))];

pub const INVALID: &str = "Choose Any resolution or a supported resolution tier";

/// Validate a selection. `None` and empty text mean `ANY`. Accepts `720`, `720p`, `720 P`, any case.
pub fn normalize_resolution_filter(value: Option<&str>) -> Result<String, &'static str> {
    let text = value.unwrap_or("");
    let text = if text.is_empty() { "ANY" } else { text };
    // Python: str.strip().upper().replace(" ", "")
    let mut n: String = text.trim().to_uppercase().replace(' ', "");
    let digits = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit());
    if n.ends_with('P') && digits(&n[..n.len() - 1]) {
        // Python re-formats via f"{n[:-1]}P", which is the same string.
    } else if digits(&n) {
        n.push('P');
    }
    if RESOLUTION_FILTERS.contains(&n.as_str()) {
        Ok(n)
    } else {
        Err(INVALID)
    }
}

/// The tier for the given dimensions (shorter edge), or `None` when unknown (zero, negative).
pub fn resolution_tier_for_dimensions(width: i64, height: i64) -> Option<&'static str> {
    let short_edge = width.min(height);
    if short_edge <= 0 {
        return None;
    }
    BANDS.iter().find(|(_, lo, hi)| short_edge >= *lo && hi.map_or(true, |h| short_edge < h)).map(|(t, _, _)| *t)
}

/// `ANY` keeps everything, including unknown dimensions; a specific tier excludes unknown dimensions.
pub fn matches_resolution_filter(width: i64, height: i64, selection: Option<&str>) -> Result<bool, &'static str> {
    let n = normalize_resolution_filter(selection)?;
    Ok(n == "ANY" || resolution_tier_for_dimensions(width, height) == Some(n.as_str()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_like_python() {
        assert_eq!(normalize_resolution_filter(None).unwrap(), "ANY");
        assert_eq!(normalize_resolution_filter(Some("")).unwrap(), "ANY");
        assert_eq!(normalize_resolution_filter(Some(" 720 ")).unwrap(), "720P");
        assert_eq!(normalize_resolution_filter(Some("1080p")).unwrap(), "1080P");
        assert_eq!(normalize_resolution_filter(Some("2160 P")).unwrap(), "2160P");
        assert_eq!(normalize_resolution_filter(Some("any")).unwrap(), "ANY");
        assert!(normalize_resolution_filter(Some("900")).is_err());
        assert!(normalize_resolution_filter(Some("HD")).is_err());
    }

    #[test]
    fn tiers_use_the_shorter_edge() {
        assert_eq!(resolution_tier_for_dimensions(1920, 1080), Some("1080P"));
        assert_eq!(resolution_tier_for_dimensions(1080, 1920), Some("1080P"));
        assert_eq!(resolution_tier_for_dimensions(3840, 2160), Some("2160P"));
        assert_eq!(resolution_tier_for_dimensions(7680, 4320), Some("4320P"));
        assert_eq!(resolution_tier_for_dimensions(640, 360), Some("480P"));
        assert_eq!(resolution_tier_for_dimensions(1, 1), Some("480P"));
        assert_eq!(resolution_tier_for_dimensions(1279, 719), Some("480P"));
        assert_eq!(resolution_tier_for_dimensions(1280, 720), Some("720P"));
        assert_eq!(resolution_tier_for_dimensions(0, 1080), None);
        assert_eq!(resolution_tier_for_dimensions(-5, 1080), None);
    }

    #[test]
    fn any_keeps_unknown_dimensions_but_a_tier_does_not() {
        assert!(matches_resolution_filter(0, 0, None).unwrap());
        assert!(!matches_resolution_filter(0, 0, Some("720p")).unwrap());
        assert!(matches_resolution_filter(1920, 1080, Some("1080")).unwrap());
        assert!(!matches_resolution_filter(1920, 1080, Some("720")).unwrap());
        assert!(matches_resolution_filter(1, 1, Some("bogus")).is_err());
    }
}
