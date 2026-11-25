//! Start-up, in the order Python's `import config` and `main.main()` do it.

use crate::layout::DataFolder;
use crate::location::{apply_pending_restore, choose, complete_pending_move};
use crate::manifest::write_persistent_data_manifest;
use crate::pyfmt::{dumps_indent2, resolve_path};
use crate::settings::Settings;
use crate::{ConfigError, Env};
use serde_json::json;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct Started {
    pub folder: DataFolder,
    pub settings: Settings,
}

/// What `import config` does: choose the data folder, finish a queued move, apply a staged restore, create the folders, read the settings
/// from the environment, write the manifest. A failure at any step leaves the earlier steps' work in place, as in Python.
pub fn start(env: &Env, log: &dyn Fn(&str)) -> Result<Started, ConfigError> {
    let chosen = choose(env)?;
    let mut folder = DataFolder::new(chosen.user_data_dir);
    if !chosen.from_environment {
        if let Some(moved) = complete_pending_move(env, &folder, &chosen.pending, log)? {
            folder = moved;
        }
        apply_pending_restore(env, &folder)?;
    }
    folder.create_dirs()?;
    let settings = Settings::from_env(env)?;
    write_persistent_data_manifest(env, &folder)?;
    Ok(Started { folder, settings })
}

/// `main.write_default_config`: a first-run settings file with the default web user name, unless one exists.
pub fn write_default_config(folder: &DataFolder) -> Result<(), ConfigError> {
    let path = &folder.paths.config_path;
    if path.exists() {
        return Ok(());
    }
    let payload = json!({
        "web_username": "admin",
        "notes": "Web UI binds to all local interfaces for same-LAN access. Keep Windows Firewall restricted to trusted/private networks.",
    });
    fs::write(path, dumps_indent2(&payload, true)).map_err(|e| ConfigError::os(&e, path))
}

/// Modules of earlier versions that the application removes from its own folder.
pub const OBSOLETE_FILES: [&str; 17] = [
    "engines/scanner.py",
    "engines/router_engine.py",
    "engines/duplicate_engine.py",
    "engines/thumbnail_repair.py",
    "engines/intake_watcher.py",
    "engines/thumbnail_engine.py",
    "telegram/watcher.py",
    "telegram/scanner.py",
    "telegram/files.py",
    "core/assistant.py",
    "video/__init__.py",
    "video/analyzer.py",
    "video/duplicate.py",
    "video/router.py",
    "video/streamability.py",
    "video/thumbnail.py",
    "video/vertical.py",
];

/// `Path.suffix`: from the last dot, unless it is the first character or the last one.
fn py_suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if i > 0 && i + 1 < name.len() => &name[i..],
        _ => "",
    }
}

/// `main.remove_obsolete_files`. An installed copy is read-only and replaced as a whole by the installer, so `frozen` leaves it alone.
/// Returns the modules removed.
pub fn remove_obsolete_files(package_dir: &Path, frozen: bool) -> Result<Vec<String>, ConfigError> {
    let mut removed = Vec::new();
    if frozen {
        return Ok(removed);
    }
    for rel in OBSOLETE_FILES {
        let path = package_dir.join(rel);
        // A path that cannot be removed (a folder of that name, no permission) is skipped.
        if path.exists() && fs::remove_file(&path).is_ok() {
            removed.push(rel.to_string());
        }
    }
    let video = package_dir.join("video");
    if video.exists() {
        let entries = fs::read_dir(&video).map_err(|e| ConfigError::os(&e, &video))?;
        let has_module = entries.flatten().any(|e| py_suffix(&e.file_name().to_string_lossy()) == ".py");
        if !has_module {
            let _ = fs::remove_dir_all(&video);
        }
    }
    Ok(removed)
}

/// Where the backend is installed and whether the program folder is a read-only bundle; the caller knows.
pub fn package_dir_of(app_dir: &Path) -> Result<std::path::PathBuf, ConfigError> {
    resolve_path(&app_dir.join("telegram_archive"))
}
