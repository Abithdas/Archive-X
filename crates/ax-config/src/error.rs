use std::fmt;
use std::io;
use std::path::Path;

/// An error with the name of the Python exception it stands for and the text Python gives it, so a caller (and the tests) can tell what
/// Python would have raised.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigError {
    pub kind: &'static str,
    pub message: String,
}

impl ConfigError {
    pub fn new(kind: &'static str, message: impl Into<String>) -> Self {
        ConfigError { kind, message: message.into() }
    }

    pub fn runtime(message: impl Into<String>) -> Self {
        Self::new("RuntimeError", message)
    }

    pub fn value(message: impl Into<String>) -> Self {
        Self::new("ValueError", message)
    }

    /// An operating-system error the way Python words it: `[Errno 17] File exists: '/path'`.
    pub fn os(err: &io::Error, path: &Path) -> Self {
        let Some(code) = err.raw_os_error() else {
            return Self::new("OSError", format!("{err}: '{}'", path.display()));
        };
        let text = err.to_string();
        let text = text.split(" (os error").next().unwrap_or(&text);
        let kind = match code {
            17 => "FileExistsError",
            2 => "FileNotFoundError",
            13 | 1 => "PermissionError",
            20 => "NotADirectoryError",
            21 => "IsADirectoryError",
            _ => "OSError",
        };
        Self::new(kind, format!("[Errno {code}] {text}: '{}'", path.display()))
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind, self.message)
    }
}

impl std::error::Error for ConfigError {}
