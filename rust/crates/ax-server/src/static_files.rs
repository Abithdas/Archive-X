//! Serves the built web app (frontend/dist). Mirrors telegram_archive/web/routes_frontend.py: hashed files under
//! /assets are cached for a year, the service worker and manifest are always re-checked, the app shell is never
//! cached, and an unknown address without a file extension opens the app (the browser router takes over).

use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::UNIX_EPOCH;

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::header::{HeaderValue, ACCEPT_ENCODING, CACHE_CONTROL, CONTENT_ENCODING, CONTENT_LENGTH, CONTENT_TYPE, ETAG, IF_NONE_MATCH, LAST_MODIFIED, VARY};
use hyper::{Method, Response, StatusCode};
use percent_encoding::percent_decode_str;

use crate::proxy::Body;

/// Paths that belong to the Python backend. Same list as routes_frontend.RESERVED, plus the few non-API GET routes it serves.
const BACKEND_PREFIXES: &[&str] = &["api", "stream", "static", "ws", "desktop", "health", "ca.crt", "app-next", "hls", "test-bridge", "openapi.json", "docs", "redoc"];
const MIN_GZIP: usize = 1024;

/// The first path segment of each page of the web app (the React router's routes). Anything else that is not a real file
/// goes to the Python backend, which decides: it has routes this front door does not know about, such as /stream-cached/....
const SPA_ROUTES: &[&str] = &["photos", "videos", "performers", "studios", "watch", "operations", "transfers", "settings", "ai", "people", "login"];

pub fn is_spa_path(path: &str) -> bool {
    let first = path.trim_start_matches('/').split('/').next().unwrap_or("");
    first.is_empty() || SPA_ROUTES.contains(&first)
}

pub fn is_backend_path(path: &str) -> bool {
    let first = path.trim_start_matches('/').split('/').next().unwrap_or("");
    BACKEND_PREFIXES.contains(&first)
}

/// Gzip results are kept in memory (the whole app is a few hundred KB); keyed by file, modified time and size.
pub struct StaticFiles {
    root: PathBuf,
    gz: Mutex<HashMap<(PathBuf, u64, u64), Bytes>>,
}

fn full(bytes: impl Into<Bytes>) -> Body {
    Full::new(bytes.into()).map_err(|never| match never {}).boxed()
}

fn plain(status: StatusCode, text: &'static str) -> Response<Body> {
    Response::builder().status(status).header(CONTENT_TYPE, "text/plain; charset=utf-8").body(full(text)).unwrap()
}

