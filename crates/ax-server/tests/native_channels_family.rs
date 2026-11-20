//! The channel lists (management screens and the Library) and the studio family, answered by the front door.
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
        "telegram_document_locations",
        "telegram_document_registry",
        "telegram_channel_registry_state",
        "local_sources",
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
        native_routes: vec!["source_channels".into(), "studio_family".into(), "library_channels".into()],
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

fn titles(v: &serde_json::Value) -> Vec<&str> {
    v.as_array().unwrap().iter().map(|c| c["title"].as_str().unwrap()).collect()
}

#[tokio::test]
async fn the_management_list_hides_only_the_nsfw_library_channels_and_only_in_sfw() {
    let add = |c: &Connection| {
        // title, in_library, rating: the fixture's own "Main" channel is NSFW and in the library
        for (id, title, in_lib, rating) in [(10, "Alpha films", 1, "sfw"), (11, "Zed hidden from library", 0, "nsfw"), (12, "Beta", 1, "nsfw")] {
            c.execute(
                "INSERT INTO channels(id,telegram_id,title,in_library,content_rating,holds) VALUES (?1,?2,?3,?4,?5,'both')",
                (id, format!("-90{id}"), title, in_lib, rating),
            )
            .unwrap();
        }
    };
    let d = door(|c| {
        c.execute("UPDATE settings SET value='\"sfw\"' WHERE key='content_mode'", []).unwrap();
        add(c);
    })
    .await;
    let r = get(d.addr, "/api/source-channels", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    // SFW: the SFW channel and the NSFW one that is not in the library; the NSFW library channels (Main, Beta) are left out
    assert_eq!(titles(&json(&r)), ["Alpha films", "Zed hidden from library"]);
    let d = door(add).await;
    let all = json(&get(d.addr, "/api/source-channels", "").await);
    assert_eq!(titles(&all), ["Alpha films", "Beta", "Main", "Zed hidden from library"], "NSFW lists every channel, by title");
    assert!(all[0].as_object().unwrap().contains_key("content_rating"), "every column of the row");
}

fn library_data(c: &Connection) {
    // the fixture's channel "Main" (-1001) is NSFW and in the library
    c.execute("DELETE FROM media", []).unwrap();
    c.execute(
        "INSERT INTO channels(id,telegram_id,title,in_library,content_rating,holds) VALUES (20,'-2002','beta (sfw)',1,'sfw','both'),(21,'-2003','Alpha (sfw)',1,'sfw','both')",
        [],
    )
    .unwrap();
    c.execute(
        "INSERT INTO local_sources(id,path,recursive,excluded,created_at,last_status,last_error,photos,videos) VALUES (1,'/data/Films/My Folder/',1,'[]','x','OK','',0,1)",
        [],
    )
    .unwrap();
    for (id, channel) in [(1, "-1001"), (2, "-1001"), (3, "-2002"), (4, "-2003"), (5, "-2003"), (6, "-2003"), (7, "-1009"), (8, "-9000000000001"), (9, "-9000000000002")] {
        c.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES (?1,?2,?1,'f.mp4','t','ACTIVE')", (id, channel))
            .unwrap();
    }
    // the registry: -2003 has 3 present (2 prepared) and one moved away; -1009 (no channel row) 1 present; -1010 only history
    for (n, channel, state, prepared) in [
        (1, "-2003", "PRESENT", "PREPARED"),
        (2, "-2003", "PRESENT", "PREPARED"),
        (3, "-2003", "PRESENT", "PENDING"),
        (4, "-2003", "MOVED_BY_ARCHIVE_X", "PREPARED"),
        (5, "-1009", "PRESENT", "PREPARED"),
        (6, "-1010", "MOVED_PENDING_DELETE", "PREPARED"),
    ] {
        c.execute("INSERT INTO telegram_document_registry(registry_key,identity_kind,size_bytes,duration,width,height,companion_message_ids_json,known_state,prepared_state,prepared_json,created_at,updated_at) VALUES (?1,'d',1,1,1,1,'[]','K',?2,'{}','x','x')", (format!("k{n}"), prepared)).unwrap();
        c.execute(
            "INSERT INTO telegram_document_locations(channel_id,message_id,registry_key,location_state,last_seen_at) VALUES (?1,?2,?3,?4,'x')",
            (channel, 100 + n, format!("k{n}"), state),
        )
        .unwrap();
    }
    c.execute("INSERT INTO telegram_channel_registry_state(channel_id,high_water_mark,last_scan_at,last_scan_status,last_scan_detail,updated_at) VALUES ('-2003',77,'2025-01-01','OK','','x')", []).unwrap();
}

#[tokio::test]
async fn in_nsfw_the_list_has_nsfw_channels_orphans_and_local_folders_but_no_sfw_channel() {
    let d = door(library_data).await;
    let r = get(d.addr, "/api/channels", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json(&r);
    let rows: Vec<(&str, i64)> = v.as_array().unwrap().iter().map(|c| (c["title"].as_str().unwrap(), c["video_count"].as_i64().unwrap())).collect();
    // sorted by folded title; the local folder takes its folder's name; the folder that is gone and the SFW channels are not listed
    assert_eq!(rows, [("Main", 2), ("My Folder", 1), ("Unknown channel", 1)]);
    let main = &v[0];
    assert_eq!(
        (main["known_count"].as_i64(), main["prepared_count"].as_i64(), main["last_scan_status"].as_str()),
        (Some(2), Some(2), Some("NEVER")),
        "no registry: the video count, never scanned"
    );
    let folder = &v[1];
    assert_eq!((folder["source_only"].as_bool(), folder["id"].is_null(), folder["telegram_id"].as_str()), (Some(true), true, Some("-9000000000001")));
    let unnamed = &v[2];
    // -1009 has no channel row but one present, prepared registry entry
    assert_eq!((unnamed["unnamed"].as_bool(), unnamed["known_count"].as_i64(), unnamed["prepared_count"].as_i64()), (Some(true), Some(1), Some(1)));
    let all = v.as_array().unwrap();
    assert!(
        !all.iter().any(|c| c["telegram_id"] == "-1010" || c["telegram_id"] == "-2003"),
        "history only, and a channel hidden by the mode, are not channels here"
    );
}

#[tokio::test]
async fn in_sfw_only_the_sfw_channels_are_listed_with_their_registry_counts() {
    let d = door(|c| {
        library_data(c);
        c.execute("UPDATE settings SET value='\"sfw\"' WHERE key='content_mode'", []).unwrap();
    })
    .await;
    let v = json(&get(d.addr, "/api/channels", "").await);
    let titles: Vec<&str> = v.as_array().unwrap().iter().map(|c| c["title"].as_str().unwrap()).collect();
    assert_eq!(titles, ["Alpha (sfw)", "beta (sfw)"], "no NSFW channel, no unnamed channel, and the folder has no channel row to be shown by");
    let alpha = &v[0];
    assert_eq!(
        (alpha["known_count"].as_i64(), alpha["prepared_count"].as_i64(), alpha["pending_count"].as_i64(), alpha["removed_count"].as_i64()),
        (Some(3), Some(2), Some(1), Some(1))
    );
    assert_eq!((alpha["last_scan_status"].as_str(), alpha["high_water_mark"].as_i64()), (Some("OK"), Some(77)));
    let beta = &v[1];
    assert_eq!((beta["known_count"].as_i64(), beta["last_scan_status"].as_str()), (Some(1), Some("NEVER")));
}

fn family_data(c: &Connection) {
    c.execute("DELETE FROM studios", []).unwrap();
    for (id, name) in [(1, "Northlight"), (2, "Aurora"), (3, "Parent Co"), (4, "Zeta"), (5, "alpha")] {
        c.execute("INSERT INTO studios(id,name) VALUES (?1,?2)", (id, name)).unwrap();
    }
    // 1 has parent p1 (inside details); 2 has the same parent (top level); 3 is p1; 4 has the parent only by name; 5 repeats 1's own id
    for (id, provider, pid, normalized) in [
        (1, "tpdb", "t1", r#"{"id":"t1","details":{"parent":{"id":"p1","name":"Parent Co"}}}"#),
        (2, "tpdb", "t2", r#"{"id":"t2","parent":{"id":"p1"}}"#),
        (3, "tpdb", "p1", r#"{"id":"p1"}"#),
        (4, "TPDB ", "t4", r#"{"parent":"parent co"}"#),
        (5, "tpdb", "t1", "not json"),
    ] {
        c.execute(
            "INSERT INTO entity_metadata(entity_type,entity_id,provider,provider_id,normalized) VALUES ('studio',?1,?2,?3,?4)",
            (id, provider, pid, normalized),
        )
        .unwrap();
    }
}

#[tokio::test]
async fn parent_child_and_sister_labels_are_found_and_ordered() {
    let d = door(family_data).await;
    let r = get(d.addr, "/api/studios/1/family", "").await;
    assert_eq!((r.status, r.headers["x-served-by"].as_str()), (200, "rust"));
    let v = json_obj(&r);
    assert_eq!(v["studio_id"], 1);
    let related: Vec<(i64, &str)> = v["related"].as_array().unwrap().iter().map(|x| (x["id"].as_i64().unwrap(), x["relation"].as_str().unwrap())).collect();
    // 3 is the parent (its id is 1's parent id); 5 shares 1's own provider id (child label); 2 shares the parent id (sister); 4 has no parent
    // id, so its parent is compared by name instead: "parent co" folds to 1's "Parent Co" (sister). This is Python's answer on the same rows.
    assert_eq!(related, [(3, "Parent company"), (5, "Child label"), (2, "Sister label"), (4, "Sister label")]);
    assert_eq!(v["related"][0]["provider"], "tpdb");
    assert_eq!(v["related"][0]["name"], "Parent Co");
}

#[tokio::test]
async fn a_name_only_parent_matches_by_case_folded_name() {
    let d = door(|c| {
        family_data(c);
        c.execute("UPDATE entity_metadata SET normalized='{\"parent\":\"PARENT CO\"}' WHERE entity_id=1", []).unwrap();
        c.execute("UPDATE entity_metadata SET provider='tpdb' WHERE entity_id=4", []).unwrap();
    })
    .await;
    let v = json_obj(&get(d.addr, "/api/studios/1/family", "").await);
    let ids: Vec<i64> = v["related"].as_array().unwrap().iter().map(|x| x["id"].as_i64().unwrap()).collect();
    assert_eq!(ids, [5, 4], "child label first, then the sister found by name; 2 has a parent id but no name, so nothing to compare");
}

#[tokio::test]
async fn an_unknown_studio_a_hidden_one_and_one_without_provider_data() {
    let d = door(family_data).await;
    assert_eq!(String::from_utf8(get(d.addr, "/api/studios/99/family", "").await.body).unwrap(), r#"{"detail":"Studio not found"}"#);
    // a studio with no provider rows has no family
    assert_eq!(json_obj(&get(d.addr, "/api/studios/1/family", "").await)["studio_id"], 1);
    let d = door(|c| {
        family_data(c);
        c.execute("DELETE FROM entity_metadata WHERE entity_id=1", []).unwrap();
    })
    .await;
    assert_eq!(String::from_utf8(get(d.addr, "/api/studios/1/family", "").await.body).unwrap(), r#"{"studio_id":1,"related":[]}"#);
    let d = door(|c| {
        family_data(c);
        c.execute("UPDATE settings SET value='\"sfw\"' WHERE key='content_mode'", []).unwrap();
    })
    .await;
    assert_eq!(
        String::from_utf8(get(d.addr, "/api/studios/1/family", "").await.body).unwrap(),
        r#"{"detail":"Not found"}"#,
        "hidden by the mode: the content guard's answer"
    );
}

fn json_obj(r: &Reply) -> serde_json::Value {
    serde_json::from_slice(&r.body).unwrap()
}

#[tokio::test]
async fn a_login_is_required() {
    let d = door(|_| {}).await;
    for p in ["/api/source-channels", "/api/studios/1/family"] {
        let mut s = TcpStream::connect(d.addr).await.unwrap();
        s.write_all(format!("GET {p} HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n").as_bytes()).await.unwrap();
        let mut raw = String::new();
        s.read_to_string(&mut raw).await.unwrap();
        assert!(raw.starts_with("HTTP/1.1 401"), "{p}: {raw}");
    }
}
