//! Port of `telegram_archive/core/encrypted_files.py`: authenticated encryption at rest.
//!
//! The file format is shared with the Python version, so an upgrade keeps reading what Python wrote (and the other way round while both
//! exist):
//!
//! * a protected file is `MAGIC ‖ 12-byte nonce ‖ AES-256-GCM(ciphertext ‖ tag)`, with the *purpose* string as associated data;
//! * the AES key is `HKDF-SHA256(secret, salt = "Archive X protected storage v1", info = "archive-x/aes-256-gcm/files")`;
//! * `secret` is `ARCHIVE_X_ENCRYPTION_KEY` or `SESSION_SECRET` (at least 32 bytes) or, when neither is set, the per-user key file
//!   `protected-storage.key` (a plain marker + 32 random bytes off Windows, DPAPI-wrapped on Windows).
//!
//! Compared with Python: JSON is written compactly (Python writes `", "` and `": "` separators); both read either. A caller supplies
//! the paths that used to come from `config`.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use hmac::{Hmac, Mac};
use serde_json::{Map, Value};
use sha2::Sha256;

pub const MAGIC: &[u8] = b"ARCHIVE-X-AESGCM-1\n";
pub const LOCAL_KEY_MAGIC: &[u8] = b"ARCHIVE-X-LOCAL-KEY-1\n";
pub const DPAPI_KEY_MAGIC: &[u8] = b"ARCHIVE-X-DPAPI-KEY-1\n";

const HKDF_SALT: &[u8] = b"Archive X protected storage v1";
const HKDF_INFO: &[u8] = b"archive-x/aes-256-gcm/files";

/// A protected file must not silently become an empty or default file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VaultError(pub String);

impl std::fmt::Display for VaultError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for VaultError {}

fn err<T>(msg: &str) -> Result<T, VaultError> {
    Err(VaultError(msg.to_string()))
}

/// Where the keys and files live; the equivalent of the `config` values the Python module read.
#[derive(Debug, Clone)]
pub struct Vault {
    /// `ARCHIVE_X_ENCRYPTION_KEY` or `SESSION_SECRET`, if set and non-empty.
    pub secret: Option<String>,
    /// The folder of `config.DATA_LOCATION_FILE`; the per-user key lives next to it.
    pub key_dir: PathBuf,
    /// `SECRETS_PATH`, `METADATA_KEYS_PATH`, `MEDIA_MANAGER_CONFIG_PATH`: if any holds ciphertext, a missing key file is an error.
    pub encrypted_user_files: Vec<PathBuf>,
}

static LOCK: Mutex<()> = Mutex::new(());

/// The secret from the environment, as Python reads it (`A or B`: an empty first value falls through).
pub fn secret_from_env() -> Option<String> {
    ["ARCHIVE_X_ENCRYPTION_KEY", "SESSION_SECRET"].iter().filter_map(|k| std::env::var(k).ok()).find(|v| !v.is_empty())
}

/// `HKDF-SHA256(secret)` to the 32-byte file key (RFC 5869).
///
/// 32 bytes is exactly one SHA-256 block, so expand is a single step: `T(1) = HMAC(PRK, info ‖ 0x01)` with
/// `PRK = HMAC(salt, secret)`. Written out here, rather than through an output buffer, so the key is the MAC result itself.
pub fn derive_key(secret: &[u8]) -> [u8; 32] {
    type HmacSha256 = Hmac<Sha256>;
    let mut extract = <HmacSha256 as Mac>::new_from_slice(HKDF_SALT).expect("HMAC accepts any key length");
    extract.update(secret);
    let prk = extract.finalize().into_bytes();
    let mut expand = <HmacSha256 as Mac>::new_from_slice(&prk).expect("HMAC accepts any key length");
    expand.update(HKDF_INFO);
    expand.update(&[1u8]);
    expand.finalize().into_bytes().into()
}

fn cipher(key: &[u8; 32]) -> Aes256Gcm {
    Aes256Gcm::new(key.into())
}

