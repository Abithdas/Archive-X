//! The instance lock: one holder per data folder, released when the holder goes, and the same lock Python takes (a Python process and
//! this crate exclude each other).

use ax_config::instance_lock::{acquire, LockError};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Command, Stdio};

fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap()
}

fn python() -> Option<&'static str> {
    ["python3", "python"].into_iter().find(|p| Command::new(p).arg("--version").output().map(|o| o.status.success()).unwrap_or(false))
}

#[test]
fn second_holder_is_refused_and_release_frees_it() {
    let dir = tempfile::tempdir().unwrap();
    let data = dir.path().join("made-on-demand");
    let first = acquire(&data).unwrap();
    assert!(first.path.ends_with(".instance.lock"));
    match acquire(&data) {
        Err(LockError::AlreadyRunning(e)) => assert_eq!(e.to_string(), format!("Archive X is already running with data folder {}", data.display())),
        other => panic!("expected AlreadyRunning, got {other:?}"),
    }
    drop(first);
    acquire(&data).expect("free again once the holder is gone");
}

#[test]
fn different_folders_do_not_interfere() {
    let (a, b) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
    let _x = acquire(a.path()).unwrap();
    let _y = acquire(b.path()).unwrap();
}

const PY_HOLD: &str = r#"
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from telegram_archive.core.instance_lock import acquire, AlreadyRunning
try:
    acquire(Path(sys.argv[2]))
except AlreadyRunning as exc:
    print("REFUSED " + str(exc), flush=True)
    sys.exit(0)
print("HELD", flush=True)
sys.stdin.readline()   # hold until told to go
"#;

#[test]
fn python_and_rust_exclude_each_other() {
    let Some(py) = python() else {
        eprintln!("no python on this machine: skipping");
        return;
    };
    let dir = tempfile::tempdir().unwrap();
    let data = dir.path().to_path_buf();
    let spawn = || {
        Command::new(py)
            .args(["-I", "-c", PY_HOLD, repo_root().to_str().unwrap(), data.to_str().unwrap()])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap()
    };
    let first_line = |child: &mut std::process::Child| {
        let mut line = String::new();
        BufReader::new(child.stdout.as_mut().unwrap()).read_line(&mut line).unwrap();
        line.trim().to_string()
    };

    // Python holds the folder: Rust is refused.
    let mut holder = spawn();
    assert_eq!(first_line(&mut holder), "HELD");
    assert!(matches!(acquire(&data), Err(LockError::AlreadyRunning(_))));
    holder.stdin.as_mut().unwrap().write_all(b"\n").unwrap();
    holder.wait().unwrap();

    // Rust holds the folder: Python is refused, with the same words.
    let rust = acquire(&data).unwrap();
    let mut refused = spawn();
    assert_eq!(first_line(&mut refused), format!("REFUSED Archive X is already running with data folder {}", data.display()));
    refused.wait().unwrap();
    drop(rust);

    // Released: Python can take it.
    let mut again = spawn();
    assert_eq!(first_line(&mut again), "HELD");
    again.stdin.as_mut().unwrap().write_all(b"\n").unwrap();
    again.wait().unwrap();
}
