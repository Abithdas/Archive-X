//! GET /api/photos/faces/groups?suggestions=0 answered by the front door; the full form (with suggestions) stays in Python.

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
    let cols: Vec<String> = snapshot[table].as_array().unwrap().iter().map(|col| format!("\"{}\" {}", col["name"].as_str().unwrap(), col["type"].as_str().unwrap())).collect();
    c.execute_batch(&format!("CREATE TABLE \"{table}\" ({});", cols.join(", "))).unwrap();
}

/// Six photos in two channels, five face clusters; clusters 3 and 4 are the same performer and are listed as one person.
fn database(extra: impl FnOnce(&Connection)) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../../ax-db/schema/schema.json")).unwrap();
    for t in ["web_sessions", "performers", "photo_library_items", "photo_face_groups", "photo_face_detections", "photo_face_analysis"] {
        create_table(&c, &snapshot, t);
    }
    let far = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0;
    c.execute("INSERT INTO web_sessions VALUES (?1,'admin',?2)", (token_hash("tok"), far)).unwrap();
    c.execute("INSERT INTO performers(id,name) VALUES (7,'Mia Rivera')", []).unwrap();
    // photos 1-4 in channel -1001, 5-6 in -1002
    for (id, ch) in [(1, "-1001"), (2, "-1001"), (3, "-1001"), (4, "-1001"), (5, "-1002"), (6, "-1002")] {
        c.execute("INSERT INTO photo_library_items(id,channel_id) VALUES (?1,?2)", (id, ch)).unwrap();
    }
    // g1: two photos, g2: one, g5: one in -1002 (all unnamed); g3 and g4 both belong to performer 7 and are shown as ONE person (id -7)
    for (id, label, perf) in [(1, "Face group 1", None), (2, "Face group 2", None), (3, "Alpha cluster", Some(7)), (4, "Face group 4", Some(7)), (5, "Face group 5", None)] {
        c.execute("INSERT INTO photo_face_groups(id,label,performer_id,updated_at) VALUES (?1,?2,?3,'2025-01-0'||?1)", (id, label, perf)).unwrap();
    }
    for (id, group, photo) in [(1, 1, 1), (2, 1, 2), (3, 2, 3), (4, 3, 4), (5, 4, 5), (6, 5, 6)] {
        c.execute("INSERT INTO photo_face_detections(id,group_id,photo_id) VALUES (?1,?2,?3)", (id, group, photo)).unwrap();
    }
    for (photo, status) in [(1, "COMPLETE"), (2, "COMPLETE"), (3, "COMPLETE"), (4, "COMPLETE"), (5, "FAILED")] {
        c.execute("INSERT INTO photo_face_analysis(photo_id,status) VALUES (?1,?2)", (photo, status)).unwrap();
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
        listen: addr, upstream, upstream_plain: true, dist: dist.path().to_path_buf(), cert: None, key: None, proxy_key: KEY.into(), print_ready: false,
        limits: Limits::default(), db: Some(dbpath), native_routes: vec!["face_groups_fast".into()], session_touch_interval: Duration::from_secs(300),
    });
    tokio::spawn(async move { let _ = server::serve(l, cfg, std::future::pending()).await; });
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
            if n == 0 { break; }
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

const FAST: &str = "/api/photos/faces/groups?suggestions=0";

