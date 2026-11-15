//! `GET /api/library` (the Videos page), answered by the front door.

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
        native_routes: vec!["library_list".into(), "tags_list".into(), "studio_metadata".into()],
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

fn ids(v: &serde_json::Value) -> Vec<i64> {
    v["items"].as_array().unwrap().iter().map(|x| x["media_id"].as_i64().unwrap()).collect()
}

#[tokio::test]
async fn the_library_page_is_shaped_like_pythons() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/library", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    assert_eq!(keys(&v), ["total", "ready_count", "page", "per", "items", "catalogued_only", "published_only", "snapshot_ready_only"]);
    assert_eq!((v["total"].as_i64(), v["page"].as_i64(), v["per"].as_i64()), (Some(4), Some(0), Some(48)));
    assert_eq!(ids(&v), [1, 2, 3, 4], "newest first");
    let first = &v["items"][0];
    assert_eq!((first["id"].as_i64(), first["name"].as_str(), first["studio"].as_str()), (Some(11), Some("Alpha title"), Some("Studio One")));
    assert_eq!((first["_cid"].as_str(), first["thumbnail_id"].as_i64(), first["channel_title"].as_str()), (Some("-1001"), Some(111), Some("Main")));
    assert_eq!(first["date"], "2025-03-01");
    assert_eq!(first["local_performers"], serde_json::json!(["Mia"]));
    assert_eq!(v["items"][2]["name"], "video 13");
}

#[tokio::test]
async fn paging_sorting_and_filters() {
    let d = door(|_| {}).await;
    let v = json(&get(d.addr, "/api/library?per=2&page=1", "").await);
    assert_eq!((v["total"].as_i64(), ids(&v)), (Some(4), vec![3, 4]));
    let v = json(&get(d.addr, "/api/library?sort=date_asc", "").await);
    assert_eq!(ids(&v), [4, 3, 2, 1]);
    let v = json(&get(d.addr, "/api/library?channels=-1002", "").await);
    assert_eq!(ids(&v), [3]);
    let v = json(&get(d.addr, "/api/library?studio=two", "").await);
    assert_eq!(ids(&v), [3]);
    let v = json(&get(d.addr, "/api/library?performer=mia", "").await);
    assert_eq!(ids(&v), [1, 2], "performers and local performers both count");
    let v = json(&get(d.addr, "/api/library?tag=tag-b", "").await);
    assert_eq!(ids(&v), [1]);
    let v = json(&get(d.addr, "/api/library?date_from=2025-02-01&date_to=2025-02-28", "").await);
    assert_eq!(ids(&v), [2]);
    let v = json(&get(d.addr, "/api/library?message_id=13", "").await);
    assert_eq!(ids(&v), [3]);
    let v = json(&get(d.addr, "/api/library?per=0&page=-5", "").await);
    assert_eq!((v["per"].as_i64(), v["page"].as_i64()), (Some(1), Some(0)));
}

#[tokio::test]
async fn searches_and_odd_numbers_go_to_python() {
    let d = door(|_| {}).await;
    for q in ["q=alpha", "per=abc", "page=1.5", "studio=%C3%A9"] {
        let r = get(d.addr, &format!("/api/library?{q}"), "").await;
        assert_eq!(json(&r)["from"], "python", "{q}");
    }
}

#[tokio::test]
async fn tags_are_listed_by_use_and_a_search_goes_to_python() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/tags", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    let names: Vec<&str> = v.as_array().unwrap().iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["tag-a", "tag-b"], "tag-a is on four videos, tag-b on one");
    assert_eq!((v[0]["video_count"].as_i64(), v[1]["video_count"].as_i64()), (Some(4), Some(1)));
    let brief = json(&get(d.addr, "/api/tags?brief=1&limit=320", "").await);
    assert_eq!(keys(&brief[0]), ["id", "name", "video_count"]);
    assert_eq!(json(&get(d.addr, "/api/tags?q=tag", "").await)["from"], "python");
}

#[tokio::test]
async fn a_studios_metadata_has_the_row_and_its_providers() {
    let d = door(|c| {
        c.execute_batch(
            "CREATE TABLE IF NOT EXISTS entity_metadata (entity_type TEXT, entity_id INTEGER, provider TEXT, provider_id TEXT, normalized TEXT, fetched_at TEXT, updated_at TEXT);",
        )
        .unwrap();
        c.execute("INSERT INTO entity_metadata VALUES ('studio',1,'tpdb','t1','{}','f','u'),('studio',1,'stashdb','s1','{}','f','u')", []).unwrap();
    })
    .await;
    let r = get(d.addr, "/api/studios/1/metadata", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    assert_eq!(keys(&v), ["entity", "providers"]);
    assert_eq!(v["entity"]["name"], "Studio One");
    let p: Vec<&str> = v["providers"].as_array().unwrap().iter().map(|x| x["provider"].as_str().unwrap()).collect();
    assert_eq!(p, ["stashdb", "tpdb"], "ordered by provider");
    let missing = get(d.addr, "/api/studios/99/metadata", "").await;
    assert_eq!((missing.status, json(&missing)["detail"].as_str().map(String::from)), (404, Some("Studio not found".into())));
}
