//! Face learning through the front door: outliers, suggestions and status from a real SQLite file, behind the backend's login.

use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use ax_db::rusqlite::Connection;
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

fn reply(status: u16, body: &str) -> Response<B> {
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(Full::new(Bytes::from(body.to_string())).boxed())
        .unwrap()
}

async fn backend_handler(db_path: String, req: Request<Incoming>) -> Result<Response<B>, Infallible> {
    let h = |n: &str| req.headers().get(n).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    Ok(match req.uri().path() {
        "/api/app/ping" => {
            if h("cookie").contains("session=ok") {
                reply(200, r#"{"ok":true}"#)
            } else {
                reply(401, r#"{"detail":"Authentication required"}"#)
            }
        }
        "/internal/provider-keys" if h("x-archive-x-internal") == KEY => reply(200, &json!({ "stashdb": "", "tpdb": "", "data_dir": "", "db_path": db_path }).to_string()),
        _ => reply(200, r#"{"from":"python"}"#),
    })
}

/// A unit vector in 128 dimensions: mostly along axis `axis`, with a little deterministic noise from `seed`.
/// Mostly along `axis`, pulled towards `other` by `pull` (a lookalike of the first person who is really someone else).
fn face_mix(axis: usize, other: usize, pull: f32, seed: u32) -> String {
    let mut v = vec![0.0f32; 128];
    v[axis] = 1.0;
    v[other] += pull;
    let mut s = seed.wrapping_mul(2654435761).wrapping_add(12345);
    for x in v.iter_mut() {
        s = s.wrapping_mul(1664525).wrapping_add(1013904223);
        *x += 0.04 * ((s >> 8) as f32 / (1u32 << 24) as f32 - 0.5);
    }
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    json!(v.iter().map(|x| x / n).collect::<Vec<_>>()).to_string()
}

struct Fixture {
    dir: tempfile::TempDir,
    wrong: Vec<i64>,
    ava_unnamed: Vec<i64>,
    removed: i64,
}

fn database() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    c.execute_batch(
        "CREATE TABLE performers(id INTEGER PRIMARY KEY, name TEXT);
         CREATE TABLE photo_face_groups(id INTEGER PRIMARY KEY, label TEXT, performer_id INTEGER, created_at TEXT, updated_at TEXT);
         CREATE TABLE photo_face_detections(id INTEGER PRIMARY KEY, photo_id INTEGER, face_index INTEGER, group_id INTEGER, embedding_json TEXT, updated_at TEXT);
         CREATE TABLE photo_face_negatives(detection_id INTEGER, performer_id INTEGER, created_at TEXT, PRIMARY KEY(detection_id, performer_id));
         INSERT INTO performers VALUES (1,'Ava Stone'),(2,'Mia Rivera');
         INSERT INTO photo_face_groups VALUES (1,'g1',1,'t','t'),(2,'g2',2,'t','t'),(3,'g3',NULL,'t','t'),(4,'g4',NULL,'t','t');",
    )
    .unwrap();
    let insert = |next: &mut i64, group: i64, axis: usize, other: usize, pull: f32| {
        *next += 1;
        c.execute("INSERT INTO photo_face_detections VALUES (?1,?2,0,?3,?4,'t')", (*next, *next * 10, group, face_mix(axis, other, pull, *next as u32)))
            .unwrap();
        *next
    };
    let next = std::cell::Cell::new(0i64);
    let add = |group: i64, axis: usize| {
        let mut n = next.get();
        let id = insert(&mut n, group, axis, axis, 0.0);
        next.set(n);
        id
    };
    for _ in 0..20 {
        add(1, 0); // Ava
    }
    let wrong: Vec<i64> = (0..2).map(|_| add(1, 5)).collect(); // two of someone else, confirmed as Ava by mistake
    for _ in 0..20 {
        add(2, 9); // Mia
    }
    let ava_unnamed: Vec<i64> = (0..3).map(|_| add(3, 0)).collect();
    let removed = {
        let mut n = next.get();
        let id = insert(&mut n, 3, 0, 7, 0.55);
        next.set(n);
        id
    }; // looks a lot like Ava, but was taken out of her
    for _ in 0..2 {
        add(4, 20); // a stranger
    }
    c.execute("INSERT INTO photo_face_negatives VALUES (?1,1,'t')", [removed]).unwrap();
    drop(c);
    Fixture { dir, wrong, ava_unnamed, removed }
}

async fn start(f: &Fixture) -> SocketAddr {
    let db_path = f.dir.path().join("archive.db").to_string_lossy().into_owned();
    let bl = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let upstream = bl.local_addr().unwrap();
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = bl.accept().await else { break };
            let p = db_path.clone();
            tokio::spawn(async move {
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(s), service_fn(move |r| backend_handler(p.clone(), r))).await;
            });
        }
    });
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "<html>shell</html>").unwrap();
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let cfg = Arc::new(Config {
        listen: addr,
        upstream,
        upstream_plain: true,
        dist: dist.path().to_path_buf(),
        cert: None,
        key: None,
        proxy_key: KEY.into(),
        print_ready: false,
        limits: Limits::default(),
        db: None,
        native_routes: vec!["face_learning".into()],
        launch_secret: None,
        session_touch_interval: Duration::from_secs(300),
    });
    tokio::spawn(async move {
        let _ = server::serve(l, cfg, std::future::pending()).await;
        drop(dist);
    });
    addr
}

