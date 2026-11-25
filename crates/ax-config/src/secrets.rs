//! The secrets file (`auth/credentials.json`, sealed with the vault): login, API keys and tokens, kept apart from the application database.

use crate::constants::SECRET_KEYS;
use ax_core::vault::{Vault, VaultError};
use serde_json::{Map, Value};
use std::path::PathBuf;

/// The purpose string the file is sealed under; Python uses the same.
pub const PURPOSE: &str = "archive-x-credentials-v1";

/// `config.is_secret_key`.
pub fn is_secret_key(key: &str) -> bool {
    SECRET_KEYS.contains(&key)
}

pub struct SecretsStore {
    pub vault: Vault,
    pub path: PathBuf,
}

impl SecretsStore {
    pub fn new(vault: Vault, path: PathBuf) -> SecretsStore {
        SecretsStore { vault, path }
    }

    /// `config.load_secrets`: the stored object, empty when there is no file.
    pub fn load(&self) -> Result<Map<String, Value>, VaultError> {
        self.vault.read_json(&self.path, PURPOSE, None)
    }

    /// `config.get_secret`: a missing key, or a stored null, gives `default`.
    pub fn get(&self, key: &str, default: Value) -> Result<Value, VaultError> {
        Ok(match self.load()?.get(key) {
            None | Some(Value::Null) => default,
            Some(v) => v.clone(),
        })
    }

    /// `config.set_secret`: null or an empty string deletes the key; anything else (including 0 and false) is stored.
    pub fn set(&self, key: &str, value: Value) -> Result<(), VaultError> {
        let mut payload = self.load()?;
        if value.is_null() || value.as_str() == Some("") {
            payload.shift_remove(key);
        } else {
            payload.insert(key.to_string(), value);
        }
        self.vault.write_json(&self.path, &payload, PURPOSE)
    }
}
