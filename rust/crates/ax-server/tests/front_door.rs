//! Starts the front door in-process in front of a small fake backend and talks to it over real sockets.

use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use ax_server::{config::Config, server};
use bytes::Bytes;
use http_body_util::{combinators::BoxBody, BodyExt, Full, StreamBody};
use hyper::body::{Frame, Incoming};
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio_stream::wrappers::ReceiverStream;

const KEY: &str = "0123456789abcdef0123456789abcdef";
type B = BoxBody<Bytes, Infallible>;

fn full(s: impl Into<Bytes>) -> B {
    Full::new(s.into()).boxed()
}

/// The fake backend: /echo mirrors what it received, /stream sends three chunks 200 ms apart, /ws echoes after a 101.
async fn backend_handler(mut req: Request<Incoming>) -> Result<Response<B>, Infallible> {
    match req.uri().path() {
        "/api/echo" => {
            let mut lines = vec![format!("{} {}", req.method(), req.uri())];
            let mut hs: Vec<_> = req.headers().iter().map(|(k, v)| format!("{}: {}", k, v.to_str().unwrap_or("?"))).collect();
            hs.sort();
            lines.extend(hs);
            let body = req.into_body().collect().await.map(|b| b.to_bytes()).unwrap_or_default();
            lines.push(format!("BODY {}", String::from_utf8_lossy(&body)));
            Ok(Response::new(full(lines.join("\n"))))
        }
        "/stream" => {
            let (tx, rx) = tokio::sync::mpsc::channel(4);
            tokio::spawn(async move {
                for c in ["one;", "two;", "three;"] {
                    let _ = tx.send(Ok::<_, Infallible>(Frame::data(Bytes::from(c)))).await;
                    tokio::time::sleep(Duration::from_millis(200)).await;
                }
            });
            Ok(Response::new(StreamBody::new(ReceiverStream::new(rx)).boxed()))
        }
        "/ws" => {
            let upgrade = hyper::upgrade::on(&mut req);
            tokio::spawn(async move {
                if let Ok(up) = upgrade.await {
                    let mut io = TokioIo::new(up);
                    let mut buf = [0u8; 64];
                    while let Ok(n) = io.read(&mut buf).await {
                        if n == 0 { break; }
                        let mut out = b"echo:".to_vec();
                        out.extend_from_slice(&buf[..n]);
                        if io.write_all(&out).await.is_err() { break; }
                    }
                }
            });
            Ok(Response::builder().status(StatusCode::SWITCHING_PROTOCOLS).header("upgrade", "websocket").header("connection", "Upgrade").body(full("")).unwrap())
        }
        _ => Ok(Response::builder().status(404).header("alt-svc", "h3=\":8443\"").body(full(r#"{"detail":"backend 404"}"#)).unwrap()),
    }
}

async fn start_backend() -> SocketAddr {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = l.accept().await else { break };
            tokio::spawn(async move {
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(s), service_fn(backend_handler)).with_upgrades().await;
            });
        }
    });
    addr
}

struct Door {
    addr: SocketAddr,
    _dist: tempfile::TempDir,
}

async fn start_door(upstream: SocketAddr) -> Door {
    let dist = tempfile::tempdir().unwrap();
    std::fs::write(dist.path().join("index.html"), "<html>app shell</html>").unwrap();
    std::fs::create_dir(dist.path().join("assets")).unwrap();
    std::fs::write(dist.path().join("assets/app-1.js"), "console.log('hello');\n".repeat(200)).unwrap();
    std::fs::write(dist.path().join("sw.js"), "// sw").unwrap();
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    let cfg = Arc::new(Config { listen: addr, upstream, upstream_plain: true, dist: dist.path().to_path_buf(), cert: None, key: None, proxy_key: KEY.into(), print_ready: false });
    tokio::spawn(async move { let _ = server::serve(l, cfg, std::future::pending()).await; });
    Door { addr, _dist: dist }
}

struct Reply {
    status: u16,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

/// One raw HTTP/1.1 exchange (Connection: close, so the answer ends when the socket does).
async fn http(addr: SocketAddr, request: &str) -> Reply {
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(request.as_bytes()).await.unwrap();
    let mut raw = Vec::new();
    s.read_to_end(&mut raw).await.unwrap();
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n").expect("no header end");
    let head = String::from_utf8_lossy(&raw[..split]).to_string();
    let mut lines = head.lines();
    let status = lines.next().unwrap().split(' ').nth(1).unwrap().parse().unwrap();
    let headers = lines.filter_map(|l| l.split_once(": ")).map(|(k, v)| (k.to_ascii_lowercase(), v.to_string())).collect();
    let mut body = raw[split + 4..].to_vec();
    if headers_get(&headers, "transfer-encoding") == Some("chunked") {
        body = dechunk(&body);
    }
    Reply { status, headers, body }
}

fn headers_get<'a>(h: &'a HashMap<String, String>, k: &str) -> Option<&'a str> {
    h.get(k).map(|s| s.as_str())
}

