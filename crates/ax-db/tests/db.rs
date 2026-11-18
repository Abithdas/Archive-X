use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use ax_db::sessions::{token_hash, Session};
use ax_db::{rusqlite::Connection, Db, REQUIRED_COLUMNS};

fn temp_db(wal: bool) -> (tempfile::TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("archive.db");
    let c = Connection::open(&path).unwrap();
    if wal {
        c.pragma_update(None, "journal_mode", "WAL").unwrap();
    }
    c.execute_batch("CREATE TABLE web_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);").unwrap();
    (dir, path)
}

/// The tables and columns the Rust code reads must exist in the schema the Python backend creates.
#[test]
fn everything_rust_reads_exists_in_the_python_schema_snapshot() {
    let snapshot: serde_json::Value = serde_json::from_str(include_str!("../schema/schema.json")).unwrap();
    for (table, columns) in REQUIRED_COLUMNS {
        let have: Vec<&str> = snapshot[table]
            .as_array()
            .unwrap_or_else(|| panic!("table {table} is not in the schema snapshot"))
            .iter()
            .map(|c| c["name"].as_str().unwrap())
            .collect();
        for col in *columns {
            assert!(have.contains(col), "{table}.{col} is not in the schema snapshot (columns: {have:?})");
        }
    }
}

#[tokio::test]
async fn the_connection_cannot_write() {
    let (_d, path) = temp_db(true);
    let db = Db::open_read_only(&path).unwrap();
    let err = db.read(|c| c.execute("INSERT INTO web_sessions VALUES ('h','u',1.0)", [])).await.unwrap_err();
    assert!(
        err.to_string().to_lowercase().contains("readonly") || err.to_string().to_lowercase().contains("read-only") || err.to_string().to_lowercase().contains("read only"),
        "{err}"
    );
    let n: i64 = db.read(|c| c.query_row("SELECT COUNT(*) FROM web_sessions", [], |r| r.get(0))).await.unwrap();
    assert_eq!(n, 0);
}

#[tokio::test]
async fn a_missing_database_is_an_error_and_is_not_created() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("nope.db");
    assert!(Db::open_read_only(&path).is_err());
    assert!(!path.exists(), "Rust must never create the database");
}

#[tokio::test]
async fn sessions_are_found_by_the_hash_of_the_cookie_and_expire() {
    let (_d, path) = temp_db(true);
    let w = Connection::open(&path).unwrap();
    let now = 1_000_000.0;
    for (tok, user, exp) in [("good-token", "admin", now + 100.0), ("old-token", "admin", now - 1.0), ("edge-token", "bob", now)] {
        w.execute("INSERT INTO web_sessions VALUES (?1, ?2, ?3)", (token_hash(tok), user, exp)).unwrap();
    }
    let db = Db::open_read_only(&path).unwrap();
    let s = |cookie: &'static str| {
        let db = db.clone();
        async move { db.session_for_cookie(cookie, now).await.unwrap() }
    };
    assert_eq!(s("session=good-token").await, Some(Session { username: "admin".into(), expires: now + 100.0 }));
    assert_eq!(s("theme=dark; session=good-token; x=1").await.unwrap().username, "admin");
    assert_eq!(s("session=old-token").await, None, "expired");
    assert_eq!(s("session=edge-token").await.unwrap().username, "bob", "expiring exactly now is still valid, as in Python (expires < now is the test)");
    assert_eq!(s("session=unknown").await, None);
    assert_eq!(s("session=good-token-but-not").await, None, "a token that only starts like a real one");
    assert_eq!(s("other=good-token").await, None, "wrong cookie name");
    assert_eq!(s("").await, None);
    // the table stores hashes, never the token
    let raw: Vec<String> = db.read(|c| c.prepare("SELECT token_hash FROM web_sessions")?.query_map([], |r| r.get(0))?.collect()).await.unwrap();
    assert!(raw.iter().all(|h| h.len() == 64 && !h.contains("token")));
}

/// Rust reads while Python writes: many readers, a writer committing constantly, nobody gets "database is locked".
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn readers_are_not_blocked_by_a_writer_in_wal_mode() {
    let (_d, path) = temp_db(true);
    {
        let c = Connection::open(&path).unwrap();
        c.execute_batch("CREATE TABLE items (id INTEGER PRIMARY KEY, v TEXT);").unwrap();
    }
    let stop = Arc::new(AtomicBool::new(false));
    let writer = {
        let (stop, path) = (stop.clone(), path.clone());
        std::thread::spawn(move || {
            let c = Connection::open(&path).unwrap();
            c.busy_timeout(Duration::from_secs(5)).unwrap();
            let mut n = 0u64;
            while !stop.load(Ordering::Relaxed) {
                let tx = c.unchecked_transaction().unwrap();
                for _ in 0..50 {
                    tx.execute("INSERT INTO items(v) VALUES (?1)", [format!("row {n}")]).unwrap();
                    n += 1;
                }
                tx.commit().unwrap();
            }
            n
        })
    };
    let db = Db::open_read_only(&path).unwrap();
    let started = Instant::now();
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let db = db.clone();
        tasks.push(tokio::spawn(async move {
            let mut last = 0i64;
            let mut reads = 0u32;
            while started.elapsed() < Duration::from_millis(1500) {
                let n: i64 = db.read(|c| c.query_row("SELECT COUNT(*) FROM items", [], |r| r.get(0))).await.expect("a read failed while the writer was busy");
                assert!(n >= last, "a reader saw the row count go down: {last} then {n}");
                last = n;
                reads += 1;
            }
            (reads, last)
        }));
    }
    let mut total_reads = 0;
    let mut max_seen = 0;
    for t in tasks {
        let (r, last) = t.await.unwrap();
        total_reads += r;
        max_seen = max_seen.max(last);
    }
    stop.store(true, Ordering::Relaxed);
    let written = writer.join().unwrap();
    assert!(total_reads > 50, "only {total_reads} reads happened in 1.5 s");
    assert!(written > 100 && max_seen > 0, "the writer wrote {written} rows, readers saw up to {max_seen}");
    let final_n: i64 = db.read(|c| c.query_row("SELECT COUNT(*) FROM items", [], |r| r.get(0))).await.unwrap();
    assert_eq!(final_n as u64, written, "after the writer stopped, readers see every row");
}

