//! Read-only access to the Archive X database.
//!
//! The Python backend stays the only writer. Rust opens the same SQLite file read-only, in WAL mode, with the same busy
//! timeout, and runs every query on a blocking thread so the async server is never held up by the disk.

pub mod sessions;

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rusqlite::{Connection, OpenFlags};

pub use rusqlite;

/// How long a reader waits for a writer's lock (the Python side uses the same 5 seconds).
pub const BUSY_TIMEOUT: Duration = Duration::from_millis(5000);
const MAX_IDLE: usize = 8;

#[derive(Debug)]
pub enum DbError {
    /// The database file is missing or cannot be opened.
    Open(String),
    Sqlite(rusqlite::Error),
    /// The blocking task was cancelled or panicked.
    Task(String),
}

impl std::fmt::Display for DbError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DbError::Open(e) => write!(f, "cannot open the database: {e}"),
            DbError::Sqlite(e) => write!(f, "database error: {e}"),
            DbError::Task(e) => write!(f, "database task failed: {e}"),
        }
    }
}
impl std::error::Error for DbError {}
impl From<rusqlite::Error> for DbError {
    fn from(e: rusqlite::Error) -> Self {
        DbError::Sqlite(e)
    }
}

struct Inner {
    path: PathBuf,
    idle: Mutex<Vec<Connection>>,
}

/// A small pool of read-only connections. Cheap to clone.
#[derive(Clone)]
pub struct Db(Arc<Inner>);

fn connect(path: &Path) -> Result<Connection, DbError> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX | OpenFlags::SQLITE_OPEN_URI)
        .map_err(|e| DbError::Open(format!("{}: {e}", path.display())))?;
    conn.busy_timeout(BUSY_TIMEOUT)?;
    // Belt and braces: even a bug in a query can never write through this connection.
    conn.pragma_update(None, "query_only", "ON")?;
    Ok(conn)
}

impl Db {
    /// Open the database read-only. Fails if the file does not exist (Rust never creates the database).
    pub fn open_read_only(path: impl Into<PathBuf>) -> Result<Db, DbError> {
        let path = path.into();
        let first = connect(&path)?;
        Ok(Db(Arc::new(Inner { path, idle: Mutex::new(vec![first]) })))
    }

    /// Run `f` with a read-only connection on a blocking thread.
    pub async fn read<T, F>(&self, f: F) -> Result<T, DbError>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> rusqlite::Result<T> + Send + 'static,
    {
        let inner = self.0.clone();
        tokio::task::spawn_blocking(move || {
            let pooled = inner.idle.lock().unwrap().pop();
            let conn = match pooled {
                Some(c) => c,
                None => connect(&inner.path)?,
            };
            let out = f(&conn);
            let mut idle = inner.idle.lock().unwrap();
            if idle.len() < MAX_IDLE {
                idle.push(conn);
            }
            out.map_err(DbError::from)
        })
        .await
        .map_err(|e| DbError::Task(e.to_string()))?
    }

    pub fn path(&self) -> &Path {
        &self.0.path
    }
}

/// The tables and columns the Rust code reads, per table. A test checks these against the committed snapshot of the
/// schema the Python side creates (schema/schema.json), so a rename on the Python side cannot silently break Rust.
pub const REQUIRED_COLUMNS: &[(&str, &[&str])] = &[
    ("web_sessions", &["token_hash", "username", "expires"]),
    ("channels", &["telegram_id", "title", "username", "image", "updated_at"]),
    ("photo_library_items", &["id", "channel_id"]),
    ("photo_library_scan_state", &["channel_id", "status", "last_scan_at", "messages_scanned", "photos_found", "last_error"]),
    ("performers", &["id", "name", "gender", "image"]),
    ("video_performers", &["performer_id", "video_id"]),
    ("local_video_performers", &["performer_id", "video_id"]),
    ("performer_image_cache", &["performer_id", "data"]),
    ("media", &["id"]),
];
