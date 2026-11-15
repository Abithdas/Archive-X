//! Face learning routes: what a person has confirmed and removed, turned into "these faces look out of place" and "these unnamed faces look
//! like this person" (the maths is in ax-faces). Read-only: the database is opened read-only and every change a person makes still goes
//! through the Python backend's own routes.
//!
//! Who is signed in, and where the database is, are asked of the backend (see names.rs for the same arrangement).

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use ax_db::rusqlite::Connection;
use ax_db::Db;
use ax_faces::{decode, Detection, Learner};
use hyper::{HeaderMap, Method, Response, StatusCode};
use serde_json::{json, Value};

use crate::auth::json_error;
use crate::optimize::json_response;
use crate::proxy::{Body, Proxy};

/// The route name that switches this on (`--native-routes face_learning`).
pub const ROUTES: &[&str] = &["face_learning"];

const STATUS: &str = "/api/photos/faces/learning/status";
const SUGGESTIONS: &str = "/api/photos/faces/learning/suggestions";
const OUTLIERS: &str = "/api/photos/faces/learning/outliers";
const SUGGESTION_LIMIT: usize = 300;
const OUTLIER_LIMIT: usize = 600;
const PEOPLE_LIMIT: usize = 50;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Route {
    Status,
    Suggestions,
    Outliers,
}

pub struct Learning {
    proxy: Arc<Proxy>,
    db: Mutex<Option<Db>>,
    cache: Mutex<Option<(String, Arc<Learner>)>>,
    auth: std::sync::OnceLock<crate::auth::Auth>,
}

impl Learning {
    pub fn new(routes: &[String], proxy: Arc<Proxy>) -> Option<Arc<Learning>> {
        if !routes.iter().any(|r| r == "face_learning" || r == "all") {
            return None;
        }
        Some(Arc::new(Learning { proxy, db: Mutex::new(None), cache: Mutex::new(None), auth: std::sync::OnceLock::new() }))
    }

    pub fn route_for(&self, method: &Method, path: &str) -> Option<Route> {
        if method != Method::GET {
            return None;
        }
        match path {
            STATUS => Some(Route::Status),
            SUGGESTIONS => Some(Route::Suggestions),
            OUTLIERS => Some(Route::Outliers),
            _ => None,
        }
    }

    async fn db(&self) -> Result<Db, String> {
        if let Some(d) = self.db.lock().unwrap().clone() {
            return Ok(d);
        }
        let v = self.proxy.internal_json("/internal/provider-keys").await?;
        let path = v.get("db_path").and_then(Value::as_str).filter(|p| !p.is_empty()).ok_or("the backend did not say where its database is")?.to_string();
        let opened = Db::open_read_only(path).map_err(|e| e.to_string())?;
        Ok(self.db.lock().unwrap().get_or_insert(opened).clone())
    }

    /// The learner for the database as it is now; rebuilt only when faces, groups or "not this person" notes have changed.
    async fn learner(&self) -> Result<Arc<Learner>, String> {
        let db = self.db().await?;
        let key = db.read(fingerprint).await.map_err(|e| e.to_string())?;
        if let Some((k, l)) = self.cache.lock().unwrap().as_ref() {
            if *k == key {
                return Ok(l.clone());
            }
        }
        let learner = Arc::new(db.read(load).await.map_err(|e| e.to_string())?);
        *self.cache.lock().unwrap() = Some((key, learner.clone()));
        Ok(learner)
    }

    // The Err value is the finished 401/503 answer, sent straight back to the client, so boxing it would only add noise.
    #[allow(clippy::result_large_err)]
    async fn allowed(&self, headers: &HeaderMap, peer: SocketAddr) -> Result<(), Response<Body>> {
        // With the database known, the sign-in check is made here from the sessions table (same rules as the backend's); without it, the backend is asked.
        if let Some(auth) = self.auth.get() {
            return auth.require(headers).await.map(|_| ());
        }
        let cookie = headers.get_all(hyper::header::COOKIE).iter().filter_map(|v| v.to_str().ok()).collect::<Vec<_>>().join("; ");
        match self.proxy.signed_in(&cookie, peer).await {
            Ok(true) => Ok(()),
            Ok(false) => Err(json_error(StatusCode::UNAUTHORIZED, "Authentication required")),
            Err(e) => {
                tracing::warn!("face learning: could not ask the backend who is signed in: {e}");
                Err(json_error(StatusCode::SERVICE_UNAVAILABLE, "The backend could not be reached"))
            }
        }
    }

    /// Use this session check instead of asking the backend who is signed in.
    pub fn use_auth(&self, auth: crate::auth::Auth) {
        let _ = self.auth.set(auth);
    }

