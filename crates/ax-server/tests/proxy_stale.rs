//! The front door's own calls to the backend (login check, keys) must survive a backend that closes a kept connection while it is idle.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use ax_server::config::{Config, Limits};
use ax_server::proxy::Proxy;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

const KEY: &str = "0123456789abcdef0123456789abcdef";

/// Answers the first request on each connection, keeps the connection open, and on a SECOND request on the same connection reads it and drops the
/// connection without answering: a server whose idle timeout fired at the very moment the client reused the connection.
async fn flaky_keepalive() -> (std::net::SocketAddr, Arc<AtomicUsize>, Arc<AtomicUsize>) {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let served = Arc::new(AtomicUsize::new(0));
    let dropped = Arc::new(AtomicUsize::new(0));
    let (s2, d2) = (served.clone(), dropped.clone());
    tokio::spawn(async move {
        loop {
            let Ok((mut s, _)) = l.accept().await else { break };
            let (served, dropped) = (s2.clone(), d2.clone());
            tokio::spawn(async move {
                let mut requests = 0;
                let mut buf = vec![0u8; 4096];
                loop {
                    let mut got = Vec::new();
                    while !got.windows(4).any(|w| w == b"\r\n\r\n") {
                        let n = s.read(&mut buf).await.unwrap_or(0);
                        if n == 0 {
                            return;
                        }
                        got.extend_from_slice(&buf[..n]);
                    }
                    requests += 1;
                    if requests == 2 {
                        dropped.fetch_add(1, Ordering::SeqCst);
                        return; // the request was read, nothing is answered, the connection closes
                    }
                    served.fetch_add(1, Ordering::SeqCst);
                    let body = r#"{"ok":true}"#;
                    let _ = s.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}", body.len()).as_bytes()).await;
                    let _ = s.flush().await;
                }
            });
        }
    });
    (addr, served, dropped)
}

#[tokio::test]
async fn calls_keep_working_when_the_backend_closed_a_kept_connection() {
    let (upstream, served, dropped) = flaky_keepalive().await;
    let proxy = Proxy::new(&Config {
        listen: "127.0.0.1:0".parse().unwrap(),
        upstream,
        upstream_plain: true,
        dist: ".".into(),
        cert: None,
        key: None,
        proxy_key: KEY.into(),
        print_ready: false,
        limits: Limits::default(),
        db: None,
        native_routes: vec![],
        launch_secret: None,
        session_touch_interval: Duration::from_secs(300),
    });
    let peer = "127.0.0.1:5000".parse().unwrap();
    for round in 0..6 {
        assert_eq!(proxy.signed_in("session=ok", peer).await, Ok(true), "call {round}");
    }
    assert!(dropped.load(Ordering::SeqCst) >= 1, "the test did meet a connection that died under a reused request");
    assert_eq!(served.load(Ordering::SeqCst), 6, "every call was answered exactly once");
}
