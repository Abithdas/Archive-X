# Archive X

**A private, local-first media library for your Telegram channels.**

Browse, search, and play your videos and photos from a Windows desktop app. Organize media by performer, studio, and tag, then move selected items between channels.

[Download the latest Windows release](https://github.com/Abithdas/Archive-X/releases/latest) · [View all releases](https://github.com/Abithdas/Archive-X/releases) · [Read the changelog](CHANGELOG.md)

> Archive X runs on your PC. Your library and app data stay local; Telegram is used to access and transfer channel media.

## What you can do

- Browse and search media from your Telegram channels.
- Play videos and view photos in the desktop app.
- Keep a library organized with performer, studio, and tag details.
- Move media between channels.

## Install

Download the latest release and choose the Windows installer that suits you:

- **`.exe`** — standard setup installer.
- **`.msi`** — Windows Installer package.

Archive X is currently distributed as a Windows x64 desktop app. See the [latest release](https://github.com/Abithdas/Archive-X/releases/latest) for downloads and version-specific notes.

## Build from source

### Requirements

- Rust stable
- Node.js 22
- Python 3.12
- [`just`](https://github.com/casey/just) (optional; provides the same commands used by CI)

Run the full test suite and lint checks:

```bash
just test
just lint
```

Without `just`:

```bash
python -m pip install -r requirements.txt pytest httpx websockets
python -m pytest tools/tests -q
cargo test --workspace
cd apps/web
npm ci
npm test
npm run typecheck
npm run unused
```

The Windows installer is built by the [Desktop GitHub Actions workflow](.github/workflows/desktop.yml). The workflow publishes a release when run with a version number.

## Project structure

| Path | Purpose | Main technologies |
| --- | --- | --- |
| `apps/web/` | User interface shown inside the desktop window | TypeScript, React, Vite, Tailwind |
| `apps/desktop/` | Desktop shell | Rust, Tauri |
| `crates/` | Rust server, database, naming, face, launcher, and player libraries | Rust |
| `telegram_archive/` | Telegram integration, database, jobs, streaming, and metadata backend | Python; migration to Rust is in progress |
| `native/axplayer/` | Native video engine | C++ |
| `packaging/` | Installer and bundled dependency tooling | Python, shell |
| `tools/` | Tests, parity checks, release, and dependency-scan scripts | Python |
| `docs/` | Architecture, plans, and project notes | Markdown |

The desktop shell starts the backend and Rust front door, then opens the web interface in its window. The Rust front door serves the web app and handles a growing set of routes, forwarding the rest to the Python backend. See the [migration plan](docs/plan.md) and [documentation index](docs/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project workflow and contribution guidelines. Bug reports and feature requests are welcome in [GitHub Issues](https://github.com/Abithdas/Archive-X/issues).

## License

Archive X is licensed under the [GNU General Public License v3 or later](LICENSE). Third-party components and their licenses are listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

