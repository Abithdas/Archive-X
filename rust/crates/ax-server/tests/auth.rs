use ax_db::rusqlite::Connection;
use ax_db::sessions::token_hash;
use ax_db::Db;
use ax_server::auth::Auth;
use http_body_util::BodyExt;
use hyper::header::{HeaderValue, COOKIE};
use hyper::{HeaderMap, StatusCode};

fn setup(rows: &[(&str, &str, f64)]) -> (tempfile::TempDir, Auth) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("a.db");
    let c = Connection::open(&path).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    c.execute_batch("CREATE TABLE web_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);").unwrap();
    for (tok, user, exp) in rows {
        c.execute("INSERT INTO web_sessions VALUES (?1,?2,?3)", (token_hash(tok), user, exp)).unwrap();
    }
    (dir, Auth::new(Db::open_read_only(&path).unwrap()))
}

fn headers(cookies: &[&str]) -> HeaderMap {
    let mut h = HeaderMap::new();
    for c in cookies {
        h.append(COOKIE, HeaderValue::from_str(c).unwrap());
    }
    h
}

fn far() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() + 86_400.0
}

#[tokio::test]
async fn a_live_session_is_let_in() {
    let (_d, auth) = setup(&[("tok", "admin", far())]);
    assert_eq!(auth.require(&headers(&["session=tok"])).await.unwrap().username, "admin");
    // HTTP/2 splits the cookies over several headers
    assert_eq!(auth.require(&headers(&["theme=dark", "session=tok"])).await.unwrap().username, "admin");
}

#[tokio::test]
async fn everything_else_gets_the_same_401_python_gives() {
    let (_d, auth) = setup(&[("tok", "admin", far()), ("old", "admin", 1.0)]);
    for h in [headers(&[]), headers(&["session=nope"]), headers(&["session=old"]), headers(&["other=tok"])] {
        let resp = auth.require(&h).await.unwrap_err();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(resp.headers()["content-type"], "application/json");
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(&body[..], br#"{"detail":"Authentication required"}"#);
    }
}

#[tokio::test]
async fn an_unreadable_session_table_never_lets_anyone_in() {
    // a database without the table: the lookup fails
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("empty.db");
    Connection::open(&path).unwrap().execute_batch("CREATE TABLE other (x INTEGER);").unwrap();
    let auth = Auth::new(Db::open_read_only(&path).unwrap());
    let resp = auth.require(&headers(&["session=tok"])).await.unwrap_err();
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE, "an error is not a logout and not a pass");
}
