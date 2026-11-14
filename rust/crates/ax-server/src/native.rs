//! Routes answered by the front door itself instead of the Python backend.
//!
//! Each route is switched on by name (`--native-routes photos_channels`, or `all`); with the list empty, which is the
//! default, nothing here is reachable and every request goes to Python. Every answer carries `X-Served-By`, so it is always
//! visible which side produced it, and `tools/parity/compare.py` can prove an enabled route gives Python's answer.

use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use ax_db::rusqlite::types::ValueRef;
use ax_db::rusqlite::Connection;
use ax_db::sessions::{cookie_value, SESSION_COOKIE};
use ax_db::Db;
use hyper::{HeaderMap, Method, Response, StatusCode};
use serde_json::{Map, Value};

use crate::auth::{json_error, Auth};
use crate::optimize::json_response;
use crate::proxy::{Body, Proxy};

/// Every route that can be answered here, by name.
pub const ROUTES: &[&str] = &["photos_channels"];

pub struct Native {
    enabled: HashSet<&'static str>,
    auth: Auth,
    db: Db,
    proxy: Arc<Proxy>,
    touched: Mutex<HashMap<String, Instant>>,
    touch_interval: Duration,
}

impl Native {
    /// `None` when no route is switched on (the database is then not even opened).
    pub fn new(routes: &[String], db_path: Option<&std::path::Path>, proxy: Arc<Proxy>, touch_interval: Duration) -> Result<Option<Native>, String> {
        let mut enabled = HashSet::new();
        for name in routes {
            if name == "none" {
                continue;
            } else if name == "all" {
                enabled.extend(ROUTES.iter().copied());
            } else if let Some(r) = ROUTES.iter().find(|r| **r == name) {
                enabled.insert(*r);
            } else {
                return Err(format!("unknown native route {name:?}; available: {}", ROUTES.join(", ")));
            }
        }
        if enabled.is_empty() {
            return Ok(None);
        }
        let path = db_path.ok_or("native routes need --db")?;
        let db = Db::open_read_only(path).map_err(|e| e.to_string())?;
        Ok(Some(Native { enabled, auth: Auth::new(db.clone()), db, proxy, touched: Mutex::new(HashMap::new()), touch_interval }))
    }

    pub fn names(&self) -> Vec<&'static str> {
        let mut v: Vec<_> = self.enabled.iter().copied().collect();
        v.sort();
        v
    }

    /// The enabled route this request is for, if any.
    pub fn route_for(&self, method: &Method, path: &str) -> Option<&'static str> {
        let name = match (method, path) {
            (&Method::GET, "/api/photos/channels") => "photos_channels",
            _ => return None,
        };
        self.enabled.get(name).copied()
    }

    pub async fn handle(&self, route: &str, headers: &HeaderMap, peer: SocketAddr) -> Response<Body> {
        let session = match self.auth.require(headers).await {
            Ok(s) => s,
            Err(resp) => return resp,
        };
        self.keep_session_alive(headers, peer, &session.username);
        match route {
            "photos_channels" => self.photos_channels(headers).await,
            _ => json_error(StatusCode::NOT_FOUND, "Not Found"),
        }
    }

    /// Python slides a session's expiry on every request it answers. Requests answered here do not reach it, so now and then
    /// one cheap authenticated call tells it the session is still in use (it writes the new expiry to the database itself).
    fn keep_session_alive(&self, headers: &HeaderMap, peer: SocketAddr, _user: &str) {
        let cookie = headers.get_all(hyper::header::COOKIE).iter().filter_map(|v| v.to_str().ok()).collect::<Vec<_>>().join("; ");
        let Some(token) = cookie_value(&cookie, SESSION_COOKIE).map(str::to_string) else { return };
        {
            let mut seen = self.touched.lock().unwrap();
            let now = Instant::now();
            if seen.get(&token).is_some_and(|t| now.duration_since(*t) < self.touch_interval) {
                return;
            }
            if seen.len() > 2048 {
                seen.retain(|_, t| now.duration_since(*t) < self.touch_interval);
            }
            seen.insert(token, now);
        }
        let proxy = self.proxy.clone();
        tokio::spawn(async move {
            if let Err(e) = proxy.touch(&cookie, peer).await {
                tracing::debug!("could not tell the backend a session is active: {e}");
            }
        });
    }

    /// GET /api/photos/channels: the channel cards of the Photos library (routes_library.photo_library_channels).
    async fn photos_channels(&self, request: &HeaderMap) -> Response<Body> {
        match self.db.read(photos_channels_rows).await {
            Ok(rows) => json_response(request, serde_json::to_vec(&rows).unwrap_or_default()),
            Err(e) => {
                tracing::error!("photos/channels: {e}");
                json_error(StatusCode::SERVICE_UNAVAILABLE, "The photo library could not be read")
            }
        }
    }
}

