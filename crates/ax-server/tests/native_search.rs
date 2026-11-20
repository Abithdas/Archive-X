//! GET /api/search answered by the front door: words are prefixes in the full-text index when there is one, plain LIKE text otherwise.

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
    // 1 "Alpha Sunrise" file a.mp4; 2 "Beta"; 3 "Évènement spécial"; 4 deleted at the source (still searchable: the library view shows it)
    for (id, filename, title, date) in [(1, "a.mp4", "Alpha Sunrise", "2025-03-01"), (2, "b.mp4", "Beta", "2025-02-01"), (3, "c.mp4", "Évènement spécial", "2025-01-01")] {
        c.execute(
            "INSERT INTO media(id,source_channel,source_message_id,filename,title,upload_date,status) VALUES (?1,'-1001',?2,?3,?4,?5,'ACTIVE')",
            (id, 10 + id, filename, title, date),
        )
        .unwrap();
    }
    c.execute("INSERT INTO performers(id,name,aliases,normalized_name) VALUES (1,'Ava Stone','Avy','ava stone'),(2,'Mia','','mia')", [])
        .unwrap();
    c.execute("INSERT INTO video_performers(performer_id,video_id) VALUES (1,1),(2,2)", []).unwrap();
    c.execute("INSERT INTO local_video_performers(performer_id,video_id) VALUES (1,3)", []).unwrap();
    c.execute("INSERT INTO studios(id,name) VALUES (1,'Northlight'),(2,'Unused')", []).unwrap();
    c.execute("INSERT INTO video_studios(video_id,studio_id) VALUES (1,1)", []).unwrap();
    c.execute("INSERT INTO tags(id,name) VALUES (1,'outdoor'),(2,'indoor')", []).unwrap();
    c.execute("INSERT INTO video_tags(video_id,tag_id) VALUES (1,1),(2,1)", []).unwrap();
    // the full-text index, as the app creates and fills it
    for ddl in [
        "CREATE VIRTUAL TABLE media_fts USING fts5(title, filename, caption, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')",
        "CREATE VIRTUAL TABLE performer_fts USING fts5(name, aliases, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')",
        "CREATE VIRTUAL TABLE tag_fts USING fts5(name, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')",
        "CREATE VIRTUAL TABLE studio_fts USING fts5(name, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')",
        "INSERT INTO media_fts(rowid,title,filename,caption) SELECT id,COALESCE(title,''),COALESCE(filename,''),COALESCE(caption,'') FROM media",
        "INSERT INTO performer_fts(rowid,name,aliases) SELECT id,COALESCE(name,''),COALESCE(aliases,'') FROM performers",
        "INSERT INTO tag_fts(rowid,name) SELECT id,name FROM tags",
        "INSERT INTO studio_fts(rowid,name) SELECT id,name FROM studios",
    ] {
        c.execute(ddl, []).unwrap();
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
        native_routes: vec!["search".into()],
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

fn names(v: &serde_json::Value, key: &str, field: &str) -> Vec<String> {
    v[key].as_array().unwrap().iter().map(|x| x[field].as_str().unwrap().to_string()).collect()
}

#[tokio::test]
async fn words_match_as_prefixes_and_every_kind_is_listed() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/search?q=Ava", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    assert_eq!(names(&v, "performers", "name"), ["Ava Stone"]);
    // videos 1 (performer) and 3 (the same performer, locally), newest first
    assert_eq!(names(&v, "videos", "title"), ["Alpha Sunrise", "Évènement spécial"]);
    assert_eq!(v["performers"][0]["video_count"], 2);
    let v = json(&get(d.addr, "/api/search?q=north", "").await);
    assert_eq!(names(&v, "studios", "name"), ["Northlight"]);
    assert_eq!(names(&v, "videos", "name"), ["Alpha Sunrise"], "found through its studio");
    let v = json(&get(d.addr, "/api/search?q=out", "").await);
    assert_eq!(names(&v, "tags", "name"), ["outdoor"], "a tag with videos; 'indoor' has none and does not match 'out'");
    assert_eq!(names(&v, "videos", "name"), ["Alpha Sunrise", "Beta"]);
}

#[tokio::test]
async fn a_video_in_the_list_has_the_search_shape() {
    let d = door(|_| {}).await;
    let v = json(&get(d.addr, "/api/search?q=Alpha", "").await);
    let video = &v["videos"][0];
    assert_eq!(video["id"], 11, "the message id, not the row id");
    assert_eq!((video["_cid"].as_str(), video["name"].as_str(), video["telegram_filename"].as_str()), (Some("-1001"), Some("Alpha Sunrise"), Some("a.mp4")));
    assert_eq!(video["performers"], serde_json::json!(["Ava Stone"]));
    assert_eq!((video["studio"].as_str(), video["thumbnail_id"].as_i64()), (Some("Northlight"), Some(11)), "no thumbnail message: the message id");
}

#[tokio::test]
async fn accents_are_folded_by_the_index_and_symbols_are_not_words() {
    let d = door(|_| {}).await;
    assert_eq!(names(&json(&get(d.addr, "/api/search?q=%C3%A9v%C3%A9nement", "").await), "videos", "title"), ["Évènement spécial"]);
    assert_eq!(names(&json(&get(d.addr, "/api/search?q=evenement", "").await), "videos", "title"), ["Évènement spécial"], "remove_diacritics");
    assert_eq!(names(&json(&get(d.addr, "/api/search?q=%22beta%22", "").await), "videos", "title"), ["Beta"], "quotes are not part of a word");
}

#[tokio::test]
async fn without_the_index_or_when_it_finds_no_video_the_plain_search_answers() {
    // the index finds nothing for a word in the middle of a title; LIKE does
    let d = door(|_| {}).await;
    assert_eq!(names(&json(&get(d.addr, "/api/search?q=unrise", "").await), "videos", "title"), ["Alpha Sunrise"]);
    let d = door(|c| {
        for t in ["media_fts", "performer_fts", "tag_fts", "studio_fts"] {
            c.execute(&format!("DROP TABLE {t}"), []).unwrap();
        }
    })
    .await;
    let v = json(&get(d.addr, "/api/search?q=north", "").await);
    assert_eq!(names(&v, "studios", "name"), ["Northlight"]);
    assert_eq!(names(&v, "videos", "name"), ["Alpha Sunrise"]);
}

#[tokio::test]
async fn an_empty_text_and_limits() {
    let d = door(|_| {}).await;
    let r = get(d.addr, "/api/search", "").await;
    assert_eq!(
        (r.headers["x-served-by"].as_str(), String::from_utf8(r.body).unwrap().as_str()),
        ("rust", r#"{"videos":[],"performers":[],"tags":[],"studios":[]}"#)
    );
    for p in ["/api/search?q=a&limit=20", "/api/search?q=a&limit=abc", "/api/search?q=a&limit=1000"] {
        assert_eq!(get(d.addr, p, "").await.headers["x-served-by"], "rust", "{p}");
    }
    for p in ["/api/search?q=a&limit=%2B20", "/api/search?q=a&limit=2_0", "/api/search?q=a&limit=-5", "/api/search?q=a&limit="] {
        assert_eq!(get(d.addr, p, "").await.headers["x-served-by"], "python", "{p}");
    }
}

#[tokio::test]
async fn a_login_is_required() {
    let d = door(|_| {}).await;
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(b"GET /api/search?q=a HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401"), "{raw}");
}
