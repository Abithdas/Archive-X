## What and why

<!-- What changed, and the problem it solves. Link the issue if there is one. -->

## How it was checked

- [ ] `python -m pytest tools/tests` passes
- [ ] Web changes: `npx tsc --noEmit` and `npx vitest run` in `apps/web`
- [ ] Rust changes: `cargo check` / `cargo test`
- [ ] Looked at it in the app (say which mode: SFW, NSFW or both)

## Content modes

- [ ] Nothing adult can show in SFW (pages, counts, search, links, logs, job text)
- [ ] New routes call `require_auth()` and, if they return names, go through the content filter

## Release notes

- [ ] Added a line under `## Unreleased` in `CHANGELOG.md`
