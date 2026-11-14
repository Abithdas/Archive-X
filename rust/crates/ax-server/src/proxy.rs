//! Forwards a request to the Python backend and streams the answer back: nothing is buffered, so large video
//! ranges and uploads pass straight through. WebSocket (and any other Upgrade) is tunnelled byte for byte.
//!
//! The real client address travels in `X-Archive-X-Peer`, signed with the shared secret in `X-Archive-X-Proxy-Key`
//! (the Python side: core/http_middleware.TrustedProxyMiddleware). Anything the client sent under those names, and
//! `X-Forwarded-For`, is removed first so it cannot be forged.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use std::sync::Mutex;
use hyper::client::conn::http1::SendRequest;

use std::pin::Pin;
use std::task::{Context, Poll};

use bytes::Bytes;
use http_body_util::{combinators::BoxBody, BodyExt};
use hyper::body::{Frame, Incoming, SizeHint};
use hyper::header::{HeaderName, HeaderValue, CONNECTION, HOST, UPGRADE};
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{ClientConfig, DigitallySignedStruct, SignatureScheme};
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;

use crate::config::Config;

pub type Body = BoxBody<Bytes, Box<dyn std::error::Error + Send + Sync>>;

const HOP_BY_HOP: &[&str] = &["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "proxy-connection"];
const NEVER_FORWARD: &[&str] = &["x-forwarded-for", "x-archive-x-peer", "x-archive-x-proxy-key", "x-archive-x-internal", "forwarded", "x-real-ip"];

/// Accepts the backend's own certificate. Safe only because the upstream address is checked to be loopback (config.rs).
#[derive(Debug)]
struct AcceptAnyCert(Vec<SignatureScheme>);

impl ServerCertVerifier for AcceptAnyCert {
    fn verify_server_cert(&self, _: &CertificateDer<'_>, _: &[CertificateDer<'_>], _: &ServerName<'_>, _: &[u8], _: UnixTime) -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }
    fn verify_tls13_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.0.clone()
    }
}

/// Idle keep-alive connections to the backend. Opening a TLS connection per request cost more than the work the
/// backend does for a small API call, so finished connections are kept and reused.
const MAX_IDLE: usize = 32;

pub struct Proxy {
    upstream: SocketAddr,
    key: HeaderValue,
    tls: Option<TlsConnector>,
    idle: Mutex<Vec<SendRequest<Body>>>,
}

fn boxed(b: Incoming) -> Body {
    b.map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>).boxed()
}

/// A JSON string body for `detail`, escaped by hand (quotes, backslashes and control characters).
pub fn json_escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if c.is_control() => out.push(' '),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn error_response(status: StatusCode, detail: &str) -> Response<Body> {
    let body = format!("{{\"detail\":{}}}", json_escape(detail));
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(http_body_util::Full::new(Bytes::from(body)).map_err(|n| match n {}).boxed())
        .unwrap()
}

impl Proxy {
    pub fn new(cfg: &Config) -> Arc<Proxy> {
        let tls = if cfg.upstream_plain {
            None
        } else {
            let provider = rustls::crypto::ring::default_provider();
            let schemes = provider.signature_verification_algorithms.supported_schemes();
            let client = ClientConfig::builder_with_provider(Arc::new(provider))
                .with_safe_default_protocol_versions()
                .unwrap()
                .dangerous()
                .with_custom_certificate_verifier(Arc::new(AcceptAnyCert(schemes)))
                .with_no_client_auth();
            Some(TlsConnector::from(Arc::new(client)))
        };
        Arc::new(Proxy { upstream: cfg.upstream, key: HeaderValue::from_str(&cfg.proxy_key).expect("proxy key is not a valid header value"), tls, idle: Mutex::new(Vec::new()) })
    }

