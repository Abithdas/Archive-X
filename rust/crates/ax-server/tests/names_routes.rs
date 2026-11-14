//! The performer name list through the front door: answered here when the local copy knows the name, handed to Python when it does not,
//! behind the backend's own login, refreshed from the providers in the background, and never reachable by `/internal/`.

use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use ax_names::Fetch;
use ax_server::config::{Config, Limits};
use ax_server::server;
use bytes::Bytes;
use http_body_util::{combinators::BoxBody, BodyExt, Full};
use hyper::body::Incoming;
use hyper::service::service_fn;
use hyper::{Request, Response};
use hyper_util::rt::TokioIo;
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const KEY: &str = "0123456789abcdef0123456789abcdef";
type B = BoxBody<Bytes, Infallible>;

struct Backend {
    data_dir: String,
    keys: Mutex<(String, String)>,
}

fn reply(status: u16, body: &str) -> Response<B> {
    Response::builder().status(status).header("content-type", "application/json").body(Full::new(Bytes::from(body.to_string())).boxed()).unwrap()
}

async fn backend_handler(b: Arc<Backend>, req: Request<Incoming>) -> Result<Response<B>, Infallible> {
    let h = |n: &str| req.headers().get(n).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    Ok(match req.uri().path() {
        "/api/app/ping" => if h("cookie").contains("session=ok") { reply(200, r#"{"ok":true}"#) } else { reply(401, r#"{"detail":"Authentication required"}"#) },
        "/internal/provider-keys" => {
            if h("x-archive-x-internal") != KEY {
                reply(404, r#"{"detail":"Not Found"}"#)
            } else {
                let k = b.keys.lock().unwrap().clone();
                reply(200, &json!({ "stashdb": k.0, "tpdb": k.1, "data_dir": b.data_dir }).to_string())
            }
        }
        _ => reply(200, r#"{"from":"python"}"#),
    })
}

struct FakeProviders;

impl Fetch for FakeProviders {
    fn stashdb(&self, key: &str, _q: &str, v: &Value) -> Result<Value, String> {
        assert_eq!(key, "stash-key");
        let rows = if v["input"]["page"] == json!(1) {
            vec![json!({ "id": "s1", "name": "Stacy Cruz", "aliases": ["Stacey Cruise"], "images": [{ "url": "https://img/1.jpg" }], "deleted": false, "updated": "2026-01-01T00:00:00Z" })]
        } else {
            vec![]
        };
        Ok(json!({ "data": { "queryPerformers": { "count": rows.len(), "performers": rows } } }))
    }
    fn tpdb(&self, _t: &str, _p: &str) -> Result<Value, String> {
        Ok(json!({ "data": [], "meta": { "last_page": 1 } }))
    }
}

struct Door {
    addr: SocketAddr,
    backend: Arc<Backend>,
    _keep: tempfile::TempDir,
}

async fn start(keys: (&str, &str)) -> Door {
    let dir = tempfile::tempdir().unwrap();
    let backend = Arc::new(Backend { data_dir: dir.path().to_string_lossy().into_owned(), keys: Mutex::new((keys.0.into(), keys.1.into())) });
    let bl = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let upstream = bl.local_addr().unwrap();
    let b2 = backend.clone();
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = bl.accept().await else { break };
            let b = b2.clone();
            tokio::spawn(async move {
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(s), service_fn(move |r| backend_handler(b.clone(), r))).await;
            });
        }
    });
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "<html>shell</html>").unwrap();
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let cfg = Arc::new(Config {
        listen: addr, upstream, upstream_plain: true, dist: dist.path().to_path_buf(), cert: None, key: None, proxy_key: KEY.into(), print_ready: false,
        limits: Limits::default(), db: None, native_routes: vec!["name_search".into(), "name_list".into()], session_touch_interval: Duration::from_secs(300),
    });
    tokio::spawn(async move {
        let fetch: Arc<dyn Fetch> = Arc::new(FakeProviders);
        let _ = server::serve_with_fetch(l, cfg, std::future::pending(), Some(fetch)).await;
        drop(dist);
    });
    Door { addr, backend, _keep: dir }
}

struct Reply {
    status: u16,
    headers: HashMap<String, String>,
    body: String,
}

async fn send(addr: SocketAddr, method: &str, path: &str, extra: &str) -> Reply {
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\nContent-Length: 0\r\n{extra}\r\n").as_bytes()).await.unwrap();
    let mut raw = Vec::new();
    s.read_to_end(&mut raw).await.unwrap();
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
    let head = String::from_utf8_lossy(&raw[..split]).to_string();
    let mut lines = head.lines();
    let status = lines.next().unwrap().split(' ').nth(1).unwrap().parse().unwrap();
    let headers: HashMap<String, String> = lines.filter_map(|l| l.split_once(": ")).map(|(k, v)| (k.to_ascii_lowercase(), v.to_string())).collect();
    let mut body = raw[split + 4..].to_vec();
    if headers.get("transfer-encoding").map(|v| v == "chunked").unwrap_or(false) {
        let mut out = Vec::new();
        let mut b = &body[..];
        while let Some(p) = b.windows(2).position(|w| w == b"\r\n") {
            let n = usize::from_str_radix(std::str::from_utf8(&b[..p]).unwrap().trim(), 16).unwrap_or(0);
            if n == 0 {
                break;
            }
            out.extend_from_slice(&b[p + 2..p + 2 + n]);
            b = &b[p + 2 + n + 2..];
        }
        body = out;
    }
    Reply { status, headers, body: String::from_utf8_lossy(&body).into_owned() }
}

