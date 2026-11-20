//! GET /api/studios answered by the front door: the plain list is Rust's; a search, an odd limit, an empty list and a pending backfill go to Python.

use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use ax_db::rusqlite::Connection;
use ax_db::sessions::token_hash;
use ax_server::config::{Config, Limits};
use ax_server::server;
use bytes::Bytes;
use http_body_util::{combinators::BoxBody, BodyExt, Full};
use hyper::body::Incoming;
use hyper::service::service_fn;
use hyper::{Request, Response};
use hyper_util::rt::TokioIo;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const KEY: &str = "0123456789abcdef0123456789abcdef";
const AUTH: &str = "Cookie: session=tok\r\n";
type B = BoxBody<Bytes, Infallible>;

fn create_table(c: &Connection, snapshot: &serde_json::Value, table: &str) {
    let cols: Vec<String> = snapshot[table]
        .as_array()
        .unwrap()
        .iter()
        .map(|col| format!("\"{}\" {}", col["name"].as_str().unwrap(), col["type"].as_str().unwrap()))
        .collect();
    c.execute_batch(&format!("CREATE TABLE \"{table}\" ({});", cols.join(", "))).unwrap();
}

/// Studios: Northlight (3 videos, cached picture), Aurora (2), Zed (1), the "Unknown Studio" row (0 videos but always listed) and
/// "No Videos" (0 videos: not listed).
fn database(extra: impl FnOnce(&Connection)) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in ["web_sessions", "studios", "video_studios", "studio_image_cache", "media", "settings"] {
        create_table(&c, &snapshot, t);
    }
    c.execute("INSERT INTO settings VALUES ('content_mode','\"nsfw\"')", []).unwrap();
    c.execute_batch("CREATE TABLE archive_x_library_media (id INTEGER);").unwrap(); // a view in the real database
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    for (id, name, image) in [(1, "Northlight", Some("https://img/n.jpg")), (2, "Aurora Films", None), (3, "Zed", None), (4, "Unknown Studio", None), (5, "No Videos", None)] {
        c.execute("INSERT INTO studios(id,name,image) VALUES (?1,?2,?3)", (id, name, image)).unwrap();
    }
    for v in 1..=4 {
        c.execute("INSERT INTO archive_x_library_media(id) VALUES (?1)", [v]).unwrap();
    }
    for (s, v) in [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (3, 4), (5, 99)] {
        c.execute("INSERT INTO video_studios(studio_id,video_id) VALUES (?1,?2)", (s, v)).unwrap();
    }
    c.execute("INSERT INTO studio_image_cache(studio_id,data,url,updated_at) VALUES (1, x'ffd8ff', 'https://img/n.jpg', '2025-01-01')", [])
        .unwrap();
    c.execute("INSERT INTO studio_image_cache(studio_id,data) VALUES (2, x'')", []).unwrap(); // empty data is not a cached picture
    extra(&c);
    (dir, path)
}

async fn backend(req: Request<Incoming>) -> Result<Response<B>, Infallible> {
    let _ = req;
    Ok(Response::builder().header("content-type", "application/json").body(Full::new(Bytes::from(r#"{"from":"python"}"#)).boxed()).unwrap())
}

struct Door {
    addr: SocketAddr,
    _keep: Vec<tempfile::TempDir>,
}

async fn door(extra: impl FnOnce(&Connection)) -> Door {
    let up = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let upstream = up.local_addr().unwrap();
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = up.accept().await else { break };
            tokio::spawn(async move {
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(s), service_fn(backend)).await;
            });
        }
    });
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "x").unwrap();
    let (dbdir, dbpath) = database(extra);
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
        db: Some(dbpath),
        native_routes: vec!["studios_list".into()],
        launch_secret: None,
        session_touch_interval: Duration::from_secs(300),
    });
    tokio::spawn(async move {
        let _ = server::serve(l, cfg, std::future::pending()).await;
    });
    Door { addr, _keep: vec![dist, dbdir] }
}

