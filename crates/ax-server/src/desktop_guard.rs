//! The desktop app's per-launch guard, for the routes the front door answers itself.
//!
//! In the desktop app the Python backend refuses every request that lacks the launch secret (the `x-archive-x-desktop` header) or the
//! `ax_desktop` cookie the app's own window receives at start (`DesktopGuardMiddleware` in core/desktop_mode.py). Requests the front door
//! forwards still meet that check; a route answered here must meet it too, or another program on the same PC could talk to it. A request
//! that fails the check is not answered here but forwarded, so Python gives its usual 403.

use hyper::HeaderMap;
use sha2::{Digest, Sha256};

const HEADER: &str = "x-archive-x-desktop";
const COOKIE: &str = "ax_desktop";

fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut k = [0u8; 64];
    if key.len() > 64 {
        k[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let (mut ipad, mut opad) = ([0x36u8; 64], [0x5cu8; 64]);
    for i in 0..64 {
        ipad[i] ^= k[i];
        opad[i] ^= k[i];
    }
    let inner = Sha256::new().chain_update(ipad).chain_update(msg).finalize();
    Sha256::new().chain_update(opad).chain_update(inner).finalize().into()
}

/// What the cookie must hold: `hmac(secret, "archive-x-desktop-cookie")` in hex, so the cookie never contains the secret.
fn cookie_value(secret: &str) -> String {
    hmac_sha256(secret.as_bytes(), b"archive-x-desktop-cookie").iter().map(|b| format!("{b:02x}")).collect()
}

fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// May this request be answered here? Always, when no launch secret is configured (not the desktop app).
pub fn allowed(secret: Option<&str>, headers: &HeaderMap) -> bool {
    let Some(secret) = secret.filter(|s| !s.is_empty()) else { return true };
    if headers.get(HEADER).and_then(|v| v.to_str().ok()).map(|v| same(v, secret)).unwrap_or(false) {
        return true;
    }
    let expected = cookie_value(secret);
    headers
        .get_all(hyper::header::COOKIE)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(';'))
        .filter_map(|part| part.trim().split_once('='))
        .any(|(name, value)| name == COOKIE && same(value, &expected))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hmac_matches_python() {
        // python: hmac.new(b"secret", b"archive-x-desktop-cookie", hashlib.sha256).hexdigest()
        assert_eq!(cookie_value("secret"), include_str!("../tests/desktop_cookie_vector.txt").trim());
    }

    #[test]
    fn header_or_cookie_or_no_secret() {
        let mut h = HeaderMap::new();
        assert!(allowed(None, &h));
        assert!(!allowed(Some("secret"), &h));
        h.insert(HEADER, "secret".parse().unwrap());
        assert!(allowed(Some("secret"), &h));
        let mut c = HeaderMap::new();
        c.insert(hyper::header::COOKIE, format!("a=b; ax_desktop={}; c=d", cookie_value("secret")).parse().unwrap());
        assert!(allowed(Some("secret"), &c));
        c.insert(hyper::header::COOKIE, "ax_desktop=wrong".parse().unwrap());
        assert!(!allowed(Some("secret"), &c));
    }
}