const IN: &str = "Cookie: session=ok\r\n";

async fn filled(d: &Door) {
    let r = send(d.addr, "POST", "/api/photos/faces/performers/name-list/sync", IN).await;
    assert_eq!(r.status, 200, "{}", r.body);
    for _ in 0..600 {   // up to 30 s: a busy Windows runner has been seen to need more than 5
        let s = send(d.addr, "GET", "/api/photos/faces/performers/name-list", IN).await;
        let v: Value = serde_json::from_str(&s.body).unwrap();
        if v["total"].as_i64().unwrap_or(0) > 0 && v["providers"]["stashdb"]["status"] == "idle" {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("the name list never filled");
}

#[tokio::test]
async fn refreshes_in_the_background_and_then_answers_searches_itself() {
    let d = start(("stash-key", "")).await;
    filled(&d).await;
    let s = send(d.addr, "GET", "/api/photos/faces/performers/name-list", IN).await;
    let v: Value = serde_json::from_str(&s.body).unwrap();
    assert_eq!(v["providers"]["stashdb"]["count"], json!(1));
    assert_eq!(v["providers"]["stashdb"]["configured"], json!(true));
    assert_eq!(v["providers"]["theporndb"]["configured"], json!(false));

    for q in ["sta", "stacy", "cruz%20stacy", "stacey+cru"] {
        let r = send(d.addr, "GET", &format!("/api/photos/faces/performers/provider-search?q={q}"), IN).await;
        assert_eq!(r.status, 200, "{q}: {}", r.body);
        assert_eq!(r.headers.get("x-served-by").map(String::as_str), Some("rust"), "{q}");
        let v: Value = serde_json::from_str(&r.body).unwrap();
        assert_eq!(v["items"][0]["name"], json!("Stacy Cruz"), "{q}");
        assert_eq!(v["items"][0]["provider_id"], json!("s1"));
        assert_eq!(v["items"][0]["profile"], json!("https://stashdb.org/performers/s1"));
        assert_eq!(v["ok"], json!(true));
    }
}

#[tokio::test]
async fn a_name_nobody_here_knows_goes_to_python_for_a_live_search() {
    let d = start(("stash-key", "")).await;
    filled(&d).await;
    let r = send(d.addr, "GET", "/api/photos/faces/performers/provider-search?q=zzzz", IN).await;
    assert_eq!(r.headers.get("x-served-by").map(String::as_str), Some("python"));
    assert_eq!(r.body, r#"{"from":"python"}"#);
    let short = send(d.addr, "GET", "/api/photos/faces/performers/provider-search?q=s", IN).await;
    assert_eq!(short.headers.get("x-served-by").map(String::as_str), Some("python"), "one letter is Python's to answer");
}

#[tokio::test]
async fn follows_the_backends_login_rules() {
    let d = start(("stash-key", "")).await;
    for (m, p) in [("GET", "/api/photos/faces/performers/provider-search?q=stacy"), ("GET", "/api/photos/faces/performers/name-list"), ("POST", "/api/photos/faces/performers/name-list/sync")] {
        let r = send(d.addr, m, p, "").await;
        assert_eq!(r.status, 401, "{m} {p}");
        assert_eq!(r.body, r#"{"detail":"Authentication required"}"#);
    }
}

#[tokio::test]
async fn refresh_needs_a_provider_key_and_a_same_site_request() {
    let d = start(("", "")).await;
    let r = send(d.addr, "POST", "/api/photos/faces/performers/name-list/sync", IN).await;
    assert_eq!(r.status, 400);
    assert!(r.body.contains("Add a StashDB or ThePornDB key"), "{}", r.body);
    let d2 = start(("stash-key", "")).await;
    let r = send(d2.addr, "POST", "/api/photos/faces/performers/name-list/sync", &format!("{IN}Origin: https://evil.test\r\n")).await;
    assert_eq!(r.status, 403);
    // a key added later is picked up (the keys are asked for again after half a minute, and a manual refresh asks at once)
    *d.backend.keys.lock().unwrap() = ("stash-key".into(), String::new());
    let r = send(d.addr, "POST", "/api/photos/faces/performers/name-list/sync", IN).await;
    assert_eq!(r.status, 200, "{}", r.body);
}

#[tokio::test]
async fn internal_paths_are_not_reachable_by_clients() {
    let d = start(("stash-key", "")).await;
    for extra in ["", IN, "x-archive-x-internal: 0123456789abcdef0123456789abcdef\r\n"] {
        let r = send(d.addr, "GET", "/internal/provider-keys", extra).await;
        assert_eq!(r.status, 404, "{extra:?}");
        assert!(!r.body.contains("stash-key"));
    }
}
