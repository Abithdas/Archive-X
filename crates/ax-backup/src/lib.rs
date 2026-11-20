//! Backups: `core/backup/manager.py` (create, inspect, list, retention, staged restore) and its file formats.
//!
//! A `.axbackup` file is a ZIP with a `BACKUP_MANIFEST.json`, optionally wrapped in an encrypted container (`AXBK1` header, Argon2id key,
//! AES-256-GCM). The two implementations read each other's files: Python's backups are inspected and staged here, and the ones made here pass
//! Python's `inspect_backup` (checked in CI by `tools/parity/check_rust_backup.py`). Settings (repository folder, retention counts, the
//! last-success and last-error notes) belong to the caller: this crate takes plain values.

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

use aes_gcm::aead::{AeadInPlace, KeyInit};
use aes_gcm::{Aes256Gcm, Nonce};
use ax_core::vault::{protected_purpose, MAGIC as VAULT_MAGIC};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

pub const BACKUP_FORMAT: &str = "archive-x-backup";
pub const BACKUP_FORMAT_VERSION: i64 = 1;
pub const MAGIC: &[u8] = b"AXBK1\n";
pub const DEFAULT_REPOSITORY_NAME: &str = "Archive X Backups";
pub const BACKUP_ROOTS: [&str; 10] = ["database", "auth", "assets", "metadata", "ai", "automation", "state", "logs", "settings.json", "PERSISTENT_DATA_MANIFEST.json"];
const EXCLUDED_DIRS: [&str; 3] = ["cache", "backups", ".restore_staging"];
const SIDECAR_SUFFIXES: [&str; 3] = ["-wal", "-shm", "-journal"];

/// An error with the text Python's `ValueError` / `VaultError` carries.
#[derive(Debug, PartialEq, Eq)]
pub struct BackupError(pub String);

impl std::fmt::Display for BackupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl From<std::io::Error> for BackupError {
    fn from(e: std::io::Error) -> Self {
        BackupError(e.to_string())
    }
}

type R<T> = Result<T, BackupError>;

fn err<T>(msg: &str) -> R<T> {
    Err(BackupError(msg.to_string()))
}

/// `unseal(payload, purpose)`: succeeds only when the vault key opens the protected file.
pub type UnsealFn<'a> = dyn Fn(&[u8], &str) -> Result<(), String> + 'a;

/// What the caller supplies: the application's identity, the clock, and the vault check for protected files.
pub struct Env<'a> {
    pub app_version: &'a str,
    pub data_manifest_version: i64,
    /// `_now()`: UTC, `2026-11-20T11:19:23+00:00`.
    pub now_utc: &'a dyn Fn() -> String,
    /// `datetime.now().strftime("%Y%m%d_%H%M%S")` (local time).
    pub file_stamp: &'a dyn Fn() -> String,
    pub unseal: &'a UnsealFn<'a>,
}

pub fn default_repository(user_data_dir: &Path) -> PathBuf {
    user_data_dir.parent().unwrap_or(Path::new(".")).join(DEFAULT_REPOSITORY_NAME)
}

fn to_hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn from_hex(s: &str) -> Option<Vec<u8>> {
    if !s.len().is_multiple_of(2) {
        return None;
    }
    (0..s.len() / 2).map(|i| u8::from_str_radix(s.get(2 * i..2 * i + 2)?, 16).ok()).collect()
}

/// `_iter_files`: the files a backup holds, with their path relative to the data folder.
pub fn iter_files(root: &Path) -> Vec<(PathBuf, PathBuf)> {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(rd) = fs::read_dir(dir) else { return };
        let mut entries: Vec<_> = rd.filter_map(|e| e.ok()).collect();
        entries.sort_by_key(|e| e.file_name());
        for e in entries {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
            } else if p.is_file() {
                out.push(p);
            }
        }
    }
    let mut out = Vec::new();
    for base in BACKUP_ROOTS {
        let path = root.join(base);
        if path.is_file() {
            out.push((path, PathBuf::from(base)));
        } else if path.is_dir() {
            let mut files = Vec::new();
            walk(&path, &mut files);
            for p in files {
                let rel = p.strip_prefix(root).unwrap().to_path_buf();
                if rel.components().any(|c| EXCLUDED_DIRS.contains(&c.as_os_str().to_string_lossy().as_ref())) {
                    continue;
                }
                if SIDECAR_SUFFIXES.iter().any(|s| p.file_name().is_some_and(|n| n.to_string_lossy().ends_with(s))) {
                    continue;
                }
                out.push((p, rel));
            }
        }
    }
    out
}

