//! Where the data folder is, and moving or restoring it (`config.py`, "User-data location and pending moves").
//!
//! The folder is chosen, in this order, by `ARCHIVE_X_DATA_DIR`, the old `TELEGRAM_ARCHIVE_DATA_DIR`, the `active_path` in a small control
//! file kept outside the data folder, and the platform default. Settings can queue a move to another folder: the control file then names
//! a `pending_path`, and the next start copies the whole tree, checks the copy, removes the old folder and switches. A restore from a
//! backup is staged next to the data folder and swapped in the same way, before anything opens the database.

use crate::layout::DataFolder;
use crate::pyfmt::{dumps_indent2, lexical, path_arg, py_str_or_empty, py_strip, py_truthy, resolve, resolve_path};
use crate::{ConfigError, Env};
use serde_json::{json, Map, Value};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub const MARKER_NAME: &str = ".archive_x_move_in_progress.json";
const MANIFEST_NAME: &str = "PERSISTENT_DATA_MANIFEST.json";

fn local_app_data(env: &Env) -> PathBuf {
    match env.non_empty("LOCALAPPDATA") {
        Some(v) => PathBuf::from(v),
        None => env.home.join("AppData").join("Local"),
    }
}

/// `config.DATA_LOCATION_FILE`.
pub fn data_location_file(env: &Env) -> PathBuf {
    let file = ".data_location.json";
    if env.windows {
        lexical(local_app_data(env).join("Archive X").join(file))
    } else {
        let base = env.non_empty("XDG_CONFIG_HOME").map(PathBuf::from).unwrap_or_else(|| env.home.join(".config"));
        lexical(base.join("Archive X").join(file))
    }
}

/// `config._default_user_data_dir()`, resolved.
pub fn default_user_data_dir(env: &Env) -> Result<PathBuf, ConfigError> {
    let dir = if env.windows {
        local_app_data(env).join("Archive X")
    } else {
        env.non_empty("XDG_DATA_HOME").map(PathBuf::from).unwrap_or_else(|| env.home.join(".local").join("share")).join("Archive X")
    };
    resolve_path(&if dir.is_absolute() { lexical(dir) } else { env.cwd.join(lexical(dir)) })
}

/// The control file as a JSON object; anything unreadable or not an object counts as empty.
pub fn read_state(env: &Env) -> Map<String, Value> {
    let text = fs::read_to_string(data_location_file(env));
    match text.ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()) {
        Some(Value::Object(map)) => map,
        _ => Map::new(),
    }
}

fn write_state(env: &Env, active: &Path, pending: Option<&Path>) -> Result<(), ConfigError> {
    let file = data_location_file(env);
    let abs = |p: &Path| resolve(&p.to_string_lossy(), env).map(|r| r.display().to_string());
    let payload = json!({
        "version": 1,
        "active_path": abs(active)?,
        "pending_path": match pending { Some(p) => abs(p)?, None => String::new() },
        "updated_at": env.now_utc_iso(),
    });
    let mut tmp = file.clone().into_os_string();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    let parent = file.parent().unwrap_or(Path::new("."));
    fs::create_dir_all(parent).map_err(|e| ConfigError::os(&e, parent))?;
    fs::write(&tmp, dumps_indent2(&payload, false)).map_err(|e| ConfigError::os(&e, &tmp))?;
    fs::rename(&tmp, &file).map_err(|e| ConfigError::os(&e, &file))
}

/// What the environment and the control file say at start-up.
#[derive(Debug, Clone)]
pub struct Chosen {
    pub user_data_dir: PathBuf,
    /// The environment names the folder: a queued move and a staged restore are then left alone.
    pub from_environment: bool,
    /// The folder a queued move should go to (empty when none is queued).
    pub pending: String,
}

pub fn choose(env: &Env) -> Result<Chosen, ConfigError> {
    let from_env = {
        let first = py_strip(env.get("ARCHIVE_X_DATA_DIR").unwrap_or(""));
        if first.is_empty() {
            py_strip(env.get("TELEGRAM_ARCHIVE_DATA_DIR").unwrap_or(""))
        } else {
            first
        }
    };
    let state = read_state(env);
    let text_of = |key: &str| -> Result<String, ConfigError> { Ok(py_strip(&py_str_or_empty(state.get(key).unwrap_or(&Value::Null))?).to_string()) };
    if !from_env.is_empty() {
        return Ok(Chosen { user_data_dir: resolve(from_env, env)?, from_environment: true, pending: text_of("pending_path")? });
    }
    let active = text_of("active_path")?;
    let user_data_dir = if active.is_empty() { default_user_data_dir(env)? } else { resolve(&active, env)? };
    Ok(Chosen { user_data_dir, from_environment: false, pending: text_of("pending_path")? })
}

