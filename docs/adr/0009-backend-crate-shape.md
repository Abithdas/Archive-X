# 0009. The shape of the Rust backend

**Status:** accepted (the target; the code moves into it as each phase of docs/plan.md arrives).

**Context.** The Rust side is five small crates plus the front door, and the front door (`ax-server`) already holds query logic that belongs below it (the Videos list, the favourite and marker rules). The rest of the migration will add about ten times as much code; without a rule it would pile up in `ax-server`.

**Decision.** The target crates and what each owns:

| Crate | Owns | Depends on |
| --- | --- | --- |
| `ax-core` | types shared by all: ids, errors, config, the data-folder layout | nothing of ours |
| `ax-db` | schema, numbered migrations, typed queries, the single writer and reader pool | `ax-core` |
| `ax-providers` | StashDB and ThePornDB clients (absorbs `ax-names`) | `ax-core`, `ax-db` |
| `ax-media` | ffmpeg and ffprobe, thumbnails, previews, image derivatives | `ax-core` |
| `ax-ai` | faces, tags, captions (absorbs `ax-faces`) | `ax-core`, `ax-db`, `ax-media` |
| `ax-jobs` | job table, lanes, pause, resume, cancel | `ax-core`, `ax-db` |
| `ax-telegram` | session, download ranges, uploads, FloodWait | `ax-core`, `ax-db` |
| `ax-server` | HTTP only: routing, guards, static files, thin handlers | all of the above |

Rules: a lower crate never depends on a higher one; an `ax-server` handler parses the request, calls a crate and shapes the answer; every crate has a `README.md` saying what it owns; errors are typed, never ignored.

**Not done now.** No code is moved yet. Crates are created when their first real content arrives (`ax-core` with the first shared type, `ax-media` with the first ffmpeg job), not as empty shells. When the next route is written, its query and rules go into the crate that will own them (for the reads and writes so far that is `ax-db`), and the handler in `ax-server` stays thin.

**Consequences.** The existing crates keep their names until the move; their READMEs say what they own and what they do not.
