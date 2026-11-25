//! The small decisions of `core/desktop_mode.py`: whether the backend runs for the desktop shell, where its window lands, the cookie that
//! proves a request came from the shell, and the page that hands the session over. (The guard that applies them to requests is in `ax-server`.)

use crate::pyfmt::{html_escape, json_string_ascii, py_int_str, py_strip};
use crate::Env;
use hmac::{Hmac, Mac};
use sha2::Sha256;

pub const HEADER: &str = "x-archive-x-desktop";
pub const COOKIE: &str = "ax_desktop";
pub const READY_PREFIX: &str = "ARCHIVE_X_READY ";

/// `ARCHIVE_X_DESKTOP`: `1`, `true`, `yes` or `on`, in any letter case.
pub fn enabled(env: &Env) -> bool {
    let v = py_strip(env.get("ARCHIVE_X_DESKTOP").unwrap_or("0")).to_lowercase();
    matches!(v.as_str(), "1" | "true" | "yes" | "on")
}

/// The path the window opens after the session hand-off: `/` unless `ARCHIVE_X_DESKTOP_LANDING` is a plain absolute path.
pub fn landing(env: &Env) -> String {
    let v = py_strip(env.get("ARCHIVE_X_DESKTOP_LANDING").unwrap_or("/"));
    let v = if v.is_empty() { "/" } else { v };
    let ok = v.starts_with('/') && !v.starts_with("//") && !v.contains('\\') && !v.contains(':') && v.chars().count() < 200;
    if ok {
        v.to_string()
    } else {
        "/".to_string()
    }
}

/// `ARCHIVE_X_DESKTOP_PORT`: zero (pick a free port) when absent, negative or not a number.
pub fn requested_port(env: &Env) -> i64 {
    match env.get("ARCHIVE_X_DESKTOP_PORT") {
        None | Some("") => 0,
        Some(v) => py_int_str(v).map_or(0, |n| n.max(0)),
    }
}

/// The per-launch secret: the one the shell passed, or a fresh random one when started by hand.
pub fn launch_secret(env: &Env) -> String {
    let given = py_strip(env.get("ARCHIVE_X_LAUNCH_SECRET").unwrap_or(""));
    if !given.is_empty() {
        return given.to_string();
    }
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("the system random source is available");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The guard cookie's value: an HMAC of a fixed text under the secret, so the cookie never holds the secret itself.
pub fn cookie_value(secret: &str) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("HMAC takes a key of any length");
    mac.update(b"archive-x-desktop-cookie");
    mac.finalize().into_bytes().iter().map(|b| format!("{b:02x}")).collect()
}

/// Constant-time comparison of a presented secret with the real one; nothing presented never matches.
/// (Python's `hmac.compare_digest` raises for a non-ASCII text; this answers no.)
pub fn secret_matches(candidate: Option<&str>, secret: &str) -> bool {
    match candidate {
        Some(c) if !c.is_empty() && c.len() == secret.len() => c.bytes().zip(secret.bytes()).fold(0u8, |acc, (a, b)| acc | (a ^ b)) == 0,
        _ => false,
    }
}

/// The page the shell opens once to start the session: it moves on to the landing path by itself (a redirect would lose the cookies).
pub fn handoff_page(landing_path: &str) -> String {
    format!(
        "<!doctype html><meta charset=\"utf-8\"><title>Archive X</title><meta http-equiv=\"refresh\" content=\"0;url={}\"><style>html{{background:#232833}}</style><script>location.replace({})</script>",
        html_escape(landing_path),
        json_string_ascii(landing_path)
    )
}

/// The one line the backend prints for the shell once it is serving.
pub fn ready_line(port: u16, pid: u32, version: &str) -> String {
    format!("{READY_PREFIX}{{\"port\": {port}, \"pid\": {pid}, \"version\": {}}}", json_string_ascii(version))
}
