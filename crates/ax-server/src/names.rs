//! The performer name list: routes answered by the front door, and the background refresh.
//!
//! `GET /api/photos/faces/performers/provider-search?q=` answers from the local copy (ax-names) when it knows somebody by that name, and
//! otherwise goes on to Python, which asks the providers live. `GET .../name-list` says how much is stored and when it was refreshed;
//! `POST .../name-list/sync` refreshes now. Who is signed in is the backend's call (`Proxy::signed_in`), and the provider keys come from
//! the backend too (`/internal/provider-keys`), so nothing secret is stored here and the login rules cannot drift apart.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use ax_names::{Fetch, Http, Keys, Outcome, Store};
use hyper::{HeaderMap, Method, Response, StatusCode};
use serde_json::{json, Value};

use crate::auth::json_error;
use crate::optimize::json_response;
use crate::proxy::{Body, Proxy};

/// The route names that switch this on (`--native-routes name_search,name_list`).
pub const ROUTES: &[&str] = &["name_search", "name_list"];

const SEARCH: &str = "/api/photos/faces/performers/provider-search";
const LIST: &str = "/api/photos/faces/performers/name-list";
const SYNC: &str = "/api/photos/faces/performers/name-list/sync";
const KEYS_TTL: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Route {
    Search,
    Status,
    Sync,
}

pub struct Names {
    proxy: Arc<Proxy>,
    fetch: Arc<dyn Fetch>,
    enabled: (bool, bool), // (search, list+sync)
    store: Mutex<Option<Arc<Store>>>,
    opening: tokio::sync::Mutex<()>,
    keys: Mutex<Option<(Instant, Keys)>>,
    running: AtomicBool,
    stop: Arc<AtomicBool>,
    pause: Duration,
}

impl Names {
    /// `None` unless one of the name routes is switched on.
    pub fn new(routes: &[String], proxy: Arc<Proxy>) -> Option<Arc<Names>> {
        Names::with_fetch(routes, proxy, Arc::new(Http::default()), Duration::from_millis(350))
    }

    pub fn with_fetch(routes: &[String], proxy: Arc<Proxy>, fetch: Arc<dyn Fetch>, pause: Duration) -> Option<Arc<Names>> {
        let all = routes.iter().any(|r| r == "all");
        let search = all || routes.iter().any(|r| r == "name_search");
        let list = all || routes.iter().any(|r| r == "name_list");
        if !search && !list {
            return None;
        }
        Some(Arc::new(Names {
            proxy,
            fetch,
            enabled: (search, list),
            store: Mutex::new(None),
            opening: tokio::sync::Mutex::new(()),
            keys: Mutex::new(None),
            running: AtomicBool::new(false),
            stop: Arc::new(AtomicBool::new(false)),
            pause,
        }))
    }

    pub fn route_for(&self, method: &Method, path: &str) -> Option<Route> {
        match (method, path) {
            (&Method::GET, SEARCH) if self.enabled.0 => Some(Route::Search),
            (&Method::GET, LIST) if self.enabled.1 => Some(Route::Status),
            (&Method::POST, SYNC) if self.enabled.1 => Some(Route::Sync),
            _ => None,
        }
    }

