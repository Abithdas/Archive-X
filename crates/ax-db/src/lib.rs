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
    // The same read settings as the Python side (core/database.py): a 32 MB page cache, 256 MB of memory-mapped file and temporary
    // tables in memory. With SQLite's small defaults a heavy query over a big library re-reads pages again and again, and was several
    // times slower than Python's under load.
    conn.pragma_update(None, "temp_store", "MEMORY")?;
    conn.pragma_update(None, "cache_size", -32768)?;
    conn.pragma_update(None, "mmap_size", 268_435_456_i64)?;
    Ok(conn)
}

/// The one connection Rust writes through, for the tables Rust owns (docs/adr/0002 and 0007). It never creates the file and opens only
/// when the first write arrives, so a front door that only reads never needs write access. Writes are short transactions: SQLite's own
/// locking and the 5 s busy timeout order them against Python's writes to other tables.
#[derive(Clone)]
pub struct Writer(Arc<WriterInner>);

struct WriterInner {
    path: PathBuf,
    conn: Mutex<Option<Connection>>,
}

impl Writer {
    pub fn new(path: impl Into<PathBuf>) -> Writer {
        Writer(Arc::new(WriterInner { path: path.into(), conn: Mutex::new(None) }))
    }

    /// Run `f` inside one `BEGIN IMMEDIATE` transaction on a blocking thread; committed if it returns Ok, rolled back otherwise.
    pub async fn write<T, F>(&self, f: F) -> Result<T, DbError>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> rusqlite::Result<T> + Send + 'static,
    {
        let inner = self.0.clone();
        tokio::task::spawn_blocking(move || {
            let mut guard = inner.conn.lock().unwrap();
            if guard.is_none() {
                let c = Connection::open_with_flags(&inner.path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX | OpenFlags::SQLITE_OPEN_URI)
                    .map_err(|e| DbError::Open(format!("{}: {e}", inner.path.display())))?;
                c.busy_timeout(BUSY_TIMEOUT)?;
                *guard = Some(c);
            }
            let conn = guard.as_ref().unwrap();
            conn.execute_batch("BEGIN IMMEDIATE")?;
            match f(conn) {
                Ok(v) => match conn.execute_batch("COMMIT") {
                    Ok(()) => Ok(v),
                    Err(e) => {
                        // A failed COMMIT (a busy lock, say) leaves the transaction open; the next write would fail with
                        // "cannot start a transaction within a transaction" until the app restarted.
                        let _ = conn.execute_batch("ROLLBACK");
                        Err(DbError::from(e))
                    }
                },
                Err(e) => {
                    let _ = conn.execute_batch("ROLLBACK");
                    Err(DbError::from(e))
                }
            }
        })
        .await
        .map_err(|e| DbError::Task(e.to_string()))?
    }
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
    ("photo_face_groups", &["id", "label", "performer_id", "updated_at"]),
    ("photo_face_detections", &["id", "group_id", "photo_id"]),
    ("photo_face_analysis", &["photo_id", "status"]),
    ("channels", &["id", "telegram_id", "title"]),
    ("studios", &["id", "name", "image"]),
    ("tags", &["id", "name"]),
    ("entity_favorites", &["entity_type", "entity_id"]),
    ("studio_image_cache", &["studio_id", "updated_at"]),
    ("video_studios", &["video_id", "studio_id"]),
    ("video_tags", &["video_id", "tag_id"]),
    (
        "media",
        &["id", "source_channel", "source_message_id", "title", "filename", "status", "upload_date", "thumbnail_message_id", "site_url", "updated_at"],
    ),
];
