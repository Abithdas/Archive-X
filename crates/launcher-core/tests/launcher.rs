//! Tests for the launcher core. The first group uses tiny fake backends (shell scripts) and runs everywhere with a
//! POSIX shell; the last test drives the real frozen backend when ARCHIVE_X_BACKEND_EXE points at it.
use archive_x_launcher_core::*;
use std::time::{Duration, Instant};

#[test]
fn ready_line_is_parsed() {
    let r = parse_ready_line("ARCHIVE_X_READY {\"port\": 41267, \"pid\": 99, \"version\": \"v29.5.400\"}\n")
        .expect("is a ready line")
        .expect("valid json");
    assert_eq!(r, Ready { port: 41267, pid: 99, version: "v29.5.400".into() });
}

#[test]
fn other_lines_are_not_ready_lines() {
    assert!(parse_ready_line("  Archive X desktop backend v29.5.400").is_none());
    assert!(parse_ready_line("").is_none());
    assert!(parse_ready_line("ARCHIVE_X_READYX {}").is_none());
}

#[test]
fn malformed_ready_line_is_an_error_not_a_panic() {
    assert!(matches!(parse_ready_line("ARCHIVE_X_READY {nope"), Some(Err(LaunchError::BadReadyLine(_)))));
    assert!(matches!(parse_ready_line("ARCHIVE_X_READY {\"port\": 70000, \"pid\": 1}"), Some(Err(_))));
}

#[test]
fn secrets_are_64_hex_chars_and_unique() {
    let a = generate_secret().unwrap();
    let b = generate_secret().unwrap();
    assert_eq!(a.len(), 64);
    assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    assert_ne!(a, b);
}

#[cfg(unix)]
mod fake {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    /// Backend::spawn, retried while Linux says "Text file busy" (ETXTBSY): another test thread that forks at the moment a script is
    /// written holds the file open for a moment, and running it then fails. Not a launcher problem.
    fn spawn(opts: &LaunchOptions) -> Result<Backend, LaunchError> {
        for _ in 0..40 {
            match Backend::spawn(opts) {
                Err(LaunchError::Spawn(e)) if e.raw_os_error() == Some(26) => std::thread::sleep(Duration::from_millis(25)),
                other => return other,
            }
        }
        Backend::spawn(opts)
    }

    fn script(dir: &tempfile::TempDir, name: &str, body: &str) -> std::path::PathBuf {
        let p = dir.path().join(name);
        std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        p
    }

