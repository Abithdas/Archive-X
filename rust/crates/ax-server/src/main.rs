//! Archive X front door.
//!
//! Serves the built web app itself and forwards every other request to the Python backend, so route groups can
//! move to Rust one at a time (see docs/desktop/RUST-MIGRATION-PLAN.md, phase 2).

use ax_server::{config, server};
use std::sync::Arc;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();
    let cfg = match config::Config::from_env_and_args(std::env::args().skip(1)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("ax-server: {e}\n\n{}", config::USAGE);
            std::process::exit(2);
        }
    };
    if let Err(e) = server::run(Arc::new(cfg)).await {
        eprintln!("ax-server: {e}");
        std::process::exit(1);
    }
}
