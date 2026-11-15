//! Watch progress (the first table Rust writes) answered by the front door.

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
        "photo_library_items",
        "watch_history",
        "scene_markers",
        "provider_sync_state",
    ] {
        create_table(&c, &snapshot, t);
    }
    // the snapshot has columns only; the real table has media_id as its key
    c.execute_batch("CREATE UNIQUE INDEX watch_history_media ON watch_history(media_id)").unwrap();
    c.execute_batch("DROP TABLE scene_markers; CREATE TABLE scene_markers (id INTEGER PRIMARY KEY AUTOINCREMENT, media_id INTEGER, position REAL, title TEXT, note TEXT, tag TEXT, created_at TEXT, updated_at TEXT, source TEXT DEFAULT 'local', provider TEXT, provider_marker_id TEXT); CREATE UNIQUE INDEX psync ON provider_sync_state(provider,entity_type);").unwrap();
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
    door_with(extra, None).await
}

async fn door_with(extra: impl FnOnce(&Connection), launch_secret: Option<&str>) -> Door {
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
        native_routes: vec![
            "play_progress".into(),
            "play_history".into(),
            "entity_favorite_toggle".into(),
            "entity_favorite_status".into(),
            "markers_list".into(),
            "marker_create".into(),
            "marker_delete".into(),
            "name_list".into(),
        ],
        launch_secret: launch_secret.map(String::from),
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

async fn post(addr: SocketAddr, path: &str, body: &str, extra: &str) -> Reply {
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(
        format!(
            "POST {path} HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n{AUTH}{extra}\r\n{body}",
            body.len()
        )
        .as_bytes(),
    )
    .await
    .unwrap();
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

fn rows(path: &std::path::Path) -> Vec<(i64, f64, f64, i64, f64, i64)> {
    let c = Connection::open(path).unwrap();
    let mut st = c.prepare("SELECT media_id,position,duration,play_count,total_watch_seconds,completed FROM watch_history ORDER BY media_id").unwrap();
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?))).unwrap().map(Result::unwrap).collect()
}

fn db_path(d: &Door) -> std::path::PathBuf {
    d._keep[1].path().join("archive.db")
}

