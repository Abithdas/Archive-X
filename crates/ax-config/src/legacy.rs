//! Copying data forward from older install layouts (`config.migrate_legacy_data`). Nothing in the old tree is deleted, so removing the old
//! program folder after an upgrade is safe.

use crate::location::copy_tree;
use crate::pyfmt::lexical;
use crate::{ConfigError, DataFolder, Env};
use std::fs;
use std::path::{Path, PathBuf};

fn copy_if_missing(src: &Path, dst: &Path, moved: &mut Vec<String>) -> Result<(), ConfigError> {
    if !src.exists() || dst.exists() {
        return Ok(());
    }
    if let Some(parent) = dst.parent() {
        fs::create_dir_all(parent).map_err(|e| ConfigError::os(&e, parent))?;
    }
    if src.is_dir() {
        copy_tree(src, dst).map_err(|e| ConfigError::os(&e, dst))?;
    } else {
        fs::copy(src, dst).map_err(|e| ConfigError::os(&e, dst))?;
        if let Some(modified) = fs::metadata(src).ok().and_then(|m| m.modified().ok()) {
            if let Ok(f) = fs::OpenOptions::new().write(true).open(dst) {
                let _ = f.set_modified(modified);
            }
        }
    }
    moved.push(format!("{} -> {}", src.display(), dst.display()));
    Ok(())
}

/// Returns one `"from -> to"` line per item copied, in the order Python does.
///
/// Note that the folders listed here (certificates, player cache, icons, …) are already made by start-up, so for those the copy never
/// happens: `dst.exists()` is true by then. The files that start-up does not make are the ones that migrate. This is kept as it is.
pub fn migrate_legacy_data(env: &Env, folder: &DataFolder) -> Result<Vec<String>, ConfigError> {
    let p = &folder.paths;
    let mut moved = Vec::new();
    let root = lexical(&env.app_dir);
    let legacy = lexical(root.join("data"));
    let legacy_config = lexical(root.join("config.json"));
    let db_dir = p.db_path.parent().unwrap_or(&folder.user_data_dir).to_path_buf();

    // The database, its write-ahead log and its shared-memory file must move together when they exist.
    let files: [(&str, &Path); 7] = [
        ("archive.db", &p.db_path),
        ("archive.db-wal", &db_dir.join("archive.db-wal")),
        ("archive.db-shm", &db_dir.join("archive.db-shm")),
        ("metadata_keys.json", &p.metadata_keys_path),
        ("archive_x.session", &p.session_path),
        ("playback.log", &p.playback_log_path),
        ("reference_export.txt", &p.reference_export_path),
    ];
    let legacy_exists = legacy.exists();
    for (name, target) in files {
        let source = if legacy_exists { legacy.join(name) } else { root.join(name) };
        copy_if_missing(&source, target, &mut moved)?;
    }

    // Certificates, session sidecars and the large runtime caches.
    for (name, target) in [
        ("cert", &p.cert_dir),
        ("player_cache", &p.player_cache_dir),
        ("automation_downloads", &p.automation_download_dir),
        ("automation_torrent_engine", &p.automation_engine_dir),
        ("media_manager", &p.media_manager_state_dir),
        ("icons", &p.icon_dir),
        ("fonts", &p.font_dir),
        ("ai_evidence", &p.ai_evidence_dir),
    ] {
        copy_if_missing(&legacy.join(name), target, &mut moved)?;
    }

    // The old settings file is only an input; the new one lives in the data folder.
    if !p.config_path.exists() && legacy_config.exists() {
        copy_if_missing(&legacy_config, &p.config_path, &mut moved)?;
    }

    // Session and certificate files that predate the data/ folder.
    copy_if_missing(&p.auth_dir.join("telegram_archive.session"), &p.session_path, &mut moved)?;
    let beside_program: Vec<(PathBuf, PathBuf)> = vec![
        (root.join("archive_x.session"), p.session_path.clone()),
        (root.join("telegram_archive.session"), p.session_path.clone()),
        (root.join("cert"), p.cert_dir.clone()),
        (root.join("archive.db"), p.db_path.clone()),
        (root.join("archive.db-wal"), db_dir.join("archive.db-wal")),
        (root.join("archive.db-shm"), db_dir.join("archive.db-shm")),
    ];
    for (src, dst) in &beside_program {
        copy_if_missing(src, dst, &mut moved)?;
    }

    // The standalone media manager's state, kept beside the program or under LOCALAPPDATA.
    let local = env.get("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| env.home.clone());
    let legacy_mm = lexical(local.join("TelegramVideoThumbnailUploader"));
    copy_if_missing(&legacy_mm.join("telegram_uploader_config.json"), &p.media_manager_config_path, &mut moved)?;
    copy_if_missing(&legacy_mm.join("telegram_uploader"), &p.media_manager_session_path, &mut moved)?;
    copy_if_missing(&legacy_mm.join("telegram_uploader.session"), &p.media_manager_session_path, &mut moved)?;
    copy_if_missing(&root.join("telegram_uploader_config.json"), &p.media_manager_config_path, &mut moved)?;
    copy_if_missing(&root.join("telegram_uploader"), &p.media_manager_session_path, &mut moved)?;
    for name in [
        "telegram_vertical_video_archiver_state.json",
        "telegram_smart_video_router_state.json",
        "telegram_vertical_mobile_mover_state.json",
        "telegram_transfer_job_state.json",
        "telegram_channel_cleanup_state.json",
        "telegram_channel_organizer_state.json",
        "telegram_duplicate_finder_state.json",
    ] {
        copy_if_missing(&root.join(name), &p.media_manager_state_dir.join(name), &mut moved)?;
    }
    Ok(moved)
}
