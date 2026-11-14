//! A route answered by the front door itself (GET /api/photos/channels): same answer as Python, same login rules, same
//! caching rules, switchable, and the session kept alive on the Python side.

use std::collections::HashMap;
use std::convert::Infallible;
use std::io::Read;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use ax_db::rusqlite::Connection;
use ax_db::sessions::token_hash;
use ax_server::config::{Config, Limits};
use ax_server::{native, server};
use bytes::Bytes;
use http_body_util::{combinators::BoxBody, BodyExt, Full};
use hyper::body::Incoming;
use hyper::service::service_fn;
use hyper::{Request, Response};
use hyper_util::rt::TokioIo;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const KEY: &str = "0123456789abcdef0123456789abcdef";
type B = BoxBody<Bytes, Infallible>;

/// Table layouts straight from the committed snapshot of the Python schema (columns and types; constraints left out so the
/// test can insert only what it needs).
fn create_table(c: &Connection, snapshot: &serde_json::Value, table: &str) {
    let cols: Vec<String> = snapshot[table].as_array().unwrap().iter().map(|col| format!("\"{}\" {}", col["name"].as_str().unwrap(), col["type"].as_str().unwrap())).collect();
    c.execute_batch(&format!("CREATE TABLE \"{table}\" ({});", cols.join(", "))).unwrap();
}

fn far() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0
}

fn database(with_channels: bool) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in ["web_sessions", "channels", "photo_library_items", "photo_library_scan_state"] {
        create_table(&c, &snapshot, t);
    }
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far())).unwrap();
    if with_channels {
        // titles chosen to prove the order is case-insensitive for ASCII only (SQLite's NOCASE does not fold \u{c9}, so \"Zed\" comes before \"\u{c9}mile\", in Python too); one without scan state; one with a non-ASCII name
        for (id, title, user, image) in [("-1002", "beta", "b", None), ("-1001", "Alpha", "a", Some("manual-upload:x")), ("-1003", "Émile ☕", "e", None), ("-1004", "Zed", "z", None)] {
            c.execute("INSERT INTO channels(telegram_id,title,username,image,updated_at) VALUES (?1,?2,?3,?4,'2025-01-01')", (id, title, user, image)).unwrap();
        }
        let mut id = 0;
        for (ch, n) in [("-1001", 3), ("-1002", 1), ("-1003", 2), ("-9999", 2)] {
            for _ in 0..n {
                id += 1;
                c.execute("INSERT INTO photo_library_items(id,channel_id) VALUES (?1,?2)", (id, ch)).unwrap();
            }
        }
        c.execute("INSERT INTO photo_library_scan_state(channel_id,status,last_scan_at,messages_scanned,photos_found,last_error) VALUES ('-1001','COMPLETE','2025-01-02',3,3,'')", []).unwrap();
        c.execute("INSERT INTO photo_library_scan_state(channel_id,status,last_scan_at,messages_scanned,photos_found,last_error) VALUES ('-1002','FAILED',NULL,0,0,'flood wait')", []).unwrap();
    }
    (dir, path)
}

/// The expected body, written out by hand: Python's compact JSON, columns in query order, orphans last.
const EXPECTED: &str = concat!(
    r#"[{"telegram_id":"-1001","title":"Alpha","username":"a","image":"manual-upload:x","updated_at":"2025-01-01","photo_count":3,"photo_scan_status":"COMPLETE","photo_last_scan_at":"2025-01-02","photo_messages_scanned":3,"photo_photos_found":3,"photo_scan_error":""},"#,
    r#"{"telegram_id":"-1002","title":"beta","username":"b","image":null,"updated_at":"2025-01-01","photo_count":1,"photo_scan_status":"FAILED","photo_last_scan_at":null,"photo_messages_scanned":0,"photo_photos_found":0,"photo_scan_error":"flood wait"},"#,
    r#"{"telegram_id":"-1004","title":"Zed","username":"z","image":null,"updated_at":"2025-01-01","photo_count":0,"photo_scan_status":"NEVER","photo_last_scan_at":null,"photo_messages_scanned":0,"photo_photos_found":0,"photo_scan_error":""},"#,
    r#"{"telegram_id":"-1003","title":"Émile ☕","username":"e","image":null,"updated_at":"2025-01-01","photo_count":2,"photo_scan_status":"NEVER","photo_last_scan_at":null,"photo_messages_scanned":0,"photo_photos_found":0,"photo_scan_error":""},"#,
    r#"{"telegram_id":"-9999","photo_count":2,"title":"Unknown channel","username":"","image":"","updated_at":"","photo_scan_status":"UNKNOWN","photo_last_scan_at":null,"photo_messages_scanned":0,"photo_photos_found":2,"photo_scan_error":""}]"#
);

