# Documents

| Document | What it is |
|----------|------------|
| [architecture.md](architecture.md) | How Archive X is built today: processes, backend layers, media sources, metadata, SFW / NSFW modes, the web app, known weak spots |
| [plan.md](plan.md) | **The working plan**: clean-up, reorganisation and the migration from Python to Rust, step by step, with its progress table |
| [adr/](adr/README.md) | One short page per architecture decision (front door, one writer per table, native player window, desktop only, layout, licence, plain files, content modes) |
| [native-player.md](native-player.md) | The native video player: design, what was verified on a real PC, what is next |
| [updates.md](updates.md) | How in-app updates work and what is needed to switch them on |
| [logs.md](logs.md) | The log bundle: what is in it and what is left out |
| [rust-migration-history.md](rust-migration-history.md) | History: the first Rust migration plan and the measured results of each round (paths predate the workspace move) |
| [security/SCA-REPORT.md](security/SCA-REPORT.md) | Dependency scan of 14 November 2026 and what was fixed |
| [api/](api/) | The frozen API description (`openapi.json`, `routes.tsv`) that the parity checks compare against |

Release notes are in [../CHANGELOG.md](../CHANGELOG.md). Older planning documents (roadmaps, the React migration plan, the code review, UI mock-ups) were removed on 18 November 2026; they are in git history before that date.