fn environment_override(env: &Env) -> bool {
    !py_strip(env.get("ARCHIVE_X_DATA_DIR").unwrap_or("")).is_empty() || !py_strip(env.get("TELEGRAM_ARCHIVE_DATA_DIR").unwrap_or("")).is_empty()
}

/// What Settings shows about the data folder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocationState {
    pub active_path: String,
    pub pending_path: String,
    pub environment_override: bool,
}

impl LocationState {
    pub fn to_json(&self) -> Value {
        json!({"active_path": self.active_path, "pending_path": self.pending_path, "environment_override": self.environment_override})
    }
}

/// `Path(state.get(key) or fallback).expanduser().resolve()`: a stored value that is not text is the TypeError Python raises.
fn stored_path(state: &Map<String, Value>, key: &str, fallback: &Path, env: &Env) -> Result<PathBuf, ConfigError> {
    match state.get(key) {
        Some(v) if py_truthy(v) => resolve(path_arg(v)?, env),
        _ => resolve_path(fallback),
    }
}

pub fn get_data_location_state(env: &Env, folder: &DataFolder) -> Result<LocationState, ConfigError> {
    let state = read_state(env);
    let active = stored_path(&state, "active_path", &folder.user_data_dir, env)?;
    let pending = match state.get("pending_path") {
        Some(v) if py_truthy(v) => resolve(path_arg(v)?, env)?.display().to_string(),
        _ => String::new(),
    };
    Ok(LocationState { active_path: active.display().to_string(), pending_path: pending, environment_override: environment_override(env) })
}

fn is_archive_folder(dir: &Path) -> bool {
    dir.join(MANIFEST_NAME).exists() || dir.join("database").join("archive.db").exists()
}

fn mkdir(dir: &Path) -> Result<(), ConfigError> {
    fs::create_dir_all(dir).map_err(|e| ConfigError::os(&e, dir))
}

/// Settings asks for the data to move to `target_path`; nothing is copied until the next start.
pub fn request_data_move(env: &Env, folder: &DataFolder, target_path: &str) -> Result<Value, ConfigError> {
    let source = resolve_path(&folder.user_data_dir)?;
    let target = resolve(py_strip(target_path), env)?;
    if source == target {
        return Err(ConfigError::value("Destination is already the current Archive X data folder"));
    }
    if target.starts_with(&source) {
        return Err(ConfigError::value("Destination cannot be inside the current Archive X data folder"));
    }
    // Program files and user data stay apart, so a new version can be unpacked without touching the data.
    if target.starts_with(resolve_path(&env.app_dir)?) {
        return Err(ConfigError::value("Choose a folder outside the Archive X application folder"));
    }
    mkdir(&target)?;
    let occupied = fs::read_dir(&target).map_err(|e| ConfigError::os(&e, &target))?.next().is_some();
    // A folder that already holds Archive X data is accepted: it is the same destination from an interrupted move.
    if occupied && !is_archive_folder(&target) {
        return Err(ConfigError::value("Destination folder must be empty"));
    }
    write_state(env, &source, Some(&target))?;
    Ok(json!({"active_path": source.display().to_string(), "pending_path": target.display().to_string(), "restart_required": true}))
}

pub fn cancel_pending_data_move(env: &Env, folder: &DataFolder) -> Result<Value, ConfigError> {
    let active = stored_path(&read_state(env), "active_path", &folder.user_data_dir, env)?;
    write_state(env, &active, None)?;
    Ok(json!({"active_path": active.display().to_string(), "pending_path": "", "cancelled": true}))
}

/// `(number of files, their total size)`: the cheap check that a copy is complete.
fn tree_signature(root: &Path, ignore: Option<&str>) -> (u64, u64) {
    fn walk(dir: &Path, ignore: Option<&str>, acc: &mut (u64, u64)) {
        let Ok(entries) = fs::read_dir(dir) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            let is_link_to_dir = entry.file_type().map(|t| t.is_symlink()).unwrap_or(false) && path.is_dir();
            if path.is_dir() && !is_link_to_dir {
                walk(&path, ignore, acc);
            } else if let Ok(md) = fs::metadata(&path) {
                if md.is_file() && ignore.is_none_or(|name| entry.file_name() != name) {
                    acc.0 += 1;
                    acc.1 += md.len();
                }
            }
        }
    }
    let mut acc = (0, 0);
    if root.exists() {
        walk(root, ignore, &mut acc);
    }
    acc
}

/// `shutil.copytree(src, dst, dirs_exist_ok=True)`: links are followed, permissions and modification times are kept.
pub fn copy_tree(src: &Path, dst: &Path) -> io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let from = entry?.path();
        let to = dst.join(from.file_name().unwrap_or_default());
        let md = fs::metadata(&from)?;
        if md.is_dir() {
            copy_tree(&from, &to)?;
        } else {
            fs::copy(&from, &to)?;
            if let Ok(modified) = md.modified() {
                if let Ok(f) = fs::OpenOptions::new().write(true).open(&to) {
                    let _ = f.set_modified(modified);
                }
            }
        }
    }
    fs::set_permissions(dst, fs::metadata(src)?.permissions())
}

