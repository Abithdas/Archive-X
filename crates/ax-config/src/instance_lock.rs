//! One Archive X process per data folder (`core/instance_lock.py`).
//!
//! The lock is an operating-system file lock held for the life of the process and released by the system if the process dies, so a crash
//! never leaves a stale lock. On Windows the locked byte is far beyond the end of the file, so backup and data-move code can still read the
//! (empty) file. It is the same lock Python takes: a Python and a Rust process on one folder exclude each other.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub struct AlreadyRunning(pub String);

impl std::fmt::Display for AlreadyRunning {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for AlreadyRunning {}

#[derive(Debug)]
pub enum LockError {
    AlreadyRunning(AlreadyRunning),
    Io(io::Error),
}

impl std::fmt::Display for LockError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LockError::AlreadyRunning(e) => e.fmt(f),
            LockError::Io(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for LockError {}

/// The held lock; it is released when this is dropped (or the process ends).
#[derive(Debug)]
pub struct InstanceLock {
    _file: File,
    pub path: PathBuf,
}

#[cfg(unix)]
fn try_lock(file: &File) -> io::Result<bool> {
    use std::os::fd::AsRawFd;
    // SAFETY: flock on a descriptor this function's caller owns.
    let rc = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if rc == 0 {
        return Ok(true);
    }
    let err = io::Error::last_os_error();
    if err.kind() == io::ErrorKind::WouldBlock {
        Ok(false)
    } else {
        Err(err)
    }
}

#[cfg(windows)]
fn try_lock(file: &File) -> io::Result<bool> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::{ERROR_LOCK_VIOLATION, HANDLE};
    use windows_sys::Win32::Storage::FileSystem::{LockFileEx, LOCKFILE_EXCLUSIVE_LOCK, LOCKFILE_FAIL_IMMEDIATELY};
    use windows_sys::Win32::System::IO::OVERLAPPED;
    // One byte at offset 1 << 30, the byte Python locks with msvcrt.locking.
    // SAFETY: the OVERLAPPED is zeroed and lives through the call; the handle belongs to `file`.
    unsafe {
        let mut overlapped: OVERLAPPED = std::mem::zeroed();
        overlapped.Anonymous.Anonymous.Offset = 1 << 30;
        let ok = LockFileEx(file.as_raw_handle() as HANDLE, LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &mut overlapped);
        if ok != 0 {
            return Ok(true);
        }
        let err = io::Error::last_os_error();
        if err.raw_os_error() == Some(ERROR_LOCK_VIOLATION as i32) {
            Ok(false)
        } else {
            Err(err)
        }
    }
}

/// Take the lock of `data_dir` (`.instance.lock` inside it), making the folder if needed.
pub fn acquire(data_dir: &Path) -> Result<InstanceLock, LockError> {
    std::fs::create_dir_all(data_dir).map_err(LockError::Io)?;
    let path = data_dir.join(".instance.lock");
    let file = OpenOptions::new().read(true).append(true).create(true).open(&path).map_err(LockError::Io)?;
    match try_lock(&file) {
        Ok(true) => Ok(InstanceLock { _file: file, path }),
        Ok(false) => Err(LockError::AlreadyRunning(AlreadyRunning(format!("Archive X is already running with data folder {}", data_dir.display())))),
        Err(e) => Err(LockError::Io(e)),
    }
}
