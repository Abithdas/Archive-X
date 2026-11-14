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
pub const ROUTES: &[&str] = &["photos_channels", "performers_list", "face_groups_fast", "channel_videos", "studio_videos", "tag_videos"];

/// A request that belongs to an enabled native route.
pub struct Match {
    pub name: &'static str,
    pub id: Option<i64>,
}

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

    /// The enabled route this request is for, with the number in its path if it has one.
    pub fn route_for(&self, method: &Method, path: &str) -> Option<Match> {
        if method != Method::GET {
            return None;
        }
        let (name, id) = match path {
            "/api/photos/channels" => ("photos_channels", None),
            "/api/performers" => ("performers_list", None),
            "/api/photos/faces/groups" => ("face_groups_fast", None),
            _ => {
                let rest = path.strip_prefix("/api/")?;
                let (kind, tail) = rest.split_once('/')?;
                let (id, tail) = tail.split_once('/')?;
                let name = match (kind, tail) {
                    ("channels", "videos") => "channel_videos",
                    ("studios", "videos") => "studio_videos",
                    ("tags", "videos") => "tag_videos",
                    _ => return None,
                };
                // FastAPI's `int` accepts more forms (signs, spaces, other digits); only plain integers are handled here, the rest goes to Python.
                let digits = id.strip_prefix('-').unwrap_or(id);
                if digits.is_empty() || digits.len() > 18 || !digits.bytes().all(|b| b.is_ascii_digit()) {
                    return None;
                }
                (name, Some(id.parse::<i64>().ok()?))
            }
        };
        self.enabled.get(name).map(|n| Match { name: n, id })
    }

    /// `None` means "not for me, ask Python": the route is on, but this particular request is one the Rust version does not
    /// handle (a search, an unusual parameter, a case where Python writes to the database), so it is forwarded as if the route were off.
    pub async fn handle(&self, m: &Match, query: Option<&str>, headers: &HeaderMap, peer: SocketAddr) -> Option<Response<Body>> {
        let route = m.name;
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
            "channel_videos" | "studio_videos" | "tag_videos" => {
                let resp = self.video_list(route, m.id?, query, headers).await?;
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

// ---- the video lists of a channel, a studio and a tag --------------------------------------------------------------------------------

/// What Python's `x or y` thinks is true.
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `[x for x in (value or "").split(",") if x]`
fn csv_list(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_str).unwrap_or("").split(',').filter(|x| !x.is_empty()).map(str::to_string).collect()
}

fn strings(v: Vec<String>) -> Value {
    Value::Array(v.into_iter().map(Value::String).collect())
}

/// `str(value)` for a number or text, as it appears inside an f-string.
fn py_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// performers = unique(performers + local performers), keeping order.
fn merged_performers(d: &Map<String, Value>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for name in csv_list(d.get("performers")).into_iter().chain(csv_list(d.get("local_performers"))) {
        if !out.contains(&name) {
            out.push(name);
        }
    }
    out
}

/// `d.get(a) or d.get(b) or f"video {id}"` (or with the first two swapped) and `thumbnail_message_id or source_message_id`.
fn display_name_and_thumbnail(d: &mut Map<String, Value>) {
    let id = d.get("id").cloned();
    let name = if truthy(d.get("title")) { d["title"].clone() } else if truthy(d.get("filename")) { d["filename"].clone() } else { Value::String(format!("video {}", py_str(id.as_ref()))) };
    d.insert("name".into(), name);
    let thumb = if truthy(d.get("thumbnail_message_id")) { d["thumbnail_message_id"].clone() } else { d.get("source_message_id").cloned().unwrap_or(Value::Null) };
    d.insert("thumbnail_id".into(), thumb);
}

impl Native {
    /// GET /api/channels/{id}/videos, /api/studios/{id}/videos and /api/tags/{id}/videos: the three video lists of the detail pages.
    /// The studio list with a `performer_id` filter and the performer list (which writes the Unknown Performer profile) stay in Python.
    async fn video_list(&self, route: &'static str, id: i64, query: Option<&str>, request: &HeaderMap) -> Option<Response<Body>> {
        let params = query_params(query)?;
        if route == "studio_videos" && params.contains_key("performer_id") {
            return None;
        }
        let read = self.db.read(move |c| video_list_rows(c, route, id)).await;
        let found = match read {
            Ok(found) => found,
            Err(e) => {
                tracing::error!("{route}: {e}");
                return Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The video list could not be read"));
            }
        };
        match found {
            Some(payload) => Some(json_response(request, serde_json::to_vec(&payload).unwrap_or_default())),
            None => Some(json_error(
                StatusCode::NOT_FOUND,
                match route {
                    "channel_videos" => "Channel not found",
                    "studio_videos" => "Site not found",
                    _ => "Tag not found",
                },
            )),
        }
    }
}

/// The four name lists of a video, each from its own sub-query. The original queries join performers, local performers, studios and tags
/// to every video in one go, which multiplies the rows (a video with 113 performers, 26 studios and 3 tags produced 8,000 rows before the
/// grouping: 125 ms for 18 videos against 2.4 ms for these). The lists come out the same, in the same order, on every database tried
/// (SQLite does not promise an order for GROUP_CONCAT(DISTINCT ...); the parity harness checks it on the big library).
fn name_lists(performers: &str, studios: &str, tags: &str) -> String {
    format!(
        "(SELECT GROUP_CONCAT(DISTINCT p.name) FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=m.id) AS {performers}, \
         (SELECT GROUP_CONCAT(DISTINCT lp.name) FROM local_video_performers lvp JOIN performers lp ON lp.id=lvp.performer_id WHERE lvp.video_id=m.id) AS local_performers, \
         (SELECT GROUP_CONCAT(DISTINCT s2.name) FROM video_studios vs2 JOIN studios s2 ON s2.id=vs2.studio_id WHERE vs2.video_id=m.id) AS {studios}, \
         (SELECT GROUP_CONCAT(DISTINCT t2.name) FROM video_tags vt2 JOIN tags t2 ON t2.id=vt2.tag_id WHERE vt2.video_id=m.id) AS {tags}"
    )
}

const ENTITY_GLOB: &str = "AND COALESCE(m.status,'')<>'SOURCE_DELETED' AND (CAST(m.source_channel AS TEXT) GLOB '-[0-9]*' OR (CAST(m.source_channel AS TEXT)<>'' AND CAST(m.source_channel AS TEXT) NOT GLOB '*[^0-9]*')) ";

/// `None` when the channel, studio or tag does not exist.
fn video_list_rows(c: &Connection, route: &str, id: i64) -> ax_db::rusqlite::Result<Option<Value>> {
    use ax_db::rusqlite::params;
    match route {
        "tag_videos" => {
            let Some(tag) = one_row(c, "SELECT * FROM tags WHERE id=?", params![id])? else { return Ok(None) };
            let sql = format!(
                "SELECT m.*, {} FROM archive_x_library_media m WHERE EXISTS(SELECT 1 FROM video_tags vt WHERE vt.video_id=m.id AND vt.tag_id=?) GROUP BY m.id ORDER BY m.upload_date DESC",
                name_lists("performers", "studios", "tags")
            );
            let rows = rows_with(c, &sql, params![id])?;
            let videos: Vec<Value> = rows
                .into_iter()
                .map(|mut d| {
                    let performers = merged_performers(&d);
                    d.insert("performers".into(), strings(performers));
                    let studios = csv_list(d.get("studios"));
                    d.insert("studios".into(), strings(studios.clone()));
                    let tags = csv_list(d.get("tags"));
                    d.insert("tags".into(), strings(tags));
                    d.insert("studio".into(), Value::String(studios.first().cloned().unwrap_or_default()));
                    let sm = d.get("source_message_id").cloned().unwrap_or(Value::Null);
                    d.insert("id".into(), sm);
                    let sc = d.get("source_channel").cloned().unwrap_or(Value::Null);
                    d.insert("_cid".into(), sc);
                    display_name_and_thumbnail(&mut d);
                    Value::Object(d)
                })
                .collect();
            let mut out = Map::new();
            out.insert("tag".into(), Value::Object(tag));
            out.insert("videos".into(), Value::Array(videos));
            Ok(Some(Value::Object(out)))
        }
        "studio_videos" => {
            let Some(mut studio) = one_row(c, "SELECT s.*, (SELECT MAX(sic.updated_at) FROM studio_image_cache sic WHERE sic.studio_id=s.id) AS image_cache_updated_at FROM studios s WHERE s.id=?", params![id])? else { return Ok(None) };
            let favorite: bool = c.query_row("SELECT EXISTS(SELECT 1 FROM entity_favorites WHERE entity_type='studio' AND entity_id=?)", params![id], |r| r.get(0))?;
            studio.insert("favorite".into(), Value::Bool(favorite));
            let sql = format!(
                "SELECT m.*, c.title AS channel_title, {} \
                 FROM archive_x_library_media m LEFT JOIN channels c ON c.telegram_id=m.source_channel \
                 WHERE EXISTS (SELECT 1 FROM video_studios vs WHERE vs.video_id=m.id AND vs.studio_id=?) {ENTITY_GLOB}GROUP BY m.id ORDER BY m.upload_date DESC",
                name_lists("performers", "studios", "tags")
            );
            let rows = rows_with(c, &sql, params![id])?;
            let site_url: Option<String> = {
                let mut st = c.prepare("SELECT site_url FROM media WHERE site_url IS NOT NULL AND TRIM(site_url)<>'' AND id IN (SELECT video_id FROM video_studios WHERE studio_id=?) ORDER BY updated_at DESC LIMIT 1")?;
                let mut it = st.query(params![id])?;
                match it.next()? {
                    Some(r) => Some(r.get::<_, Option<String>>(0)?.unwrap_or_default()),
                    None => None,
                }
            };
            let videos: Vec<Value> = rows
                .into_iter()
                .map(|mut d| {
                    let performers = merged_performers(&d);
                    d.insert("performers".into(), strings(performers));
                    let local = csv_list(d.get("local_performers"));
                    d.insert("local_performers".into(), strings(local));
                    let studios = csv_list(d.get("studios"));
                    d.insert("studios".into(), strings(studios.clone()));
                    d.insert("studio".into(), Value::String(studios.first().cloned().unwrap_or_default()));
                    let tags = csv_list(d.get("tags"));
                    d.insert("tags".into(), strings(tags));
                    let row_id = d.get("id").cloned().unwrap_or(Value::Null);
                    d.insert("media_id".into(), row_id);
                    let sc = d.get("source_channel").cloned().unwrap_or(Value::Null);
                    d.insert("_cid".into(), sc);
                    let sm = d.get("source_message_id").cloned().unwrap_or(Value::Null);
                    d.insert("id".into(), sm);
                    display_name_and_thumbnail(&mut d);
                    Value::Object(d)
                })
                .collect();
            let mut out = Map::new();
            out.insert("site".into(), Value::Object(studio));
            out.insert("videos".into(), Value::Array(videos));
            out.insert("site_url".into(), Value::String(site_url.unwrap_or_default().trim().to_string()));
            Ok(Some(Value::Object(out)))
        }
        _ => {
            // channel_videos
            let key = id.to_string();
            let channel = one_row(c, "SELECT * FROM channels WHERE telegram_id=?", params![key])?;
            let title = match channel {
                Some(ch) => ch.get("title").cloned().unwrap_or(Value::Null),
                None => {
                    let known: bool = c.query_row("SELECT EXISTS(SELECT 1 FROM archive_x_published_media WHERE source_channel=? LIMIT 1)", params![key], |r| r.get(0))?;
                    if !known {
                        return Ok(None);
                    }
                    Value::from("Unknown channel")
                }
            };
            let sql = format!(
                "SELECT m.*, 1 AS library_ready, 'Ready' AS library_state, 'COMPLETE' AS library_catalogue_state, 'COMPLETE' AS library_metadata_state, 'COMPLETE' AS library_entity_state, 'READY' AS library_snapshot_state, {} FROM media m WHERE m.source_channel=? GROUP BY m.id ORDER BY m.upload_date DESC",
                name_lists("performers", "studios", "tags")
            );
            let rows = rows_with(c, &sql, params![key])?;
            let videos: Vec<Value> = rows
                .into_iter()
                .map(|mut d| {
                    let performers = merged_performers(&d);
                    d.insert("performers".into(), strings(performers));
                    let local = csv_list(d.get("local_performers"));
                    d.insert("local_performers".into(), strings(local));
                    let studios = csv_list(d.get("studios"));
                    d.insert("studios".into(), strings(studios.clone()));
                    d.insert("studio".into(), Value::String(studios.first().cloned().unwrap_or_default()));
                    let tags = csv_list(d.get("tags"));
                    d.insert("tags".into(), strings(tags));
                    d.insert("channel_id".into(), Value::String(key.clone()));
                    d.insert("_cid".into(), Value::String(key.clone()));
                    let sm = d.get("source_message_id").cloned().unwrap_or(Value::Null);
                    d.insert("id".into(), sm);
                    // channel list: the name prefers the title, and the telegram filename is the file name or empty
                    let name = if truthy(d.get("title")) { d["title"].clone() } else if truthy(d.get("filename")) { d["filename"].clone() } else { Value::String(format!("video {}", py_str(d.get("id")))) };
                    d.insert("name".into(), name);
                    let fname = if truthy(d.get("filename")) { d["filename"].clone() } else { Value::String(String::new()) };
                    d.insert("telegram_filename".into(), fname);
                    let thumb = if truthy(d.get("thumbnail_message_id")) { d["thumbnail_message_id"].clone() } else { d.get("source_message_id").cloned().unwrap_or(Value::Null) };
                    d.insert("thumbnail_id".into(), thumb);
                    Value::Object(d)
                })
                .collect();
            let ready = videos.iter().filter(|v| truthy(v.get("library_ready"))).count();
            let mut out = Map::new();
            out.insert("channel_id".into(), Value::from(id));
            out.insert("title".into(), title);
            out.insert("count".into(), Value::from(videos.len()));
            out.insert("ready_count".into(), Value::from(ready));
            out.insert("videos".into(), Value::Array(videos));
            out.insert("catalogued_only".into(), Value::Bool(false));
            out.insert("published_only".into(), Value::Bool(true));
            Ok(Some(Value::Object(out)))
        }
    }
}

fn rows_with(conn: &Connection, sql: &str, params: impl ax_db::rusqlite::Params) -> ax_db::rusqlite::Result<Vec<Map<String, Value>>> {
    let mut stmt = conn.prepare(sql)?;
    let names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
    let rows = stmt.query_map(params, |row| {
        let mut m = Map::new();
        for (i, name) in names.iter().enumerate() {
            m.insert(name.clone(), json_value(row.get_ref(i)?));
        }
        Ok(m)
    })?;
    rows.collect()
}

fn one_row(conn: &Connection, sql: &str, params: impl ax_db::rusqlite::Params) -> ax_db::rusqlite::Result<Option<Map<String, Value>>> {
    Ok(rows_with(conn, sql, params)?.into_iter().next())
}