fn dechunk(mut b: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    while let Some(p) = b.windows(2).position(|w| w == b"\r\n") {
        let n = usize::from_str_radix(std::str::from_utf8(&b[..p]).unwrap().trim(), 16).unwrap_or(0);
        if n == 0 { break; }
        out.extend_from_slice(&b[p + 2..p + 2 + n]);
        b = &b[p + 2 + n + 2..];
    }
    out
}

fn get(path: &str, extra: &str) -> String {
    format!("GET {path} HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\n{extra}\r\n")
}

#[tokio::test]
async fn forged_address_headers_are_dropped_and_the_real_peer_is_signed() {
    let door = start_door(start_backend().await).await;
    let r = http(door.addr, &get("/api/echo?x=1", "X-Forwarded-For: 6.6.6.6\r\nX-Real-IP: 6.6.6.6\r\nX-Archive-X-Peer: 8.8.8.8\r\nX-Archive-X-Proxy-Key: forged\r\nForwarded: for=6.6.6.6\r\n")).await;
    let body = String::from_utf8(r.body).unwrap();
    assert_eq!(r.status, 200, "{body}");
    assert!(body.starts_with("GET /api/echo?x=1"), "{body}");
    assert!(body.contains("host: archive.test"), "the Host header must pass through unchanged\n{body}");
    assert!(body.contains("x-archive-x-peer: 127.0.0.1"), "{body}");
    assert!(body.contains(&format!("x-archive-x-proxy-key: {KEY}")), "{body}");
    for gone in ["x-forwarded-for", "x-real-ip", "forwarded:", "8.8.8.8", "6.6.6.6", "forged"] {
        assert!(!body.contains(gone), "{gone} reached the backend\n{body}");
    }
}

#[tokio::test]
async fn a_request_body_reaches_the_backend() {
    let door = start_door(start_backend().await).await;
    let req = "POST /api/echo HTTP/1.1\r\nHost: archive.test\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: 17\r\n\r\n{\"a\":\"hello wor\"}";
    let body = String::from_utf8(http(door.addr, req).await.body).unwrap();
    assert!(body.ends_with("BODY {\"a\":\"hello wor\"}"), "{body}");
}

#[tokio::test]
async fn a_streamed_answer_is_passed_on_as_it_arrives() {
    let door = start_door(start_backend().await).await;
    let mut s = TcpStream::connect(door.addr).await.unwrap();
    s.write_all(get("/stream", "").as_bytes()).await.unwrap();
    let start = Instant::now();
    let mut seen = Vec::new();
    let mut first_chunk_at = None;
    let mut buf = [0u8; 512];
    loop {
        let n = s.read(&mut buf).await.unwrap();
        if n == 0 { break; }
        seen.extend_from_slice(&buf[..n]);
        if first_chunk_at.is_none() && String::from_utf8_lossy(&seen).contains("one;") {
            first_chunk_at = Some(start.elapsed());
        }
    }
    let total = start.elapsed();
    let text = String::from_utf8_lossy(&seen);
    assert!(text.contains("one;") && text.contains("two;") && text.contains("three;"), "{text}");
    assert!(first_chunk_at.unwrap() < Duration::from_millis(300), "the first chunk waited for the whole answer ({first_chunk_at:?})");
    assert!(total >= Duration::from_millis(400), "the backend sent everything at once? total {total:?}");
}

#[tokio::test]
async fn a_websocket_is_tunnelled_in_both_directions() {
    let door = start_door(start_backend().await).await;
    let mut s = TcpStream::connect(door.addr).await.unwrap();
    s.write_all(b"GET /ws HTTP/1.1\r\nHost: archive.test\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").await.unwrap();
    let mut buf = [0u8; 512];
    let n = tokio::time::timeout(Duration::from_secs(3), s.read(&mut buf)).await.expect("no handshake answer").unwrap();
    let head = String::from_utf8_lossy(&buf[..n]).to_string();
    assert!(head.starts_with("HTTP/1.1 101"), "{head}");
    s.write_all(b"hello").await.unwrap();
    let n = tokio::time::timeout(Duration::from_secs(3), s.read(&mut buf)).await.expect("no echo").unwrap();
    assert_eq!(&buf[..n], b"echo:hello");
}

