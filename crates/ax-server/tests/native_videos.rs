//! The video lists of a channel, a studio and a tag, answered by the front door.

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

fn database(extra: impl FnOnce(&Connection)) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in [
        "web_sessions",
        "performers",
        "media",
        "channels",
        "video_performers",
        "local_video_performers",
        "studios",
        "video_studios",
        "tags",
        "video_tags",
        "entity_favorites",
        "studio_image_cache",
    ] {
        create_table(&c, &snapshot, t);
    }
    // the rows these tests make are adult and in the library; the app itself opens in SFW
    create_table(&c, &snapshot, "settings");
    c.execute("INSERT INTO settings VALUES ('content_mode','\"nsfw\"')", []).unwrap();
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    // the library views are views in the real database
    c.execute_batch("CREATE VIEW archive_x_library_media AS SELECT m.*, 1 AS library_ready FROM media m; CREATE VIEW archive_x_published_media AS SELECT m.* FROM media m;")
        .unwrap();
    c.execute("INSERT INTO channels(id,telegram_id,title) VALUES (1,'-1001','Main')", []).unwrap();
    //  1: title and file name, thumbnail, newest      2: empty title, no thumbnail      3: channel without a row, no title and no file name
    //  4: deleted at the source (the studio list hides it, the others do not)
    for (id, ch, msg, filename, title, date, status, thumb) in [
        (1, "-1001", 11, "a.mp4", Some("Alpha title"), "2025-03-01", "ACTIVE", Some(111)),
        (2, "-1001", 12, "b.mp4", Some(""), "2025-02-01", "ACTIVE", None),
        (3, "-1002", 13, "", None, "2025-01-01", "ACTIVE", None),
        (4, "-1001", 14, "d.mp4", Some("Deleted"), "2024-12-01", "SOURCE_DELETED", Some(0)),
    ] {
        c.execute(
            "INSERT INTO media(id,source_channel,source_message_id,filename,title,upload_date,status,thumbnail_message_id) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
            (id, ch, msg, filename, title, date, status, thumb),
        )
        .unwrap();
    }
    c.execute("UPDATE media SET site_url='  https://site/1  ', updated_at='2025-03-02' WHERE id=1", []).unwrap();
    c.execute("INSERT INTO performers(id,name) VALUES (1,'Ava'),(2,'Mia')", []).unwrap();
    // video 1: Ava and Mia, and Mia again as a local performer (listed once); video 2: only a local performer
    c.execute("INSERT INTO video_performers(performer_id,video_id) VALUES (1,1),(2,1)", []).unwrap();
    c.execute("INSERT INTO local_video_performers(performer_id,video_id) VALUES (2,1),(2,2)", []).unwrap();
    c.execute("INSERT INTO studios(id,name) VALUES (1,'Studio One'),(2,'Studio Two')", []).unwrap();
    c.execute("INSERT INTO video_studios(video_id,studio_id) VALUES (1,1),(2,1),(4,1),(3,2)", []).unwrap();
    c.execute("INSERT INTO tags(id,name) VALUES (1,'tag-a'),(2,'tag-b')", []).unwrap();
    c.execute("INSERT INTO video_tags(video_id,tag_id) VALUES (1,1),(2,1),(3,1),(4,1),(1,2)", []).unwrap();
    c.execute("INSERT INTO entity_favorites(entity_type,entity_id) VALUES ('studio',1)", []).unwrap();
    c.execute("INSERT INTO studio_image_cache(studio_id,updated_at) VALUES (1,'2025-03-05')", []).unwrap();
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
        native_routes: vec!["channel_videos".into(), "studio_videos".into(), "tag_videos".into()],
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

fn keys(v: &serde_json::Value) -> Vec<&str> {
    v.as_object().unwrap().keys().map(String::as_str).collect()
}

fn names(v: &serde_json::Value) -> Vec<String> {
    v["videos"].as_array().unwrap().iter().map(|x| x["name"].as_str().unwrap().to_string()).collect()
}

#[tokio::test]
async fn the_channel_list_is_shaped_like_pythons() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/channels/-1001/videos", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    assert_eq!(keys(&v), ["channel_id", "title", "count", "ready_count", "videos", "catalogued_only", "published_only"]);
    assert_eq!(
        (v["channel_id"].as_i64(), v["title"].as_str(), v["count"].as_i64(), v["ready_count"].as_i64()),
        (Some(-1001), Some("Main"), Some(3), Some(3))
    );
    assert_eq!((v["catalogued_only"].as_bool(), v["published_only"].as_bool()), (Some(false), Some(true)));
    // newest first; this list also holds the video deleted at the source
    assert_eq!(names(&v), ["Alpha title", "b.mp4", "Deleted"], "an empty title falls back to the file name");
    let first = &v["videos"][0];
    assert_eq!(first["id"], 11, "id is the Telegram message id");
    assert_eq!(first["_cid"], "-1001");
    assert_eq!(first["channel_id"], "-1001", "as a string, as in Python");
    assert_eq!(first["telegram_filename"], "a.mp4");
    assert_eq!(first["thumbnail_id"], 111);
    assert_eq!(v["videos"][1]["thumbnail_id"], 12, "no thumbnail message: the video's own message id");
    assert_eq!(v["videos"][2]["thumbnail_id"], 14, "a thumbnail id of 0 counts as none, as in Python");
    assert_eq!(
        (first["library_ready"].as_i64(), first["library_state"].as_str(), first["library_snapshot_state"].as_str()),
        (Some(1), Some("Ready"), Some("READY"))
    );
    let mut performers: Vec<&str> = first["performers"].as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect();
    performers.sort();
    assert_eq!(performers, ["Ava", "Mia"], "Mia is a performer and a local performer: listed once");
    assert_eq!(first["local_performers"], serde_json::json!(["Mia"]));
    assert_eq!(first["studios"], serde_json::json!(["Studio One"]));
    assert_eq!(first["studio"], "Studio One");
    let mut tags: Vec<&str> = first["tags"].as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect();
    tags.sort();
    assert_eq!(tags, ["tag-a", "tag-b"]);
    assert_eq!(v["videos"][1]["studio"], "Studio One");
    assert_eq!(v["videos"][1]["performers"], serde_json::json!(["Mia"]), "a local performer alone");
}

