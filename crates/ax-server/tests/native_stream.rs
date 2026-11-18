//! Local-folder videos and finished playback copies sent by the front door itself (native_stream.rs).

use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::path::PathBuf;
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
type B = BoxBody<Bytes, Infallible>;
const SFW_FOLDER: &str = "-9000000000001";
const NSFW_FOLDER: &str = "-9000000000002";

fn create_table(c: &Connection, snapshot: &serde_json::Value, table: &str) {
    let cols: Vec<String> = snapshot[table]
        .as_array()
        .unwrap()
        .iter()
        .map(|col| format!("\"{}\" {}", col["name"].as_str().unwrap(), col["type"].as_str().unwrap()))
        .collect();
    c.execute_batch(&format!("CREATE TABLE \"{table}\" ({});", cols.join(", "))).unwrap();
}

struct Fixture {
    addr: SocketAddr,
    db: PathBuf,
    folders: PathBuf,
    _keep: Vec<tempfile::TempDir>,
}

/// 1000 bytes 0,1,2,…; the same content in both folders and as the finished copy of Telegram video -1002/1.
fn content() -> Vec<u8> {
    (0..1000u32).map(|i| (i % 251) as u8).collect()
}

async fn fixture() -> Fixture {
    let data = tempfile::tempdir().unwrap();
    let folders = data.path().join("folders");
    let assets = data.path().join("assets");
    for (sub, name) in [("sfw", "movie.mp4"), ("nsfw", "clip.mp4")] {
        std::fs::create_dir_all(folders.join(sub)).unwrap();
        std::fs::write(folders.join(sub).join(name), content()).unwrap();
    }
    std::fs::write(folders.join("outside.mp4"), b"not for you").unwrap();
    // a finished copy has to be over 1 KB to count (routes_playback.py)
    let copy = assets.join("-1002").join("1");
    std::fs::create_dir_all(&copy).unwrap();
    let mut big = content();
    big.extend(content());
    std::fs::write(copy.join("video.mp4"), &big).unwrap();

    let db = data.path().join("archive.db");
    let c = Connection::open(&db).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in ["web_sessions", "media", "channels", "settings", "local_sources", "local_video_files"] {
        create_table(&c, &snapshot, t);
    }
    c.execute("INSERT INTO settings VALUES ('content_mode','\"sfw\"')", []).unwrap();
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    for (id, ch, sub, name, rating) in [(1, SFW_FOLDER, "sfw", "movie.mp4", "sfw"), (2, NSFW_FOLDER, "nsfw", "clip.mp4", "nsfw")] {
        let root = folders.join(sub);
        c.execute("INSERT INTO local_sources(id,path,created_at) VALUES (?1,?2,'x')", (id, root.to_str().unwrap())).unwrap();
        c.execute("INSERT INTO channels(telegram_id,title,in_library,content_rating,holds) VALUES (?1,?2,1,?3,'both')", (ch, sub, rating)).unwrap();
        c.execute("INSERT INTO local_video_files(channel_id,message_id,path,size) VALUES (?1,1,?2,1000)", (ch, root.join(name).to_str().unwrap()))
            .unwrap();
        c.execute("INSERT INTO media(source_channel,source_message_id,filename) VALUES (?1,1,?2)", (ch, name)).unwrap();
    }
    // video 2 of the SFW folder points outside its folder (a row that went stale, or was tampered with)
    c.execute(
        "INSERT INTO local_video_files(channel_id,message_id,path,size) VALUES (?1,2,?2,11)",
        (SFW_FOLDER, folders.join("outside.mp4").to_str().unwrap()),
    )
    .unwrap();
    c.execute("INSERT INTO media(source_channel,source_message_id,filename) VALUES (?1,2,'outside.mp4')", [SFW_FOLDER]).unwrap();
    c.execute("INSERT INTO channels(telegram_id,title,in_library,content_rating,holds) VALUES ('-1002','Films',1,'sfw','both')", []).unwrap();
    c.execute("INSERT INTO media(source_channel,source_message_id,filename) VALUES ('-1002',1,'film.mp4'),('-1002',2,'film2.mp4')", []).unwrap();
    drop(c);

    let up = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let upstream = up.local_addr().unwrap();
    let asset_dir = assets.to_str().unwrap().to_string();
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = up.accept().await else { break };
            let asset_dir = asset_dir.clone();
            tokio::spawn(async move {
                let svc = service_fn(move |req: Request<Incoming>| {
                    let asset_dir = asset_dir.clone();
                    async move {
                        let body = if req.uri().path() == "/internal/provider-keys" {
                            serde_json::json!({ "playback_asset_dir": asset_dir }).to_string()
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
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "x").unwrap();
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
        db: Some(db.clone()),
        native_routes: vec!["local_stream".into(), "cached_stream".into()],
        launch_secret: None,
        session_touch_interval: Duration::from_secs(300),
    });
    tokio::spawn(async move {
        let _ = server::serve(l, cfg, std::future::pending()).await;
    });
    Fixture { addr, db, folders, _keep: vec![data, dist] }
}

struct Reply {
    status: u16,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

impl Reply {
    fn by(&self) -> &str {
        self.headers.get("x-served-by").map(String::as_str).unwrap_or("")
    }
}

async fn send(addr: SocketAddr, method: &str, path: &str, extra: &str) -> Reply {
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\nCookie: session=tok\r\n{extra}\r\n").as_bytes())
        .await
        .unwrap();
    let mut raw = Vec::new();
    s.read_to_end(&mut raw).await.unwrap();
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
    let head = String::from_utf8_lossy(&raw[..split]).to_string();
    let mut lines = head.lines();
    let status = lines.next().unwrap().split(' ').nth(1).unwrap().parse().unwrap();
    let headers = lines.filter_map(|l| l.split_once(": ")).map(|(k, v)| (k.to_ascii_lowercase(), v.to_string())).collect();
    Reply { status, headers, body: raw[split + 4..].to_vec() }
}

async fn get(addr: SocketAddr, path: &str, extra: &str) -> Reply {
    send(addr, "GET", path, extra).await
}

#[tokio::test]
async fn a_local_video_is_sent_from_disk_with_ranges() {
    let f = fixture().await;
    let path = format!("/stream/{SFW_FOLDER}/1");
    let whole = get(f.addr, &path, "").await;
    assert_eq!((whole.status, whole.by()), (200, "rust"));
    assert_eq!(whole.body, content());
    assert_eq!(whole.headers["content-type"], "video/mp4");
    assert_eq!(whole.headers["accept-ranges"], "bytes");
    assert_eq!(whole.headers["x-archive-x-playback-delivery"], "local-file");

    let part = get(f.addr, &path, "Range: bytes=10-19\r\n").await;
    assert_eq!((part.status, part.by()), (206, "rust"));
    assert_eq!(part.body, content()[10..20]);
    assert_eq!(part.headers["content-range"], "bytes 10-19/1000");

    let tail = get(f.addr, &path, "Range: bytes=-5\r\n").await;
    assert_eq!(tail.body, content()[995..]);

    let past = get(f.addr, &path, "Range: bytes=1000-\r\n").await;
    assert_eq!((past.status, past.by()), (416, "rust"));
    assert_eq!(past.headers["content-range"], "bytes */1000");

    let head = send(f.addr, "HEAD", &path, "").await;
    assert_eq!((head.status, head.by()), (200, "rust"));
    assert_eq!(head.headers["content-length"], "1000");
    assert!(head.body.is_empty());
}

#[tokio::test]
async fn the_other_modes_videos_are_not_there() {
    let f = fixture().await;
    let hidden = get(f.addr, &format!("/stream/{NSFW_FOLDER}/1"), "Range: bytes=0-9\r\n").await;
    assert_eq!((hidden.status, hidden.by()), (404, "rust"), "an NSFW folder's video in SFW");

    // the mode is read on every request: a switch takes effect at once
    Connection::open(&f.db).unwrap().execute("UPDATE settings SET value='\"nsfw\"' WHERE key='content_mode'", []).unwrap();
    let shown = get(f.addr, &format!("/stream/{NSFW_FOLDER}/1"), "Range: bytes=0-9\r\n").await;
    assert_eq!((shown.status, shown.by()), (206, "rust"));
    let now_hidden = get(f.addr, &format!("/stream/{SFW_FOLDER}/1"), "Range: bytes=0-9\r\n").await;
    assert_eq!((now_hidden.status, now_hidden.by()), (404, "rust"));
    let copy_hidden = get(f.addr, "/stream-cached/-1002/1", "").await;
    assert_eq!((copy_hidden.status, copy_hidden.by()), (404, "rust"));
}

#[tokio::test]
async fn a_file_outside_its_folder_is_left_to_python() {
    let f = fixture().await;
    let r = get(f.addr, &format!("/stream/{SFW_FOLDER}/2"), "").await;
    assert_eq!(r.by(), "python");
    assert!(f.folders.join("outside.mp4").exists());
}

#[tokio::test]
async fn what_this_side_does_not_read_goes_to_python() {
    let f = fixture().await;
    let path = format!("/stream/{SFW_FOLDER}/1");
    assert_eq!(get(f.addr, &path, "Range: bytes=0-1,5-6\r\n").await.by(), "python", "several ranges");
    assert_eq!(get(f.addr, &path, "Range: bytes=0-9\r\nIf-Range: \"x\"\r\n").await.by(), "python", "a conditional range");
    assert_eq!(get(f.addr, &format!("/stream/{SFW_FOLDER}/99"), "").await.by(), "python", "a video the folder does not have");
    assert_eq!(get(f.addr, "/stream/-1002/1", "").await.by(), "python", "a Telegram channel needs the playback engine");
    assert_eq!(get(f.addr, "/stream-cached/-1002/2", "").await.by(), "python", "no finished copy yet");
}

#[tokio::test]
async fn a_finished_copy_is_sent_from_disk() {
    let f = fixture().await;
    let r = get(f.addr, "/stream-cached/-1002/1", "Range: bytes=1000-1009\r\n").await;
    assert_eq!((r.status, r.by()), (206, "rust"));
    assert_eq!(r.body, content()[..10]);
    assert_eq!(r.headers["content-range"], "bytes 1000-1009/2000");
    assert_eq!(r.headers["x-playback-cache"], "1");
}

#[tokio::test]
async fn signed_out_is_refused() {
    let f = fixture().await;
    let mut s = TcpStream::connect(f.addr).await.unwrap();
    s.write_all(format!("GET /stream/{SFW_FOLDER}/1 HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\n\r\n").as_bytes()).await.unwrap();
    let mut raw = Vec::new();
    s.read_to_end(&mut raw).await.unwrap();
    assert!(String::from_utf8_lossy(&raw).starts_with("HTTP/1.1 401"));
}
