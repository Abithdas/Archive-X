//! Port of `telegram_archive/core/safe_paths.py`: turn "a folder plus a name that came from a request" into a path that cannot leave
//! the folder.
//!
//! Python's `os.path.realpath` is not strict: it resolves symlinks in the part of the path that exists and keeps the rest as written.
//! `std::fs::canonicalize` fails on missing paths, so `realpath` below follows `os.path.realpath`'s algorithm.
//!
//! One deliberate difference: Python checks `candidate.startswith(root + os.sep)`, which wrongly rejects every path when `root` is
//! the filesystem root (`//`). Here the check is by path components, so the root folder contains everything under it.

use std::ffi::OsString;
use std::io;
use std::path::{Component, Path, PathBuf};

const MAX_LINKS: usize = 40;

#[derive(Debug, PartialEq, Eq)]
pub struct EscapesFolder;

impl std::fmt::Display for EscapesFolder {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("path escapes its folder")
    }
}
impl std::error::Error for EscapesFolder {}

/// `os.path.realpath(path)`: absolute, `..` and `.` removed, symlinks resolved as far as the path exists. Never fails on missing parts.
pub fn realpath(path: &Path) -> io::Result<PathBuf> {
    let abs = if path.is_absolute() { path.to_path_buf() } else { std::env::current_dir()?.join(path) };
    let mut done = PathBuf::new();
    let mut pending: Vec<OsString> = Vec::new();
    for c in abs.components() {
        match c {
            Component::Prefix(p) => done.push(p.as_os_str()),
            Component::RootDir => done.push(Component::RootDir.as_os_str()),
            other => pending.push(other.as_os_str().to_os_string()),
        }
    }
    pending.reverse(); // pop() takes the next component
    let mut links = 0usize;
    while let Some(name) = pending.pop() {
        if name == "." {
            continue;
        }
        if name == ".." {
            // `..` of the root is the root; otherwise drop the last resolved component
            if done.parent().is_some() && done.file_name().is_some() {
                done.pop();
            }
            continue;
        }
        let next = done.join(&name);
        match std::fs::symlink_metadata(&next) {
            Ok(m) if m.file_type().is_symlink() => {
                links += 1;
                if links > MAX_LINKS {
                    // like Python: a loop is left unresolved, the rest is appended as written
                    done = next;
                    while let Some(rest) = pending.pop() {
                        done.push(rest);
                    }
                    break;
                }
                let target = std::fs::read_link(&next)?;
                if target.is_absolute() {
                    done = PathBuf::new();
                }
                let mut comps: Vec<OsString> = Vec::new();
                for c in target.components() {
                    match c {
                        Component::Prefix(p) => done.push(p.as_os_str()),
                        Component::RootDir => done.push(Component::RootDir.as_os_str()),
                        other => comps.push(other.as_os_str().to_os_string()),
                    }
                }
                for comp in comps.into_iter().rev() {
                    pending.push(comp);
                }
            }
            _ => done = next, // a normal entry, or one that does not exist (kept as written)
        }
    }
    Ok(done)
}

/// `base / parts`, resolved; `Err` when the result is not inside `base` (`..`, absolute parts, links out).
pub fn inside<I, S>(base: &Path, parts: I) -> Result<PathBuf, EscapesFolder>
where
    I: IntoIterator<Item = S>,
    S: AsRef<Path>,
{
    let root = realpath(base).map_err(|_| EscapesFolder)?;
    let mut joined = root.clone();
    for p in parts {
        joined.push(p.as_ref()); // an absolute part replaces everything so far, like os.path.join
    }
    let candidate = realpath(&joined).map_err(|_| EscapesFolder)?;
    if candidate == root || candidate.starts_with(&root) {
        Ok(candidate)
    } else {
        Err(EscapesFolder)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stays_inside_and_refuses_escapes() {
        let dir = tempfile::tempdir().unwrap();
        let base = dir.path();
        let root = realpath(base).unwrap();
        assert_eq!(inside(base, ["12_34", "audio_1.m4a"]).unwrap(), root.join("12_34").join("audio_1.m4a"));
        for bad in ["../x", "a/../../x", "/etc/passwd"] {
            assert_eq!(inside(base, [bad]), Err(EscapesFolder), "{bad}");
        }
    }

    #[test]
    fn the_folder_itself_is_inside() {
        let dir = tempfile::tempdir().unwrap();
        let root = realpath(dir.path()).unwrap();
        assert_eq!(inside(dir.path(), ["."]).unwrap(), root);
        assert_eq!(inside(dir.path(), Vec::<&str>::new()).unwrap(), root);
        assert_eq!(inside(dir.path(), ["a/../b"]).unwrap(), root.join("b"));
    }

    #[test]
    fn a_sibling_with_the_same_prefix_is_outside() {
        let dir = tempfile::tempdir().unwrap();
        let base = dir.path().join("lib");
        std::fs::create_dir(&base).unwrap();
        std::fs::create_dir(dir.path().join("lib2")).unwrap();
        assert_eq!(inside(&base, ["../lib2/x"]), Err(EscapesFolder));
    }

    #[cfg(unix)]
    #[test]
    fn a_link_out_of_the_folder_is_refused_and_a_link_inside_is_followed() {
        let dir = tempfile::tempdir().unwrap();
        let base = dir.path().join("lib");
        let outside = dir.path().join("secret");
        std::fs::create_dir(&base).unwrap();
        std::fs::create_dir(&outside).unwrap();
        std::fs::create_dir(base.join("real")).unwrap();
        std::os::unix::fs::symlink(&outside, base.join("out")).unwrap();
        std::os::unix::fs::symlink(base.join("real"), base.join("in")).unwrap();
        assert_eq!(inside(&base, ["out", "x"]), Err(EscapesFolder));
        let root = realpath(&base).unwrap();
        assert_eq!(inside(&base, ["in", "x"]).unwrap(), root.join("real").join("x"));
    }

    #[cfg(unix)]
    #[test]
    fn a_link_loop_does_not_hang() {
        let dir = tempfile::tempdir().unwrap();
        std::os::unix::fs::symlink(dir.path().join("b"), dir.path().join("a")).unwrap();
        std::os::unix::fs::symlink(dir.path().join("a"), dir.path().join("b")).unwrap();
        assert!(realpath(&dir.path().join("a").join("x")).is_ok());
    }
}
