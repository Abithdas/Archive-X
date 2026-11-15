# Architecture decisions

One short page per decision that shaped the code, so the reason is not only in someone's memory. Newest last. A decision is changed by writing a new page that says it replaces an older one.

| # | Decision |
|---|----------|
| [0001](0001-rust-front-door.md) | A Rust front door in front of the Python backend, instead of a rewrite in one go |
| [0002](0002-one-writer-per-table.md) | One writer per table group while two backends share one database |
| [0003](0003-new-features-in-rust.md) | New features are written in Rust; Python gets fixes and deletions |
| [0004](0004-native-player-own-window.md) | The native video player draws in its own window above the app |
| [0005](0005-desktop-app-only.md) | The desktop app is the only supported way to run Archive X |
| [0006](0006-one-workspace-and-layout.md) | One Cargo workspace and an `apps/`, `crates/`, `native/`, `packaging/` layout |
| [0007](0007-rust-writes-watch-history.md) | Rust writes its first tables: watch history entity favourites, scene markers |