    #[test]
    fn spawn_reads_the_ready_line_and_passes_the_desktop_environment() {
        let dir = tempfile::tempdir().unwrap();
        // Prints the environment it received so the test can see it, then the ready line, then lingers.
        let exe = script(&dir, "ok.sh", r#"echo "booting"; echo "ARCHIVE_X_READY {\"port\": 4242, \"pid\": $$, \"version\": \"t\"}"; sleep 30"#);
        let mut b = spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_secs(10)), ..Default::default() }).unwrap();
        assert_eq!(b.port(), 4242);
        assert!(b.session_url().starts_with("http://127.0.0.1:4242/desktop/session?token="));
        assert_eq!(b.session_url().split("token=").nth(1).unwrap().len(), 64);
        assert!(b.is_running());
        // No HTTP server in the fake: shutdown must fall back to a kill, and say so.
        let t = Instant::now();
        assert!(!b.shutdown(Duration::from_millis(500)));
        assert!(t.elapsed() < Duration::from_secs(5));
        assert!(!b.is_running());
    }

    /// A fake backend that says it is ready, waits a moment, then ends with `status`.
    fn ends_with(status: i32) -> Backend {
        let dir = tempfile::tempdir().unwrap();
        let exe = script(&dir, "ends.sh", &format!(r#"echo "ARCHIVE_X_READY {{\"port\": 4243, \"pid\": $$, \"version\": \"t\"}}"; sleep 0.4; exit {status}"#));
        let b = spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_secs(10)), ..Default::default() }).unwrap();
        std::mem::forget(dir); // the script must outlive the call
        b
    }

    fn wait_until_ended(b: &mut Backend) {
        let t = Instant::now();
        while b.is_running() && t.elapsed() < Duration::from_secs(10) {
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    #[test]
    fn a_backend_that_stops_by_itself_with_success_is_a_requested_exit_not_a_crash() {
        let mut b = ends_with(0);
        assert_eq!(b.exited_cleanly(), None, "still running: nothing to say yet");
        wait_until_ended(&mut b);
        assert_eq!(b.exited_cleanly(), Some(true));
    }

    #[test]
    fn a_backend_that_ends_with_an_error_status_is_a_crash() {
        let mut b = ends_with(3);
        wait_until_ended(&mut b);
        assert_eq!(b.exited_cleanly(), Some(false));
    }

    #[test]
    fn a_backend_that_was_killed_is_a_crash() {
        let dir = tempfile::tempdir().unwrap();
        let exe = script(&dir, "lingers.sh", r#"echo "ARCHIVE_X_READY {\"port\": 4244, \"pid\": $$, \"version\": \"t\"}"; sleep 30"#);
        let mut b = spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_secs(10)), ..Default::default() }).unwrap();
        assert!(!b.shutdown(Duration::from_millis(300)), "no HTTP server in the fake: it is killed");
        assert_eq!(b.exited_cleanly(), Some(false));
    }

    #[test]
    fn environment_contains_secret_parent_pid_and_desktop_flag() {
        let dir = tempfile::tempdir().unwrap();
        let out = dir.path().join("env.txt");
        let exe = script(
            &dir,
            "env.sh",
            &format!(
                r#"echo "$ARCHIVE_X_DESKTOP|${{#ARCHIVE_X_LAUNCH_SECRET}}|$ARCHIVE_X_PARENT_PID|$ARCHIVE_X_TOOLS_DIR|$ARCHIVE_X_DATA_DIR|$ARCHIVE_X_DESKTOP_LANDING" > {}; echo "ARCHIVE_X_READY {{\"port\": 1, \"pid\": 1}}"; sleep 30"#,
                out.display()
            ),
        );
        let _b = spawn(&LaunchOptions {
            exe,
            data_dir: Some("/tmp/axdata".into()),
            tools_dir: Some("/tmp/axtools".into()),
            landing: Some("/app-next/".into()),
            ready_timeout: Some(Duration::from_secs(10)),
            ..Default::default()
        })
        .unwrap();
        let env = std::fs::read_to_string(out).unwrap();
        let parts: Vec<&str> = env.trim().split('|').collect();
        assert_eq!(parts[0], "1");
        assert_eq!(parts[1], "64");
        assert_eq!(parts[2], std::process::id().to_string());
        assert_eq!(parts[3], "/tmp/axtools");
        assert_eq!(parts[4], "/tmp/axdata");
        assert_eq!(parts[5], "/app-next/");
    }

    #[test]
    fn backend_that_exits_early_reports_its_output() {
        let dir = tempfile::tempdir().unwrap();
        let exe = script(&dir, "crash.sh", r#"echo "Traceback: boom" >&2; exit 3"#);
        match spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_secs(10)), ..Default::default() }) {
            Err(LaunchError::Exited(code, log)) => {
                assert_eq!(code, Some(3));
                assert!(log.iter().any(|l| l.contains("boom")), "log was {log:?}");
            }
            other => panic!("expected Exited, got {:?}", other.map(|_| "a running backend")),
        }
    }

    #[test]
    fn backend_that_never_becomes_ready_times_out_and_is_killed() {
        let dir = tempfile::tempdir().unwrap();
        let exe = script(&dir, "hang.sh", "echo starting; sleep 60");
        let t = Instant::now();
        let res = spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_millis(700)), ..Default::default() });
        assert!(matches!(res, Err(LaunchError::Timeout(..))));
        assert!(t.elapsed() < Duration::from_secs(8));
    }

    #[test]
    fn missing_executable_is_a_clear_error() {
        let res = spawn(&LaunchOptions { exe: "/nonexistent/archive-x-backend".into(), ..Default::default() });
        assert!(matches!(res, Err(LaunchError::Spawn(_))));
    }

    #[test]
    fn a_front_door_goes_in_front_and_shares_one_key_with_the_backend() {
        let dir = tempfile::tempdir().unwrap();
        let keys = dir.path().join("keys.txt");
        let args = dir.path().join("args.txt");
        let backend = script(
            &dir,
            "backend.sh",
            &format!(
                r#"echo "$ARCHIVE_X_PROXY_KEY" > {k}; echo "ARCHIVE_X_READY {{\"port\": 4242, \"pid\": $$, \"version\": \"t\"}}"; sleep 5"#,
                k = keys.display()
            ),
        );
        let front = script(
            &dir,
            "front.sh",
            &format!(
                r#"echo "$ARCHIVE_X_PROXY_KEY" >> {k}; echo "$@" > {a}; echo "ARCHIVE_X_READY {{\"port\": 5151, \"pid\": $$}}"; sleep 5"#,
                k = keys.display(),
                a = args.display()
            ),
        );
        let dist = dir.path().join("dist");
        let mut b = spawn(&LaunchOptions {
            exe: backend,
            ready_timeout: Some(Duration::from_secs(10)),
            front_door: Some(FrontDoorOptions { exe: front, dist_dir: dist.clone() }),
            ..Default::default()
        })
        .unwrap();
        assert_eq!(b.ready.port, 4242, "the backend keeps its own port");
        assert_eq!(b.port(), 5151, "the window opens the front door");
        assert!(b.session_url().starts_with("http://127.0.0.1:5151/desktop/session?token="));
        std::thread::sleep(Duration::from_millis(300));
        let keys: Vec<String> = std::fs::read_to_string(&keys).unwrap().lines().map(String::from).collect();
        assert_eq!(keys.len(), 2, "both processes were given a key");
        assert_eq!(keys[0], keys[1], "the same key");
        assert_eq!(keys[0].len(), 64);
        assert_ne!(keys[0], b.session_url().split("token=").nth(1).unwrap(), "the proxy key is not the launch secret");
        let args = std::fs::read_to_string(&args).unwrap();
        assert!(args.contains("--upstream 127.0.0.1:4242") && args.contains("--upstream-plain") && args.contains("--print-ready"), "{args}");
        assert!(args.contains(&format!("--dist {}", dist.display())), "{args}");
        assert!(b.is_running());
        assert!(!b.shutdown(Duration::from_millis(300)), "the fake backend has no HTTP server, so it is killed");
        assert!(!b.is_running(), "backend and front door are both stopped");
    }

    #[test]
    fn without_a_front_door_nothing_changes() {
        let dir = tempfile::tempdir().unwrap();
        let exe = script(
            &dir,
            "plain.sh",
            r#"[ -z "$ARCHIVE_X_PROXY_KEY" ] || echo "UNEXPECTED KEY" >&2; echo "ARCHIVE_X_READY {\"port\": 4343, \"pid\": $$}"; sleep 3"#,
        );
        let mut b = spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_secs(10)), ..Default::default() }).unwrap();
        assert_eq!(b.port(), 4343);
        std::thread::sleep(Duration::from_millis(200));
        assert!(!b.log_tail().iter().any(|l| l.contains("UNEXPECTED KEY")), "the backend must not be given a proxy key");
        b.shutdown(Duration::from_millis(200));
    }

    #[test]
    fn a_front_door_that_cannot_start_falls_back_to_the_backend() {
        let dir = tempfile::tempdir().unwrap();
        let backend = script(&dir, "backend.sh", r#"echo "ARCHIVE_X_READY {\"port\": 4444, \"pid\": $$}"; sleep 3"#);
        for (name, front) in [("missing", dir.path().join("nope")), ("exits", script(&dir, "dies.sh", "echo broken >&2; exit 3"))] {
            let mut b = spawn(&LaunchOptions {
                exe: backend.clone(),
                ready_timeout: Some(Duration::from_secs(10)),
                front_door: Some(FrontDoorOptions { exe: front, dist_dir: dir.path().to_path_buf() }),
                ..Default::default()
            })
            .unwrap();
            assert_eq!(b.port(), 4444, "{name}: the app must still open through the backend");
            assert!(b.log_tail().iter().any(|l| l.contains("front door not started")), "{name}: the reason is logged: {:?}", b.log_tail());
            assert!(b.is_running(), "{name}");
            b.shutdown(Duration::from_millis(200));
        }
    }
}