/// `MAGIC ‖ nonce ‖ ciphertext+tag` with a fresh random nonce.
pub fn seal_with(key: &[u8; 32], data: &[u8], purpose: &str) -> Vec<u8> {
    let mut nonce = [0u8; 12];
    getrandom::getrandom(&mut nonce).expect("operating system random numbers");
    seal_with_nonce(key, &nonce, data, purpose)
}

fn seal_with_nonce(key: &[u8; 32], nonce: &[u8; 12], data: &[u8], purpose: &str) -> Vec<u8> {
    let ct = cipher(key).encrypt(Nonce::from_slice(nonce), Payload { msg: data, aad: purpose.as_bytes() }).expect("AES-GCM encryption does not fail");
    let mut out = Vec::with_capacity(MAGIC.len() + 12 + ct.len());
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(nonce);
    out.extend_from_slice(&ct);
    out
}

pub fn unseal_with(key: &[u8; 32], data: &[u8], purpose: &str) -> Result<Vec<u8>, VaultError> {
    if !data.starts_with(MAGIC) || data.len() < MAGIC.len() + 28 {
        return err("Invalid protected-file format; no data was overwritten.");
    }
    let off = MAGIC.len();
    cipher(key)
        .decrypt(Nonce::from_slice(&data[off..off + 12]), Payload { msg: &data[off + 12..], aad: purpose.as_bytes() })
        .map_err(|_| VaultError("Protected data could not be authenticated. Restore the original encryption secret or an intact backup; no data was overwritten.".into()))
}

// --- the per-user local key -------------------------------------------------------------------------------------------------------

#[cfg(not(windows))]
fn protect_local_key(key: &[u8]) -> Result<Vec<u8>, VaultError> {
    Ok([LOCAL_KEY_MAGIC, key].concat())
}

#[cfg(not(windows))]
fn unprotect_local_key(payload: &[u8]) -> Result<Vec<u8>, VaultError> {
    match payload.strip_prefix(LOCAL_KEY_MAGIC) {
        Some(k) => Ok(k.to_vec()),
        None => err("Archive X's local encryption key has an unsupported or invalid format. Restore the original encryption secret or an intact backup."),
    }
}

#[cfg(windows)]
fn protect_local_key(key: &[u8]) -> Result<Vec<u8>, VaultError> {
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptProtectData, CRYPT_INTEGER_BLOB};
    let source = CRYPT_INTEGER_BLOB { cbData: key.len() as u32, pbData: key.as_ptr() as *mut u8 };
    let mut out = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
    let descr: Vec<u16> = "Archive X protected-storage key".encode_utf16().chain(std::iter::once(0)).collect();
    // SAFETY: `source` points at `key` for the call; `out` is filled by the API and freed with LocalFree below.
    let ok = unsafe { CryptProtectData(&source, descr.as_ptr(), std::ptr::null(), std::ptr::null(), std::ptr::null(), 0x1, &mut out) };
    if ok == 0 {
        return Err(VaultError(format!("Archive X could not protect its local key: {}", std::io::Error::last_os_error())));
    }
    // SAFETY: on success `out.pbData` points at `out.cbData` bytes owned by the API.
    let bytes = unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize) }.to_vec();
    unsafe { LocalFree(out.pbData as _) };
    Ok([DPAPI_KEY_MAGIC, &bytes].concat())
}

#[cfg(windows)]
fn unprotect_local_key(payload: &[u8]) -> Result<Vec<u8>, VaultError> {
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptUnprotectData, CRYPT_INTEGER_BLOB};
    let Some(protected) = payload.strip_prefix(DPAPI_KEY_MAGIC) else {
        return err("Archive X's local encryption key has an unsupported or invalid format. Restore the original encryption secret or an intact backup.");
    };
    let source = CRYPT_INTEGER_BLOB { cbData: protected.len() as u32, pbData: protected.as_ptr() as *mut u8 };
    let mut out = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
    // SAFETY: as in `protect_local_key`.
    let ok = unsafe { CryptUnprotectData(&source, std::ptr::null_mut(), std::ptr::null(), std::ptr::null(), std::ptr::null(), 0x1, &mut out) };
    if ok == 0 {
        return err("Archive X cannot unlock its Windows-protected local key. Sign in to the same Windows account or restore the original encryption secret.");
    }
    let bytes = unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize) }.to_vec();
    unsafe { LocalFree(out.pbData as _) };
    Ok(bytes)
}