fn not_found() -> Response<Body> {
    Response::builder().status(StatusCode::NOT_FOUND).header(CONTENT_TYPE, "application/json").body(full(r#"{"detail":"Not Found"}"#)).unwrap()
}

impl StaticFiles {
    pub fn new(root: PathBuf) -> StaticFiles {
        let root = std::fs::canonicalize(&root).unwrap_or(root);
        StaticFiles { root, gz: Mutex::new(HashMap::new()) }
    }

    /// Resolve a request path to a file inside the dist folder, never outside it.
    fn resolve(&self, raw_path: &str) -> Option<PathBuf> {
        if raw_path.contains('\\') || raw_path.contains('\0') {
            return None;
        }
        let decoded = percent_decode_str(raw_path).decode_utf8().ok()?;
        if decoded.contains('\\') || decoded.contains('\0') {
            return None;
        }
        let mut out = self.root.clone();
        for part in decoded.split('/').filter(|p| !p.is_empty()) {
            if part == ".." || part == "." {
                return None;
            }
            out.push(part);
        }
        Some(out)
    }

    /// The file this path names inside the web folder, if there is one (never outside it).
    pub async fn lookup(&self, path: &str) -> Option<PathBuf> {
        let trimmed = path.trim_matches('/');
        if trimmed.is_empty() {
            return None;
        }
        let candidate = self.resolve(path)?;
        match tokio::fs::canonicalize(&candidate).await {
            Ok(real) if real.starts_with(&self.root) && real.is_file() => Some(real),
            _ => None,
        }
    }

    pub async fn serve(&self, method: &Method, path: &str, headers: &hyper::HeaderMap, tls: bool) -> Response<Body> {
        let head = method == Method::HEAD;
        let trimmed = path.trim_matches('/');
        let first = trimmed.split('/').next().unwrap_or("");
        let has_ext = Path::new(trimmed).extension().is_some();
        let file = self.lookup(path).await;
        let resp = match file {
            Some(real) => {
                let cache = if trimmed == "sw.js" || trimmed == "manifest.webmanifest" { "no-cache" } else if first == "assets" { "public, max-age=31536000, immutable" } else { "public, max-age=3600" };
                self.file_response(&real, cache, headers, head).await
            }
            None if has_ext => not_found(),
            None => {
                let index = self.root.join("index.html");
                if !index.is_file() {
                    plain(StatusCode::NOT_FOUND, "frontend not built: run npm run build in frontend/")
                } else {
                    self.file_response(&index, "no-store", headers, head).await
                }
            }
        };
        with_security_headers(resp, tls)
    }

    async fn file_response(&self, path: &Path, cache: &str, req: &hyper::HeaderMap, head: bool) -> Response<Body> {
        let (data, meta) = match (tokio::fs::read(path).await, tokio::fs::metadata(path).await) {
            (Ok(d), Ok(m)) => (d, m),
            _ => return not_found(),
        };
        let mtime = meta.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs()).unwrap_or(0);
        let size = data.len() as u64;
        let etag = format!("\"{:x}-{:x}\"", mtime, size);
        let mut builder = Response::builder()
            .header(ETAG, etag.as_str())
            .header(CACHE_CONTROL, cache)
            .header(LAST_MODIFIED, httpdate::fmt_http_date(UNIX_EPOCH + std::time::Duration::from_secs(mtime)));
        if let Some(inm) = req.get(IF_NONE_MATCH).and_then(|v| v.to_str().ok()) {
            if inm.split(',').any(|t| t.trim() == etag || t.trim() == "*") {
                return builder.status(StatusCode::NOT_MODIFIED).body(full(Bytes::new())).unwrap();
            }
        }
        let mime = mime_guess::from_path(path).first_or_octet_stream();
        let ctype = if mime.type_() == "text" || mime.subtype() == "javascript" || mime.subtype() == "json" { format!("{}; charset=utf-8", mime.essence_str()) } else { mime.essence_str().to_string() };
        builder = builder.header(CONTENT_TYPE, ctype).header(VARY, "Accept-Encoding");
        let compressible = mime.type_() == "text" || matches!(mime.subtype().as_str(), "javascript" | "json" | "svg+xml" | "xml" | "manifest+json" | "wasm");
        let wants_gzip = req.get(ACCEPT_ENCODING).and_then(|v| v.to_str().ok()).map(|v| v.to_ascii_lowercase().contains("gzip")).unwrap_or(false);
        let body = if compressible && wants_gzip && data.len() >= MIN_GZIP {
            builder = builder.header(CONTENT_ENCODING, "gzip");
            self.gzip(path, mtime, size, &data)
        } else {
            Bytes::from(data)
        };
        builder = builder.header(CONTENT_LENGTH, body.len());
        builder.status(StatusCode::OK).body(if head { full(Bytes::new()) } else { full(body) }).unwrap()
    }

    fn gzip(&self, path: &Path, mtime: u64, size: u64, data: &[u8]) -> Bytes {
        let key = (path.to_path_buf(), mtime, size);
        if let Some(hit) = self.gz.lock().unwrap().get(&key) {
            return hit.clone();
        }
        let mut enc = flate2::write::GzEncoder::new(Vec::with_capacity(data.len() / 3), flate2::Compression::new(6));
        enc.write_all(data).ok();
        let out = Bytes::from(enc.finish().unwrap_or_default());
        self.gz.lock().unwrap().insert(key, out.clone());
        out
    }
}

/// The same headers the Python backend adds to every response (core/http_middleware.SecurityHeadersMiddleware).
pub fn with_security_headers(mut resp: Response<Body>, tls: bool) -> Response<Body> {
    const CSP: &str = "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; img-src 'self' data: blob: https:; media-src 'self' blob:; font-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss: https:";
    let h = resp.headers_mut();
    let mut set = |k: &'static str, v: &'static str| {
        if !h.contains_key(k) {
            h.insert(k, HeaderValue::from_static(v));
        }
    };
    set("x-content-type-options", "nosniff");
    set("x-frame-options", "SAMEORIGIN");
    set("referrer-policy", "strict-origin-when-cross-origin");
    set("permissions-policy", "camera=(), microphone=(), geolocation=()");
    set("cross-origin-opener-policy", "same-origin");
    set("cross-origin-resource-policy", "same-origin");
    set("content-security-policy", CSP);
    if tls {
        set("strict-transport-security", "max-age=31536000; includeSubDomains");
    }
    resp
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_apps_own_pages_are_recognised_and_backend_routes_are_not() {
        for p in ["/", "/photos", "/photos/x", "/watch/1/2", "/login", "/performers/5"] {
            assert!(is_spa_path(p), "{p}");
        }
        for p in ["/stream-cached/-1/2", "/stream-compat/-1/2", "/api/status", "/hls/1/2/index.m3u8", "/whatever"] {
            assert!(!is_spa_path(p), "{p}");
        }
    }

    #[test]
    fn a_path_can_never_leave_the_web_folder() {
        let dir = std::env::temp_dir().join(format!("ax_static_{}", std::process::id()));
        std::fs::create_dir_all(dir.join("assets")).unwrap();
        std::fs::write(dir.join("assets/a.js"), "x").unwrap();
        let files = StaticFiles::new(dir.clone());
        for bad in ["/..%2f..%2fetc/passwd", "/%2e%2e/secret", "/assets/../../x", "/a%5c..%5cb", "/%00", "/../x", "/assets/./a.js"] {
            assert!(files.resolve(bad).is_none(), "{bad} was accepted");
        }
        assert!(files.resolve("/assets/a.js").unwrap().starts_with(&files.root));
        let _ = std::fs::remove_dir_all(dir);
    }
}
