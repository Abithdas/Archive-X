//! The performer and studio edit forms and the performer's provider data, answered by the front door.
//! still has to be tidied go to Python.

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
        "entity_metadata",
        "entity_refresh_state",
    ] {
        create_table(&c, &snapshot, t);
    }
    create_table(&c, &snapshot, "settings");
    c.execute("INSERT INTO settings VALUES ('content_mode','\"nsfw\"')", []).unwrap();
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    c.execute_batch("CREATE VIEW archive_x_library_media AS SELECT m.*, 1 AS library_ready FROM media m; CREATE VIEW archive_x_published_media AS SELECT m.* FROM media m;")
        .unwrap();
    c.execute("INSERT INTO channels(id,telegram_id,title) VALUES (1,'-1001','Main')", []).unwrap();
    //  1: has a title and a file name (the performer page shows the file name)  2: no file name, a title  3: no title, no file name
    //  4: deleted at the source (hidden)  5: another performer's
    for (id, ch, msg, filename, title, date, status, payload) in [
        (1, "-1001", 11, "a.mp4", "Alpha title", "2025-03-01", "ACTIVE", Some(r#"{"url":"https://prov/a","profile":"https://x"}"#)),
        (2, "-1001", 12, "", "Beta title", "2025-02-01", "ACTIVE", Some(r#"{"scene_url":"  http://prov/b  "}"#)),
        (3, "-1001", 13, "", "", "2025-01-01", "ACTIVE", Some("not json")),
        (4, "-1001", 14, "d.mp4", "Deleted", "2024-12-01", "SOURCE_DELETED", None),
        (5, "-1001", 15, "e.mp4", "Elsewhere", "2024-11-01", "ACTIVE", None),
    ] {
        c.execute(
            "INSERT INTO media(id,source_channel,source_message_id,filename,title,upload_date,status,provider_payload) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
            (id, ch, msg, filename, title, date, status, payload),
        )
        .unwrap();
    }
    c.execute("UPDATE media SET provider_url='https://already' WHERE id=3", []).unwrap();
    // 1 Ava, 3 'ava ' (the same person typed differently), 2 Mia, 4 Unknown Performer (already tidy: local only)
    c.execute("INSERT INTO performers(id,name,normalized_name) VALUES (1,'Ava','ava'),(2,'Mia','mia'),(3,'ava ','ava')", []).unwrap();
    c.execute("INSERT INTO performers(id,name,provider_ids,name_locked) VALUES (4,'Unknown Performer','{}',1)", []).unwrap();
    c.execute("INSERT INTO video_performers(performer_id,video_id) VALUES (1,1),(3,2),(2,3),(1,4),(2,5)", []).unwrap();
    c.execute("INSERT INTO local_video_performers(performer_id,video_id) VALUES (1,2),(3,3)", []).unwrap();
    c.execute("INSERT INTO studios(id,name) VALUES (1,'Studio One'),(2,'Studio Two')", []).unwrap();
    c.execute("INSERT INTO video_studios(video_id,studio_id) VALUES (1,1),(2,2)", []).unwrap();
    c.execute("INSERT INTO tags(id,name) VALUES (1,'tag-a')", []).unwrap();
    c.execute("INSERT INTO video_tags(video_id,tag_id) VALUES (1,1)", []).unwrap();
    c.execute("INSERT INTO entity_favorites(entity_type,entity_id) VALUES ('performer',1)", []).unwrap();
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
        native_routes: vec!["performer_edit".into(), "studio_edit".into(), "performer_metadata".into()],
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

fn text(r: &Reply) -> String {
    String::from_utf8(r.body.clone()).unwrap()
}

#[tokio::test]
async fn the_edit_forms_have_the_fields_python_selects() {
    let d = door(|c| {
        c.execute("UPDATE performers SET bio='Bio', gender='female', image_locked=1 WHERE id=1", []).unwrap();
    })
    .await;
    let r = get(d.addr, "/api/performers/1/edit", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let keys: Vec<String> = json(&r).as_object().unwrap().keys().cloned().collect();
    assert_eq!(keys, ["id", "name", "image", "profile", "bio", "gender", "provider_ids", "image_source", "image_locked", "name_locked", "profile_locked"]);
    assert_eq!(json(&r)["bio"], "Bio");
    assert_eq!(json(&r)["image_locked"], 1);
    let s = get(d.addr, "/api/studios/1/edit", "").await;
    let keys: Vec<String> = json(&s).as_object().unwrap().keys().cloned().collect();
    assert_eq!(keys, ["id", "name", "image", "provider_ids", "image_source", "image_locked", "name_locked"]);
    assert_eq!(json(&s)["name"], "Studio One");
}

#[tokio::test]
async fn a_missing_item_says_what_it_was_and_a_hidden_one_says_not_found() {
    let d = door(|_| {}).await;
    assert_eq!(text(&get(d.addr, "/api/performers/99/edit", "").await), r#"{"detail":"Performer not found"}"#);
    assert_eq!(text(&get(d.addr, "/api/studios/99/edit", "").await), r#"{"detail":"Studio/site not found"}"#);
    assert_eq!(text(&get(d.addr, "/api/performers/99/metadata", "").await), r#"{"detail":"Performer not found"}"#);
    // the app opens in SFW and the rows here are NSFW: they exist, but the content guard answers "Not found" first
    let d = door(|c| {
        c.execute("UPDATE settings SET value='\"sfw\"' WHERE key='content_mode'", []).unwrap();
    })
    .await;
    for p in ["/api/performers/1/edit", "/api/studios/1/edit", "/api/performers/1/metadata"] {
        let r = get(d.addr, p, "").await;
        assert_eq!((r.status, text(&r).as_str()), (404, r#"{"detail":"Not found"}"#), "{p}");
        assert_eq!(r.headers["x-served-by"], "rust", "{p}");
    }
    assert_eq!(text(&get(d.addr, "/api/performers/99/edit", "").await), r#"{"detail":"Performer not found"}"#, "a row that is not there at all");
}

#[tokio::test]
async fn the_metadata_page_lists_the_providers_in_order_and_none_for_the_unknown_performer() {
    let d = door(|c| {
        for (provider, pid) in [("tpdb", "t1"), ("stashdb", "s1")] {
            c.execute("INSERT INTO entity_metadata(entity_type,entity_id,provider,provider_id,normalized) VALUES ('performer',1,?1,?2,'{}')", (provider, pid))
                .unwrap();
        }
    })
    .await;
    let v = json(&get(d.addr, "/api/performers/1/metadata", "").await);
    assert_eq!(v["entity"]["name"], "Ava");
    let providers: Vec<&str> = v["providers"].as_array().unwrap().iter().map(|p| p["provider"].as_str().unwrap()).collect();
    assert_eq!(providers, ["stashdb", "tpdb"], "ordered by provider");
    let u = json(&get(d.addr, "/api/performers/4/metadata", "").await);
    assert_eq!(u["entity"]["name"], "Unknown Performer");
    assert_eq!(u["providers"], serde_json::json!([]));
}

#[tokio::test]
async fn the_metadata_page_waits_for_python_when_the_unknown_performer_needs_tidying() {
    let d = door(|c| {
        c.execute("UPDATE performers SET gender='female' WHERE id=4", []).unwrap();
    })
    .await;
    assert_eq!(get(d.addr, "/api/performers/1/metadata", "").await.headers["x-served-by"], "python");
    // the edit forms do not run that tidy-up in Python, so they are Rust's either way
    assert_eq!(get(d.addr, "/api/performers/1/edit", "").await.headers["x-served-by"], "rust");
}

#[tokio::test]
async fn a_login_is_required() {
    let d = door(|_| {}).await;
    for p in ["/api/performers/1/edit", "/api/studios/1/edit", "/api/performers/1/metadata"] {
        let mut s = TcpStream::connect(d.addr).await.unwrap();
        s.write_all(format!("GET {p} HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").as_bytes()).await.unwrap();
        let mut raw = String::new();
        s.read_to_string(&mut raw).await.unwrap();
        assert!(raw.starts_with("HTTP/1.1 401"), "{p}: {raw}");
    }
}
