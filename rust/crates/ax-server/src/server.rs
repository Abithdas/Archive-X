use std::convert::Infallible;
use std::io::BufReader;
use std::net::SocketAddr;
use std::sync::Arc;

use hyper::service::service_fn;
use hyper::{Method, Request, Response};
use hyper_util::rt::{TokioExecutor, TokioIo, TokioTimer};
use std::time::Duration;
use tokio::sync::{watch, Semaphore};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;
use tracing::{info, warn};

use crate::config::Config;
use crate::native::Native;
use crate::proxy::{Body, Proxy};
use crate::static_files::{is_backend_path, is_spa_path, StaticFiles};

/// Run one connection until it ends; on shutdown ask it to finish its current request first.
macro_rules! drive {
    ($conn:ident, $stop:ident) => {{
        tokio::select! {
            _ = &mut $conn => {}
            _ = async { let _ = $stop.wait_for(|stopping| *stopping).await; } => {
                $conn.as_mut().graceful_shutdown();
                let _ = $conn.as_mut().await;
            }
        }
    }};
}

pub struct State {
    pub proxy: Arc<Proxy>,
    pub native: Option<Native>,
    pub files: StaticFiles,
}

fn tls_acceptor(cert: &std::path::Path, key: &std::path::Path) -> Result<TlsAcceptor, String> {
    let certs = rustls_pemfile::certs(&mut BufReader::new(std::fs::File::open(cert).map_err(|e| format!("cannot read {}: {e}", cert.display()))?))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("bad certificate file: {e}"))?;
    let key = rustls_pemfile::private_key(&mut BufReader::new(std::fs::File::open(key).map_err(|e| format!("cannot read {}: {e}", key.display()))?))
        .map_err(|e| format!("bad key file: {e}"))?
        .ok_or("no private key found in the key file")?;
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let mut cfg = rustls::ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|e| e.to_string())?
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .map_err(|e| format!("certificate and key do not match: {e}"))?;
    cfg.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
    Ok(TlsAcceptor::from(Arc::new(cfg)))
}

/// Decide who answers: the web app's own files here, everything else the Python backend.
pub async fn handle(req: Request<hyper::body::Incoming>, peer: SocketAddr, tls: bool, state: Arc<State>) -> Result<Response<Body>, Infallible> {
    let served_by = |mut r: Response<Body>, who: &'static str| {
        r.headers_mut().insert("x-served-by", hyper::header::HeaderValue::from_static(who));
        r
    };
    // Routes switched on in --native-routes are answered here (see native.rs).
    if let Some(native) = &state.native {
        if let Some(route) = native.route_for(req.method(), req.uri().path()) {
            if let Some(resp) = native.handle(&route, req.uri().query(), req.headers(), peer).await {
                return Ok(served_by(crate::static_files::with_security_headers(resp, tls), "rust"));
            }
            // otherwise this request is one the route does not handle: it goes on to Python like any other
        }
    }
    // Static only for what is certainly ours: a file that exists in the web folder, or one of the app's own pages.
    // Everything else, including routes this server has never heard of, is the Python backend's to answer.
    let path = req.uri().path();
    let candidate = matches!(*req.method(), Method::GET | Method::HEAD) && !is_backend_path(path) && !req.headers().contains_key(hyper::header::UPGRADE);
    let serve_static = candidate && (is_spa_path(path) || state.files.lookup(path).await.is_some());
    if serve_static {
        return Ok(served_by(state.files.serve(req.method(), req.uri().path(), req.headers(), tls).await, "rust"));
    }
    Ok(served_by(state.proxy.forward(req, peer).await, "python"))
}

pub async fn run(cfg: Arc<Config>) -> Result<(), String> {
    let listener = TcpListener::bind(cfg.listen).await.map_err(|e| format!("cannot listen on {}: {e}", cfg.listen))?;
    serve(listener, cfg, async { let _ = tokio::signal::ctrl_c().await; }).await
}

