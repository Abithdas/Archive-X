//! The cross-site guard for requests the front door answers itself. A line-for-line port of the Python backend's
//! `BrowserMutationGuardMiddleware` (core/http_middleware.py): a state-changing request to the API (or /login, /logout)
//! that names an `Origin` must name this very host. Requests the front door forwards are guarded by Python as before.
//!
//! tests/origin_vectors.json is produced by the real Python middleware (tools/parity/gen_origin_vectors.py); every vector
//! must give the same answer here.

/// Does this request need the check at all? (POST, PUT, PATCH and DELETE under /api/, plus /login and /logout.)
pub fn needs_check(method: &str, path: &str) -> bool {
    matches!(method.to_ascii_uppercase().as_str(), "POST" | "PUT" | "PATCH" | "DELETE") && (path.starts_with("/api/") || path == "/login" || path == "/logout")
}

/// The authority part of an origin the way Python's `urllib.parse.urlsplit(origin).netloc` finds it ("" when there is none,
/// or the brackets are unbalanced, which Python reports as an error).
fn netloc(origin: &str) -> &str {
    let mut rest = origin;
    if let Some(i) = rest.find(':') {
        let scheme = &rest[..i];
        let mut chars = scheme.chars();
        let starts_with_letter = chars.next().map(|c| c.is_ascii_alphabetic()).unwrap_or(false);
        if starts_with_letter && scheme.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.')) {
            rest = &rest[i + 1..];
        }
    }
    let Some(after) = rest.strip_prefix("//") else { return "" };
    let end = after.find(['/', '?', '#']).unwrap_or(after.len());
    let authority = &after[..end];
    if authority.contains('[') != authority.contains(']') {
        return "";
    }
    authority
}

/// May this request go ahead? `origin` is the `Origin` header if there is one, `host` the `Host` header ("" if missing).
pub fn allowed(method: &str, path: &str, origin: Option<&str>, host: &str) -> bool {
    if !needs_check(method, path) {
        return true;
    }
    match origin {
        None | Some("") => true,
        Some(o) => {
            let n = netloc(o);
            !n.is_empty() && n.to_lowercase() == host.to_lowercase()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_and_other_methods_are_never_checked() {
        assert!(allowed("GET", "/api/status", Some("https://evil.test"), "archive.test"));
        assert!(allowed("OPTIONS", "/api/x", Some("https://evil.test"), "archive.test"));
        assert!(allowed("POST", "/photos", Some("https://evil.test"), "archive.test"), "only the API, /login and /logout are guarded");
    }

    #[test]
    fn a_mutation_must_come_from_this_host() {
        assert!(allowed("POST", "/api/x", Some("https://archive.test"), "archive.test"));
        assert!(allowed("POST", "/api/x", Some("http://ARCHIVE.test:8443"), "archive.test:8443"), "scheme and case are ignored");
        assert!(!allowed("POST", "/api/x", Some("https://evil.test"), "archive.test"));
        assert!(!allowed("DELETE", "/login", Some("null"), "archive.test"));
        assert!(allowed("POST", "/api/x", None, "archive.test"), "no Origin: not a browser cross-site request");
    }
}
