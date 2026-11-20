//! The photo files answered by the front door: a cached file is sent from here with Starlette's FileResponse headers; anything that needs
//! Python (a derivative to render, a cache path to record, a Range request, an odd type) goes there.

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

const THUMB_BYTES: usize = 2000;

fn photo_key(channel: &str, message: i64) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(format!("{channel}:{message}").as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

struct World {
    data: tempfile::TempDir,
}

impl World {
    fn photos(&self) -> std::path::PathBuf {
        self.data.path().join("cache").join("photo_library")
    }
    fn put(&self, kind: &str, name: &str, bytes: usize) -> std::path::PathBuf {
        let dir = self.photos().join(kind);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        std::fs::write(&path, vec![7u8; bytes]).unwrap();
        path
    }
}

/// Photo 1: everything cached (a recorded path, a thumbnail, a preview).  2: nothing cached, no recorded path.
/// 3: a recorded path but a PNG-named type that is not an image type.  4: a one-byte thumbnail.
fn database(world: &World, extra: impl FnOnce(&Connection)) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in ["web_sessions", "photo_library_items", "channels", "settings"] {
        create_table(&c, &snapshot, t);
    }
    c.execute("INSERT INTO settings VALUES ('content_mode','\"nsfw\"')", []).unwrap();
    c.execute("INSERT INTO channels(telegram_id,title,in_library,content_rating,holds) VALUES ('-1001','Photos',1,'nsfw','both')", []).unwrap();
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    let rel = |m: i64| format!("cache/photo_library/originals/{}.bin", photo_key("-1001", m));
    for (m, mime, local) in [(1, "image/jpeg", Some(rel(1))), (2, "image/jpeg", None), (3, "application/octet-stream", Some(rel(3))), (4, "image/png", Some(rel(4)))] {
        c.execute(
            "INSERT INTO photo_library_items(channel_id,message_id,filename,mime_type,local_path) VALUES ('-1001',?1,?2,?3,?4)",
            (m, format!("p{m}.jpg"), mime, local),
        )
        .unwrap();
    }
    let _ = world;
    extra(&c);
    (dir, path)
}

async fn backend_for(user: String, cache: String) -> (SocketAddr, tokio::task::JoinHandle<()>) {
    let up = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = up.local_addr().unwrap();
    let h = tokio::spawn(async move {
        loop {
            let Ok((s, _)) = up.accept().await else { break };
            let (user, cache) = (user.clone(), cache.clone());
            tokio::spawn(async move {
                let svc = service_fn(move |req: Request<Incoming>| {
                    let (user, cache) = (user.clone(), cache.clone());
                    async move {
                        let body = if req.uri().path() == "/internal/provider-keys" {
                            serde_json::json!({ "user_data_dir": user, "cache_dir": cache }).to_string()
                        } else {
                            r#"{"from":"python"}"#.to_string()
                        };
                        Ok::<_, Infallible>(Response::builder().header("content-type", "application/json").body(Full::new(Bytes::from(body)).boxed()).unwrap() as Response<B>)
                    }
                });
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(s), svc).await;
            });
        }
    });
    (addr, h)
}

struct Door {
    addr: SocketAddr,
    world: World,
    _keep: Vec<tempfile::TempDir>,
}

async fn door(extra: impl FnOnce(&World, &Connection)) -> Door {
    let world = World { data: tempfile::tempdir().unwrap() };
    std::fs::create_dir_all(world.data.path().join("cache")).unwrap();
    // photo 1: original, thumbnail and preview all on disk; the preview is written last, so it is the newer
    world.put("originals", &format!("{}.bin", photo_key("-1001", 1)), 5000);
    world.put("thumbnails", &format!("{}.w640.jpg", photo_key("-1001", 1)), THUMB_BYTES);
    world.put("originals", &format!("{}.bin", photo_key("-1001", 3)), 100);
    world.put("originals", &format!("{}.bin", photo_key("-1001", 4)), 100);
    world.put("thumbnails", &format!("{}.w640.jpg", photo_key("-1001", 4)), 1);
    std::thread::sleep(Duration::from_millis(30));
    world.put("previews", &format!("{}.jpg", photo_key("-1001", 1)), 3000);
    let (user, cache) = (world.data.path().to_str().unwrap().to_string(), world.data.path().join("cache").to_str().unwrap().to_string());
    let (upstream, _h) = backend_for(user, cache).await;
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "x").unwrap();
    let (dbdir, dbpath) = database(&world, |c| extra(&world, c));
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
        native_routes: vec!["photo_thumbnail".into(), "photo_preview".into(), "photo_image".into()],
        launch_secret: None,
        session_touch_interval: Duration::from_secs(300),
    });
    tokio::spawn(async move {
        let _ = server::serve(l, cfg, std::future::pending()).await;
    });
    Door { addr, world, _keep: vec![dist, dbdir] }
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

fn served(r: &Reply) -> &str {
    r.headers["x-served-by"].as_str()
}

