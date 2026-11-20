//! The durable job system, ported from `telegram_archive/jobs/` (Phase 3 of `docs/rust-migration-plan.md`).
//!
//! Built on the database layer (`ax-db::{jobs, leases, job_reads}`), the resource policy and the governor policy of `ax-core`.

pub mod manager_policy;
pub mod orchestrator;