#[tokio::test]
async fn progress_is_saved_and_counted_like_pythons() {
    let d = door(|_| {}).await;
    let body = |pos: f64, dur: f64, delta: f64| format!(r#"{{"channel_id":"-1001","message_id":11,"position":{pos},"duration":{dur},"watch_delta":{delta}}}"#);
    let r = post(d.addr, "/api/play/progress", &body(30.0, 100.0, 5.0), "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    assert_eq!(json(&r), serde_json::json!({"ok": true, "stored": true, "completed": false}));
    assert_eq!(rows(&db_path(&d)), [(1, 30.0, 100.0, 1, 5.0, 0)], "first save: play count 1");
    // later saves add the watch time; a jump bigger than 20 s is a seek and adds nothing
    post(d.addr, "/api/play/progress", &body(40.0, 100.0, 5.0), "").await;
    post(d.addr, "/api/play/progress", &body(80.0, 100.0, 45.0), "").await;
    assert_eq!(rows(&db_path(&d)), [(1, 80.0, 100.0, 1, 10.0, 0)]);
    // near the end: completed; starting again from the top (position < 5 with watch time) is a new play
    let r = post(d.addr, "/api/play/progress", &body(95.0, 100.0, 2.0), "").await;
    assert_eq!(json(&r)["completed"], true);
    post(d.addr, "/api/play/progress", &body(1.0, 100.0, 2.0), "").await;
    assert_eq!(rows(&db_path(&d)), [(1, 1.0, 100.0, 2, 14.0, 0)]);
    // the history route reads it back
    let h = json(&get(d.addr, "/api/play/history/-1001/11", "").await);
    assert_eq!((h["position"].as_f64(), h["play_count"].as_i64(), h["media_id"].as_i64()), (Some(1.0), Some(2), Some(1)));
    let none = json(&get(d.addr, "/api/play/history/-1001/12", "").await);
    assert_eq!(none, serde_json::json!({"position": 0, "duration": 0, "play_count": 0, "total_watch_seconds": 0, "completed": false}));
}

#[tokio::test]
async fn an_unknown_video_is_not_stored() {
    let d = door(|_| {}).await;
    let r = post(d.addr, "/api/play/progress", r#"{"channel_id":-1001,"message_id":999,"position":1,"duration":2}"#, "").await;
    assert_eq!(json(&r), serde_json::json!({"ok": false, "stored": false}));
    assert!(rows(&db_path(&d)).is_empty());
}

#[tokio::test]
async fn odd_bodies_and_cross_site_posts() {
    let d = door(|_| {}).await;
    for body in ["not json", "[]", r#"{"message_id":11}"#, r#"{"channel_id":"-1001","message_id":"11"}"#, r#"{"channel_id":"-1001","message_id":11,"position":"5"}"#] {
        let r = post(d.addr, "/api/play/progress", body, "").await;
        assert_eq!(json(&r)["from"], "python", "{body}");
    }
    let blocked = post(d.addr, "/api/play/progress", r#"{"channel_id":"-1001","message_id":11}"#, "Origin: https://evil.example\r\n").await;
    assert_eq!(blocked.status, 403);
    assert!(rows(&db_path(&d)).is_empty());
    let anon = {
        let mut s = TcpStream::connect(d.addr).await.unwrap();
        let body = r#"{"channel_id":"-1001","message_id":11}"#;
        s.write_all(format!("POST /api/play/progress HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{body}", body.len()).as_bytes())
            .await
            .unwrap();
        let mut raw = String::new();
        s.read_to_string(&mut raw).await.unwrap();
        raw
    };
    assert!(anon.starts_with("HTTP/1.1 401"), "{anon}");
}

fn favourites(path: &std::path::Path) -> Vec<(String, i64)> {
    let c = Connection::open(path).unwrap();
    let mut st = c.prepare("SELECT entity_type,entity_id FROM entity_favorites ORDER BY entity_type,entity_id").unwrap();
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).unwrap().map(Result::unwrap).collect()
}

#[tokio::test]
async fn a_favourite_is_flipped_and_read_back() {
    let d = door(|_| {}).await;
    // the fixture already has studio 1 as a favourite
    assert_eq!(json(&get(d.addr, "/api/entities/studio/1/favorite", "").await), serde_json::json!({"favorite": true}));
    assert_eq!(json(&get(d.addr, "/api/entities/performer/1/favorite", "").await), serde_json::json!({"favorite": false}));
    let on = post(d.addr, "/api/entities/performer/1/favorite", "", "").await;
    assert_eq!((on.status, on.headers["x-served-by"].as_str()), (200, "rust"));
    assert_eq!(json(&on), serde_json::json!({"ok": true, "favorite": true, "entity_type": "performer", "entity_id": 1}));
    assert_eq!(favourites(&db_path(&d)), [("performer".to_string(), 1), ("studio".to_string(), 1)]);
    let off = post(d.addr, "/api/entities/studio/1/favorite", "", "").await;
    assert_eq!(json(&off)["favorite"], false);
    assert_eq!(favourites(&db_path(&d)), [("performer".to_string(), 1)]);
    let missing = post(d.addr, "/api/entities/performer/99/favorite", "", "").await;
    assert_eq!((missing.status, json(&missing)["detail"].as_str().map(String::from)), (404, Some("Entity not found".into())));
    for path in ["/api/entities/Performer/1/favorite", "/api/entities/tag/1/favorite", "/api/entities/performer/+1/favorite"] {
        assert_eq!(json(&post(d.addr, path, "", "").await)["from"], "python", "{path}");
    }
    let blocked = post(d.addr, "/api/entities/performer/2/favorite", "", "Origin: https://evil.example\r\n").await;
    assert_eq!(blocked.status, 403);
}

async fn delete(addr: SocketAddr, path: &str, extra: &str) -> Reply {
    // the helper above sends POST; a DELETE is the same shape
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(format!("DELETE {path} HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\n{AUTH}{extra}\r\n").as_bytes()).await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    let (head, body) = raw.split_once("\r\n\r\n").unwrap();
    let status = head.split(' ').nth(1).unwrap().parse().unwrap();
    let headers: HashMap<String, String> = head.lines().skip(1).filter_map(|l| l.split_once(": ")).map(|(k, v)| (k.to_ascii_lowercase(), v.to_string())).collect();
    Reply { status, headers, body: body.as_bytes().to_vec() }
}

#[tokio::test]
async fn markers_are_listed_created_and_deleted() {
    let d = door(|c| {
        c.execute("INSERT INTO scene_markers(media_id,position,title,source) VALUES (1,50,'late','local'),(1,10,'early','provider'),(2,5,'other','local')", [])
            .unwrap();
    })
    .await;
    let list = json(&get(d.addr, "/api/media/1/markers", "").await);
    let titles: Vec<&str> = list.as_array().unwrap().iter().map(|m| m["title"].as_str().unwrap()).collect();
    assert_eq!(titles, ["early", "late"], "by position");
    let r = post(d.addr, "/api/media/1/markers", r#"{"position": 20, "title": "  Hello  ", "note": null, "tag": "x"}"#, "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    assert_eq!(
        (v["marker"]["title"].as_str(), v["marker"]["note"].as_str(), v["marker"]["position"].as_f64(), v["marker"]["media_id"].as_i64()),
        (Some("Hello"), Some(""), Some(20.0), Some(1))
    );
    let id = v["marker"]["id"].as_i64().unwrap();
    assert_eq!(json(&get(d.addr, "/api/media/1/markers", "").await).as_array().unwrap().len(), 3);
    let long = format!(r#"{{"position":1,"title":"{}"}}"#, "é".repeat(200));
    let clipped = json(&post(d.addr, "/api/media/1/markers", &long, "").await);
    assert_eq!(clipped["marker"]["title"].as_str().unwrap().chars().count(), 160);
    let none = post(d.addr, "/api/media/99/markers", r#"{"position":1}"#, "").await;
    assert_eq!((none.status, json(&none)["detail"].as_str().map(String::from)), (404, Some("Media not found".into())));
    for bad in ["[]", r#"{"position":"3"}"#, r#"{"position":1,"title":5}"#, "{\"position\":1,\"title\":\"a\\u001cb\"}"] {
        assert_eq!(json(&post(d.addr, "/api/media/1/markers", bad, "").await)["from"], "python", "{bad}");
    }
    assert_eq!(json(&post(d.addr, "/api/media/1/markers/sync", "{}", "").await)["from"], "python");
    // deleting a provider marker leaves a note so the automatic repair does not bring it back; a local one does not
    let provider_id = list[0]["id"].as_i64().unwrap();
    let r = delete(d.addr, &format!("/api/media/1/markers/{provider_id}"), "").await;
    assert_eq!((r.status, json(&r)), (200, serde_json::json!({"ok": true})));
    delete(d.addr, &format!("/api/media/1/markers/{id}"), "").await;
    delete(d.addr, "/api/media/1/markers/12345", "").await;
    let c = Connection::open(db_path(&d)).unwrap();
    let state: Vec<(String, String, String)> = c
        .prepare("SELECT provider,entity_type,status FROM provider_sync_state")
        .unwrap()
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert_eq!(state, [("theporndb".to_string(), "scene_markers:1".to_string(), "USER_MODIFIED".to_string())]);
    let left: i64 = c.query_row("SELECT COUNT(*) FROM scene_markers WHERE media_id=1", [], |r| r.get(0)).unwrap();
    assert_eq!(left, 2, "late and the one that was too long");
    let blocked = delete(d.addr, "/api/media/1/markers/1", "Origin: https://evil.example\r\n").await;
    assert_eq!(blocked.status, 403);
}

#[tokio::test]
async fn with_a_launch_secret_only_the_apps_own_requests_are_answered_here() {
    let d = door_with(|_| {}, Some("launch-secret")).await;
    // no secret: not answered here, so Python (the fake one) answers
    let r = get(d.addr, "/api/play/history/-1001/11", "").await;
    assert_eq!(r.headers["x-served-by"], "python");
    let ok = get(d.addr, "/api/play/history/-1001/11", "x-archive-x-desktop: launch-secret\r\n").await;
    assert_eq!(ok.headers["x-served-by"], "rust");
    let wrong = post(d.addr, "/api/play/progress", r#"{"channel_id":"-1001","message_id":11}"#, "x-archive-x-desktop: nope\r\n").await;
    assert_eq!(wrong.headers["x-served-by"], "python");
    assert!(rows(&db_path(&d)).is_empty());
}

#[tokio::test]
async fn the_name_list_checks_the_session_in_the_database_when_it_is_known() {
    // this backend would call anybody signed in (it answers every path 200), so a 401 can only come from the sessions table
    let d = door(|_| {}).await;
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(b"GET /api/photos/faces/performers/name-list HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\n\r\n").await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401"), "{raw}");
    let signed_in = get(d.addr, "/api/photos/faces/performers/name-list", "").await;
    assert_ne!(signed_in.status, 401);
}