#[tokio::test]
async fn a_cached_thumbnail_is_sent_with_the_file_response_headers() {
    let d = door(|_, _| {}).await;
    let r = get(d.addr, "/api/photos/-1001/1/thumbnail", "").await;
    assert_eq!((r.status, served(&r)), (200, "rust"));
    assert_eq!(r.body, vec![7u8; THUMB_BYTES]);
    let h = &r.headers;
    assert_eq!(h["content-type"], "image/jpeg");
    assert_eq!(h["content-length"], THUMB_BYTES.to_string());
    assert_eq!(h["cache-control"], "private, max-age=3600");
    assert_eq!(h["x-content-type-options"], "nosniff");
    assert_eq!(h["accept-ranges"], "bytes");
    assert!(!h.contains_key("content-disposition"), "the thumbnail route sets none");
    assert!(h["last-modified"].ends_with(" GMT"));
    // ETag: md5 of "<st_mtime as a Python float>-<st_size>", like Starlette
    let path = d.world.photos().join("thumbnails").join(format!("{}.w640.jpg", photo_key("-1001", 1)));
    let meta = std::fs::metadata(&path).unwrap();
    let since = meta.modified().unwrap().duration_since(std::time::UNIX_EPOCH).unwrap();
    let mtime = since.as_secs() as f64 + f64::from(since.subsec_nanos()) * 1e-9;
    assert_eq!(h["etag"], format!("\"{:x}\"", md5::compute(format!("{:?}-{}", mtime, meta.len()))));
}

#[tokio::test]
async fn a_thumbnail_that_is_missing_or_a_stub_is_left_to_python_which_renders_it() {
    let d = door(|_, _| {}).await;
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/2/thumbnail", "").await), "python", "no thumbnail yet");
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/4/thumbnail", "").await), "python", "512 bytes or fewer does not count");
}

#[tokio::test]
async fn the_image_is_sent_inline_once_its_cache_path_is_recorded() {
    let d = door(|_, _| {}).await;
    let r = get(d.addr, "/api/photos/-1001/1/image", "").await;
    assert_eq!((r.status, served(&r)), (200, "rust"));
    assert_eq!(r.body.len(), 5000);
    assert_eq!(r.headers["content-type"], "image/jpeg");
    assert_eq!(r.headers["content-disposition"], "inline");
    assert_eq!(r.headers["cache-control"], "private, max-age=3600");
    // no recorded path (Python records it), a type that is not an image type, a file that is not there: Python
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/2/image", "").await), "python");
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/3/image", "").await), "python");
    let d = door(|w, _| {
        std::fs::remove_file(w.photos().join("originals").join(format!("{}.bin", photo_key("-1001", 1)))).unwrap();
    })
    .await;
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/1/image", "").await), "python");
}

#[tokio::test]
async fn the_preview_is_sent_while_it_is_not_older_than_the_original() {
    let d = door(|_, _| {}).await;
    let r = get(d.addr, "/api/photos/-1001/1/preview", "").await;
    assert_eq!((r.status, served(&r)), (200, "rust"));
    assert_eq!(r.body.len(), 3000);
    assert_eq!((r.headers["cache-control"].as_str(), r.headers["content-disposition"].as_str()), ("private, max-age=86400", "inline"));
    // touch the original so that it is newer than the preview: Python renders a new one
    let original = d.world.photos().join("originals").join(format!("{}.bin", photo_key("-1001", 1)));
    std::thread::sleep(Duration::from_millis(30));
    std::fs::write(&original, vec![1u8; 5000]).unwrap();
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/1/preview", "").await), "python");
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/3/preview", "").await), "python", "no preview yet");
}

#[tokio::test]
async fn range_requests_unknown_photos_and_hidden_photos() {
    let d = door(|_, _| {}).await;
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/1/image", "Range: bytes=0-9\r\n").await), "python");
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/1/image", "If-Range: x\r\n").await), "python");
    let r = get(d.addr, "/api/photos/-1001/99/thumbnail", "").await;
    assert_eq!((r.status, String::from_utf8(r.body).unwrap().as_str()), (404, r#"{"detail":"Photo is not in the Archive X photo index"}"#));
    assert_eq!(served(&get(d.addr, "/api/photos/-1001/001/thumbnail", "").await), "python", "an odd spelling of the id");
    assert_eq!(served(&get(d.addr, "/api/photos/abc/1/thumbnail", "").await), "python");
    // in SFW the NSFW channel's photos exist but are hidden: the content guard says "Not found"
    let d = door(|_, c| {
        c.execute("UPDATE settings SET value='\"sfw\"' WHERE key='content_mode'", []).unwrap();
    })
    .await;
    let r = get(d.addr, "/api/photos/-1001/1/thumbnail", "").await;
    assert_eq!((r.status, served(&r)), (404, "rust"));
    assert_eq!(String::from_utf8(r.body).unwrap(), r#"{"detail":"Not found"}"#);
}

#[tokio::test]
async fn a_login_is_required() {
    let d = door(|_, _| {}).await;
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(b"GET /api/photos/-1001/1/thumbnail HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401"), "{raw}");
}
