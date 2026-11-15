//! Login sessions, read the way the Python backend stores them (telegram_archive/core/auth.py): the `web_sessions` table
//! holds the SHA-256 hash of each token, never the token. Python creates and ends sessions; Rust only looks them up.
//!
//! One difference to keep in mind: Python slides a session's expiry forward on every use but writes the new value to the
//! database at most every 10 minutes, so the stored expiry can be up to 10 minutes behind. A session is therefore never
//! accepted here that Python would refuse, but one in its last minutes may be refused here a little early.

use rusqlite::OptionalExtension;
use sha2::{Digest, Sha256};

use crate::{Db, DbError};

/// The cookie name (config.SESSION_COOKIE).
pub const SESSION_COOKIE: &str = "session";

#[derive(Debug, Clone, PartialEq)]
pub struct Session {
    pub username: String,
    /// Seconds since the Unix epoch, as stored.
    pub expires: f64,
}

/// Lower-case hex SHA-256 of the token, as `SessionStore._hash` computes it.
pub fn token_hash(token: &str) -> String {
    Sha256::digest(token.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

/// The value of cookie `name` in a `Cookie` header. As in Starlette, the last one wins when a name is repeated.
pub fn cookie_value<'a>(header: &'a str, name: &str) -> Option<&'a str> {
    let mut found = None;
    for pair in header.split(';') {
        if let Some((k, v)) = pair.split_once('=') {
            if k.trim() == name {
                found = Some(v.trim().trim_matches('"'));
            }
        }
    }
    found.filter(|v| !v.is_empty())
}

impl Db {
    /// The live session behind a `Cookie` header, if there is one.
    pub async fn session_for_cookie(&self, cookie_header: &str, now: f64) -> Result<Option<Session>, DbError> {
        let Some(token) = cookie_value(cookie_header, SESSION_COOKIE) else { return Ok(None) };
        let hash = token_hash(token);
        let row = self
            .read(move |c| {
                c.query_row("SELECT username, expires FROM web_sessions WHERE token_hash = ?1", [&hash], |r| Ok(Session { username: r.get(0)?, expires: r.get(1)? }))
                    .optional()
            })
            .await?;
        Ok(row.filter(|s| s.expires >= now))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_hash_matches_python_hashlib_sha256() {
        // python3 -c "import hashlib;print(hashlib.sha256(b'abc').hexdigest())"
        assert_eq!(token_hash("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(token_hash(""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    }

    #[test]
    fn cookies_are_found_like_starlette_finds_them() {
        assert_eq!(cookie_value("session=abc", "session"), Some("abc"));
        assert_eq!(cookie_value("a=1; session=abc; b=2", "session"), Some("abc"));
        assert_eq!(cookie_value("a=1;session=abc", "session"), Some("abc"));
        assert_eq!(cookie_value("session=old; session=new", "session"), Some("new"), "the last one wins");
        assert_eq!(cookie_value("session=\"quoted\"", "session"), Some("quoted"));
        assert_eq!(cookie_value("xsession=abc; session2=abc", "session"), None, "names match whole");
        assert_eq!(cookie_value("session=", "session"), None);
        assert_eq!(cookie_value("", "session"), None);
        assert_eq!(cookie_value("garbage", "session"), None);
    }
}
