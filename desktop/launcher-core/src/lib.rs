//! Launcher core for the Archive X desktop app.
//!
//! Responsibilities (all unit/integration tested without a GUI):
//! * generate the per-launch secret and start the backend with the desktop-mode environment,
//! * read the `ARCHIVE_X_READY {json}` line the backend prints once it is serving (this is how the shell learns the
//!   port the backend bound),
//! * build the one-time session URL the window opens,
//! * check health, and stop the backend gracefully (`POST /desktop/shutdown`), falling back to a kill.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use serde::Deserialize;

pub const READY_PREFIX: &str = "ARCHIVE_X_READY ";
pub const SECRET_HEADER: &str = "X-Archive-X-Desktop";

#[derive(Debug)]
pub enum LaunchError {
    Spawn(std::io::Error),
    Exited(Option<i32>, Vec<String>),
    Timeout(Duration, Vec<String>),
    BadReadyLine(String),
    Random(String),
}

impl std::fmt::Display for LaunchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LaunchError::Spawn(e) => write!(f, "could not start the Archive X backend: {e}"),
            LaunchError::Exited(code, log) => write!(
                f,
                "the Archive X backend exited before it was ready (exit code {:?}). Last output:\n{}",
                code,
                log.join("\n")
            ),
            LaunchError::Timeout(d, log) => write!(
                f,
                "the Archive X backend did not become ready within {:?}. Last output:\n{}",
                d,
                log.join("\n")
            ),
            LaunchError::BadReadyLine(l) => write!(f, "unreadable ready line from the backend: {l}"),
            LaunchError::Random(e) => write!(f, "could not generate the launch secret: {e}"),
        }
    }
}
impl std::error::Error for LaunchError {}

#[derive(Debug, Deserialize, Clone, PartialEq, Eq)]
pub struct Ready {
    pub port: u16,
    pub pid: u32,
    #[serde(default)]
    pub version: String,
}

/// Parse one backend stdout line; `None` if it is not the ready line.
pub fn parse_ready_line(line: &str) -> Option<Result<Ready, LaunchError>> {
    let rest = line.trim_end().strip_prefix(READY_PREFIX)?;
    Some(serde_json::from_str::<Ready>(rest).map_err(|_| LaunchError::BadReadyLine(line.to_string())))
}

/// 32 random bytes as 64 hex characters.
pub fn generate_secret() -> Result<String, LaunchError> {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|e| LaunchError::Random(e.to_string()))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

#[derive(Debug, Clone, Default)]
pub struct LaunchOptions {
    pub exe: PathBuf,
    pub data_dir: Option<PathBuf>,
    pub tools_dir: Option<PathBuf>,
    pub ready_timeout: Option<Duration>,
    /// Extra environment for tests or advanced setups.
    pub extra_env: Vec<(String, String)>,
}

pub struct Backend {
    child: Child,
    pub ready: Ready,
    secret: String,
    /// Recent backend output, kept for diagnostics.
    log: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
}