#[tokio::test]
async fn the_web_app_is_served_here_and_the_rest_goes_to_the_backend() {
    let door = start_door(start_backend().await).await;
    // app shell for the app's own addresses, never cached
    for p in ["/", "/photos", "/watch/1/2", "/login"] {
        let r = http(door.addr, &get(p, "")).await;
        assert_eq!(r.status, 200, "{p}");
        assert_eq!(headers_get(&r.headers, "cache-control"), Some("no-store"), "{p}");
        assert!(String::from_utf8_lossy(&r.body).contains("app shell"));
        assert_eq!(headers_get(&r.headers, "x-content-type-options"), Some("nosniff"));
        assert!(r.headers.contains_key("content-security-policy"));
    }
    // hashed assets are cached for a year and compressed on request
    let r = http(door.addr, &get("/assets/app-1.js", "Accept-Encoding: gzip\r\n")).await;
    assert_eq!(r.status, 200);
    assert_eq!(headers_get(&r.headers, "cache-control"), Some("public, max-age=31536000, immutable"));
    assert_eq!(headers_get(&r.headers, "content-encoding"), Some("gzip"));
    assert!(r.body.len() < 4200 / 2, "gzip should shrink a repetitive 4 KB file, got {}", r.body.len());
    // a repeat visit costs nothing
    let etag = r.headers.get("etag").unwrap().clone();
    let r2 = http(door.addr, &get("/assets/app-1.js", &format!("If-None-Match: {etag}\r\n"))).await;
    assert_eq!(r2.status, 304);
    assert!(r2.body.is_empty());
    // the service worker is always re-checked
    assert_eq!(headers_get(&http(door.addr, &get("/sw.js", "")).await.headers, "cache-control"), Some("no-cache"));
    // an unknown file is a 404, not the app shell
    let r = http(door.addr, &get("/missing.js", "")).await;
    assert_eq!(r.status, 404);
    // HEAD has the headers and no body
    let r = http(door.addr, "HEAD /assets/app-1.js HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").await;
    assert_eq!(r.status, 200);
    assert!(r.body.is_empty() && r.headers.contains_key("content-length"));
    // backend paths are never answered here, and the backend's answer (and its 404) comes back untouched
    let r = http(door.addr, &get("/api/anything", "")).await;
    assert_eq!(r.status, 404);
    assert!(String::from_utf8_lossy(&r.body).contains("backend 404"));
    assert!(!r.headers.contains_key("alt-svc"), "HTTP/3 must not be advertised by a server that does not speak it");
}

#[tokio::test]
async fn a_path_that_climbs_out_of_the_web_folder_is_refused() {
    let door = start_door(start_backend().await).await;
    for p in ["/..%2f..%2fetc/passwd", "/%2e%2e/secret", "/assets/../../x", "/a%5c..%5cb", "/%00"] {
        let r = http(door.addr, &get(p, "")).await;
        assert_eq!(r.status, 404, "{p}");
        assert!(!String::from_utf8_lossy(&r.body).contains("root:"), "{p}");
        assert!(r.headers.contains_key("x-frame-options"), "{p}: rejections carry the security headers too");
    }
}

#[tokio::test]
async fn a_missing_backend_gives_a_clear_502() {
    let dead = {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        l.local_addr().unwrap()
    }; // closed again
    let door = start_door(dead).await;
    let r = http(door.addr, &get("/api/status", "")).await;
    assert_eq!(r.status, 502);
    assert!(String::from_utf8_lossy(&r.body).contains("backend is not reachable"));
    // the web app itself still opens while the backend is down
    assert_eq!(http(door.addr, &get("/photos", "")).await.status, 200);
}

#[tokio::test]
async fn backend_connections_are_reused_between_requests() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let accepted = Arc::new(AtomicUsize::new(0));
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let upstream = l.local_addr().unwrap();
    let counter = accepted.clone();
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = l.accept().await else { break };
            counter.fetch_add(1, Ordering::SeqCst);
            tokio::spawn(async move {
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(s), service_fn(backend_handler)).await;
            });
        }
    });
    let door = start_door(upstream).await;
    for _ in 0..6 {
        // Each client request has its own connection to the front door; only the backend side is pooled.
        let r = http(door.addr, &get("/api/echo", "")).await;
        assert_eq!(r.status, 200);
        tokio::time::sleep(Duration::from_millis(30)).await;
    }
    assert!(accepted.load(Ordering::SeqCst) <= 2, "the backend saw {} connections for 6 requests", accepted.load(Ordering::SeqCst));
}
