//! The data folder's layout (`config.py`, "User-data location"): where every kind of file lives under the data root.

use crate::pyfmt::lexical;
use crate::{ConfigError, Env};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

macro_rules! layout {
    ($($field:ident : $name:literal = $rel:literal;)+) => {
        /// Every path `config.py` builds from the data folder.
        #[derive(Debug, Clone, PartialEq, Eq)]
        pub struct Layout {
            $(pub $field: PathBuf,)+
        }

        impl Layout {
            /// The layout of a data folder (`root` must already be resolved).
            pub fn new(root: &Path) -> Layout {
                Layout { $($field: lexical(root.join($rel)),)+ }
            }

            /// The names `config.py` gives these paths, with the paths.
            fn derived(&self) -> Vec<(&'static str, &Path)> {
                vec![$(($name, self.$field.as_path()),)+]
            }
        }
    };
}

// What each path is made of, relative to the data folder.
layout! {
    database_dir: "DATABASE_DIR" = "database";
    auth_dir: "AUTH_DIR" = "auth";
    asset_dir: "ASSET_DIR" = "assets";
    thumbnail_dir: "THUMBNAIL_DIR" = "assets/thumbnails/playback";
    performer_asset_dir: "PERFORMER_ASSET_DIR" = "assets/performers";
    studio_asset_dir: "STUDIO_ASSET_DIR" = "assets/studios";
    scene_asset_dir: "SCENE_ASSET_DIR" = "assets/scenes";
    metadata_dir: "METADATA_DIR" = "metadata";
    scene_metadata_dir: "SCENE_METADATA_DIR" = "metadata/scenes";
    media_metadata_dir: "MEDIA_METADATA_DIR" = "metadata/media";
    performer_metadata_dir: "PERFORMER_METADATA_DIR" = "metadata/performers";
    studio_metadata_dir: "STUDIO_METADATA_DIR" = "metadata/studios";
    site_metadata_dir: "SITE_METADATA_DIR" = "metadata/sites";
    reference_dir: "REFERENCE_DIR" = "metadata/reference";
    ai_dir: "AI_DIR" = "ai";
    ai_models_dir: "AI_MODELS_DIR" = "ai/models";
    ai_evidence_dir: "AI_EVIDENCE_DIR" = "ai/evidence";
    cache_dir: "CACHE_DIR" = "cache";
    player_cache_dir: "PLAYER_CACHE_DIR" = "cache/player_cache";
    playback_asset_dir: "PLAYBACK_ASSET_DIR" = "cache/player_cache/assets";
    automation_dir: "AUTOMATION_DIR" = "automation";
    automation_download_dir: "AUTOMATION_DOWNLOAD_DIR" = "automation/downloads";
    automation_engine_dir: "AUTOMATION_ENGINE_DIR" = "automation/torrent_engine";
    state_dir: "STATE_DIR" = "state";
    runtime_state_dir: "RUNTIME_STATE_DIR" = "state/runtime";
    media_manager_state_dir: "MEDIA_MANAGER_STATE_DIR" = "state/media_manager";
    media_manager_config_path: "MEDIA_MANAGER_CONFIG_PATH" = "state/media_manager/telegram_uploader_config.json";
    media_manager_session_path: "MEDIA_MANAGER_SESSION_PATH" = "auth/telegram_media_manager.session";
    log_dir: "LOG_DIR" = "logs";
    playback_log_path: "PLAYBACK_LOG_PATH" = "logs/playback.log";
    cli_log_path: "CLI_LOG_PATH" = "logs/launcher-cli.log";
    icon_dir: "ICON_DIR" = "assets/icons";
    font_dir: "FONT_DIR" = "assets/fonts";
    cert_dir: "CERT_DIR" = "auth/cert";
    db_path: "DB_PATH" = "database/archive.db";
    metadata_keys_path: "METADATA_KEYS_PATH" = "auth/provider_keys.json";
    secrets_path: "SECRETS_PATH" = "auth/credentials.json";
    config_path: "CONFIG_PATH" = "settings.json";
    session_path: "SESSION_PATH" = "auth/archive_x.session";
    cert_path: "CERT_PATH" = "auth/cert/cert.pem";
    key_path: "KEY_PATH" = "auth/cert/key.pem";
    reference_export_path: "REFERENCE_EXPORT_PATH" = "metadata/reference/reference_export.txt";
}

impl Layout {
    /// The folders start-up creates, in the order of the loop at the end of the "User-data location" section of `config.py`.
    fn dirs_to_create<'a>(&'a self, root: &'a Path) -> Vec<&'a Path> {
        vec![
            root,
            &self.database_dir,
            &self.auth_dir,
            &self.asset_dir,
            &self.thumbnail_dir,
            &self.performer_asset_dir,
            &self.studio_asset_dir,
            &self.scene_asset_dir,
            &self.metadata_dir,
            &self.scene_metadata_dir,
            &self.media_metadata_dir,
            &self.performer_metadata_dir,
            &self.studio_metadata_dir,
            &self.site_metadata_dir,
            &self.reference_dir,
            &self.ai_dir,
            &self.ai_models_dir,
            &self.ai_evidence_dir,
            &self.cache_dir,
            &self.player_cache_dir,
            &self.playback_asset_dir,
            &self.automation_dir,
            &self.automation_download_dir,
            &self.automation_engine_dir,
            &self.font_dir,
            &self.state_dir,
            &self.media_manager_state_dir,
            &self.log_dir,
            &self.icon_dir,
            &self.cert_dir,
        ]
    }
}

/// A data folder together with the paths that hang off it, and the few that do not (the location file, the legacy folders).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DataFolder {
    pub user_data_dir: PathBuf,
    pub paths: Layout,
}

impl DataFolder {
    pub fn new(root: PathBuf) -> DataFolder {
        let paths = Layout::new(&root);
        DataFolder { user_data_dir: root, paths }
    }

    /// Create the folders (`mkdir(parents=True, exist_ok=True)`, in `config.py`'s order).
    pub fn create_dirs(&self) -> Result<(), ConfigError> {
        for dir in self.paths.dirs_to_create(&self.user_data_dir) {
            std::fs::create_dir_all(dir).map_err(|e| ConfigError::os(&e, dir))?;
        }
        // config.py makes the media manager's configuration folder once more; it is the state folder already made above.
        let parent = self.paths.media_manager_config_path.parent().unwrap_or(&self.user_data_dir);
        std::fs::create_dir_all(parent).map_err(|e| ConfigError::os(&e, parent))
    }

    /// Every path-valued name of `config.py` (apart from the program's own folder) with the text Python gives it.
    pub fn named_paths(&self, env: &Env) -> BTreeMap<String, String> {
        let mut out: BTreeMap<String, String> = self.paths.derived().into_iter().map(|(k, v)| (k.to_string(), v.display().to_string())).collect();
        out.insert("USER_DATA_DIR".into(), self.user_data_dir.display().to_string());
        out.insert("DATA_DIR".into(), self.user_data_dir.display().to_string());
        out.insert("DATA_LOCATION_FILE".into(), crate::location::data_location_file(env).display().to_string());
        out.insert("LEGACY_DATA_DIR".into(), lexical(env.app_dir.join("data")).display().to_string());
        out.insert("LEGACY_CONFIG_PATH".into(), lexical(env.app_dir.join("config.json")).display().to_string());
        out
    }
}