#[tokio::test]
async fn a_channel_without_a_row_is_unknown_but_listed_if_it_has_videos() {
    let d = door(|_| {}).await;
    let v = json(&get(d.addr, "/api/channels/-1002/videos", "").await);
    assert_eq!((v["title"].as_str(), v["count"].as_i64()), (Some("Unknown channel"), Some(1)));
    assert_eq!(names(&v), ["video 13"], "no title and no file name: 'video <message id>'");
    assert_eq!(v["videos"][0]["telegram_filename"], "");
    let r = get(d.addr, "/api/channels/-777/videos", "").await;
    assert_eq!((r.status, String::from_utf8(r.body).unwrap().as_str()), (404, r#"{"detail":"Channel not found"}"#));
    assert_eq!(r.headers["x-served-by"], "rust");
}

#[tokio::test]
async fn the_studio_list_has_the_site_and_hides_deleted_videos() {
    let d = door(|_| {}).await;
    let v = json(&get(d.addr, "/api/studios/1/videos", "").await);
    assert_eq!(keys(&v), ["site", "videos", "site_url"]);
    let site = &v["site"];
    let site_keys = keys(site);
    assert_eq!(&site_keys[site_keys.len() - 2..], ["image_cache_updated_at", "favorite"]);
    assert_eq!(
        (site["name"].as_str(), site["favorite"].as_bool(), site["image_cache_updated_at"].as_str()),
        (Some("Studio One"), Some(true), Some("2025-03-05"))
    );
    assert_eq!(v["site_url"], "https://site/1", "the newest non-blank site link, trimmed");
    assert_eq!(names(&v), ["Alpha title", "b.mp4"], "the video deleted at the source is not listed");
    let first = &v["videos"][0];
    assert_eq!((first["media_id"].as_i64(), first["id"].as_i64(), first["_cid"].as_str()), (Some(1), Some(11), Some("-1001")));
    assert_eq!(first["channel_title"], "Main");
    assert!(first["local_performers"].is_array());
    let other = json(&get(d.addr, "/api/studios/2/videos", "").await);
    assert_eq!(
        (other["site"]["favorite"].as_bool(), other["site"]["image_cache_updated_at"].is_null(), other["site_url"].as_str()),
        (Some(false), true, Some(""))
    );
    assert!(other["videos"][0]["channel_title"].is_null(), "a channel without a row has no title");
    let r = get(d.addr, "/api/studios/99/videos", "").await;
    assert_eq!((r.status, String::from_utf8(r.body).unwrap().as_str()), (404, r#"{"detail":"Site not found"}"#));
}

#[tokio::test]
async fn the_tag_list_keeps_its_own_shape() {
    let d = door(|_| {}).await;
    let v = json(&get(d.addr, "/api/tags/1/videos", "").await);
    assert_eq!(keys(&v), ["tag", "videos"]);
    assert_eq!(v["tag"]["name"], "tag-a");
    assert_eq!(names(&v), ["Alpha title", "b.mp4", "video 13", "Deleted"], "unlike the studio list this one also holds the deleted video");
    let first = &v["videos"][0];
    assert!(first["local_performers"].is_string(), "in this list local_performers stays the raw comma text, as in Python");
    assert_eq!(first["local_performers"], "Mia");
    assert!(!first.as_object().unwrap().contains_key("media_id"), "and there is no media_id");
    assert!(!first.as_object().unwrap().contains_key("channel_title"));
    assert_eq!(first["_cid"], "-1001");
    let r = get(d.addr, "/api/tags/99/videos", "").await;
    assert_eq!((r.status, String::from_utf8(r.body).unwrap().as_str()), (404, r#"{"detail":"Tag not found"}"#));
}

#[tokio::test]
async fn odd_ids_and_the_studio_performer_filter_are_left_to_python() {
    let d = door(|_| {}).await;
    for p in [
        "/api/tags/%2B1/videos",
        "/api/tags/%201/videos",
        "/api/tags/1.0/videos",
        "/api/tags/abc/videos",
        "/api/tags//videos",
        "/api/tags/1/videos/extra",
        "/api/studios/1/videos?performer_id=2",
        "/api/channels/9999999999999999999/videos",
    ] {
        assert_eq!(get(d.addr, p, "").await.headers["x-served-by"], "python", "{p}");
    }
    for p in ["/api/studios/1/videos?other=1", "/api/tags/1/videos?x=1", "/api/channels/-001001/videos"] {
        assert_eq!(get(d.addr, p, "").await.headers["x-served-by"], "rust", "{p}");
    }
}

#[tokio::test]
async fn a_login_is_required() {
    let d = door(|_| {}).await;
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(b"GET /api/tags/1/videos HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401"), "{raw}");
}