struct Reply {
    status: u16,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

async fn get(addr: SocketAddr, path: &str, extra: &str) -> Reply {
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(format!("GET {path} HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\n{AUTH}{extra}\r\n").as_bytes()).await.unwrap();
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
    Reply { status, headers, body }
}

fn json(r: &Reply) -> Vec<serde_json::Value> {
    serde_json::from_slice(&r.body).unwrap()
}

#[tokio::test]
async fn the_plain_list_is_ordered_and_counted_like_python() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/studios", "").await;
    assert_eq!(r.status, 200);
    assert_eq!(r.headers["x-served-by"], "rust");
    let rows = json(&r);
    let names: Vec<&str> = rows.iter().map(|s| s["name"].as_str().unwrap()).collect();
    // "Unknown Studio" first although it has no videos; then by video count, then name; "No Videos" (only video 99, not in the library) is out
    assert_eq!(names, ["Unknown Studio", "Northlight", "Aurora Films", "Zed"]);
    let counts: Vec<i64> = rows.iter().map(|s| s["video_count"].as_i64().unwrap()).collect();
    assert_eq!(counts, [0, 3, 2, 1]);
    let cached: Vec<i64> = rows.iter().map(|s| s["has_cached_image"].as_i64().unwrap()).collect();
    assert_eq!(cached, [0, 1, 0, 0], "only a non-empty cached picture that matches the studio's image counts");
    assert_eq!(rows[1]["image_cache_updated_at"], "2025-01-01");
    assert!(rows.iter().all(|s| s["movies"] == 0 && s["series"] == 0), "no matched titles in this fixture");
    let keys: Vec<&str> = rows[1].as_object().unwrap().keys().map(|k| k.as_str()).collect();
    assert_eq!(&keys[keys.len() - 5..], ["video_count", "image_cache_updated_at", "has_cached_image", "movies", "series"]);
}

#[tokio::test]
async fn brief_gives_the_eight_fields_in_order() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/studios?brief=1&limit=2", "").await;
    assert_eq!(
        String::from_utf8(r.body).unwrap(),
        r#"[{"id":4,"name":"Unknown Studio","video_count":0,"movies":0,"series":0,"image":null,"image_cache_updated_at":null,"has_cached_image":0},{"id":1,"name":"Northlight","video_count":3,"movies":0,"series":0,"image":"https://img/n.jpg","image_cache_updated_at":"2025-01-01","has_cached_image":1}]"#
    );
    let r = get(d.addr, "/api/studios?brief=yes&limit=1", "").await;
    assert!(json(&r)[0].as_object().unwrap().len() > 8, "only the exact value 1 turns brief on");
}

#[tokio::test]
async fn limits_follow_pythons_clamping_and_odd_forms_are_left_to_python() {
    let d = door(|_| {}).await;
    assert_eq!(json(&get(d.addr, "/api/studios?limit=2", "").await).len(), 2);
    assert_eq!(json(&get(d.addr, "/api/studios?limit=0", "").await).len(), 1, "clamped up to 1");
    assert_eq!(json(&get(d.addr, "/api/studios?limit=abc", "").await).len(), 4, "int() fails, so the default");
    for p in ["/api/studios?limit=%2B2", "/api/studios?limit=1_0", "/api/studios?limit=-1"] {
        assert_eq!(get(d.addr, p, "").await.headers["x-served-by"], "python", "{p}");
    }
}

#[tokio::test]
async fn a_search_a_pending_backfill_and_an_empty_answer_are_passed_to_python() {
    let d = door(|_| {}).await;
    assert_eq!(get(d.addr, "/api/studios?q=zed", "").await.headers["x-served-by"], "python");
    // a video that names a studio but has no link yet: Python repairs that before listing
    let d = door(|c| {
        c.execute("INSERT INTO media(id, site_name) VALUES (50, 'Brand New Site')", []).unwrap();
    })
    .await;
    assert_eq!(get(d.addr, "/api/studios", "").await.headers["x-served-by"], "python");
    let d = door(|c| {
        c.execute("DELETE FROM studios", []).unwrap();
    })
    .await;
    assert_eq!(get(d.addr, "/api/studios", "").await.headers["x-served-by"], "python");
}

#[tokio::test]
async fn a_login_is_still_required() {
    let d = door(|_| {}).await;
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(b"GET /api/studios HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401"), "{raw}");
}