/// Finish a queued move: copy, verify, remove the old folder, record the new one. Returns the new folder, or `None` when nothing was queued.
pub fn complete_pending_move(env: &Env, folder: &DataFolder, pending: &str, log: &dyn Fn(&str)) -> Result<Option<DataFolder>, ConfigError> {
    if pending.is_empty() {
        return Ok(None);
    }
    let source = resolve_path(&folder.user_data_dir)?;
    let target = resolve(pending, env)?;
    if source == target {
        write_state(env, &source, None)?;
        return Ok(None);
    }
    if target.starts_with(&source) {
        return Err(ConfigError::runtime("The new data folder cannot be inside the current Archive X data folder."));
    }
    if !source.exists() {
        return Err(ConfigError::runtime(format!("Current Archive X data folder does not exist: {}", source.display())));
    }
    mkdir(&target)?;
    log(&format!("[storage] Moving Archive X data: {} -> {}", source.display(), target.display()));
    // Never merge into an unrelated, non-empty folder. A half-copied target of an interrupted move is safe to resume: its marker names this source.
    let marker = target.join(MARKER_NAME);
    let occupied = fs::read_dir(&target).map_err(|e| ConfigError::os(&e, &target))?.flatten().any(|e| e.file_name() != MARKER_NAME);
    if occupied {
        let marker_source = fs::read_to_string(&marker)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.as_object().and_then(|o| o.get("source").cloned()))
            .map(|v| py_str_or_empty(&v).unwrap_or_else(|_| "\0".into()))
            .unwrap_or_default();
        let same_source = !marker_source.is_empty() && resolve(&marker_source, env).is_ok_and(|p| p == source);
        if !same_source && !is_archive_folder(&target) {
            return Err(ConfigError::runtime(format!("Destination is not empty: {}", target.display())));
        }
    }
    let started = json!({"source": source.display().to_string(), "target": target.display().to_string(), "started_at": env.now_utc_iso()});
    fs::write(&marker, dumps_indent2(&started, true)).map_err(|e| ConfigError::os(&e, &marker))?;
    let before = tree_signature(&source, None);
    copy_tree(&source, &target).map_err(|e| ConfigError::os(&e, &target))?;
    // The marker is control data, not user data; it stays until the copy is verified so that an interrupted copy can resume.
    let after = tree_signature(&target, Some(MARKER_NAME));
    if before != after {
        return Err(ConfigError::runtime(format!("Archive X data verification failed: source={before:?}, destination={after:?}")));
    }
    let _ = fs::remove_file(&marker);
    log("[storage] Data copy verified; removing old Archive X data folder.");
    fs::remove_dir_all(&source).map_err(|e| ConfigError::os(&e, &source))?;
    write_state(env, &target, None)?;
    Ok(Some(DataFolder::new(target)))
}

/// Apply a validated, staged restore before the database is opened: the live folder is kept beside it as `Archive X PreRestore <time>`.
pub fn apply_pending_restore(env: &Env, folder: &DataFolder) -> Result<(), ConfigError> {
    let current = resolve_path(&folder.user_data_dir)?;
    let parent = current.parent().unwrap_or(&current).to_path_buf();
    let marker_path = folder.user_data_dir.parent().unwrap_or(&folder.user_data_dir).join(".archive_x_pending_restore.json");
    if !marker_path.exists() {
        return Ok(());
    }
    let text = fs::read_to_string(&marker_path).map_err(|e| ConfigError::os(&e, &marker_path))?;
    let marker: Value = serde_json::from_str(&text).map_err(|e| ConfigError::new("JSONDecodeError", e.to_string()))?;
    let Value::Object(marker) = marker else {
        return Err(ConfigError::new("AttributeError", format!("'{}' object has no attribute 'get'", crate::pyfmt::py_type_name(&marker))));
    };
    let staging = py_str_or_empty(marker.get("staging_path").unwrap_or(&Value::Null))?;
    let stage = resolve(&staging, env)?;
    if !stage.is_dir() {
        return Err(ConfigError::runtime(format!("Pending Archive X restore staging folder is missing: {}", stage.display())));
    }
    let rollback = parent.join(format!("Archive X PreRestore {}", env.local_stamp()));
    if current.exists() {
        fs::rename(&current, &rollback).map_err(|e| ConfigError::os(&e, &current))?;
    }
    fs::rename(&stage, &current).map_err(|e| ConfigError::os(&e, &stage))?;
    match fs::remove_file(&marker_path) {
        Err(e) if e.kind() != io::ErrorKind::NotFound => Err(ConfigError::os(&e, &marker_path)),
        _ => Ok(()),
    }
}