/// Real frozen backend: ARCHIVE_X_BACKEND_EXE=/path/to/archive-x-backend cargo test -- --ignored
#[test]
#[ignore]
fn real_frozen_backend_end_to_end() {
    let exe = std::env::var("ARCHIVE_X_BACKEND_EXE").expect("set ARCHIVE_X_BACKEND_EXE to the frozen backend");
    let data = std::env::temp_dir().join(format!("axlauncher_{}", std::process::id()));
    std::fs::create_dir_all(&data).unwrap();
    let started = Instant::now();
    let mut b = Backend::spawn(&LaunchOptions { exe: exe.into(), data_dir: Some(data.clone()), ..Default::default() }).expect("backend should start");
    println!("ready in {:.1}s on port {} (version {})", started.elapsed().as_secs_f32(), b.port(), b.ready.version);
    assert!(b.healthy(), "health endpoint");
    // Guard: the API refuses a request without the secret.
    let (status, _) = http_request(b.port(), "GET", "/api/status", &[], Duration::from_secs(5)).unwrap();
    assert_eq!(status, 403);
    // The one-time session URL answers with the hand-off page (and sets the cookies).
    let path = b.session_url().replace(&format!("http://127.0.0.1:{}", b.port()), "");
    let (status, body) = http_request(b.port(), "GET", &path, &[], Duration::from_secs(5)).unwrap();
    assert_eq!(status, 200);
    assert!(body.contains("location.replace"), "hand-off page body was {body:?}");
    // Graceful shutdown: exits on its own, no kill.
    let t = Instant::now();
    assert!(b.shutdown(Duration::from_secs(30)), "backend should exit gracefully");
    println!("graceful shutdown took {:.1}s", t.elapsed().as_secs_f32());
    let _ = std::fs::remove_dir_all(data);
}