    /// `None` means "not for me" (an unusual request): it goes on to Python, which answers 404 for these paths.
    pub async fn handle(&self, route: Route, query: Option<&str>, headers: &HeaderMap, peer: SocketAddr) -> Option<Response<Body>> {
        if let Err(resp) = self.allowed(headers, peer).await {
            return Some(resp);
        }
        let unavailable = |e: String| {
            tracing::warn!("face learning: {e}");
            Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "Face learning is not available right now"))
        };
        let learner = match self.learner().await {
            Ok(l) => l,
            Err(e) => return unavailable(e),
        };
        let body = match route {
            Route::Status => {
                let counts = learner.counts();
                let names = match self.db().await {
                    Ok(db) => db.read(performer_names).await.unwrap_or_default(),
                    Err(_) => Default::default(),
                };
                let (confirmed, removed) = counts.iter().fold((0, 0), |a, c| (a.0 + c.confirmed, a.1 + c.removed));
                let people: Vec<Value> = counts
                    .iter()
                    .take(PEOPLE_LIMIT)
                    .map(|c| json!({ "performer_id": c.performer, "name": names.get(&c.performer).cloned().unwrap_or_default(), "confirmed": c.confirmed, "removed": c.removed }))
                    .collect();
                json!({ "ok": true, "faces": learner.len(), "confirmed": confirmed, "removed": removed, "people": people })
            }
            Route::Suggestions => {
                let id = performer_id(query)?;
                let all = learner.suggestions(id);
                let strong = all.iter().filter(|s| s.flag).count();
                let items: Vec<Value> = all.iter().take(SUGGESTION_LIMIT).map(|s| item(s, "strong")).collect();
                json!({ "ok": true, "performer_id": id, "total": all.len(), "strong": strong, "items": items })
            }
            Route::Outliers => {
                let id = performer_id(query)?;
                let o = learner.outliers(id);
                let different = o.items.iter().filter(|s| s.flag).count();
                let items: Vec<Value> = o.items.iter().take(OUTLIER_LIMIT).map(|s| item(s, "different")).collect();
                json!({ "ok": true, "performer_id": id, "total": o.items.len(), "different": different, "threshold": (o.threshold * 10000.0).round() / 10000.0, "items": items })
            }
        };
        let mut resp = json_response(headers, serde_json::to_vec(&body).ok()?);
        resp.headers_mut().insert(hyper::header::CACHE_CONTROL, hyper::header::HeaderValue::from_static("no-store"));
        Some(resp)
    }
}

fn item(s: &ax_faces::Scored, flag_name: &str) -> Value {
    let mut v = json!({
        "detection_id": s.detection_id, "photo_id": s.photo_id, "group_id": s.group_id,
        "crop_url": format!("/api/photos/faces/crops/{}", s.detection_id), "score": (s.score * 10000.0).round() / 10000.0,
    });
    v[flag_name] = Value::Bool(s.flag);
    v
}

fn performer_id(query: Option<&str>) -> Option<i64> {
    let q = query.unwrap_or("");
    q.split('&')
        .find_map(|p| p.strip_prefix("performer_id="))
        .filter(|v| !v.is_empty() && v.len() <= 18 && v.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|v| v.parse().ok())
}

/// Something that changes whenever faces, groups or "not this person" notes do. Cheap: three aggregate queries over indexed columns.
fn fingerprint(c: &Connection) -> ax_db::rusqlite::Result<String> {
    let faces: (i64, i64, String) = c.query_row("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(updated_at),'') FROM photo_face_detections", [], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?))
    })?;
    let groups: (i64, String, i64) = c.query_row("SELECT COUNT(*), COALESCE(MAX(updated_at),''), COALESCE(SUM(performer_id IS NOT NULL),0) FROM photo_face_groups", [], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?))
    })?;
    let negatives: i64 = c.query_row("SELECT COUNT(*) FROM photo_face_negatives", [], |r| r.get(0)).unwrap_or(0); // the table appears with the first removal
    Ok(format!("{faces:?}|{groups:?}|{negatives}"))
}

fn load(c: &Connection) -> ax_db::rusqlite::Result<Learner> {
    let mut stmt = c.prepare("SELECT d.id,d.photo_id,d.group_id,g.performer_id,d.embedding_json FROM photo_face_detections d JOIN photo_face_groups g ON g.id=d.group_id")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?, r.get::<_, Option<i64>>(3)?, r.get::<_, String>(4)?)))?;
    let mut dets = Vec::new();
    for row in rows {
        let (id, photo_id, group_id, performer, json) = row?;
        if let Some(v) = decode(&json) {
            dets.push(Detection { id, photo_id, group_id, performer, v });
        }
    }
    let negatives: Vec<(i64, i64)> = match c.prepare("SELECT detection_id,performer_id FROM photo_face_negatives") {
        Ok(mut s) => s.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.filter_map(Result::ok).collect(),
        Err(_) => Vec::new(), // no removals yet
    };
    Ok(Learner::new(dets, negatives))
}

fn performer_names(c: &Connection) -> ax_db::rusqlite::Result<std::collections::HashMap<i64, String>> {
    let mut stmt = c.prepare("SELECT id,name FROM performers")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?;
    Ok(rows.filter_map(Result::ok).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_performer_number_only_when_it_is_a_plain_integer() {
        assert_eq!(performer_id(Some("performer_id=12")), Some(12));
        assert_eq!(performer_id(Some("x=1&performer_id=7")), Some(7));
        for bad in ["performer_id=", "performer_id=-3", "performer_id=1e3", "performer_id=1 2", "x=1"] {
            assert_eq!(performer_id(Some(bad)), None, "{bad}");
        }
        assert_eq!(performer_id(None), None);
    }
}
