//! Port of `telegram_archive/core/net_errors.py`: a network error in words a person can act on.
//!
//! Python inspects the exception's class (`ConnectionResetError`, `socket.timeout`, `ssl.SSLError`, `urllib.error.HTTPError` ...) and its
//! text. Here the caller says which class family it was (`Kind`) and passes the text; the same text patterns still apply.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    ConnectionReset,
    Timeout,
    /// `socket.gaierror`
    Dns,
    Ssl,
    Other,
}

#[derive(Debug, Clone)]
pub struct NetError {
    pub kind: Kind,
    /// `str(reason)` (for a `URLError`, its `.reason`).
    pub text: String,
    /// Set when the error is an HTTP status answer (`urllib.error.HTTPError`).
    pub http_code: Option<u16>,
    /// `URLError` or `OSError`: the "could not be reached" fallback applies.
    pub is_network: bool,
}

/// One short sentence for what went wrong reaching a provider; `""` for an error that is not a network one.
pub fn explain(err: &NetError, host: &str) -> String {
    let text = err.text.as_str();
    let where_ = if host.is_empty() { String::new() } else { format!(" ({host})") };
    if err.kind == Kind::ConnectionReset || text.contains("10054") || text.contains("forcibly closed") || text.contains("Connection reset") {
        return format!("The provider{where_} closed the connection. Your network, ISP or a firewall may be blocking it; try another network, a VPN or another DNS.");
    }
    if err.kind == Kind::Timeout || text.contains("timed out") {
        return format!("The provider{where_} did not answer in time. Check the connection and try again.");
    }
    if err.kind == Kind::Dns || text.contains("getaddrinfo") || text.contains("Name or service not known") {
        return format!("The provider's address{where_} could not be found. Check the internet connection or DNS.");
    }
    if err.kind == Kind::Ssl || text.to_uppercase().contains("CERTIFICATE") {
        return format!("A secure connection to the provider{where_} could not be made (certificate or TLS problem).");
    }
    if let Some(code) = err.http_code {
        return match code {
            401 | 403 => format!("The provider{where_} refused the key (HTTP {code}). Check it in Settings, Metadata."),
            429 => format!("The provider{where_} says too many requests (HTTP 429). It will work again after a short wait."),
            _ => format!("The provider{where_} answered HTTP {code}."),
        };
    }
    if err.is_network {
        let shown: String = text.chars().take(120).collect();
        return format!("The provider{where_} could not be reached: {shown}");
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn err(kind: Kind, text: &str) -> NetError {
        NetError { kind, text: text.into(), http_code: None, is_network: true }
    }

    #[test]
    fn classes_and_text_patterns_pick_the_same_sentence() {
        assert!(explain(&err(Kind::ConnectionReset, ""), "x.org").contains("closed the connection"));
        assert!(explain(&err(Kind::Other, "[WinError 10054] An existing connection was forcibly closed"), "").contains("closed the connection"));
        assert!(explain(&err(Kind::Timeout, ""), "").contains("did not answer in time"));
        assert!(explain(&err(Kind::Other, "The read operation timed out"), "").contains("did not answer in time"));
        assert!(explain(&err(Kind::Dns, ""), "").contains("could not be found"));
        assert!(explain(&err(Kind::Other, "getaddrinfo failed"), "").contains("could not be found"));
        assert!(explain(&err(Kind::Ssl, ""), "").contains("certificate or TLS"));
        assert!(explain(&err(Kind::Other, "bad certificate_verify"), "").contains("certificate or TLS"));
    }

    #[test]
    fn host_is_shown_in_brackets_only_when_given() {
        assert!(explain(&err(Kind::Timeout, ""), "api.tmdb.org").starts_with("The provider (api.tmdb.org) did not"));
        assert!(explain(&err(Kind::Timeout, ""), "").starts_with("The provider did not"));
        assert!(explain(&err(Kind::Dns, ""), "h").starts_with("The provider's address (h) could"));
    }

    #[test]
    fn http_codes() {
        let http = |c| NetError { kind: Kind::Other, text: "HTTP Error".into(), http_code: Some(c), is_network: true };
        assert_eq!(explain(&http(401), ""), "The provider refused the key (HTTP 401). Check it in Settings, Metadata.");
        assert!(explain(&http(403), "h").contains("(h) refused the key (HTTP 403)"));
        assert!(explain(&http(429), "").contains("too many requests (HTTP 429)"));
        assert_eq!(explain(&http(500), ""), "The provider answered HTTP 500.");
    }

    #[test]
    fn fallbacks() {
        let long = "x".repeat(300);
        let s = explain(&err(Kind::Other, &long), "");
        assert_eq!(s, format!("The provider could not be reached: {}", "x".repeat(120)));
        assert_eq!(explain(&NetError { kind: Kind::Other, text: "boom".into(), http_code: None, is_network: false }, ""), "");
    }

    #[test]
    fn text_cut_counts_characters_not_bytes() {
        let s = explain(&err(Kind::Other, &"é".repeat(130)), "");
        assert!(s.ends_with(&"é".repeat(120)));
    }
}
