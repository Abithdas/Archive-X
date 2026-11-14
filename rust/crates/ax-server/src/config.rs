use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::time::Duration;

pub const USAGE: &str = "usage: ax-server --listen 0.0.0.0:8443 --upstream 127.0.0.1:18443 --dist frontend/dist \
[--cert cert.pem --key key.pem] [--upstream-plain] [--print-ready] [--db archive.db --native-routes photos_channels|all]\n\
environment: ARCHIVE_X_PROXY_KEY  secret shared with the Python backend (required)";

/// Protection against clients that connect and then do nothing, or that open too many connections.
#[derive(Debug, Clone)]
pub struct Limits {
    /// How long a client may take to send the request line and headers (this also ends idle keep-alive connections).
    pub header_read_timeout: Duration,
    pub tls_handshake_timeout: Duration,
    pub max_connections: usize,
    /// On shutdown, requests already in flight get this long to finish.
    pub shutdown_grace: Duration,
}

impl Default for Limits {
    fn default() -> Self {
        Limits { header_read_timeout: Duration::from_secs(30), tls_handshake_timeout: Duration::from_secs(10), max_connections: 2048, shutdown_grace: Duration::from_secs(10) }
    }
}

#[derive(Debug, Clone)]
pub struct Config {
    pub listen: SocketAddr,
    pub upstream: SocketAddr,
    /// The Python backend speaks HTTPS with its own certificate unless this is set (tests).
    pub upstream_plain: bool,
    pub dist: PathBuf,
    pub cert: Option<PathBuf>,
    pub key: Option<PathBuf>,
    pub proxy_key: String,
    /// Print `ARCHIVE_X_READY {"port":N,"pid":P}` once listening (how the desktop launcher learns the port).
    pub print_ready: bool,
    pub limits: Limits,
    /// The database file, needed by routes answered here (`--db`, or ARCHIVE_X_DB).
    pub db: Option<PathBuf>,
    /// Routes answered here instead of by Python: names from `native::ROUTES`, or `all`. Empty by default, so nothing moves unless asked.
    pub native_routes: Vec<String>,
    /// How often a session used only on routes answered here is reported to Python, so it keeps sliding (Python writes the new expiry
    /// to its database at most every 10 minutes).
    pub session_touch_interval: Duration,
}

impl Config {
    pub fn from_env_and_args(args: impl Iterator<Item = String>) -> Result<Config, String> {
        let mut listen = "0.0.0.0:8443".to_string();
        let mut upstream = "127.0.0.1:18443".to_string();
        let mut dist = "frontend/dist".to_string();
        let (mut cert, mut key) = (None, None);
        let mut upstream_plain = false;
        let mut print_ready = false;
        let mut db = std::env::var("ARCHIVE_X_DB").ok().filter(|v| !v.is_empty()).map(PathBuf::from);
        let mut native = std::env::var("ARCHIVE_X_NATIVE_ROUTES").unwrap_or_default();
        let mut args = args.peekable();
        while let Some(a) = args.next() {
            let mut value = |name: &str| args.next().ok_or_else(|| format!("{name} needs a value"));
            match a.as_str() {
                "--listen" => listen = value("--listen")?,
                "--upstream" => upstream = value("--upstream")?,
                "--dist" => dist = value("--dist")?,
                "--cert" => cert = Some(PathBuf::from(value("--cert")?)),
                "--key" => key = Some(PathBuf::from(value("--key")?)),
                "--upstream-plain" => upstream_plain = true,
                "--print-ready" => print_ready = true,
                "--db" => db = Some(PathBuf::from(value("--db")?)),
                "--native-routes" => native = value("--native-routes")?,
                "-h" | "--help" => return Err("help".into()),
                other => return Err(format!("unknown option {other}")),
            }
        }
        if cert.is_some() != key.is_some() {
            return Err("--cert and --key go together".into());
        }
        let listen: SocketAddr = listen.parse().map_err(|_| format!("bad --listen address {listen}"))?;
        let upstream: SocketAddr = upstream.parse().map_err(|_| format!("bad --upstream address {upstream}"))?;
        // The proxy trusts the upstream completely (it accepts any certificate), so it must be on this machine.
        let loopback = match upstream.ip() {
            IpAddr::V4(ip) => ip.is_loopback(),
            IpAddr::V6(ip) => ip.is_loopback(),
        };
        if !loopback {
            return Err("--upstream must be a loopback address (127.0.0.1 or ::1)".into());
        }
        let proxy_key = std::env::var("ARCHIVE_X_PROXY_KEY").unwrap_or_default();
        if proxy_key.len() < 16 {
            return Err("ARCHIVE_X_PROXY_KEY must be set to a random secret of at least 16 characters".into());
        }
        let native_routes: Vec<String> = native.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty() && s != "none").collect();
        if !native_routes.is_empty() && db.is_none() {
            return Err("--native-routes needs --db (the database file the routes read)".into());
        }
        Ok(Config { listen, upstream, upstream_plain, dist: PathBuf::from(dist), cert, key, proxy_key, print_ready, limits: Limits::default(), db, native_routes, session_touch_interval: Duration::from_secs(300) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(args: &[&str], key: &str) -> Result<Config, String> {
        // The key is read from the environment, so serialise the tests that set it.
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("ARCHIVE_X_PROXY_KEY", key);
        Config::from_env_and_args(args.iter().map(|s| s.to_string()))
    }

    #[test]
    fn the_backend_must_be_on_this_machine() {
        assert!(parse(&["--upstream", "127.0.0.1:18443"], "0123456789abcdef").is_ok());
        assert!(parse(&["--upstream", "[::1]:18443"], "0123456789abcdef").is_ok());
        let e = parse(&["--upstream", "192.168.1.5:18443"], "0123456789abcdef").unwrap_err();
        assert!(e.contains("loopback"), "{e}");
    }

    #[test]
    fn the_shared_secret_is_required_and_long_enough() {
        assert!(parse(&[], "short").unwrap_err().contains("ARCHIVE_X_PROXY_KEY"));
        assert!(parse(&[], "").unwrap_err().contains("ARCHIVE_X_PROXY_KEY"));
    }

    #[test]
    fn a_certificate_needs_its_key_and_options_are_checked() {
        assert!(parse(&["--cert", "c.pem"], "0123456789abcdef").unwrap_err().contains("go together"));
        assert!(parse(&["--bogus"], "0123456789abcdef").unwrap_err().contains("unknown option"));
        assert!(parse(&["--listen", "nope"], "0123456789abcdef").unwrap_err().contains("--listen"));
    }
}