pub fn sha256_file(path: &Path) -> std::io::Result<String> {
    let mut h = Sha256::new();
    let mut f = File::open(path)?;
    let mut buf = vec![0u8; 1024 * 1024];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(to_hex(&h.finalize()))
}

/// `_derive_key`: Argon2id, 3 passes, 64 MiB, 2 lanes, 32 bytes.
///
/// The key is taken from the hash `hash_password` returns, not from a buffer made here for the library to fill.
pub fn derive_key(password: &str, salt: &[u8]) -> R<[u8; 32]> {
    use argon2::password_hash::{PasswordHasher, SaltString};
    let params = argon2::Params::new(64 * 1024, 3, 2, Some(32)).map_err(|e| BackupError(e.to_string()))?;
    let a2 = argon2::Argon2::new(argon2::Algorithm::Argon2id, argon2::Version::V0x13, params);
    let salt = SaltString::encode_b64(salt).map_err(|e| BackupError(e.to_string()))?;
    let hash = a2.hash_password(password.as_bytes(), &salt).map_err(|e| BackupError(e.to_string()))?;
    let out = hash.hash.ok_or_else(|| BackupError("key derivation gave no output".into()))?;
    out.as_bytes().try_into().map_err(|_| BackupError("key derivation gave the wrong length".into()))
}

/// `_encrypt_file`: header, ciphertext, tag. (The whole file is held in memory, where Python streams it in 1 MiB blocks.)
pub fn encrypt_file(src: &Path, dst: &Path, password: &str, salt: &[u8], nonce: &[u8; 12], created_at: &str) -> R<()> {
    let key = derive_key(password, salt)?;
    let cipher = Aes256Gcm::new_from_slice(&key).map_err(|e| BackupError(e.to_string()))?;
    let mut data = fs::read(src)?;
    let tag = cipher.encrypt_in_place_detached(Nonce::from_slice(nonce), b"", &mut data).map_err(|_| BackupError("encryption failed".into()))?;
    if let Some(parent) = dst.parent() {
        fs::create_dir_all(parent)?;
    }
    let header = json!({"format": BACKUP_FORMAT, "version": BACKUP_FORMAT_VERSION, "encrypted": true, "kdf": "argon2id", "salt": to_hex(salt), "nonce": to_hex(nonce), "created_at": created_at});
    let raw = serde_json::to_vec(&header).unwrap(); // compact: separators (",", ":")
    let mut out = File::create(dst)?;
    out.write_all(MAGIC)?;
    out.write_all(&(raw.len() as u32).to_be_bytes())?;
    out.write_all(&raw)?;
    out.write_all(&data)?;
    out.write_all(&tag)?;
    Ok(())
}

