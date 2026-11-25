//! The few settings of `config.py` that come from environment variables (everything else in `config.py` is a fixed value, in `constants`).

use crate::pyfmt::{py_float_str, py_int_str};
use crate::{ConfigError, Env};

#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    /// `TELEGRAM_ARCHIVE_HOST`.
    pub host: String,
    /// `TELEGRAM_ARCHIVE_PORT`.
    pub port: i64,
    /// `TELEGRAM_ARCHIVE_PLAYER_PORT`.
    pub local_player_port: i64,
    /// `TELEGRAM_ARCHIVE_PLAYER_HOST`.
    pub local_player_host: String,
    /// `ARCHIVE_X_METADATA_CACHE_TTL_SECONDS`, never below zero.
    pub metadata_cache_ttl_seconds: f64,
    /// `ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS`, never below five.
    pub telegram_background_connect_timeout_seconds: f64,
}

/// `int(os.getenv(name, default))`: a variable that is set but empty is an error, as in Python.
fn int_var(env: &Env, name: &str, default: &str) -> Result<i64, ConfigError> {
    py_int_str(env.get(name).unwrap_or(default))
}

fn float_var(env: &Env, name: &str, default: &str) -> Result<f64, ConfigError> {
    py_float_str(env.get(name).unwrap_or(default))
}

impl Settings {
    /// Read in the order `config.py` does, so the first bad value is the one reported.
    pub fn from_env(env: &Env) -> Result<Settings, ConfigError> {
        let host = env.get("TELEGRAM_ARCHIVE_HOST").unwrap_or("0.0.0.0").to_string();
        let port = int_var(env, "TELEGRAM_ARCHIVE_PORT", "8443")?;
        let local_player_port = int_var(env, "TELEGRAM_ARCHIVE_PLAYER_PORT", "8444")?;
        let local_player_host = env.get("TELEGRAM_ARCHIVE_PLAYER_HOST").unwrap_or("0.0.0.0").to_string();
        // Python's max(a, b) keeps `a` unless `b` is greater, so a NaN gives `a`.
        let ttl = float_var(env, "ARCHIVE_X_METADATA_CACHE_TTL_SECONDS", &(90 * 86400).to_string())?;
        let timeout = float_var(env, "ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS", "20")?;
        Ok(Settings {
            host,
            port,
            local_player_port,
            local_player_host,
            metadata_cache_ttl_seconds: if ttl > 0.0 { ttl } else { 0.0 },
            telegram_background_connect_timeout_seconds: if timeout > 5.0 { timeout } else { 5.0 },
        })
    }
}
