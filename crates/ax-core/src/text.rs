//! Small text helpers that follow Python's `str` methods.

/// `str.casefold()`: full Unicode case folding (`ß` becomes `ss`, final sigma folds to sigma, and so on).
pub fn casefold(s: &str) -> String {
    caseless::default_case_fold_str(s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folds_like_python() {
        assert_eq!(casefold("Straße"), "strasse");
        assert_eq!(casefold("ΣΊΣΥΦΟΣ"), "σίσυφοσ");
        assert_eq!(casefold("ABC"), "abc");
        assert_eq!(casefold(""), "");
    }
}