/// `_decrypt_file`: the plain ZIP bytes and the header.
pub fn decrypt_file(src: &Path, password: &str) -> R<(Vec<u8>, Value)> {
    let bytes = fs::read(src)?;
    if bytes.len() < MAGIC.len() + 4 || &bytes[..MAGIC.len()] != MAGIC {
        return err("Invalid Archive X encrypted backup header");
    }
    let size = u32::from_be_bytes(bytes[MAGIC.len()..MAGIC.len() + 4].try_into().unwrap()) as usize;
    let start = MAGIC.len() + 4;
    let header: Value = bytes
        .get(start..start + size)
        .and_then(|h| serde_json::from_slice(h).ok())
        .ok_or_else(|| BackupError("Invalid Archive X encrypted backup header".into()))?;
    let salt = header
        .get("salt")
        .and_then(Value::as_str)
        .and_then(from_hex)
        .ok_or_else(|| BackupError("Invalid Archive X encrypted backup header".into()))?;
    let nonce = header
        .get("nonce")
        .and_then(Value::as_str)
        .and_then(from_hex)
        .filter(|n| n.len() == 12)
        .ok_or_else(|| BackupError("Invalid Archive X encrypted backup header".into()))?;
    let key = derive_key(password, &salt)?;
    let body = &bytes[start + size..];
    if body.len() < 16 {
        return err("Encrypted backup is incomplete");
    }
    let (cipher_text, tag) = body.split_at(body.len() - 16);
    let mut buf = cipher_text.to_vec();
    let cipher = Aes256Gcm::new_from_slice(&key).map_err(|e| BackupError(e.to_string()))?;
    cipher
        .decrypt_in_place_detached(Nonce::from_slice(&nonce), b"", &mut buf, aes_gcm::Tag::from_slice(tag))
        .map_err(|_| BackupError("Backup password is incorrect or the backup is corrupted".into()))?;
    Ok((buf, header))
}

fn is_encrypted(path: &Path) -> std::io::Result<bool> {
    let mut head = vec![0u8; MAGIC.len()];
    let n = File::open(path)?.read(&mut head)?;
    Ok(&head[..n] == MAGIC)
}

/// The ZIP bytes of a backup file: itself, or what its encrypted container holds.
fn open_archive(path: &Path, password: Option<&str>) -> R<Vec<u8>> {
    if is_encrypted(path)? {
        let Some(password) = password.filter(|p| !p.is_empty()) else { return err("This backup is encrypted; a backup password is required") };
        return Ok(decrypt_file(path, password)?.0);
    }
    Ok(fs::read(path)?)
}