fn unprotect_checked(payload: &[u8]) -> Result<Vec<u8>, VaultError> {
    let key = unprotect_local_key(payload)?;
    if key.len() != 32 {
        return err("Archive X's local encryption key is invalid; protected data was not changed.");
    }
    Ok(key)
}

fn encrypted_user_files_exist(files: &[PathBuf]) -> bool {
    files.iter().any(|path| {
        let latest = PathBuf::from(format!("{}.latest", path.display()));
        [path.clone(), latest].iter().any(|c| c.is_file() && fs::read(c).map(|b| b.starts_with(MAGIC)).unwrap_or(false))
    })
}

#[cfg(unix)]
fn create_new_private(path: &Path) -> std::io::Result<fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path)
}

#[cfg(not(unix))]
fn create_new_private(path: &Path) -> std::io::Result<fs::File> {
    fs::OpenOptions::new().write(true).create_new(true).open(path)
}

#[cfg(unix)]
fn make_dir_private(dir: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    fs::DirBuilder::new().recursive(true).mode(0o700).create(dir)
}

#[cfg(not(unix))]
fn make_dir_private(dir: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dir)
}

impl Vault {
    /// Load the protected-storage key, creating it when no encrypted files exist yet.
    fn local_key(&self) -> Result<Vec<u8>, VaultError> {
        let key_path = self.key_dir.join("protected-storage.key");
        let read_existing = || -> Result<Vec<u8>, VaultError> {
            match fs::read(&key_path) {
                Ok(b) => unprotect_checked(&b),
                Err(_) => err("Archive X cannot read its per-user encryption key. Check access to the Archive X folder under your user profile."),
            }
        };
        if key_path.exists() {
            return read_existing();
        }
        if encrypted_user_files_exist(&self.encrypted_user_files) {
            return err(
                "Archive X found encrypted data but its local key is missing. Set the original ARCHIVE_X_ENCRYPTION_KEY or SESSION_SECRET, or restore the key from the same Windows user profile. No protected data was overwritten.",
            );
        }
        make_dir_private(&self.key_dir).map_err(|e| VaultError(format!("Archive X cannot create its key folder: {e}")))?;
        let mut key = [0u8; 32];
        getrandom::getrandom(&mut key).expect("operating system random numbers");
        let payload = protect_local_key(&key)?;
        let mut file = match create_new_private(&key_path) {
            Ok(f) => f,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => return read_existing(),
            Err(e) => return Err(VaultError(format!("Archive X cannot write its per-user encryption key: {e}"))),
        };
        let written = file.write_all(&payload).and_then(|_| file.flush()).and_then(|_| file.sync_all());
        if let Err(e) = written {
            let _ = fs::remove_file(&key_path);
            return Err(VaultError(format!("Archive X cannot write its per-user encryption key: {e}")));
        }
        Ok(key.to_vec())
    }

    /// The 32-byte file key (`encryption_key()` in Python).
    pub fn encryption_key(&self) -> Result<[u8; 32], VaultError> {
        let secret = match &self.secret {
            Some(s) if !s.is_empty() => s.as_bytes().to_vec(),
            _ => self.local_key()?,
        };
        if secret.len() < 32 {
            return err(
                "Protected storage requires ARCHIVE_X_ENCRYPTION_KEY or SESSION_SECRET with at least 32 characters, or an initialized per-user local key. Preserve the original key for restores.",
            );
        }
        Ok(derive_key(&secret))
    }

    pub fn seal(&self, data: &[u8], purpose: &str) -> Result<Vec<u8>, VaultError> {
        Ok(seal_with(&self.encryption_key()?, data, purpose))
    }

    pub fn unseal(&self, data: &[u8], purpose: &str) -> Result<Vec<u8>, VaultError> {
        unseal_with(&self.encryption_key()?, data, purpose)
    }

