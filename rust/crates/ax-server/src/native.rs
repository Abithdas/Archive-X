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
pub const ROUTES: &[&str] = &["photos_channels", "performers_list", "face_groups_fast"];

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
            (&Method::GET, "/api/performers") => "performers_list",
            (&Method::GET, "/api/photos/faces/groups") => "face_groups_fast",
            _ => return None,
        };
        self.enabled.get(name).copied()
    }

    /// `None` means "not for me, ask Python": the route is on, but this particular request is one the Rust version does not
    /// handle (a search, an unusual parameter, a case where Python writes to the database), so it is forwarded as if the route were off.
    pub async fn handle(&self, route: &str, query: Option<&str>, headers: &HeaderMap, peer: SocketAddr) -> Option<Response<Body>> {
        let session = match self.auth.require(headers).await {
            Ok(s) => s,
            Err(resp) => return Some(resp),
        };
        match route {
            "photos_channels" => {
                self.keep_session_alive(headers, peer, &session.username);
                Some(self.photos_channels(headers).await)
            }
            "performers_list" => {
                let resp = self.performers_list(query, headers).await?;
                self.keep_session_alive(headers, peer, &session.username);
                Some(resp)
            }
            "face_groups_fast" => {
                let resp = self.face_groups_fast(query, headers).await?;
                self.keep_session_alive(headers, peer, &session.username);
                Some(resp)
            }
            _ => Some(json_error(StatusCode::NOT_FOUND, "Not Found")),
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

/// The query string as Starlette's `query_params` sees it, or `None` when a name is repeated (the Rust side then declines the request).
fn query_params(query: Option<&str>) -> Option<HashMap<String, String>> {
    let mut out = HashMap::new();
    for pair in query.unwrap_or("").split('&').filter(|p| !p.is_empty()) {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        let dec = |s: &str| percent_encoding::percent_decode_str(&s.replace('+', " ")).decode_utf8().map(|c| c.into_owned()).ok();
        if out.insert(dec(k)?, dec(v)?).is_some() {
            return None;
        }
    }
    Some(out)
}

const PERFORMER_COLUMNS: &str = "SELECT p.*, COUNT(DISTINCT combined.video_id) video_count, \
    EXISTS(SELECT 1 FROM performer_image_cache pic WHERE pic.performer_id=p.id \
    AND pic.data IS NOT NULL AND length(pic.data)>0) AS has_cached_image FROM performers p \
    LEFT JOIN (SELECT performer_id, video_id FROM video_performers \
    WHERE video_id IN (SELECT id FROM archive_x_library_media) \
    UNION SELECT performer_id, video_id FROM local_video_performers \
    WHERE video_id IN (SELECT id FROM archive_x_library_media)) combined \
    ON combined.performer_id=p.id ";

impl Native {
    /// GET /api/performers (performer_catalogue.list_performers_sync), the plain catalogue: no search text, a limit that is a plain
    /// number. Anything else, an empty catalogue (Python then fills it in), or an empty answer is left to Python.
    async fn performers_list(&self, query: Option<&str>, request: &HeaderMap) -> Option<Response<Body>> {
        let params = query_params(query)?;
        if !params.get("q").map(|q| q.trim().is_empty()).unwrap_or(true) {
            return None; // searching: FTS and LIKE fallbacks stay in Python for now
        }
        let limit = match params.get("limit") {
            None => 5000,
            Some(l) if !l.is_empty() && l.len() <= 6 && l.bytes().all(|b| b.is_ascii_digit()) => l.parse::<i64>().ok()?.clamp(1, 5000),
            Some(l) if l.bytes().all(|b| b.is_ascii_alphabetic()) && !l.is_empty() => 5000, // Python: int() fails, so the default
            Some(_) => return None, // signs, spaces, underscores, other digits: let Python's int() decide
        };
        let brief = params.get("brief").map(|b| b == "1").unwrap_or(false);
        let rows = self
            .db
            .read(move |c| {
                let total: i64 = c.query_row("SELECT COUNT(*) FROM performers", [], |r| r.get(0))?;
                if total == 0 {
                    return Ok(None);
                }
                let sql = format!("{PERFORMER_COLUMNS}GROUP BY p.id HAVING video_count > 0 OR p.name='Unknown Performer' ORDER BY (p.name='Unknown Performer') DESC, video_count DESC,p.name LIMIT {limit}");
                let rows = rows_as_maps(c, &sql)?;
                Ok(if rows.is_empty() { None } else { Some(rows) })
            })
            .await;
        let rows = match rows {
            Ok(Some(rows)) => rows,
            Ok(None) => return None,
            Err(e) => {
                tracing::error!("performers: {e}");
                return Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The performer catalogue could not be read"));
            }
        };
        let out: Vec<Map<String, Value>> = rows
            .into_iter()
            .map(|mut p| {
                let gender = p.get("gender").and_then(Value::as_str).unwrap_or("").trim().to_lowercase();
                let normal = if gender.contains("trans") { "trans".to_string() } else if gender == "male" || gender == "female" { gender } else { String::new() };
                p.insert("gender".to_string(), Value::String(normal));
                if brief {
                    let mut b = Map::new();
                    for key in ["id", "name", "video_count", "image", "gender", "favorite", "has_cached_image"] {
                        b.insert(key.to_string(), p.get(key).cloned().unwrap_or(Value::Null));
                    }
                    b
                } else {
                    p
                }
            })
            .collect();
        Some(json_response(request, serde_json::to_vec(&out).unwrap_or_default()))
    }
}

const FACE_SCOPES: &[(&str, &str)] = &[
    ("named", "EXISTS (SELECT 1 FROM photo_face_detections fd JOIN photo_face_groups fg ON fg.id=fd.group_id WHERE fd.photo_id=p.id AND fg.performer_id IS NOT NULL)"),
    ("unnamed", "(EXISTS (SELECT 1 FROM photo_face_detections fd WHERE fd.photo_id=p.id) AND NOT EXISTS (SELECT 1 FROM photo_face_detections fd JOIN photo_face_groups fg ON fg.id=fd.group_id WHERE fd.photo_id=p.id AND fg.performer_id IS NOT NULL))"),
    ("no_face", "(EXISTS (SELECT 1 FROM photo_face_analysis a WHERE a.photo_id=p.id AND a.status='COMPLETE') AND NOT EXISTS (SELECT 1 FROM photo_face_detections fd WHERE fd.photo_id=p.id))"),
    ("pending", "(NOT EXISTS (SELECT 1 FROM photo_face_analysis a WHERE a.photo_id=p.id AND a.status='COMPLETE'))"),
];

/// Python's `str.casefold()` for the letters that differ from lower-casing in everyday names (the full table is much longer).
fn casefold(s: &str) -> String {
    s.chars().flat_map(|c| c.to_lowercase()).collect::<String>().replace('\u{df}', "ss").replace('\u{3c2}', "\u{3c3}").replace('\u{17f}', "s")
}

fn ints(csv: &Value) -> Vec<Value> {
    csv.as_str().unwrap_or("").split(',').filter(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit())).filter_map(|v| v.parse::<i64>().ok()).map(Value::from).collect()
}

impl Native {
    /// GET /api/photos/faces/groups?suggestions=0 (routes_library._face_groups_payload without the suggestion work): the people of the
    /// Photos page. The full form (guessing who an unnamed group might be) stays in Python.
    async fn face_groups_fast(&self, query: Option<&str>, request: &HeaderMap) -> Option<Response<Body>> {
        let params = query_params(query)?;
        if params.get("suggestions").map(String::as_str) != Some("0") {
            return None;
        }
        let top = match params.get("top").map(String::as_str) {
            None | Some("") => 0,
            Some(t) if t.len() <= 6 && t.bytes().all(|b| b.is_ascii_digit()) => t.parse::<i64>().ok()?.min(500),
            Some(t) if t.bytes().all(|b| b.is_ascii_alphabetic()) => 0, // Python's int() fails: no cut
            Some(_) => return None,
        } as usize;
        let mut channels: Vec<String> = Vec::new();
        for c in params.get("channels").map(String::as_str).unwrap_or("").split(',').map(str::trim).filter(|c| !c.is_empty()) {
            if !channels.iter().any(|x| x == c) {
                channels.push(c.to_string());
            }
        }
        channels.truncate(250);
        let result = self
            .db
            .read(move |c| {
                let marks = vec!["?"; channels.len()].join(",");
                let clause = if channels.is_empty() { String::new() } else { format!(" WHERE pi.channel_id IN ({marks})") };
                let groups_sql = format!(
                    "SELECT CASE WHEN g.performer_id IS NOT NULL THEN -g.performer_id ELSE g.id END AS id,
                            CASE WHEN g.performer_id IS NOT NULL THEN COALESCE(pr.name,MIN(g.label)) ELSE MIN(g.label) END AS label,
                            g.performer_id,COALESCE(pr.name,'') AS performer_name,
                            GROUP_CONCAT(DISTINCT g.id) AS cluster_ids,
                            MIN(d.id) AS representative_detection_id,
                            COUNT(DISTINCT d.photo_id) AS photo_count,COUNT(d.id) AS face_count,
                            GROUP_CONCAT(DISTINCT pi.channel_id) AS channel_ids,
                            COALESCE(MAX(g.updated_at),'') AS updated_at,
                            GROUP_CONCAT(DISTINCT d.id) AS detection_ids
                     FROM photo_face_groups g
                     JOIN photo_face_detections d ON d.group_id=g.id
                     JOIN photo_library_items pi ON pi.id=d.photo_id
                     LEFT JOIN performers pr ON pr.id=g.performer_id
                     {clause}
                     GROUP BY CASE WHEN g.performer_id IS NOT NULL THEN -g.performer_id ELSE g.id END,g.performer_id,pr.name
                     ORDER BY CASE WHEN COALESCE(pr.name,'')<>'' THEN pr.name ELSE MIN(g.label) END COLLATE NOCASE,MIN(g.id)"
                );
                let mut stmt = c.prepare(&groups_sql)?;
                let names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
                let rows = stmt
                    .query_map(ax_db::rusqlite::params_from_iter(channels.iter()), |row| {
                        let mut m = Map::new();
                        for (i, name) in names.iter().enumerate() {
                            m.insert(name.clone(), json_value(row.get_ref(i)?));
                        }
                        Ok(m)
                    })?
                    .collect::<ax_db::rusqlite::Result<Vec<_>>>()?;
                let analysis_sql = format!(
                    "SELECT COUNT(*) AS total,
                            SUM(CASE WHEN a.status='COMPLETE' THEN 1 ELSE 0 END) AS analyzed,
                            SUM(CASE WHEN a.status='FAILED' THEN 1 ELSE 0 END) AS failed
                     FROM photo_library_items p LEFT JOIN photo_face_analysis a ON a.photo_id=p.id{}",
                    if channels.is_empty() { String::new() } else { format!(" WHERE p.channel_id IN ({marks})") }
                );
                let (total, analyzed, failed): (i64, Option<i64>, Option<i64>) =
                    c.query_row(&analysis_sql, ax_db::rusqlite::params_from_iter(channels.iter()), |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
                let mut scopes = Map::new();
                for (name, cond) in FACE_SCOPES {
                    let n: i64 = c.query_row(&format!("SELECT COUNT(*) AS n FROM photo_library_items p WHERE {cond}"), [], |r| r.get(0))?;
                    scopes.insert(name.to_string(), Value::from(n));
                }
                Ok((rows, scopes, total, analyzed.unwrap_or(0), failed.unwrap_or(0)))
            })
            .await;
        let (rows, scopes, total, analyzed, failed) = match result {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("face groups: {e}");
                return Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The face groups could not be read"));
            }
        };
        let mut groups: Vec<Map<String, Value>> = rows
            .into_iter()
            .map(|mut item| {
                let get_i = |item: &Map<String, Value>, k: &str| item.get(k).and_then(Value::as_i64).unwrap_or(0);
                item.insert("photo_count".into(), Value::from(get_i(&item, "photo_count")));
                item.insert("face_count".into(), Value::from(get_i(&item, "face_count")));
                let cluster_ids = ints(item.get("cluster_ids").unwrap_or(&Value::Null));
                item.insert("cluster_ids".into(), Value::Array(cluster_ids));
                let rep = get_i(&item, "representative_detection_id");
                item.insert("representative_url".into(), Value::from(format!("/api/photos/faces/crops/{rep}")));
                let channel_ids: Vec<Value> = item.get("channel_ids").and_then(Value::as_str).unwrap_or("").split(',').filter(|v| !v.is_empty()).map(Value::from).collect();
                item.insert("channel_ids".into(), Value::Array(channel_ids));
                let detections = ints(item.get("detection_ids").unwrap_or(&Value::Null));
                item.insert("detection_ids".into(), Value::Array(detections));
                let performer = item.get("performer_id").and_then(Value::as_i64).unwrap_or(0);
                item.insert("performer_image_url".into(), Value::from(if performer != 0 { format!("/api/performers/{performer}/image") } else { String::new() }));
                for k in ["metadata_suggestions", "confirmed_assignment_suggestions", "profile_image_suggestions"] {
                    item.insert(k.into(), Value::Array(vec![]));
                }
                item
            })
            .collect();
        if top > 0 && groups.len() > top {
            // the busiest people only, named ones first (the Photos page shows a row of them, not all)
            groups.sort_by(|a, b| {
                let key = |g: &Map<String, Value>| (g.get("performer_id").and_then(Value::as_i64).unwrap_or(0) == 0, -g.get("photo_count").and_then(Value::as_i64).unwrap_or(0), casefold(g.get("label").and_then(Value::as_str).unwrap_or("")));
                key(a).cmp(&key(b))
            });
            groups.truncate(top);
        }
        let mut analysis = Map::new();
        analysis.insert("total_photos".into(), Value::from(total));
        analysis.insert("analyzed_photos".into(), Value::from(analyzed));
        analysis.insert("failed_photos".into(), Value::from(failed));
        let mut out = Map::new();
        out.insert("ok".into(), Value::Bool(true));
        out.insert("groups".into(), Value::Array(groups.into_iter().map(Value::Object).collect()));
        out.insert("photo_scopes".into(), Value::Object(scopes));
        out.insert("analysis".into(), Value::Object(analysis));
        out.insert("cosine_threshold".into(), Value::from(0.42));
        out.insert("grouping_mode".into(), Value::from("image_embedding_cluster"));
        Some(json_response(request, serde_json::to_vec(&Value::Object(out)).unwrap_or_default()))
    }
}