/// `Path.resolve()` for a path that may not exist: absolute, `.` and `..` removed, symlinks of the existing part followed.
pub fn absolute(p: &Path) -> PathBuf {
    let abs = if p.is_absolute() { p.to_path_buf() } else { std::env::current_dir().unwrap_or_default().join(p) };
    let mut out = PathBuf::new();
    for c in abs.components() {
        match c {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    // follow symlinks for the part that exists
    let mut existing = out.clone();
    let mut tail = Vec::new();
    while !existing.exists() {
        match existing.file_name() {
            Some(n) => tail.push(n.to_os_string()),
            None => break,
        }
        existing.pop();
    }
    let mut real = fs::canonicalize(&existing).unwrap_or(existing);
    for t in tail.into_iter().rev() {
        real.push(t);
    }
    real
}

/// `inspect_backup`: validate a backup and return `{ok, manifest, size, encrypted}`.
pub fn inspect_backup(path: &Path, password: Option<&str>, env: &Env) -> R<Value> {
    let src = absolute(path);
    if !src.is_file() {
        return err("Backup file does not exist");
    }
    let encrypted = is_encrypted(&src)?;
    let bytes = open_archive(&src, password)?;
    let mut zf = zip::ZipArchive::new(std::io::Cursor::new(bytes)).map_err(|_| BackupError("Backup archive is corrupted".into()))?;
    // `testzip`: every member must read to the end with a good checksum
    for i in 0..zf.len() {
        let mut member = zf.by_index(i).map_err(|_| BackupError("Backup archive is corrupted".into()))?;
        let mut sink = std::io::sink();
        if std::io::copy(&mut member, &mut sink).is_err() {
            return err("Backup archive checksum test failed");
        }
    }
    let mut raw = Vec::new();
    zf.by_name("BACKUP_MANIFEST.json")
        .map_err(|_| BackupError("There is no item named 'BACKUP_MANIFEST.json' in the archive".into()))?
        .read_to_end(&mut raw)?;
    let manifest: Value = serde_json::from_slice(&raw).map_err(|e| BackupError(e.to_string()))?;
    if manifest.get("format").and_then(Value::as_str) != Some(BACKUP_FORMAT) {
        return err("Unsupported backup format");
    }
    if manifest.get("format_version").and_then(Value::as_i64).unwrap_or(0) != BACKUP_FORMAT_VERSION {
        return err("Unsupported backup format version");
    }
    let names: BTreeSet<String> = zf.file_names().filter_map(|n| n.ok()).map(|n| n.into_owned()).collect();
    for name in &names {
        if let Some(purpose) = protected_purpose(name) {
            let mut payload = Vec::new();
            zf.by_name(name).map_err(|e| BackupError(e.to_string()))?.read_to_end(&mut payload)?;
            if payload.starts_with(VAULT_MAGIC) {
                (env.unseal)(&payload, purpose).map_err(BackupError)?;
            }
        }
    }
    let files = manifest.get("files").and_then(Value::as_array).cloned().unwrap_or_default();
    let missing = files.iter().filter(|x| !x.get("path").and_then(Value::as_str).is_some_and(|p| names.contains(p))).count();
    if missing > 0 {
        return Err(BackupError(format!("Backup is missing {missing} manifest file(s)")));
    }
    Ok(json!({"ok": true, "manifest": manifest, "size": fs::metadata(&src)?.len(), "encrypted": encrypted}))
}

fn zip_options(size: u64, mtime: Option<std::time::SystemTime>) -> zip::write::SimpleFileOptions {
    let mut o = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated)
        .compression_level(Some(6))
        .large_file(size >= u32::MAX as u64);
    if let Some(t) = mtime {
        if let Ok(dt) = zip::DateTime::try_from(time_of(t)) {
            o = o.last_modified_time(dt);
        }
    }
    o
}

/// A system time as the civil date a ZIP header stores.
fn time_of(t: std::time::SystemTime) -> TimeParts {
    let secs = t.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    TimeParts(secs)
}

struct TimeParts(i64);

impl TryFrom<TimeParts> for zip::DateTime {
    type Error = ();
    fn try_from(t: TimeParts) -> Result<Self, ()> {
        let days = t.0.div_euclid(86_400);
        let rem = t.0.rem_euclid(86_400);
        let z = days + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z.rem_euclid(146_097);
        let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        let y = if m <= 2 { y + 1 } else { y };
        zip::DateTime::from_date_and_time(y as u16, m as u8, d as u8, (rem / 3600) as u8, ((rem % 3600) / 60) as u8, (rem % 60) as u8).map_err(|_| ())
    }
}

/// `create_backup` without the settings and retention steps: writes and verifies the archive, returns `{ok, path, size, manifest, verified}`.
pub fn create_backup(user_data_dir: &Path, repo: &Path, backup_type: &str, password: Option<&str>, env: &Env) -> R<Value> {
    let root = absolute(user_data_dir);
    let repo = absolute(repo);
    if repo == root || repo.starts_with(&root) {
        return err("Backup repository cannot be inside Archive X data");
    }
    fs::create_dir_all(&repo)?;
    if backup_type != "configuration" && backup_type != "full_application" {
        return err("Unsupported backup type");
    }
    let stamp = (env.file_stamp)();
    let final_path = repo.join(format!("Archive_X_Backup_{stamp}.axbackup"));
    let tmp_zip = repo.join(format!(".Archive_X_Backup_{stamp}.zip.tmp"));
    let mut manifest = Map::new();
    for (k, v) in [
        ("format", json!(BACKUP_FORMAT)),
        ("format_version", json!(BACKUP_FORMAT_VERSION)),
        ("application", json!("Archive X")),
        ("application_version", json!(env.app_version)),
        ("data_manifest_version", json!(env.data_manifest_version)),
        ("created_at", json!((env.now_utc)())),
        ("backup_type", json!(backup_type)),
        ("media_included", json!(false)),
        ("cache_included", json!(false)),
        ("encrypted", json!(password.is_some_and(|p| !p.is_empty()))),
        ("protected_credentials", json!("AES-256-GCM; original vault environment secret required")),
        ("checksum_algorithm", json!("sha256")),
    ] {
        manifest.insert(k.into(), v);
    }
    let base = manifest;
    let build = || -> R<Map<String, Value>> {
        let mut files: Vec<Value> = Vec::new();
        let mut manifest = base.clone();
        let file = File::create(&tmp_zip)?;
        let mut zw = zip::ZipWriter::new(file);
        for (src, rel) in iter_files(&root) {
            let first = rel.components().next().map(|c| c.as_os_str().to_string_lossy().into_owned()).unwrap_or_default();
            if backup_type == "configuration" && !["settings.json", "PERSISTENT_DATA_MANIFEST.json", "database", "auth"].contains(&first.as_str()) {
                continue;
            }
            let arc = rel.to_string_lossy().replace('\\', "/");
            let meta = fs::metadata(&src)?;
            if arc == "database/archive.db" {
                let snap = tempfile::Builder::new().prefix("archive_x_db_snapshot_").suffix(".db").tempfile()?;
                let source = ax_db::rusqlite::Connection::open(&src).map_err(|e| BackupError(e.to_string()))?;
                let mut dest = ax_db::rusqlite::Connection::open(snap.path()).map_err(|e| BackupError(e.to_string()))?;
                {
                    let b = ax_db::rusqlite::backup::Backup::new(&source, &mut dest).map_err(|e| BackupError(e.to_string()))?;
                    b.run_to_completion(256, std::time::Duration::from_millis(0), None).map_err(|e| BackupError(e.to_string()))?;
                }
                drop(dest);
                drop(source);
                let size = fs::metadata(snap.path())?.len();
                zw.start_file(&arc, zip_options(size, meta.modified().ok())).map_err(|e| BackupError(e.to_string()))?;
                std::io::copy(&mut File::open(snap.path())?, &mut zw)?;
                files.push(json!({"path": arc, "size": size, "sha256": sha256_file(snap.path())?}));
            } else if let Some(purpose) = protected_purpose(&arc) {
                let payload = fs::read(&src)?;
                if !payload.starts_with(VAULT_MAGIC) {
                    return err("Backup refused an unencrypted credentials/session file. Restart Archive X to complete protected-storage migration.");
                }
                (env.unseal)(&payload, purpose).map_err(BackupError)?;
                zw.start_file(&arc, zip_options(payload.len() as u64, meta.modified().ok())).map_err(|e| BackupError(e.to_string()))?;
                zw.write_all(&payload)?;
                files.push(json!({"path": arc, "size": payload.len(), "sha256": to_hex(&Sha256::digest(&payload))}));
            } else {
                zw.start_file(&arc, zip_options(meta.len(), meta.modified().ok())).map_err(|e| BackupError(e.to_string()))?;
                std::io::copy(&mut File::open(&src)?, &mut zw)?;
                files.push(json!({"path": arc, "size": meta.len(), "sha256": sha256_file(&src)?}));
            }
        }
        manifest.insert("files".into(), Value::Array(files.clone()));
        let text = serde_json::to_string_pretty(&Value::Object(manifest.clone())).unwrap() + "\n";
        zw.start_file("BACKUP_MANIFEST.json", zip_options(text.len() as u64, None)).map_err(|e| BackupError(e.to_string()))?;
        zw.write_all(text.as_bytes())?;
        zw.finish().map_err(|e| BackupError(e.to_string()))?;
        Ok(manifest)
    };
    let outcome = (|| -> R<Value> {
        let mut manifest = build()?;
        match password.filter(|p| !p.is_empty()) {
            Some(pw) => {
                let mut salt = [0u8; 16];
                let mut nonce = [0u8; 12];
                getrandom::getrandom(&mut salt).map_err(|e| BackupError(e.to_string()))?;
                getrandom::getrandom(&mut nonce).map_err(|e| BackupError(e.to_string()))?;
                encrypt_file(&tmp_zip, &final_path, pw, &salt, &nonce, &(env.now_utc)())?;
                let _ = fs::remove_file(&tmp_zip);
            }
            None => fs::rename(&tmp_zip, &final_path)?,
        }
        inspect_backup(&final_path, password, env)?;
        manifest.insert("verified_at".into(), json!((env.now_utc)()));
        Ok(json!({"ok": true, "path": final_path.to_string_lossy(), "size": fs::metadata(&final_path)?.len(), "manifest": Value::Object(manifest.clone()), "verified": true}))
    })();
    if outcome.is_err() {
        let _ = fs::remove_file(&tmp_zip);
    }
    outcome
}

/// `list_backups`: every `.axbackup` in the repository, newest first.
pub fn list_backups(repo: &Path, env: &Env) -> Vec<Value> {
    let Ok(rd) = fs::read_dir(repo) else { return vec![] };
    let mut paths: Vec<(std::time::SystemTime, PathBuf)> = rd
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "axbackup") && p.is_file())
        .filter_map(|p| Some((fs::metadata(&p).ok()?.modified().ok()?, p)))
        .collect();
    paths.sort_by_key(|p| std::cmp::Reverse(p.0));
    let mut out = Vec::new();
    for (mtime, p) in paths {
        let name = p.file_name().unwrap().to_string_lossy().into_owned();
        let size = fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
        if is_encrypted(&p).unwrap_or(false) {
            let secs = mtime.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
            out.push(json!({"name": name, "path": p.to_string_lossy(), "size": size, "created_at": ax_db::jobs::iso_from_unix(secs), "type": "encrypted", "encrypted": true, "verified": Value::Null, "files": 0}));
            continue;
        }
        match inspect_backup(&p, None, env) {
            Ok(info) => {
                let m = &info["manifest"];
                out.push(json!({"name": name, "path": p.to_string_lossy(), "size": size, "created_at": m.get("created_at").cloned().unwrap_or(json!("")), "type": m.get("backup_type").cloned().unwrap_or(json!("")), "encrypted": false, "verified": true, "files": m.get("files").and_then(Value::as_array).map_or(0, |a| a.len())}));
            }
            Err(e) => {
                out.push(json!({"name": name, "path": p.to_string_lossy(), "size": size, "created_at": "", "type": "unknown", "encrypted": false, "verified": false, "error": e.0}))
            }
        }
    }
    out
}

