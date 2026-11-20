//! Pure leaf logic ported from `telegram_archive/core` (Phase 1 of `docs/rust-migration-plan.md`).
//!
//! Each module is a line-for-line port of one Python module, with the Python behaviour (including its quirks) pinned by tests that
//! were written from the Python source. No module here touches the network or the database.

pub mod duration_filter;
pub mod resolution_filter;
pub mod retry_policy;
pub mod safe_paths;
pub mod sql_batches;