/// Real Python session: sign in to the running backend, then
/// ARCHIVE_X_DB=<data>/database/archive.db ARCHIVE_X_COOKIE='session=<value>' cargo test -p ax-db -- --ignored real_python_session
#[tokio::test]
#[ignore]
async fn real_python_session_is_accepted_and_a_forged_one_is_not() {
    let path = std::env::var("ARCHIVE_X_DB").expect("set ARCHIVE_X_DB");
    let cookie = std::env::var("ARCHIVE_X_COOKIE").expect("set ARCHIVE_X_COOKIE to session=<value>");
    let db = Db::open_read_only(path).unwrap();
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    let s = db.session_for_cookie(&cookie, now).await.unwrap().expect("Python's own session cookie was not accepted");
    println!("accepted: user {:?}, expires in {:.1} days", s.username, (s.expires - now) / 86400.0);
    assert!(s.expires > now + 20.0 * 86400.0, "a fresh session should last about 30 days");
    let forged = format!("{}x", cookie);
    assert_eq!(db.session_for_cookie(&forged, now).await.unwrap(), None, "a changed cookie must not be accepted");
}

/// A COMMIT that fails (here: a reader holds a shared lock in rollback-journal mode, so the commit stays busy) must not leave the
/// writer's connection inside a transaction, or every later write would fail until the app restarted.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_failed_commit_does_not_poison_later_writes() {
    let (_dir, path) = temp_db(false);
    let setup = Connection::open(&path).unwrap();
    setup.execute_batch("CREATE TABLE t (n INTEGER); INSERT INTO t VALUES (1);").unwrap();

    let writer = ax_db::Writer::new(&path);
    let reader = Connection::open(&path).unwrap();
    reader.execute_batch("BEGIN").unwrap();
    let mut stmt = reader.prepare("SELECT n FROM t").unwrap();
    let mut rows = stmt.query([]).unwrap();
    assert!(rows.next().unwrap().is_some(), "the reader now holds a shared lock");

    let blocked = writer.write(|c| c.execute("INSERT INTO t VALUES (2)", []).map(|_| ())).await;
    assert!(blocked.is_err(), "the commit cannot finish while the reader holds its lock");

    drop(rows);
    drop(stmt);
    reader.execute_batch("COMMIT").unwrap();

    writer.write(|c| c.execute("INSERT INTO t VALUES (3)", []).map(|_| ())).await.expect("the next write works");
    let n: i64 = setup.query_row("SELECT count(*) FROM t", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 2, "the failed insert was rolled back, the later one kept");
}

/// The SFW / NSFW rule: reads see only the channels in the library that match the mode (default sfw), and the performers of that mode.
#[tokio::test]
async fn reads_follow_the_content_mode() {
    let (_d, path) = temp_db(true);
    {
        let c = Connection::open(&path).unwrap();
        c.execute_batch(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
             CREATE TABLE channels (telegram_id TEXT, title TEXT, in_library INTEGER NOT NULL DEFAULT 1, content_rating TEXT NOT NULL DEFAULT 'nsfw');
             CREATE TABLE media (id INTEGER PRIMARY KEY, source_channel TEXT, filename TEXT);
             CREATE TABLE performers (id INTEGER PRIMARY KEY, name TEXT, content_rating TEXT NOT NULL DEFAULT 'nsfw');
             INSERT INTO channels VALUES ('1','Adult',1,'nsfw'),('2','Movies',1,'sfw'),('3','Hidden',0,'nsfw');
             INSERT INTO media(source_channel,filename) VALUES ('1','a.mp4'),('2','b.mkv'),('3','c.mp4');
             INSERT INTO performers(name,content_rating) VALUES ('Adult Star','nsfw'),('Movie Actor','sfw');",
        )
        .unwrap();
    }
    let names = |db: Db| async move {
        db.read(|c| {
            let mut q = c.prepare("SELECT filename FROM media ORDER BY id")?;
            let rows = q.query_map([], |r| r.get::<_, String>(0))?.collect::<Result<Vec<_>, _>>()?;
            let mut p = c.prepare("SELECT name FROM performers")?;
            let who = p.query_map([], |r| r.get::<_, String>(0))?.collect::<Result<Vec<_>, _>>()?;
            Ok((rows, who))
        })
        .await
        .unwrap()
    };
    assert_eq!(names(Db::open_read_only(&path).unwrap()).await, (vec!["b.mkv".to_string()], vec!["Movie Actor".to_string()]), "sfw is the default");
    Connection::open(&path).unwrap().execute("INSERT INTO settings VALUES ('content_mode','\"nsfw\"')", []).unwrap();
    assert_eq!(
        names(Db::open_read_only(&path).unwrap()).await,
        (vec!["a.mp4".to_string()], vec!["Adult Star".to_string()]),
        "nsfw shows adult channels that are in the library"
    );
}