struct Reply {
    status: u16,
    headers: HashMap<String, String>,
    body: String,
}

async fn get(addr: SocketAddr, path: &str, extra: &str) -> Reply {
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(format!("GET {path} HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\n{extra}\r\n").as_bytes()).await.unwrap();
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

fn ids(v: &Value) -> Vec<i64> {
    v["items"].as_array().unwrap().iter().map(|i| i["detection_id"].as_i64().unwrap()).collect()
}

#[tokio::test]
async fn outliers_put_the_mistakes_first_and_flag_them() {
    let f = database();
    let addr = start(&f).await;
    let r = get(addr, "/api/photos/faces/learning/outliers?performer_id=1", IN).await;
    assert_eq!(r.status, 200, "{}", r.body);
    assert_eq!(r.headers.get("x-served-by").map(String::as_str), Some("rust"));
    let v: Value = serde_json::from_str(&r.body).unwrap();
    assert_eq!(v["total"], json!(22));
    assert_eq!(v["different"], json!(2));
    let first_two: Vec<i64> = ids(&v)[..2].to_vec();
    assert!(f.wrong.iter().all(|w| first_two.contains(w)), "{first_two:?} vs {:?}", f.wrong);
    assert_eq!(v["items"][0]["different"], json!(true));
    assert_eq!(v["items"][5]["different"], json!(false));
    assert!(v["items"][0]["crop_url"].as_str().unwrap().starts_with("/api/photos/faces/crops/"));
}

#[tokio::test]
async fn suggestions_offer_the_unnamed_lookalikes_but_not_the_removed_face_or_strangers() {
    let f = database();
    let addr = start(&f).await;
    let r = get(addr, "/api/photos/faces/learning/suggestions?performer_id=1", IN).await;
    assert_eq!(r.status, 200, "{}", r.body);
    let v: Value = serde_json::from_str(&r.body).unwrap();
    let got: std::collections::HashSet<i64> = ids(&v).into_iter().collect();
    assert_eq!(got, f.ava_unnamed.iter().copied().collect(), "{got:?}");
    assert!(!got.contains(&f.removed));
    assert_eq!(v["strong"], json!(3));
    assert_eq!(v["items"][0]["strong"], json!(true));
}

#[tokio::test]
async fn review_lists_who_has_new_lookalikes_waiting() {
    let f = database();
    let addr = start(&f).await;
    let r = get(addr, "/api/photos/faces/learning/review", IN).await;
    assert_eq!(r.status, 200, "{}", r.body);
    let v: Value = serde_json::from_str(&r.body).unwrap();
    let ava = v["people"].as_array().unwrap().iter().find(|p| p["performer_id"] == json!(1)).expect("Ava has unnamed lookalikes");
    assert_eq!((ava["name"].as_str(), ava["suggestions"].as_i64(), ava["strong"].as_i64()), (Some("Ava Stone"), Some(3), Some(3)));
    assert!(v["people"].as_array().unwrap().iter().all(|p| p["performer_id"] != json!(2)), "Mia has none");
}

#[tokio::test]
async fn status_counts_confirmed_and_removed_faces_with_names() {
    let f = database();
    let addr = start(&f).await;
    let r = get(addr, "/api/photos/faces/learning/status", IN).await;
    let v: Value = serde_json::from_str(&r.body).unwrap();
    assert_eq!(v["confirmed"], json!(42));
    assert_eq!(v["removed"], json!(1));
    let ava = v["people"].as_array().unwrap().iter().find(|p| p["performer_id"] == json!(1)).unwrap();
    assert_eq!((ava["name"].as_str(), ava["confirmed"].as_i64(), ava["removed"].as_i64()), (Some("Ava Stone"), Some(22), Some(1)));
}

#[tokio::test]
async fn follows_the_backends_login_and_ignores_bad_numbers() {
    let f = database();
    let addr = start(&f).await;
    for p in ["/api/photos/faces/learning/status", "/api/photos/faces/learning/outliers?performer_id=1", "/api/photos/faces/learning/suggestions?performer_id=1"] {
        let r = get(addr, p, "").await;
        assert_eq!(r.status, 401, "{p}");
        assert_eq!(r.body, r#"{"detail":"Authentication required"}"#);
    }
    let r = get(addr, "/api/photos/faces/learning/outliers?performer_id=abc", IN).await;
    assert_eq!(r.headers.get("x-served-by").map(String::as_str), Some("python"), "a bad number goes to Python, which answers it");
}

#[tokio::test]
async fn a_new_removal_is_picked_up_without_a_restart() {
    let f = database();
    let addr = start(&f).await;
    let before: Value = serde_json::from_str(&get(addr, "/api/photos/faces/learning/suggestions?performer_id=1", IN).await.body).unwrap();
    assert_eq!(before["total"], json!(3));
    let c = Connection::open(f.dir.path().join("archive.db")).unwrap();
    c.execute("INSERT INTO photo_face_negatives VALUES (?1,1,'t')", [f.ava_unnamed[0]]).unwrap();
    let after: Value = serde_json::from_str(&get(addr, "/api/photos/faces/learning/suggestions?performer_id=1", IN).await.body).unwrap();
    assert!(after["total"].as_i64().unwrap() < 3, "the face just removed, and what looks just like it, is no longer offered");
    assert!(!ids(&after).contains(&f.ava_unnamed[0]));
}