#[tokio::test]
async fn people_are_merged_by_performer_counted_and_described_like_python() {
    let d = door(|_| {}).await;
    let r = get(d.addr, FAST, "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    assert_eq!(v.as_object().unwrap().keys().map(String::as_str).collect::<Vec<_>>(), ["ok", "groups", "photo_scopes", "analysis", "cosine_threshold", "grouping_mode"]);
    assert_eq!(v["cosine_threshold"], 0.42);
    assert_eq!(v["grouping_mode"], "image_embedding_cluster");
    let labels: Vec<&str> = v["groups"].as_array().unwrap().iter().map(|g| g["label"].as_str().unwrap()).collect();
    assert_eq!(labels, ["Face group 1", "Face group 2", "Face group 5", "Mia Rivera"], "ordered by name, ignoring case; a named person shows the performer's name");
    let first = &v["groups"][0];
    assert_eq!(
        first.as_object().unwrap().keys().map(String::as_str).collect::<Vec<_>>(),
        ["id", "label", "performer_id", "performer_name", "cluster_ids", "representative_detection_id", "photo_count", "face_count", "channel_ids", "updated_at", "detection_ids",
         "representative_url", "performer_image_url", "metadata_suggestions", "confirmed_assignment_suggestions", "profile_image_suggestions"],
        "the same keys in the same order as Python"
    );
    assert_eq!((first["id"].as_i64(), first["photo_count"].as_i64(), first["face_count"].as_i64()), (Some(1), Some(2), Some(2)));
    assert_eq!(first["cluster_ids"], serde_json::json!([1]));
    assert_eq!(first["detection_ids"], serde_json::json!([1, 2]));
    assert_eq!(first["representative_url"], "/api/photos/faces/crops/1");
    assert_eq!(first["performer_image_url"], "");
    assert!(first["performer_id"].is_null());
    assert_eq!(first["performer_name"], "");
    assert_eq!(first["metadata_suggestions"], serde_json::json!([]));
    let mia = &v["groups"][3];
    assert_eq!(mia["id"], -7, "a performer's clusters are one person with the negative performer id");
    assert_eq!(mia["performer_id"], 7);
    assert_eq!(mia["performer_name"], "Mia Rivera");
    assert_eq!((mia["photo_count"].as_i64(), mia["face_count"].as_i64()), (Some(2), Some(2)));
    let mut clusters: Vec<i64> = mia["cluster_ids"].as_array().unwrap().iter().map(|x| x.as_i64().unwrap()).collect();
    clusters.sort();
    assert_eq!(clusters, [3, 4]);
    assert_eq!(mia["performer_image_url"], "/api/performers/7/image");
    let mut channels: Vec<&str> = mia["channel_ids"].as_array().unwrap().iter().map(|x| x.as_str().unwrap()).collect();
    channels.sort();
    assert_eq!(channels, ["-1001", "-1002"]);
    assert_eq!(mia["updated_at"], "2025-01-04");
    assert_eq!(v["photo_scopes"], serde_json::json!({"named": 2, "unnamed": 4, "no_face": 0, "pending": 2}));
    assert_eq!(v["analysis"], serde_json::json!({"total_photos": 6, "analyzed_photos": 4, "failed_photos": 1}));
}

#[tokio::test]
async fn a_channel_list_narrows_the_people_and_the_totals_but_not_the_scope_counts() {
    let d = door(|_| {}).await;
    let v = json(&get(d.addr, &format!("{FAST}&channels=-1002"), "").await);
    let labels: Vec<&str> = v["groups"].as_array().unwrap().iter().map(|g| g["label"].as_str().unwrap()).collect();
    assert_eq!(labels, ["Face group 5", "Mia Rivera"]);
    assert_eq!(v["groups"][1]["photo_count"], 1, "only photo 5 of Mia's two photos is in this channel");
    assert_eq!(v["analysis"], serde_json::json!({"total_photos": 2, "analyzed_photos": 0, "failed_photos": 1}));
    assert_eq!(v["photo_scopes"]["unnamed"], 4, "the scope counts always cover the whole library, as in Python");
    // repeated, blank and padded names are tidied; an unknown channel gives no people
    let v = json(&get(d.addr, &format!("{FAST}&channels=-1001,%20-1001,,"), "").await);
    let labels: Vec<&str> = v["groups"].as_array().unwrap().iter().map(|g| g["label"].as_str().unwrap()).collect();
    assert_eq!(labels, ["Face group 1", "Face group 2", "Mia Rivera"], "channel -1001 has photos 1-4 only, so Face group 5 (photo 6) is not there");
    let v = json(&get(d.addr, &format!("{FAST}&channels=-9999"), "").await);
    assert_eq!((v["groups"].as_array().unwrap().len(), v["analysis"]["total_photos"].as_i64()), (0, Some(0)));
}

#[tokio::test]
async fn top_keeps_named_people_first_then_the_busiest() {
    let d = door(|_| {}).await;
    let names = |q: &'static str| {
        let addr = d.addr;
        async move { json(&get(addr, &format!("{FAST}{q}"), "").await)["groups"].as_array().unwrap().iter().map(|g| g["label"].as_str().unwrap().to_string()).collect::<Vec<_>>() }
    };
    assert_eq!(names("&top=1").await, ["Mia Rivera"]);
    assert_eq!(names("&top=2").await, ["Mia Rivera", "Face group 1"], "then the busiest unnamed group (2 photos)");
    assert_eq!(names("&top=3").await, ["Mia Rivera", "Face group 1", "Face group 2"], "ties keep the name order");
    assert_eq!(names("&top=99").await.len(), 4, "a cut larger than the list changes nothing");
    assert_eq!(names("&top=abc").await.len(), 4);
}

#[tokio::test]
async fn the_full_form_and_odd_parameters_are_left_to_python() {
    let d = door(|_| {}).await;
    for q in ["/api/photos/faces/groups", "/api/photos/faces/groups?suggestions=1", "/api/photos/faces/groups?top=3", "/api/photos/faces/groups?suggestions=00",
              "/api/photos/faces/groups?suggestions=0&top=-1", "/api/photos/faces/groups?suggestions=0&top=1_0", "/api/photos/faces/groups?suggestions=0&top=1&top=2"] {
        assert_eq!(get(d.addr, q, "").await.headers["x-served-by"], "python", "{q}");
    }
}

#[tokio::test]
async fn an_empty_library_and_a_missing_login() {
    let d = door(|c| { for t in ["photo_face_analysis", "photo_face_detections", "photo_face_groups", "photo_library_items"] { c.execute(&format!("DELETE FROM {t}"), []).unwrap(); } }).await;
    let v = json(&get(d.addr, FAST, "").await);
    assert_eq!(v["groups"], serde_json::json!([]));
    assert_eq!(v["analysis"], serde_json::json!({"total_photos": 0, "analyzed_photos": 0, "failed_photos": 0}), "SUM() of nothing is NULL in SQL and 0 in the answer");
    assert_eq!(v["photo_scopes"], serde_json::json!({"named": 0, "unnamed": 0, "no_face": 0, "pending": 0}));
    let mut s = TcpStream::connect(d.addr).await.unwrap();
    s.write_all(format!("GET {FAST} HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").as_bytes()).await.unwrap();
    let mut raw = String::new();
    s.read_to_string(&mut raw).await.unwrap();
    assert!(raw.starts_with("HTTP/1.1 401"), "{raw}");
}
