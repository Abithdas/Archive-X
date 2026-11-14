# Archive X

A private media archive for Telegram: browse, search and play the videos and photos in your channels, keep them organised by performer, studio and tag, and move them between channels. It runs on your PC as a desktop app (Windows installer) and keeps everything local.

## What is in this repository

| Folder | What it is | Language |
|--------|------------|----------|
| `frontend/` | The web app the desktop window shows | TypeScript, React 19, Vite, Tailwind |
| `telegram_archive/` | The backend: Telegram, database, jobs, streaming, metadata | Python (being rewritten in Rust) |
| `rust/` | The Rust front door and the new backend features (`ax-server`, `ax-db`, `ax-names`, `ax-faces`) | Rust |
| `desktop/` | The Tauri shell, the backend launcher, the native video player | Rust, C++ |
| `tools/` | Tests for the Python backend, parity checks against the Rust front door, release and dependency-scan scripts | Python |
| `docs/` | Plans, architecture, logs, security reports (start at [docs/README.md](docs/README.md)) | Markdown |

How the pieces fit: the desktop shell starts the backend and the Rust front door, then opens the web app in a window. The front door serves the web app's files and a growing set of routes itself, and forwards everything else to the Python backend. The plan to move the rest into Rust is in [docs/plan.md](docs/plan.md).

## Build and test

Tools needed: Rust (stable), Node 22, Python 3.12. [`just`](https://github.com/casey/just) is optional but runs exactly what CI runs.

```bash
just test    # Python, Rust and web tests
just lint    # clippy and the type check
```

Without `just`:

```bash
python -m pip install -r requirements.txt pytest httpx websockets
python -m pytest tools/tests -q
(cd rust && cargo test --workspace)
(cd desktop && cargo test -p archive-x-launcher-core -p archive-x-native-player -p archive-x-desktop --lib)
(cd frontend && npm ci && npm test && npm run typecheck && npm run unused)
```

The Windows installer is built by the **Desktop** workflow in GitHub Actions (`.github/workflows/desktop.yml`). Run it with a version number to publish a release.

## More

- [CONTRIBUTING.md](CONTRIBUTING.md): the rules the work follows
- [CHANGELOG.md](CHANGELOG.md): what changed in each release
- [docs/README.md](docs/README.md): all the documents
