//! Create a fresh Archive X database: the same tables, indexes, triggers, views and starting rows the Python backend creates.
//!
//! `schema/schema.sql` is generated from a database the Python code just created (`tools/parity/dump_schema_sql.py`; CI checks that it is
//! current), so a new database made here is the one Python would make. Upgrading an existing database is a different matter (migrations); that
//! is not done here.

use rusqlite::Connection;

/// The SQL of a fresh database.
pub const FRESH_SCHEMA_SQL: &str = include_str!("../schema/schema.sql");

/// Create every table, index, trigger and view in an empty database and add the starting rows, in one transaction. Refuses a database that
/// already has tables (use the migrations for those).
pub fn create_fresh(conn: &Connection) -> rusqlite::Result<()> {
    let existing: i64 = conn.query_row("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'", [], |r| r.get(0))?;
    if existing > 0 {
        return Err(rusqlite::Error::SqliteFailure(rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_MISUSE), Some("the database already has tables".to_string())));
    }
    // the settings Python's connection uses: write-ahead log, so readers and the one writer do not block each other
    let _: String = conn.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
    conn.execute_batch("BEGIN IMMEDIATE")?;
    match conn.execute_batch(FRESH_SCHEMA_SQL) {
        Ok(()) => conn.execute_batch("COMMIT"),
        Err(e) => {
            let _ = conn.execute_batch("ROLLBACK");
            Err(e)
        }
    }
}
