# Contributing

The migration plan ([docs/plan.md](docs/plan.md)) is the source of truth for what to work on and in what order. These are the rules it relies on.

## Rules

1. **The app works after every commit.** Risky changes sit behind a switch that can be turned off without a new build (`--native-routes`, a setting, an environment variable).
2. **Three things stay compatible until the Python backend is gone:** the HTTP API the web app uses, the SQLite database file, and the data folder.
3. **One writer per table group.** Rust may read any table. A table is written by Python or by Rust, never both. A group changes owner in one step.
4. **New features are written in Rust.** Python gets bug fixes and deletions only.
5. **Delete, do not refactor, Python.** It is going away; tidying it is wasted work.
6. **A route moves to Rust only when the parity check passes** (`tools/parity/compare.py`) and it ships switched off first.
7. **Nothing is "done" without evidence:** a test, a parity run, a measured number, or a result from a real PC. What could not be checked is written down as not checked.
8. **Secrets stay in one place.** Python owns the login session and the provider keys until the login moves; Rust asks for them over `/internal/` routes that need the launch secret.

## Moving a route from Python to Rust

1. Read the Python handler and everything it calls; write down its parameters, answers, errors, and the tables it reads and writes.
2. Seed data that exercises it and record Python's answers.
3. Write the Rust version in the right crate, with tests whose expected values can be checked by hand.
4. Wire it into `ax-server` behind a route name, off by default.
5. Run the parity check: same request to both, same status, headers and body. Requests the Rust version does not handle are declined and go on to Python.
6. Measure on the big seeded library with one client and eight.
7. Switch it on in one release; watch a week of logs.
8. Delete the Python handler and what only it used; regenerate `docs/api/`.

A write route also moves every other writer of that table group in the same release and is tested against an interrupted write.

## Commits

One change per commit, with a message that says what changed and why. A commit that moves files contains no other change. Run `just check` before pushing.

## Tests

- Python: `python -m pytest tools/tests -q`. Each test uses its own data folder; none may depend on another's leftovers.
- Rust: `cargo test --workspace` in `rust/`, and the launcher, native player and shell tests in `desktop/`. `cargo clippy -- -D warnings` must be clean.
- Web: `npm test`, `npm run typecheck` and `npm run unused` in `frontend/`.
- Windows-only behaviour (the native player, reopening the app) is tried on a real PC; CI covers what it can.
