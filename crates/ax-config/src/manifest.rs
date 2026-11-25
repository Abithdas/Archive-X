//! `PERSISTENT_DATA_MANIFEST.json`: the marker that tells an Archive X data folder from any other folder (storage moves and destination checks use it).

use crate::constants::DATA_MANIFEST_VERSION;
use crate::pyfmt::dumps_indent2;
use crate::{ConfigError, DataFolder, Env};
use serde_json::json;
use std::fs;
use std::path::PathBuf;

pub const FILE_NAME: &str = "PERSISTENT_DATA_MANIFEST.json";

/// The structure the folder is expected to have (not every user file).
pub const EXPECTED_DIRECTORIES: [&str; 26] = [
    "database",
    "auth",
    "auth/cert",
    "assets",
    "assets/thumbnails/playback",
    "assets/performers",
    "assets/studios",
    "assets/scenes",
    "assets/icons",
    "assets/fonts",
    "metadata",
    "metadata/media",
    "metadata/scenes",
    "metadata/performers",
    "metadata/studios",
    "metadata/sites",
    "metadata/reference",
    "ai/evidence",
    "cache/player_cache",
    "automation",
    "automation/downloads",
    "automation/torrent_engine",
    "state",
    "state/runtime",
    "state/media_manager",
    "logs",
];

pub fn write_persistent_data_manifest(env: &Env, folder: &DataFolder) -> Result<PathBuf, ConfigError> {
    let payload = json!({
        "manifest_version": DATA_MANIFEST_VERSION,
        "application": "Archive X",
        "application_version": env.app_version,
        "data_schema_version": 1,
        "data_root": folder.user_data_dir.display().to_string(),
        "generated_at": env.now_utc_iso(),
        "expected_directories": EXPECTED_DIRECTORIES,
        "compatibility": {
            "legacy_data_environment": "TELEGRAM_ARCHIVE_DATA_DIR",
            "legacy_session_filename": "telegram_archive.session",
            "current_session_filename": "archive_x.session",
        },
    });
    let path = folder.user_data_dir.join(FILE_NAME);
    let tmp = folder.user_data_dir.join(format!("{FILE_NAME}.tmp"));
    fs::write(&tmp, dumps_indent2(&payload, false) + "\n").map_err(|e| ConfigError::os(&e, &tmp))?;
    fs::rename(&tmp, &path).map_err(|e| ConfigError::os(&e, &path))?;
    Ok(path)
}