    /// Encrypt, verify that it decrypts back, then replace the file atomically.
    pub fn write_encrypted(&self, path: &Path, data: &[u8], purpose: &str) -> Result<(), VaultError> {
        let _g = LOCK.lock().unwrap_or_else(|p| p.into_inner());
        self.write_encrypted_locked(path, data, purpose)
    }

    fn write_encrypted_locked(&self, path: &Path, data: &[u8], purpose: &str) -> Result<(), VaultError> {
        let ciphertext = self.seal(data, purpose)?;
        if self.unseal(&ciphertext, purpose)? != data {
            return err("Encrypted storage verification failed.");
        }
        atomic_write(path, &ciphertext).map_err(|e| VaultError(format!("Archive X cannot write {}: {e}", path.display())))
    }

    /// Read a protected JSON object. A missing file gives `default` (or `{}`); legacy plaintext is encrypted on first read.
    pub fn read_json(&self, path: &Path, purpose: &str, default: Option<Map<String, Value>>) -> Result<Map<String, Value>, VaultError> {
        let _g = LOCK.lock().unwrap_or_else(|p| p.into_inner());
        self.encryption_key()?; // fail closed even on first-run writes
        if !path.exists() {
            return Ok(default.unwrap_or_default());
        }
        let raw = fs::read(path).map_err(|e| VaultError(format!("Archive X cannot read {}: {e}", path.display())))?;
        let encrypted = raw.starts_with(MAGIC);
        let clear = if encrypted { self.unseal(&raw, purpose)? } else { raw };
        let parsed: Result<Value, _> = serde_json::from_slice(&clear);
        let Ok(Value::Object(map)) = parsed else {
            return err("Protected JSON data is invalid; no data was overwritten.");
        };
        if !encrypted {
            self.write_encrypted_locked(path, &clear, purpose)?;
        }
        Ok(map)
    }

    pub fn write_json(&self, path: &Path, value: &Map<String, Value>, purpose: &str) -> Result<(), VaultError> {
        let bytes = serde_json::to_vec(value).expect("a JSON object serialises");
        self.write_encrypted(path, &bytes, purpose)
    }
}

