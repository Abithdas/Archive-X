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
    cd rust && cargo test --workspace
    cd desktop && cargo test -p archive-x-launcher-core -p archive-x-native-player -p archive-x-desktop --lib

test-web:
    cd frontend && npm test

# Type checks and lints.
lint:
    cd rust && cargo clippy --workspace --all-targets -- -D warnings
    cd desktop && cargo clippy --all-targets -- -D warnings
    cd frontend && npm run typecheck

# The web app, built.
build-web:
    cd frontend && npm run build
