//! Archive X front door (library part, so the tests can start the server in-process).
pub mod auth;
pub mod config;
pub mod guard;
pub mod native;
pub mod optimize;
pub mod proxy;
pub mod server;
pub mod static_files;
