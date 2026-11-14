# Common tasks. Install `just` (https://github.com/casey/just), then `just <name>`.
# Every command here is what CI runs, so a green `just check` locally means a green CI.

default:
    @just --list

# Everything CI checks.
check: test lint

# All tests: Python, the Rust workspaces, the web app.
test: test-py test-rust test-web

test-py:
    python -m pytest tools/tests -q

test-rust:
    cargo test --workspace --lib --tests

test-web:
    cd frontend && npm test

# Type checks and lints.
lint:
    cargo clippy --workspace --all-targets -- -D warnings
    cd frontend && npm run typecheck

# The web app, built.
build-web:
    cd frontend && npm run build
