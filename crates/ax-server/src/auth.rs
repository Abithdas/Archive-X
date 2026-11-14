//! Authentication for the routes the front door answers itself: the same session cookie the Python backend issues,
//! checked against the same database (see ax-db's `sessions`). A request without a live session gets exactly the answer
//! Python gives, `401 {"detail":"Authentication required"}`.

use std::time::{SystemTime, UNIX_EPOCH};

use ax_db::sessions::Session;
use ax_db::Db;
use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::header::{COOKIE, CONTENT_TYPE};
use hyper::{HeaderMap, Response, StatusCode};

use crate::proxy::Body;

#[derive(Clone)]
pub struct Auth {
    db: Db,
}

pub fn json_error(status: StatusCode, detail: &str) -> Response<Body> {
    let body = format!("{{\"detail\":{}}}", crate::proxy::json_escape(detail));
    Response::builder().status(status).header(CONTENT_TYPE, "application/json").body(Full::new(Bytes::from(body)).map_err(|n| match n {}).boxed()).unwrap()
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

impl Auth {
    pub fn new(db: Db) -> Auth {
        Auth { db }
    }

    /// The signed-in session behind these request headers, or the 401 to send back.
    // The Err value is the finished 401/503 answer, sent straight back to the client, so boxing it would only add noise.
    #[allow(clippy::result_large_err)]
    pub async fn require(&self, headers: &HeaderMap) -> Result<Session, Response<Body>> {
        // HTTP/2 may carry the cookies in several headers; they are one list.
        let cookie = headers.get_all(COOKIE).iter().filter_map(|v| v.to_str().ok()).collect::<Vec<_>>().join("; ");
        match self.db.session_for_cookie(&cookie, now()).await {
            Ok(Some(session)) => Ok(session),
            Ok(None) => Err(json_error(StatusCode::UNAUTHORIZED, "Authentication required")),
            // A database that cannot be read must not look like a logout, and must not let anyone in.
            Err(e) => {
                tracing::error!("session lookup failed: {e}");
                Err(json_error(StatusCode::SERVICE_UNAVAILABLE, "The session database could not be read"))
            }
        }
    }
}