#[derive(Default)]
struct Seen {
    pings: AtomicUsize,
    ping_cookies: Mutex<Vec<String>>,
    ping_peers: Mutex<Vec<String>>,
}

async fn backend_handler(seen: Arc<Seen>, req: Request<Incoming>) -> Result<Response<B>, Infallible> {
    if req.uri().path() == "/api/app/ping" {
        seen.pings.fetch_add(1, Ordering::SeqCst);
        seen.ping_cookies.lock().unwrap().push(req.headers().get("cookie").and_then(|v| v.to_str().ok()).unwrap_or("").to_string());
        seen.ping_peers.lock().unwrap().push(req.headers().get("x-archive-x-peer").and_then(|v| v.to_str().ok()).unwrap_or("").to_string());
        return Ok(Response::builder().header("content-type", "application/json").body(Full::new(Bytes::from(r#"{"ok":true}"#)).boxed()).unwrap());
    }
    Ok(Response::builder().status(200).header("content-type", "application/json").body(Full::new(Bytes::from(r#"{"from":"python"}"#)).boxed()).unwrap())
}

async fn start_backend() -> (SocketAddr, Arc<Seen>) {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let seen = Arc::new(Seen::default());
    let s2 = seen.clone();
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = l.accept().await else { break };
            let seen = s2.clone();
            tokio::spawn(async move {
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(s), service_fn(move |r| backend_handler(seen.clone(), r))).await;
            });
        }
    });
    (addr, seen)
}

struct Door {
    addr: SocketAddr,
    _keep: Vec<tempfile::TempDir>,
}

async fn start_door(routes: &[&str], with_channels: bool, touch: Duration) -> (Door, Arc<Seen>) {
    let (upstream, seen) = start_backend().await;
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "<html>shell</html>").unwrap();
    let (dbdir, dbpath) = database(with_channels);
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let cfg = Arc::new(Config {
        listen: addr, upstream, upstream_plain: true, dist: dist.path().to_path_buf(), cert: None, key: None, proxy_key: KEY.into(), print_ready: false,
        limits: Limits::default(), db: Some(dbpath), native_routes: routes.iter().map(|s| s.to_string()).collect(), session_touch_interval: touch,
    });
    tokio::spawn(async move { let _ = server::serve(l, cfg, std::future::pending()).await; });
    (Door { addr, _keep: vec![dist, dbdir] }, seen)
}

struct Reply {
    status: u16,
    headers: HashMap<String, String>,
    body: Vec<u8>,
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
            if n == 0 { break; }
            out.extend_from_slice(&b[p + 2..p + 2 + n]);
            b = &b[p + 2 + n + 2..];
        }
        body = out;
    }
    Reply { status, headers, body }
}

const AUTH: &str = "Cookie: session=tok\r\n";

#[tokio::test]
async fn it_answers_exactly_like_python_when_switched_on() {
    let (door, _) = start_door(&["photos_channels"], true, Duration::from_secs(300)).await;
    let r = get(door.addr, "/api/photos/channels", AUTH).await;
    assert_eq!(r.status, 200);
    assert_eq!(String::from_utf8(r.body).unwrap(), EXPECTED);
    assert_eq!(r.headers["x-served-by"], "rust");
    assert_eq!(r.headers["content-type"], "application/json");
    assert_eq!(r.headers["cache-control"], "private, no-cache");
    assert!(r.headers["etag"].starts_with('"') && r.headers["etag"].len() == 10, "{:?}", r.headers["etag"]);
    assert_eq!(r.headers["x-content-type-options"], "nosniff", "the same security headers as every other answer");
}

#[tokio::test]
async fn without_a_live_session_it_is_the_same_401() {
    let (door, _) = start_door(&["photos_channels"], true, Duration::from_secs(300)).await;
    for extra in ["", "Cookie: session=nope\r\n", "Cookie: other=tok\r\n"] {
        let r = get(door.addr, "/api/photos/channels", extra).await;
        assert_eq!(r.status, 401);
        assert_eq!(String::from_utf8(r.body).unwrap(), r#"{"detail":"Authentication required"}"#);
        assert_eq!(r.headers["x-served-by"], "rust");
    }
}

#[tokio::test]
async fn switched_off_it_goes_to_python_as_before() {
    for routes in [&[][..], &["none"][..]] {
        let (door, _) = start_door(routes, true, Duration::from_secs(300)).await;
        let r = get(door.addr, "/api/photos/channels", AUTH).await;
        assert_eq!(String::from_utf8(r.body).unwrap(), r#"{"from":"python"}"#, "routes {routes:?}");
        assert_eq!(r.headers["x-served-by"], "python");
    }
    // other methods and other paths never reach the native route
    let (door, _) = start_door(&["photos_channels"], true, Duration::from_secs(300)).await;
    let r = get(door.addr, "/api/photos/channels/extra", AUTH).await;
    assert_eq!(r.headers["x-served-by"], "python");
}

#[tokio::test]
async fn etag_304_and_gzip_follow_the_pythons_rules() {
    let (door, _) = start_door(&["photos_channels"], true, Duration::from_secs(300)).await;
    let plain = get(door.addr, "/api/photos/channels", AUTH).await;
    // this answer is under 1,024 bytes? make sure the test data is big enough to be compressed
    assert!(plain.body.len() >= 1024, "the test data must be at least 1,024 bytes (got {})", plain.body.len());
    assert!(!plain.headers.contains_key("content-encoding"));
    let etag = plain.headers["etag"].clone();

    let gz = get(door.addr, "/api/photos/channels", &format!("{AUTH}Accept-Encoding: gzip, deflate, br\r\n")).await;
    assert_eq!(gz.headers["content-encoding"], "gzip");
    assert_eq!(gz.headers["vary"], "Accept-Encoding");
    assert_eq!(gz.headers["etag"], etag, "the ETag is of the uncompressed body, as in Python");
    let mut unzipped = String::new();
    flate2::read::GzDecoder::new(&gz.body[..]).read_to_string(&mut unzipped).unwrap();
    assert_eq!(unzipped.as_bytes(), &plain.body[..]);
    assert!(gz.body.len() < plain.body.len());

    let again = get(door.addr, "/api/photos/channels", &format!("{AUTH}If-None-Match: {etag}\r\n")).await;
    assert_eq!(again.status, 304);
    assert!(again.body.is_empty());
    assert_eq!(again.headers["etag"], etag);
    let other = get(door.addr, "/api/photos/channels", &format!("{AUTH}If-None-Match: \"00000000\"\r\n")).await;
    assert_eq!(other.status, 200);
}

#[tokio::test]
async fn an_empty_library_is_an_empty_list() {
    let (door, _) = start_door(&["photos_channels"], false, Duration::from_secs(300)).await;
    let r = get(door.addr, "/api/photos/channels", AUTH).await;
    assert_eq!((r.status, String::from_utf8(r.body).unwrap().as_str()), (200, "[]"));
}

#[tokio::test]
async fn the_backend_is_told_now_and_then_that_the_session_is_in_use() {
    let (door, seen) = start_door(&["photos_channels"], true, Duration::from_millis(400)).await;
    for _ in 0..4 {
        assert_eq!(get(door.addr, "/api/photos/channels", AUTH).await.status, 200);
    }
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(seen.pings.load(Ordering::SeqCst), 1, "four requests inside the interval need one report");
    assert_eq!(seen.ping_cookies.lock().unwrap()[0], "session=tok", "the report carries the session cookie");
    assert_eq!(seen.ping_peers.lock().unwrap()[0], "127.0.0.1", "and the signed client address");
    tokio::time::sleep(Duration::from_millis(450)).await;
    assert_eq!(get(door.addr, "/api/photos/channels", AUTH).await.status, 200);
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(seen.pings.load(Ordering::SeqCst), 2, "after the interval it reports again");
    // a refused session is not reported
    get(door.addr, "/api/photos/channels", "Cookie: session=nope\r\n").await;
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert_eq!(seen.pings.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn an_unreadable_library_is_a_503_not_a_wrong_answer() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.execute_batch("CREATE TABLE web_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);").unwrap();
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far())).unwrap();
    let (upstream, _) = start_backend().await;
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "x").unwrap();
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let cfg = Arc::new(Config { listen: addr, upstream, upstream_plain: true, dist: dist.path().to_path_buf(), cert: None, key: None, proxy_key: KEY.into(), print_ready: false, limits: Limits::default(), db: Some(path), native_routes: vec!["photos_channels".into()], session_touch_interval: Duration::from_secs(300) });
    tokio::spawn(async move { let _ = server::serve(l, cfg, std::future::pending()).await; });
    let r = get(addr, "/api/photos/channels", AUTH).await;
    assert_eq!(r.status, 503);
    assert!(String::from_utf8_lossy(&r.body).contains("could not be read"));
}

#[test]
fn an_unknown_route_name_is_refused_at_start() {
    let proxy = ax_server::proxy::Proxy::new(&Config {
        listen: "127.0.0.1:0".parse().unwrap(), upstream: "127.0.0.1:1".parse().unwrap(), upstream_plain: true, dist: ".".into(), cert: None, key: None, proxy_key: KEY.into(),
        print_ready: false, limits: Limits::default(), db: None, native_routes: vec![], session_touch_interval: Duration::from_secs(300),
    });
    let e = native::Native::new(&["nonsense".to_string()], None, proxy, Duration::from_secs(1)).err().unwrap();
    assert!(e.contains("unknown native route") && e.contains("photos_channels"), "{e}");
}