    /// A usable connection: an idle one if there is one, otherwise a new one.
    #[allow(clippy::result_large_err)] // the error is a ready-made HTTP response, returned once and sent straight back
    async fn acquire(&self, fresh: bool) -> Result<(SendRequest<Body>, bool), Response<Body>> {
        if !fresh {
            let mut idle = self.idle.lock().unwrap();
            idle.retain(|s| !s.is_closed());
            if let Some(i) = idle.iter().position(|s| s.is_ready()) {
                return Ok((idle.swap_remove(i), true));
            }
        }
        let connect = async {
            let tcp = TcpStream::connect(self.upstream).await?;
            tcp.set_nodelay(true).ok();
            Ok::<_, std::io::Error>(tcp)
        };
        let tcp = match tokio::time::timeout(Duration::from_secs(5), connect).await {
            Ok(Ok(t)) => t,
            Ok(Err(e)) => return Err(error_response(StatusCode::BAD_GATEWAY, &format!("The Archive X backend is not reachable: {e}"))),
            Err(_) => return Err(error_response(StatusCode::GATEWAY_TIMEOUT, "The Archive X backend did not answer in time")),
        };
        let sender = match &self.tls {
            Some(tls) => {
                let name = ServerName::IpAddress(self.upstream.ip().into());
                match tls.connect(name, tcp).await {
                    Ok(stream) => handshake(TokioIo::new(stream)).await,
                    Err(e) => return Err(error_response(StatusCode::BAD_GATEWAY, &format!("TLS to the backend failed: {e}"))),
                }
            }
            None => handshake(TokioIo::new(tcp)).await,
        };
        sender.map(|s| (s, false)).map_err(|e| error_response(StatusCode::BAD_GATEWAY, &format!("The Archive X backend failed: {e}")))
    }

    /// One cheap authenticated call to the backend with the client's session cookie, so the backend sees the session as in
    /// use (see native.rs). The answer is read and thrown away.
    pub async fn touch(self: &Arc<Self>, cookie: &str, peer: SocketAddr) -> Result<(), String> {
        let (status, _) = self.call(hyper::Method::GET, "/api/app/ping", Some(cookie), Some(peer.ip().to_string()), false).await?;
        if status.is_success() { Ok(()) } else { Err(format!("the backend answered {status}")) }
    }

    /// Is this cookie a signed-in session? Asks the backend (which also counts it as use), so routes that need no database of their own
    /// still honour exactly the backend's login rules.
    pub async fn signed_in(self: &Arc<Self>, cookie: &str, peer: SocketAddr) -> Result<bool, String> {
        let (status, _) = self.call(hyper::Method::GET, "/api/app/ping", Some(cookie), Some(peer.ip().to_string()), false).await?;
        match status {
            s if s.is_success() => Ok(true),
            StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => Ok(false),
            s => Err(format!("the backend answered {s}")),
        }
    }

    /// What only the front door may ask the backend for (`/internal/...`): proved by the secret they share, from this PC.
    pub async fn internal_json(self: &Arc<Self>, path: &str) -> Result<serde_json::Value, String> {
        let (status, body) = self.call(hyper::Method::GET, path, None, Some("127.0.0.1".into()), true).await?;
        if !status.is_success() {
            return Err(format!("the backend answered {status}"));
        }
        serde_json::from_slice(&body).map_err(|e| e.to_string())
    }

    async fn call(self: &Arc<Self>, method: hyper::Method, path: &str, cookie: Option<&str>, peer: Option<String>, internal: bool) -> Result<(StatusCode, Bytes), String> {
        let empty: Body = http_body_util::Empty::new().map_err(|n| match n {}).boxed();
        let mut b = Request::builder().method(method).uri(path).header(HOST, "127.0.0.1").header(HeaderName::from_static("x-archive-x-proxy-key"), self.key.clone());
        if let Some(c) = cookie {
            b = b.header(hyper::header::COOKIE, c);
        }
        if let Some(p) = peer {
            b = b.header(HeaderName::from_static("x-archive-x-peer"), p);
        }
        if internal {
            b = b.header(HeaderName::from_static("x-archive-x-internal"), self.key.clone());
        }
        let req = b.body(empty).map_err(|e| e.to_string())?;
        let (mut sender, _) = self.acquire(false).await.map_err(|r| format!("backend not reachable ({})", r.status()))?;
        let resp = sender.send_request(req).await.map_err(|e| e.to_string())?;
        let status = resp.status();
        let body = resp.into_body().collect().await.map_err(|e| e.to_string())?.to_bytes();
        self.release(sender);
        Ok((status, body))
    }