impl Backend {
    pub fn spawn(opts: &LaunchOptions) -> Result<Backend, LaunchError> {
        let secret = generate_secret()?;
        let timeout = opts.ready_timeout.unwrap_or(Duration::from_secs(90));
        let mut cmd = Command::new(&opts.exe);
        cmd.env("ARCHIVE_X_DESKTOP", "1")
            .env("ARCHIVE_X_LAUNCH_SECRET", &secret)
            .env("ARCHIVE_X_PARENT_PID", std::process::id().to_string())
            .env("ARCHIVE_X_AUTO_OPEN_BROWSER", "0")
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(d) = &opts.data_dir {
            cmd.env("ARCHIVE_X_DATA_DIR", d);
        }
        if let Some(t) = &opts.tools_dir {
            cmd.env("ARCHIVE_X_TOOLS_DIR", t);
        }
        for (k, v) in &opts.extra_env {
            cmd.env(k, v);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window next to the app
        }
        let mut child = cmd.spawn().map_err(LaunchError::Spawn)?;

        let log = std::sync::Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let (tx, rx) = mpsc::channel::<Result<Ready, LaunchError>>();
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        let log_out = log.clone();
        thread::spawn(move || {
            let mut sent = false;
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if !sent {
                    if let Some(parsed) = parse_ready_line(&line) {
                        let _ = tx.send(parsed);
                        sent = true;
                        continue;
                    }
                }
                push_log(&log_out, line);
            }
        });
        let log_err = log.clone();
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                push_log(&log_err, line);
            }
        });

        let deadline = Instant::now() + timeout;
        loop {
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(Ok(ready)) => return Ok(Backend { child, ready, secret, log }),
                Ok(Err(e)) => {
                    let _ = child.kill();
                    return Err(e);
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    let status = child.wait().ok().and_then(|s| s.code());
                    return Err(LaunchError::Exited(status, snapshot(&log)));
                }
            }
            if let Ok(Some(status)) = child.try_wait() {
                // Give the reader threads a moment to drain the last lines.
                thread::sleep(Duration::from_millis(150));
                return Err(LaunchError::Exited(status.code(), snapshot(&log)));
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                return Err(LaunchError::Timeout(timeout, snapshot(&log)));
            }
        }
    }

    pub fn port(&self) -> u16 {
        self.ready.port
    }

    /// The one-time URL the window opens: it creates the web session and redirects to the app.
    pub fn session_url(&self) -> String {
        format!("http://127.0.0.1:{}/desktop/session?token={}", self.ready.port, self.secret)
    }

    pub fn log_tail(&self) -> Vec<String> {
        snapshot(&self.log)
    }

    pub fn is_running(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    /// `GET /health` over plain loopback HTTP.
    pub fn healthy(&self) -> bool {
        http_request(self.ready.port, "GET", "/health", &[], Duration::from_secs(3))
            .map(|(status, _)| status == 200)
            .unwrap_or(false)
    }

    /// Ask the backend to stop (jobs are checkpointed); kill it if it does not exit within `grace`.
    /// Returns true if it exited on its own.
    pub fn shutdown(&mut self, grace: Duration) -> bool {
        if !self.is_running() {
            return true;
        }
        let asked = http_request(
            self.ready.port,
            "POST",
            "/desktop/shutdown",
            &[(SECRET_HEADER, self.secret.as_str()), ("Content-Length", "0")],
            Duration::from_secs(5),
        )
        .map(|(status, _)| status == 202)
        .unwrap_or(false);
        let deadline = Instant::now() + grace;
        while asked && Instant::now() < deadline {
            if !self.is_running() {
                return true;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
        false
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        if self.is_running() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

fn push_log(log: &std::sync::Arc<std::sync::Mutex<Vec<String>>>, line: String) {
    if let Ok(mut v) = log.lock() {
        v.push(line);
        let len = v.len();
        if len > 200 {
            v.drain(0..len - 200);
        }
    }
}

fn snapshot(log: &std::sync::Arc<std::sync::Mutex<Vec<String>>>) -> Vec<String> {
    log.lock().map(|v| v.iter().rev().take(30).rev().cloned().collect()).unwrap_or_default()
}

/// Minimal blocking HTTP/1.1 request to 127.0.0.1 (no TLS, no extra dependencies).
pub fn http_request(
    port: u16,
    method: &str,
    path: &str,
    headers: &[(&str, &str)],
    timeout: Duration,
) -> std::io::Result<(u16, String)> {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut stream = TcpStream::connect_timeout(&addr, timeout)?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;
    let mut req = format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n");
    for (k, v) in headers {
        req.push_str(&format!("{k}: {v}\r\n"));
    }
    req.push_str("\r\n");
    stream.write_all(req.as_bytes())?;
    let mut raw = String::new();
    stream.read_to_string(&mut raw)?;
    let status = raw
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidData, "bad HTTP status line"))?;
    let body = raw.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
    Ok((status, body))
}

/// Where the frozen backend lives relative to the app's resource directory.
pub fn backend_exe_in(resource_dir: &Path) -> PathBuf {
    let name = if cfg!(windows) { "archive-x-backend.exe" } else { "archive-x-backend" };
    resource_dir.join("backend").join(name)
}

pub fn tools_dir_in(resource_dir: &Path) -> PathBuf {
    resource_dir.join("ffmpeg")
}
