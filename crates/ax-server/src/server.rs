use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use http_body_util::BodyExt;
use hyper::service::service_fn;
use hyper::{Method, Request, Response};
use hyper_util::rt::{TokioExecutor, TokioIo, TokioTimer};
use std::time::Duration;
use tokio::net::TcpListener;
use tokio::sync::{watch, Semaphore};
use tokio_rustls::TlsAcceptor;
use tracing::{info, warn};

use crate::config::Config;
use crate::learning::Learning;
use crate::names::Names;
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
    pub names: Option<Arc<Names>>,
    pub learning: Option<Arc<Learning>>,
    pub files: StaticFiles,
    pub launch_secret: Option<String>,
}

fn tls_acceptor(cert: &std::path::Path, key: &std::path::Path) -> Result<TlsAcceptor, String> {
    use rustls::pki_types::{pem::Error as PemError, pem::PemObject, CertificateDer, PrivateKeyDer};
    // rustls-pki-types reads the PEM files itself (rustls-pemfile, used before, is no longer maintained).
    let certs = CertificateDer::pem_file_iter(cert)
        .map_err(|e| format!("cannot read {}: {e}", cert.display()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("bad certificate file: {e}"))?;
    let key = PrivateKeyDer::from_pem_file(key).map_err(|e| match e {
        PemError::NoItemsFound => "no private key found in the key file".to_string(),
        other => format!("bad key file {}: {other}", key.display()),
    })?;
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
    // `/internal/...` is for the front door's own calls to the backend, never for a client.
    if req.uri().path().starts_with("/internal/") {
        return Ok(served_by(crate::auth::json_error(hyper::StatusCode::NOT_FOUND, "Not Found"), "rust"));
    }
    // The performer name list (see names.rs).
    if let Some(names) = &state.names {
        if let Some(route) = names.route_for(req.method(), req.uri().path()) {
            let host = req.headers().get(hyper::header::HOST).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
            if let Some(resp) = names.handle(route, req.uri().query(), req.headers(), peer, &host).await {
                return Ok(served_by(crate::static_files::with_security_headers(resp, tls), "rust"));
            }
        }
    }
    // Face learning (see learning.rs).
    if let Some(learning) = &state.learning {
        if let Some(route) = learning.route_for(req.method(), req.uri().path()) {
            if let Some(resp) = learning.handle(route, req.uri().query(), req.headers(), peer).await {
                return Ok(served_by(crate::static_files::with_security_headers(resp, tls), "rust"));
            }
        }
    }
    // Routes switched on in --native-routes are answered here (see native.rs).
    if let Some(native) = &state.native {
        let guarded = crate::desktop_guard::allowed(state.launch_secret.as_deref(), req.headers());
        if let Some(route) = native.route_for(req.method(), req.uri().path()).filter(|_| guarded) {
            let host = req.headers().get(hyper::header::HOST).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
            if route.needs_body {
                // A POST: read the (small) body first; if the route declines it, the same body goes on to Python.
                let (parts, body) = req.into_parts();
                let bytes = match http_body_util::Limited::new(body, 64 * 1024).collect().await {
                    Ok(c) => c.to_bytes(),
                    Err(_) => return Ok(served_by(crate::auth::json_error(hyper::StatusCode::PAYLOAD_TOO_LARGE, "Request body too large"), "rust")),
                };
                let ctx = crate::native::Ctx { body: &bytes, host: &host };
                if let Some(resp) = native.handle(&route, parts.uri.query(), &parts.headers, peer, &ctx).await {
                    return Ok(served_by(crate::static_files::with_security_headers(resp, tls), "rust"));
                }
                let again = Request::from_parts(parts, http_body_util::Full::new(bytes));
                return Ok(served_by(state.proxy.forward(again, peer).await, "python"));
            }
            let ctx = crate::native::Ctx { body: &[], host: &host };
            if let Some(resp) = native.handle(&route, req.uri().query(), req.headers(), peer, &ctx).await {
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
    serve(listener, cfg, async {
        let _ = tokio::signal::ctrl_c().await;
    })
    .await
}

/// Serve on an already-bound listener until `shutdown` completes.
pub async fn serve(listener: TcpListener, cfg: Arc<Config>, shutdown: impl std::future::Future<Output = ()>) -> Result<(), String> {
    serve_with_fetch(listener, cfg, shutdown, None).await
}

/// `serve`, with the way the performer name list reaches the providers replaceable (tests use a fake).
pub async fn serve_with_fetch(
    listener: TcpListener,
    cfg: Arc<Config>,
    shutdown: impl std::future::Future<Output = ()>,
    fetch: Option<Arc<dyn ax_names::Fetch>>,
) -> Result<(), String> {
    let acceptor = match (&cfg.cert, &cfg.key) {
        (Some(c), Some(k)) => Some(tls_acceptor(c, k)?),
        _ => None,
    };
    tokio::pin!(shutdown);
    let proxy = Proxy::new(&cfg);
    let database_routes: Vec<String> = cfg
        .native_routes
        .iter()
        .filter(|r| !crate::names::ROUTES.contains(&r.as_str()) && !crate::learning::ROUTES.contains(&r.as_str()))
        .cloned()
        .collect();
    // No --db: the backend knows where its database is. If it cannot say, the database routes stay off and Python answers everything.
    let mut db_path = cfg.db.clone();
    if !database_routes.is_empty() && db_path.is_none() {
        for attempt in 0..5 {
            match proxy.internal_json("/internal/provider-keys").await {
                Ok(v) => {
                    db_path = v.get("db_path").and_then(|p| p.as_str()).filter(|p| !p.is_empty()).map(std::path::PathBuf::from);
                    break;
                }
                Err(e) if attempt == 4 => warn!("could not ask the backend where the database is ({e}); the routes that read it stay with Python"),
                Err(_) => tokio::time::sleep(Duration::from_millis(500)).await,
            }
        }
    }
    let database_routes = if db_path.is_some() { database_routes } else { Vec::new() };
    let native = Native::new(&database_routes, db_path.as_deref(), proxy.clone(), cfg.session_touch_interval)?;
    let names = match fetch {
        Some(f) => Names::with_fetch(&cfg.native_routes, proxy.clone(), f, std::time::Duration::ZERO),
        None => Names::new(&cfg.native_routes, proxy.clone()),
    };
    let learning = Learning::new(&cfg.native_routes, proxy.clone());
    if let Some(native) = &native {
        if let Some(n) = &names {
            n.use_auth(native.auth());
        }
        if let Some(l) = &learning {
            l.use_auth(native.auth());
        }
    }
    if let Some(n) = &names {
        n.clone().spawn_loop(std::time::Duration::from_secs(120), std::time::Duration::from_secs(6 * 3600));
    }
    if let Some(n) = &native {
        info!("answering these routes itself: {}", n.names().join(", "));
    }
    let state = Arc::new(State { proxy, native, names, learning, files: StaticFiles::new(cfg.dist.clone()), launch_secret: cfg.launch_secret.clone() });
    info!(
        "front door on {}{} -> backend {}{}",
        if acceptor.is_some() { "https://" } else { "http://" },
        listener.local_addr().map(|a| a.to_string()).unwrap_or_default(),
        if cfg.upstream_plain { "http://" } else { "https://" },
        cfg.upstream
    );
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
