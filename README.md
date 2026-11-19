<p align="center"><img src="docs/brand/banner.png" alt="Archive X: movies, series, collections" width="640"></p>

# Archive X

**A private, local-first media library for your Telegram channels.**

Browse, search, and play your videos and photos from a Windows desktop app. Organize media by performer, studio, and tag, then move selected items between channels.

[Download the latest Windows release](https://github.com/Abithdas/Archive-X/releases/latest) · [View all releases](https://github.com/Abithdas/Archive-X/releases) · [Changelog](CHANGELOG.md) · [Architecture](docs/architecture.md)

> Archive X runs on your PC. Your library, caches and settings stay local. Telegram is used only to read and move channel media, and metadata providers only to look up names you ask it to identify.

> **Note**
> Archive X is a personal fan project I made for my own use and decided to share. It was built with the help of [Claude Code](https://www.anthropic.com/claude-code), Anthropic's AI coding assistant. It isn't affiliated with Telegram, Anthropic or any of the metadata providers it can look names up in.

## What you can do

- **One library from many places.** Telegram channels and folders on your PC (read where they lie, never copied) appear together. Choose per channel whether it is in the library, whether it is SFW or NSFW, and whether it holds videos, photos or both.
- **Two modes, one switch.** The app opens in **SFW** every time and shows nothing adult: pages, counts, people, studios, tags, search, direct links and settings all follow the mode. Switching to **NSFW** asks for a PIN. In SFW the app becomes a movie and TV library with a Movies page and a Series page that lists missing episodes.
- **Know what you have.** Videos are identified from their file names and fingerprints: metadata is collected from public metadata APIs (TMDB, OpenSubtitles, OMDb and TVmaze for movies and shows, and other metadata APIs for NSFW). Doubtful matches wait in a review list for your yes, and an id in a file name (`{tmdb-603}`) settles it.
- **Play anything.** A built-in player with 36 preview frames under the timeline, watch history and scene markers, plus a native video engine for formats a browser cannot play. Telegram videos play through a persistent cache with read-ahead that follows your position; local videos play straight from the disk.
- **Organize.** Performers, studios and tags, photo galleries with face grouping, favourites, and bulk edits.
- **Move media.** Transfer selected videos between channels, or upload a local folder to a channel, keeping what was already identified so nothing is looked up or snapshotted twice.
- **Stay in control.** An Operations page shows what is running, job history and logs; content automation (RSS, qBittorrent) and an optional local AI assistant are built in. Updates install from inside the app.

## Install

Download the latest release and choose the Windows installer that suits you:

- **`.exe`** — standard setup installer.
- **`.msi`** — Windows Installer package.

Archive X is currently distributed as a Windows x64 desktop app. See the [latest release](https://github.com/Abithdas/Archive-X/releases/latest) for downloads and version-specific notes.

### First run

1. Sign in to Telegram when asked; your session is stored encrypted on this PC.
2. Open **Settings, Telegram, Channels** and mark the channels you want in the library (SFW or NSFW). New channels stay out until you mark them.
3. Optional: **Settings, Local files** to add folders, and **Settings, Metadata** to add API keys for metadata collection (TMDB, OpenSubtitles and OMDb for movies and TV; further metadata APIs in NSFW mode). Each key has a **Test** button.
4. Use the switch at the top of the profile menu (your picture, top right) to change mode; the NSFW PIN is in **Settings, Security**. Your existing library is NSFW, so it appears after you switch.

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
| `apps/web/` | User interface shown inside the desktop window | TypeScript, React 19, Vite, Tailwind |
| `apps/desktop/` | Desktop shell: starts and stops everything, updates | Rust, Tauri 2 |
| `crates/` | Front door (`ax-server`), database access (`ax-db`), performer names (`ax-names`), face learning (`ax-faces`), launcher, native player wrapper | Rust |
| `telegram_archive/` | Telegram integration, jobs, metadata, playback cache, content modes | Python (FastAPI, Telethon, SQLite); migration to Rust is in progress |
| `native/axplayer/` | Native video engine | C++ |
| `packaging/` | Installer and bundled dependency tooling | Python, shell |
| `tools/` | Tests, parity checks, benchmarks, release and dependency-scan scripts | Python |
| `docs/` | Architecture, decisions, plan | Markdown |

The desktop shell starts the Python backend and the Rust front door on private loopback ports, then opens the web interface in its window. The front door serves the web app, answers a growing set of routes itself (lists, favourites, watch history, local video files) and forwards the rest to Python. Nothing listens on your network.

Start with [docs/architecture.md](docs/architecture.md); the reasons behind the big choices are in [docs/adr/](docs/adr/README.md), and what is being worked on is in [docs/plan.md](docs/plan.md). All documents are listed in [docs/README.md](docs/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project workflow and contribution guidelines. Bug reports and feature requests are welcome in [GitHub Issues](https://github.com/Abithdas/Archive-X/issues).

## License

Archive X is licensed under the [GNU General Public License v3 or later](LICENSE). Third-party components and their licenses are listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

