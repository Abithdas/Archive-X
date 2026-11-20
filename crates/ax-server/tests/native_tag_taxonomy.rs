//! GET /api/tag-taxonomy answered by the front door, from the current mode's tags every time.

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

/// Tags on four library videos: "Outdoor" and "outdoor" are one tag by key (video 1 carries both, so it counts once), "1080p" is a format
/// label (generic), "Everywhere" is on every video (common), and one tag is only on a video that is not in the library.
fn database(extra: impl FnOnce(&Connection)) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in ["web_sessions", "tags", "video_tags", "settings"] {
        create_table(&c, &snapshot, t);
    }
    c.execute("INSERT INTO settings VALUES ('content_mode','\"nsfw\"')", []).unwrap();
    c.execute_batch("CREATE TABLE archive_x_library_media (id INTEGER);").unwrap(); // a view in the real database
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    for v in 1..=4 {
        c.execute("INSERT INTO archive_x_library_media(id) VALUES (?1)", [v]).unwrap();
    }
    for (id, name) in [(1, "Outdoor"), (2, "outdoor"), (3, "1080p"), (4, "Everywhere"), (5, "Rare Find"), (6, "Hidden")] {
        c.execute("INSERT INTO tags(id,name) VALUES (?1,?2)", (id, name)).unwrap();
    }
    for (t, v) in [(1, 1), (1, 2), (2, 1), (2, 3), (3, 1), (4, 1), (4, 2), (4, 3), (4, 4), (5, 2), (6, 99)] {
        c.execute("INSERT INTO video_tags(tag_id,video_id) VALUES (?1,?2)", (t, v)).unwrap();
    }
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
        native_routes: vec!["tag_taxonomy".into()],
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

fn json(r: &Reply) -> serde_json::Value {
    serde_json::from_slice(&r.body).unwrap()
}

#[tokio::test]
async fn variants_share_a_key_and_a_video_with_both_counts_once() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/tag-taxonomy", "").await;
    assert_eq!(r.status, 200);
    assert_eq!(r.headers["x-served-by"], "rust");
    let v = json(&r);
    assert_eq!(v["total_videos"], 4);
    let outdoor = &v["tag_map"]["outdoor"];
    assert_eq!(outdoor["video_count"], 3, "videos 1, 2 and 3; video 1 has both spellings");
    assert_eq!(outdoor["name"], "Outdoor", "the spelling on more videos, else the shorter, else the folded name");
    assert_eq!(outdoor["aliases"], serde_json::json!(["outdoor"]));
    assert_eq!(outdoor["categories"], serde_json::json!(["Outdoor & Public"]));
    assert_eq!(v["tag_map"].as_object().unwrap().len(), 4, "the tag only on a video outside the library is not there");
    assert!(v["tag_map"].get("hidden").is_none());
}

#[tokio::test]
async fn generic_and_common_tags_are_counted_but_not_ranked() {
    let d = door(|_| {}).await;
    let v = json(&get(d.addr, "/api/tag-taxonomy", "").await);
    assert_eq!(v["tag_map"]["1080p"]["generic"], true);
    assert_eq!(v["tag_map"]["everywhere"]["common"], true, "on all 4 of 4 videos");
    assert_eq!(v["excluded_generic_count"], 1);
    assert_eq!(v["excluded_common_count"], 1);
    let ranked: Vec<&str> = v["ranked_tags"].as_array().unwrap().iter().map(|t| t["key"].as_str().unwrap()).collect();
    assert_eq!(ranked, ["rare find", "outdoor"], "rarer first");
    assert_eq!(v["common_threshold"], 0.9);
}

#[tokio::test]
async fn an_empty_library_gives_an_empty_taxonomy_and_a_login_is_required() {
    let d = door(|c| {
        c.execute("DELETE FROM archive_x_library_media", []).unwrap();
    })
    .await;
    let v = json(&get(d.addr, "/api/tag-taxonomy", "").await);
    assert_eq!((v["total_videos"].as_i64(), v["tag_map"].as_object().unwrap().len(), v["ranked_tags"].as_array().unwrap().len()), (Some(0), 0, 0));
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(b"GET /api/tag-taxonomy HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401"), "{raw}");
}
