//! Where Archive X keeps its data and how it starts, ported from `telegram_archive/config.py` and the data-handling half of `main.py`.
//!
//! Importing `config.py` *is* Python's start-up: it picks the data folder, finishes a queued move, applies a staged restore, creates
//! the folders and writes the manifest. [`startup::start`] does the same steps in the same order, so a folder left half-way by a failure
//! looks the same either way. Everything is checked against the real Python in temporary folders (`tools/parity/gen_layout_vectors.py`,
//! `tests/layout.rs`).
//!
//! Not wired in yet: the Python backend still starts itself. This crate is what the Rust start-up will call.

mod clock;
pub mod constants;
pub mod desktop;
mod env;
mod error;
pub mod instance_lock;
pub mod layout;
pub mod legacy;
pub mod location;
pub mod manifest;
pub mod pyfmt;
pub mod secrets;
pub mod settings;
pub mod startup;

pub use env::Env;
pub use error::ConfigError;
pub use layout::{DataFolder, Layout};