/// Serve on an already-bound listener until `shutdown` completes.
pub async fn serve(listener: TcpListener, cfg: Arc<Config>, shutdown: impl std::future::Future<Output = ()>) -> Result<(), String> {
    let acceptor = match (&cfg.cert, &cfg.key) {
        (Some(c), Some(k)) => Some(tls_acceptor(c, k)?),
        _ => None,
    };
    tokio::pin!(shutdown);
    let proxy = Proxy::new(&cfg);
    let native = Native::new(&cfg.native_routes, cfg.db.as_deref(), proxy.clone(), cfg.session_touch_interval)?;
    if let Some(n) = &native {
        info!("answering these routes itself: {}", n.names().join(", "));
    }
    let state = Arc::new(State { proxy, native, files: StaticFiles::new(cfg.dist.clone()) });
    info!("front door on {}{} -> backend {}{}", if acceptor.is_some() { "https://" } else { "http://" }, listener.local_addr().map(|a| a.to_string()).unwrap_or_default(), if cfg.upstream_plain { "http://" } else { "https://" }, cfg.upstream);
    let tls_on = acceptor.is_some();
    if cfg.print_ready {
        let port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
        println!("ARCHIVE_X_READY {{\"port\":{port},\"pid\":{}}}", std::process::id());
        use std::io::Write;
        let _ = std::io::stdout().flush();
    }
    let permits = Arc::new(Semaphore::new(cfg.limits.max_connections));
    let (stop_tx, stop_rx) = watch::channel(false);
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, peer) = match accepted { Ok(x) => x, Err(e) => { warn!("accept failed: {e}"); continue; } };
                let Ok(permit) = permits.clone().try_acquire_owned() else {
                    warn!("connection limit ({}) reached; refusing {peer}", cfg.limits.max_connections);
                    continue; // dropping the stream closes it
                };
                stream.set_nodelay(true).ok();
                let (state, acceptor, mut stop, limits) = (state.clone(), acceptor.clone(), stop_rx.clone(), cfg.limits.clone());
                tokio::spawn(async move {
                    let _permit = permit;
                    let service = service_fn(move |req| handle(req, peer, tls_on, state.clone()));
                    // The protocol is chosen up front (HTTP/2 only when TLS negotiated it), never sniffed from the
                    // first bytes: sniffing waits for the client, and a silent client must not be able to hold a connection.
                    let http1 = || {
                        let mut b = hyper::server::conn::http1::Builder::new();
                        b.timer(TokioTimer::new()).header_read_timeout(limits.header_read_timeout);
                        b
                    };
                    match acceptor {
                        Some(a) => match tokio::time::timeout(limits.tls_handshake_timeout, a.accept(stream)).await {
                            Ok(Ok(s)) if s.get_ref().1.alpn_protocol() == Some(&b"h2"[..]) => {
                                let mut b = hyper::server::conn::http2::Builder::new(TokioExecutor::new());
                                b.timer(TokioTimer::new()).keep_alive_interval(Some(Duration::from_secs(30))).keep_alive_timeout(Duration::from_secs(20));
                                let mut conn = std::pin::pin!(b.serve_connection(TokioIo::new(s), service));
                                drive!(conn, stop);
                            }
                            Ok(Ok(s)) => {
                                let mut conn = std::pin::pin!(http1().serve_connection(TokioIo::new(s), service).with_upgrades());
                                drive!(conn, stop);
                            }
                            Ok(Err(e)) => tracing::debug!("TLS handshake from {peer} failed: {e}"),
                            Err(_) => tracing::debug!("TLS handshake from {peer} timed out"),
                        },
                        None => {
                            let mut conn = std::pin::pin!(http1().serve_connection(TokioIo::new(stream), service).with_upgrades());
                            drive!(conn, stop);
                        }
                    }
                });
            }
            _ = &mut shutdown => {
                info!("shutting down; waiting up to {:?} for requests in flight", cfg.limits.shutdown_grace);
                let _ = stop_tx.send(true); // every connection finishes its current request and then closes
                // Each connection holds a permit; once all are back, nothing is left in flight.
                if tokio::time::timeout(cfg.limits.shutdown_grace, permits.acquire_many(cfg.limits.max_connections as u32)).await.is_err() {
                    warn!("some connections were still open after the grace period");
                }
                return Ok(());
            }
        }
    }
}
