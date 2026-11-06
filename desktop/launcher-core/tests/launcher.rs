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
        let mut b = Backend::spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_secs(10)), ..Default::default() }).unwrap();
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
        let _b = Backend::spawn(&LaunchOptions {
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
        match Backend::spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_secs(10)), ..Default::default() }) {
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
        let res = Backend::spawn(&LaunchOptions { exe, ready_timeout: Some(Duration::from_millis(700)), ..Default::default() });
        assert!(matches!(res, Err(LaunchError::Timeout(..))));
        assert!(t.elapsed() < Duration::from_secs(8));
    }

    #[test]
    fn missing_executable_is_a_clear_error() {
        let res = Backend::spawn(&LaunchOptions { exe: "/nonexistent/archive-x-backend".into(), ..Default::default() });
        assert!(matches!(res, Err(LaunchError::Spawn(_))));
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
    let mut b = Backend::spawn(&LaunchOptions { exe: exe.into(), data_dir: Some(data.clone()), ..Default::default() })
        .expect("backend should start");
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
