//! GET /api/performers answered by the front door: the plain catalogue is Rust's, everything unusual is passed to Python.

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

/// Performers: Ava (3 videos, has a cached picture), Ana (2), Zed (2, one of them only through local_video_performers), Mia (1), the
/// "Unknown Performer" row (0 videos but always listed), and "No Videos" (0 videos: not listed).
fn database(extra: impl FnOnce(&Connection)) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in ["web_sessions", "performers", "video_performers", "local_video_performers", "performer_image_cache"] {
        create_table(&c, &snapshot, t);
    }
    // the rows these tests make are adult and in the library; the app itself opens in SFW
    create_table(&c, &snapshot, "settings");
    c.execute("INSERT INTO settings VALUES ('content_mode','\"nsfw\"')", []).unwrap();
    c.execute_batch("CREATE TABLE archive_x_library_media (id INTEGER);").unwrap(); // a view in the real database
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    for (id, name, gender, image) in [
        (1, "Ava Stone", "female", Some("https://img/a.jpg")),
        (2, "Mia Rivera", "Trans woman", None),
        (3, "Zed", "nonbinary", None),
        (4, "Unknown Performer", "", None),
        (5, "No Videos", "female", None),
        (6, "Ana", "MALE ", None),
    ] {
        c.execute("INSERT INTO performers(id,name,image,gender) VALUES (?1,?2,?3,?4)", (id, name, image, gender)).unwrap();
    }
    for v in 1..=4 {
        c.execute("INSERT INTO archive_x_library_media(id) VALUES (?1)", [v]).unwrap();
    }
    for (p, v) in [(1, 1), (1, 2), (1, 3), (6, 1), (6, 2), (3, 1), (2, 4)] {
        c.execute("INSERT INTO video_performers(performer_id,video_id) VALUES (?1,?2)", (p, v)).unwrap();
    }
    c.execute("INSERT INTO local_video_performers(performer_id,video_id) VALUES (3,3)", []).unwrap();
    c.execute("INSERT INTO local_video_performers(performer_id,video_id) VALUES (3,1)", []).unwrap(); // the same video twice counts once
    c.execute("INSERT INTO performer_image_cache(performer_id,data) VALUES (1, x'ffd8ff')", []).unwrap();
    c.execute("INSERT INTO performer_image_cache(performer_id,data) VALUES (2, x'')", []).unwrap(); // empty data is not a cached picture
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
        native_routes: vec!["performers_list".into()],
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
async fn the_catalogue_is_ordered_counted_and_tidied_like_python() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/performers", "").await;
    assert_eq!(r.status, 200);
    assert_eq!(r.headers["x-served-by"], "rust");
    let rows = json(&r);
    let names: Vec<&str> = rows.iter().map(|p| p["name"].as_str().unwrap()).collect();
    // "Unknown Performer" first although it has no videos; then by video count, then name; "No Videos" is not listed
    assert_eq!(names, ["Unknown Performer", "Ava Stone", "Ana", "Zed", "Mia Rivera"]);
    let counts: Vec<i64> = rows.iter().map(|p| p["video_count"].as_i64().unwrap()).collect();
    assert_eq!(counts, [0, 3, 2, 2, 1], "Zed has video 1 twice (both tables) and video 3 locally: 2, not 3");
    let genders: Vec<&str> = rows.iter().map(|p| p["gender"].as_str().unwrap()).collect();
    assert_eq!(genders, ["", "female", "male", "", "trans"], "'MALE ' becomes male, 'Trans woman' trans, 'nonbinary' blank");
    let cached: Vec<i64> = rows.iter().map(|p| p["has_cached_image"].as_i64().unwrap()).collect();
    assert_eq!(cached, [0, 1, 0, 0, 0], "only a non-empty cached picture counts");
    // every performers column comes first, in table order, then the two computed ones, then the title counts Python adds
    let keys: Vec<&str> = rows[1].as_object().unwrap().keys().map(|k| k.as_str()).collect();
    assert_eq!(&keys[..4], ["id", "name", "image", "profile"]);
    assert_eq!(&keys[keys.len() - 4..], ["video_count", "has_cached_image", "movies", "series"]);
    assert!(rows.iter().all(|p| p["movies"] == 0 && p["series"] == 0), "no matched titles in this fixture");
    assert_eq!(rows[1]["image"], "https://img/a.jpg");
    assert!(rows[2]["image"].is_null());
}