/// Write via a temporary file in the same folder, fsync, then rename over the target. The rename is retried briefly because
/// antivirus, search indexing and backup tools open fresh files on Windows and make it fail with "Access is denied".
pub fn atomic_write(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let dir = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
    make_dir_private(dir)?;
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let mut tmp = tempfile::Builder::new().prefix(&format!(".{name}.")).tempfile_in(dir)?;
    tmp.write_all(data)?;
    tmp.flush()?;
    tmp.as_file().sync_all()?;
    let mut attempt = 0u32;
    let mut tmp = tmp;
    loop {
        match tmp.persist(path) {
            Ok(_) => break,
            Err(e) if e.error.kind() == std::io::ErrorKind::PermissionDenied && attempt < 5 => {
                std::thread::sleep(std::time::Duration::from_millis(50 * (1u64 << attempt)));
                attempt += 1;
                tmp = e.file;
            }
            Err(e) => return Err(e.error),
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

/// Which protected-file purpose a path inside a backup has (without exposing values); `None` for ordinary files.
pub fn protected_purpose(relative_path: &str) -> Option<&'static str> {
    let path = Path::new(relative_path);
    let in_auth = path.components().any(|c| c.as_os_str() == "auth");
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    if in_auth && ["credentials.json", "credentials.json.tmp", "credentials.json.latest"].contains(&name.as_str()) {
        return Some("archive-x-credentials-v1");
    }
    if in_auth && name.ends_with(".session") {
        return Some("telegram-sqlite-session-v1");
    }
    if in_auth && ["provider_keys.json", "provider_keys.json.tmp", "provider_keys.json.latest"].contains(&name.as_str()) {
        return Some("archive-x-provider-keys-v1");
    }
    if ["telegram_uploader_config.json", "telegram_uploader_config.json.latest", "telegram_uploader_config.json.tmp"].contains(&name.as_str()) {
        return Some("archive-x-media-manager-v1");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    const SECRET: &str = "0123456789abcdef0123456789abcdef-test";
    // Produced by the Python original: ARCHIVE_X_ENCRYPTION_KEY=SECRET, encrypted_files.seal(b'{"a": "é", "n": 1}', purpose).
    const PYTHON_KEY: &str = "33ab521d4cf41714a28f2af7800993f151be00a2da989fef2f1ef8571be5d729";
    const PYTHON_BLOB: &str = "415243484956452d582d41455347434d2d310a80d5157cc18e929261b5d19a33f0b3b16dc67cd4755ceae0a9a8856ba1657c473cc4b88078ced9c1ef8932f97d0a8b";
    const PURPOSE: &str = "archive-x-credentials-v1";

    fn unhex(s: &str) -> Vec<u8> {
        (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
    }
    fn hex(b: &[u8]) -> String {
        b.iter().map(|x| format!("{x:02x}")).collect()
    }
    fn vault(dir: &Path, secret: Option<&str>) -> Vault {
        Vault { secret: secret.map(String::from), key_dir: dir.join("keys"), encrypted_user_files: vec![dir.join("secrets.json")] }
    }

    #[test]
    fn derives_the_same_key_as_python() {
        assert_eq!(hex(&derive_key(SECRET.as_bytes())), PYTHON_KEY);
    }

    #[test]
    fn reads_what_python_wrote() {
        let d = tempfile::tempdir().unwrap();
        let plain = vault(d.path(), Some(SECRET)).unseal(&unhex(PYTHON_BLOB), PURPOSE).unwrap();
        assert_eq!(plain, "{\"a\": \"é\", \"n\": 1}".as_bytes());
    }

    #[test]
    fn the_purpose_is_part_of_the_proof() {
        let d = tempfile::tempdir().unwrap();
        let e = vault(d.path(), Some(SECRET)).unseal(&unhex(PYTHON_BLOB), "archive-x-provider-keys-v1").unwrap_err();
        assert!(e.0.starts_with("Protected data could not be authenticated"));
    }

    #[test]
    fn a_wrong_key_or_a_flipped_bit_or_a_short_file_is_refused() {
        let d = tempfile::tempdir().unwrap();
        let other = vault(d.path(), Some("a-completely-different-secret-of-32+chars"));
        assert!(other.unseal(&unhex(PYTHON_BLOB), PURPOSE).is_err());
        let mut b = unhex(PYTHON_BLOB);
        *b.last_mut().unwrap() ^= 1;
        assert!(vault(d.path(), Some(SECRET)).unseal(&b, PURPOSE).is_err());
        assert_eq!(
            vault(d.path(), Some(SECRET)).unseal(b"ARCHIVE-X-AESGCM-1\nshort", PURPOSE).unwrap_err().0,
            "Invalid protected-file format; no data was overwritten."
        );
        assert_eq!(
            vault(d.path(), Some(SECRET)).unseal(b"plain text, not protected", PURPOSE).unwrap_err().0,
            "Invalid protected-file format; no data was overwritten."
        );
    }

    #[test]
    fn sealing_round_trips_and_uses_fresh_nonces() {
        let d = tempfile::tempdir().unwrap();
        let v = vault(d.path(), Some(SECRET));
        let (a, b) = (v.seal(b"hello", PURPOSE).unwrap(), v.seal(b"hello", PURPOSE).unwrap());
        assert_ne!(a, b);
        assert!(a.starts_with(MAGIC));
        assert_eq!(v.unseal(&a, PURPOSE).unwrap(), b"hello");
        assert_eq!(v.unseal(&v.seal(b"", PURPOSE).unwrap(), PURPOSE).unwrap(), b"");
    }

    #[test]
    fn a_short_secret_is_refused() {
        let d = tempfile::tempdir().unwrap();
        let e = vault(d.path(), Some("too short")).encryption_key().unwrap_err();
        assert!(e.0.contains("at least 32 characters"));
    }

    #[test]
    fn json_files_are_encrypted_and_plaintext_is_upgraded_on_read() {
        let d = tempfile::tempdir().unwrap();
        let v = vault(d.path(), Some(SECRET));
        let path = d.path().join("auth").join("credentials.json");
        assert!(v.read_json(&path, PURPOSE, None).unwrap().is_empty());
        let mut m = Map::new();
        m.insert("user".into(), Value::from("é"));
        v.write_json(&path, &m, PURPOSE).unwrap();
        assert!(fs::read(&path).unwrap().starts_with(MAGIC));
        assert_eq!(v.read_json(&path, PURPOSE, None).unwrap(), m);

        let legacy = d.path().join("legacy.json");
        fs::write(&legacy, br#"{"k": 1}"#).unwrap();
        assert_eq!(v.read_json(&legacy, PURPOSE, None).unwrap()["k"], 1);
        assert!(fs::read(&legacy).unwrap().starts_with(MAGIC), "plaintext is encrypted after the first read");

        let bad = d.path().join("bad.json");
        fs::write(&bad, b"[1, 2]").unwrap();
        assert_eq!(v.read_json(&bad, PURPOSE, None).unwrap_err().0, "Protected JSON data is invalid; no data was overwritten.");
        assert_eq!(fs::read(&bad).unwrap(), b"[1, 2]", "an invalid file is left alone");
    }

    #[cfg(unix)]
    #[test]
    fn files_are_private_and_no_temp_files_are_left() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join("x.bin");
        atomic_write(&path, b"data").unwrap();
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(fs::read_dir(d.path()).unwrap().count(), 1);
    }

    #[cfg(not(windows))]
    #[test]
    fn the_local_key_is_created_once_and_reused() {
        let d = tempfile::tempdir().unwrap();
        let v = vault(d.path(), None);
        let first = v.encryption_key().unwrap();
        let stored = fs::read(d.path().join("keys").join("protected-storage.key")).unwrap();
        assert!(stored.starts_with(LOCAL_KEY_MAGIC) && stored.len() == LOCAL_KEY_MAGIC.len() + 32);
        assert_eq!(v.encryption_key().unwrap(), first);
    }

    #[cfg(not(windows))]
    #[test]
    fn a_missing_local_key_never_replaces_existing_ciphertext_keys() {
        let d = tempfile::tempdir().unwrap();
        fs::write(d.path().join("secrets.json"), [MAGIC, b"x"].concat()).unwrap();
        let e = vault(d.path(), None).encryption_key().unwrap_err();
        assert!(e.0.starts_with("Archive X found encrypted data but its local key is missing"));
        assert!(!d.path().join("keys").join("protected-storage.key").exists());
    }

    #[cfg(not(windows))]
    #[test]
    fn a_malformed_local_key_file_is_reported() {
        let d = tempfile::tempdir().unwrap();
        fs::create_dir_all(d.path().join("keys")).unwrap();
        fs::write(d.path().join("keys").join("protected-storage.key"), b"garbage").unwrap();
        assert!(vault(d.path(), None).encryption_key().unwrap_err().0.contains("unsupported or invalid format"));
        fs::write(d.path().join("keys").join("protected-storage.key"), [LOCAL_KEY_MAGIC, b"short"].concat()).unwrap();
        assert!(vault(d.path(), None).encryption_key().unwrap_err().0.contains("local encryption key is invalid"));
    }

    #[test]
    fn purposes_for_backup_paths() {
        assert_eq!(protected_purpose("data/auth/credentials.json"), Some("archive-x-credentials-v1"));
        assert_eq!(protected_purpose("auth/credentials.json.latest"), Some("archive-x-credentials-v1"));
        assert_eq!(protected_purpose("auth/me.session"), Some("telegram-sqlite-session-v1"));
        assert_eq!(protected_purpose("auth/provider_keys.json.tmp"), Some("archive-x-provider-keys-v1"));
        assert_eq!(protected_purpose("x/telegram_uploader_config.json"), Some("archive-x-media-manager-v1"));
        assert_eq!(protected_purpose("credentials.json"), None);
        assert_eq!(protected_purpose("library/video.mp4"), None);
    }

    #[test]
    #[ignore = "prints a blob for the Python round-trip check"]
    fn print_a_blob_for_python() {
        let b = seal_with(&derive_key(SECRET.as_bytes()), "rust says é".as_bytes(), PURPOSE);
        println!("RUSTBLOB {}", hex(&b));
    }
}