    /// The backend's answer to "what are the keys and where is the data", at most every half minute.
    async fn config(&self) -> Result<(Keys, Option<PathBuf>), String> {
        let cached = self.keys.lock().unwrap().clone();
        if let Some((at, keys)) = cached {
            if at.elapsed() < KEYS_TTL {
                return Ok((keys, None));
            }
        }
        // A few tries: the backend may be busy or a kept connection may have just closed; the front door itself must not give up on one hiccup.
        let mut asked = self.proxy.internal_json("/internal/provider-keys").await;
        for _ in 0..2 {
            if asked.is_ok() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(150)).await;
            asked = self.proxy.internal_json("/internal/provider-keys").await;
        }
        let v = asked?;
        let pick = |k: &str| v.get(k).and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty()).map(str::to_string);
        let keys = Keys { stashdb: pick("stashdb"), tpdb: pick("tpdb") };
        *self.keys.lock().unwrap() = Some((Instant::now(), keys.clone()));
        Ok((keys, pick("data_dir").map(|d| PathBuf::from(d).join("performer_names.db"))))
    }

    async fn keys(&self) -> Keys {
        self.config().await.map(|c| c.0).unwrap_or_default()
    }

    /// The stored names, opened the first time they are needed (the backend says where its data lives).
    async fn store(&self) -> Result<Arc<Store>, String> {
        if let Some(s) = self.store.lock().unwrap().clone() {
            return Ok(s);
        }
        let _one_at_a_time = self.opening.lock().await;
        if let Some(s) = self.store.lock().unwrap().clone() {
            return Ok(s); // another request opened it while this one waited
        }
        *self.keys.lock().unwrap() = None; // the path comes with a fresh answer
        let (_, path) = self.config().await?;
        let path = path.ok_or("the backend did not say where its data is")?;
        let opened = tokio::task::spawn_blocking(move || Store::open(&path).map(Arc::new).map_err(|e| e.to_string())).await.map_err(|e| e.to_string())??;
        let mut slot = self.store.lock().unwrap();
        Ok(slot.get_or_insert(opened).clone())
    }

    // The Err value is the finished 401/503 answer, sent straight back to the client, so boxing it would only add noise.
    #[allow(clippy::result_large_err)]
    async fn allowed(&self, headers: &HeaderMap, peer: SocketAddr) -> Result<(), Response<Body>> {
        let cookie = headers.get_all(hyper::header::COOKIE).iter().filter_map(|v| v.to_str().ok()).collect::<Vec<_>>().join("; ");
        match self.proxy.signed_in(&cookie, peer).await {
            Ok(true) => Ok(()),
            Ok(false) => Err(json_error(StatusCode::UNAUTHORIZED, "Authentication required")),
            Err(e) => {
                tracing::warn!("name list: could not ask the backend who is signed in: {e}");
                Err(json_error(StatusCode::SERVICE_UNAVAILABLE, "The backend could not be reached"))
            }
        }
    }

    /// `None` means "not for me, ask Python" (a name nobody here knows yet: the providers are asked live).
    pub async fn handle(self: &Arc<Self>, route: Route, query: Option<&str>, headers: &HeaderMap, peer: SocketAddr, host: &str) -> Option<Response<Body>> {
        if route == Route::Sync && !crate::guard::allowed("POST", SYNC, headers.get(hyper::header::ORIGIN).and_then(|v| v.to_str().ok()), host) {
            return Some(json_error(StatusCode::FORBIDDEN, "Cross-site request blocked"));
        }
        if let Err(resp) = self.allowed(headers, peer).await {
            return Some(resp);
        }
        match route {
            Route::Search => {
                let q = query_value(query, "q")?;
                if q.trim().chars().count() < 2 {
                    return None;
                }
                let store = self.store().await.ok()?;
                let hits = tokio::task::spawn_blocking(move || store.search(q.trim(), 8)).await.ok()?;
                if hits.is_empty() {
                    return None;
                }
                let items: Vec<Value> = hits.iter().map(|h| h.to_json()).collect();
                Some(json_response(headers, serde_json::to_vec(&json!({ "ok": true, "items": items, "configured": true })).ok()?))
            }
            Route::Status => {
                let keys = self.keys().await;
                let store = match self.store().await {
                    Ok(s) => s,
                    Err(e) => return Some(json_error(StatusCode::SERVICE_UNAVAILABLE, &format!("The name list is not available: {e}"))),
                };
                let status = tokio::task::spawn_blocking(move || store.status(&keys)).await.ok()?;
                Some(json_response(headers, serde_json::to_vec(&status).ok()?))
            }
            Route::Sync => {
                self.keys.lock().unwrap().take(); // a manual refresh uses the keys as they are right now
                let keys = self.keys().await;
                if keys.stashdb.is_none() && keys.tpdb.is_none() {
                    return Some(json_error(StatusCode::BAD_REQUEST, "Add a StashDB or ThePornDB key in Settings first"));
                }
                let started = self.clone().refresh_in_background(true);
                let body = if started { json!({ "ok": true, "started": true }) } else { json!({ "ok": true, "started": false, "detail": "Already refreshing" }) };
                Some(json_response(headers, serde_json::to_vec(&body).ok()?))
            }
        }
    }

    /// Start a refresh unless one is running. Returns whether this call started it.
    pub fn refresh_in_background(self: Arc<Self>, force: bool) -> bool {
        if self.running.swap(true, Ordering::SeqCst) {
            return false;
        }
        tokio::spawn(async move {
            let result = self.refresh(force).await;
            self.running.store(false, Ordering::SeqCst);
            if let Err(e) = result {
                tracing::warn!("performer name list refresh: {e}");
            }
        });
        true
    }

    async fn refresh(&self, force: bool) -> Result<(), String> {
        let store = self.store().await?;
        self.keys.lock().unwrap().take(); // use the keys as they are now
        let keys = self.config().await?.0; // a failed ask is an error to report, not "no keys, nothing to do"
        if keys.stashdb.is_none() && keys.tpdb.is_none() {
            return Ok(());
        }
        let (fetch, stop, pause) = (self.fetch.clone(), self.stop.clone(), self.pause);
        let results = tokio::task::spawn_blocking(move || store.refresh(fetch.as_ref(), &keys, force, &|| stop.load(Ordering::Relaxed), pause))
            .await
            .map_err(|e| e.to_string())?;
        for (provider, outcome) in results {
            match outcome {
                Outcome::Done { written } => tracing::info!("performer names from {provider}: {written} written"),
                Outcome::Failed(e) => tracing::warn!("performer names from {provider} failed: {e}"),
                _ => {}
            }
        }
        Ok(())
    }

    /// Ask a refresh in progress to stop at the end of its page (it picks up where it left off next time).
    pub fn stop(&self) {
        self.stop.store(true, Ordering::Relaxed);
    }

    /// Background task: refresh a little after start, then every few hours, for as long as the front door runs.
    pub fn spawn_loop(self: Arc<Self>, first_delay: Duration, every: Duration) {
        if !self.enabled.1 {
            return;
        }
        tokio::spawn(async move {
            tokio::time::sleep(first_delay).await;
            while !self.stop.load(Ordering::Relaxed) {
                self.clone().refresh_in_background(false);
                tokio::time::sleep(every).await;
            }
        });
    }
}

fn query_value(query: Option<&str>, key: &str) -> Option<String> {
    for pair in query.unwrap_or("").split('&') {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        if k == key {
            return percent_encoding::percent_decode_str(&v.replace('+', " ")).decode_utf8().ok().map(|c| c.into_owned());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_a_query_value_with_plus_and_percent_encoding() {
        assert_eq!(query_value(Some("q=stacy+cr%C3%BCz&x=1"), "q").as_deref(), Some("stacy crüz"));
        assert_eq!(query_value(Some("x=1"), "q"), None);
        assert_eq!(query_value(None, "q"), None);
    }
}