#[tokio::test]
async fn brief_gives_the_nine_fields_in_order_with_null_for_the_missing_favourite() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/performers?brief=1&limit=2", "").await;
    assert_eq!(
        String::from_utf8(r.body).unwrap(),
        r#"[{"id":4,"name":"Unknown Performer","video_count":0,"movies":0,"series":0,"image":null,"gender":"","favorite":null,"has_cached_image":0},{"id":1,"name":"Ava Stone","video_count":3,"movies":0,"series":0,"image":"https://img/a.jpg","gender":"female","favorite":null,"has_cached_image":1}]"#
    );
    // only the exact value 1 turns brief on
    let r = get(d.addr, "/api/performers?brief=yes&limit=1", "").await;
    assert!(json(&r)[0].as_object().unwrap().len() > 9);
}

#[tokio::test]
async fn limits_follow_pythons_clamping_and_odd_forms_are_left_to_python() {
    let d = door(|_| {}).await;
    let n = |q: &'static str| {
        let addr = d.addr;
        async move {
            let r = get(addr, &format!("/api/performers{q}"), "").await;
            (r.headers["x-served-by"].clone(), if r.status == 200 && r.headers["x-served-by"] == "rust" { json(&r).len() } else { 0 })
        }
    };
    assert_eq!(n("?limit=2").await, ("rust".into(), 2));
    assert_eq!(n("?limit=0").await, ("rust".into(), 1), "max(1, ...)");
    assert_eq!(n("?limit=99999").await, ("rust".into(), 5), "min(5000, ...)");
    assert_eq!(n("?limit=abc").await, ("rust".into(), 5), "int() fails, so the default");
    assert_eq!(n("?limit=").await.0, "python", "empty: leave to Python");
    for odd in ["?limit=-3", "?limit=1_000", "?limit=%2B5", "?limit=%20%207", "?limit=1.5", "?limit=1&limit=2"] {
        assert_eq!(n(odd).await.0, "python", "{odd}");
    }
}

#[tokio::test]
async fn a_search_an_empty_catalogue_and_an_empty_answer_are_passed_to_python() {
    let d = door(|_| {}).await;
    assert_eq!(get(d.addr, "/api/performers?q=ava", "").await.headers["x-served-by"], "python");
    assert_eq!(get(d.addr, "/api/performers?q=%20%20", "").await.headers["x-served-by"], "rust", "blank search text is no search");
    assert_eq!(get(d.addr, "/api/performers?q=", "").await.headers["x-served-by"], "rust");
    let empty = door(|c| {
        c.execute("DELETE FROM performers", []).unwrap();
    })
    .await;
    assert_eq!(get(empty.addr, "/api/performers", "").await.headers["x-served-by"], "python", "Python fills an empty catalogue in; Rust never writes");
    let none_listed = door(|c| {
        c.execute("DELETE FROM video_performers", []).unwrap();
        c.execute("DELETE FROM local_video_performers", []).unwrap();
        c.execute("DELETE FROM performers WHERE name='Unknown Performer'", []).unwrap();
    })
    .await;
    assert_eq!(get(none_listed.addr, "/api/performers", "").await.headers["x-served-by"], "python", "an empty answer is re-checked by Python");
}

#[tokio::test]
async fn a_login_is_still_required() {
    let d = door(|_| {}).await;
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(b"GET /api/performers HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\n\r\n").await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401") && raw.contains(r#"{"detail":"Authentication required"}"#), "{raw}");
}

/// Python buffers at most 2 MiB; a bigger answer goes out untouched: no ETag, no gzip, no Cache-Control.
#[tokio::test]
async fn an_answer_over_2_mib_is_not_compressed_or_tagged_just_as_in_python() {
    let d = door(|c| {
        for i in 100..2600 {
            c.execute("INSERT INTO performers(id,name,bio) VALUES (?1,?2,?3)", (i, format!("Big {i:05}"), "x".repeat(1000))).unwrap();
            c.execute("INSERT INTO video_performers(performer_id,video_id) VALUES (?1,1)", [i]).unwrap();
        }
    })
    .await;
    let big = get(d.addr, "/api/performers", "Accept-Encoding: gzip\r\n").await;
    assert!(big.body.len() > 2 * 1024 * 1024, "the test data must exceed 2 MiB ({} bytes)", big.body.len());
    for h in ["etag", "content-encoding", "cache-control", "vary"] {
        assert!(!big.headers.contains_key(h), "{h} must not be set on an answer over 2 MiB");
    }
    assert_eq!(big.headers["content-length"], big.body.len().to_string());
    // the same data, trimmed under the limit, is tagged and compressed
    let small = get(d.addr, "/api/performers?limit=50", "Accept-Encoding: gzip\r\n").await;
    assert!(small.headers.contains_key("etag") && small.headers["content-encoding"] == "gzip");
}
