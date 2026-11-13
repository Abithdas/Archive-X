use std::convert::Infallible;
use std::io::BufReader;
use std::net::SocketAddr;
use std::sync::Arc;

use hyper::service::service_fn;
use hyper::{Method, Request, Response};
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto;
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;
use tracing::{info, warn};

use crate::config::Config;
use crate::proxy::{Body, Proxy};
use crate::static_files::{is_backend_path, StaticFiles};

pub struct State {
    pub proxy: Arc<Proxy>,
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
    let serve_static = matches!(*req.method(), Method::GET | Method::HEAD) && !is_backend_path(req.uri().path()) && !req.headers().contains_key(hyper::header::UPGRADE);
    if serve_static {
        return Ok(state.files.serve(req.method(), req.uri().path(), req.headers(), tls).await);
    }
    Ok(state.proxy.forward(req, peer).await)
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
    let state = Arc::new(State { proxy: Proxy::new(&cfg), files: StaticFiles::new(cfg.dist.clone()) });
    info!("front door on {}{} -> backend {}{}", if acceptor.is_some() { "https://" } else { "http://" }, listener.local_addr().map(|a| a.to_string()).unwrap_or_default(), if cfg.upstream_plain { "http://" } else { "https://" }, cfg.upstream);
    let tls_on = acceptor.is_some();
    if cfg.print_ready {
        let port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
        println!("ARCHIVE_X_READY {{\"port\":{port},\"pid\":{}}}", std::process::id());
        use std::io::Write;
        let _ = std::io::stdout().flush();
    }
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, peer) = match accepted { Ok(x) => x, Err(e) => { warn!("accept failed: {e}"); continue; } };
                stream.set_nodelay(true).ok();
                let (state, acceptor) = (state.clone(), acceptor.clone());
                tokio::spawn(async move {
                    let service = service_fn(move |req| handle(req, peer, tls_on, state.clone()));
                    let builder = auto::Builder::new(TokioExecutor::new());
                    match acceptor {
                        Some(a) => match a.accept(stream).await {
                            Ok(s) => { let _ = builder.serve_connection_with_upgrades(TokioIo::new(s), service).await; }
                            Err(e) => tracing::debug!("TLS handshake from {peer} failed: {e}"),
                        },
                        None => { let _ = builder.serve_connection_with_upgrades(TokioIo::new(stream), service).await; }
                    }
                });
            }
            _ = &mut shutdown => { info!("shutting down"); return Ok(()); }
        }
    }
}