    pub(crate) fn release(&self, sender: SendRequest<Body>) {
        let mut idle = self.idle.lock().unwrap();
        if idle.len() < MAX_IDLE && !sender.is_closed() {
            idle.push(sender);
        }
    }

    /// The request the backend sees: origin-form target, same Host, hop-by-hop headers removed, address headers set.
    #[allow(clippy::result_large_err)]
    fn build_request(&self, parts: &hyper::http::request::Parts, wants_upgrade: bool, body: Body) -> Result<Request<Body>, Response<Body>> {
        let target = parts.uri.path_and_query().map(|p| p.as_str().to_string()).unwrap_or_else(|| "/".into());
        let mut out = Request::builder().method(parts.method.clone()).uri(target).version(hyper::Version::HTTP_11);
        let h = out.headers_mut().unwrap();
        for (k, v) in parts.headers.iter() {
            let name = k.as_str();
            if NEVER_FORWARD.contains(&name) || (HOP_BY_HOP.contains(&name) && !(wants_upgrade && (name == "upgrade" || name == "connection"))) {
                continue;
            }
            h.append(k.clone(), v.clone());
        }
        if !h.contains_key(HOST) {
            if let Some(a) = parts.uri.authority() {
                if let Ok(v) = HeaderValue::from_str(a.as_str()) {
                    h.insert(HOST, v);
                }
            }
        }
        h.insert(HeaderName::from_static("x-archive-x-peer"), HeaderValue::from_str(&self.peer_for(parts)).unwrap());
        h.insert(HeaderName::from_static("x-archive-x-proxy-key"), self.key.clone());
        if wants_upgrade && !h.contains_key(CONNECTION) {
            h.insert(CONNECTION, HeaderValue::from_static("Upgrade"));
        }
        out.body(body).map_err(|e| error_response(StatusCode::BAD_GATEWAY, &format!("bad request: {e}")))
    }