/// The date parts retention groups by, for a file time: `(iso year, iso week, year, month)`.
pub type LocalParts = dyn Fn(f64) -> (i64, i64, i64, i64);

/// A retention setting as Python reads it: `max(0, int(value or default))`, so a stored 0 means the default.
pub fn retention_count(stored: i64, default: i64) -> usize {
    (if stored == 0 { default } else { stored }).max(0) as usize
}

/// `apply_retention`: keep the newest `daily`, the newest of each of the last `weekly` weeks and `monthly` months; delete the rest.
pub fn apply_retention(repo: &Path, daily: usize, weekly: usize, monthly: usize, parts: &LocalParts) -> Value {
    let mut rows: Vec<(f64, PathBuf)> = Vec::new();
    if repo.exists() {
        if let Ok(rd) = fs::read_dir(repo) {
            for e in rd.filter_map(|e| e.ok()) {
                let p = e.path();
                if p.extension().is_some_and(|x| x == "axbackup") {
                    if let Ok(m) = fs::metadata(&p).and_then(|m| m.modified()) {
                        rows.push((m.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0), p));
                    }
                }
            }
        }
    }
    rows.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap().then_with(|| b.1.cmp(&a.1)));
    let mut keep: BTreeSet<PathBuf> = rows.iter().take(daily).map(|(_, p)| p.clone()).collect();
    let (mut weeks, mut months) = (BTreeSet::new(), BTreeSet::new());
    for (ts, p) in &rows {
        let (iy, iw, y, m) = parts(*ts);
        if !weeks.contains(&(iy, iw)) && weeks.len() < weekly {
            keep.insert(p.clone());
            weeks.insert((iy, iw));
        }
        if !months.contains(&(y, m)) && months.len() < monthly {
            keep.insert(p.clone());
            months.insert((y, m));
        }
    }
    let mut deleted = 0;
    for (_, p) in &rows {
        if !keep.contains(p) && fs::remove_file(p).is_ok() {
            deleted += 1;
        }
    }
    json!({"kept": keep.len(), "deleted": deleted})
}