/// Real backend with the Rust front door in front of it:
/// ARCHIVE_X_BACKEND_EXE=... ARCHIVE_X_FRONT_DOOR_EXE=... ARCHIVE_X_DIST=apps/web/dist cargo test -- --ignored real_backend_with_front_door
#[test]
#[ignore]
fn real_backend_with_front_door_end_to_end() {
    let exe = std::env::var("ARCHIVE_X_BACKEND_EXE").expect("set ARCHIVE_X_BACKEND_EXE");
    let front = std::env::var("ARCHIVE_X_FRONT_DOOR_EXE").expect("set ARCHIVE_X_FRONT_DOOR_EXE to the ax-server binary");
    let dist = std::env::var("ARCHIVE_X_DIST").expect("set ARCHIVE_X_DIST to the built web app");
    let data = std::env::temp_dir().join(format!("axfront_{}", std::process::id()));
    std::fs::create_dir_all(&data).unwrap();
    let mut b = Backend::spawn(&LaunchOptions {
        exe: exe.into(),
        data_dir: Some(data.clone()),
        front_door: Some(FrontDoorOptions { exe: front.into(), dist_dir: dist.into() }),
        ..Default::default()
    })
    .expect("backend and front door should start");
    assert_ne!(b.port(), b.ready.port, "the window must be pointed at the front door, not the backend");
    assert!(b.healthy(), "health through the front door");
    // The web app is served by the front door itself.
    let (status, body) = http_request(b.port(), "GET", "/photos", &[], Duration::from_secs(5)).unwrap();
    assert_eq!(status, 200);
    assert!(body.contains("<div id=\"root\">"), "app shell: {body:.200}");
    // The backend's own guard still applies to requests that arrive through the front door.
    let (status, _) = http_request(b.port(), "GET", "/api/status", &[], Duration::from_secs(5)).unwrap();
    assert_eq!(status, 403);
    // The one-time session hand-off works through it.
    let path = b.session_url().replace(&format!("http://127.0.0.1:{}", b.port()), "");
    let (status, body) = http_request(b.port(), "GET", &path, &[], Duration::from_secs(5)).unwrap();
    assert_eq!(status, 200);
    assert!(body.contains("location.replace"), "hand-off page body was {body:?}");
    assert!(b.shutdown(Duration::from_secs(30)), "backend should exit gracefully");
    assert!(!b.is_running());
    let _ = std::fs::remove_dir_all(data);
}