    pub async fn forward(self: &Arc<Self>, mut req: Request<Incoming>, peer: SocketAddr) -> Response<Body> {
        let wants_upgrade = req.headers().contains_key(UPGRADE);
        let client_upgrade = if wants_upgrade { Some(hyper::upgrade::on(&mut req)) } else { None };
        let (mut parts, body) = req.into_parts();
        // The peer address is carried in an extension so build_request can sign it.
        parts.extensions.insert(PeerIp(peer.ip().to_string()));
        let safe_to_retry = matches!(parts.method, hyper::Method::GET | hyper::Method::HEAD | hyper::Method::OPTIONS) && !wants_upgrade;

        let out = match self.build_request(&parts, wants_upgrade, boxed(body)) {
            Ok(r) => r,
            Err(resp) => return resp,
        };
        let (mut sender, reused) = match self.acquire(wants_upgrade).await {
            Ok(x) => x,
            Err(resp) => return resp,
        };
        let mut response = sender.send_request(out).await;
        // A kept connection may have been closed by the backend while it sat idle. A request without a body can simply
        // be sent again on a new connection; one with a body has been consumed, so it is reported instead.
        if response.is_err() && reused && safe_to_retry {
            let empty: Body = http_body_util::Empty::new().map_err(|n| match n {}).boxed();
            let retry = match self.build_request(&parts, false, empty) {
                Ok(r) => r,
                Err(resp) => return resp,
            };
            match self.acquire(true).await {
                Ok((s, _)) => {
                    sender = s;
                    response = sender.send_request(retry).await;
                }
                Err(resp) => return resp,
            }
        }
        let mut resp = match response {
            Ok(r) => r,
            Err(e) => return error_response(StatusCode::BAD_GATEWAY, &format!("The Archive X backend failed: {e}")),
        };
        // The connection goes back to the pool when the answer has been fully read (see PoolBody), not before.
        let reusable = if !wants_upgrade && resp.status() != StatusCode::SWITCHING_PROTOCOLS { Some((self.clone(), sender)) } else { None };

        // A WebSocket handshake: once the backend says 101, tunnel both directions until either side closes.
        if resp.status() == StatusCode::SWITCHING_PROTOCOLS {
            if let Some(client_upgrade) = client_upgrade {
                let backend_upgrade = hyper::upgrade::on(&mut resp);
                tokio::spawn(async move {
                    if let (Ok(c), Ok(b)) = (client_upgrade.await, backend_upgrade.await) {
                        let (mut c, mut b) = (TokioIo::new(c), TokioIo::new(b));
                        let _ = tokio::io::copy_bidirectional(&mut c, &mut b).await;
                    }
                });
            }
            let (parts, _) = resp.into_parts();
            return Response::from_parts(parts, http_body_util::Empty::new().map_err(|n| match n {}).boxed());
        }

        let (mut rparts, rbody) = resp.into_parts();
        for name in HOP_BY_HOP {
            rparts.headers.remove(*name);
        }
        // The Python server advertises HTTP/3 on its own port; the front door does not speak it, so do not send browsers there.
        rparts.headers.remove("alt-svc");
        let body = PoolBody { inner: rbody, pool: reusable };
        Response::from_parts(rparts, body.map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>).boxed())
    }
}

/// The backend's answer, which hands its connection back to the pool once the whole answer has been read.
/// A client that goes away mid-answer drops the connection instead, since it cannot be reused safely.
struct PoolBody {
    inner: Incoming,
    pool: Option<(Arc<Proxy>, SendRequest<Body>)>,
}

impl PoolBody {
    fn finish(&mut self) {
        if let Some((proxy, sender)) = self.pool.take() {
            proxy.release(sender);
        }
    }
}

impl hyper::body::Body for PoolBody {
    type Data = Bytes;
    type Error = hyper::Error;

    fn poll_frame(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Result<Frame<Bytes>, hyper::Error>>> {
        let this = &mut *self;
        let polled = Pin::new(&mut this.inner).poll_frame(cx);
        match &polled {
            Poll::Ready(None) => this.finish(),
            // A sized answer ends with its last data frame; the server may never poll again after that.
            Poll::Ready(Some(Ok(_))) if this.inner.is_end_stream() => this.finish(),
            _ => {}
        }
        polled
    }

    fn is_end_stream(&self) -> bool {
        self.inner.is_end_stream()
    }

    fn size_hint(&self) -> SizeHint {
        self.inner.size_hint()
    }
}

/// Start the HTTP/1.1 connection task for a new backend connection; it ends when the connection closes.
async fn handshake<I>(io: TokioIo<I>) -> Result<SendRequest<Body>, hyper::Error>
where
    I: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send + 'static,
{
    let (sender, conn) = hyper::client::conn::http1::handshake(io).await?;
    tokio::spawn(async move {
        let _ = conn.with_upgrades().await;
    });
    Ok(sender)
}

/// The client's address, handed from `forward` to `build_request`.
#[derive(Clone)]
struct PeerIp(String);

impl Proxy {
    fn peer_for(&self, parts: &hyper::http::request::Parts) -> String {
        parts.extensions.get::<PeerIp>().map(|p| p.0.clone()).unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::json_escape;

    #[test]
    fn error_text_is_a_valid_json_string() {
        assert_eq!(json_escape(r#"say "hi" \ there"#), r#""say \"hi\" \\ there""#);
        assert_eq!(json_escape("line\nbreak"), "\"line break\"");
    }
}