/// A SQLite value as the Python `sqlite3` module would hand it to `json.dumps`.
fn json_value(v: ValueRef<'_>) -> Value {
    match v {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(i) => Value::from(i),
        ValueRef::Real(f) => serde_json::Number::from_f64(f).map(Value::Number).unwrap_or(Value::Null),
        ValueRef::Text(t) => Value::String(String::from_utf8_lossy(t).into_owned()),
        ValueRef::Blob(_) => Value::Null,
    }
}

fn rows_as_maps(conn: &Connection, sql: &str) -> ax_db::rusqlite::Result<Vec<Map<String, Value>>> {
    let mut stmt = conn.prepare(sql)?;
    let names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
    let rows = stmt.query_map([], |row| {
        let mut m = Map::new();
        for (i, name) in names.iter().enumerate() {
            m.insert(name.clone(), json_value(row.get_ref(i)?));
        }
        Ok(m)
    })?;
    rows.collect()
}

/// The same two queries as the Python route, and the same rule for photos whose channel is not saved.
fn photos_channels_rows(conn: &Connection) -> ax_db::rusqlite::Result<Vec<Map<String, Value>>> {
    let mut out = rows_as_maps(
        conn,
        "SELECT c.telegram_id,c.title,c.username,c.image,c.updated_at,
                COUNT(p.id) AS photo_count,
                COALESCE(s.status,'NEVER') AS photo_scan_status,
                s.last_scan_at AS photo_last_scan_at,
                COALESCE(s.messages_scanned,0) AS photo_messages_scanned,
                COALESCE(s.photos_found,0) AS photo_photos_found,
                COALESCE(s.last_error,'') AS photo_scan_error
         FROM channels c
         LEFT JOIN photo_library_items p ON p.channel_id=c.telegram_id
         LEFT JOIN photo_library_scan_state s ON s.channel_id=c.telegram_id
         GROUP BY c.telegram_id,c.title,c.username,c.image,c.updated_at,s.status,s.last_scan_at,
                  s.messages_scanned,s.photos_found,s.last_error
         ORDER BY c.title COLLATE NOCASE",
    )?;
    let orphans = rows_as_maps(
        conn,
        "SELECT p.channel_id AS telegram_id,COUNT(*) AS photo_count
         FROM photo_library_items p
         WHERE NOT EXISTS(SELECT 1 FROM channels c WHERE c.telegram_id=p.channel_id)
         GROUP BY p.channel_id",
    )?;
    for mut item in orphans {
        let count = item.get("photo_count").cloned().unwrap_or(Value::from(0));
        for (k, v) in [("title", Value::from("Unknown channel")), ("username", Value::from("")), ("image", Value::from("")), ("updated_at", Value::from("")),
                       ("photo_scan_status", Value::from("UNKNOWN")), ("photo_last_scan_at", Value::Null), ("photo_messages_scanned", Value::from(0)),
                       ("photo_photos_found", count), ("photo_scan_error", Value::from(""))] {
            item.insert(k.to_string(), v);
        }
        out.push(item);
    }
    Ok(out)
}
