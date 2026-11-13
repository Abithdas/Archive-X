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
    /// Path to open after the session hand-off: "/" (classic UI, default) or "/app-next/" (React UI).
    pub landing: Option<String>,
    /// Extra environment for tests or advanced setups.
    pub extra_env: Vec<(String, String)>,
    /// Opt-in: put the Rust front door (rust/crates/ax-server) in front of the backend. `None` keeps the old behaviour.
    pub front_door: Option<FrontDoorOptions>,
}

/// The Rust front door: serves the web app's files itself and forwards everything else to the Python backend.
#[derive(Debug, Clone)]
pub struct FrontDoorOptions {
    pub exe: PathBuf,
    /// The built web app (frontend/dist).
    pub dist_dir: PathBuf,
}

struct FrontDoor {
    child: Child,
    port: u16,
}

pub struct Backend {
    child: Child,
    front: Option<FrontDoor>,
    pub ready: Ready,
    secret: String,
    /// Recent backend output, kept for diagnostics.
    log: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
}

impl Backend {
    pub fn spawn(opts: &LaunchOptions) -> Result<Backend, LaunchError> {
        let secret = generate_secret()?;
        // Shared by the backend and the front door so the backend can trust the client address the front door reports.
        let proxy_key = if opts.front_door.is_some() { Some(generate_secret()?) } else { None };
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
        if let Some(k) = &proxy_key {
            cmd.env("ARCHIVE_X_PROXY_KEY", k);
        }
        if let Some(d) = &opts.data_dir {
            cmd.env("ARCHIVE_X_DATA_DIR", d);
        }
        if let Some(t) = &opts.tools_dir {
            cmd.env("ARCHIVE_X_TOOLS_DIR", t);
        }
        if let Some(l) = &opts.landing {
            cmd.env("ARCHIVE_X_DESKTOP_LANDING", l);
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
                Ok(Ok(ready)) => {
                    let front = match (&opts.front_door, &proxy_key) {
                        (Some(fd), Some(key)) => match start_front_door(fd, key, ready.port, &log) {
                            Ok(f) => Some(f),
                            Err(e) => {
                                // The app still works without it: fall back to talking to the backend directly.
                                push_log(&log, format!("front door not started, using the backend directly: {e}"));
                                None
                            }
                        },
                        _ => None,
                    };
                    return Ok(Backend { child, front, ready, secret, log });
                }
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

    /// The port the window should open: the front door's when it is running, otherwise the backend's.
    pub fn port(&self) -> u16 {
        self.front.as_ref().map(|f| f.port).unwrap_or(self.ready.port)
    }

    /// The one-time URL the window opens: it creates the web session and redirects to the app.
    pub fn session_url(&self) -> String {
        format!("http://127.0.0.1:{}/desktop/session?token={}", self.port(), self.secret)
    }

    pub fn log_tail(&self) -> Vec<String> {
        snapshot(&self.log)
    }

    /// True while the backend (and the front door, if one was started) are both alive.
    pub fn is_running(&mut self) -> bool {
        let backend = matches!(self.child.try_wait(), Ok(None));
        let front = match self.front.as_mut() {
            Some(f) => matches!(f.child.try_wait(), Ok(None)),
            None => true,
        };
        backend && front
    }

    /// `GET /health` over plain loopback HTTP.
    pub fn healthy(&self) -> bool {
        http_request(self.port(), "GET", "/health", &[], Duration::from_secs(3))
            .map(|(status, _)| status == 200)
            .unwrap_or(false)
    }

    /// Ask the backend to stop (jobs are checkpointed); kill it if it does not exit within `grace`.
    /// Returns true if it exited on its own.
    pub fn shutdown(&mut self, grace: Duration) -> bool {
        if !self.is_running() {
            self.stop_front_door();
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
            if !matches!(self.child.try_wait(), Ok(None)) {
                self.stop_front_door();
                return true;
            }
            thread::sleep(Duration::from_millis(100));
        }
        self.stop_front_door();
        let _ = self.child.kill();
        let _ = self.child.wait();
        false
    }

    fn stop_front_door(&mut self) {
        if let Some(mut f) = self.front.take() {
            let _ = f.child.kill();
            let _ = f.child.wait();
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.stop_front_door();
        if matches!(self.child.try_wait(), Ok(None)) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

/// Start the front door in front of the backend and wait for it to say which port it bound.
fn start_front_door(opts: &FrontDoorOptions, key: &str, backend_port: u16, log: &std::sync::Arc<std::sync::Mutex<Vec<String>>>) -> Result<FrontDoor, String> {
    let mut cmd = Command::new(&opts.exe);
    cmd.args(["--listen", "127.0.0.1:0", "--upstream", &format!("127.0.0.1:{backend_port}"), "--upstream-plain", "--print-ready", "--dist"])
        .arg(&opts.dist_dir)
        .env("ARCHIVE_X_PROXY_KEY", key)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }
    let mut child = cmd.spawn().map_err(|e| format!("cannot start {}: {e}", opts.exe.display()))?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let stderr = child.stderr.take().ok_or("no stderr")?;
    let (tx, rx) = mpsc::channel::<Result<Ready, LaunchError>>();
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
            push_log(&log_out, format!("[front door] {line}"));
        }
    });
    let log_err = log.clone();
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            push_log(&log_err, format!("[front door] {line}"));
        }
    });
    match rx.recv_timeout(Duration::from_secs(15)) {
        Ok(Ok(ready)) => Ok(FrontDoor { child, port: ready.port }),
        Ok(Err(e)) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(e.to_string())
        }
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            Err("it did not report a port within 15 seconds".into())
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