fn extract(zip_bytes: Vec<u8>, stage: &Path) -> R<()> {
    let mut zf = zip::ZipArchive::new(std::io::Cursor::new(zip_bytes)).map_err(|_| BackupError("Backup archive is corrupted".into()))?;
    for i in 0..zf.len() {
        let mut member = zf.by_index(i).map_err(|e| BackupError(e.to_string()))?;
        let Some(rel) = member.enclosed_name() else { continue };
        let target = stage.join(rel);
        if member.is_dir() {
            fs::create_dir_all(&target)?;
        } else {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            std::io::copy(&mut member, &mut File::create(&target)?)?;
        }
    }
    Ok(())
}

/// `stage_restore`: validate a backup and unpack it next to the data folder for the next start to apply.
pub fn stage_restore(user_data_dir: &Path, backup_path: &Path, password: Option<&str>, env: &Env) -> R<Value> {
    let src = absolute(backup_path);
    let info = inspect_backup(&src, password, env)?;
    let manifest = &info["manifest"];
    if manifest.get("media_included").is_some_and(|v| v.as_bool().unwrap_or(false)) {
        return err("Media-inclusive restores are not supported by this staged restore yet");
    }
    let root = absolute(user_data_dir);
    let parent = root.parent().unwrap_or(Path::new(".")).to_path_buf();
    let stage = tempfile::Builder::new().prefix("archive_x_restore_").tempdir_in(&parent)?.keep();
    let result = (|| -> R<Value> {
        extract(open_archive(&src, password)?, &stage)?;
        for item in manifest.get("files").and_then(Value::as_array).cloned().unwrap_or_default() {
            let path = item.get("path").and_then(Value::as_str).unwrap_or("");
            let target = stage.join(path);
            let meta = fs::metadata(&target).ok().filter(|m| m.is_file());
            let size_ok = meta.as_ref().is_some_and(|m| item.get("size").and_then(Value::as_i64).is_none_or(|s| s == m.len() as i64));
            if !size_ok {
                return Err(BackupError(format!("Restored file failed size validation: {path}")));
            }
            if sha256_file(&target)? != item.get("sha256").and_then(Value::as_str).unwrap_or("") {
                return Err(BackupError(format!("Restored file failed checksum validation: {path}")));
            }
        }
        fn drop_sidecars(dir: &Path) {
            if let Ok(rd) = fs::read_dir(dir) {
                for e in rd.filter_map(|e| e.ok()) {
                    let p = e.path();
                    if p.is_dir() {
                        drop_sidecars(&p);
                    } else if SIDECAR_SUFFIXES.iter().any(|s| p.file_name().is_some_and(|n| n.to_string_lossy().ends_with(s))) {
                        let _ = fs::remove_file(&p);
                    }
                }
            }
        }
        drop_sidecars(&stage);
        let db_file = stage.join("database").join("archive.db");
        if !db_file.is_file() {
            return err("Backup does not contain the Archive X database");
        }
        let conn = ax_db::rusqlite::Connection::open_with_flags(&db_file, ax_db::rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).map_err(|e| BackupError(e.to_string()))?;
        let _: String = conn.query_row("PRAGMA integrity_check", [], |r| r.get(0)).map_err(|e| BackupError(e.to_string()))?;
        drop(conn);
        let marker = json!({"version": 1, "backup_path": src.to_string_lossy(), "staging_path": stage.to_string_lossy(), "created_at": (env.now_utc)(), "application_version": manifest.get("application_version").cloned().unwrap_or(json!(""))});
        fs::write(parent.join(".archive_x_pending_restore.json"), serde_json::to_string_pretty(&marker).unwrap())?;
        Ok(json!({"ok": true, "restart_required": true, "staging_path": stage.to_string_lossy(), "manifest": manifest.clone()}))
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&stage);
    }
    result
}
